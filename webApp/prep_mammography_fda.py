"""
prep_mammography_fda.py — build data/geo/mammography.geojson from the FDA MQSA
certified mammography facility list.

Steps
  1. Download the weekly FDA file (public.zip) — or use a local copy via --input.
  2. Parse it (pipe-delimited or the documented fixed-width layout:
     Name 75 | Addr1 50 | Addr2 50 | Addr3 50 | City 50 | State 2 | Zip 15 | Phone 50 | Fax 50).
  3. Keep 50 states + DC (add --include-pr for Puerto Rico). Military APO/FPO
     (AA/AE/AP) and other territories are dropped because the app maps states + DC only.
  4. Geocode street addresses with the free US Census batch geocoder.
  5. Unmatched addresses fall back to the ZIP (ZCTA) internal point from the
     Census Gazetteer; GEOCODE = "address" or "zip_centroid".
  6. Write data/geo/mammography.geojson (+ a QA CSV of every facility and how it was located).

Geocoding results are cached in ~/.cache/cspm_census/mammo_geocode_cache.csv,
so weekly re-runs only geocode new or changed addresses.

Usage
    python prep_mammography_fda.py
    python prep_mammography_fda.py --input path/to/public.zip   # if fda.gov download is blocked
    python prep_mammography_fda.py --include-pr --chunk 1000

Pure standard library + pandas (no geopandas needed).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent
OUT_GEOJSON = APP_DIR / "data" / "geo" / "mammography.geojson"
OUT_QA = APP_DIR / "data" / "mammography_geocode_qa.csv"
CACHE_DIR = Path.home() / ".cache" / "cspm_census"
GEOCODE_CACHE = CACHE_DIR / "mammo_geocode_cache.csv"

FDA_URL = "https://www.accessdata.fda.gov/premarket/ftparea/public.zip"
CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
GAZ_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
           "{y}_Gazetteer/{y}_Gaz_zcta_national.zip")
UA = {"User-Agent": "Mozilla/5.0 (cspm-prep)"}

FIELDS = ["NAME", "ADDR1", "ADDR2", "ADDR3", "CITY", "STATE", "ZIP", "PHONE", "FAX"]
WIDTHS = [75, 50, 50, 50, 50, 2, 15, 50, 50]

STATES_DC = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def fetch(url: str, dest: Path | None = None, data: bytes | None = None,
          headers: dict | None = None, timeout: int = 600, retries: int = 3) -> bytes:
    h = {**UA, **(headers or {})}
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
            return body
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            print(f"    attempt {attempt}/{retries} failed: {e}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"Could not fetch {url}: {last}")


# ---------------------------------------------------------------------------
# 1–2. FDA file
# ---------------------------------------------------------------------------
def read_fda(path_or_none: str | None) -> pd.DataFrame:
    if path_or_none:
        raw = Path(path_or_none).read_bytes()
        print(f"[1/5] Using local file {path_or_none}")
    else:
        print(f"[1/5] Downloading {FDA_URL}")
        raw = fetch(FDA_URL, CACHE_DIR / "fda_mqsa_public.zip")

    if raw[:2] == b"PK":  # zip
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = [n for n in z.namelist() if not n.endswith("/")]
            name = max(members, key=lambda n: z.getinfo(n).file_size)
            raw = z.read(name)
            print(f"    extracted {name}")

    text = raw.decode("latin-1")
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]

    sample = lines[:200]
    half = len(sample) / 2
    if sum(ln.count("|") >= 5 for ln in sample) > half:
        rows = [ln.split("|") for ln in lines]
        rows = [(r + [""] * len(FIELDS))[: len(FIELDS)] for r in rows]
        df = pd.DataFrame(rows, columns=FIELDS)
        fmt = "pipe-delimited"
    elif sum(ln.count("\t") >= 5 for ln in sample) > half:
        rows = [(ln.split("\t") + [""] * len(FIELDS))[: len(FIELDS)] for ln in lines]
        df = pd.DataFrame(rows, columns=FIELDS)
        fmt = "tab-delimited"
    else:
        df = pd.read_fwf(io.StringIO("\n".join(lines)), widths=WIDTHS, names=FIELDS,
                         dtype=str, keep_default_na=False)
        fmt = "fixed-width"

    df = df.apply(lambda s: s.astype(str).str.strip())
    # Drop a header row if the file has one
    df = df[~df["NAME"].str.lower().isin({"facility name", "name", "facility"})]
    print(f"[2/5] Parsed {len(df):,} facility rows ({fmt})")
    return df.reset_index(drop=True)


def pick_street(r) -> str:
    """Prefer the first address line that starts with a house number (Addr1 is often a suite/building)."""
    lines = [r["ADDR1"], r["ADDR2"], r["ADDR3"]]
    for ln in lines:
        if re.match(r"^\d", ln):
            return ln
    return next((ln for ln in lines if ln), "")


def clean(df: pd.DataFrame, include_pr: bool) -> pd.DataFrame:
    keep = STATES_DC | ({"PR"} if include_pr else set())
    df = df.copy()
    df["STATE"] = df["STATE"].str.upper()
    dropped = df.loc[~df["STATE"].isin(keep), "STATE"].value_counts()
    df = df[df["STATE"].isin(keep)].copy()
    if df.empty:
        raise SystemExit("No facilities left after state filter — check the file format.")
    if len(dropped):
        print("    dropped (outside states+DC" + ("+PR" if include_pr else "") + "): "
              + ", ".join(f"{k}={v}" for k, v in dropped.items()))

    df["ZIP5"] = df["ZIP"].str.extract(r"(\d{5})", expand=False).fillna("")
    df["STREET"] = [pick_street(r) for _, r in df.iterrows()]
    df["ADDRESS"] = [", ".join(x for x in r if x)
                     for r in df[["ADDR1", "ADDR2", "ADDR3"]].itertuples(index=False)]
    df["PHONE"] = df["PHONE"].str.replace(r"\s+", " ", regex=True)

    before = len(df)
    df = df.drop_duplicates(["NAME", "STREET", "CITY", "STATE", "ZIP5"])
    if before - len(df):
        print(f"    removed {before - len(df):,} exact duplicate facilities")
    df["KEY"] = (df["STREET"] + "|" + df["CITY"] + "|" + df["STATE"] + "|" + df["ZIP5"]).str.upper()
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. Census batch geocoder (with cache)
# ---------------------------------------------------------------------------
def _multipart(fields: dict, file_field: str, filename: str, content: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
         f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n').encode()
        + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def census_batch(addr: pd.DataFrame) -> pd.DataFrame:
    """addr: columns id, STREET, CITY, STATE, ZIP5 → id, lon, lat, match_type."""
    buf = io.StringIO()
    addr[["id", "STREET", "CITY", "STATE", "ZIP5"]].to_csv(buf, index=False, header=False)
    body, headers = _multipart({"benchmark": "Public_AR_Current"}, "addressFile",
                               "addresses.csv", buf.getvalue().encode("utf-8"))
    resp = fetch(CENSUS_BATCH_URL, data=body, headers=headers, timeout=900).decode("utf-8", "replace")

    out = []
    for row in pd.read_csv(io.StringIO(resp), header=None, dtype=str,
                           keep_default_na=False).itertuples(index=False):
        rid, status = row[0], row[2] if len(row) > 2 else ""
        lon = lat = None
        mtype = ""
        if status == "Match" and len(row) > 5 and "," in row[5]:
            lon, lat = (float(v) for v in row[5].split(","))
            mtype = row[3]
        out.append({"id": rid, "lon": lon, "lat": lat, "match_type": mtype})
    return pd.DataFrame(out)


def geocode(df: pd.DataFrame, chunk: int) -> pd.DataFrame:
    cache = (pd.read_csv(GEOCODE_CACHE, dtype={"KEY": str}) if GEOCODE_CACHE.exists()
             else pd.DataFrame(columns=["KEY", "lon", "lat", "match_type"]))
    cache = cache.drop_duplicates("KEY", keep="last")
    todo = df.loc[~df["KEY"].isin(cache["KEY"]) & (df["STREET"] != ""),
                  ["KEY", "STREET", "CITY", "STATE", "ZIP5"]].drop_duplicates("KEY")
    print(f"[3/5] Census geocoding: {len(todo):,} new addresses "
          f"({len(cache):,} cached) in chunks of {chunk:,}")

    todo = todo.reset_index(drop=True)
    todo["id"] = todo.index.astype(str)
    new = []
    for start in range(0, len(todo), chunk):
        part = todo.iloc[start:start + chunk]
        t0 = time.time()
        res = census_batch(part).merge(part[["id", "KEY"]], on="id")
        new.append(res[["KEY", "lon", "lat", "match_type"]])
        n_ok = res["lon"].notna().sum()
        print(f"    rows {start + 1:,}-{start + len(part):,}: matched {n_ok:,}/{len(part):,} "
              f"({time.time() - t0:,.0f}s)")
        # save progress after every chunk so an interruption doesn't lose work
        frames = [f for f in (cache, new[-1]) if not f.empty]
        cache = pd.concat(frames, ignore_index=True).drop_duplicates("KEY", keep="last")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.to_csv(GEOCODE_CACHE, index=False)

    return df.merge(cache, on="KEY", how="left")


# ---------------------------------------------------------------------------
# 5. ZIP fallback (Census Gazetteer ZCTA internal points)
# ---------------------------------------------------------------------------
def zcta_points(year: int) -> pd.DataFrame:
    for y in (year, year - 1, year - 2):
        dest = CACHE_DIR / f"{y}_Gaz_zcta_national.zip"
        try:
            if not dest.exists():
                print(f"    downloading ZCTA gazetteer {y}")
                fetch(GAZ_URL.format(y=y), dest)
            with zipfile.ZipFile(dest) as z:
                g = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype={"GEOID": str})
            g.columns = g.columns.str.strip()
            return g.rename(columns={"GEOID": "ZIP5", "INTPTLAT": "zlat", "INTPTLONG": "zlon"})[
                ["ZIP5", "zlat", "zlon"]]
        except Exception as e:  # noqa: BLE001
            print(f"    gazetteer {y} unavailable ({e})")
    raise RuntimeError("No ZCTA gazetteer available for ZIP fallback")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local public.zip / .txt (skip the FDA download)")
    ap.add_argument("--include-pr", action="store_true")
    ap.add_argument("--chunk", type=int, default=2000, help="addresses per Census batch (max 10000)")
    ap.add_argument("--gazetteer-year", type=int, default=2024)
    args = ap.parse_args()

    df = clean(read_fda(args.input), args.include_pr)
    df = geocode(df, min(args.chunk, 10000))

    print("[4/5] ZIP-centroid fallback for unmatched addresses")
    miss = df["lon"].isna()
    df["GEOCODE"] = "address"
    if miss.any():
        z = zcta_points(args.gazetteer_year)
        df = df.merge(z, on="ZIP5", how="left")
        fill = df["lon"].isna() & df["zlon"].notna()
        df.loc[fill, "lon"] = df.loc[fill, "zlon"]
        df.loc[fill, "lat"] = df.loc[fill, "zlat"]
        df.loc[fill, "GEOCODE"] = "zip_centroid"
        df = df.drop(columns=["zlat", "zlon"])
    df.loc[df["lon"].isna(), "GEOCODE"] = "not_located"

    # QA report (all rows, including any that could not be located)
    OUT_QA.parent.mkdir(parents=True, exist_ok=True)
    df[["NAME", "ADDRESS", "STREET", "CITY", "STATE", "ZIP5", "PHONE",
        "GEOCODE", "match_type", "lon", "lat"]].to_csv(OUT_QA, index=False)

    ok = df[df["lon"].notna()]
    print("[5/5] Writing GeoJSON")
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(r.lon), 5), round(float(r.lat), 5)]},
            "properties": {
                "NAME": r.NAME,
                "ADDRESS": f"{r.ADDRESS}, {r.CITY}, {r.STATE} {r.ZIP5}".strip(", "),
                "PHONE": r.PHONE,
                "GEOCODE": r.GEOCODE,
            },
        }
        for r in ok.itertuples(index=False)
    ]
    OUT_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                      separators=(",", ":")))

    counts = df["GEOCODE"].value_counts()
    print(f"\nDone: {len(features):,} facilities -> {OUT_GEOJSON}")
    for k in ["address", "zip_centroid", "not_located"]:
        n = int(counts.get(k, 0))
        print(f"  {k:<13} {n:>6,}  ({n / max(len(df), 1):.1%})")
    print(f"  QA report: {OUT_QA}")


if __name__ == "__main__":
    main()
