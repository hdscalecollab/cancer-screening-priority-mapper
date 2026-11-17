# =============================================================================
# 04_validation_places.R
# Cancer Screening Priority Mapper - validation against CDC PLACES
# NCI Data Jamboree 2026
#
# Author: Calvin Tribby
#
# Purpose
#   Check whether the priority measures and classes from mvp_classification.R
#   line up with CDC PLACES tract estimates of mammography use
#   (MAMMOUSE_CrudePrev, women 50-74, past 2 years). Implements
#   PLACES_Validation_Plan.docx:
#     A. Tract measures vs. PLACES (Spearman rho, AUC), overall and urban/rural
#     B. Adjusted: "residual PLACES" after removing PLACES' own model inputs
#        (race/ethnicity, education, age mix) and county effects
#     C. Within-county correlations (county-demeaned)
#     D. County burden vs. PLACES aggregated to counties (+ partial for poverty)
#     E. Classes: PLACES by class at default cutoffs; lift and sensitivity
#        for all 81 precomputed combinations (3 burden measures x 27 cutoffs)
#     F. Sensitivity: 3- vs 4-barrier index, national vs urban/rural ranking,
#        PLACES precision, tract size, low-screening threshold, county-
#        bootstrap CIs
#
# Inputs (written by mvp_classification.R to out_dir)
#   df_classification.csv             values, percentiles, levels, default classes
#   df_percentiles.csv                item-level barrier percentiles (nat and ur)
#   df_w_calcs.csv                    ACS indicators (PLACES adjusters)
#   county_burden.csv                 one row per county, burden + rurality
#   df_classification_all_cutoffs.csv integer class codes, cls_<m>_b<>_a<>_r<>
#   class_codes.csv                   code -> class label
# Plus:
#   PLACES tract file, wide (GIS-friendly) format: one row per tract with
#     TractFIPS, MAMMOUSE_CrudePrev, MAMMOUSE_Crude95CI
#   ACS file (only the B01001 female 50-74 columns, for the age-mix adjuster)
#
# Conventions
#   All priority percentiles are 0-100 with HIGHER = MORE NEED.
#   Aligned with PLACES => rho < 0 and AUC > 0.5.
#   Report effect sizes, not p-values (~80k tracts).
# =============================================================================

library(tidyverse)

# ---- Settings ---------------------------------------------------------------
data_dir <- "C:/NCI_Data"                       # same as mvp_classification.R
out_dir  <- data_dir
val_dir  <- file.path(out_dir, "validation")
fig_dir  <- file.path(val_dir, "figures")
dir.create(fig_dir, recursive = TRUE, showWarnings = FALSE)

places_file <- "PLACES_census_tract_2025.csv"               # EDIT: name of your PLACES download
acs_file    <- "acs5_2019_2023_tract_MVP v2.csv"

# PLACES columns (wide format)
places_id   <- "TractFIPS"
places_prev <- "MAMMOUSE_CrudePrev"   # mammography use, women 50-74 (%)
places_ci   <- "MAMMOUSE_Crude95CI"   # text, e.g. "(70.1, 75.3)"; NA if absent

burden_names <- c("aair", "cases", "late_stage")

barrier_vars <- c("pct_uninsured_f35_74", "pct_no_vehicle_hh",
                  "pct_no_hs_f25", "pct_lep_hh")
barrier3     <- setdiff(barrier_vars, "pct_no_hs_f25")   # drops PLACES-shared input

default_cut    <- c(burden = 90, access = 67, barriers = 67)  # app opening view
low_thresholds <- c(10, 20, 33)   # bottom X% of PLACES = "low screening"
primary_low    <- 20              # team decision 1
min_women      <- 100             # sensitivity: drop small tracts
n_boot         <- 200             # county-bootstrap reps; set 0 while debugging
set.seed(2026)

# Tract measures (labels -> columns). Urban/rural-ranked = app default.
measures_ur <- c(
  distance        = "pctl_access_ur",
  uninsured       = "pctl_ur_pct_uninsured_f35_74",
  no_vehicle      = "pctl_ur_pct_no_vehicle_hh",
  limited_english = "pctl_ur_pct_lep_hh",
  no_hs_diploma   = "pctl_ur_pct_no_hs_f25",
  barrier_4       = "pctl_barriers_ur",
  barrier_3       = "pctl_barriers3_ur")
measures_nat <- c(
  distance        = "pctl_access_nat",
  uninsured       = "pctl_nat_pct_uninsured_f35_74",
  no_vehicle      = "pctl_nat_pct_no_vehicle_hh",
  limited_english = "pctl_nat_pct_lep_hh",
  no_hs_diploma   = "pctl_nat_pct_no_hs_f25",
  barrier_4       = "pctl_barriers_nat",
  barrier_3       = "pctl_barriers3_nat")
overlap <- c(no_hs_diploma = "shared input", barrier_4 = "partial (education)")

# PLACES model inputs, used as adjusters in Analysis B (team decision 2)
adjusters <- c("pct_nh_white", "pct_nh_black", "pct_nh_asian", "pct_hispanic",
               "pct_no_hs_f25", "share_f65_74")

# ---- Helpers ----------------------------------------------------------------
fix_geoid <- function(x, n) str_pad(as.character(x), n, pad = "0")
pctl      <- function(x) 100 * percent_rank(x)
rho       <- function(x, y) suppressWarnings(cor(x, y, method = "spearman",
                                                 use = "complete.obs"))

# AUC (Mann-Whitney): P(score in a low-screening tract > score in another tract)
auc <- function(score, is_low) {
  ok <- !is.na(score) & !is.na(is_low)
  score <- score[ok]; is_low <- is_low[ok]
  n1 <- sum(is_low); n0 <- sum(!is_low)
  if (n1 == 0 || n0 == 0) return(NA_real_)
  (sum(rank(score)[is_low]) - n1 * (n1 + 1) / 2) / (n1 * n0)
}

partial_rho <- function(x, y, z) {
  d <- na.omit(tibble(x = rank(x), y = rank(y), z = rank(z)))
  cor(resid(lm(x ~ z, d)), resid(lm(y ~ z, d)))
}

demean <- function(x, g) x - ave(x, g, FUN = \(v) mean(v, na.rm = TRUE))

# Width of a PLACES CI string like "(70.1, 75.3)"; NA if it can't be parsed
ci_width_from_text <- function(x) {
  nums <- str_extract_all(x, "-?\\d+(\\.\\d+)?")
  map_dbl(nums, \(v) if (length(v) == 2) diff(as.numeric(v)) else NA_real_)
}

# Resample counties (not tracts) so CIs respect clustering
boot_ci <- function(data, f, B = n_boot, cluster = "burden_FIPS") {
  if (B == 0) return(c(lo = NA_real_, hi = NA_real_))
  idx <- split(seq_len(nrow(data)), data[[cluster]])
  est <- replicate(B, f(data[unlist(sample(idx, replace = TRUE), use.names = FALSE), ]))
  setNames(quantile(est, c(.025, .975), na.rm = TRUE), c("lo", "hi"))
}

chr_ids <- cols(GEOID20 = "c", FIPS = "c", burden_FIPS = "c", acs_period = "c")

# ---- 1. Load classification outputs -----------------------------------------
df_class <- read_csv(file.path(out_dir, "df_classification.csv"), col_types = chr_ids)

df_pctl <- read_csv(file.path(out_dir, "df_percentiles.csv"), col_types = chr_ids) |>
  select(GEOID20, starts_with("pctl_nat_pct_"), starts_with("pctl_ur_pct_"))

df_acs <- read_csv(file.path(out_dir, "df_w_calcs.csv"), col_types = chr_ids) |>
  select(GEOID20, pct_nh_white, pct_nh_black, pct_nh_asian, pct_hispanic,
         pct_no_hs_f25, pct_poverty_f35_74)

# Age mix: share of women 50-74 who are 65-74 (B01001_044-046 / _040-046)
age_cols <- sprintf("B01001_%03dE", 40:46)
df_age <- read_csv(file.path(data_dir, acs_file),
                   col_select = c(GEOID, all_of(age_cols)),
                   col_types = cols(GEOID = "c", .default = "d")) |>
  transmute(GEOID20 = fix_geoid(GEOID, 11),
            share_f65_74 = if_else(rowSums(pick(all_of(age_cols))) > 0,
                                   rowSums(pick(B01001_044E:B01001_046E)) /
                                     rowSums(pick(all_of(age_cols))), NA_real_))

county_burden <- read_csv(file.path(out_dir, "county_burden.csv"),
                          col_types = cols(FIPS = "c"))

class_codes <- read_csv(file.path(out_dir, "class_codes.csv"), show_col_types = FALSE)
hp_code     <- class_codes$class_code[class_codes$class == "High priority"]

# ---- 2. Load PLACES (wide format) and join ----------------------------------
# One row per tract; read as text so TractFIPS keeps leading zeros.
# The wide file has no Year column: record the PLACES release and BRFSS
# year in the README.
places_hdr  <- names(read_csv(file.path(data_dir, places_file), n_max = 0,
                              show_col_types = FALSE))
stopifnot("PLACES file is missing TractFIPS or MAMMOUSE_CrudePrev" =
            all(c(places_id, places_prev) %in% places_hdr))
has_ci <- places_ci %in% places_hdr
if (!has_ci) message("No ", places_ci, " column: the drop_wide_ci sensitivity is skipped.")

places <- read_csv(file.path(data_dir, places_file),
                   col_select = any_of(c(places_id, places_prev, places_ci)),
                   col_types = cols(.default = "c")) |>
  transmute(GEOID20    = fix_geoid(.data[[places_id]], 11),
            mammo_prev = as.numeric(.data[[places_prev]]),
            ci_width   = if (has_ci) ci_width_from_text(.data[[places_ci]]) else NA_real_)

stopifnot("Duplicate PLACES tracts" = !anyDuplicated(places$GEOID20))
cat(sprintf("PLACES tracts: %s | missing %s: %s | unparsed CI: %s\n",
            nrow(places), places_prev, sum(is.na(places$mammo_prev)),
            sum(is.na(places$ci_width))))
summary(places$mammo_prev) |> print()    # expect roughly 50-90 (%)

# Match rate (low => PLACES on 2010 tracts; needs a 2010->2020 crosswalk)
match_rate <- mean(df_class$GEOID20 %in% places$GEOID20)
cat(sprintf("Tract match rate: %.1f%%\n", 100 * match_rate))
df_class |>
  filter(!GEOID20 %in% places$GEOID20) |>
  count(state, sort = TRUE) |>
  write_csv(file.path(val_dir, "val_unmatched_tracts_by_state.csv"))
if (match_rate < 0.95) warning("Match rate < 95%: check PLACES tract vintage.")

val <- df_class |>
  left_join(df_pctl, by = "GEOID20", relationship = "one-to-one") |>
  left_join(df_acs,  by = "GEOID20", relationship = "one-to-one") |>
  left_join(df_age,  by = "GEOID20", relationship = "one-to-one") |>
  inner_join(places, by = "GEOID20", relationship = "one-to-one") |>
  filter(n_women_50_74 > 0, !is.na(mammo_prev))

# 3-barrier index (no education), built the same way as the 4-barrier index:
# mean of item percentiles, re-ranked; unknown rurality falls back to national
val <- val |>
  mutate(barrier3_nat = rowMeans(pick(all_of(paste0("pctl_nat_", barrier3)))),
         pctl_barriers3_nat = pctl(barrier3_nat)) |>
  group_by(rurality) |>
  mutate(barrier3_ur = rowMeans(pick(all_of(paste0("pctl_ur_", barrier3)))),
         pctl_barriers3_ur = pctl(barrier3_ur)) |>
  ungroup() |>
  mutate(pctl_barriers3_ur = if_else(is.na(rurality), pctl_barriers3_nat,
                                     pctl_barriers3_ur))

# Low-screening flags (national percentiles of PLACES)
for (t in low_thresholds) {
  val[[paste0("low_", t)]] <- val$mammo_prev <= quantile(val$mammo_prev, t / 100)
}
val$is_low <- val[[paste0("low_", primary_low)]]

# ---- 3. Analysis B: residual PLACES -----------------------------------------
# County fixed effects via demeaning (same estimates as factor(county) in lm,
# without a ~3,100-column model matrix)
adj <- val |>
  select(GEOID20, burden_FIPS, mammo_prev, all_of(adjusters)) |>
  na.omit() |>
  mutate(across(c(mammo_prev, all_of(adjusters)), \(x) demean(x, burden_FIPS),
                .names = "dm_{.col}"))
fit_adj <- lm(reformulate(paste0("dm_", adjusters), "dm_mammo_prev"), data = adj)
adj$mammo_resid <- resid(fit_adj)

r2_total <- 1 - sum(adj$mammo_resid^2) /
  sum((adj$mammo_prev - mean(adj$mammo_prev))^2)
cat(sprintf("PLACES variance explained by its inputs + county: R2 = %.2f\n", r2_total))

val <- val |>
  left_join(select(adj, GEOID20, mammo_resid), by = "GEOID20") |>
  mutate(is_low_resid = mammo_resid <= quantile(mammo_resid, primary_low / 100,
                                                na.rm = TRUE))

# ---- 4. Analyses A, B, C: tract measures ------------------------------------
tract_stats <- function(d, var) {
  x <- d[[var]]
  tibble(n            = sum(!is.na(x)),
         rho_raw      = rho(x, d$mammo_prev),
         auc_raw      = auc(x, d$is_low),
         rho_adjusted = rho(x, d$mammo_resid),
         auc_adjusted = auc(x, d$is_low_resid),
         rho_within   = rho(demean(x, d$burden_FIPS),
                            demean(d$mammo_prev, d$burden_FIPS)))
}

strata <- list(overall = val,
               urban   = filter(val, rurality == "Urban"),
               rural   = filter(val, rurality == "Rural"))

res_tract <- imap_dfr(strata, \(d, s)
                      imap_dfr(measures_ur, \(v, lab) tract_stats(d, v) |>
                                 mutate(measure = lab, stratum = s, .before = 1))) |>
  mutate(overlap = coalesce(overlap[measure], "independent"),
         r2_places_inputs = r2_total)

if (n_boot > 0) {
  ci <- imap_dfr(measures_ur, \(v, lab) {
    b <- boot_ci(val, \(d) rho(d[[v]], d$mammo_prev))
    tibble(measure = lab, stratum = "overall", rho_raw_lo = b[["lo"]], rho_raw_hi = b[["hi"]])
  })
  res_tract <- left_join(res_tract, ci, by = c("measure", "stratum"))
}
write_csv(res_tract, file.path(val_dir, "val_tract_measures.csv"))

# ---- 5. Analysis D: county burden -------------------------------------------
# County poverty approximated as tract poverty weighted by women 40-74
county_val <- val |>
  group_by(burden_FIPS) |>
  summarise(mammo_cty   = weighted.mean(mammo_prev, n_women_50_74, na.rm = TRUE),
            poverty_cty = weighted.mean(pct_poverty_f35_74, n_women_40_74, na.rm = TRUE),
            .groups = "drop") |>
  inner_join(county_burden, by = c("burden_FIPS" = "FIPS"))

res_county <- map_dfr(burden_names, \(b) tibble(
  burden_measure  = b,
  n_counties      = sum(!is.na(county_val[[b]])),
  rho_raw         = rho(county_val[[b]], county_val$mammo_cty),
  rho_partial_pov = partial_rho(county_val[[b]], county_val$mammo_cty,
                                county_val$poverty_cty)))
write_csv(res_county, file.path(val_dir, "val_county_burden.csv"))

# ---- 6. Analysis E: classes -------------------------------------------------
# Default cutoffs: class_<measure> columns in df_classification
res_class <- val |>
  pivot_longer(all_of(paste0("class_", burden_names)), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class") |>
  mutate(class = factor(class, class_codes$class)) |>
  group_by(burden_measure, class) |>
  summarise(n_tracts     = n(),
            mean_mammo   = mean(mammo_prev),
            median_mammo = median(mammo_prev),
            mean_resid   = mean(mammo_resid, na.rm = TRUE),
            pct_low      = 100 * mean(is_low),
            .groups = "drop")
write_csv(res_class, file.path(val_dir, "val_by_class.csv"))

# All 81 combinations (precomputed at pctl_scale = "ur")
all_cut <- read_csv(file.path(out_dir, "df_classification_all_cutoffs.csv"),
                    col_types = cols(GEOID20 = "c", .default = "i")) |>
  filter(GEOID20 %in% val$GEOID20)

combo_stats <- function(code, low) {
  hp <- code %in% hp_code
  tibble(n_high      = sum(hp),
         pct_low_hp  = mean(low[hp], na.rm = TRUE),
         sensitivity = sum(hp & low, na.rm = TRUE) / sum(low, na.rm = TRUE))
}

lows <- c(setNames(paste0("low_", low_thresholds), paste0("raw_", low_thresholds)),
          resid_20 = "is_low_resid")
low_mat <- val[match(all_cut$GEOID20, val$GEOID20), unname(lows)]

res_combos <- imap_dfr(select(all_cut, starts_with("cls_")), \(code, nm)
                       imap_dfr(lows, \(col, lab) combo_stats(code, low_mat[[col]]) |>
                                  mutate(combo = nm, outcome = lab, .before = 1))) |>
  extract(combo, c("burden_measure", "cut_burden", "cut_access", "cut_barriers"),
          "cls_(.+)_b(\\d+)_a(\\d+)_r(\\d+)", convert = TRUE) |>
  mutate(base_rate  = if_else(str_starts(outcome, "raw"),
                              as.numeric(str_extract(outcome, "\\d+")) / 100,
                              primary_low / 100),
         lift       = pct_low_hp / base_rate,
         reportable = n_high >= 30)
write_csv(res_combos, file.path(val_dir, "val_cutoff_combos.csv"))

# ---- 7. Analysis F: sensitivity ---------------------------------------------
# (3- vs 4-barrier and urban/rural strata are in val_tract_measures.csv)
sens_sets <- list(
  national_rank = list(d = val, vars = measures_nat),
  min_women     = list(d = filter(val, n_women_50_74 >= min_women), vars = measures_ur))
if (has_ci) {
  sens_sets$drop_wide_ci <- list(
    d = filter(val, ci_width < quantile(ci_width, .9, na.rm = TRUE)),
    vars = measures_ur)
}

res_sens <- imap_dfr(sens_sets, \(s, nm)
                     imap_dfr(s$vars, \(v, lab) tract_stats(s$d, v) |>
                                mutate(measure = lab, analysis = nm, .before = 1)))
write_csv(res_sens, file.path(val_dir, "val_sensitivity.csv"))

# ---- 8. Figures -------------------------------------------------------------
p1 <- res_tract |>
  filter(stratum == "overall") |>
  pivot_longer(c(rho_raw, rho_adjusted, rho_within), names_to = "type", values_to = "rho") |>
  mutate(type = factor(type, c("rho_raw", "rho_adjusted", "rho_within"),
                       c("Raw", "Adjusted (residual PLACES)", "Within county")),
         measure = paste0(measure, if_else(overlap == "independent", "", " *"))) |>
  ggplot(aes(rho, reorder(measure, rho), fill = type)) +
  geom_col(position = "dodge") +
  geom_vline(xintercept = 0) +
  labs(x = "Spearman rho with PLACES mammography (negative = aligned)", y = NULL,
       fill = NULL, caption = "* shares inputs with PLACES") +
  theme_minimal() + theme(legend.position = "bottom")
ggsave(file.path(fig_dir, "fig_val_correlations.png"), p1, width = 8, height = 5, dpi = 300)

p2 <- val |>
  pivot_longer(all_of(paste0("class_", burden_names)), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class") |>
  filter(!is.na(class)) |>
  mutate(class = fct_rev(factor(class, class_codes$class))) |>
  ggplot(aes(mammo_prev, class)) +
  geom_boxplot(outlier.shape = NA) +
  facet_wrap(~ burden_measure) +
  labs(x = "PLACES mammography use (%)", y = NULL,
       title = sprintf("PLACES screening by priority class (%s/%s/%s)",
                       default_cut[["burden"]], default_cut[["access"]],
                       default_cut[["barriers"]])) +
  theme_minimal()
ggsave(file.path(fig_dir, "fig_val_by_class.png"), p2, width = 10, height = 5, dpi = 300)

p3 <- res_combos |>
  filter(outcome == paste0("raw_", primary_low), reportable) |>
  ggplot(aes(factor(cut_access), factor(cut_barriers), fill = lift)) +
  geom_tile() +
  geom_text(aes(label = sprintf("%.2f", lift)), size = 3) +
  facet_grid(burden_measure ~ paste("Burden", cut_burden)) +
  scale_fill_gradient2(midpoint = 1) +
  labs(x = "Access cutoff", y = "Barriers cutoff", fill = "Lift",
       title = sprintf("Lift: high-priority share in bottom %s%% of screening / %s%%",
                       primary_low, primary_low)) +
  theme_minimal()
ggsave(file.path(fig_dir, "fig_val_lift_heatmap.png"), p3, width = 9, height = 7, dpi = 300)

# ---- 9. Console summary (for the report-out slide) --------------------------
cat("\n== Tract measures vs PLACES (overall) ==\n")
res_tract |>
  filter(stratum == "overall") |>
  select(measure, overlap, rho_raw, rho_adjusted, rho_within, auc_raw) |>
  mutate(across(where(is.numeric), \(x) round(x, 2))) |>
  print()

cat("\n== Default cutoffs: lift and sensitivity (bottom", primary_low, "%) ==\n")
res_combos |>
  filter(outcome == paste0("raw_", primary_low),
         cut_burden == default_cut[["burden"]], cut_access == default_cut[["access"]],
         cut_barriers == default_cut[["barriers"]]) |>
  select(burden_measure, n_high, lift, sensitivity) |>
  mutate(across(c(lift, sensitivity), \(x) round(x, 2))) |>
  print()
