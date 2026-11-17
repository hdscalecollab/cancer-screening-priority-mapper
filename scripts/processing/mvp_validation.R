# =============================================================================
# 04_validation_places.R
# Cancer Screening Priority Mapper - validation against CDC PLACES
# NCI Data Jamboree 2026
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
#     G. Six slide-ready figures (16:9) + headline numbers CSV
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
data_dir <- "C:/NCI_Data"                       
out_dir  <- data_dir
val_dir  <- file.path(out_dir, "validation")
fig_dir  <- file.path(val_dir, "figures")
dir.create(fig_dir, recursive = TRUE, showWarnings = FALSE)

places_file <- "PLACES_census_tract_2025.csv"               
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

# ---- 8. Presentation figures (16:9, slide-ready) ----------------------------
# Six PNGs sized for a full 16:9 slide (13.33 x 7.5 in, 300 dpi), numbered in
# suggested talk order. Subtitles are filled in from the results, so they
# update when the analysis is rerun.
# Colors: colorblind-safe; class colors should match the Shiny app (EDIT
# class_pal if Jay's app uses different ones).

fig_w <- 13.33; fig_h <- 7.5

theme_slide <- function(base = 16) {
  theme_minimal(base_size = base) +
    theme(plot.title          = element_text(face = "bold", size = base + 6),
          plot.subtitle       = element_text(color = "grey30", size = base + 1,
                                             margin = margin(b = 12)),
          plot.caption        = element_text(color = "grey45", size = base - 5, hjust = 0),
          plot.title.position = "plot", plot.caption.position = "plot",
          panel.grid.minor    = element_blank(),
          legend.position     = "top", legend.justification = "left",
          strip.text          = element_text(face = "bold", size = base - 1),
          plot.margin         = margin(15, 20, 10, 15))
}
theme_set(theme_slide())

save_slide <- function(p, name) {
  ggsave(file.path(fig_dir, paste0(name, ".png")), p,
         width = fig_w, height = fig_h, dpi = 300, bg = "white")
}

class_pal <- c("High priority"                   = "#B2182B",
               "Access gap"                      = "#E69F00",
               "Barrier gap"                     = "#CC79A7",
               "Monitor"                         = "#56B4E9",
               "Lower priority"                  = "#BDBDBD",
               "Burden suppressed: gaps present" = "#8C8C8C",
               "Burden suppressed: no gaps"      = "#E5E5E5")

measure_lab <- c(distance        = "Distance to mammography",
                 uninsured       = "Uninsured (women 35-74)",
                 no_vehicle      = "No vehicle (households)",
                 limited_english = "Limited English (households)",
                 no_hs_diploma   = "No HS diploma (women 25+) *",
                 barrier_4       = "Barrier index, 4 items *",
                 barrier_3       = "Barrier index, 3 items (no education)")
burden_lab  <- c(aair       = "Late-stage incidence rate",
                 cases      = "Late-stage cases",
                 late_stage = "% diagnosed late stage")

src_cap <- paste0("Sources: CDC PLACES (mammography, women 50-74); ACS 2019-2023; ",
                  "FDA-certified mammography facilities; State Cancer Profiles 2019-2023.")
def_txt <- sprintf("burden %sth / access %sth / barriers %sth percentile",
                   default_cut[["burden"]], default_cut[["access"]],
                   default_cut[["barriers"]])
is_default_combo <- function(b, a, r) b == default_cut[["burden"]] &
  a == default_cut[["access"]] & r == default_cut[["barriers"]]

# -- Fig 1: PLACES screening by priority class (headline result) --------------
f1_dat <- val |>
  pivot_longer(all_of(paste0("class_", burden_names)), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class") |>
  filter(!is.na(class)) |>
  mutate(class = factor(class, rev(names(class_pal))),
         burden_measure = factor(burden_lab[burden_measure], burden_lab))
f1_n <- count(f1_dat, burden_measure, class)

med_aair <- res_class |> filter(burden_measure == "aair")
med_aair <- setNames(med_aair$median_mammo, as.character(med_aair$class))
xlim1    <- quantile(val$mammo_prev, c(.01, .99), na.rm = TRUE)

p_f1 <- ggplot(f1_dat, aes(mammo_prev, class, fill = class)) +
  geom_vline(xintercept = median(val$mammo_prev), linetype = 2, color = "grey40") +
  geom_boxplot(outlier.shape = NA, width = 0.65, show.legend = FALSE) +
  geom_text(data = f1_n, aes(x = xlim1[[2]], y = class, label = scales::comma(n)),
            inherit.aes = FALSE, hjust = 1, size = 3.6, color = "grey30") +
  scale_fill_manual(values = class_pal) +
  coord_cartesian(xlim = xlim1) +
  facet_wrap(~ burden_measure, nrow = 1) +
  labs(title = "Do high-priority tracts have lower screening?",
       subtitle = sprintf(paste0("Median mammography use: High priority %.1f%% vs ",
                                 "Lower priority %.1f%% (incidence rate burden). ",
                                 "Dashed line = national median."),
                          med_aair["High priority"], med_aair["Lower priority"]),
       x = "CDC PLACES mammography use, women 50-74 (%)", y = NULL,
       caption = paste0("Default app cutoffs: ", def_txt,
                        ". Numbers = tracts per class.\n", src_cap))
save_slide(p_f1, "fig1_screening_by_class")

# -- Fig 2: alignment of each tract measure (raw, adjusted, within county) ----
# Plotted as -rho so that bars to the right = more need where screening is lower
f2_dat <- res_tract |>
  filter(stratum == "overall") |>
  pivot_longer(c(rho_raw, rho_adjusted, rho_within), names_to = "type",
               values_to = "rho") |>
  mutate(align = -rho,
         type  = factor(type, c("rho_raw", "rho_adjusted", "rho_within"),
                        c("Raw", "Adjusted for PLACES model inputs", "Within county")),
         label = fct_reorder(measure_lab[measure], align, .fun = max))
if ("rho_raw_lo" %in% names(f2_dat)) {
  f2_dat <- f2_dat |>
    mutate(lo = if_else(type == "Raw", -rho_raw_hi, NA_real_),
           hi = if_else(type == "Raw", -rho_raw_lo, NA_real_))
} else {
  f2_dat <- mutate(f2_dat, lo = NA_real_, hi = NA_real_)
}
top2 <- f2_dat |> filter(type == "Raw") |> slice_max(align, n = 1, with_ties = FALSE)

p_f2 <- ggplot(f2_dat, aes(label, align, color = type)) +
  geom_hline(yintercept = 0, color = "grey40") +
  geom_linerange(aes(ymin = lo, ymax = hi), position = position_dodge(0.65),
                 linewidth = 1, na.rm = TRUE) +
  geom_point(size = 4.5, position = position_dodge(0.65)) +
  scale_color_manual(values = c("#0072B2", "#E69F00", "#009E73"), name = NULL) +
  coord_flip() +
  labs(title = "Which priority measures track low screening?",
       subtitle = sprintf(paste0("Strongest raw alignment: %s (rho = %.2f). ",
                                 "PLACES inputs + county explain %.0f%% of PLACES variation."),
                          as.character(top2$label), -top2$align, 100 * r2_total),
       x = NULL, y = "Alignment with low screening (-Spearman rho; right = aligned)",
       caption = paste0("* Shares an input with the PLACES model (partly circular). ",
                        "Lines = 95% county-bootstrap CI.\n", src_cap))
save_slide(p_f2, "fig2_measure_alignment")

# -- Fig 3: urban vs rural ----------------------------------------------------
f3_dat <- res_tract |>
  filter(stratum %in% c("urban", "rural")) |>
  transmute(label = measure_lab[measure], stratum = str_to_title(stratum),
            align = -rho_raw) |>
  mutate(label = fct_reorder(label, align, .fun = max))

p_f3 <- ggplot(f3_dat, aes(align, label)) +
  geom_vline(xintercept = 0, color = "grey40") +
  geom_line(aes(group = label), color = "grey70", linewidth = 2) +
  geom_point(aes(color = stratum), size = 5) +
  scale_color_manual(values = c(Urban = "#0072B2", Rural = "#009E73"), name = NULL) +
  labs(title = "Alignment differs between urban and rural tracts",
       subtitle = "Measures ranked within urban and rural tracts separately (app default)",
       x = "Alignment with low screening (-Spearman rho)", y = NULL,
       caption = paste0("* Shares an input with the PLACES model.\n", src_cap))
save_slide(p_f3, "fig3_urban_rural")

# -- Fig 4: county burden vs county screening ---------------------------------
f4_dat <- county_val |>
  pivot_longer(all_of(paste0("pctl_", burden_names)), names_to = "burden_measure",
               names_prefix = "pctl_", values_to = "burden_pctl") |>
  filter(!is.na(burden_pctl)) |>
  mutate(burden_measure = factor(burden_lab[burden_measure], burden_lab))
f4_lab <- res_county |>
  mutate(burden_measure = factor(burden_lab[burden_measure], burden_lab),
         lab = sprintf("rho = %.2f\nadj. for poverty = %.2f\n%s counties",
                       rho_raw, rho_partial_pov, scales::comma(n_counties)))

p_f4 <- ggplot(f4_dat, aes(burden_pctl, mammo_cty)) +
  geom_point(alpha = 0.25, size = 1.3, color = "grey30") +
  geom_smooth(method = "loess", formula = y ~ x, se = TRUE,
              color = "#B2182B", fill = "#B2182B", alpha = 0.15) +
  geom_label(data = f4_lab, aes(x = 2, y = Inf, label = lab), inherit.aes = FALSE,
             hjust = 0, vjust = 1.1, size = 3.8, label.size = 0, fill = "white") +
  facet_wrap(~ burden_measure, nrow = 1) +
  labs(title = "County late-stage burden vs. county screening",
       subtitle = "Each point is a county; PLACES aggregated from tracts (weighted by women 50-74)",
       x = "County burden percentile (higher = more burden)",
       y = "Mammography use, women 50-74 (%)",
       caption = paste0("Suppressed counties excluded. ", src_cap))
save_slide(p_f4, "fig4_county_burden")

# -- Fig 5: lift heat map across all cutoff combinations ----------------------
f5_dat <- res_combos |>
  filter(outcome == paste0("raw_", primary_low)) |>
  mutate(burden_measure = factor(burden_lab[burden_measure], burden_lab),
         burden_col     = paste0("Burden ", cut_burden, "th+"),
         fill_val       = if_else(reportable, lift, NA_real_),
         lab            = if_else(reportable,
                                  sprintf("%.2f\n%s", lift, scales::comma(n_high)),
                                  "n < 30"),
         is_default     = is_default_combo(cut_burden, cut_access, cut_barriers))

p_f5 <- ggplot(f5_dat, aes(factor(cut_access), factor(cut_barriers), fill = fill_val)) +
  geom_tile(color = "white", linewidth = 0.8) +
  geom_tile(data = filter(f5_dat, is_default), fill = NA, color = "black",
            linewidth = 1.3) +
  geom_text(aes(label = lab), size = 3.3, lineheight = 0.9) +
  scale_fill_gradient2(low = "#2166AC", mid = "white", high = "#B2182B",
                       midpoint = 1, na.value = "grey92", name = "Lift") +
  facet_grid(burden_measure ~ burden_col, labeller = label_wrap_gen(18)) +
  labs(title = "How well does each cutoff combination find low-screening tracts?",
       subtitle = sprintf(paste0("Lift = share of high-priority tracts in the bottom %s%% ",
                                 "of screening / %s%%. Lift > 1 = better than chance. ",
                                 "Outlined = app default."), primary_low, primary_low),
       x = "Access cutoff (percentile)", y = "Barriers cutoff (percentile)",
       caption = paste0("Cell labels: lift and number of high-priority tracts. ", src_cap)) +
  theme(panel.grid = element_blank(), legend.position = "right")
save_slide(p_f5, "fig5_lift_heatmap")

# -- Fig 6: precision vs reach trade-off --------------------------------------
f6_dat <- filter(f5_dat, reportable)
f6_def <- filter(f6_dat, is_default)

p_f6 <- ggplot(f6_dat, aes(n_high, lift, color = burden_measure)) +
  geom_hline(yintercept = 1, linetype = 2, color = "grey40") +
  geom_point(size = 3.5, alpha = 0.8) +
  geom_point(data = f6_def, shape = 21, size = 8, stroke = 1.4, color = "black",
             fill = NA, show.legend = FALSE) +
  geom_text(data = f6_def, aes(label = "App default"), color = "black",
            vjust = -1.6, size = 4, show.legend = FALSE) +
  scale_x_log10(labels = scales::comma) +
  scale_color_manual(values = c("#0072B2", "#E69F00", "#009E73"), name = NULL) +
  labs(title = "Stricter cutoffs: fewer tracts, more precise targeting?",
       subtitle = "Each point is one cutoff combination (burden x access x barriers)",
       x = "Number of high-priority tracts (log scale)",
       y = sprintf("Lift (bottom %s%% of screening)", primary_low),
       caption = paste0("Combinations with < 30 high-priority tracts not shown. ", src_cap))
save_slide(p_f6, "fig6_precision_vs_reach")

# ---- 9. Headline numbers (for the report-out slide) -------------------------
headline <- bind_rows(
  res_tract |>
    filter(stratum == "overall") |>
    transmute(item = paste("Tract:", measure), overlap,
              rho_raw, rho_adjusted, rho_within, auc_raw),
  res_combos |>
    filter(outcome == paste0("raw_", primary_low),
           is_default_combo(cut_burden, cut_access, cut_barriers)) |>
    transmute(item = paste("Default classes:", burden_measure),
              n_high, lift, sensitivity)) |>
  mutate(across(where(is.double), \(x) round(x, 2)))
write_csv(headline, file.path(val_dir, "val_headline_numbers.csv"))
print(headline, n = Inf)
cat("\nFigures saved to:", fig_dir, "\n")
