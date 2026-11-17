# Cancer Screening Priority Mapper

A flexible, multi-scale geospatial decision-support tool for identifying high-priority areas for breast cancer screening in the United States. The tool combines **county-level late-stage breast cancer burden** with **tract-level screening barriers and access to mammography**. Users choose the burden measure and the percentile cutoff for each domain.

Developed for the 2026 NCI Data Jamboree ([https://events.cancer.gov/nci/datajamboree](https://events.cancer.gov/nci/datajamboree)).

## MVP scope

| Component | Geography | Source |
|---|---|---|
| Late-stage breast cancer burden (AAIR, average annual cases, % late stage), 2019-2023 | County | State Cancer Profiles (NCI/CDC): [https://statecancerprofiles.cancer.gov/index.html](https://statecancerprofiles.cancer.gov/index.html) |
| Urban/rural designation | County | State Cancer Profiles county file (2023 Rural-Urban Continuum Codes, labeled Urban/Rural): [https://statecancerprofiles.cancer.gov/index.html](https://statecancerprofiles.cancer.gov/index.html) |
| Screening-eligible women, race/ethnicity, and barrier indicators | Census tract (2020) | ACS 5-year, 2019-2023: [https://data.census.gov/table](https://data.census.gov/table) |
| Distance to the nearest mammography facility | Census tract (2020) | FDA MQSA facility list (geocoded): [https://www.fda.gov/radiation-emitting-products/mammography-information-patients/search-certified-mammography-facility](https://www.fda.gov/radiation-emitting-products/mammography-information-patients/search-certified-mammography-facility) |
| Mammography use, women 50-74 (validation only) | Census tract | CDC PLACES (2025), tract data, GIS-friendly (wide) format: [https://www.cdc.gov/places/](https://www.cdc.gov/places/) |

## Repository structure

```text
README.md
docs/
  data_dictionary.md          Variables and classes for every output file
scripts/
  processing/
    mvp_classification.R      Builds indicators, ranks, and classifies tracts
    mvp_visualizations.R      Figures for class distributions and cutoff comparisons
    mvp_validation.R          Compares priority measures and classes with CDC PLACES
webApp/                       Shiny R web application
```

Data files are **not** stored in this repository. Inputs and outputs live in the team's shared `MVP_data` folder in the NCI Data Jamboree Teams site.

## Running the scripts

### Requirements

- R 4.2 or later
- Packages: tidyverse, sf, tigris, patchwork, scales
- An internet connection on the first run (tigris downloads Connecticut tract and county boundaries and caches them)

### Inputs

Put these files in a local data folder and set `data_dir` in each script's settings block.

| File | Contents | Used by |
|---|---|---|
| acs5_2019_2023_tract_MVP v2.csv | ACS 2019-2023 tract estimates (see codebook_acs5_2019_2023_MVP.csv) | classification, validation |
| US_Counties_Late_Stage_Breast_Cancer_2019-2023(in).csv | County late-stage breast cancer measures and Urban/Rural label from State Cancer Profiles | classification |
| mammography_distance.csv | Tract distance to the nearest mammography facility (NEAR_DIST, meters) | classification |
| PLACES tract file (set `places_file`) | One row per tract: TractFIPS, MAMMOUSE_CrudePrev, MAMMOUSE_Crude95CI | validation |

### Settings

Key settings at the top of mvp_classification.R:

| Setting | Default | Purpose |
|---|---|---|
| cut_options | c(67, 80, 90) | Percentile cutoffs users can choose for each domain in the app |
| cutoffs | burden = 90, access = 67, barriers = 67 | Cutoffs shown when the app opens (must be in cut_options) |
| pctl_scale | "ur" | Ranking for access and barriers: "ur" = within urban and rural tracts, "nat" = national |

Key settings at the top of mvp_validation.R:

| Setting | Default | Purpose |
|---|---|---|
| places_file | "PLACES_tract.csv" | File name of the PLACES download |
| places_id, places_prev, places_ci | TractFIPS, MAMMOUSE_CrudePrev, MAMMOUSE_Crude95CI | PLACES column names |
| default_cut | burden = 90, access = 67, barriers = 67 | Must match `cutoffs` in the classification script |
| low_thresholds | c(10, 20, 33) | "Low screening" = bottom X% of tracts on PLACES |
| primary_low | 20 | Primary low-screening threshold (team decision) |
| min_women | 100 | Minimum women 50-74 for the small-tract sensitivity check |
| n_boot | 200 | County-bootstrap repetitions for confidence intervals (set 0 while debugging) |

### Run

```r
source("scripts/processing/mvp_classification.R")
source("scripts/processing/mvp_visualizations.R")   # optional figures
source("scripts/processing/mvp_validation.R")       # needs classification outputs + PLACES
```

The classification script prints checks for:

- the number of counties and urban/rural counts
- suppressed counties for each burden measure
- unmatched counties, and tracts with no urban/rural label
- missing values
- class counts at the default cutoffs
- high-priority tracts for every cutoff combination

It stops with an error if a county appears twice, no rural tracts are found, or a default cutoff is not one of cut_options.

The validation script prints the PLACES tract count and missing values, the tract match rate (a warning below 95% suggests PLACES is on 2010 tracts and needs a crosswalk), the share of PLACES variation explained by its own model inputs, and the headline results. It stops if the PLACES file is missing TractFIPS or MAMMOUSE_CrudePrev, or has duplicate tracts.

### Outputs

All classification outputs are written to `data_dir`. See [docs/data_dictionary.md](docs/data_dictionary.md) for every variable.

| File | One row per | Description |
|---|---|---|
| df_w_calcs.csv | Tract | Derived ACS indicators |
| county_burden.csv | County | Burden measures, urban/rural label, and burden percentiles |
| df_percentiles.csv | Tract | National and urban/rural percentiles (0-100) for barriers, access, and county burden |
| df_classification.csv | Tract | **Main app file.** Values, percentiles, cutoff levels (lvl_*), and classes at the default cutoffs |
| df_classification_long.csv | Tract x burden measure | Default classes in long format, for the app's burden-measure selector |
| df_classification_all_cutoffs.csv | Tract | Precomputed integer class codes for all 81 combinations (3 burden measures x 27 cutoff combinations) |
| class_codes.csv | Class | Lookup from integer class code to class label |
| cutoff_grid.csv | Ranking x burden measure x cutoff combination | Tract and women counts for every class at every combination (162 rows) |
| sensitivity_grid.csv | Subset of cutoff_grid.csv | Combinations where the access and barrier cutoffs are equal (used by the figures script) |

mvp_visualizations.R saves 8 figures (PNG, 300 dpi):

| Figure | Shows |
|---|---|
| fig_classes_overall | Class distribution at the default cutoffs, by burden measure and urban/rural |
| fig_class_agreement | How often the three burden measures give a tract the same class |
| fig_class_mix_cutoffs | Class distribution at all 27 cutoff combinations |
| fig_heat_tracts, fig_heat_women | High-priority tracts and women 40-74 at every cutoff combination |
| fig_sens_tracts, fig_sens_women | National vs urban/rural ranking |
| fig_sens_rural_share | Urban/rural mix of high-priority tracts |

#### Validation outputs

mvp_validation.R writes to `data_dir/validation`:

| File | One row per | Description |
|---|---|---|
| val_headline_numbers.csv | Measure or burden measure | Key results for the report-out slide |
| val_tract_measures.csv | Tract measure x stratum | Correlation and AUC with PLACES: raw, adjusted, and within county; overall, urban, rural |
| val_county_burden.csv | Burden measure | County burden vs county screening, raw and adjusted for poverty |
| val_by_class.csv | Burden measure x class | PLACES screening by class at the default cutoffs |
| val_cutoff_combos.csv | Cutoff combination x outcome | Lift and sensitivity for all 81 combinations |
| val_sensitivity.csv | Analysis x tract measure | Robustness checks |
| val_unmatched_tracts_by_state.csv | State | Tracts with no PLACES match |

It also saves 6 slide-ready figures (16:9, 300 dpi) to `data_dir/validation/figures`. Each figure answers a question in the validation plan overview (PLACES_Validation_Overview.docx):

| Figure | Shows | Validation question |
|---|---|---|
| fig1_screening_by_class | PLACES screening by priority class, for each burden measure | Do high-priority tracts screen less? |
| fig2_measure_alignment | Alignment of each tract measure with low screening: raw, adjusted, within county | Do the independent measures align, including after adjustment and within counties? |
| fig3_urban_rural | Alignment in urban vs rural tracts | Robustness across rankings |
| fig4_county_burden | County burden percentile vs county screening | Is county burden higher where screening is lower? |
| fig5_lift_heatmap | Lift for every cutoff combination; app default outlined | Which cutoffs best find low-screening tracts? |
| fig6_precision_vs_reach | Lift vs number of high-priority tracts | Should validation inform the default cutoffs? |

## Method summary

- **Barrier index (tract level).** Four barrier indicators (uninsured women 35-74, households without a vehicle, women 25+ without a high school diploma, limited English-speaking households) are converted to percentiles. Their mean is re-ranked across tracts to give a barrier percentile.
- **Access (tract level).** Distance to the nearest mammography facility is converted to a percentile (higher = farther).
- **Urban/rural ranking.** Access and barrier percentiles are ranked within urban tracts and within rural tracts separately, using the county's Urban/Rural label, so distance is compared with similar places. National percentiles are also kept.
- **Burden (county level).** Each burden measure is ranked across counties, not tracts, so that counties with many tracts don't shift the cutoffs. Tracts take on their county's percentile. Burden is always ranked nationally.
- **Separate burden views.** Tracts are classified once for each burden measure, giving three views of burden: relative (AAIR), absolute (cases), and late-stage share. They are not combined into a single score.
- **User-selected cutoffs.** A domain is "high" when its percentile is at or above the selected cutoff (67th, 80th, or 90th), set independently for burden, access, and barriers. Each tract stores a level per domain (lvl_* = 0, 67, 80, or 90, the highest cutoff cleared), so the app can classify any combination without recomputing percentiles.
- **Classification.** See the class definitions in the data dictionary. Counties with suppressed burden values get their own classes instead of being dropped.

### Validation against CDC PLACES

PLACES is a check on direction, not a gold standard. CDC models tract screening from survey data using age, sex, race/ethnicity, education, and county poverty, so it can't see local factors like distance to a facility. Education is both a PLACES input and one of our barriers, so a simple correlation would overstate agreement.

- **Low screening** = the bottom 20% of tracts on PLACES mammography use (10% and 33% as sensitivity checks).
- **Alignment.** Spearman correlation and AUC between each priority measure and PLACES. Higher need should go with lower screening (correlation below 0, AUC above 0.5).
- **Adjusted (strongest test).** PLACES is regressed on its own model inputs (race/ethnicity, education, share of women 65-74) plus county effects. Measures are then compared with what is left over.
- **Within county.** Measures and PLACES are centered on their county means, so only differences between neighboring tracts count.
- **County burden.** PLACES is averaged to counties (weighted by women 50-74) and compared with county burden, with and without adjusting for poverty.
- **Classes.** **Lift** = the share of high-priority tracts in the bottom 20% of screening divided by 20%. A lift of 1 is no better than chance; 2 means twice as many as chance. **Sensitivity** = the share of all low-screening tracts that are high priority (reach).
- **Robustness.** A 3-item barrier index without education, national vs urban/rural ranking, dropping the 10% least precise PLACES estimates, dropping tracts with fewer than 100 women 50-74, and county-bootstrap confidence intervals.

Education and the 4-item barrier index are reported separately and are not counted as support, because they share an input with PLACES.

### Using the cutoffs in the app

In Shiny, load df_classification.csv and the classify() and classify_at() functions from the classification script, then classify on each user change:

```r
classify_at(df, burden = "aair", cut = c(burden = 90, access = 80, barriers = 67), scale = "ur")
```

Apps that don't run R can read the precomputed column instead, for example cls_aair_b90_a80_r67 in df_classification_all_cutoffs.csv, and map codes with class_codes.csv. cutoff_grid.csv gives the matching summary counts (tracts, women 40-74, counties, states) for a summary panel.

### Geography notes

- **Connecticut:** ACS 2019-2023 uses Connecticut's 9 planning regions, but the cancer data uses the 8 pre-2022 counties. Each Connecticut tract is assigned to the pre-2022 county containing it (or the nearest one) through burden_FIPS.
- **Alaska:** the Chugach (02063) and Copper River (02066) census areas are mapped to the former Valdez-Cordova census area (02261) to match the cancer data.
- FIPS is the tract's current county code, used for display. burden_FIPS is the county used for the burden and urban/rural join. The two differ only for these tracts.
- **PLACES:** joined on the 2020 tract GEOID. Check the match rate printed by the validation script; unmatched tracts by state are saved to val_unmatched_tracts_by_state.csv.

### Limitations

- County burden is regional context. It is not a tract-level estimate of cancer burden.
- Suppressed county values are not estimated or reconstructed.
- Urban/rural is assigned at the county level, so a rural tract in a metro county is treated as urban.
- With urban/rural ranking, a percentile compares a tract with similar tracts, not the whole country. A rural tract at a moderate percentile can still be farther from a facility than any urban tract; the raw distance (NEAR_DIST) is included for display.
- ACS estimates have margins of error, which tend to be large for small tracts. Margins of error are not carried into the classification.
- PLACES is modeled, not measured, and partly shares our inputs. Adjustment reduces this overlap but doesn't remove it.
- PLACES doesn't reflect local programs such as mobile mammography units, so it can't validate the access measure.
- Data years and age ranges differ across sources (PLACES: women 50-74; uninsured: women 35-74).

## Contributors

See the project action plan for roles. Contributions are recorded using CRediT roles.

- Original classification code: Alexis Carey
- Revisions and validation code: Calvin Tribby
- Documentation: Jenny Ozga, Alexis Carey, Calvin Tribby
- Web application: Jay Yang

## Contributing

- Create a branch from main (for example, fix/short-description).
- Commit your changes with a clear message.
- Open a pull request and request a review from the original author of the code you changed.

Do not commit data files, credentials, or personal file paths.
