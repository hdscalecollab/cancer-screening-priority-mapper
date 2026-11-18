"""
prep_state_lines.py — writes data/geo/state_lines.geojson (state borders as lines) for app.py.

Uses the Census cartographic boundary file for states (1:20M, small and detailed enough
at state and national zoom). Shared borders between neighboring states are merged into a
single line, so they aren't drawn twice (which would make them look darker than coastlines
when drawn semi-transparent).

Reuses the download / write helpers in prep_geodata.py, so run it from the project folder.

    python prep_state_lines.py
    python prep_state_lines.py --include-pr
    python prep_state_lines.py --year 2022
"""

from __future__ import annotations

import argparse

import geopandas as gpd
from shapely.ops import unary_union

import prep_geodata as P


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023, help="Census boundary vintage")
    ap.add_argument("--include-pr", action="store_true")
    args = ap.parse_args()

    states = {**P.STATES, **(P.PR if args.include_pr else {})}
    url = P.BASE.format(year=args.year) + f"cb_{args.year}_us_state_20m.zip"
    g = P.read_zip(url)
    g = g[g["STATEFP"].isin(states)]
    if g.empty:
        raise SystemExit("No states left after filtering — check the downloaded file.")

    # polygons -> border lines; unary_union dissolves the shared (duplicate) segments
    lines = unary_union(list(g.geometry.boundary))
    out_gdf = gpd.GeoDataFrame({"name": ["state borders"]}, geometry=[lines], crs=g.crs)

    out = P.OUT_DIR / "state_lines.geojson"
    P.write_geojson(out_gdf, out)
    size_kb = out.stat().st_size / 1024
    print(f"Wrote borders for {len(g)} states to {out} ({size_kb:,.0f} KB)")


if __name__ == "__main__":
    main()
