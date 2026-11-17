# Data dictionary: MVP outputs

Produced by scripts/processing/mvp_classification.R (classification outputs) and scripts/processing/mvp_validation.R (validation outputs). All files are UTF-8 CSV.

## Conventions

- NA means the value is missing: suppressed, unavailable, or a denominator of zero.
- **Percentiles** are 100 * dplyr::percent_rank(), 0-100. The lowest value is 0 and the highest is 100. Ties share the lowest rank, and NA values are not ranked.
- A higher percentile always means **more need**: more barriers, longer distance, or higher burden.
- **Burden** percentiles are ranked nationally across counties (one row per county). Tracts take on their county's value.
- **Access and barrier** percentiles come in two versions:
  - _nat: ranked across all U.S. tracts.
  - _ur: ranked within urban tracts and within rural tracts separately, using the county's Urban/Rural label. Tracts whose county has no label use the national percentile.
- **Levels** (lvl_*) are the highest cutoff a percentile clears: 0, 67, 80, or 90. A domain is "high" at a selected cutoff when lvl >= cutoff. This gives the same result as percentile >= cutoff for the app's cutoff options.
- **Cutoff options:** 67th, 80th, or 90th percentile, chosen independently for burden, access, and barriers (27 combinations per burden measure). **Defaults:** burden 90th, access 67th, barriers 67th, with urban/rural ranking.

## Identifier columns

Used in all tract-level files unless noted.

| Variable | Type | Description |
|---|---|---|
| FIPS | text (5) | County FIPS code for the tract's current county (first 5 digits of GEOID20). Used for display. |
| burden_FIPS | text (5) | County FIPS used to join cancer burden and urban/rural. Same as FIPS, except Connecticut (pre-2022 county) and Alaska 02063/02066 (mapped to 02261). |
| GEOID20 | text (11) | 2020 census tract GEOID. Leading zeros are kept. |
| county | text | County name (Connecticut: planning region name). |
| state | text | State name. |
| acs_period | text | ACS 5-year period (2019-2023). |
| rurality | text | Urban or Rural, from the county file's 2023 Rural-Urban Continuum label. NA if the county has no label. Not in df_w_calcs.csv. |

## df_w_calcs.csv: derived ACS indicators (one row per tract)

Includes FIPS, burden_FIPS, GEOID20, county, state, and acs_period, plus:

| Variable | Type | Description | ACS source |
|---|---|---|---|
| n_women_40_74 | count | Women aged 40-74 | B01001_038-046 |
| n_women_40_49 | count | Women aged 40-49 (additional population eligible under the age-40 USPSTF recommendation) | B01001_038-039 |
| n_women_50_74 | count | Women aged 50-74 (age range of the PLACES mammography estimate) | B01001_040-046 |
| pct_nh_white | % | Non-Hispanic White, % of total population | B03002_003 / _001 |
| pct_nh_black | % | Non-Hispanic Black, % of total population | B03002_004 / _001 |
| pct_nh_aian | % | Non-Hispanic American Indian/Alaska Native, % of total population | B03002_005 / _001 |
| pct_nh_asian | % | Non-Hispanic Asian, % of total population | B03002_006 / _001 |
| pct_hispanic | % | Hispanic or Latino (any race), % of total population | B03002_012 / _001 |
| ice_race_income | index (-1 to 1) | Index of Concentration at the Extremes, race + income: (non-Hispanic White households with income $100k+ minus Black households with income under $25k) / all households. Higher = more privileged. Context only; not used in classification. | B19001H_014-017, B19001B_002-005, B19001_001 |
| pct_uninsured_f35_74 | % | Women aged 35-74 without health insurance | B27001_045, 048, 051, 054 / _043, 046, 049, 052 |
| pct_poverty_f35_74 | % | Women aged 35-74 below the poverty level. Context only (used as a validation adjuster). | B17001_026-029 / (_026-029 + _055-058) |
| pct_no_hs_f25 | % | Women 25+ without a high school diploma | B15002_020-027 / _019 |
| pct_lep_hh | % | Limited English-speaking households | C16002_004, 007, 010, 013 / _001 |
| pct_no_vehicle_hh | % | Households without a vehicle | B25044_003 + _010 / _001 |

**Barrier indicators used in classification:** pct_uninsured_f35_74, pct_no_vehicle_hh, pct_no_hs_f25, pct_lep_hh.

## county_burden.csv: county burden (one row per county)

Source: State Cancer Profiles, female breast cancer, late stage, 2019-2023.

| Variable | Type | Description |
|---|---|---|
| FIPS | text (5) | County FIPS code as used in the cancer data (Connecticut: pre-2022 counties). |
| rurality | text | Urban or Rural; NA if not available. |
| aair | rate | Age-adjusted annual incidence rate per 100,000 women. Relative burden. |
| cases | count | Average annual number of cases. Absolute burden. |
| late_stage | % | Percent of cases diagnosed at a late stage. |
| pctl_aair, pctl_cases, pctl_late_stage | 0-100 | Percentile across counties. |

NA in a burden measure means the value was suppressed in State Cancer Profiles (small counts).

## df_percentiles.csv: percentiles (one row per tract)

Identifier columns, plus:

| Variable | Type | Description |
|---|---|---|
| pctl_aair, pctl_cases, pctl_late_stage | 0-100 | The tract's county burden percentiles (ranked across counties). |
| pctl_nat_&lt;barrier&gt; | 0-100 | Each barrier indicator's percentile, ranked across all tracts (4 columns, e.g. pctl_nat_pct_lep_hh). |
| pctl_ur_&lt;barrier&gt; | 0-100 | Each barrier indicator's percentile, ranked within urban or rural tracts. NA when rurality is NA. |
| barrier_index_nat | 0-100 | Mean of the four pctl_nat_ barrier percentiles. NA if any barrier is missing. |
| barrier_index_ur | 0-100 | Mean of the four pctl_ur_ barrier percentiles. NA when rurality is NA. |
| pctl_barriers_nat | 0-100 | barrier_index_nat ranked across all tracts. |
| pctl_barriers_ur | 0-100 | barrier_index_ur ranked within urban or rural tracts; national value when rurality is NA. |
| pctl_access_nat | 0-100 | Distance to the nearest mammography facility, ranked across all tracts. Higher = farther away. |
| pctl_access_ur | 0-100 | Distance ranked within urban or rural tracts; national value when rurality is NA. |

## df_classification.csv: main app file (one row per tract)

Identifier columns, plus:

| Variable | Type | Description |
|---|---|---|
| n_women_40_74, n_women_40_49, n_women_50_74 | count | Screening-eligible women (see df_w_calcs.csv). |
| aair, cases, late_stage | as above | The tract's county burden values. |
| pctl_aair, pctl_cases, pctl_late_stage | 0-100 | County burden percentiles. |
| NEAR_DIST | meters | Distance to the nearest FDA-certified mammography facility. Use for display alongside the percentile. |
| pctl_access_nat, pctl_access_ur | 0-100 | Access percentiles (see df_percentiles.csv). |
| pctl_barriers_nat, pctl_barriers_ur | 0-100 | Barrier percentiles (see df_percentiles.csv). |
| lvl_aair, lvl_cases, lvl_late_stage | 0/67/80/90 | Burden level: highest cutoff the county burden percentile clears. NA = suppressed. |
| lvl_access_nat, lvl_access_ur | 0/67/80/90 | Access level for each ranking. |
| lvl_barriers_nat, lvl_barriers_ur | 0/67/80/90 | Barrier level for each ranking. |
| class_aair, class_cases, class_late_stage | text | Priority class for each burden measure at the **default** cutoffs and ranking. NA = no data. |

For other cutoffs, the app classifies from the lvl_* columns with classify_at().

## df_classification_long.csv: default classes, long (one row per tract per burden measure)

Same columns as df_classification.csv, with the three class_* columns replaced by:

| Variable | Description |
|---|---|
| burden_measure | aair, cases, or late_stage |
| class | Priority class for that burden measure at the default cutoffs |

## df_classification_all_cutoffs.csv: precomputed classes (one row per tract)

For apps that read classes directly instead of computing them. Uses the default ranking (pctl_scale, urban/rural).

| Variable | Type | Description |
|---|---|---|
| GEOID20 | text (11) | 2020 census tract GEOID. |
| cls_&lt;measure&gt;_b&lt;burden&gt;_a&lt;access&gt;_r&lt;barriers&gt; | integer 1-7 | Class code for one burden measure and cutoff combination. 81 columns (3 measures x 27 combinations). Example: cls_aair_b90_a67_r67 = AAIR, burden 90th, access 67th, barriers 67th. NA = no data. |

## class_codes.csv: class code lookup

| class_code | class |
|---|---|
| 1 | High priority |
| 2 | Access gap |
| 3 | Barrier gap |
| 4 | Monitor |
| 5 | Lower priority |
| 6 | Burden suppressed: gaps present |
| 7 | Burden suppressed: no gaps |

## cutoff_grid.csv: summary for every combination (162 rows)

One row per ranking (2) x burden measure (3) x burden cutoff (3) x access cutoff (3) x barrier cutoff (3).

| Variable | Type | Description |
|---|---|---|
| scale | text | nat (national) or ur (urban/rural) ranking for access and barriers. |
| burden_measure | text | aair, cases, or late_stage. |
| burden_cut, access_cut, barrier_cut | 67/80/90 | Percentile cutoffs. |
| hp_tracts | count | High-priority tracts. |
| hp_urban, hp_rural | count | High-priority tracts in urban and rural counties (tracts with unknown rurality are excluded). |
| hp_women_40_74 | count | Women 40-74 in high-priority tracts. |
| hp_counties | count | Counties (burden_FIPS) with at least one high-priority tract. |
| hp_states | count | States with at least one high-priority tract. |
| n_high_priority, n_access_gap, n_barrier_gap, n_monitor, n_lower_priority, n_burden_suppressed_gaps_present, n_burden_suppressed_no_gaps | count | Tracts in each class. |
| n_no_data | count | Tracts with no class (missing access or barrier data). |
| pct_of_tracts | % | High-priority tracts as a percent of all tracts. |
| current | logical | TRUE for the default cutoffs and ranking. |

## sensitivity_grid.csv: equal access and barrier cutoffs (54 rows)

The rows of cutoff_grid.csv where access_cut == barrier_cut, used by mvp_visualizations.R. Same columns, except access_cut is renamed tract_cut and barrier_cut is dropped.

## Priority classes

Rules are applied in order, and the first rule that matches sets the class. At the selected cutoffs:

- **Elevated burden:** county burden level >= burden cutoff.
- **Access gap:** access level >= access cutoff.
- **High barriers:** barrier level >= barrier cutoff.

| Class | Rule | Interpretation |
|---|---|---|
| NA (No data) | Access or barrier data missing | Tract data unavailable (for example, a zero-population or water tract). |
| Burden suppressed: gaps present | County burden suppressed; access gap or high barriers | Burden unknown, but the tract has screening gaps. |
| Burden suppressed: no gaps | County burden suppressed; no access gap and low barriers | Burden unknown; no tract-level gaps. |
| Lower priority | Burden below cutoff | Not in the highest-burden counties at this cutoff. Does not mean no need. |
| High priority | Elevated burden; access gap; high barriers | Elevated burden with both access and barrier gaps. |
| Access gap | Elevated burden; access gap; low barriers | Elevated burden, mainly an access gap. |
| Barrier gap | Elevated burden; no access gap; high barriers | Elevated burden, mainly barriers. |
| Monitor | Elevated burden; no access gap; low barriers | Elevated burden without tract-level gaps. |

**Interpretation:** burden is county-level context and is the same for every tract in a county. Classes describe where tract-level screening gaps fall within high-burden counties. They are not tract-level cancer risk estimates. With urban/rural ranking, access and barrier percentiles compare a tract with similar tracts, not with the whole country.

---

# Validation outputs (mvp_validation.R)

Written to `data_dir/validation`. These files compare the priority measures and classes with CDC PLACES tract estimates of mammography use. PLACES is used for validation only and is not an input to the classification.

## Validation conventions

- **PLACES screening** = MAMMOUSE_CrudePrev: % of women 50-74 with a mammogram in the past 2 years (model-based estimate).
- **Low screening** = tract in the bottom X% of PLACES screening nationally. Primary threshold: 20%; sensitivity thresholds: 10% and 33%.
- **Residual PLACES** = PLACES screening minus what its own model inputs predict (race/ethnicity, education, share of women 65-74) plus county effects. "Adjusted" results use this.
- **Sign:** all priority measures are coded so higher = more need. **Aligned** with PLACES means rho below 0 and AUC above 0.5.
- **Analysis sample:** tracts matched to PLACES with n_women_50_74 > 0 and non-missing PLACES screening.
- Results are effect sizes; p-values are not reported because nearly every result is significant with ~80,000 tracts.

### Measure labels

Used in the `measure` column of val_tract_measures.csv and val_sensitivity.csv.

| measure | Source column (urban/rural; national in the national_rank analysis) | Shared with PLACES? |
|---|---|---|
| distance | pctl_access_ur | No |
| uninsured | pctl_ur_pct_uninsured_f35_74 | No |
| no_vehicle | pctl_ur_pct_no_vehicle_hh | No |
| limited_english | pctl_ur_pct_lep_hh | No |
| no_hs_diploma | pctl_ur_pct_no_hs_f25 | Yes (shared input) |
| barrier_4 | pctl_barriers_ur | Partly (includes education) |
| barrier_3 | 3-item barrier index without education (uninsured, no vehicle, limited English); built in the validation script the same way as the 4-item index; not saved to a file | No |

## val_tract_measures.csv: tract measures vs PLACES (one row per measure x stratum)

| Variable | Type | Description |
|---|---|---|
| measure | text | Tract measure (see measure labels). |
| stratum | text | overall, urban, or rural. |
| n | count | Tracts with a non-missing value. |
| rho_raw | -1 to 1 | Spearman correlation with PLACES screening. |
| auc_raw | 0-1 | Probability that a low-screening tract (bottom 20%) has a higher value than another tract. 0.5 = chance. |
| rho_adjusted | -1 to 1 | Spearman correlation with residual PLACES. The strongest test. |
| auc_adjusted | 0-1 | AUC for the bottom 20% of residual PLACES. |
| rho_within | -1 to 1 | Spearman correlation after centering the measure and PLACES on their county means. |
| overlap | text | independent, shared input, or partial (education). |
| r2_places_inputs | 0-1 | Share of PLACES variation explained by its model inputs plus county effects (same in every row). |
| rho_raw_lo, rho_raw_hi | -1 to 1 | 95% county-bootstrap confidence interval for rho_raw. Overall stratum only; absent if n_boot = 0. |

## val_county_burden.csv: county burden vs county screening (one row per burden measure)

PLACES is averaged to counties (burden_FIPS), weighted by women 50-74.

| Variable | Type | Description |
|---|---|---|
| burden_measure | text | aair, cases, or late_stage. |
| n_counties | count | Counties with a non-suppressed burden value. |
| rho_raw | -1 to 1 | Spearman correlation between county burden and county screening. |
| rho_partial_pov | -1 to 1 | Same, adjusted for county poverty (tract pct_poverty_f35_74 averaged to counties, weighted by women 40-74). |

## val_by_class.csv: PLACES screening by class (one row per burden measure x class)

Default cutoffs (burden 90th, access 67th, barriers 67th; urban/rural ranking).

| Variable | Type | Description |
|---|---|---|
| burden_measure | text | aair, cases, or late_stage. |
| class | text | Priority class (NA = no data). |
| n_tracts | count | Tracts in the class. |
| mean_mammo, median_mammo | % | Mean and median PLACES screening. |
| mean_resid | percentage points | Mean residual PLACES. Below 0 = lower screening than PLACES' inputs predict. |
| pct_low | % | Percent of tracts in the bottom 20% of screening (20% = chance). |

## val_cutoff_combos.csv: lift for every cutoff combination (324 rows)

One row per burden measure (3) x cutoff combination (27) x outcome (4). Uses the precomputed classes in df_classification_all_cutoffs.csv (urban/rural ranking).

| Variable | Type | Description |
|---|---|---|
| burden_measure | text | aair, cases, or late_stage. |
| cut_burden, cut_access, cut_barriers | 67/80/90 | Percentile cutoffs. |
| outcome | text | Low-screening definition: raw_10, raw_20, raw_33 (bottom 10/20/33% of PLACES) or resid_20 (bottom 20% of residual PLACES). |
| n_high | count | High-priority tracts in the analysis sample. |
| pct_low_hp | 0-1 | Share of high-priority tracts that are low screening (precision). |
| sensitivity | 0-1 | Share of all low-screening tracts that are high priority (reach). |
| base_rate | 0-1 | Share of all tracts that are low screening by definition (0.10, 0.20, or 0.33). |
| lift | ratio | pct_low_hp / base_rate. 1 = chance; above 1 = high-priority tracts are concentrated in low-screening areas. |
| reportable | logical | TRUE when n_high >= 30. Combinations with fewer tracts are not plotted. |

## val_sensitivity.csv: robustness checks (one row per analysis x measure)

Same statistics as val_tract_measures.csv (n, rho_raw, auc_raw, rho_adjusted, auc_adjusted, rho_within), for all tracts, plus:

| Variable | Type | Description |
|---|---|---|
| analysis | text | national_rank: measures ranked nationally instead of within urban/rural. min_women: tracts with at least 100 women 50-74. drop_wide_ci: drops the 10% of tracts with the widest PLACES confidence intervals (only if MAMMOUSE_Crude95CI is present). |
| measure | text | See measure labels. |

## val_headline_numbers.csv: key results for the report-out

Stacked summary; columns that don't apply to a row are NA. Values are rounded to 2 decimals.

| Variable | Description |
|---|---|
| item | "Tract: &lt;measure&gt;" (overall tract results) or "Default classes: &lt;burden measure&gt;" (default-cutoff lift). |
| overlap, rho_raw, rho_adjusted, rho_within, auc_raw | Tract rows; see val_tract_measures.csv. |
| n_high, lift, sensitivity | Class rows, bottom 20% of raw PLACES; see val_cutoff_combos.csv. |

## val_unmatched_tracts_by_state.csv: tracts with no PLACES match

| Variable | Type | Description |
|---|---|---|
| state | text | State name. |
| n | count | Tracts in df_classification.csv with no matching PLACES tract. |

## Validation figures (data_dir/validation/figures)

PNG, 13.33 x 7.5 in (16:9 slide), 300 dpi.

| File | Shows | Source table |
|---|---|---|
| fig1_screening_by_class.png | PLACES screening by class at the default cutoffs, by burden measure | val_by_class.csv |
| fig2_measure_alignment.png | -rho for each tract measure: raw (with bootstrap CI), adjusted, within county | val_tract_measures.csv |
| fig3_urban_rural.png | -rho in urban vs rural tracts | val_tract_measures.csv |
| fig4_county_burden.png | County burden percentile vs county screening | val_county_burden.csv |
| fig5_lift_heatmap.png | Lift (bottom 20%) for all 81 combinations; app default outlined | val_cutoff_combos.csv |
| fig6_precision_vs_reach.png | Lift vs number of high-priority tracts | val_cutoff_combos.csv |
