# Deployment checklist: shinyapps.io

Work through this list top to bottom for every deploy. Steps marked *(first deploy)* only need to be done once.

## 1. Data

- [ ] New MVP outputs are in the input folder (`df_classification.csv`, `df_w_calcs.csv`, `df_percentiles.csv`, `county_burden.csv`, plus the optional QA files).
- [ ] Rebuild the app data:
  ```bash
  python prep_mvp_data.py --input-dir "path/to/MVP_data" --strict
  ```
- [ ] The last line of `data/app/prep_qa.txt` reads **"QA summary: all checks passed"**, and `data/app/qa_issues.csv` doesn't exist.
- [ ] In `prep_qa.txt`, the tract count and default class counts match the R pipeline output.
- [ ] *(first deploy, or when boundaries change)* `prep_geodata.py` and `prep_state_lines.py` have been run, and `data/geo/` contains:
  - `counties.geojson`, `states.json`, `tract_names.json`, `state_lines.geojson`
  - `state_lines.geojson` comes from `prep_state_lines.py`, which must be run after `prep_geodata.py` (`states.json` doesn't contain borders)
  - `tracts/` and `neighbors/` with one file per state
- [ ] *(optional)* Run `prep_mammography_fda.py` to refresh the facilities. FDA updates its list weekly. Review `data/mammography_geocode_qa.csv` for ZIP-centroid placements.

## 2. Local test

- [ ] Start the app: `shiny run app.py`. Then confirm:
  - [ ] **Home** loads, and its numbers look right at the default cutoffs.
  - [ ] **Priority & Access, county view:** the map draws with state borders, and clicking a county updates all four boxes and the Need profile.
  - [ ] Changing each cutoff bar updates the map, boxes, Need profile and summary line, and **Reset to defaults** restores them.
  - [ ] **Tract view:** switching state zooms to it, clicking a tract shows the class explanation in box 1, and **✕ Clear** and **Esc** both clear the selection.
  - [ ] **Barriers:** the variable dropdown and the scatterplot X/Y work, and the selected region shows as a red diamond.
  - [ ] The mammography checkbox toggles the facility layer.
  - [ ] **Data & Methods** loads.
  - [ ] The browser console (F12) shows no `[map]` errors.
  - [ ] There's no **⚠ Data QA** badge in the navigation bar.
- [ ] Check a narrow window (about 800 px wide): the panel, map, boxes and chart should stack vertically.

## 3. Bundle

- [ ] Pin exact package versions in `requirements.txt`, using the versions you tested with:
  ```bash
  pip freeze | grep -iE "^(shiny|plotly|pandas|numpy)=="
  ```
- [ ] The bundle needs these files: `app.py`, `classify.py`, `pages/`, `requirements.txt`, `data/app/` and `data/geo/`.
- [ ] Check the bundle size. The limit on the Free and Starter plans is 1 GB.
  ```bash
  du -sh data/app data/geo
  ```
- [ ] No raw MVP files, prep scripts or `.venv/` will be uploaded (the `-x` excludes in step 4 cover these).

## 4. Deploy

- [ ] *(first deploy)* Install the tool and link your account. Get the token from shinyapps.io → avatar → **Tokens** → **Show**.
  ```bash
  pip install rsconnect-python
  rsconnect add --account <ACCOUNT> --name <NAME> --token <TOKEN> --secret <SECRET>
  ```
- [ ] Deploy from the project folder, using the same Python environment you tested in:
  ```bash
  rsconnect deploy shiny . --name <NAME> --title cancer-screening-priority-mapper \
      -x "prep_*.py" -x "requirements-prep.txt" -x "data/raw_mvp/**" -x "data/MVP_data/**" \
      -x "data/mammography_geocode_qa.csv" -x ".venv/**" -x "__pycache__/**"
  ```
- [ ] Redeploys use the same command. `rsconnect` replaces the existing app.

## 5. After deploying

- [ ] Open `https://<ACCOUNT>.shinyapps.io/cancer-screening-priority-mapper/` and repeat the main checks from step 2 in a fresh browser window.
- [ ] Check the **Logs** on the app's shinyapps.io page for startup errors.
- [ ] *(first deploy)* Set the instance size under **Settings → General → Instance Size**. The default is 1 GB. Increase it if switching to tract view crashes the app or the logs show memory errors.
- [ ] Note the deploy date, data release and package versions, for example in the release notes or a commit message.

## Rollback

shinyapps.io keeps no old versions of the app. To roll back, check out the previous app code and `data/app/` from version control, then redeploy with the step 4 command.

## Notes

- **Retirement:** Posit is retiring shinyapps.io in 2027 and recommends Posit Connect Cloud for new apps. For internal hosting, the in-house Docker Shiny server is an option, but it would need a Python image.
- **Idle disconnects:** sessions disconnect after 15 minutes without interaction. Users just reload the page.
- **Basemap:** the CARTO basemap loads in each user's browser. If a user's network blocks it, they'll see the polygons without streets underneath.
