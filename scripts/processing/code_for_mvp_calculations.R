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
#   counties, and classify tracts into priority classes separately for each
#   burden measure.
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
#      counties with many tracts shifted the tertile cutoffs.
#   4. case_when(): `burden = 1` replaced with `==`, and tracts with a barrier
#      score of exactly 6 now receive a class (cutoff is >= 6; see settings).
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
#      and joined to tracts by county FIPS (first 5 digits of GEOID), replacing
#      the tract-joined incidence file. This keeps all counties, including
#      suppressed ones, and avoids tracts that failed to join.
#
#   Robustness and clean-up
#  10. ACS columns are referenced by explicit variable lists, so results do not
#      depend on column order in the CSV.
#  11. Margin-of-error columns are dropped with a precise pattern ("_###M").
#  12. merge() replaced with left_join() with explicit relationships, plus
#      checks for unmatched tracts and counties.
#  13. Tertile labels use cut() with explicit breaks; the original nested
#      ifelse() returned correct results only because two errors cancelled out.
#  14. Removed setwd() and personal paths; write_csv() no longer adds a
#      row-number column.
#  15. Added a long-format output for the app's burden-measure selector.
#  16. Connecticut tracts assigned to pre-2022 counties and Alaska census 
#      areas 02063/02066 mapped to 02261 to match cancer data geography.
#  17. Burden tertile columns selected by exact name (burden_t_cols), so
#      burden_FIPS is not classified as a burden measure.
#
# Open questions for the team
#   - Confirm the barrier cutoff (>= 6 on a 4-12 scale) and record it in the
#     decision log.
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
# the file. Update the values to match the county file.
fips_col    <- "FIPS"
burden_cols <- c(aair       = "AAIR",
                 cases      = "Ave_annual_count",
                 late_stage = "Percent_late_stage")

burden_t_cols <- paste0("burden_", names(burden_cols))   # burden_aair, burden_cases, burden_late_stage

barrier_vars   <- c("pct_uninsured_f35_74", "pct_no_vehicle_hh",
                    "pct_no_hs_f25", "pct_lep_hh")
barrier_cutoff <- 6             # barrier score range is 4-12

# ---- Helpers ----------------------------------------------------------------
fix_geoid <- function(x, n) str_pad(as.character(x), n, pad = "0")
pct       <- function(num, den) if_else(den > 0, 100 * num / den, NA_real_)
acs_vars  <- function(table, nums) sprintf("%s_%03dE", table, nums)
row_sum   <- function(cols) rowSums(pick(all_of(cols)))

# Suppression markers ("*", "3 or fewer", "data not available") become NA.
# Commas are removed first so "1,234" stays a number.
to_num <- function(x) suppressWarnings(as.numeric(str_remove_all(x, ",")))

pctl    <- function(x) 100 * percent_rank(x)
tertile <- function(x) as.integer(cut(pctl(x), c(0, 33.33, 66.67, 100),
                                      include.lowest = TRUE))   # 1 low, 2 moderate, 3 high
tertile_label <- function(t) factor(t, levels = 1:3, labels = c("low", "moderate", "high"))

classify <- function(burden, access, barriers) case_when(
  is.na(access) | is.na(barriers)                           ~ NA_character_,  # missing tract data
  is.na(burden) & (access > 1 | barriers >= barrier_cutoff) ~ "Burden suppressed: gaps present",
  is.na(burden)                                             ~ "Burden suppressed: no gaps",
  burden == 1                                               ~ "Lower priority",
  access > 1 & barriers >= barrier_cutoff                   ~ "High priority",
  access > 1                                                ~ "Access gap",
  barriers >= barrier_cutoff                                ~ "Barrier gap",
  .default                                                  = "Monitor")

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


# county mis-matches, fixes:
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
  left_join(ct_lookup, by = "GEOID20", relationship = "one-to-one") |>
  mutate(burden_FIPS = coalesce(burden_FIPS,
                                unname(ak_recode[FIPS]),
                                FIPS))

# Check: should now return 0 rows
df_calcs |> distinct(burden_FIPS, state) |>
  anti_join(county_burden, by = c("burden_FIPS" = "FIPS"))


write_csv(df_calcs, file.path(out_dir, "df_w_calcs.csv"))

# ---- County burden (one row per county) -------------------------------------
# Read everything as text: numeric FIPS lose leading zeros, and suppressed
# values are text markers. If the file has title rows above the header
# (common in State Cancer Profiles exports), add skip = <n> to read_csv().
county_raw <- read_csv(file.path(data_dir, burden_file),
                       col_types = cols(.default = "c"))

county_raw  <- county_raw  %>%
  mutate(County = str_squish(str_remove_all(County, "\\([^)]*\\)")),
         Urban_rural = `2023 Rural-Urban Continuum Codes([rural urban note])`,
         AAIR = `Age-Adjusted Incidence Rate([rate note]) - cases per 100,000`,
         Ave_annual_count = `Average Annual Count`,
         Percent_late_stage = `Percent of Cases with Late Stage`) %>%
  select(FIPS, Urban_rural, AAIR, Ave_annual_count, Percent_late_stage)

county_burden <- county_raw |>
  transmute(FIPS = str_pad(str_trim(.data[[fips_col]]), 5, pad = "0"),
            across(all_of(burden_cols), to_num)) |>          # renames to aair, cases, late_stage
  filter(str_detect(FIPS, "^\\d{5}$"),                       # drops footnote rows
         !str_ends(FIPS, "000")) |>                          # drops US and state rows
  mutate(across(all_of(names(burden_cols)), pctl,    .names = "pctl_{.col}"),
         across(all_of(names(burden_cols)), tertile, .names = "burden_{.col}"))

stopifnot("County burden must be one row per county" = !anyDuplicated(county_burden$FIPS))

nrow(county_burden)                                                          # ~3,143
county_burden |> summarise(across(all_of(names(burden_cols)), ~ sum(is.na(.x))))  # suppressed per measure

# Tract counties with no row in the county file (check Connecticut planning
# regions and recent Alaska boundary changes if any appear)
df_calcs |>
  distinct(FIPS, state) |>
  anti_join(county_burden, by = "FIPS") |>
  count(state, sort = TRUE) |>
  print()

# ---- Mammography distance ---------------------------------------------------
mam_distance <- read_csv(file.path(data_dir, distance_file),
                         col_types = cols(GEOID20 = "c")) |>
  transmute(GEOID20 = fix_geoid(GEOID20, 11), NEAR_DIST)   # meters

df_all <- df_calcs |>
  left_join(mam_distance, by = "GEOID20", relationship = "one-to-one") |>
  left_join(county_burden, by = c("burden_FIPS" = "FIPS"), relationship = "many-to-one")

df_all |>
  summarise(n_tracts = n(),
            across(c(NEAR_DIST, all_of(names(burden_cols))), ~ sum(is.na(.x)))) |>
  print()

# ---- Tract percentiles, tertiles, and classification ------------------------
df_class <- df_all |>
  mutate(across(all_of(barrier_vars), pctl,    .names = "pctl_{.col}"),
         across(all_of(barrier_vars), tertile, .names = "{.col}_t"),
         pctl_NEAR_DIST = pctl(NEAR_DIST),
         access   = tertile(NEAR_DIST),
         barriers = rowSums(pick(all_of(paste0(barrier_vars, "_t"))))) |>   # 4-12
  mutate(across(all_of(burden_t_cols), ~ classify(.x, access, barriers),
                .names = "{sub('burden_', 'class_', .col)}"))

# Percentiles (0-100); burden percentiles are county-level
df_percentiles <- df_class |>
  select(FIPS, GEOID20, county, state, acs_period, starts_with("pctl_"))

# Low / moderate / high labels
df_rank <- df_class |>
  transmute(FIPS, GEOID20, county, state, acs_period,
            across(c(paste0(barrier_vars, "_t"), access, all_of(burden_t_cols)),
                   tertile_label))

# Wide classification: one column per burden measure
df_classification <- df_class |>
  select(FIPS, GEOID20, county, state, acs_period, all_of(names(burden_cols)),
         barriers, access, all_of(burden_t_cols), starts_with("class_"))

# Long classification for the app's burden-measure selector
df_classification_long <- df_classification |>
  pivot_longer(starts_with("class_"), names_to = "burden_measure",
               names_prefix = "class_", values_to = "class")

# How often the burden views disagree
count(df_classification, class_aair, class_cases) |> print(n = Inf)

write_csv(county_burden,          file.path(out_dir, "county_burden.csv"))
write_csv(df_percentiles,         file.path(out_dir, "df_percentiles.csv"))
write_csv(df_rank,                file.path(out_dir, "df_rank.csv"))
write_csv(df_classification,      file.path(out_dir, "df_classification.csv"))
write_csv(df_classification_long, file.path(out_dir, "df_classification_long.csv"))
