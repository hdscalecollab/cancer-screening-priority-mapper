# Cancer Screening Priority Mapper (Shiny for Python)

An interactive county and census-tract map of the MVP breast cancer screening-priority classification. It combines county cancer burden with tract-level distance to mammography and social barriers to show where screening outreach may matter most. Users can change the classification cutoffs and compare any place with its neighbors, county, state and the US.

- **Definitions and methods:** `docs/data_dictionary.md`
- **Deploying:** follow [`DEPLOY_CHECKLIST.md`](DEPLOY_CHECKLIST.md)

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-prep.txt

python prep_geodata.py                                   # once
python prep_state_lines.py                               # once, after prep_geodata.py
python prep_mammography_fda.py                           # optional
python prep_mvp_data.py --input-dir "path/to/MVP_data" --strict
shiny run --reload app.py                                # http://127.0.0.1:8000
```

See [Build and run locally](#build-and-run-locally) for details on each step.

## App behavior

### Pages

| Page | Left panel | Right side |
|---|---|---|
| **Home** | — | Landing page: purpose, the three dimensions, class legend and headline numbers at the default cutoffs (`pages/landing.py`) |
| **Priority & Access** | Map level, state, **Priority / Access variable**, mammography layer, **cutoff options** | 4 info boxes · map · **Need profile** chart over the lower-left of the map |
| **Barriers** | Map level, state, **all other variables** (barriers, county burden, population, context), mammography layer, **scatterplot X / Y** | 4 info boxes · map · **scatterplot** over the lower-left of the map |
| **Data & Methods** | — | Sources and links (`pages/methods.py`) |

- **Navigation:** the top bar switches pages. **Explore the map** on Home opens Priority & Access.
- **Shared state:** the two map pages share one map, one set of info boxes and one control panel, and the panel's sections switch with the page. The selected polygon, the zoom and the cutoff settings all carry over when you switch between Priority & Access and Barriers.
- **Data period:** the navigation bar shows the data periods (ACS 2019–2023 · cancer burden 2019–2023).

### Variable dropdowns

- **Grouping:** variables are grouped (Priority, Access, Barriers, County burden, Population, Context). Each map page only lists its own groups.
- **Short description:** a one-line description of the selected variable appears under the dropdown.
- **Map level:** the choices change with the map level. County view uses county versions of tract variables (median tract, or sums for counts). If the selected variable doesn't exist at the new level, the page falls back to its default.

| Page | Tract view | County view |
|---|---|---|
| Priority & Access | Priority class, distance to facility (miles), access percentile | % of tracts High priority, median-tract distance and access percentile |
| Barriers | Barrier percentile and index (urban/rural and national), each barrier's % and percentiles, county burden, women 40–74, demographics, ICE, urban/rural | Same, as median tract or county value |

### Map

- **Colors:**
  - priority class: categorical legend in the class colors
  - percentiles: fixed 0–100 scale, so maps are comparable
  - ICE index: diverging scale centered on 0
  - counts: blue scale
- **Legend:** bottom-right. For priority variables, its subtitle shows the active burden measure, cutoffs and ranking (for example `Incidence · B90 A67 R67 · Urban/rural`).
- **Hover:** shows the place name and the formatted value.
- **Selecting:** click a county or tract to select it. A dark blue outline marks it, and the boxes and overlay chart update.
- **Clearing:** press **✕ Clear** in the *Selected* box or press **Esc**. Switching map level or state also clears the selection.
- **Tract view:** pick a state from the dropdown and the map zooms to fit it in the area not covered by the panel and boxes. Tracts are shown one state at a time to keep the map fast.
- **Map view:** changing the variable or cutoffs keeps your pan and zoom.
- **State borders:** light grey lines drawn above the polygon colors. Shared borders are merged, so inland borders aren't darker than coastlines.
- **Mammography facilities:** an optional layer of FDA-certified facilities. The checkbox only appears when `data/geo/mammography.geojson` exists. Hover shows the name, address and phone, and notes when a location is approximate (ZIP centroid). Clicking a facility doesn't change the selected polygon.
- **Panel:** **«** hides the control panel and **☰ Controls** brings it back. The boxes and overlay chart widen to use the space.

### Info boxes

| Box | County view | Tract view |
|---|---|---|
| 1 · Selected | Selected county | Selected tract |
| 2 · Adjacent | Counties sharing a border or corner, including across state lines | Tracts sharing a border or corner |
| 3 | Counties in the state | Tracts in the county |
| 4 | All US counties | Tracts in the state |

- **Main value:** the selected variable for box 1, and the group summary for boxes 2–4.
- **Numeric variables:** boxes 2–4 show the median and *n*. Each box lists up to 4 related variables, with the selected one in bold.
- **Priority class (tract view):** box 1 explains the class, for example `Burden 98 ≥ 90 ✓ · Access 32 < 67 · Barriers 71 ≥ 67 ✓`, and shows "suppressed" or "no data" when a dimension is missing. Boxes 2–4 show the class mix as % of tracts, with color chips.
- **% of tracts High priority (county view):** boxes 2–4 pool all tracts in the group. They don't take a median of county shares, which would usually be 0.
- **Before a click:** boxes 1–3 prompt you to click a polygon. Box 4 always shows its group.

### Cutoff options (Priority & Access)

The options follow *Conventions → Cutoff options* in the data dictionary.

| Bar | Choices | Default |
|---|---|---|
| Burden measure | Incidence · Cases · % late stage | Incidence |
| Access & barrier ranking | Urban/rural · National | Urban/rural |
| Burden cutoff | 67th · 80th · 90th | 90th |
| Access cutoff | 67th · 80th · 90th | 67th |
| Barrier cutoff | 67th · 80th · 90th | 67th |

- **Instant reclassification:** classes are recomputed in the app from the `lvl_*` columns with `classify.classify_at()`, covering all 162 combinations. The map, boxes, Need profile and county "% of tracts High priority" all update together.
- **Ranking:** the Access & barrier ranking bar also switches which percentiles the access variable and the Need profile use.
- **Summary line:** under the bars, a line shows the national counts of High priority tracts, women 40–74 in those tracts, and counties with at least one.
- **Reset:** **Reset to defaults** restores the defaults above.

### Overlay charts

- **Need profile (Priority & Access):**
  - percentile bars for the selected region vs the three comparison groups, in the box colors
  - rows: distance to facility, barrier index, each barrier, and the three burden measures
  - black `|` marks show the selected access, barrier and burden cutoffs, and ◀ marks the selected burden measure
  - expanded on page load
- **Scatterplot (Barriers):**
  - X vs Y for all US counties or tracts, with an optional linear trend line and *r*
  - the selected region is a red diamond with crosshairs, and a note gives its national standing on each axis
  - the chart keeps its zoom when you click another polygon
- **Legends:** both charts label the clicked polygon "Selected region". Its actual name appears in the box subtitle and in hover text.
- **Minimize:** the **–** / **+** button minimizes or expands each chart. To make a chart start minimized, pass `minimized=True` to `overlay()` in `map_block`.

### Data QA badge

If any check in `prep_mvp_data.py` failed, a red **⚠ Data QA** badge in the navigation bar names the failed checks. Details are in `data/app/prep_qa.txt` and `qa_issues.csv`.

### Small screens

Below about 990 px wide, the panel, map, boxes and chart stack vertically instead of floating over the map.

## Pipeline

```
 MVP R outputs (mvp_classification.R)        Census boundaries (census.gov)        FDA MQSA facility list
   df_classification.csv                        county 500k, tract 500k,             public.zip (weekly)
   df_w_calcs.csv                               state 20m                                   |
   df_percentiles.csv                                   |                                   |
   county_burden.csv                         +----------+-----------+                       |
   [optional QA: df_classification_          |                      |                       |
    all_cutoffs.csv, cutoff_grid.csv,  prep_geodata.py  -->  prep_state_lines.py   prep_mammography_fda.py
    class_codes.csv]                         |      (imports helpers)  |              (Census geocoder +
          |                                  |                      |               ZIP fallback)
   prep_mvp_data.py <-- classify.py          |                      |                       |
          |   (QA: levels, default_class,    |                      |                       |
          |    all_cutoffs, cutoff_grid,     v                      v                       v
          |    barrier_index)            data/geo/                                    data/geo/
          v                                counties.geojson     state_lines.geojson   mammography.geojson
     data/app/                             tracts/<STATEFP>.geojson
       tract_values.csv                    neighbors/county.json, tract_<STATEFP>.json
       county_values.csv                   states.json, tract_names.json
       variables.json   (catalog, cutoffs, need-profile rows, qa_failed)
       prep_qa.txt, qa_issues.csv          |                      |                       |
          |                                |                      |                       |
          +--------------------------------+-----------+----------+-----------------------+
                                                       |
                                                       v
                                   app.py  <-- classify.py (reclassify at any cutoff)
                                     |     <-- pages/landing.py, pages/methods.py
                                     |
          +----------------+---------+-----------+-------------------+
          |                |                     |                   |
        Home       Priority & Access         Barriers         Data & Methods
                   map + 4 boxes +        map + 4 boxes +
                   cutoff bars +          scatterplot X/Y
                   Need profile           overlay
                   overlay
```

| Script | When to run | Writes |
|---|---|---|
| `prep_geodata.py` | Once (or when the boundary vintage changes) | `data/geo/` counties, tracts, neighbors, `states.json`, `tract_names.json` |
| `prep_state_lines.py` | Once, **after** `prep_geodata.py` | `data/geo/state_lines.geojson` |
| `prep_mammography_fda.py` | Optional; rerun to refresh facilities | `data/geo/mammography.geojson` |
| `prep_mvp_data.py` | Every MVP data release | `data/app/` |

Only `app.py`, `classify.py`, `pages/`, `requirements.txt`, `data/app/` and `data/geo/` are deployed. The prep scripts and raw inputs stay local.

## Folder layout

```
cancer-screening-priority-mapper/
├── app.py                        # the app
├── classify.py                   # priority-class rule (shared with prep_mvp_data.py)
├── pages/                        # __init__.py, landing.py, methods.py
├── requirements.txt              # app dependencies (deployed)
├── prep_geodata.py               # once: county/tract boundaries, adjacency, states.json
├── prep_state_lines.py           # once, after prep_geodata.py: state border lines
├── prep_mammography_fda.py       # optional: FDA facility layer
├── prep_mvp_data.py              # each data release: MVP outputs -> data/app/
├── requirements-prep.txt         # prep-only dependencies (not deployed)
├── DEPLOY_CHECKLIST.md
└── data/
    ├── raw_mvp/                  # MVP R outputs (input to prep_mvp_data.py; not deployed)
    ├── app/                      # tract_values.csv, county_values.csv, variables.json, prep_qa.txt
    └── geo/                      # counties.geojson, tracts/, neighbors/, states.json,
                                  # tract_names.json, state_lines.geojson, mammography.geojson
```

## Build and run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-prep.txt

python prep_geodata.py                                     # once (2020-based tract GEOIDs)
python prep_state_lines.py                                 # once, after prep_geodata.py (imports its helpers)
python prep_mammography_fda.py                             # optional; rerun to refresh facilities
python prep_mvp_data.py --input-dir "path/to/MVP_data" --strict
shiny run --reload app.py                                  # http://127.0.0.1:8000
```

- **Restarting:** `--reload` only watches `.py` files. After rebuilding anything in `data/`, stop and restart the app.
- **Basemap:** if your network blocks the CARTO basemap, run with `CSPM_MAP_STYLE=white-bg` (PowerShell: `$env:CSPM_MAP_STYLE="white-bg"`).

### State borders

`prep_state_lines.py` builds `data/geo/state_lines.geojson`. `states.json` only holds state names, abbreviations and map extents, not borders.

1. Keep `prep_state_lines.py` in the project folder next to `prep_geodata.py`. It imports that file's download and write helpers.
2. Run it after `prep_geodata.py`, with the same options:
   ```bash
   python prep_state_lines.py                 # 50 states + DC, 2023 boundaries
   python prep_state_lines.py --include-pr    # also Puerto Rico
   python prep_state_lines.py --year 2022     # other boundary vintage
   ```
   It downloads the Census 1:20M state file (cached in `~/.cache/cspm_census/`), merges shared borders and writes one `MultiLineString`.
3. Check the output:
   ```bash
   python -c "import json; g=json.load(open('data/geo/state_lines.geojson')); print(g['features'][0]['geometry']['type'])"
   ```
   It should print `MultiLineString`.
4. Stop and restart the app.

## Data preparation (`prep_mvp_data.py`)

| Input | Used for |
|---|---|
| `df_classification.csv` | Main file: identifiers, rurality, counts, burden, `NEAR_DIST`, access/barrier percentiles, `lvl_*`, default `class_*` |
| `df_w_calcs.csv` | Raw barrier %, demographics, ICE |
| `df_percentiles.csv` | Per-barrier percentiles (`pctl_nat_*`, `pctl_ur_*`), `barrier_index_*` |
| `county_burden.csv` | County burden and rurality for the county map (via `burden_FIPS`) |
| `df_classification_all_cutoffs.csv`, `cutoff_grid.csv`, `class_codes.csv` | *Optional:* QA and class lookup |

- **County table:** burden and rurality come from `county_burden.csv` via `burden_FIPS`. Where a map county's tracts point to more than one burden county (Connecticut planning regions), the one covering the most women 40–74 is used and listed in `prep_qa.txt`. Tract indicators become the median tract, and counts are summed.
- **Outputs:** `tract_values.csv`, `county_values.csv` and `variables.json`. The JSON file holds the variable catalog, cutoff options and defaults, Need-profile rows and any failed QA checks.

### QA checks

| Check | Fails when |
|---|---|
| `levels` | A `lvl_*` value isn't the highest cutoff (0/67/80/90) its percentile clears |
| `default_class` | The rule at the defaults doesn't reproduce `class_aair` / `class_cases` / `class_late_stage` |
| `all_cutoffs` | The rule doesn't reproduce a column of `df_classification_all_cutoffs.csv` |
| `cutoff_grid` | Class counts don't match a row of `cutoff_grid.csv` |
| `barrier_index` | `barrier_index_*` isn't the mean of its four barrier percentiles |

- **Results:** written to `data/app/prep_qa.txt`.
- **Failing tracts:** listed in `data/app/qa_issues.csv`.
- **`--strict`:** exits with an error if any check fails.
- **Shared rule:** passing `default_class`, `all_cutoffs` and `cutoff_grid` shows the app's reclassification matches the R pipeline, because the app uses the same `classify_at`.

## Customizing

| To change | Edit | Then |
|---|---|---|
| Variables, labels, colors, page assignment, box rows | `catalog()` in `prep_mvp_data.py` (`pages`: `["pa"]`, `["b"]`, or `[]` = scatterplot only) | Rerun `prep_mvp_data.py` |
| Need-profile rows | `profile_rows()` in `prep_mvp_data.py` | Rerun `prep_mvp_data.py` |
| Box colors | `BOX_THEMES` in `app.py` | Restart the app |
| Panel width, chart size | `--panel-w`, `--chart-w`, `--chart-h` in `APP_CSS` | Restart the app |
| Chart starts minimized | `minimized=True` on `overlay()` in `map_block` | Restart the app |
| State border look | `STATE_LINE_COLOR`, `STATE_LINE_OPACITY`, `STATE_LINE_WIDTH` in `app.py` | Restart the app |
| Basemap | `CSPM_MAP_STYLE` environment variable | Restart the app |
