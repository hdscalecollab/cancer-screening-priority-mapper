# Cancer Screening Priority Mapper

A flexible, multi-scale geospatial decision-support tool for identifying high-priority areas for breast cancer screening in the United States. The tool combines **county-level late-stage breast cancer burden** with **tract-level screening barriers and access to mammography**.

Developed for the 2026 NCI Data Jamboree.

## MVP scope

| Component | Geography | Source |
|---|---|---|
| Late-stage breast cancer burden (AAIR, average annual cases, % late stage), 2019-2023 | County | State Cancer Profiles (NCI/CDC) |
| Screening-eligible women, race/ethnicity, and barrier indicators | Census tract (2020) | ACS 5-year, 2019-2023 |
| Distance to the nearest mammography facility | Census tract (2020) | FDA MQSA facility list (geocoded) |

## Repository structure

```text
README.md
docs/
  data_dictionary.md        Variables and classes for every output file
scripts/
  processing/
    mvp_classification.R    Builds indicators, ranks, and classifies tracts
```

Data files are **not** stored in this repository. Inputs and outputs live in the team's shared `MVP_data` folder in the NCI Data Jamboree Teams site.

## Running the classification script

### Requirements

- R 4.2 or later
- Packages: `tidyverse`, `sf`, `tigris`
- An internet connection on the first run (`tigris` downloads Connecticut tract and county boundaries and caches them)

### Inputs

Put these files in a local `MVP_data/` folder, or change `data_dir` in the script's settings block.

| File | Contents |
|---|---|
| `acs5_2019_2023_tract_MVP v2.csv` | ACS 2019-2023 tract estimates (see `codebook_acs5_2019_2023_MVP.csv`) |
| `US_Counties_Late_Stage_Breast_Cancer_2019-2023(in).csv` | County late-stage breast cancer measures from State Cancer Profiles |
| `mammography_distance.csv` | Tract distance to the nearest mammography facility (`NEAR_DIST`, meters) |

### Run

```r
source("scripts/processing/mvp_classification.R")
```

The console prints checks for:

- the number of counties
- suppressed counties for each burden measure
- unmatched counties
- missing values

### Outputs

All outputs are written to `MVP_data/`. See [`docs/data_dictionary.md`](docs/data_dictionary.md) for every variable.

| File | One row per | Description |
|---|---|---|
| `df_w_calcs.csv` | Tract | Derived ACS indicators |
| `county_burden.csv` | County | Burden measures, percentiles, and tertiles |
| `df_percentiles.csv` | Tract | Percentile ranks (0-100) for barriers, access, and county burden |
| `df_rank.csv` | Tract | Low / moderate / high labels |
| `df_classification.csv` | Tract | Priority class for each burden measure (wide format) |
| `df_classification_long.csv` | Tract x burden measure | Priority classes in long format, for the app's burden-measure selector |

## Method summary

1. **Barrier and access tertiles (tract level).** Four barrier indicators and distance to mammography are ranked across all U.S. tracts and split into tertiles (1 = low, 2 = moderate, 3 = high). The barrier score is the sum of the four barrier tertiles (range 4-12).
2. **Burden tertiles (county level).** Each burden measure is ranked across counties, not tracts, so that counties with many tracts don't shift the cutoffs. Tracts take on their county's tertile.
3. **Separate burden views.** Tracts are classified once for each burden measure, giving three views of burden: relative (AAIR), absolute (cases), and late-stage share. They are not combined into a single score.
4. **Classification.** See the class definitions in the data dictionary. Counties with suppressed burden values get their own classes instead of being dropped.

### Geography notes

- **Connecticut:** ACS 2019-2023 uses Connecticut's 9 planning regions, but the cancer data uses the 8 pre-2022 counties. Each Connecticut tract is assigned to the pre-2022 county containing it (or the nearest one) through `burden_FIPS`.
- **Alaska:** the Chugach (02063) and Copper River (02066) census areas are mapped to the former Valdez-Cordova census area (02261) to match the cancer data.
- `FIPS` is the tract's current county code, used for display. `burden_FIPS` is the county used for the burden join. The two differ only for these tracts.

### Limitations

- County burden is regional context. It is not a tract-level estimate of cancer burden.
- Suppressed county values are not estimated or reconstructed.
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
