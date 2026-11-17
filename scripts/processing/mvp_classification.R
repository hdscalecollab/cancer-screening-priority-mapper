# =============================================================================
# Cancer Screening Priority Mapper - MVP tract classification
# NCI Data Jamboree 2026
#
# Original code: Alexis Carey
# Revisions:     Calvin Tribby
#
# Purpose
#   Build tract-level barrier and access indicators (ACS 2019-2023, mammography
#   distance), rank county-level late-stage breast cancer burden across
#   counties, rank access and barriers within urban and rural tracts, and
#   classify tracts into priority classes for each burden measure at every
#   combination of user-selectable cutoffs (67th, 80th, 90th percentile).
#
# Summary of changes from the original script
#   Bug fixes (these changed results)
#   1. GEOID is read as character and zero-padded. read.csv() read it as a
#      number, which dropped leading zeros (e.g., CA "06...") and produced
#      wrong county FIPS codes for 9 states (AL, AK, AZ, AR, CA, CO, CT, DE, DC).
#   2. Poverty denominator now = below + at/above poverty (B17001_026-029 +
#      _055-058). The original divided by the at/above-poverty count only.
#   3. County burden is ranked across counties (one row per county), not across
#      tracts. Ranking tract rows counted each county once per tract, so
#      counties with many tracts shifted the cutoffs.
#   4. case_when(): `burden = 1` replaced with `==`, and tracts on the barrier
#      cutoff now receive a class.
#   5. Columns are selected by name, not position. c(6:25) after merge() picked
#      up the wrong variables (join key moved, .x/.y suffixes added) and ranked
#      raw counts.
#
#   Design changes
#   6. The three burden measures (AAIR, average annual cases, % late stage) are
#      kept separate, giving three classifications per tract: relative burden,
#      absolute burden, and late-stage share.
#   7. Counties with suppressed burden values get their own classes ("Burden
#      suppressed: gaps present / no gaps") instead of being dropped or
#      labeled "Monitor".
#   8. Added women 50-74 (matches PLACES mammography age range) and labeled
#      uninsured and poverty measures with their actual age range (35-74).
#   9. County burden is read from the county-level State Cancer Profiles file
#      and joined to tracts by county FIPS, replacing the tract-joined
#      incidence file. This keeps all counties, including suppressed ones.
#
#   Robustness and clean-up
#  10. ACS columns are referenced by explicit variable lists, so results do not
#      depend on column order in the CSV.
#  11. Margin-of-error columns are dropped with a precise pattern ("_###M").
#  12. merge() replaced with left_join() with explicit relationships, plus
#      checks for unmatched tracts and counties.
#  13. The original nested ifelse() tertile labels returned correct results
#      only because two errors cancelled out (tertiles since replaced; #18).
#  14. Removed setwd() and personal paths; write_csv() no longer adds a
#      row-number column.
#  15. Added a long-format output for the app's burden-measure selector.
#  16. Connecticut tracts assigned to pre-2022 counties and Alaska census
#      areas 02063/02066 mapped to 02261 (burden_FIPS) to match cancer data
#      geography. County FIPS 51019 was "N/A". Manual look-up reveals it is urban. 
#      Changed in county burden section.
#  17. Burden columns selected by exact name, so burden_FIPS is not treated
#      as a burden measure.
#
#   Cutoffs, urban/rural stratification, and sensitivity (team meeting)
#  18. Tertiles replaced with percentile cutoffs. The summed barrier tertile
#      score is replaced by a barrier index: the mean of the four barrier
#      percentiles, re-ranked across tracts.
#  19. Access and barrier percentiles are ranked within urban and rural tracts
#      separately, using the county file's Urban/Rural label. National
#      percentiles are kept (*_nat). Tracts whose county has no label fall
#      back to national percentiles. Burden stays ranked nationally across
#      counties.
#  20. Removed df_rank.csv (tertile labels no longer used).
#
#   User-selected cutoffs for the web app
#  21. Each domain (burden, access, barriers) can be set independently to the
#      67th, 80th, or 90th percentile (`cut_options`): 27 combinations per
#      burden measure.
#  22. Each tract gets a "level" per domain: the highest cutoff it clears
#      (0, 67, 80, 90; lvl_* columns). A domain is "high" when level >=
#      the selected cutoff, so the app can classify any combination from one
#      small file without recomputing percentiles.
#  23. All combinations are precomputed as integer class codes
#      (df_classification_all_cutoffs.csv, lookup in class_codes.csv), for
#      apps that read classes directly instead of computing them.
#  24. The sensitivity grid is replaced by cutoff_grid.csv: tract and women
#      counts for every class at every combination, by burden measure and
#      ranking scale. `sensitivity` (access cutoff = barrier cutoff) is kept
#      for the figures script.
#
# Open questions for the team
#   - Confirm the app's default cutoffs (`cutoffs`) and ranking scale
#     (`pctl_scale`) and record them in the decision log.
# =============================================================================

library(tidyverse)
library(sf)
library(tigris)
options(tigris_use_cache = TRUE)

# ---- Settings ---------------------------------------------------------------
data_dir <- "C:/NCI_Data"          # local data folder (not committed to GitHub)
out_dir  <- data_dir

acs_file      <- "acs5_2019_2023_tract_MVP v2.csv"
burden_file   <- "US_Counties_Late_Stage_Breast_Cancer_2019-2023(in).csv"
distance_file <- "mammography_distance.csv"

# County file columns. Names = names used in this script; values = names in
# county_raw after the rename step below.
fips_col    <- "FIPS"
burden_cols <- c(aair       = "AAIR",
                 cases      = "Ave_annual_count",
                 late_stage = "Percent_late_stage")

barrier_vars <- c("pct_uninsured_f35_74", "pct_no_vehicle_hh",
                  "pct_no_hs_f25", "pct_lep_hh")

# Cutoffs users can choose in the app, for each domain independently
cut_options <- c(67, 80, 90)

# Default cutoffs shown when the app opens (must be in cut_options)
cutoffs <- c(burden = 90, access = 67, barriers = 67)
stopifnot("Default cutoffs must be in cut_options" = all(cutoffs %in% cut_options))

# Percentile scale for access and barriers: "ur" = ranked within urban/rural,
# "nat" = ranked nationally. Used for the default and precomputed classes;
# cutoff_grid.csv reports both.
pctl_scale <- "ur"

# Class labels, in display order, with integer codes for the precomputed file
class_levels <- c("High priority", "Access gap", "Barrier gap", "Monitor",
                  "Lower priority", "Burden suppressed: gaps present",
                  "Burden suppressed: no gaps")

# ---- Helpers ----------------------------------------------------------------
fix_geoid <- function(x, n) str_pad(as.character(x), n, pad = "0")
pct       <- function(num, den) if_else(den > 0, 100 * num / den, NA_real_)
acs_vars  <- function(table, nums) sprintf("%s_%03dE", table, nums)
row_sum   <- function(cols) rowSums(pick(all_of(cols)))

# Suppression markers ("*", "3 or fewer", "data not available") become NA.
# Commas are removed first so "1,234" stays a number.
to_num <- function(x) suppressWarnings(as.numeric(str_remove_all(x, ",")))

pctl <- function(x) 100 * percent_rank(x)   # 0 = lowest, 100 = highest

# Highest cutoff a percentile clears: 0, 67, 80, or 90 (NA stays NA).
# level >= cut is identical to percentile >= cut for any cut in cut_options.
pctl_level <- function(p, cuts = cut_options) {
  out <- if_else(is.na(p), NA_integer_, 0L)
  for (k in sort(cuts)) out[!is.na(p) & p >= k] <- as.integer(k)
  out
}

classify <- function(burden_hi, access_gap, barrier_hi) case_when(
  is.na(access_gap) | is.na(barrier_hi)        ~ NA_character_,   # missing tract data
  is.na(burden_hi) & (access_gap | barrier_hi) ~ "Burden suppressed: gaps present",
  is.na(burden_hi)                             ~ "Burden suppressed: no gaps",
  !burden_hi                                   ~ "Lower priority",
  access_gap & barrier_hi                      ~ "High priority",
  access_gap                                   ~ "Access gap",
  barrier_hi                                   ~ "Barrier gap",
  .default                                     = "Monitor")

# Classify tracts for one burden measure at a set of cutoffs and scale.
# Uses levels, so the same function works in a Shiny app on df_app.csv.
classify_at <- function(data, burden, cut = cutoffs, scale = pctl_scale) {
  classify(
    burden_hi  = data[[paste0("lvl_", burden)]]          >= cut[["burden"]],   # county-level
    access_gap = data[[paste0("lvl_access_", scale)]]    >= cut[["access"]],
    barrier_hi = data[[paste0("lvl_barriers_", scale)]]  >= cut[["barriers"]])
}

snake <- function(x) str_remove(str_replace_all(str_to_lower(x), "[^a-z0-9]+", "_"), "_$")

# ---- ACS tract indicators ---------------------------------------------------
acs <- read_csv(file.path(data_dir, acs_file),
                col_types = cols(GEOID = "c", NAME = "c", acs_period = "c", .default = "d"))

df_calcs <- acs |>
  mutate(GEOID = fix_geoid(GEOID, 11)) |>
  separate_wider_delim(NAME, delim = "; ", names = c("census_tract", "county", "state")) |>
  select(!matches("_\\d{3}M$")) |>                  # drop margin-of-error columns
  transmute(
    FIPS = str_sub(GEOID, 1, 5), GEOID20 = GEOID, county, state, acs_period,
    
    # Screening-eligible women
    n_women_40_74 = row_sum(acs_vars("B01001", 38:46)),
    n_women_40_49 = row_sum(acs_vars("B01001", 38:39)),
    n_women_50_74 = row_sum(acs_vars("B01001", 40:46)),
    
    # Race/ethnicity (% of total population)
    pct_nh_white = pct(B03002_003E, B03002_001E),
    pct_nh_black = pct(B03002_004E, B03002_001E),
    pct_nh_aian  = pct(B03002_005E, B03002_001E),
    pct_nh_asian = pct(B03002_006E, B03002_001E),
    pct_hispanic = pct(B03002_012E, B03002_001E),
    
    # ICE race + income (high = more privileged); context only, not in score
    ice_race_income = (row_sum(acs_vars("B19001H", 14:17)) -
                         row_sum(acs_vars("B19001B", 2:5))) / B19001_001E,
    
    # Barriers
    pct_uninsured_f35_74 = pct(row_sum(acs_vars("B27001", c(45, 48, 51, 54))),
                               row_sum(acs_vars("B27001", c(43, 46, 49, 52)))),
    pct_poverty_f35_74   = pct(row_sum(acs_vars("B17001", 26:29)),
                               row_sum(acs_vars("B17001", c(26:29, 55:58)))),
    pct_no_hs_f25        = pct(row_sum(acs_vars("B15002", 20:27)), B15002_019E),
    pct_lep_hh           = pct(row_sum(acs_vars("C16002", c(4, 7, 10, 13))), C16002_001E),
    pct_no_vehicle_hh    = pct(B25044_003E + B25044_010E, B25044_001E)
  )

# ---- County mismatches: Connecticut and Alaska -------------------------------
# Connecticut: assign each tract (planning-region GEOID) to its pre-2022 county
ct_tracts <- tracts("CT", year = 2023, cb = FALSE) |>      # full boundaries, includes water tracts
  st_point_on_surface() |>
  select(GEOID20 = GEOID)
ct_counties <- counties("CT", year = 2020, cb = FALSE) |>  # full boundaries extend into the Sound
  select(burden_FIPS = GEOID) |>
  st_transform(st_crs(ct_tracts))

ct_lookup <- st_join(ct_tracts, ct_counties, join = st_nearest_feature) |>  # every tract gets a county
  st_drop_geometry()

stopifnot(!anyDuplicated(ct_lookup$GEOID20), !anyNA(ct_lookup$burden_FIPS))

# Alaska: post-2019 census areas -> Valdez-Cordova as used in the cancer file
ak_recode <- c("02063" = "02261", "02066" = "02261")

df_calcs <- df_calcs |>
  select(-starts_with("burden_FIPS")) |>          # safe to rerun
  left_join(ct_lookup, by = "GEOID20", relationship = "one-to-one") |>
  mutate(burden_FIPS = coalesce(burden_FIPS, unname(ak_recode[FIPS]), FIPS))

write_csv(df_calcs, file.path(out_dir, "df_w_calcs.csv"))

# ---- County burden and urban/rural (one row per county) ---------------------
# Read everything as text: numeric FIPS lose leading zeros, and suppressed
# values are text markers.
county_raw <- read_csv(file.path(data_dir, burden_file),
                       col_types = cols(.default = "c"))

county_raw <- county_raw |>
  mutate(County             = str_squish(str_remove_all(County, "\\([^)]*\\)")),
         Urban_rural        = `2023 Rural-Urban Continuum Codes([rural urban note])`,
         AAIR               = `Age-Adjusted Incidence Rate([rate note]) - cases per 100,000`,
         Ave_annual_count   = `Average Annual Count`,
         Percent_late_stage = `Percent of Cases with Late Stage`) |>
  select(FIPS, Urban_rural, AAIR, Ave_annual_count, Percent_late_stage)

county_burden <- county_raw |>
  transmute(FIPS = str_pad(str_trim(.data[[fips_col]]), 5, pad = "0"),
            rurality = case_when(str_squish(Urban_rural) == "Urban" ~ "Urban",
                                 str_squish(Urban_rural) == "Rural" ~ "Rural",
                                 str_squish(Urban_rural) == "N/A" ~ "Urban"),   # "N/A" becomes Urban (FIPS == 51019, which is an urban county)
            across(all_of(burden_cols), to_num)) |>
  filter(str_detect(FIPS, "^\\d{5}$"),
         !str_ends(FIPS, "000")) |>
  mutate(across(all_of(names(burden_cols)), pctl, .names = "pctl_{.col}"))   # ranked across counties

stopifnot("County burden must be one row per county" = !anyDuplicated(county_burden$FIPS))
count(county_burden, rurality) |> print()        # expect ~1,187 Urban, ~1,956 Rural

nrow(county_burden)                                                               # ~3,144
county_burden |> summarise(across(all_of(names(burden_cols)), ~ sum(is.na(.x))))  # suppressed per measure

# Tract counties with no row in the county file (should return 0 rows)
df_calcs |>
  distinct(burden_FIPS, state) |>
  anti_join(county_burden, by = c("burden_FIPS" = "FIPS")) |>
  print()

# ---- Mammography distance and join -------------------------------------------
mam_distance <- read_csv(file.path(data_dir, distance_file),
                         col_types = cols(GEOID20 = "c")) |>
  transmute(GEOID20 = fix_geoid(GEOID20, 11), NEAR_DIST)   # meters

df_all <- df_calcs |>
  left_join(mam_distance, by = "GEOID20", relationship = "one-to-one") |>
  left_join(county_burden, by = c("burden_FIPS" = "FIPS"), relationship = "many-to-one")

stopifnot("No rural tracts found; check Urban_rural" =
            any(df_all$rurality == "Rural", na.rm = TRUE))

df_all |>
  summarise(n_tracts = n(),
            across(c(NEAR_DIST, rurality, all_of(names(burden_cols))), ~ sum(is.na(.x)))) |>
  print()

# Tracts with no Urban/Rural label (these use national percentiles)
df_all |> filter(is.na(rurality)) |> count(state, burden_FIPS) |> print(n = Inf)

# ---- Tract percentiles: national and urban/rural-stratified -----------------
df_scores <- df_all |>
  # National ranking across all tracts
  mutate(across(all_of(barrier_vars), pctl, .names = "pctl_nat_{.col}"),
         barrier_index_nat = rowMeans(pick(all_of(paste0("pctl_nat_", barrier_vars)))),
         pctl_barriers_nat = pctl(barrier_index_nat),
         pctl_access_nat   = pctl(NEAR_DIST)) |>
  # Ranking within urban and within rural tracts
  group_by(rurality) |>
  mutate(across(all_of(barrier_vars), pctl, .names = "pctl_ur_{.col}"),
         barrier_index_ur = rowMeans(pick(all_of(paste0("pctl_ur_", barrier_vars)))),
         pctl_barriers_ur = pctl(barrier_index_ur),
         pctl_access_ur   = pctl(NEAR_DIST)) |>
  ungroup() |>
  # Tracts with unknown rurality fall back to national percentiles
  mutate(across(c(starts_with("pctl_ur_"), barrier_index_ur, pctl_barriers_ur, pctl_access_ur),
                ~ if_else(is.na(rurality), NA_real_, .x)),
         pctl_barriers_ur = coalesce(pctl_barriers_ur, pctl_barriers_nat),
         pctl_access_ur   = coalesce(pctl_access_ur,   pctl_access_nat))

# ---- Levels: highest cutoff each domain clears (0, 67, 80, 90) --------------
level_src <- c(paste0("pctl_", names(burden_cols)),
               "pctl_access_nat", "pctl_access_ur", "pctl_barriers_nat", "pctl_barriers_ur")

df_scores <- df_scores |>
  mutate(across(all_of(level_src), pctl_level, .names = "{sub('^pctl_', 'lvl_', .col)}"))
# -> lvl_aair, lvl_cases, lvl_late_stage, lvl_access_nat, lvl_access_ur,
#    lvl_barriers_nat, lvl_barriers_ur

# Check: levels reproduce percentile cutoffs exactly (should be TRUE)
stopifnot(identical(df_scores$lvl_access_ur >= 80, df_scores$pctl_access_ur >= 80))

# ---- Default classification (app opening view) --------------------------------
df_class <- df_scores
for (b in names(burden_cols)) df_class[[paste0("class_", b)]] <- classify_at(df_scores, b)

# Class counts by burden measure and urban/rural at the default cutoffs
df_class |>
  pivot_longer(starts_with("class_"), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class") |>
  count(burden_measure, rurality, class) |>
  pivot_wider(names_from = rurality, values_from = n, values_fill = 0) |>
  print(n = Inf)

# ---- Cutoff grid: every combination (summary for the app and decision log) ----
# 2 scales x 3 burden measures x 3 x 3 x 3 cutoffs = 162 rows
cutoff_grid <- expand_grid(scale          = c("nat", "ur"),
                           burden_measure = names(burden_cols),
                           burden_cut     = cut_options,
                           access_cut     = cut_options,
                           barrier_cut    = cut_options)

summarise_combo <- function(scale, burden_measure, burden_cut, access_cut, barrier_cut) {
  cls <- classify_at(df_scores, burden_measure,
                     c(burden = burden_cut, access = access_cut, barriers = barrier_cut), scale)
  hp  <- cls %in% "High priority"
  class_n <- setNames(as.list(as.integer(table(factor(cls, class_levels)))),
                      paste0("n_", snake(class_levels)))
  bind_cols(
    tibble(hp_tracts      = sum(hp),
           hp_urban       = sum(hp & df_scores$rurality %in% "Urban"),
           hp_rural       = sum(hp & df_scores$rurality %in% "Rural"),
           hp_women_40_74 = sum(df_scores$n_women_40_74[hp], na.rm = TRUE),
           hp_counties    = n_distinct(df_scores$burden_FIPS[hp]),
           hp_states      = n_distinct(df_scores$state[hp])),
    as_tibble(class_n),
    tibble(n_no_data = sum(is.na(cls))))
}

cutoff_grid <- cutoff_grid |>
  mutate(res = pmap(cutoff_grid, summarise_combo)) |>
  unnest(res) |>
  mutate(pct_of_tracts = round(100 * hp_tracts / nrow(df_scores), 2),
         current       = scale == pctl_scale & burden_cut == cutoffs[["burden"]] &
           access_cut == cutoffs[["access"]] & barrier_cut == cutoffs[["barriers"]])

# Compact view: high-priority tracts at the selected scale
cutoff_grid |>
  filter(scale == pctl_scale) |>
  select(burden_cut, access_cut, barrier_cut, burden_measure, hp_tracts) |>
  pivot_wider(names_from = burden_measure, values_from = hp_tracts) |>
  print(n = Inf)

# Sensitivity view for the figures script (access cutoff = barrier cutoff)
sensitivity <- cutoff_grid |>
  filter(access_cut == barrier_cut) |>
  rename(tract_cut = access_cut) |>
  select(-barrier_cut)

# ---- Precomputed classes for every combination (integer codes) ---------------
# One column per burden measure x cutoff combination, at pctl_scale:
#   cls_<measure>_b<burden>_a<access>_r<barriers>, e.g. cls_aair_b90_a67_r67
class_codes <- tibble(class_code = seq_along(class_levels), class = class_levels)

combos <- expand_grid(burden_measure = names(burden_cols),
                      burden_cut = cut_options, access_cut = cut_options,
                      barrier_cut = cut_options)

precomputed <- pmap(combos, \(burden_measure, burden_cut, access_cut, barrier_cut) {
  match(classify_at(df_scores, burden_measure,
                    c(burden = burden_cut, access = access_cut, barriers = barrier_cut)),
        class_levels)                                          # NA = no data
}) |>
  set_names(with(combos, sprintf("cls_%s_b%s_a%s_r%s",
                                 burden_measure, burden_cut, access_cut, barrier_cut)))

df_classification_all_cutoffs <- bind_cols(select(df_scores, GEOID20), as_tibble(precomputed))

# ---- Outputs ------------------------------------------------------------------
id_cols <- c("FIPS", "burden_FIPS", "GEOID20", "county", "state", "acs_period", "rurality")

# Percentiles (0-100); burden percentiles are county-level and national
df_percentiles <- df_class |>
  select(all_of(id_cols), starts_with("pctl_"), barrier_index_nat, barrier_index_ur)

# App file: values, percentiles, levels, and default classes (one row per tract).
# The app classifies any cutoff combination with classify_at() on lvl_* columns.
df_classification <- df_class |>
  select(all_of(id_cols), n_women_40_74, n_women_40_49, n_women_50_74,
         all_of(names(burden_cols)), all_of(paste0("pctl_", names(burden_cols))),
         NEAR_DIST, pctl_access_nat, pctl_access_ur, pctl_barriers_nat, pctl_barriers_ur,
         starts_with("lvl_"), starts_with("class_"))

# Long classification (default cutoffs) for the burden-measure selector
df_classification_long <- df_classification |>
  pivot_longer(starts_with("class_"), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class")

write_csv(county_burden,                 file.path(out_dir, "county_burden.csv"))
write_csv(df_percentiles,                file.path(out_dir, "df_percentiles.csv"))
write_csv(df_classification,             file.path(out_dir, "df_classification.csv"))
write_csv(df_classification_long,        file.path(out_dir, "df_classification_long.csv"))
write_csv(df_classification_all_cutoffs, file.path(out_dir, "df_classification_all_cutoffs.csv"))
write_csv(class_codes,                   file.path(out_dir, "class_codes.csv"))
write_csv(cutoff_grid,                   file.path(out_dir, "cutoff_grid.csv"))
write_csv(sensitivity,                   file.path(out_dir, "sensitivity_grid.csv"))
