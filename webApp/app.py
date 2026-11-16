"""
Cancer Screening Priority Mapper
================================
Shiny for Python app (core API).

Layout: full-bleed map with a floating, collapsible control panel (left) and
four floating info boxes (top). Need-profile chart + scatterplot below the map.

Data: built by prep_mvp_data.py from the MVP classification outputs
(see data_dictionary.md). The app is driven by data/app/variables.json, so the
dropdowns, colors, number formats and info-box tables follow the catalog.

Run locally (VS Code terminal):
    shiny run --reload app.py

Deploy to shinyapps.io:
    rsconnect deploy shiny . --name <account> --title cancer-screening-priority-mapper

Inputs (relative to this file):
    data/app/tract_values.csv, county_values.csv, variables.json   (prep_mvp_data.py)
    data/geo/...                                                    (prep_geodata.py)
    data/geo/mammography.geojson          optional                  (prep_mammography_fda.py)
"""

from __future__ import annotations

import json
import os
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from shiny import App, reactive, render, ui

warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="All-NaN slice")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
APP_DATA = APP_DIR / "data" / "app"
GEO_DIR = APP_DIR / "data" / "geo"
MAMMO_PATH = GEO_DIR / "mammography.geojson"

APP_TITLE = "Cancer Screening Priority Mapper"
DEFAULT_STATE = "06"  # California
US_BBOX = [-125.0, 24.4, -66.9, 49.4]   # lower 48: initial county view (AK/HI reachable by panning)
AK_BBOX = [-170.0, 52.0, -130.0, 71.5]  # Alaska crosses the antimeridian

# Basemap: "carto-positron" (needs internet tiles) or "white-bg" (no basemap, works offline)
MAP_STYLE = os.environ.get("CSPM_MAP_STYLE", "carto-positron")

BOX_THEMES = [
    ui.value_box_theme(bg="#FB938F", fg="#2B1A1A"),  # Selected polygon
    ui.value_box_theme(bg="#F2CAC8", fg="#2B1A1A"),  # Adjacent
    ui.value_box_theme(bg="#C36BB5", fg="#FFFFFF"),  # County / State
    ui.value_box_theme(bg="#FDBB75", fg="#2B1A1A"),  # State / US
]
GROUP_COLORS = ["#E8615C", "#D9A5A2", "#C36BB5", "#F29A3A"]  # chart colors matching the boxes

SCATTER_POINT_COLOR = "#8C96A3"
TREND_COLOR = "#3B4A5A"
SELECTED_COLOR = "#E8615C"
MAMMO_COLOR = "#1F6FB2"

CLICK_HINT = "Click a polygon on the map"
PLOT_FONT = dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=12)
GROUP_ORDER = ["Priority", "Access", "Barriers", "County burden", "Population", "Context"]
MAX_TABLE_ROWS = 8


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
CATALOG: dict = json.loads((APP_DATA / "variables.json").read_text())
META: dict = CATALOG["meta"]
VARMETA: dict[str, dict[str, dict]] = {
    lvl: {d["id"]: d for d in CATALOG[lvl]} for lvl in ("county", "tract")
}


def _read(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"GEOID": str, "FIPS": str, "burden_FIPS": str},
                     keep_default_na=True)
    width = 11 if "tract" in path.name else 5
    df["GEOID"] = df["GEOID"].str.zfill(width)
    return df.set_index("GEOID")


DF = {"county": _read(APP_DATA / "county_values.csv"),
      "tract": _read(APP_DATA / "tract_values.csv")}


def _positions(keys: np.ndarray) -> dict:
    return pd.Series(np.arange(len(keys))).groupby(keys).apply(np.asarray).to_dict()


_tidx = DF["tract"].index
_cidx = DF["county"].index
GROUP_IDX = {
    "tract_by_county": _positions(_tidx.str[:5].values),
    "tract_by_state": _positions(_tidx.str[:2].values),
    "county_by_state": _positions(_cidx.str[:2].values),
}

with open(GEO_DIR / "states.json") as f:
    STATES: dict = json.load(f)

STATE_CHOICES = {
    fips: meta["name"] for fips, meta in sorted(STATES.items(), key=lambda kv: kv[1]["name"])
}


def _index_features(gj: dict) -> dict:
    return {f["properties"]["GEOID"]: f for f in gj["features"]}


@lru_cache(maxsize=1)
def load_counties():
    with open(GEO_DIR / "counties.geojson") as f:
        gj = json.load(f)
    return gj, _index_features(gj)


@lru_cache(maxsize=6)
def load_tracts(state_fips: str):
    path = GEO_DIR / "tracts" / f"{state_fips}.geojson"
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}, {}
    with open(path) as f:
        gj = json.load(f)
    return gj, _index_features(gj)


@lru_cache(maxsize=1)
def county_names() -> dict:
    _, feats = load_counties()
    names = {gid: f["properties"].get("NAME", gid) for gid, f in feats.items()}
    if "NAME" in DF["county"]:
        for gid, n in DF["county"]["NAME"].dropna().items():
            names.setdefault(gid, n)
    return names


@lru_cache(maxsize=1)
def tract_names() -> dict:
    path = GEO_DIR / "tract_names.json"
    names = json.loads(path.read_text()) if path.exists() else {}
    t = DF["tract"]
    if {"county", "state"} <= set(t.columns):
        for gid, c, s in zip(t.index, t["county"], t["state"]):
            names.setdefault(gid, f"Tract {gid[5:]}, {c}, {s}")
    return names


@lru_cache(maxsize=1)
def load_county_neighbors() -> dict:
    with open(GEO_DIR / "neighbors" / "county.json") as f:
        return json.load(f)


@lru_cache(maxsize=6)
def load_tract_neighbors(state_fips: str) -> dict:
    path = GEO_DIR / "neighbors" / f"tract_{state_fips}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_mammo() -> dict | None:
    if not MAMMO_PATH.exists():
        return None
    gj = json.loads(MAMMO_PATH.read_text())
    lon, lat, text = [], [], []
    for feat in gj["features"]:
        x, y = feat["geometry"]["coordinates"][:2]
        p = feat["properties"]
        extra = [str(p[k]) for k in ("ADDRESS", "PHONE") if p.get(k)]
        if p.get("GEOCODE") == "zip_centroid":
            extra.append("<i>approx. location (ZIP centroid)</i>")
        lon.append(x)
        lat.append(y)
        text.append(f"<b>{p.get('NAME', 'Mammography facility')}</b>"
                    + ("<br>" + "<br>".join(extra) if extra else ""))
    return {"lon": lon, "lat": lat, "text": text}


def place_name(level: str, gid: str) -> str:
    names = county_names() if level == "county" else tract_names()
    return names.get(gid, f"GEOID {gid}")


def state_label(state_fips: str, short: bool = False) -> str:
    meta = STATES.get(state_fips, {})
    return meta.get("abbr" if short else "name", state_fips)


def bbox_for_state(state_fips: str) -> list[float]:
    return AK_BBOX if state_fips == "02" else STATES[state_fips]["bbox"]


# ---------------------------------------------------------------------------
# Variable helpers (catalog-driven)
# ---------------------------------------------------------------------------
def var_choices(level: str, numeric_only: bool = False) -> dict:
    """Grouped choices for ui.input_select: {group: {id: label}}."""
    groups: dict[str, dict] = {}
    for d in CATALOG[level]:
        if numeric_only and d["type"] == "nominal":
            continue
        groups.setdefault(d["group"], {})[d["id"]] = d["label"]
    ordered = {g: groups[g] for g in GROUP_ORDER if g in groups}
    ordered.update({g: c for g, c in groups.items() if g not in ordered})
    return ordered


def valid(level: str, var: str | None, key: str) -> str:
    """Return var if it exists at this level, else the catalog default."""
    if var in VARMETA[level]:
        return var
    d = META.get("defaults", {}).get(level, {}).get(key)
    return d if d in VARMETA[level] else CATALOG[level][0]["id"]


def short_label(level: str, var: str) -> str:
    m = VARMETA[level][var]
    return m.get("short") or m["label"]


def table_vars(level: str, var: str) -> list[str]:
    """Rows listed in the info boxes when `var` is selected.

    Uses the catalog's explicit "table" list when present (e.g. the four barrier
    tertiles + barrier score); otherwise variables of the same group and format.
    """
    m = VARMETA[level][var]
    if m.get("table"):
        ids = [v for v in m["table"] if v in VARMETA[level]]
    else:
        ids = [d["id"] for d in CATALOG[level] if d["group"] == m["group"] and d["fmt"] == m["fmt"]]
        if len(ids) < 3:
            ids = [d["id"] for d in CATALOG[level] if d["group"] == m["group"]]
    if var not in ids:
        ids = [var] + ids
    return ids[:MAX_TABLE_ROWS]


def fmt_value(m: dict, v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "—"
    if m["type"] == "nominal":
        return str(v)
    f = m["fmt"]
    if m["type"] == "ordinal":
        vals, labels = m["levels"]["values"], m["levels"]["labels"]
        if float(v).is_integer() and int(v) in vals:
            return labels[vals.index(int(v))]
        lo, hi = int(np.floor(v)), int(np.ceil(v))   # median between two levels
        if lo in vals and hi in vals:
            return f"{labels[vals.index(lo)]}–{labels[vals.index(hi)]}"
        return f"{v:.1f}"
    return {
        "pct": f"{v:,.1f}%",
        "pctl": f"{v:,.0f}",
        "rate": f"{v:,.1f}",
        "count": f"{v:,.0f}",
        "count1": f"{v:,.1f}",
        "index": f"{v:+.3f}",
        "score": f"{v:,.1f}" if not float(v).is_integer() else f"{v:,.0f}",
    }.get(f, f"{v:,.2f}")


def pct_top(m: dict, s: pd.Series) -> float | None:
    """% of non-missing values in the top ordinal level (e.g. % High tertile)."""
    s = s.dropna()
    if not len(s):
        return None
    return float((s == m["levels"]["values"][-1]).mean() * 100)


def summarize(level: str, rows: pd.DataFrame, var: str) -> dict:
    """Group summary: median for numeric/ordinal, mode + share for nominal."""
    m = VARMETA[level][var]
    s = rows[var]
    if m["type"] == "nominal":
        vc = s.dropna().value_counts()
        n = int(vc.sum())
        if not n:
            return {"text": "—", "n": 0, "dist": None}
        top = vc.index[0]
        return {"text": str(top), "share": vc.iloc[0] / n * 100, "n": n, "dist": vc}
    med = s.median()
    out = {"text": fmt_value(m, med), "value": med, "n": int(s.notna().sum())}
    if m["type"] == "ordinal":
        out["pct_top"] = pct_top(m, s)
    return out


def cell_text(level: str, rows: pd.DataFrame | pd.Series, var: str, single: bool) -> str:
    m = VARMETA[level][var]
    if single:
        return fmt_value(m, rows.get(var))
    s = summarize(level, rows, var)
    if m["type"] == "nominal" and s["n"]:
        return f"{s['text']} ({s['share']:.0f}%)"
    if m["type"] == "ordinal" and s.get("pct_top") is not None:
        return f"{s['pct_top']:.0f}% {m['levels']['labels'][-1]}"
    return s["text"]


def discrete_scale(colors: list[str]) -> list:
    k = len(colors)
    scale = []
    for i, c in enumerate(colors):
        scale += [[i / k, c], [(i + 1) / k, c]]
    return scale


def _fig_payload(fig: go.Figure) -> dict:
    return json.loads(fig.to_json())


# ---------------------------------------------------------------------------
# Browser-side bridge
# ---------------------------------------------------------------------------
PLOTLY_JS_DIR = Path(plotly.__file__).resolve().parent / "package_data"

BRIDGE_JS = """
(function () {
  const CFG = { responsive: true, displaylogo: false };

  function watchResize(el) {
    if (el._cspmRO) return;
    el._cspmRO = new ResizeObserver(() => Plotly.Plots.resize(el));
    el._cspmRO.observe(el);
  }
  function isFloating(el) {
    return el && getComputedStyle(el).position === "absolute" && el.offsetParent !== null;
  }
  // Fit [minx, miny, maxx, maxy] into the part of the map not covered by the panel / boxes
  function fitView(map, bbox, el) {
    const panel = document.getElementById("ctrl_panel");
    const boxes = document.getElementById("float_boxes");
    const hero = document.getElementById("hero");
    const panelOn = isFloating(panel) && !hero.classList.contains("panel-collapsed");
    const padL = panelOn ? panel.offsetLeft + panel.offsetWidth : 0;
    const padT = isFloating(boxes) ? boxes.offsetTop + boxes.offsetHeight : 0;
    const PAD = 28;
    const W = Math.max(el.clientWidth - padL - 2 * PAD, 200);
    const H = Math.max(el.clientHeight - padT - 2 * PAD, 150);
    const rad = Math.PI / 180;
    const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * rad / 2));
    const [minx, miny, maxx, maxy] = bbox;
    const zx = Math.log2(W * 360 / (512 * Math.max(maxx - minx, 1e-4)));
    const zy = Math.log2(H * 2 * Math.PI / (512 * Math.max(merc(maxy) - merc(miny), 1e-6)));
    const zoom = Math.max(1, Math.min(zx, zy, 12));
    const center = { lon: (minx + maxx) / 2,
                     lat: (2 * Math.atan(Math.exp((merc(miny) + merc(maxy)) / 2)) - Math.PI / 2) / rad };
    return offsetCenter(Object.assign({}, map, { zoom: zoom, center: center }), padL / 2, padT / 2);
  }
  function offsetCenter(map, dx, dy) {
    if (!dx && !dy) return map;
    const world = 512 * Math.pow(2, map.zoom);
    const rad = Math.PI / 180;
    const x = (map.center.lon + 180) / 360 * world - dx;
    const yMerc = Math.log(Math.tan(Math.PI / 4 + map.center.lat * rad / 2));
    const y = (1 - yMerc / Math.PI) / 2 * world - dy;
    const lat = (2 * Math.atan(Math.exp((1 - 2 * y / world) * Math.PI)) - Math.PI / 2) / rad;
    return Object.assign({}, map, { center: { lon: x / world * 360 - 180, lat: lat } });
  }

  // ---- Map ----
  // 1) All map messages run one after another (no overlapping draws).
  // 2) Each one waits until the basemap style has loaded before touching layers.
  // 3) The click listener is re-attached after every draw, even if a draw failed.
  let mapQueue = Promise.resolve();
  function enqueue(fn) {
    mapQueue = mapQueue.then(fn).catch((err) => console.error("[map]", err));
  }

  // Resolves when MapLibre has finished loading the style (or after a timeout).
  function mapReady(el, timeoutMs = 8000) {
    const sub = el && el._fullLayout && el._fullLayout.map && el._fullLayout.map._subplot;
    const m = sub && sub.map;
    if (!m || (m.isStyleLoaded && m.isStyleLoaded())) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => { clearTimeout(t); resolve(); };
      const t = setTimeout(done, timeoutMs);      // don't hang if tiles are blocked
      m.once("idle", done);
    });
  }

  function bindClick(el) {
    if (!el.on) return;
    if (el.removeAllListeners) el.removeAllListeners("plotly_click");
    el.on("plotly_click", function (ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      const p = ev.points[0];
      if (p.curveNumber !== 0) return;  // ignore highlight + facility layers
      Shiny.setInputValue("map_click", { geoid: p.location, t: Date.now() },
                          { priority: "event" });
    });
  }

  function drawMap(el, fig) {
    return Plotly.react(el, fig.data, fig.layout, Object.assign({ scrollZoom: true }, CFG));
  }

  Shiny.addCustomMessageHandler("draw_map", function (msg) {
    enqueue(async function () {
      const el = document.getElementById("screen_map");
      const fig = msg.fig;
      if (msg.bbox && fig.layout.map) fig.layout.map = fitView(fig.layout.map, msg.bbox, el);
      await mapReady(el);                          // previous style finished loading
      try {
        await drawMap(el, fig);
      } catch (err) {                              // style still loading: wait, then retry once
        console.warn("[map] draw retry:", err.message);
        await mapReady(el);
        await drawMap(el, fig);
      } finally {
        watchResize(el);
        bindClick(el);                             // always re-attach, even after an error
      }
      await mapReady(el);                          // let later restyles see a loaded style
    });
  });

  Shiny.addCustomMessageHandler("highlight", function (msg) {
    enqueue(async function () {
      const el = document.getElementById("screen_map");
      if (!el || !el.data || el.data.length < 2) return;
      const cur = el.data[1].locations || [];
      if (!cur.length && !msg.locations.length) return;   // nothing to change (initial load)
      await mapReady(el);
      await Plotly.restyle(el, { geojson: [msg.geojson], locations: [msg.locations], z: [msg.z] }, [1]);
    });
  });

  Shiny.addCustomMessageHandler("toggle_trace", function (msg) {
    enqueue(async function () {
      const el = document.getElementById("screen_map");
      if (!el || !el.data || el.data.length <= msg.index) return;
      await mapReady(el);
      await Plotly.restyle(el, { visible: msg.visible }, [msg.index]);
    });
  });

  // ---- Charts ----
  Shiny.addCustomMessageHandler("draw_chart", function (msg) {
    const el = document.getElementById(msg.id);
    if (!el) return;
    Plotly.react(el, msg.fig.data, msg.fig.layout, CFG).then(() => watchResize(el));
  });

  // ---- Collapsible control panel ----
  document.addEventListener("click", function (ev) {
    if (ev.target.closest(".panel-toggle")) {
      document.getElementById("hero").classList.toggle("panel-collapsed");
    }
  });
})();
"""

APP_CSS = """
:root { --titlebar-h: 52px; --gap: 16px; --panel-w: 300px; }
body { background: #f4f5f7; overflow-x: hidden; }
.container-fluid.app-root { padding: 0; }

.app-titlebar {
  position: sticky; top: 0; z-index: 1000; height: var(--titlebar-h);
  display: flex; align-items: center; gap: 14px; padding: 0 20px;
  background: #fff; border-bottom: 1px solid #e5e7eb; font-size: 1.25rem; font-weight: 500;
}
.app-titlebar .period { font-size: 0.78rem; color: #6b7280; font-weight: 400; }
.app-titlebar .qa-flag { font-size: 0.75rem; font-weight: 500; color: #8a1c1c; background: #fde2e1;
                         border-radius: 999px; padding: 2px 10px; }

.hero { position: relative; height: calc(100vh - var(--titlebar-h)); min-height: 640px; overflow: hidden; }
.map-bg { position: absolute; inset: 0; }

.glass {
  background: rgba(255, 255, 255, 0.86);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 14px; box-shadow: 0 8px 28px rgba(20, 30, 50, 0.18);
}
.float-panel {
  position: absolute; z-index: 20; top: var(--gap); left: var(--gap);
  width: var(--panel-w); max-height: calc(100% - 2 * var(--gap));
  overflow-y: auto; padding: 12px 18px 14px;
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.float-panel .form-group, .float-panel .shiny-input-container { margin-bottom: 0.6rem; width: 100%; }
.float-panel label { font-size: 0.85rem; margin-bottom: 0.2rem; }
.float-panel .form-select { font-size: 0.88rem; padding-top: 0.3rem; padding-bottom: 0.3rem; }
.float-panel hr { margin: 0.6rem 0; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; }
.sb-head { font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em;
           color: #6b7280; margin: 0.2rem 0 0.4rem; }
.panel-note { font-size: 0.76rem; color: #4b5563; }
.panel-note p { margin-bottom: 0.3rem; }
.var-desc { font-size: 0.76rem; color: #4b5563; margin: -0.3rem 0 0.5rem; }

.panel-toggle { border: none; background: transparent; font-size: 1.1rem; line-height: 1;
                padding: 4px 8px; border-radius: 8px; color: #374151; cursor: pointer; }
.panel-toggle:hover { background: rgba(0, 0, 0, 0.06); }
.panel-open-btn { position: absolute; z-index: 21; top: var(--gap); left: var(--gap);
                  padding: 8px 14px; font-size: 0.9rem; display: none; }
.hero.panel-collapsed .float-panel { transform: translateX(calc(-100% - 2 * var(--gap))); opacity: 0; pointer-events: none; }
.hero.panel-collapsed .panel-open-btn { display: block; }

.float-boxes {
  position: absolute; z-index: 15; top: var(--gap); right: var(--gap);
  left: calc(var(--panel-w) + 2 * var(--gap));
  pointer-events: none; transition: left 0.25s ease;
}
.hero.panel-collapsed .float-boxes { left: calc(var(--gap) + 130px); }
.float-boxes .bslib-grid { gap: 12px; }
.float-boxes .bslib-value-box {
  pointer-events: auto; margin-bottom: 0; height: 100%;
  border: none; border-radius: 14px; box-shadow: 0 8px 24px rgba(20, 30, 50, 0.20); opacity: 0.96;
}
#box1, #box2, #box3, #box4 { height: 100%; }
.float-boxes .value-box-area { padding: 0.7rem 1rem !important; }
.float-boxes .value-box-title { font-size: 0.85rem; margin-bottom: 0.1rem; }
.bslib-value-box .value-box-value { font-size: 1.55rem; line-height: 1.15; margin-bottom: 0.15rem; }
.bslib-value-box .value-box-value .txt { font-size: 1.05rem; font-weight: 600; line-height: 1.25; display: block; }
.vb-sub   { font-size: 0.72rem; opacity: 0.9; margin-bottom: 0.15rem; line-height: 1.25; }
.vb-table { font-size: 0.70rem; width: 100%; }
.vb-table td { padding: 0 0.25rem 0 0; vertical-align: top; }
.vb-table td.num { text-align: right; white-space: nowrap; }
.vb-table tr.sel { font-weight: 700; }
.vb-table tr.total td { border-top: 1px solid currentColor; padding-top: 1px; }
.chip { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px;
        border: 1px solid rgba(0,0,0,0.15); }

@media (min-width: 992px) {
  #screen_map .modebar-container { top: auto !important; bottom: var(--gap);
                                   right: auto !important; left: calc(var(--panel-w) + 2 * var(--gap)); }
}
.scroll-hint {
  position: absolute; z-index: 15; bottom: var(--gap); left: 50%; transform: translateX(-50%);
  padding: 6px 14px; font-size: 0.82rem; color: #374151; text-decoration: none;
}
.scroll-hint:hover { color: #111827; }

.charts-section { padding: 20px var(--gap) 28px; }
.charts-section .card { border-radius: 14px; box-shadow: 0 2px 10px rgba(20, 30, 50, 0.08); }
.card.bslib-full-screen .plot-host { height: 100% !important; }

@media (max-width: 991.98px) {
  .hero { height: auto; min-height: 0; overflow: visible; display: flex; flex-direction: column; }
  .map-bg { position: relative; inset: auto; height: 65vh; order: 2; }
  .float-panel, .float-boxes { position: static; width: auto; max-height: none; margin: var(--gap); }
  .float-panel { order: 1; }
  .float-boxes { order: 3; pointer-events: auto; }
  .panel-toggle, .panel-open-btn, .scroll-hint { display: none !important; }
  .hero.panel-collapsed .float-panel { transform: none; opacity: 1; pointer-events: auto; }
}

/* ---- Section navigation ---- */
html { scroll-behavior: smooth; }
#hero, #charts { scroll-margin-top: var(--titlebar-h); }   /* don't hide under the sticky title bar */
.charts-nav { display: flex; justify-content: center; margin: -4px 0 14px; }
.nav-pill { display: inline-block; padding: 6px 14px; font-size: 0.82rem;
            color: #374151; text-decoration: none; }
.nav-pill:hover { color: #111827; }
"""


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
DEFAULT_LEVEL = "county"
QA_FAILED = META.get("qa_failed") or []

control_panel = ui.div(
    ui.div(
        ui.div("Map", class_="sb-head"),
        ui.tags.button("«", class_="panel-toggle", title="Hide panel", type="button"),
        class_="panel-head",
    ),
    ui.input_select("level", "Map level", {"county": "County", "tract": "Census tract"},
                    selected=DEFAULT_LEVEL),
    ui.panel_conditional(
        "input.level === 'tract'",
        ui.input_select("state", "State (tract view)", STATE_CHOICES, selected=DEFAULT_STATE),
    ),
    ui.input_select("var", "Variable", var_choices(DEFAULT_LEVEL),
                    selected=valid(DEFAULT_LEVEL, None, "var")),
    ui.output_ui("var_desc"),
    (ui.input_checkbox("show_mammo", "Show mammography locations", value=False)
     if MAMMO_PATH.exists() else None),
    ui.hr(),
    ui.div("Scatterplot", class_="sb-head"),
    ui.input_select("sx", "X-axis variable", var_choices(DEFAULT_LEVEL, numeric_only=True),
                    selected=valid(DEFAULT_LEVEL, None, "x")),
    ui.input_select("sy", "Y-axis variable", var_choices(DEFAULT_LEVEL, numeric_only=True),
                    selected=valid(DEFAULT_LEVEL, None, "y")),
    ui.input_checkbox("trend", "Show trend line", value=True),
    ui.hr(),
    ui.div(
        ui.markdown(
            "Click a polygon to update the boxes and charts. Percentiles: 0 = lowest, "
            "100 = highest; higher tertile = **more need**. "
            "Burden is county-level context, the same for every tract in a county."
        ),
        ui.markdown(
            "County view: tract indicators are shown as the **median tract** in the county; "
            "priority and barrier tertiles are the **% of tracts in the High group**."
        ),
        class_="panel-note",
    ),
    id="ctrl_panel",
    class_="float-panel glass",
)

app_ui = ui.page_fluid(
    ui.head_content(
        ui.tags.title(APP_TITLE),
        ui.tags.script(src="plotlyjs/plotly.min.js"),
        ui.tags.style(APP_CSS),
    ),
    ui.div(
        APP_TITLE,
        ui.span(f"ACS {META.get('acs_period', '')} · Cancer burden 2019-2023", class_="period"),
        (ui.span(f"⚠ Data QA: {', '.join(QA_FAILED)} check(s) failed — see prep_qa.txt",
                 class_="qa-flag") if QA_FAILED else None),
        class_="app-titlebar",
    ),
    ui.div(
        ui.div(id="screen_map", class_="map-bg"),
        control_panel,
        ui.tags.button("☰  Controls", class_="panel-toggle panel-open-btn glass", type="button"),
        ui.div(
            ui.layout_columns(
                ui.output_ui("box1"), ui.output_ui("box2"),
                ui.output_ui("box3"), ui.output_ui("box4"),
                col_widths={"sm": [6, 6, 6, 6], "xl": [3, 3, 3, 3]},
                fill=False,
            ),
            id="float_boxes",
            class_="float-boxes",
        ),
        ui.tags.a("▼  Profile & comparison", href="#charts", class_="scroll-hint glass"),
        id="hero",
        class_="hero",
    ),
    ui.div(
        ui.div(ui.tags.a("▲  Map & distribution", href="#hero", class_="nav-pill glass"),
               class_="charts-nav"),
        ui.layout_columns(
            ui.card(
                ui.card_header(ui.output_text("profile_title", inline=True)),
                ui.div(id="profile", class_="plot-host", style="height:400px;"),
                full_screen=True,
            ),
            ui.card(
                ui.card_header(ui.output_text("scatter_title", inline=True)),
                ui.div(id="scatter", class_="plot-host", style="height:400px;"),
                full_screen=True,
            ),
            col_widths={"sm": [12, 12], "lg": [6, 6]},
            fill=False,
        ),
        id="charts",
        class_="charts-section",
    ),
    ui.tags.script(BRIDGE_JS),
    class_="app-root",
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def server(input, output, session):
    selected = reactive.value(None)  # GEOID of clicked polygon

    @reactive.calc
    def cur_var():
        return valid(input.level(), input.var(), "var")

    @reactive.calc
    def cur_x():
        return valid(input.level(), input.sx(), "x")

    @reactive.calc
    def cur_y():
        return valid(input.level(), input.sy(), "y")

    @reactive.effect
    @reactive.event(input.level, ignore_init=True)
    def _swap_choices():
        level = input.level()
        with reactive.isolate():
            v, x, y = input.var(), input.sx(), input.sy()
        ui.update_select("var", choices=var_choices(level), selected=valid(level, v, "var"))
        ui.update_select("sx", choices=var_choices(level, True), selected=valid(level, x, "x"))
        ui.update_select("sy", choices=var_choices(level, True), selected=valid(level, y, "y"))

    @reactive.effect
    @reactive.event(input.level, input.state)
    def _reset_selection():
        selected.set(None)

    @reactive.effect
    @reactive.event(input.map_click)
    def _on_click():
        click = input.map_click()
        if click and click.get("geoid"):
            selected.set(str(click["geoid"]))

    def _geo():
        if input.level() == "county":
            return load_counties()
        return load_tracts(input.state())

    def _highlight_payload(feats: dict, gid: str | None) -> dict:
        ok = bool(gid) and gid in feats
        return {
            "geojson": {"type": "FeatureCollection", "features": [feats[gid]] if ok else []},
            "locations": [gid] if ok else [],
            "z": [1] if ok else [],
        }

    @render.ui
    def var_desc():
        m = VARMETA[input.level()][cur_var()]
        return ui.div(m["description"], class_="var-desc") if m.get("description") else None

    # =======================================================================
    # Comparison groups — rows for each box (single row for box 1)
    # =======================================================================
    @reactive.calc
    def groups() -> list[dict]:
        level = input.level()
        gid = selected()
        data = DF[level]
        unit = "county" if level == "county" else "tract"
        units = "counties" if level == "county" else "tracts"

        def g(title, sub=CLICK_HINT, legend="", rows=None, single=False):
            return {"title": title, "sub": sub, "legend": legend, "rows": rows, "single": single}

        def take(key, k):
            idx = GROUP_IDX[key].get(k)
            return data.iloc[idx] if idx is not None else None

        g1 = g(f"Selected {unit}", legend=f"Selected {unit}", single=True)
        g2 = g(f"Adjacent {units}", legend=f"Adjacent {units}")
        if level == "county":
            g3 = g("State (all counties)", legend="State")
            g4 = g("United States (all counties)", "All US counties", "US", data)
        else:
            st = input.state()
            g3 = g("County (all tracts)", legend="County")
            g4 = g("State (all tracts)", f"All tracts in {state_label(st)}",
                   f"{state_label(st, short=True)}", take("tract_by_state", st))

        if not gid:
            return [g1, g2, g3, g4]

        name = place_name(level, gid)
        if gid in data.index:
            g1.update(sub=f"{name} (GEOID {gid})", legend=name, rows=data.loc[gid])
        else:
            g1.update(sub=f"{name} (GEOID {gid}) — no data", legend=name)

        nbrs = (load_county_neighbors() if level == "county"
                else load_tract_neighbors(gid[:2])).get(gid, [])
        nb = data.loc[data.index.intersection(nbrs)]
        g2.update(sub=f"{len(nbrs)} adjacent {units} of {name}", rows=nb if len(nb) else None)

        if level == "county":
            st = gid[:2]
            g3.update(sub=f"All counties in {state_label(st)}",
                      legend=state_label(st, short=True), rows=take("county_by_state", st))
        else:
            cty, st = gid[:5], gid[:2]
            g3.update(sub=f"All tracts in {place_name('county', cty)}",
                      legend=place_name("county", cty), rows=take("tract_by_county", cty))
            g4.update(sub=f"All tracts in {state_label(st)}",
                      legend=state_label(st, short=True), rows=take("tract_by_state", st))
        return [g1, g2, g3, g4]

    # =======================================================================
    # Map
    # =======================================================================
    @reactive.effect
    async def _draw_map():
        level, var = input.level(), cur_var()
        state = input.state()
        gj, feats = _geo()
        m = VARMETA[level][var]

        ids = [f["properties"]["GEOID"] for f in gj["features"]]
        names = [f["properties"].get("NAME", f["properties"]["GEOID"]) for f in gj["features"]]
        vals = DF[level][var].reindex(ids)
        text = [fmt_value(m, v) for v in vals]

        cb = dict(thickness=12, len=0.40, lenmode="fraction",
                  x=0.995, xanchor="right", y=0.06, yanchor="bottom",
                  bgcolor="rgba(255,255,255,0.85)", outlinewidth=0, xpad=10, ypad=10)
        title = m["label"].replace(" (", "<br>(", 1)

        if m["type"] in ("nominal", "ordinal"):
            if m["type"] == "nominal":
                labels = m["levels"]
                pos = {lv: i for i, lv in enumerate(labels)}
                z = [pos.get(v) if isinstance(v, str) else None for v in vals]
            else:
                lv, labels = m["levels"]["values"], m["levels"]["labels"]
                z = [lv.index(int(v)) if pd.notna(v) and float(v).is_integer() and int(v) in lv
                     else None for v in vals]
            k = len(labels)
            choro = dict(z=z, zmin=-0.5, zmax=k - 0.5, colorscale=discrete_scale(m["colors"]),
                         colorbar=dict(**cb, title=dict(text=title, side="top"),
                                       tickvals=list(range(k)), ticktext=labels))
        else:
            z = [None if pd.isna(v) else float(v) for v in vals]
            choro = dict(z=z, colorscale=m.get("colorscale", "YlOrRd"),
                         reversescale=bool(m.get("reverse")),
                         colorbar=dict(**cb, title=dict(text=title, side="top")))
            if m["fmt"] == "pctl":
                choro.update(zmin=0, zmax=100)
            elif m["fmt"] == "index":
                lim = float(np.nanmax(np.abs(DF[level][var]))) or 1.0
                choro.update(zmin=-lim, zmax=lim)

        with reactive.isolate():
            hl = _highlight_payload(feats, selected())
            show_mammo = bool(MAMMO_PATH.exists() and input.show_mammo())

        fig = go.Figure(
            data=[
                go.Choroplethmap(
                    geojson=gj, featureidkey="properties.GEOID", locations=ids,
                    customdata=list(zip(names, text)),
                    marker_line_width=0.25 if level == "county" else 0.35,
                    marker_line_color="white", marker_opacity=0.85,
                    hovertemplate="%{customdata[0]}<br>" + m["label"]
                                  + ": <b>%{customdata[1]}</b><extra></extra>",
                    **choro,
                ),
                go.Choroplethmap(
                    geojson=hl["geojson"], featureidkey="properties.GEOID",
                    locations=hl["locations"], z=hl["z"],
                    colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
                    showscale=False, marker_line_width=3, marker_line_color="#0b3d91",
                    hoverinfo="skip",
                ),
            ]
        )
        mammo = load_mammo()
        if mammo:  # trace index 2
            fig.add_trace(go.Scattermap(
                lon=mammo["lon"], lat=mammo["lat"], text=mammo["text"], mode="markers",
                name="Mammography facility", marker=dict(size=7, color=MAMMO_COLOR, opacity=0.9),
                hovertemplate="%{text}<extra></extra>", visible=show_mammo, showlegend=False,
            ))
        fig.update_layout(
            map=dict(style=MAP_STYLE, center=dict(lat=38.5, lon=-96.5), zoom=3),
            margin=dict(l=0, r=0, t=0, b=0), font=PLOT_FONT, paper_bgcolor="#e9ecef",
            uirevision=f"{level}-{state if level == 'tract' else 'us'}",
        )
        bbox = US_BBOX if level == "county" else bbox_for_state(state)
        await session.send_custom_message("draw_map", {"fig": _fig_payload(fig), "bbox": bbox})

    @reactive.effect
    async def _update_highlight():
        gid = selected()
        with reactive.isolate():
            _, feats = _geo()
        await session.send_custom_message("highlight", _highlight_payload(feats, gid))

    if MAMMO_PATH.exists():
        @reactive.effect
        @reactive.event(input.show_mammo, ignore_init=True)
        async def _toggle_mammo():
            await session.send_custom_message(
                "toggle_trace", {"index": 2, "visible": bool(input.show_mammo())})

    # =======================================================================
    # Info boxes
    # =======================================================================
    def _make_box(d: dict, theme):
        level, var = input.level(), cur_var()
        m = VARMETA[level][var]
        rows, single = d["rows"], d["single"]
        units = "counties" if level == "county" else "tracts"

        if rows is None:
            return ui.value_box(d["title"], "—", ui.div(d["sub"], class_="vb-sub"),
                                theme=theme, fill=False)

        children = [ui.div(d["sub"], class_="vb-sub")]
        if single:
            main = fmt_value(m, rows.get(var))
        else:
            s = summarize(level, rows, var)
            main = s["text"]
            if m["type"] == "nominal":
                if s["n"]:
                    children.append(ui.div(
                        f"Most common: {s['share']:.0f}% of {s['n']:,} {units}", class_="vb-sub"))
            elif m["type"] == "ordinal" and s.get("pct_top") is not None:
                children.append(ui.div(
                    f"median · n = {s['n']:,} · {s['pct_top']:.0f}% {m['levels']['labels'][-1]}",
                    class_="vb-sub"))
            else:
                children.append(ui.div(f"median · n = {s['n']:,}", class_="vb-sub"))

        if m["type"] == "nominal" and not single:
            dist = summarize(level, rows, var)["dist"]
            if dist is not None:
                n = dist.sum()
                color = dict(zip(m["levels"], m["colors"]))
                trs = [ui.tags.tr(
                    ui.tags.td(ui.span(class_="chip", style=f"background:{color.get(k, '#ccc')}"), k),
                    ui.tags.td(f"{c / n * 100:.0f}%", class_="num"))
                    for k, c in dist.items()]
                children.append(ui.tags.table(*trs, class_="vb-table"))
        else:
            ids = table_vars(level, var)
            trs = []
            for v in ids:
                cls = []
                if v == var:
                    cls.append("sel")
                if m.get("table") and v == ids[-1] and VARMETA[level][v]["fmt"] == "score":
                    cls.append("total")       # barrier score row under the four tertiles
                trs.append(ui.tags.tr(
                    ui.tags.td(short_label(level, v)),
                    ui.tags.td(cell_text(level, rows, v, single), class_="num"),
                    class_=" ".join(cls) or None))
            children.append(ui.tags.table(*trs, class_="vb-table"))

        value_cls = "txt" if m["type"] == "nominal" else None
        return ui.value_box(d["title"], ui.span(main, class_=value_cls), *children,
                            theme=theme, fill=False)

    @render.ui
    def box1():
        return _make_box(groups()[0], BOX_THEMES[0])

    @render.ui
    def box2():
        return _make_box(groups()[1], BOX_THEMES[1])

    @render.ui
    def box3():
        return _make_box(groups()[2], BOX_THEMES[2])

    @render.ui
    def box4():
        return _make_box(groups()[3], BOX_THEMES[3])

    # =======================================================================
    # Chart 1 — need profile: percentile indicators, selected vs comparison groups
    # =======================================================================
    @render.text
    def profile_title():
        unit = "tract" if input.level() == "tract" else "county"
        return f"Need profile — percentiles (higher = more need), selected {unit} vs comparisons"

    @reactive.effect
    async def _draw_profile():
        level = input.level()
        gs = groups()
        prof = [d for d in CATALOG[level] if d.get("profile")]
        labels = [d["label"].replace(" (pctl)", "").replace(" (median tract pctl)", " †")
                  for d in prof]

        fig = go.Figure()
        for x0, x1, c in ((0, 33.33, "rgba(0,0,0,0.00)"), (33.33, 66.67, "rgba(0,0,0,0.03)"),
                          (66.67, 100, "rgba(178,24,43,0.06)")):
            fig.add_vrect(x0=x0, x1=x1, fillcolor=c, line_width=0, layer="below")
        for i, d in enumerate(gs):
            rows = d["rows"]
            if rows is None:
                continue
            if d["single"]:
                vals = [rows.get(p["id"]) for p in prof]
            else:
                vals = [rows[p["id"]].median() for p in prof]
            vals = [None if v is None or pd.isna(v) else float(v) for v in vals]
            fig.add_trace(go.Bar(
                y=labels, x=vals, orientation="h", name=d["legend"],
                marker_color=GROUP_COLORS[i],
                marker_line=dict(color="#7a1f1c", width=1.2) if i == 0 else None,
                hovertemplate=f"{d['legend']}<br>%{{y}}: %{{x:.0f}}<extra></extra>",
            ))
        if selected() is None:
            fig.add_annotation(text="Click a polygon on the map to add its profile",
                               xref="paper", yref="paper", x=0, y=1.07, xanchor="left",
                               showarrow=False, font=dict(size=11, color="#6b7280"))
        notes = "Medians for comparison groups." + (
            " † = median tract percentile within the county." if level == "county" else "")
        fig.add_annotation(text=notes, xref="paper", yref="paper", x=0, y=-0.16, xanchor="left",
                           yanchor="top", showarrow=False, font=dict(size=10, color="#6b7280"))
        fig.update_layout(
            template="plotly_white", font=PLOT_FONT, barmode="group", bargap=0.25,
            margin=dict(l=10, r=15, t=35, b=70),
            xaxis=dict(range=[0, 100], title=None, tickvals=[0, 33.33, 66.67, 100],
                       ticktext=["0", "33 · low|mod", "67 · mod|high", "100"]),
            yaxis=dict(autorange="reversed", automargin=True),
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        )
        await session.send_custom_message("draw_chart", {"id": "profile", "fig": _fig_payload(fig)})

    # =======================================================================
    # Chart 2 — scatterplot: selected location vs all US locations
    # =======================================================================
    @render.text
    def scatter_title():
        lvl = input.level()
        units = "counties" if lvl == "county" else "tracts"
        return f"{VARMETA[lvl][cur_y()]['label']} vs {VARMETA[lvl][cur_x()]['label']} — all US {units}"

    @reactive.effect
    async def _draw_scatter():
        level, xv, yv = input.level(), cur_x(), cur_y()
        show_trend = input.trend()
        gid = selected()
        units = "counties" if level == "county" else "tracts"
        mx, my = VARMETA[level][xv], VARMETA[level][yv]
        xl, yl = mx["label"], my["label"]

        cols = list(dict.fromkeys([xv, yv]))
        df = DF[level][cols].dropna()
        x = df[xv].to_numpy(dtype=float)
        y = df[yv].to_numpy(dtype=float)
        names = county_names() if level == "county" else tract_names()
        labels = [names.get(g, g) for g in df.index]

        # ordinal / integer axes: jitter points so overlapping values stay visible
        rng = np.random.default_rng(0)
        def jitter(m, a):
            return a + rng.uniform(-0.18, 0.18, len(a)) if m["type"] == "ordinal" or m["fmt"] == "score" else a
        jx, jy = jitter(mx, x), jitter(my, y)

        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=jx, y=jy, mode="markers", name=f"All US {units} (n = {len(df):,})",
            marker=dict(size=6 if level == "county" else 4, color=SCATTER_POINT_COLOR,
                        opacity=0.5 if level == "county" else 0.3),
            customdata=np.column_stack([labels, [fmt_value(mx, a) for a in x],
                                        [fmt_value(my, b) for b in y]]) if len(df) else None,
            hovertemplate=f"%{{customdata[0]}}<br>{xl}: %{{customdata[1]}}"
                          f"<br>{yl}: %{{customdata[2]}}<extra></extra>",
        ))
        if show_trend and len(df) >= 3 and np.ptp(x) > 0:
            slope, intercept = np.polyfit(x, y, 1)
            r = float(np.corrcoef(x, y)[0, 1]) if np.ptp(y) > 0 else float("nan")
            xs = np.array([x.min(), x.max()])
            fig.add_trace(go.Scattergl(
                x=xs, y=slope * xs + intercept, mode="lines",
                name=f"Linear trend (r = {r:.2f}, slope = {slope:,.3g})",
                line=dict(color=TREND_COLOR, width=2), hoverinfo="skip",
            ))

        def note(text, color="#6b7280"):
            fig.add_annotation(text=text, xref="paper", yref="paper", x=0, y=1.07,
                               xanchor="left", showarrow=False, font=dict(size=11, color=color))

        if gid:
            name = place_name(level, gid)
            if gid in df.index:
                sx, sy = float(df.at[gid, xv]), float(df.at[gid, yv])
                px_ = (x < sx).mean() * 100
                py_ = (y < sy).mean() * 100
                fig.add_hline(y=sy, line_width=1, line_dash="dot", line_color=SELECTED_COLOR)
                fig.add_vline(x=sx, line_width=1, line_dash="dot", line_color=SELECTED_COLOR)
                fig.add_trace(go.Scattergl(
                    x=[sx], y=[sy], mode="markers", name=f"Selected: {name}",
                    marker=dict(size=15, color=SELECTED_COLOR, symbol="diamond",
                                line=dict(color="white", width=1.5)),
                    hovertemplate=f"<b>{name}</b><br>{xl}: {fmt_value(mx, sx)}"
                                  f"<br>{yl}: {fmt_value(my, sy)}<extra></extra>",
                ))
                note(f"{name}: higher than {px_:.0f}% of US {units} on X, {py_:.0f}% on Y",
                     SELECTED_COLOR)
            else:
                note(f"{name} has no data for {xl} and/or {yl}")
        else:
            note("Click a polygon on the map to locate it here")

        def axis(m, t):
            a = dict(title=t, zeroline=False)
            if m["type"] == "ordinal":
                a.update(tickvals=m["levels"]["values"], ticktext=m["levels"]["labels"])
            return a

        fig.update_layout(
            template="plotly_white", font=PLOT_FONT, margin=dict(l=55, r=15, t=35, b=10),
            xaxis=axis(mx, xl), yaxis=axis(my, yl),
            legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
            uirevision=f"{level}-{xv}-{yv}",
        )
        await session.send_custom_message("draw_chart", {"id": "scatter", "fig": _fig_payload(fig)})


app = App(app_ui, server, static_assets={"/plotlyjs": PLOTLY_JS_DIR})
