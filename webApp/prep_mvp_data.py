"""
prep_mvp_data.py — build the app-ready tables from the MVP classification outputs
(scripts/processing/code_for_mvp_classification_v2.R; see data_dictionary.md).

Inputs (default folder: data/raw_mvp/, override with --input-dir)
    df_w_calcs.csv          tract  — derived ACS indicators + identifier columns
    df_percentiles.csv      tract  — barrier / distance / burden percentiles
    df_rank.csv             tract  — tertile labels (low / moderate / high), incl. the
                                     four per-barrier tertiles (<barrier>_t)
    df_classification.csv   tract  — barrier score, access tertile, burden tertiles, priority classes
    county_burden.csv       county — late-stage female breast cancer burden (burden FIPS)
    (df_classification_long.csv is not needed: same content as df_classification, long format.)

Outputs (data/app/)
    tract_values.csv        one row per tract   (GEOID = GEOID20)
    county_values.csv       one row per county  (GEOID = current county FIPS, matches the map)
    variables.json          variable catalog the app reads (labels, groups, types, colors)
    prep_qa.txt             join / coverage / consistency report
    qa_tertile_issues.csv   tract-level rows that failed a tertile consistency check (if any)

Tertile consistency checks (df_rank vs df_classification vs df_percentiles)
    1. Labels      every df_rank tertile value is low / moderate / high (or 1-3)
    2. Score sum   sum of the four barrier tertiles == barriers (4-12)
    3. Score NA    barriers is NA exactly when any barrier tertile is NA
    4. Ordering    within each barrier, tertile never decreases as the percentile increases
                   (no tract ranked lower than a tract with a smaller percentile)
    5. Cross-file  access / burden_* tertiles in df_rank equal the 1-3 codes in df_classification
    Use --strict to stop with an error if any check fails.

County-level table
    * Burden values come from county_burden.csv via burden_FIPS
      (Connecticut planning regions -> pre-2022 counties; AK 02063/02066 -> 02261).
      When the tracts of one map county point to more than one burden_FIPS
      (Connecticut), the burden_FIPS covering the most women 40-74 is used and
      the county is listed in prep_qa.txt.
    * Tract indicators are summarized per county: counts are summed, rates and
      percentiles are the MEDIAN across the county's tracts, priority classes become
      "% of classified tracts that are High priority", and barrier tertiles become
      "% of tracts in the High tertile".

Usage
    python prep_mvp_data.py
    python prep_mvp_data.py --input-dir "C:/path/to/mvp_outputs"
    python prep_mvp_data.py --strict
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

APP_DIR = Path(__file__).resolve().parent
DEFAULT_IN = APP_DIR / "data" / "raw_mvp"
OUT_DIR = APP_DIR / "data" / "app"
GEO_COUNTIES = APP_DIR / "data" / "geo" / "counties.geojson"

FILES = {
    "calcs": "df_w_calcs.csv",
    "pctl": "df_percentiles.csv",
    "rank": "df_rank.csv",
    "cls": "df_classification.csv",
    "burden": "county_burden.csv",
}

ID_COLS = ["GEOID20", "FIPS", "county", "state", "acs_period", "burden_FIPS"]
BURDEN = ["aair", "cases", "late_stage"]
BARRIERS = ["pct_uninsured_f35_74", "pct_no_vehicle_hh", "pct_no_hs_f25", "pct_lep_hh"]
BARRIER_T = [f"{b}_t" for b in BARRIERS]
DEMOG = ["pct_nh_white", "pct_nh_black", "pct_nh_aian", "pct_nh_asian", "pct_hispanic",
         "ice_race_income", "pct_poverty_f35_74"]
COUNTS = ["n_women_40_74", "n_women_40_49", "n_women_50_74"]

CALC_COLS = COUNTS + DEMOG + BARRIERS
PCTL_COLS = [f"pctl_{b}" for b in BARRIERS] + ["pctl_NEAR_DIST"] + [f"pctl_{m}" for m in BURDEN]
RANK_OTHER = ["access"] + [f"burden_{m}" for m in BURDEN]   # cross-checked only
RANK_COLS = BARRIER_T + RANK_OTHER
CLS_COLS = BURDEN + ["barriers", "access"] + [f"burden_{m}" for m in BURDEN] + [f"class_{m}" for m in BURDEN]
BURDEN_FILE_COLS = ["FIPS"] + BURDEN + [f"pctl_{m}" for m in BURDEN] + [f"burden_{m}" for m in BURDEN]

TERTILE_CODE = {"low": 1, "moderate": 2, "mod": 2, "medium": 2, "med": 2, "high": 3,
                "1": 1, "2": 2, "3": 3, "1.0": 1, "2.0": 2, "3.0": 3}

CLASS_LEVELS = [
    "High priority", "Access gap", "Barrier gap", "Monitor", "Lower priority",
    "Burden suppressed: gaps present", "Burden suppressed: no gaps",
]
CLASS_COLORS = ["#B2182B", "#F4A261", "#8E5EA2", "#F6D55C", "#9DC3E6", "#7F7F7F", "#D9D9D9"]
TERTILE = {"values": [1, 2, 3], "labels": ["Low", "Moderate", "High"]}
TERTILE_COLORS = ["#FEE8C8", "#FDBB84", "#E34A33"]

BURDEN_LABEL = {"aair": "incidence rate", "cases": "cases", "late_stage": "% late stage"}
BARRIER_LABEL = {
    "pct_uninsured_f35_74": "Uninsured women 35-74",
    "pct_no_vehicle_hh": "Households without a vehicle",
    "pct_no_hs_f25": "Women 25+ without HS diploma",
    "pct_lep_hh": "Limited-English households",
}
BARRIER_SHORT = {
    "pct_uninsured_f35_74": "Uninsured",
    "pct_no_vehicle_hh": "No vehicle",
    "pct_no_hs_f25": "No HS diploma",
    "pct_lep_hh": "Limited English",
}
DEMOG_LABEL = {
    "pct_nh_white": "Non-Hispanic White", "pct_nh_black": "Non-Hispanic Black",
    "pct_nh_aian": "Non-Hispanic AIAN", "pct_nh_asian": "Non-Hispanic Asian",
    "pct_hispanic": "Hispanic or Latino", "ice_race_income": "ICE race + income",
    "pct_poverty_f35_74": "Women 35-74 below poverty",
}
COUNT_LABEL = {"n_women_40_74": "Women 40-74", "n_women_40_49": "Women 40-49",
               "n_women_50_74": "Women 50-74"}


# ---------------------------------------------------------------------------
# Reading / validation
# ---------------------------------------------------------------------------
QA: list[str] = []
FAILED: list[str] = []   # names of failed consistency checks (for --strict)


def log(msg: str) -> None:
    print(msg)
    QA.append(msg)


def read_csv(folder: Path, key: str, want: list[str], id_col: str,
             required: bool = True) -> pd.DataFrame | None:
    path = folder / FILES[key]
    if not path.exists():
        if required:
            raise SystemExit(f"Missing input file: {path}")
        log(f"[warn] {path.name} not found — skipped")
        return None
    df = pd.read_csv(path, dtype=str, keep_default_na=True, na_values=["NA", ""])
    df.columns = df.columns.str.strip()
    if id_col not in df.columns:
        raise SystemExit(f"{path.name}: required id column '{id_col}' not found")
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

    dups = df[id_col].duplicated()
    if dups.any():
        log(f"[warn] {path.name}: {dups.sum():,} duplicate {id_col} rows dropped (kept first)")
        df = df[~dups]
    log(f"  {path.name:<24} {len(df):>7,} rows, {len(keep) - 1} columns used")
    return df


def to_num(df: pd.DataFrame, cols: list[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def tertile_codes(rank: pd.DataFrame, issues: list[pd.DataFrame]) -> pd.DataFrame:
    """Convert df_rank text labels to 1-3 (QA check 1: labels)."""
    out = rank.copy()
    bad_total = 0
    for c in RANK_COLS:
        if c not in out:
            continue
        raw = out[c]
        norm = raw.str.strip().str.lower()
        code = norm.map(TERTILE_CODE)
        bad = raw.notna() & code.isna()
        if bad.any():
            bad_total += int(bad.sum())
            log(f"[FAIL] labels: df_rank.{c} has {bad.sum():,} unrecognized values "
                f"{raw[bad].value_counts().head(5).to_dict()} -> set to NA")
            issues.append(pd.DataFrame({"GEOID": out.loc[bad, "GEOID20"], "check": "labels",
                                        "column": c, "detail": "unrecognized: " + raw[bad]}))
        out[c] = code.astype(float)
    if bad_total:
        FAILED.append("labels")
    else:
        log("  [ok] labels: all df_rank tertile values are low / moderate / high")
    return out


# ---------------------------------------------------------------------------
# Tertile consistency checks (2-5)
# ---------------------------------------------------------------------------
def check_tertiles(df: pd.DataFrame, rank_raw: pd.DataFrame | None,
                   issues: list[pd.DataFrame]) -> None:
    have_t = [c for c in BARRIER_T if c in df]
    if len(have_t) < 4 or "barriers" not in df:
        log("[warn] tertile checks skipped: barrier tertiles or barriers score not available")
        return

    t = df[have_t]
    all_t = t.notna().all(axis=1)

    # 2. Score sum
    tsum = t.sum(axis=1, min_count=4)
    both = all_t & df["barriers"].notna()
    diff = both & (tsum != df["barriers"])
    if diff.any():
        FAILED.append("score_sum")
        log(f"[FAIL] score sum: {diff.sum():,} of {both.sum():,} tracts where the four "
            f"barrier tertiles do not add up to 'barriers' "
            f"(examples: {', '.join(df.loc[diff, 'GEOID'].head(5))})")
        issues.append(pd.DataFrame({
            "GEOID": df.loc[diff, "GEOID"], "check": "score_sum", "column": "barriers",
            "detail": "sum of tertiles = " + tsum[diff].astype(int).astype(str)
                      + ", barriers = " + df.loc[diff, "barriers"].astype(int).astype(str)}))
    else:
        log(f"  [ok] score sum: tertiles add up to 'barriers' for all {both.sum():,} tracts")

    # 3. Score NA pattern
    na_mismatch = (all_t & df["barriers"].isna()) | (~all_t & df["barriers"].notna())
    if na_mismatch.any():
        FAILED.append("score_na")
        a = int((all_t & df["barriers"].isna()).sum())
        b = int((~all_t & df["barriers"].notna()).sum())
        log(f"[FAIL] score NA: {a:,} tracts have all 4 tertiles but barriers = NA; "
            f"{b:,} tracts have barriers but a missing tertile")
        issues.append(pd.DataFrame({
            "GEOID": df.loc[na_mismatch, "GEOID"], "check": "score_na", "column": "barriers",
            "detail": np.where(all_t[na_mismatch], "all tertiles present, barriers NA",
                               "barriers present, tertile NA")}))
    else:
        n_na = int((~all_t).sum())
        log(f"  [ok] score NA: barriers is NA exactly when a tertile is NA ({n_na:,} tracts)")

    # 4. Ordering vs percentile
    any_order_fail = False
    for b in BARRIERS:
        tc, pc = f"{b}_t", f"pctl_{b}"
        if tc not in df or pc not in df:
            continue
        d = df[["GEOID", tc, pc]].dropna()
        if d.empty:
            continue
        rng = d.groupby(tc)[pc].agg(["min", "max", "count"])
        bad_ids = []
        for k in (1, 2):
            if k in rng.index and k + 1 in rng.index:
                cut = rng.at[k + 1, "min"]
                over = d[(d[tc] == k) & (d[pc] > cut)]
                bad_ids.append(over)
        bad = pd.concat(bad_ids) if bad_ids else d.iloc[0:0]
        bounds = "; ".join(f"T{int(k)}: {r['min']:.1f}-{r['max']:.1f} (n={int(r['count']):,})"
                           for k, r in rng.iterrows())
        if len(bad):
            any_order_fail = True
            log(f"[FAIL] ordering: {tc}: {len(bad):,} tracts rank lower than tracts with a "
                f"smaller percentile. Ranges {bounds}")
            issues.append(pd.DataFrame({
                "GEOID": bad["GEOID"], "check": "ordering", "column": tc,
                "detail": "tertile " + bad[tc].astype(int).astype(str)
                          + " but percentile " + bad[pc].round(2).astype(str)}))
        else:
            log(f"  [ok] ordering: {tc}: {bounds}")
    if any_order_fail:
        FAILED.append("ordering")

    # 5. Cross-file: df_rank access/burden tertiles vs df_classification codes
    if rank_raw is not None:
        cross_fail = False
        for c in RANK_OTHER:
            rc = f"{c}__rank"
            if rc not in df or c not in df:
                continue
            both = df[rc].notna() & df[c].notna()
            mism = both & (df[rc] != df[c])
            na_diff = df[rc].isna() != df[c].isna()
            if mism.any() or na_diff.any():
                cross_fail = True
                log(f"[FAIL] cross-file: {c}: {mism.sum():,} value mismatches, "
                    f"{na_diff.sum():,} NA mismatches between df_rank and df_classification")
                sel = mism | na_diff
                issues.append(pd.DataFrame({
                    "GEOID": df.loc[sel, "GEOID"], "check": "cross_file", "column": c,
                    "detail": "df_rank = " + df.loc[sel, rc].astype(str)
                              + ", df_classification = " + df.loc[sel, c].astype(str)}))
            else:
                log(f"  [ok] cross-file: {c} matches between df_rank and df_classification "
                    f"({both.sum():,} tracts)")
        if cross_fail:
            FAILED.append("cross_file")


# ---------------------------------------------------------------------------
# Tract table
# ---------------------------------------------------------------------------
def build_tracts(folder: Path) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    issues: list[pd.DataFrame] = []
    calcs = read_csv(folder, "calcs", ID_COLS + CALC_COLS, "GEOID20")
    pctl = read_csv(folder, "pctl", PCTL_COLS, "GEOID20")
    rank_raw = read_csv(folder, "rank", RANK_COLS, "GEOID20", required=False)
    cls = read_csv(folder, "cls", CLS_COLS, "GEOID20")

    rank = None
    if rank_raw is not None:
        rank = tertile_codes(rank_raw, issues)
        # keep access/burden from df_rank under a suffix — only used for the cross-file check
        rank = rank.rename(columns={c: f"{c}__rank" for c in RANK_OTHER if c in rank})

    df = calcs
    for name, other in (("df_percentiles", pctl), ("df_rank", rank), ("df_classification", cls)):
        if other is None:
            continue
        other = other.drop(columns=[c for c in other.columns if c in df.columns and c != "GEOID20"])
        n_no = (~df["GEOID20"].isin(other["GEOID20"])).sum()
        n_extra = (~other["GEOID20"].isin(df["GEOID20"])).sum()
        if n_no or n_extra:
            log(f"[warn] {name}: {n_no:,} tracts in df_w_calcs not found; "
                f"{n_extra:,} extra tracts ignored")
        df = df.merge(other, on="GEOID20", how="left", validate="one_to_one")

    num = CALC_COLS + PCTL_COLS + BURDEN + ["barriers", "access"] + [f"burden_{m}" for m in BURDEN]
    to_num(df, num)

    for m in BURDEN:
        c = f"class_{m}"
        if c in df.columns:
            df[c] = df[c].str.strip()
            bad = df[c].notna() & ~df[c].isin(CLASS_LEVELS)
            if bad.any():
                log(f"[warn] {c}: unexpected values set to NA: "
                    f"{df.loc[bad, c].value_counts().head(5).to_dict()}")
                df.loc[bad, c] = pd.NA

    if "FIPS" not in df.columns:
        df["FIPS"] = df["GEOID20"].str[:5]
    if "burden_FIPS" not in df.columns:
        df["burden_FIPS"] = df["FIPS"]
    df = df.rename(columns={"GEOID20": "GEOID"})

    log("  Tertile consistency checks")
    check_tertiles(df, rank_raw, issues)
    df = df.drop(columns=[c for c in df.columns if c.endswith("__rank")])
    return df, issues


# ---------------------------------------------------------------------------
# County table
# ---------------------------------------------------------------------------
def county_crosswalk(tracts: pd.DataFrame) -> pd.Series:
    """Map county FIPS -> burden_FIPS (women-weighted majority when ambiguous)."""
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


def _pct_where(tracts: pd.DataFrame, col: str, cond: pd.Series) -> pd.Series:
    """% of tracts (with non-missing col) in each county where cond is True."""
    g = tracts.assign(_hit=cond & tracts[col].notna(), _has=tracts[col].notna()).groupby("FIPS")
    has = g["_has"].sum()
    return (g["_hit"].sum() / has.where(has > 0) * 100).round(2)


def build_counties(folder: Path, tracts: pd.DataFrame) -> pd.DataFrame:
    burden = read_csv(folder, "burden", BURDEN_FILE_COLS, "FIPS")
    to_num(burden, [c for c in BURDEN_FILE_COLS if c != "FIPS"])
    burden = burden.rename(columns={"FIPS": "burden_FIPS"})

    ids = set(tracts["FIPS"])
    if GEO_COUNTIES.exists():
        gj = json.loads(GEO_COUNTIES.read_text())
        geo_ids = {f["properties"]["GEOID"] for f in gj["features"]}
        log(f"  map counties: {len(geo_ids):,}; counties with tract data: {len(ids):,}; "
            f"map counties without tracts: {len(geo_ids - ids):,}")
        ids |= geo_ids
    cty = pd.DataFrame({"GEOID": sorted(ids)})

    xw = county_crosswalk(tracts)
    cty["burden_FIPS"] = cty["GEOID"].map(xw).fillna(cty["GEOID"])
    cty = cty.merge(burden, on="burden_FIPS", how="left")
    no_burden = cty[BURDEN].isna().all(axis=1).sum()
    unused = (~burden["burden_FIPS"].isin(cty["burden_FIPS"])).sum()
    log(f"  county burden: {len(cty) - no_burden:,} counties with any burden value; "
        f"{no_burden:,} all-NA (suppressed / unmatched); {unused:,} burden rows unused")

    g = tracts.groupby("FIPS")
    agg = pd.DataFrame(index=g.size().index)
    agg["n_tracts"] = g.size()
    for c in COUNTS:
        if c in tracts:
            agg[c] = g[c].sum(min_count=1)
    for c in BARRIERS + DEMOG + ["barriers"] + [p for p in PCTL_COLS if p.startswith("pctl_pct_")] \
            + ["pctl_NEAR_DIST"]:
        if c in tracts:
            agg[f"med_{c}"] = g[c].median()
    for c in BARRIER_T:                                  # % of tracts in the High tertile
        if c in tracts:
            agg[f"pct_high_{c}"] = _pct_where(tracts, c, tracts[c].eq(3))
    for m in BURDEN:
        c = f"class_{m}"
        if c in tracts:
            agg[f"pct_high_{m}"] = _pct_where(tracts, c, tracts[c].eq("High priority"))
    if {"county", "state"} <= set(tracts.columns):
        names = g[["county", "state"]].first()
        agg["NAME"] = names["county"] + ", " + names["state"]
    cty = cty.merge(agg, left_on="GEOID", right_index=True, how="left")
    return cty


# ---------------------------------------------------------------------------
# Variable catalog
# ---------------------------------------------------------------------------
def v(id_, label, group, fmt, *, type_="numeric", desc="", colorscale="YlOrRd",
      reverse=False, profile=False, levels=None, colors=None, short=None, table=None):
    d = {"id": id_, "label": label, "short": short, "group": group, "type": type_, "fmt": fmt,
         "description": desc, "colorscale": colorscale, "reverse": reverse, "profile": profile}
    if levels is not None:
        d["levels"] = levels
        d["colors"] = colors
    if table:
        d["table"] = table   # variables listed in the info boxes when this one is selected
    return d


def tract_catalog() -> list[dict]:
    tert_table = BARRIER_T + ["barriers"]
    c = []
    for m in BURDEN:
        c.append(v(f"class_{m}", f"Priority class ({BURDEN_LABEL[m]} view)", "Priority", "text",
                   type_="nominal", levels=CLASS_LEVELS, colors=CLASS_COLORS,
                   short=f"Class · {BURDEN_LABEL[m]}",
                   desc=f"Priority class using county {BURDEN_LABEL[m]} burden"))
    c.append(v("barriers", "Barrier score (4-12)", "Priority", "score", short="Barrier score",
               desc="Sum of the four barrier tertiles (Low = 1, Moderate = 2, High = 3)",
               table=tert_table))
    c.append(v("access", "Distance-to-mammography tertile", "Access", "ordinal", type_="ordinal",
               levels=TERTILE, colors=TERTILE_COLORS, desc="High = farthest"))
    c.append(v("pctl_NEAR_DIST", "Distance to nearest facility (pctl)", "Access", "pctl",
               profile=True, desc="Higher = farther away; ranked across US tracts"))
    for b in BARRIERS:
        c.append(v(f"{b}_t", f"{BARRIER_LABEL[b]} (tertile)", "Barriers", "ordinal",
                   type_="ordinal", levels=TERTILE, colors=TERTILE_COLORS,
                   short=f"{BARRIER_SHORT[b]} tertile", table=tert_table,
                   desc="Barrier tertile from df_rank; the four tertiles sum to the barrier score"))
        c.append(v(b, f"{BARRIER_LABEL[b]} (%)", "Barriers", "pct"))
        c.append(v(f"pctl_{b}", f"{BARRIER_LABEL[b]} (pctl)", "Barriers", "pctl", profile=True))
    for m in BURDEN:
        fmt = {"aair": "rate", "cases": "count1", "late_stage": "pct"}[m]
        c.append(v(m, f"County {BURDEN_LABEL[m]}", "County burden", fmt,
                   desc="County-level context, same for every tract in the county"))
        c.append(v(f"pctl_{m}", f"County {BURDEN_LABEL[m]} (pctl)", "County burden", "pctl",
                   profile=True))
        c.append(v(f"burden_{m}", f"County {BURDEN_LABEL[m]} tertile", "County burden", "ordinal",
                   type_="ordinal", levels=TERTILE, colors=TERTILE_COLORS))
    for k in COUNTS:
        c.append(v(k, COUNT_LABEL[k], "Population", "count", colorscale="Blues"))
    for k in DEMOG:
        if k == "ice_race_income":
            c.append(v(k, DEMOG_LABEL[k], "Context", "index", colorscale="RdBu",
                       desc="-1 to 1; higher = more privileged (not used in score)"))
        else:
            c.append(v(k, f"{DEMOG_LABEL[k]} (%)", "Context", "pct",
                       colorscale="YlOrRd" if k == "pct_poverty_f35_74" else "Blues"))
    return c


def county_catalog() -> list[dict]:
    tert_table = [f"pct_high_{t}" for t in BARRIER_T] + ["med_barriers"]
    c = []
    for m in BURDEN:
        c.append(v(f"pct_high_{m}", f"% tracts High priority ({BURDEN_LABEL[m]} view)", "Priority",
                   "pct", short=f"% High · {BURDEN_LABEL[m]}",
                   desc="Share of classified tracts in the county that are High priority"))
    c.append(v("med_barriers", "Barrier score (median tract)", "Priority", "score",
               short="Barrier score (median)", table=tert_table))
    c.append(v("med_pctl_NEAR_DIST", "Distance to facility (median tract pctl)", "Access", "pctl",
               profile=True))
    for b in BARRIERS:
        c.append(v(f"pct_high_{b}_t", f"{BARRIER_LABEL[b]} (% tracts High tertile)", "Barriers",
                   "pct", short=f"{BARRIER_SHORT[b]}: % High", table=tert_table,
                   desc="Share of the county's tracts in the High tertile for this barrier"))
        c.append(v(f"med_{b}", f"{BARRIER_LABEL[b]} (median tract %)", "Barriers", "pct"))
        c.append(v(f"med_pctl_{b}", f"{BARRIER_LABEL[b]} (median tract pctl)", "Barriers", "pctl",
                   profile=True))
    for m in BURDEN:
        fmt = {"aair": "rate", "cases": "count1", "late_stage": "pct"}[m]
        c.append(v(m, f"County {BURDEN_LABEL[m]}", "County burden", fmt))
        c.append(v(f"pctl_{m}", f"County {BURDEN_LABEL[m]} (pctl)", "County burden", "pctl",
                   profile=True))
        c.append(v(f"burden_{m}", f"County {BURDEN_LABEL[m]} tertile", "County burden", "ordinal",
                   type_="ordinal", levels=TERTILE, colors=TERTILE_COLORS))
    for k in COUNTS:
        c.append(v(k, f"{COUNT_LABEL[k]} (sum of tracts)", "Population", "count", colorscale="Blues"))
    for k in DEMOG:
        if k == "ice_race_income":
            c.append(v(f"med_{k}", f"{DEMOG_LABEL[k]} (median tract)", "Context", "index",
                       colorscale="RdBu"))
        else:
            c.append(v(f"med_{k}", f"{DEMOG_LABEL[k]} (median tract %)", "Context", "pct",
                       colorscale="YlOrRd" if k == "pct_poverty_f35_74" else "Blues"))
    return c


def available(cat: list[dict], df: pd.DataFrame) -> list[dict]:
    out = [d for d in cat if d["id"] in df.columns and df[d["id"]].notna().any()]
    ids = {d["id"] for d in out}
    for d in out:   # drop table entries that were skipped
        if "table" in d:
            d["table"] = [t for t in d["table"] if t in ids]
    dropped = [d["id"] for d in cat if d["id"] not in ids]
    if dropped:
        log(f"[note] catalog: skipped (missing or all-NA): {dropped}")
    return out


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=str(DEFAULT_IN))
    ap.add_argument("--strict", action="store_true",
                    help="exit with an error if any tertile consistency check fails")
    args = ap.parse_args()
    folder = Path(args.input_dir)

    log(f"[1/4] Reading MVP outputs from {folder}")
    tracts, issues = build_tracts(folder)
    log(f"[2/4] Tract table: {len(tracts):,} tracts, {tracts['FIPS'].nunique():,} counties")
    for m in BURDEN:
        c = f"class_{m}"
        if c in tracts:
            vc = tracts[c].value_counts(dropna=False)
            log(f"  {c}: " + "; ".join(f"{k if pd.notna(k) else 'NA'}={n:,}" for k, n in vc.items()))
    for c in BARRIER_T:
        if c in tracts:
            vc = tracts[c].value_counts(dropna=False).sort_index()
            lab = {1.0: "Low", 2.0: "Moderate", 3.0: "High"}
            log(f"  {c}: " + "; ".join(f"{lab.get(k, 'NA')}={n:,}" for k, n in vc.items()))

    log("[3/4] County table")
    counties = build_counties(folder, tracts)

    period = (tracts["acs_period"].dropna().mode().iat[0]
              if "acs_period" in tracts and tracts["acs_period"].notna().any() else "2019-2023")
    meta = {
        "acs_period": period,
        "burden_source": "State Cancer Profiles, female breast cancer, late stage, 2019-2023",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "qa_failed": FAILED,
        "defaults": {
            "tract": {"var": "class_aair", "x": "barriers", "y": "pctl_NEAR_DIST"},
            "county": {"var": "pct_high_aair", "x": "med_barriers", "y": "pct_high_aair"},
        },
    }
    catalog = {"meta": meta,
               "tract": available(tract_catalog(), tracts),
               "county": available(county_catalog(), counties)}

    log("[4/4] Writing outputs")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t_cols = ["GEOID", "FIPS", "burden_FIPS", "county", "state"] + [d["id"] for d in catalog["tract"]]
    c_cols = ["GEOID", "burden_FIPS", "NAME", "n_tracts"] + [d["id"] for d in catalog["county"]]
    tracts[[c for c in t_cols if c in tracts]].to_csv(OUT_DIR / "tract_values.csv", index=False)
    counties[[c for c in dict.fromkeys(c_cols) if c in counties]].to_csv(
        OUT_DIR / "county_values.csv", index=False)
    (OUT_DIR / "variables.json").write_text(json.dumps(catalog, indent=1))
    log(f"  {OUT_DIR / 'tract_values.csv'}  ({len(tracts):,} rows, {len(catalog['tract'])} variables)")
    log(f"  {OUT_DIR / 'county_values.csv'} ({len(counties):,} rows, {len(catalog['county'])} variables)")

    issue_path = OUT_DIR / "qa_tertile_issues.csv"
    if issues:
        pd.concat(issues, ignore_index=True).to_csv(issue_path, index=False)
        log(f"  {issue_path} ({sum(len(i) for i in issues):,} rows)")
    elif issue_path.exists():
        issue_path.unlink()

    log("QA summary: " + ("all tertile checks passed" if not FAILED
                          else f"FAILED checks: {', '.join(FAILED)}"))
    (OUT_DIR / "prep_qa.txt").write_text("\n".join(QA) + "\n")
    print(f"QA report: {OUT_DIR / 'prep_qa.txt'}")
    if FAILED and args.strict:
        raise SystemExit(f"--strict: tertile consistency checks failed: {', '.join(FAILED)}")


if __name__ == "__main__":
    main()
