"""
prep_mvp_data.py — build the app-ready tables from the MVP classification outputs
(scripts/processing/mvp_classification.R; see data_dictionary.md).

Inputs (default folder: data/raw_mvp/, override with --input-dir)
    df_classification.csv            tract  — main file: burden, access & barrier percentiles,
                                              levels (lvl_*), default classes, NEAR_DIST, rurality
    df_w_calcs.csv                   tract  — raw barrier %, demographics
    df_percentiles.csv               tract  — per-barrier percentiles (_nat / _ur), barrier index
    county_burden.csv                county — burden values / percentiles, rurality (burden FIPS)
    df_classification_all_cutoffs.csv  optional, QA only (81 precomputed class columns)
    class_codes.csv                    optional, class code lookup (built-in default otherwise)
    cutoff_grid.csv                    optional, QA only (class counts for all 162 combinations)
    (df_classification_long.csv and the validation outputs are not needed.)

Outputs (data/app/)
    tract_values.csv      one row per tract  (GEOID = GEOID20); includes the lvl_* columns the
                          app uses to reclassify at any cutoff (classify.classify_at)
    county_values.csv     one row per county (GEOID = current county FIPS, matches the map)
    variables.json        variable catalog + cutoff options/defaults + need-profile rows
    prep_qa.txt           join / coverage / consistency report
    qa_issues.csv         tract rows that failed a consistency check (only if any fail)

Consistency checks (a failed check is listed in prep_qa.txt and shown as a badge in the app)
    1. levels          every lvl_* equals the highest cutoff (0/67/80/90) its percentile clears
    2. default_class   classify_at(default cutoffs, urban/rural) reproduces class_aair/cases/late_stage
    3. all_cutoffs     classify_at reproduces all 81 columns of df_classification_all_cutoffs.csv
    4. cutoff_grid     class counts for all 162 rows of cutoff_grid.csv match
    5. barrier_index   barrier_index_* = mean of its four barrier percentiles (NA if any missing)
    Use --strict to stop with an error if any check fails.

County-level table
    * Burden + rurality come from county_burden.csv via burden_FIPS (Connecticut planning
      regions -> pre-2022 counties; AK 02063/02066 -> 02261). When a map county's tracts
      point to more than one burden_FIPS, the one covering the most women 40-74 is used.
    * Tract indicators: counts are summed; rates, distance and percentiles are the MEDIAN
      tract. Priority (% of tracts High priority) is computed in the app at the selected cutoffs.

Usage
    python prep_mvp_data.py
    python prep_mvp_data.py --input-dir "C:/path/to/MVP_data" --strict
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from classify import CLASS_COLORS, CLASS_LEVELS, CUTOFFS, classify_at, level_of

APP_DIR = Path(__file__).resolve().parent
DEFAULT_IN = APP_DIR / "data" / "raw_mvp"
OUT_DIR = APP_DIR / "data" / "app"
GEO_COUNTIES = APP_DIR / "data" / "geo" / "counties.geojson"

FILES = {
    "cls": "df_classification.csv",
    "calcs": "df_w_calcs.csv",
    "pctl": "df_percentiles.csv",
    "burden": "county_burden.csv",
    "all_cut": "df_classification_all_cutoffs.csv",
    "codes": "class_codes.csv",
    "grid": "cutoff_grid.csv",
}

SCALES = ["ur", "nat"]                               # urban/rural (default) and national
SCALE_LABEL = {"ur": "urban/rural", "nat": "national"}
DEFAULTS = {"measure": "aair", "scale": "ur", "burden": 90, "access": 67, "barriers": 67}

ID_COLS = ["GEOID20", "FIPS", "burden_FIPS", "county", "state", "acs_period", "rurality"]
BURDEN = ["aair", "cases", "late_stage"]
BARRIERS = ["pct_uninsured_f35_74", "pct_no_vehicle_hh", "pct_no_hs_f25", "pct_lep_hh"]
DEMOG = ["pct_nh_white", "pct_nh_black", "pct_nh_aian", "pct_nh_asian", "pct_hispanic",
         "ice_race_income", "pct_poverty_f35_74"]
COUNTS = ["n_women_40_74", "n_women_40_49", "n_women_50_74"]

LVL_COLS = ([f"lvl_{m}" for m in BURDEN] + [f"lvl_access_{s}" for s in SCALES]
            + [f"lvl_barriers_{s}" for s in SCALES])
CLS_COLS = (COUNTS + BURDEN + [f"pctl_{m}" for m in BURDEN] + ["NEAR_DIST"]
            + [f"pctl_access_{s}" for s in SCALES] + [f"pctl_barriers_{s}" for s in SCALES]
            + LVL_COLS + [f"class_{m}" for m in BURDEN])
CALC_COLS = COUNTS + DEMOG + BARRIERS
PCTL_COLS = ([f"pctl_{s}_{b}" for s in SCALES for b in BARRIERS]
             + [f"barrier_index_{s}" for s in SCALES])
BURDEN_FILE_COLS = ["FIPS", "rurality"] + BURDEN + [f"pctl_{m}" for m in BURDEN]
TEXT_COLS = {"GEOID20", "FIPS", "burden_FIPS", "county", "state", "acs_period", "rurality",
             "class_aair", "class_cases", "class_late_stage"}

MEASURE_LABEL = {"aair": "Incidence", "cases": "Cases", "late_stage": "% late stage"}
BURDEN_LABEL = {"aair": "incidence rate", "cases": "cases", "late_stage": "% late stage"}
BURDEN_FMT = {"aair": "rate", "cases": "count1", "late_stage": "pct"}
BARRIER_LABEL = {
    "pct_uninsured_f35_74": "Uninsured women 35-74",
    "pct_no_vehicle_hh": "Households without a vehicle",
    "pct_no_hs_f25": "Women 25+ without HS diploma",
    "pct_lep_hh": "Limited-English households",
}
BARRIER_SHORT = {"pct_uninsured_f35_74": "Uninsured", "pct_no_vehicle_hh": "No vehicle",
                 "pct_no_hs_f25": "No HS diploma", "pct_lep_hh": "Limited English"}
DEMOG_LABEL = {
    "pct_nh_white": "Non-Hispanic White", "pct_nh_black": "Non-Hispanic Black",
    "pct_nh_aian": "Non-Hispanic AIAN", "pct_nh_asian": "Non-Hispanic Asian",
    "pct_hispanic": "Hispanic or Latino", "ice_race_income": "ICE race + income",
    "pct_poverty_f35_74": "Women 35-74 below poverty",
}
COUNT_LABEL = {"n_women_40_74": "Women 40-74", "n_women_40_49": "Women 40-49",
               "n_women_50_74": "Women 50-74"}
RURALITY = {"levels": ["Urban", "Rural"], "colors": ["#6C8EBF", "#9BBF6C"]}

# ---------------------------------------------------------------------------
QA: list[str] = []
FAILED: list[str] = []


def log(msg: str) -> None:
    print(msg)
    QA.append(msg)


def fail(name: str) -> None:
    if name not in FAILED:
        FAILED.append(name)


def read_csv(folder: Path, key: str, want: list[str] | None, id_col: str,
             required: bool = True) -> pd.DataFrame | None:
    path = folder / FILES[key]
    if not path.exists():
        if required:
            raise SystemExit(f"Missing input file: {path}")
        log(f"  [skip] {path.name} not found")
        return None
    df = pd.read_csv(path, dtype=str, keep_default_na=True, na_values=["NA", ""])
    df.columns = df.columns.str.strip()
    if id_col not in df.columns:
        raise SystemExit(f"{path.name}: required id column '{id_col}' not found")
    if want is not None:
        missing = [c for c in want if c not in df.columns]
        if missing:
            log(f"[warn] {path.name}: columns not found and skipped: {missing}")
        keep = [id_col] + [c for c in want if c in df.columns and c != id_col]
        df = df[keep].copy()

    width = 11 if id_col == "GEOID20" else 5
    df[id_col] = df[id_col].str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(width)
    for c in ("FIPS", "burden_FIPS"):
        if c in df.columns and c != id_col:
            df[c] = df[c].str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(5)
    if "rurality" in df.columns:
        df["rurality"] = df["rurality"].str.strip().str.title()

    dups = df[id_col].duplicated()
    if dups.any():
        log(f"[warn] {path.name}: {dups.sum():,} duplicate {id_col} rows dropped (kept first)")
        df = df[~dups]
    num = [c for c in df.columns if c not in TEXT_COLS and c != id_col]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    log(f"  {path.name:<34} {len(df):>7,} rows, {df.shape[1] - 1} columns used")
    return df


def class_lookup(folder: Path) -> tuple[list[str], list[str]]:
    path = folder / FILES["codes"]
    if not path.exists():
        return CLASS_LEVELS, CLASS_COLORS
    t = pd.read_csv(path)
    t = t.sort_values("class_code")
    levels = t["class"].str.strip().tolist()
    if levels != CLASS_LEVELS:
        log(f"[warn] class_codes.csv differs from the built-in class order: {levels}")
    colors = [CLASS_COLORS[CLASS_LEVELS.index(c)] if c in CLASS_LEVELS else "#BBBBBB"
              for c in levels]
    log(f"  class_codes.csv: {len(levels)} classes")
    return levels, colors


# ---------------------------------------------------------------------------
# Tract table
# ---------------------------------------------------------------------------
def build_tracts(folder: Path) -> pd.DataFrame:
    cls = read_csv(folder, "cls", ID_COLS + CLS_COLS, "GEOID20")
    calcs = read_csv(folder, "calcs", CALC_COLS, "GEOID20")
    pctl = read_csv(folder, "pctl", PCTL_COLS + ["rurality"], "GEOID20")

    df = cls
    for name, other in (("df_w_calcs", calcs), ("df_percentiles", pctl)):
        other = other.drop(columns=[c for c in other.columns if c in df.columns and c != "GEOID20"])
        n_no = (~df["GEOID20"].isin(other["GEOID20"])).sum()
        n_extra = (~other["GEOID20"].isin(df["GEOID20"])).sum()
        if n_no or n_extra:
            log(f"[warn] {name}: {n_no:,} tracts in df_classification not found; "
                f"{n_extra:,} extra tracts ignored")
        df = df.merge(other, on="GEOID20", how="left", validate="one_to_one")

    for m in BURDEN:
        c = f"class_{m}"
        if c in df:
            df[c] = df[c].str.strip()
            bad = df[c].notna() & ~df[c].isin(CLASS_LEVELS)
            if bad.any():
                log(f"[warn] {c}: unexpected values set to NA: "
                    f"{df.loc[bad, c].value_counts().head(5).to_dict()}")
                df.loc[bad, c] = pd.NA

    if "FIPS" not in df:
        df["FIPS"] = df["GEOID20"].str[:5]
    if "burden_FIPS" not in df:
        df["burden_FIPS"] = df["FIPS"]
    if "NEAR_DIST" in df:
        df["dist_mi"] = (df["NEAR_DIST"] / 1609.344).round(3)
    return df.rename(columns={"GEOID20": "GEOID"})


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------
def _issues(df, mask, check, column, detail) -> pd.DataFrame:
    return pd.DataFrame({"GEOID": df.loc[mask, "GEOID"].values, "check": check,
                         "column": column, "detail": np.asarray(detail)[np.asarray(mask)]})


def codes_for(df: pd.DataFrame, measure: str, scale: str, b: int, a: int, r: int) -> np.ndarray:
    return classify_at(df[f"lvl_{measure}"], df[f"lvl_access_{scale}"],
                       df[f"lvl_barriers_{scale}"], b, a, r)


def run_checks(df: pd.DataFrame, folder: Path, levels: list[str]) -> list[pd.DataFrame]:
    issues: list[pd.DataFrame] = []
    log("  Consistency checks")

    # 1. levels vs percentiles
    pairs = ([(f"lvl_{m}", f"pctl_{m}") for m in BURDEN]
             + [(f"lvl_access_{s}", f"pctl_access_{s}") for s in SCALES]
             + [(f"lvl_barriers_{s}", f"pctl_barriers_{s}") for s in SCALES])
    bad_any = False
    for lc, pc in pairs:
        if lc not in df or pc not in df:
            log(f"[warn] levels: {lc} or {pc} missing — skipped")
            continue
        expect = level_of(df[pc])
        got = df[lc].to_numpy(float)
        mism = ~((np.isnan(expect) & np.isnan(got)) | (expect == got))
        if mism.any():
            bad_any = True
            log(f"[FAIL] levels: {lc}: {mism.sum():,} tracts don't match {pc}")
            det = [f"{lc} = {g}, {pc} = {p} (expected {e})"
                   for g, p, e in zip(got, df[pc].to_numpy(), expect)]
            issues.append(_issues(df, mism, "levels", lc, det))
    if bad_any:
        fail("levels")
    else:
        log("  [ok] levels: every lvl_* matches the cutoff its percentile clears")

    have_lvls = all(c in df for c in LVL_COLS)
    if not have_lvls:
        log("[warn] lvl_* columns incomplete — class checks skipped")
        return issues
    lab = np.array([None] + levels, dtype=object)

    def labels(codes):
        return lab[np.where(np.isnan(codes), 0, codes).astype(int)]

    # 2. default classes
    d = DEFAULTS
    bad_any = False
    for m in BURDEN:
        c = f"class_{m}"
        if c not in df:
            continue
        got = labels(codes_for(df, m, d["scale"], d["burden"], d["access"], d["barriers"]))
        have = df[c].where(df[c].notna(), None).to_numpy(object)
        mism = np.array([g != h for g, h in zip(got, have)])
        if mism.any():
            bad_any = True
            log(f"[FAIL] default_class: {c}: {mism.sum():,} tracts differ from the rule at "
                f"the default cutoffs")
            issues.append(_issues(df, mism, "default_class", c,
                                  [f"file = {h}, rule = {g}" for g, h in zip(got, have)]))
        else:
            log(f"  [ok] default_class: {c} reproduced for all {len(df):,} tracts")
    if bad_any:
        fail("default_class")

    # 3. all cutoffs (optional file)
    allc = read_csv(folder, "all_cut", None, "GEOID20", required=False)
    if allc is not None:
        a = df[["GEOID"]].merge(allc.rename(columns={"GEOID20": "GEOID"}), on="GEOID", how="left")
        pat = re.compile(r"^cls_(aair|cases|late_stage)_b(\d+)_a(\d+)_r(\d+)$")
        cols = [c for c in a.columns if pat.match(c)]
        n_bad_cols = 0
        for c in cols:
            m, b, ac, r = pat.match(c).groups()
            exp = codes_for(df, m, "ur", int(b), int(ac), int(r))
            got = a[c].to_numpy(float)
            mism = ~((np.isnan(exp) & np.isnan(got)) | (exp == got))
            if mism.any():
                n_bad_cols += 1
                issues.append(_issues(df, mism, "all_cutoffs", c,
                                      [f"file = {g}, rule = {e}" for g, e in zip(got, exp)]))
        if n_bad_cols:
            fail("all_cutoffs")
            log(f"[FAIL] all_cutoffs: {n_bad_cols} of {len(cols)} columns differ from the rule")
        else:
            log(f"  [ok] all_cutoffs: all {len(cols)} precomputed class columns reproduced")

    # 4. cutoff grid (optional file)
    path = folder / FILES["grid"]
    if path.exists():
        g = pd.read_csv(path)
        count_cols = {1: "n_high_priority", 2: "n_access_gap", 3: "n_barrier_gap", 4: "n_monitor",
                      5: "n_lower_priority", 6: "n_burden_suppressed_gaps_present",
                      7: "n_burden_suppressed_no_gaps"}
        bad_rows = []
        for r in g.itertuples(index=False):
            codes = codes_for(df, r.burden_measure, r.scale, int(r.burden_cut),
                              int(r.access_cut), int(r.barrier_cut))
            for k, col in count_cols.items():
                if hasattr(r, col) and int((codes == k).sum()) != int(getattr(r, col)):
                    bad_rows.append(f"{r.scale}/{r.burden_measure}/b{r.burden_cut}"
                                    f"/a{r.access_cut}/r{r.barrier_cut}: {col} file="
                                    f"{getattr(r, col)} rule={int((codes == k).sum())}")
        if bad_rows:
            fail("cutoff_grid")
            log(f"[FAIL] cutoff_grid: {len(bad_rows)} count mismatches, e.g. {bad_rows[0]}")
        else:
            log(f"  [ok] cutoff_grid: class counts match for all {len(g)} combinations")
    else:
        log("  [skip] cutoff_grid.csv not found")

    # 5. barrier index = mean of the four percentiles
    bad_any = False
    for s in SCALES:
        ic = f"barrier_index_{s}"
        parts = [f"pctl_{s}_{b}" for b in BARRIERS]
        if ic not in df or not all(p in df for p in parts):
            continue
        mean = df[parts].mean(axis=1, skipna=False).to_numpy()
        got = df[ic].to_numpy(float)
        mism = ~((np.isnan(mean) & np.isnan(got)) | (np.abs(mean - got) <= 0.01))
        if mism.any():
            bad_any = True
            log(f"[FAIL] barrier_index: {ic}: {mism.sum():,} tracts differ from the mean of "
                f"the four pctl_{s}_ percentiles")
            issues.append(_issues(df, mism, "barrier_index", ic,
                                  [f"file = {gv}, mean = {mv}" for gv, mv in zip(got, mean)]))
        else:
            log(f"  [ok] barrier_index: {ic} = mean of the four barrier percentiles")
    if bad_any:
        fail("barrier_index")
    return issues


# ---------------------------------------------------------------------------
# County table
# ---------------------------------------------------------------------------
def county_crosswalk(tracts: pd.DataFrame) -> pd.Series:
    w = tracts[["FIPS", "burden_FIPS"]].copy()
    w["w"] = tracts.get("n_women_40_74", pd.Series(1, index=tracts.index)).fillna(0) + 1e-9
    agg = w.groupby(["FIPS", "burden_FIPS"])["w"].sum().reset_index()
    multi = agg.groupby("FIPS")["burden_FIPS"].nunique()
    multi = multi[multi > 1]
    if len(multi):
        log(f"[note] {len(multi)} map counties span >1 burden county "
            f"(burden = the one with most women 40-74): {', '.join(multi.index[:20])}"
            + (" ..." if len(multi) > 20 else ""))
    best = agg.sort_values("w", ascending=False).drop_duplicates("FIPS")
    return best.set_index("FIPS")["burden_FIPS"]


MED_COLS = (BARRIERS + DEMOG + ["dist_mi"]
            + [f"pctl_{s}_{b}" for s in SCALES for b in BARRIERS]
            + [f"barrier_index_{s}" for s in SCALES] + [f"pctl_barriers_{s}" for s in SCALES]
            + [f"pctl_access_{s}" for s in SCALES])


def build_counties(folder: Path, tracts: pd.DataFrame) -> pd.DataFrame:
    burden = read_csv(folder, "burden", BURDEN_FILE_COLS, "FIPS")
    burden = burden.rename(columns={"FIPS": "burden_FIPS"})

    ids = set(tracts["FIPS"])
    if GEO_COUNTIES.exists():
        gj = json.loads(GEO_COUNTIES.read_text())
        geo_ids = {f["properties"]["GEOID"] for f in gj["features"]}
        log(f"  map counties: {len(geo_ids):,}; counties with tract data: {len(ids):,}; "
            f"map counties without tracts: {len(geo_ids - ids):,}")
        ids |= geo_ids
    cty = pd.DataFrame({"GEOID": sorted(ids)})
    cty["burden_FIPS"] = cty["GEOID"].map(county_crosswalk(tracts)).fillna(cty["GEOID"])
    cty = cty.merge(burden, on="burden_FIPS", how="left")
    no_burden = cty[[m for m in BURDEN if m in cty]].isna().all(axis=1).sum()
    unused = (~burden["burden_FIPS"].isin(cty["burden_FIPS"])).sum()
    log(f"  county burden: {len(cty) - no_burden:,} counties with any burden value; "
        f"{no_burden:,} all-NA (suppressed / unmatched); {unused:,} burden rows unused")

    g = tracts.groupby("FIPS")
    agg = pd.DataFrame(index=g.size().index)
    agg["n_tracts"] = g.size()
    for c in COUNTS:
        if c in tracts:
            agg[c] = g[c].sum(min_count=1)
    for c in MED_COLS:
        if c in tracts:
            agg[f"med_{c}"] = g[c].median()
    if {"county", "state"} <= set(tracts.columns):
        names = g[["county", "state"]].first()
        agg["NAME"] = names["county"] + ", " + names["state"]
    return cty.merge(agg, left_on="GEOID", right_index=True, how="left")


# ---------------------------------------------------------------------------
# Variable catalog
# ---------------------------------------------------------------------------
def v(id_, label, group, fmt, *, pages=("b",), type_="numeric", desc="", colorscale="YlOrRd",
      short=None, table=None, by_scale=None, dynamic=False, levels=None, colors=None):
    d = {"id": id_, "label": label, "short": short, "group": group, "pages": list(pages),
         "type": type_, "fmt": fmt, "description": desc, "colorscale": colorscale}
    if table:
        d["table"] = table
    if by_scale:
        d["by_scale"] = by_scale        # resolved to a column by the selected ranking (page 2)
    if dynamic:
        d["dynamic"] = True             # computed in the app at the selected cutoffs
    if levels is not None:
        d["levels"], d["colors"] = levels, colors
    return d


def catalog(level: str, levels: list[str], colors: list[str]) -> list[dict]:
    p = "" if level == "tract" else "med_"
    agg = "" if level == "tract" else " (median tract)"
    c = []
    # ---- Page 2: Priority & Access ----
    if level == "tract":
        c.append(v("priority_class", "Priority class", "Priority", "text", pages=["pa"],
                   type_="nominal", levels=levels, colors=colors, dynamic=True,
                   desc="Class at the burden measure, ranking and cutoffs selected below"))
    else:
        c.append(v("pct_high_priority", "% of tracts High priority", "Priority", "pct",
                   pages=["pa"], dynamic=True, short="% High priority",
                   desc="Share of the county's classified tracts that are High priority "
                        "at the cutoffs selected below"))
    c.append(v(f"{p}dist_mi", f"Distance to nearest mammography facility{agg} (miles)",
               "Access", "mi", pages=["pa"], short="Distance (mi)",
               desc="Nearest FDA-certified facility (NEAR_DIST)"))
    c.append(v(f"{p}pctl_access", f"Access percentile{agg}", "Access", "pctl", pages=["pa"],
               short="Access pctl",
               by_scale={s: f"{p}pctl_access_{s}" for s in SCALES},
               desc="Distance percentile (higher = farther); follows the ranking selected below"))
    for s in SCALES:   # explicit versions: scatterplot only
        c.append(v(f"{p}pctl_access_{s}", f"Access percentile{agg} ({SCALE_LABEL[s]})",
                   "Access", "pctl", pages=[], short=f"Access pctl ({s})"))

    # ---- Page 3: everything else ----
    for s in SCALES:
        comp = [f"{p}pctl_{s}_{b}" for b in BARRIERS]
        c.append(v(f"{p}pctl_barriers_{s}", f"Barrier percentile{agg} ({SCALE_LABEL[s]})",
                   "Barriers", "pctl", short=f"Barrier pctl ({s})", table=comp,
                   desc="Barrier index ranked across tracts; higher = more barriers"))
    for s in SCALES:
        c.append(v(f"{p}barrier_index_{s}", f"Barrier index{agg} ({SCALE_LABEL[s]})",
                   "Barriers", "pctl", short=f"Barrier index ({s})",
                   table=[f"{p}pctl_{s}_{b}" for b in BARRIERS],
                   desc="Mean of the four barrier percentiles"))
    for b in BARRIERS:
        raw = [f"{p}{x}" for x in BARRIERS]
        c.append(v(f"{p}{b}", f"{BARRIER_LABEL[b]}{agg} (%)", "Barriers", "pct",
                   short=f"{BARRIER_SHORT[b]} %", table=raw))
        for s in SCALES:
            c.append(v(f"{p}pctl_{s}_{b}", f"{BARRIER_LABEL[b]}{agg} (pctl, {SCALE_LABEL[s]})",
                       "Barriers", "pctl", short=f"{BARRIER_SHORT[b]} pctl",
                       table=[f"{p}pctl_{s}_{x}" for x in BARRIERS]))
    for m in BURDEN:
        c.append(v(m, f"County {BURDEN_LABEL[m]}", "County burden", BURDEN_FMT[m],
                   short=f"County {BURDEN_LABEL[m]}", table=BURDEN,
                   desc="County-level context, the same for every tract in the county"))
    for m in BURDEN:
        c.append(v(f"pctl_{m}", f"County {BURDEN_LABEL[m]} (pctl)", "County burden", "pctl",
                   short=f"{MEASURE_LABEL[m]} pctl", table=[f"pctl_{x}" for x in BURDEN]))
    for k in COUNTS:
        c.append(v(k, COUNT_LABEL[k] + ("" if level == "tract" else " (sum of tracts)"),
                   "Population", "count", colorscale="Blues", short=COUNT_LABEL[k], table=COUNTS))
    c.append(v("rurality", "Urban / rural (county)", "Context", "text", type_="nominal",
               levels=RURALITY["levels"], colors=RURALITY["colors"],
               desc="2023 Rural-Urban Continuum label of the county"))
    for k in DEMOG:
        if k == "ice_race_income":
            c.append(v(f"{p}{k}", DEMOG_LABEL[k] + agg, "Context", "index", colorscale="RdBu",
                       short="ICE race+income",
                       desc="-1 to 1; higher = more privileged (context only)"))
        else:
            c.append(v(f"{p}{k}", f"{DEMOG_LABEL[k]}{agg} (%)", "Context", "pct",
                       colorscale="YlOrRd" if k == "pct_poverty_f35_74" else "Blues",
                       short=DEMOG_LABEL[k],
                       table=[f"{p}{x}" for x in DEMOG if x not in ("ice_race_income",
                                                                    "pct_poverty_f35_74")]))
    return c


def profile_rows(level: str) -> list[dict]:
    p = "" if level == "tract" else "med_"
    rows = [{"label": "Distance to facility", "dim": "access",
             "by_scale": {s: f"{p}pctl_access_{s}" for s in SCALES}},
            {"label": "Barrier index", "dim": "barriers",
             "by_scale": {s: f"{p}pctl_barriers_{s}" for s in SCALES}}]
    for b in BARRIERS:
        rows.append({"label": f"  {BARRIER_SHORT[b]}", "dim": "barrier_item",
                     "by_scale": {s: f"{p}pctl_{s}_{b}" for s in SCALES}})
    for m in BURDEN:
        rows.append({"label": f"Burden: {BURDEN_LABEL[m]}", "dim": "burden", "measure": m,
                     "id": f"pctl_{m}"})
    return rows


def available(cat: list[dict], df: pd.DataFrame) -> list[dict]:
    def ok(d):
        if d.get("dynamic"):
            return True
        if d.get("by_scale"):
            return all(c in df for c in d["by_scale"].values())
        return d["id"] in df.columns and df[d["id"]].notna().any()
    out = [d for d in cat if ok(d)]
    ids = {d["id"] for d in out}
    for d in out:
        if "table" in d:
            d["table"] = [t for t in d["table"] if t in ids]
    dropped = [d["id"] for d in cat if d["id"] not in ids]
    if dropped:
        log(f"[note] catalog: skipped (missing or all-NA): {dropped}")
    return out


def columns_needed(cat: list[dict], prof: list[dict]) -> list[str]:
    cols: list[str] = []
    for d in cat:
        if d.get("dynamic"):
            continue
        cols += list(d["by_scale"].values()) if d.get("by_scale") else [d["id"]]
    for r in prof:
        cols += list(r["by_scale"].values()) if r.get("by_scale") else [r["id"]]
    return list(dict.fromkeys(cols))


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=str(DEFAULT_IN))
    ap.add_argument("--strict", action="store_true",
                    help="exit with an error if any consistency check fails")
    args = ap.parse_args()
    folder = Path(args.input_dir)

    log(f"[1/4] Reading MVP outputs from {folder}")
    levels, colors = class_lookup(folder)
    tracts = build_tracts(folder)
    issues = run_checks(tracts, folder, levels)

    log(f"[2/4] Tract table: {len(tracts):,} tracts, {tracts['FIPS'].nunique():,} counties")
    for m in BURDEN:
        c = f"class_{m}"
        if c in tracts:
            vc = tracts[c].value_counts(dropna=False)
            log(f"  {c} (default): " + "; ".join(f"{k if pd.notna(k) else 'NA'}={n:,}"
                                                for k, n in vc.items()))
    if "rurality" in tracts:
        log("  rurality: " + "; ".join(f"{k if pd.notna(k) else 'NA'}={n:,}"
                                       for k, n in tracts["rurality"].value_counts(dropna=False).items()))

    log("[3/4] County table")
    counties = build_counties(folder, tracts)

    period = (tracts["acs_period"].dropna().mode().iat[0]
              if "acs_period" in tracts and tracts["acs_period"].notna().any() else "2019-2023")
    t_cat = available(catalog("tract", levels, colors), tracts)
    c_cat = available(catalog("county", levels, colors), counties)
    meta = {
        "acs_period": period,
        "burden_source": "State Cancer Profiles, female breast cancer, late stage, 2019-2023",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "qa_failed": FAILED,
        "classes": {"levels": levels, "colors": colors},
        "cutoffs": {"options": list(CUTOFFS), "defaults": DEFAULTS},
        "measures": MEASURE_LABEL,
        "scales": {"ur": "Urban/rural", "nat": "National"},
        "defaults": {
            "tract": {"pa": "priority_class", "b": "pctl_barriers_ur",
                      "x": "pctl_barriers_ur", "y": "pctl_access_ur"},
            "county": {"pa": "pct_high_priority", "b": "med_pctl_barriers_ur",
                       "x": "med_pctl_barriers_ur", "y": "med_pctl_access_ur"},
        },
        "profile": {"tract": profile_rows("tract"), "county": profile_rows("county")},
    }
    catalog_json = {"meta": meta, "tract": t_cat, "county": c_cat}

    log("[4/4] Writing outputs")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t_cols = (["GEOID", "FIPS", "burden_FIPS", "county", "state"] + LVL_COLS
              + [f"class_{m}" for m in BURDEN]
              + columns_needed(t_cat, meta["profile"]["tract"]))
    c_cols = (["GEOID", "burden_FIPS", "NAME", "n_tracts"]
              + columns_needed(c_cat, meta["profile"]["county"]))
    tracts[[c for c in dict.fromkeys(t_cols) if c in tracts]].to_csv(
        OUT_DIR / "tract_values.csv", index=False)
    counties[[c for c in dict.fromkeys(c_cols) if c in counties]].to_csv(
        OUT_DIR / "county_values.csv", index=False)
    (OUT_DIR / "variables.json").write_text(json.dumps(catalog_json, indent=1))
    log(f"  tract_values.csv  ({len(tracts):,} rows, {len(t_cat)} variables)")
    log(f"  county_values.csv ({len(counties):,} rows, {len(c_cat)} variables)")

    issue_path = OUT_DIR / "qa_issues.csv"
    if issues:
        pd.concat(issues, ignore_index=True).to_csv(issue_path, index=False)
        log(f"  qa_issues.csv ({sum(len(i) for i in issues):,} rows)")
    elif issue_path.exists():
        issue_path.unlink()
    old = OUT_DIR / "qa_tertile_issues.csv"       # from the previous (tertile) version
    if old.exists():
        old.unlink()

    log("QA summary: " + ("all checks passed" if not FAILED else f"FAILED checks: {', '.join(FAILED)}"))
    (OUT_DIR / "prep_qa.txt").write_text("\n".join(QA) + "\n")
    print(f"QA report: {OUT_DIR / 'prep_qa.txt'}")
    if FAILED and args.strict:
        raise SystemExit(f"--strict: consistency checks failed: {', '.join(FAILED)}")


if __name__ == "__main__":
    main()
