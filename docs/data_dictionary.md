# Data dictionary: MVP classification outputs

Produced by `scripts/processing/code_for_mvp_classification_v2.R`. All files are UTF-8 CSV.

**Conventions**

- `NA` means the value is missing: suppressed, unavailable, or a denominator of zero.
- Percentiles are `100 * dplyr::percent_rank()`. The lowest value is 0 and the highest is 100. Ties share the lowest rank.
- Tertiles are percentiles cut at 33.33 and 66.67: **1 = low, 2 = moderate, 3 = high**.
- A higher tertile always means **more need**: more barriers, longer distance, or higher burden.
- Tract percentiles and tertiles are ranked across all U.S. tracts. Burden percentiles and tertiles are ranked across counties.

## Identifier columns (all tract-level files)

| Variable | Type | Description |
|---|---|---|
| `FIPS` | text (5) | County FIPS code for the tract's current county (first 5 digits of `GEOID20`). Used for display. |
| `GEOID20` | text (11) | 2020 census tract GEOID. Leading zeros are kept. |
| `county` | text | County name (Connecticut: planning region name). |
| `state` | text | State name. |
| `acs_period` | text | ACS 5-year period (2019-2023). |
| `burden_FIPS` | text (5) | County FIPS used to join the cancer burden data. Same as `FIPS`, except Connecticut (pre-2022 county) and Alaska 02063/02066 (mapped to 02261). |

## `df_w_calcs.csv` — derived ACS indicators (one row per tract)

| Variable | Type | Description | ACS source |
|---|---|---|---|
| `n_women_40_74` | count | Women aged 40-74 | B01001_038-046 |
| `n_women_40_49` | count | Women aged 40-49 (additional population eligible under the age-40 USPSTF recommendation) | B01001_038-039 |
| `n_women_50_74` | count | Women aged 50-74 (age range of the PLACES mammography estimate) | B01001_040-046 |
| `pct_nh_white` | % | Non-Hispanic White, % of total population | B03002_003 / _001 |
| `pct_nh_black` | % | Non-Hispanic Black, % of total population | B03002_004 / _001 |
| `pct_nh_aian` | % | Non-Hispanic American Indian/Alaska Native, % of total population | B03002_005 / _001 |
| `pct_nh_asian` | % | Non-Hispanic Asian, % of total population | B03002_006 / _001 |
| `pct_hispanic` | % | Hispanic or Latino (any race), % of total population | B03002_012 / _001 |
| `ice_race_income` | index (-1 to 1) | Index of Concentration at the Extremes, race + income: (White NH households with income $100k+ minus Black households with income under $25k) / all households. Higher = more privileged. Context only; not used in the score. | B19001H_014-017, B19001B_002-005, B19001_001 |
| `pct_uninsured_f35_74` | % | Women aged 35-74 without health insurance | B27001_045, 048, 051, 054 / _043, 046, 049, 052 |
| `pct_poverty_f35_74` | % | Women aged 35-74 below the poverty level | B17001_026-029 / (_026-029 + _055-058) |
| `pct_no_hs_f25` | % | Women 25+ without a high school diploma | B15002_020-027 / _019 |
| `pct_lep_hh` | % | Limited English-speaking households | C16002_004, 007, 010, 013 / _001 |
| `pct_no_vehicle_hh` | % | Households without a vehicle | B25044_003 + _010 / _001 |

**Barrier indicators used in the score:** `pct_uninsured_f35_74`, `pct_no_vehicle_hh`, `pct_no_hs_f25`, `pct_lep_hh`.

## `county_burden.csv` — county burden (one row per county)

Source: State Cancer Profiles, female breast cancer, late stage, 2019-2023.

| Variable | Type | Description |
|---|---|---|
| `FIPS` | text (5) | County FIPS code as used in the cancer data (Connecticut: pre-2022 counties). |
| `aair` | rate | Age-adjusted annual incidence rate per 100,000 women. Relative burden. |
| `cases` | count | Average annual number of cases. Absolute burden. |
| `late_stage` | % | Percent late stage. |
| `pctl_aair`, `pctl_cases`, `pctl_late_stage` | 0-100 | Percentile rank across counties. |
| `burden_aair`, `burden_cases`, `burden_late_stage` | 1-3 | Burden tertile across counties. |

`NA` means the value was suppressed in State Cancer Profiles (small counts).

## `df_percentiles.csv` — percentile ranks (one row per tract)

| Variable | Type | Description |
|---|---|---|
| `pctl_pct_uninsured_f35_74`, `pctl_pct_no_vehicle_hh`, `pctl_pct_no_hs_f25`, `pctl_pct_lep_hh` | 0-100 | Barrier percentiles, ranked across tracts. |
| `pctl_NEAR_DIST` | 0-100 | Percentile of distance to the nearest mammography facility, ranked across tracts. Higher = farther away. |
| `pctl_aair`, `pctl_cases`, `pctl_late_stage` | 0-100 | The tract's county burden percentiles, ranked across counties. |

## `df_rank.csv` — tertile labels (one row per tract)

Values are `low`, `moderate`, or `high`.

| Variable | Description |
|---|---|
| `pct_uninsured_f35_74_t`, `pct_no_vehicle_hh_t`, `pct_no_hs_f25_t`, `pct_lep_hh_t` | Barrier tertiles |
| `access` | Distance-to-mammography tertile (high = farthest) |
| `burden_aair`, `burden_cases`, `burden_late_stage` | County burden tertiles |

## `df_classification.csv` — priority classes, wide (one row per tract)

| Variable | Type | Description |
|---|---|---|
| `aair`, `cases`, `late_stage` | as above | The tract's county burden values. |
| `barriers` | 4-12 | Barrier score: the sum of the four barrier tertiles. |
| `access` | 1-3 | Distance-to-mammography tertile. |
| `burden_aair`, `burden_cases`, `burden_late_stage` | 1-3 | County burden tertiles. |
| `class_aair`, `class_cases`, `class_late_stage` | text | Priority class for each burden view (see below). |

## `df_classification_long.csv` — priority classes, long (one row per tract per burden measure)

Same columns as `df_classification.csv`, with the three `class_*` columns replaced by:

| Variable | Description |
|---|---|
| `burden_measure` | `aair`, `cases`, or `late_stage` |
| `class` | Priority class for that burden measure |

## Priority classes

Rules are applied in order, and the first rule that matches sets the class. The cutoffs are an **access gap** when `access` is 2 or 3, and **high barriers** when `barriers` is 6 or more. The barrier cutoff is set in the script (`barrier_cutoff`).

| Class | Rule | Interpretation |
|---|---|---|
| `NA` | Access or barrier data missing | Tract data unavailable (for example, a zero-population or water tract). |
| Burden suppressed: gaps present | County burden suppressed; access gap or high barriers | Burden unknown, but the tract has screening gaps. |
| Burden suppressed: no gaps | County burden suppressed; no access gap and low barriers | Burden unknown; no tract-level gaps. |
| Lower priority | Burden tertile = 1 | Low county burden. |
| High priority | Burden 2-3; access gap; high barriers | Elevated burden with both access and barrier gaps. |
| Access gap | Burden 2-3; access gap; low barriers | Elevated burden, mainly an access gap. |
| Barrier gap | Burden 2-3; no access gap; high barriers | Elevated burden, mainly barriers. |
| Monitor | Burden 2-3; no access gap; low barriers | Elevated burden without tract-level gaps. |

**Interpretation:** burden is county-level context and is the same for every tract in a county. Classes describe where tract-level screening gaps fall within high-burden counties. They are not tract-level cancer risk estimates.
