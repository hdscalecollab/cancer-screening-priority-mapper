# Cancer Screening Priority Mapper (Shiny for Python)

Full-screen county / census-tract map of the MVP screening-priority classification. It has a floating control panel, four comparison boxes, a need-profile chart and a scatterplot.

## Pages

A top navigation bar switches between four pages (`ui.page_navbar` in `app.py`):

| Page | Source | Notes |
|---|---|---|
| Home | `pages/landing.py` | Landing page. Class legend colors come from `variables.json`; the "At a glance" numbers are computed from `tract_values.csv`. |
| Priority & Access | `map_block` in `app.py` | The map, info boxes and charts. |
| Barriers | `map_block` in `app.py` | Currently the same map as Priority & Access. |
| Data & Methods | `pages/methods.py` | Placeholder (source table and documentation links to come). |

`MAP_PAGES` in `app.py` lists the pages that show the map. The map and charts are drawn only while one of them is showing. To give Barriers its own map, put separate map content in each `nav_panel` and remove the shared `map_block` from the page header. `pages/` is part of the app bundle, so the deploy command below needs no change.

## Pipeline

```
MVP R outputs (code_for_mvp_classification_v2.R)          Census boundaries
  df_w_calcs.csv, df_percentiles.csv, df_rank.csv,        (prep_geodata.py)
  df_classification.csv, county_burden.csv                        |
            |                                                     |
      prep_mvp_data.py  ──>  data/app/                      data/geo/
                              tract_values.csv              counties.geojson, tracts/, neighbors/,
                              county_values.csv             states.json, tract_names.json,
                              variables.json                mammography.geojson (prep_mammography_fda.py)
                              prep_qa.txt
                              qa_tertile_issues.csv (only if a check fails)
                                         \                 /
                                               app.py
```

```bash
pip install -r requirements.txt -r requirements-prep.txt
python prep_geodata.py                                    # once (2020 boundaries; matches GEOID20 incl. CT planning regions)
python prep_mammography_fda.py                            # optional facility layer
python prep_mvp_data.py --input-dir "path/to/MVP_data" # add --strict to stop on QA failures
shiny run --reload app.py
```


## Input files

| File | Level | Used for |
|---|---|---|
| `df_w_calcs.csv` | tract | Identifiers, counts, demographics, raw barrier % |
| `df_percentiles.csv` | tract | Barrier, distance and burden percentiles |
| `df_rank.csv` | tract | **The four barrier tertiles** (`<barrier>_t`, low / moderate / high). Its `access` / `burden_*` columns are used only for the cross-file QA check. |
| `df_classification.csv` | tract | Barrier score (4-12), access tertile, burden tertiles, priority classes |
| `county_burden.csv` | county | Burden values, percentiles and tertiles (joined via `burden_FIPS`) |
| `df_classification_long.csv` | tract | Not read. It has the same content as `df_classification.csv` in long format. |

`df_rank.csv` is optional. If it's missing, the script logs a warning, skips the tertile variables and checks, and continues.

## What prep_mvp_data.py does

| Step | Detail |
|---|---|
| Read | `NA` is treated as missing. GEOID20 is padded to 11 digits; FIPS and burden_FIPS to 5. Duplicate IDs are dropped with a warning. |
| Tertiles | The `df_rank` labels are converted to 1 / 2 / 3. The conversion is case-insensitive and also accepts 1-3. |
| Tract table | The four tract files are joined on GEOID20, one-to-one. Class labels are checked against the 7 classes in the data dictionary. |
| County table | **Burden:** taken from `county_burden.csv` via `burden_FIPS`. If a county's tracts span several burden counties (Connecticut planning regions), the one with the most women 40-74 is used, and the county is listed in `prep_qa.txt`. **Tract indicators:** counts are summed, and rates and percentiles are the median tract. **Priority:** the % of classified tracts that are *High priority*. **Barrier tertiles:** the % of tracts in the *High* tertile for each barrier. |
| Catalog | `variables.json` holds each variable's label, group, type, format, colors and description. It also lists which rows the info boxes show (`table`). Variables that are missing or all-NA are skipped. |

## QA checks

Every run writes `data/app/prep_qa.txt`, with row counts, join coverage, class and tertile counts, and burden coverage. It also runs these tertile consistency checks:

| # | Check | Fails when |
|---|---|---|
| 1 | `labels` | A `df_rank` tertile value isn't low / moderate / high. The value is set to NA. |
| 2 | `score_sum` | The four barrier tertiles don't add up to `barriers`. |
| 3 | `score_na` | `barriers` is present while a tertile is missing, or the reverse. |
| 4 | `ordering` | Within a barrier, a tract is in a lower tertile than another tract with a smaller percentile. The log shows each tertile's percentile range. |
| 5 | `cross_file` | `access` / `burden_*` in `df_rank` don't match `df_classification`. |

- **Results:** each check is logged as `[ok]` or `[FAIL]`, followed by a summary line.
- **Failing tracts** are written to `qa_tertile_issues.csv` (`GEOID, check, column, detail`). The file is deleted on a clean run.
- **`--strict`** makes the script exit with an error if any check fails. This is useful in scheduled or CI runs.
- **In the app:** failed checks are saved in `variables.json` (`meta.qa_failed`), and the app shows a red **⚠ Data QA** badge in the title bar.
- **Overlap:** one error can fail more than one check. For example, a bad label becomes NA, which also fails `score_na`.
- **Cut rule:** check 4 doesn't assume a specific rule (33.33 / 66.67, `ntile()`, etc.). It only requires that tertiles never go down as the percentile goes up.

If your tertile columns aren't named `<barrier>_t`, update `BARRIER_T` at the top of the script. Otherwise the checks are skipped, with a note in the log.

## App behavior

- **Variable dropdown:** grouped into Priority, Access, Barriers, County burden, Population and Context. The choices change with the map level.
- **Map colors:** priority classes and tertiles use a categorical legend. Percentile maps are fixed to 0-100, and the ICE index uses a diverging scale.
- **Barrier tertiles:**
  - *Tract view:* four new *Barriers* variables, such as "Households without a vehicle (tertile)".
  - *County view:* "% tracts High tertile" for each barrier.
  - *Info boxes:* when a tertile or the barrier score is selected, every box lists all four tertiles with the barrier score below them, so you can see how the score is built. Comparison boxes show "% High" for each barrier.
- **Boxes:** Box 1 shows the clicked polygon. The other three show the adjacent / county or state / state or US groups, with the median for numeric variables and the class mix for priority classes.
- **Need profile chart:** the percentile indicators (higher = more need) for the selected polygon vs each comparison group, with shaded tertile bands.
- **Scatterplot:** X and Y can be any non-categorical variable. Tertile and barrier-score axes are jittered so overlapping points stay visible.
- **Section navigation:** **▼ Profile & comparison** (on the map) scrolls down to the charts; **▲ Map & distribution** (above the charts) scrolls back to the map. Both scroll smoothly and stop just below the title bar.

To rename variables, change colors, defaults or box rows, edit `tract_catalog()` / `county_catalog()` / `meta["defaults"]` in `prep_mvp_data.py` and rerun it. `app.py` doesn't need changes.

## Deploy to shinyapps.io

```bash
rsconnect deploy shiny . --name <ACCOUNT> --title cancer-screening-priority-mapper \
    -x "prep_*.py" -x "requirements-prep.txt" -x "data/MVP_data/**" -x ".venv/**"
```

Only `data/app/` and `data/geo/` need to be in the bundle. Run `prep_mvp_data.py --strict` before deploying, so data that fails QA isn't published.
