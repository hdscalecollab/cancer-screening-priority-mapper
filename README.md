# Cancer Screening Priority Mapper

A flexible, multi-scale geospatial decision-support tool for identifying high-priority areas for breast cancer screening in the United States. The tool combines **county-level late-stage breast cancer burden** with **tract-level screening barriers and access to mammography**. Users choose the burden measure and the percentile cutoff for each domain.

Developed for the 2026 NCI Data Jamboree (https://events.cancer.gov/nci/datajamboree).

## MVP scope

| Component | Geography | Source |
|---|---|---|
| Late-stage breast cancer burden (AAIR, average annual cases, % late stage), 2019-2023 | County | State Cancer Profiles (NCI/CDC) |
| Urban/rural designation | County | State Cancer Profiles county file (2023 Rural-Urban Continuum Codes, labeled Urban/Rural) |
| Screening-eligible women, race/ethnicity, and barrier indicators | Census tract (2020) | ACS 5-year, 2019-2023 |
| Distance to the nearest mammography facility | Census tract (2020) | FDA MQSA facility list (geocoded) |

## Repository structure

```text
README.md
docs/
  data_dictionary.md          Variables and classes for every output file
scripts/
  processing/
    mvp_classification.R      Builds indicators, ranks, and classifies tracts
    mvp_visualizations.R      Figures for class distributions and cutoff comparisons
```

Data files are **not** stored in this repository. Inputs and outputs live in the team's shared `MVP_data` folder in the NCI Data Jamboree Teams site.

## Running the scripts

### Requirements

- R 4.2 or later
- Packages: `tidyverse`, `sf`, `tigris`, `patchwork`, `scales`
- An internet connection on the first run (`tigris` downloads Connecticut tract and county boundaries and caches them)

### Inputs

Put these files in a local data folder and set `data_dir` in the classification script's settings block.

| File | Contents |
|---|---|
| `acs5_2019_2023_tract_MVP v2.csv` | ACS 2019-2023 tract estimates (see `codebook_acs5_2019_2023_MVP.csv`) |
| `US_Counties_Late_Stage_Breast_Cancer_2019-2023(in).csv` | County late-stage breast cancer measures and Urban/Rural label from State Cancer Profiles |
| `mammography_distance.csv` | Tract distance to the nearest mammography facility (`NEAR_DIST`, meters) |

### Settings

Key settings at the top of `mvp_classification.R`:

| Setting | Default | Purpose |
|---|---|---|
| `cut_options` | `c(67, 80, 90)` | Percentile cutoffs users can choose for each domain in the app |
| `cutoffs` | `burden = 90, access = 67, barriers = 67` | Cutoffs shown when the app opens (must be in `cut_options`) |
| `pctl_scale` | `"ur"` | Ranking for access and barriers: `"ur"` = within urban and rural tracts, `"nat"` = national |

### Run

```r
source("scripts/processing/mvp_classification.R")
source("scripts/processing/mvp_visualizations.R")   # optional figures
```

The console prints checks for:

- the number of counties and urban/rural counts
- suppressed counties for each burden measure
- unmatched counties, and tracts with no urban/rural label
- missing values
- class counts at the default cutoffs
- high-priority tracts for every cutoff combination

The script stops with an error if a county appears twice, no rural tracts are found, or a default cutoff is not one of `cut_options`.

### Outputs

All outputs are written to `data_dir`. See [`docs/data_dictionary.md`](docs/data_dictionary.md) for every variable.

| File | One row per | Description |
|---|---|---|
| `df_w_calcs.csv` | Tract | Derived ACS indicators |
| `county_burden.csv` | County | Burden measures, urban/rural label, and burden percentiles |
| `df_percentiles.csv` | Tract | National and urban/rural percentiles (0-100) for barriers, access, and county burden |
| `df_classification.csv` | Tract | **Main app file.** Values, percentiles, cutoff levels (`lvl_*`), and classes at the default cutoffs |
| `df_classification_long.csv` | Tract x burden measure | Default classes in long format, for the app's burden-measure selector |
| `df_classification_all_cutoffs.csv` | Tract | Precomputed integer class codes for all 81 combinations (3 burden measures x 27 cutoff combinations) |
| `class_codes.csv` | Class | Lookup from integer class code to class label |
| `cutoff_grid.csv` | Ranking x burden measure x cutoff combination | Tract and women counts for every class at every combination (162 rows) |
| `sensitivity_grid.csv` | Subset of `cutoff_grid.csv` | Combinations where the access and barrier cutoffs are equal (used by the figures script) |

`mvp_visualizations.R` saves 8 figures (PNG, 300 dpi):

| Figure | Shows |
|---|---|
| `fig_classes_overall` | Class distribution at the default cutoffs, by burden measure and urban/rural |
| `fig_class_agreement` | How often the three burden measures give a tract the same class |
| `fig_class_mix_cutoffs` | Class distribution at all 27 cutoff combinations |
| `fig_heat_tracts`, `fig_heat_women` | High-priority tracts and women 40-74 at every cutoff combination |
| `fig_sens_tracts`, `fig_sens_women` | National vs urban/rural ranking |
| `fig_sens_rural_share` | Urban/rural mix of high-priority tracts |

## Method summary

1. **Barrier index (tract level).** Four barrier indicators (uninsured women 35-74, households without a vehicle, women 25+ without a high school diploma, limited English-speaking households) are converted to percentiles. Their mean is re-ranked across tracts to give a barrier percentile.
2. **Access (tract level).** Distance to the nearest mammography facility is converted to a percentile (higher = farther).
3. **Urban/rural ranking.** Access and barrier percentiles are ranked within urban tracts and within rural tracts separately, using the county's Urban/Rural label, so distance is compared with similar places. National percentiles are also kept. Tracts in a county with no label use national percentiles.
4. **Burden (county level).** Each burden measure is ranked across counties, not tracts, so that counties with many tracts don't shift the cutoffs. Tracts take on their county's percentile. Burden is always ranked nationally.
5. **Separate burden views.** Tracts are classified once for each burden measure, giving three views of burden: relative (AAIR), absolute (cases), and late-stage share. They are not combined into a single score.
6. **User-selected cutoffs.** A domain is "high" when its percentile is at or above the selected cutoff (67th, 80th, or 90th), set independently for burden, access, and barriers. Each tract stores a level per domain (`lvl_*` = 0, 67, 80, or 90, the highest cutoff cleared), so the app can classify any combination without recomputing percentiles.
7. **Classification.** See the class definitions in the data dictionary. Counties with suppressed burden values get their own classes instead of being dropped.

### Using the cutoffs in the app

In Shiny, load `df_classification.csv` and the `classify()` and `classify_at()` functions from the classification script, then classify on each user change:

```r
classify_at(df, burden = "aair", cut = c(burden = 90, access = 80, barriers = 67), scale = "ur")
```

Apps that don't run R can read the precomputed column instead, for example `cls_aair_b90_a80_r67` in `df_classification_all_cutoffs.csv`, and map codes with `class_codes.csv`. `cutoff_grid.csv` gives the matching summary counts (tracts, women 40-74, counties, states) for a summary panel.

### Geography notes

- **Connecticut:** ACS 2019-2023 uses Connecticut's 9 planning regions, but the cancer data uses the 8 pre-2022 counties. Each Connecticut tract is assigned to the pre-2022 county containing it (or the nearest one) through `burden_FIPS`.
- **Alaska:** the Chugach (02063) and Copper River (02066) census areas are mapped to the former Valdez-Cordova census area (02261) to match the cancer data.
- `FIPS` is the tract's current county code, used for display. `burden_FIPS` is the county used for the burden and urban/rural join. The two differ only for these tracts.

### Limitations

- County burden is regional context. It is not a tract-level estimate of cancer burden.
- Suppressed county values are not estimated or reconstructed.
- Urban/rural is assigned at the county level, so a rural tract in a metro county is treated as urban.
- With urban/rural ranking, a percentile compares a tract with similar tracts, not the whole country. A rural tract at a moderate percentile can still be farther from a facility than any urban tract; the raw distance (`NEAR_DIST`) is included for display.
- ACS estimates have margins of error, which tend to be large for small tracts. Margins of error are not carried into the classification.

## Contributors

See the project action plan for roles. Contributions are recorded using CRediT roles.

- Original classification code: Alexis Carey
- Revisions: Calvin Tribby

## Contributing

1. Create a branch from `main` (for example, `fix/<short-description>`).
2. Commit your changes with a clear message.
3. Open a pull request and request a review from the original author of the code you changed.

Do not commit data files, credentials, or personal file paths.
