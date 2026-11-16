# =============================================================================
# Validation: priority measures vs. CDC PLACES mammography screening (MAMMOUSE)
# Run after mvp_classification.R (same session).
# =============================================================================
library(tidyverse)
library(scales)

places_file <- "PLACES_census_tract_2025.csv"   # PLACES tract file (long or GIS-friendly wide format)

# ---- 1. Read PLACES mammography use (% women 50-74 screened in past 2 years) ----
places_raw <- read_csv(file.path(data_dir, places_file), col_types = cols(.default = "c"))

places <- if ("MeasureId" %in% names(places_raw)) {            # long format
  places_raw |>
    filter(MeasureId == "MAMMOUSE") |>
    transmute(GEOID20 = str_pad(LocationName, 11, pad = "0"), mammo_prev = as.numeric(Data_Value))
} else {                                                        # wide format
  places_raw |>
    transmute(GEOID20 = str_pad(TractFIPS, 11, pad = "0"), mammo_prev = as.numeric(MAMMOUSE_CrudePrev))
}
stopifnot("One row per tract" = !anyDuplicated(places$GEOID20))

# ---- 2. Join to priority measures --------------------------------------------
val <- df_classification |>
  left_join(select(df_calcs, GEOID20, all_of(barrier_vars), pct_poverty_f35_74), by = "GEOID20") |>
  inner_join(places, by = "GEOID20", relationship = "one-to-one") |>
  filter(!is.na(mammo_prev))

# Low match rate = tract vintage mismatch (PLACES 2010 vs ACS 2020 tracts)
cat(sprintf("Matched %s of %s tracts (%.1f%%)\n", comma(nrow(val)), comma(nrow(df_classification)),
            100 * nrow(val) / nrow(df_classification)))

q20     <- quantile(val$mammo_prev, 0.2)          # "low screening" = bottom 20% of tracts
val$low <- val$mammo_prev <= q20

# ---- Helpers --------------------------------------------------------------------
# AUC: how well a need score (higher = more need) picks out low-screening tracts (0.5 = chance)
auc <- function(score, y) {
  ok <- !is.na(score) & !is.na(y); s <- score[ok]; y <- y[ok]
  n1 <- sum(y); n0 <- sum(!y)
  (sum(rank(s)[y]) - n1 * (n1 + 1) / 2) / (n1 * n0)
}

# Spearman rho with screening (expect negative) and AUC for low screening
assoc <- function(d, vars, y_low = d$low) map_dfr(vars, \(v) {
  ok <- complete.cases(d[[v]], d$mammo_prev)
  tibble(measure = v, n = sum(ok),
         rho = cor(d[[v]][ok], d$mammo_prev[ok], method = "spearman"),
         auc = auc(d[[v]], y_low))
})

# ---- 3. Tract-level measures: access and barriers -----------------------------
tract_measures <- c("pctl_access_ur", "pctl_access_nat", "NEAR_DIST",
                    "pctl_barriers_ur", "pctl_barriers_nat", barrier_vars, "pct_poverty_f35_74")

assoc_tract <- bind_rows(
  assoc(val, tract_measures) |> mutate(group = "All tracts"),
  val |> filter(!is.na(rurality)) |> group_by(group = rurality) |>
    group_modify(\(d, k) assoc(d, tract_measures, d$low)) |> ungroup()
) |> arrange(group, rho)

print(assoc_tract, n = Inf)

# ---- 4. County-level measures: burden ----------------------------------------
# Burden is county-level, so compare at the county level (screening weighted by women 50-74)
county_val <- val |>
  group_by(burden_FIPS) |>
  summarise(mammo_prev = weighted.mean(mammo_prev, pmax(n_women_50_74, 1)), .groups = "drop") |>
  left_join(county_burden, by = c("burden_FIPS" = "FIPS")) |>
  mutate(low = mammo_prev <= quantile(mammo_prev, 0.2, na.rm = TRUE))

assoc_county <- assoc(county_val, names(burden_cols), county_val$low)
print(assoc_county)

# ---- 5. Screening by priority class (default cutoffs) -------------------------
val_long <- val |>
  pivot_longer(starts_with("class_"), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class") |>
  mutate(class = replace_na(class, "No data"))

class_summary <- val_long |>
  group_by(burden_measure, class) |>
  summarise(tracts = n(), mean_prev = mean(mammo_prev), median_prev = median(mammo_prev),
            pct_low_screening = 100 * mean(low), .groups = "drop") |>
  arrange(burden_measure, mean_prev)

print(class_summary, n = Inf)

# ---- 6. Every cutoff combination: do high-priority tracts screen less? --------
combo_val <- expand_grid(scale = c("nat", "ur"), burden_measure = names(burden_cols),
                         burden_cut = cut_options, access_cut = cut_options, barrier_cut = cut_options) |>
  mutate(res = pmap(list(scale, burden_measure, burden_cut, access_cut, barrier_cut), \(s, b, bc, ac, rc) {
    hp <- classify_at(val, b, c(burden = bc, access = ac, barriers = rc), s) %in% "High priority"
    tibble(hp_tracts  = sum(hp),
           prev_hp    = mean(val$mammo_prev[hp]),
           prev_other = mean(val$mammo_prev[!hp]),
           ppv_low    = mean(val$low[hp]),    # share of high-priority tracts with low screening
           sens_low   = mean(hp[val$low]))    # share of low-screening tracts flagged high priority
  })) |>
  unnest(res) |>
  mutate(diff_prev = prev_hp - prev_other,    # negative = high-priority tracts screen less
         lift      = ppv_low / 0.2)           # >1 = better than chance at finding low screening

# Best-aligned combinations (minimum 30 high-priority tracts for stable estimates)
combo_val |> filter(hp_tracts >= 30) |> arrange(desc(lift)) |> print(n = 20)

# ---- 7. Figures -----------------------------------------------------------------
p_assoc <- assoc_tract |>
  ggplot(aes(rho, fct_reorder(measure, rho), fill = rho < 0)) +
  geom_col() +
  facet_wrap(~ group) +
  scale_fill_manual(values = c(`TRUE` = "#2b8cbe", `FALSE` = "#bdbdbd"), guide = "none") +
  theme_minimal(base_size = 11) +
  labs(title = "Correlation of tract measures with PLACES mammography screening",
       subtitle = "Spearman rho; negative = higher need goes with lower screening",
       x = "Spearman rho", y = NULL)

p_class <- val_long |>
  ggplot(aes(mammo_prev, fct_reorder(class, mammo_prev, median))) +
  geom_boxplot(outlier.size = 0.3, fill = "#deebf7") +
  facet_wrap(~ burden_measure) +
  theme_minimal(base_size = 11) +
  labs(title = "PLACES mammography screening by priority class (default cutoffs)",
       x = "% women 50-74 screened (PLACES)", y = NULL)

p_combo <- combo_val |>
  filter(scale == pctl_scale) |>
  ggplot(aes(factor(access_cut), factor(barrier_cut), fill = lift)) +
  geom_tile(color = "white") +
  geom_text(aes(label = if_else(hp_tracts >= 30, sprintf("%.2f", lift), "n<30")), size = 3) +
  facet_grid(paste0("Burden ", burden_cut, "th") ~ burden_measure) +
  scale_fill_gradient2(midpoint = 1, low = "#bdbdbd", mid = "white", high = "#b2182b") +
  theme_minimal(base_size = 11) +
  labs(title = "How well high-priority tracts find low-screening tracts",
       subtitle = "Lift = share of high-priority tracts in bottom 20% of screening / 0.20 (1 = chance)",
       x = "Access cutoff", y = "Barrier cutoff", fill = "Lift")

p_assoc; p_class; p_combo

# ---- 8. Save --------------------------------------------------------------------
write_csv(assoc_tract,   file.path(out_dir, "validation_tract_measures.csv"))
write_csv(assoc_county,  file.path(out_dir, "validation_county_burden.csv"))
write_csv(class_summary, file.path(out_dir, "validation_by_class.csv"))
write_csv(combo_val,     file.path(out_dir, "validation_cutoff_combos.csv"))
ggsave(file.path(out_dir, "fig_validation_assoc.png"), p_assoc, width = 11, height = 6, dpi = 300, bg = "white")
ggsave(file.path(out_dir, "fig_validation_class.png"), p_class, width = 11, height = 6, dpi = 300, bg = "white")
ggsave(file.path(out_dir, "fig_validation_combos.png"), p_combo, width = 11, height = 8, dpi = 300, bg = "white")