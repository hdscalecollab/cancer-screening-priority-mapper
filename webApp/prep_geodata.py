"""
prep_geodata.py — one-time build of the geography files used by app.py.

Downloads Census cartographic boundary files (500k), computes polygon
adjacency (queen contiguity: shared edge OR shared vertex) on the full-
resolution geometry, simplifies geometry for fast web display, and writes:

    data/geo/states.json
    data/geo/counties.geojson
    data/geo/tracts/<STATEFP>.geojson
    data/geo/neighbors/county.json
    data/geo/neighbors/tract_<STATEFP>.json

Geography only — independent of the CSV years. Only needed locally
(NOT on shinyapps.io). Requires: geopandas, pyogrio.
    pip install -r requirements-prep.txt
    python prep_geodata.py                # 50 states + DC
    python prep_geodata.py --include-pr   # also Puerto Rico
    python prep_geodata.py --year 2022
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

APP_DIR = Path(__file__).resolve().parent
OUT_DIR = APP_DIR / "data" / "geo"
CACHE_DIR = Path.home() / ".cache" / "cspm_census"  # raw downloads stay out of the app bundle

BASE = "https://www2.census.gov/geo/tiger/GENZ{year}/shp/"

# Simplification tolerance in degrees (~ 0.003 deg ≈ 300 m; 0.0003 deg ≈ 30 m)
COUNTY_TOL = 0.003
TRACT_TOL = 0.0003

STATES = {
    "01": ("AL", "Alabama"), "02": ("AK", "Alaska"), "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"), "06": ("CA", "California"), "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"), "10": ("DE", "Delaware"), "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"), "13": ("GA", "Georgia"), "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"), "17": ("IL", "Illinois"), "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"), "20": ("KS", "Kansas"), "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"), "23": ("ME", "Maine"), "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"), "26": ("MI", "Michigan"), "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"), "29": ("MO", "Missouri"), "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"), "32": ("NV", "Nevada"), "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"), "35": ("NM", "New Mexico"), "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"), "38": ("ND", "North Dakota"), "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"), "41": ("OR", "Oregon"), "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"), "45": ("SC", "South Carolina"), "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"), "48": ("TX", "Texas"), "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"), "51": ("VA", "Virginia"), "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"), "55": ("WI", "Wisconsin"), "56": ("WY", "Wyoming"),
}
PR = {"72": ("PR", "Puerto Rico")}


# ---------------------------------------------------------------------------
def download(url: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / url.rsplit("/", 1)[-1]
    if not dest.exists():
        print(f"  downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cspm-prep)"})
        with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
            f.write(r.read())
    return dest


def read_zip(url: str) -> gpd.GeoDataFrame:
    return gpd.read_file(download(url)).to_crs(4269)


def load_counties(year: int, states: dict) -> gpd.GeoDataFrame:
    g = read_zip(BASE.format(year=year) + f"cb_{year}_us_county_500k.zip")
    g = g[g["STATEFP"].isin(states)].copy()
    name_col = "NAMELSAD" if "NAMELSAD" in g.columns else "NAME"
    g["NAME"] = g[name_col] + ", " + g["STATEFP"].map(lambda s: states[s][0])
    return g[["GEOID", "NAME", "STATEFP", "geometry"]]


def load_tracts(year: int, states: dict) -> gpd.GeoDataFrame:
    base = BASE.format(year=year)
    try:
        g = read_zip(base + f"cb_{year}_us_tract_500k.zip")
    except (urllib.error.HTTPError, urllib.error.URLError):
        print("  national tract file not available; downloading per state")
        g = pd.concat(
            [read_zip(base + f"cb_{year}_{fips}_tract_500k.zip") for fips in states],
            ignore_index=True,
        )
        g = gpd.GeoDataFrame(g, geometry="geometry", crs=4269)
    return g[g["STATEFP"].isin(states)].copy()


def queen_neighbors(g: gpd.GeoDataFrame) -> dict[str, list[str]]:
    """GEOID -> sorted list of GEOIDs whose polygons touch (edge or vertex)."""
    left = g[["GEOID", "geometry"]].reset_index(drop=True)
    pairs = gpd.sjoin(left, left, how="inner", predicate="intersects")
    pairs = pairs[pairs["GEOID_left"] != pairs["GEOID_right"]]
    out = pairs.groupby("GEOID_left")["GEOID_right"].apply(lambda s: sorted(set(s))).to_dict()
    return {gid: out.get(gid, []) for gid in g["GEOID"]}


def simplify(g: gpd.GeoDataFrame, tol: float) -> gpd.GeoDataFrame:
    g = g.copy()
    g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    return g[~g.geometry.is_empty]


def write_geojson(g: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gj = json.loads(g.to_crs(4326).to_json(drop_id=True))

    def rnd(c):  # 5 decimals ≈ 1 m
        return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 5), round(c[1], 5)]

    for f in gj["features"]:
        f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
    path.write_text(json.dumps(gj, separators=(",", ":")))


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2020, help="Census boundary vintage")
    ap.add_argument("--include-pr", action="store_true")
    args = ap.parse_args()

    states = {**STATES, **(PR if args.include_pr else {})}

    print("[1/4] Counties")
    counties = load_counties(args.year, states)
    county_names = counties.set_index("GEOID")["NAME"].to_dict()
    write_json(queen_neighbors(counties), OUT_DIR / "neighbors" / "county.json")
    write_geojson(simplify(counties, COUNTY_TOL), OUT_DIR / "counties.geojson")
    print(f"  {len(counties):,} counties")

    print("[2/4] States metadata")
    meta = {}
    for fips, (abbr, name) in states.items():
        b = counties.loc[counties["STATEFP"] == fips].total_bounds
        meta[fips] = {"abbr": abbr, "name": name, "bbox": [round(float(x), 4) for x in b]}
    write_json(meta, OUT_DIR / "states.json")

    print("[3/4] Tracts (adjacency computed nationally so cross-state neighbors are kept)")
    tracts = load_tracts(args.year, states)
    tract_nbrs = queen_neighbors(tracts)
    print(f"  {len(tracts):,} tracts")

    print("[4/4] Writing per-state tract files")
    tract_label = tracts["NAMELSAD"] if "NAMELSAD" in tracts.columns else "Tract " + tracts["NAME"]
    tracts["NAME"] = tract_label + ", " + tracts["GEOID"].str[:5].map(county_names).fillna("")
    tracts = tracts[["GEOID", "NAME", "STATEFP", "geometry"]]
    for fips in states:
        sub = tracts[tracts["STATEFP"] == fips]
        if sub.empty:
            continue
        write_geojson(simplify(sub, TRACT_TOL), OUT_DIR / "tracts" / f"{fips}.geojson")
        write_json({gid: tract_nbrs[gid] for gid in sub["GEOID"]},
                   OUT_DIR / "neighbors" / f"tract_{fips}.json")
        print(f"  {states[fips][0]}: {len(sub):,} tracts")

    # Lightweight name lookup for the scatterplot hover (all US tracts)
    write_json(tracts.set_index("GEOID")["NAME"].to_dict(), OUT_DIR / "tract_names.json")

    print(f"Done. Output in {OUT_DIR}")


if __name__ == "__main__":
    main()
