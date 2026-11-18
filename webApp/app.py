"""
Cancer Screening Priority Mapper
================================
Shiny for Python app (core API).

Pages (top navigation bar)
    Home               landing page (pages/landing.py)
    Priority & Access  map of Priority / Access variables; cutoff selector (burden measure,
                       ranking, burden / access / barrier cutoffs); Need-profile chart overlaid
                       on the lower-left of the map
    Barriers           map of all other variables (barriers, county burden, population,
                       context); scatterplot overlaid on the lower-left of the map
    Data & Methods     pages/methods.py

Both map pages share one map, one set of info boxes and one floating control panel whose
sections switch with the page, so the clicked polygon and the map view carry over.

Priority classes are recomputed in the app from the lvl_* columns (classify.classify_at),
following data_dictionary.md, so every cutoff combination is available instantly.

Run locally:   shiny run --reload app.py
Deploy:        rsconnect deploy shiny . --name <account> --title cancer-screening-priority-mapper
Inputs:        data/app/{tract_values.csv, county_values.csv, variables.json}  (prep_mvp_data.py)
               data/geo/...                                               (prep_geodata.py)
               data/geo/mammography.geojson  optional                     (prep_mammography_fda.py)
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

from classify import classify_at
from pages.landing import landing_ui
from pages.methods import methods_ui

warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="All-NaN slice")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
APP_DATA = APP_DIR / "data" / "app"
GEO_DIR = APP_DIR / "data" / "geo"
MAMMO_PATH = GEO_DIR / "mammography.geojson"

# for state-line
STATE_LINES_PATH = GEO_DIR / "state_lines.geojson"
STATE_LINE_COLOR = "#374151"   # dark grey, softened by the opacity below
STATE_LINE_OPACITY = 0.35
STATE_LINE_WIDTH = 0.8         # px

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
CUTOFF_MARK_COLOR = "#111827"

CLICK_HINT = "Click a polygon on the map"
PLOT_FONT = dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=11)
GROUP_ORDER = ["Priority", "Access", "Barriers", "County burden", "Population", "Context"]
MAX_TABLE_ROWS = 4          # rows in each info box (kept short so the map has room)

MAP_PAGES = ("map", "barriers")
PAGE_KEY = {"map": "pa", "barriers": "b"}   # page -> catalog "pages" key

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
CATALOG: dict = json.loads((APP_DATA / "variables.json").read_text())
META: dict = CATALOG["meta"]
VARMETA: dict[str, dict[str, dict]] = {
    lvl: {d["id"]: d for d in CATALOG[lvl]} for lvl in ("county", "tract")
}
CLASS_LEVELS: list[str] = META["classes"]["levels"]
CLASS_COLORS: list[str] = META["classes"]["colors"]
CUT_OPTS = [str(c) for c in META["cutoffs"]["options"]]
CUT_DEF = META["cutoffs"]["defaults"]
MEASURES: dict = META["measures"]
SCALES: dict = META["scales"]
PROFILE: dict = META["profile"]
QA_FAILED = META.get("qa_failed") or []


def _read(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"GEOID": str, "FIPS": str, "burden_FIPS": str,
                                  "rurality": str}, keep_default_na=True)
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
TRACT_CTY_POS = _cidx.get_indexer(_tidx.str[:5])          # tract -> county row (-1 if none)
TRACT_WOMEN = DF["tract"].get("n_women_40_74", pd.Series(0, index=_tidx)).fillna(0).to_numpy()


def tracts_in_counties(county_pos) -> np.ndarray:
    """Tract row positions for a set of county row positions."""
    ids = _cidx[np.atleast_1d(county_pos)]
    parts = [GROUP_IDX["tract_by_county"].get(g) for g in ids]
    parts = [p for p in parts if p is not None]
    return np.concatenate(parts) if parts else np.array([], dtype=int)


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

@lru_cache(maxsize=1)
def load_state_lines() -> dict | None:
    return json.loads(STATE_LINES_PATH.read_text()) if STATE_LINES_PATH.exists() else None


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
def _grouped(entries) -> dict:
    groups: dict[str, dict] = {}
    for d in entries:
        groups.setdefault(d["group"], {})[d["id"]] = d["label"]
    ordered = {g: groups[g] for g in GROUP_ORDER if g in groups}
    ordered.update({g: c for g, c in groups.items() if g not in ordered})
    return ordered


def var_choices(level: str, page_key: str) -> dict:
    """Dropdown choices for a page: entries whose 'pages' list contains page_key."""
    return _grouped(d for d in CATALOG[level] if page_key in d.get("pages", []))


def scatter_choices(level: str) -> dict:
    """Any stored numeric variable (not class / not computed / not ranking-dependent)."""
    return _grouped(d for d in CATALOG[level]
                    if d["type"] != "nominal" and not d.get("dynamic") and not d.get("by_scale"))


def valid(level: str, var: str | None, key: str) -> str:
    """var if it is valid for this level and slot ('pa', 'b', 'x', 'y'), else the default."""
    ok = (scatter_choices if key in ("x", "y") else
          (lambda lv: var_choices(lv, key)))(level)
    allowed = {i for g in ok.values() for i in g}
    if var in allowed:
        return var
    d = META["defaults"][level].get(key)
    return d if d in allowed else next(iter(allowed))


def short_label(level: str, var: str) -> str:
    m = VARMETA[level][var]
    return m.get("short") or m["label"]


def table_vars(level: str, var: str) -> list[str]:
    m = VARMETA[level][var]
    if m.get("table"):
        ids = [v for v in m["table"] if v in VARMETA[level]]
    else:
        ids = [d["id"] for d in CATALOG[level] if d["group"] == m["group"]
               and d["fmt"] == m["fmt"] and not d.get("dynamic") and not d.get("by_scale")]
    if var not in ids:
        ids = [var] + ids
    if var in ids[MAX_TABLE_ROWS:]:                 # keep the selected variable visible
        ids = [var] + [i for i in ids if i != var]
    return ids[:MAX_TABLE_ROWS]


def fmt_value(m: dict, v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "—"
    if m["type"] == "nominal":
        return str(v)
    return {
        "pct": f"{v:,.1f}%",
        "pctl": f"{v:,.0f}",
        "rate": f"{v:,.1f}",
        "count": f"{v:,.0f}",
        "count1": f"{v:,.1f}",
        "index": f"{v:+.3f}",
        "mi": f"{v:,.1f} mi",
    }.get(m["fmt"], f"{v:,.2f}")


def discrete_scale(colors: list[str]) -> list:
    k = len(colors)
    scale = []
    for i, c in enumerate(colors):
        scale += [[i / k, c], [(i + 1) / k, c]]
    return scale


def _fig_payload(fig: go.Figure) -> dict:
    return json.loads(fig.to_json())


CLASS_LABELS = np.array([None] + CLASS_LEVELS, dtype=object)


def code_labels(codes: np.ndarray) -> np.ndarray:
    return CLASS_LABELS[np.where(np.isnan(codes), 0, codes).astype(int)]


def default_codes(measure: str | None = None) -> np.ndarray:
    d, t = CUT_DEF, DF["tract"]
    m = measure or d["measure"]
    return classify_at(t[f"lvl_{m}"], t[f"lvl_access_{d['scale']}"],
                       t[f"lvl_barriers_{d['scale']}"], d["burden"], d["access"], d["barriers"])


# ---------------------------------------------------------------------------
# Browser-side bridge
# ---------------------------------------------------------------------------
PLOTLY_JS_DIR = Path(plotly.__file__).resolve().parent / "package_data"

BRIDGE_JS = """
(function () {
  const CFG = { responsive: true, displaylogo: false };

  function watchResize(el) {
    if (el._cspmRO) return;
    el._cspmRO = new ResizeObserver(() => { if (el.offsetParent) Plotly.Plots.resize(el); });
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
  // Messages run one after another; each waits for the basemap style; the click
  // listener is re-attached after every draw.
  let mapQueue = Promise.resolve();
  function enqueue(fn) {
    mapQueue = mapQueue.then(fn).catch((err) => console.error("[map]", err));
  }
  function mapReady(el, timeoutMs = 8000) {
    const sub = el && el._fullLayout && el._fullLayout.map && el._fullLayout.map._subplot;
    const m = sub && sub.map;
    if (!m || (m.isStyleLoaded && m.isStyleLoaded())) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => { clearTimeout(t); resolve(); };
      const t = setTimeout(done, timeoutMs);
      m.once("idle", done);
    });
  }
  // Move a trace's MapLibre layers to the top of the map (above polygons and borders)
  function bringToFront(el, index) {
    const sub = el && el._fullLayout && el._fullLayout.map && el._fullLayout.map._subplot;
    const m = sub && sub.map;
    const tr = el && el._fullData && el._fullData[index];
    if (!m || !tr || !m.getStyle) return;
    const prefix = "plotly-trace-layer-" + tr.uid;
    m.getStyle().layers
      .filter((l) => l.id.startsWith(prefix))
      .forEach((l) => m.moveLayer(l.id));   // no beforeId = move to the very top
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
      await mapReady(el);
      if (el.data && el.data.length > 2) bringToFront(el, 2);
      try {
        await drawMap(el, fig);
      } catch (err) {
        console.warn("[map] draw retry:", err.message);
        await mapReady(el);
        if (el.data && el.data.length > 2) bringToFront(el, 2);
        await drawMap(el, fig);
      } finally {
        watchResize(el);
        bindClick(el);
      }
      await mapReady(el);
      if (el.data && el.data.length > 2) bringToFront(el, 2);
    });
  });
  Shiny.addCustomMessageHandler("highlight", function (msg) {
    enqueue(async function () {
      const el = document.getElementById("screen_map");
      if (!el || !el.data || el.data.length < 2) return;
      const cur = el.data[1].locations || [];
      if (!cur.length && !msg.locations.length) return;
      await mapReady(el);
      await Plotly.restyle(el, { geojson: [msg.geojson], locations: [msg.locations], z: [msg.z] }, [1]);
    });
  });
  Shiny.addCustomMessageHandler("set_mammo", function (msg) {
    enqueue(async function () {
      const el = document.getElementById("screen_map");
      if (!el || !el.data || el.data.length <= msg.index) return;
      await mapReady(el);
      await Plotly.restyle(el, { lon: [msg.lon], lat: [msg.lat], text: [msg.text] }, [msg.index]);
      bringToFront(el, msg.index);
      await mapReady(el);
      bringToFront(el, msg.index);   // again after the map settles, in case layers were re-added
    });
  });

  // ---- Overlay charts ----
  Shiny.addCustomMessageHandler("draw_chart", function (msg) {
    const el = document.getElementById(msg.id);
    if (!el) return;
    Plotly.react(el, msg.fig.data, msg.fig.layout, CFG).then(() => watchResize(el));
  });

  // ---- Collapsible control panel / minimizable overlay charts ----
  document.addEventListener("click", function (ev) {
    if (ev.target.closest(".panel-toggle")) {
      document.getElementById("hero").classList.toggle("panel-collapsed");
    }
    if (ev.target.closest(".clear-sel")) {
      Shiny.setInputValue("clear_selection", Date.now(), { priority: "event" });
    }
    const t = ev.target.closest(".ov-toggle");
    if (t) {
      const card = t.closest(".overlay-chart");
      card.classList.toggle("min");
      t.textContent = card.classList.contains("min") ? "+" : "–";
      const plot = card.querySelector(".ov-plot");
      if (plot && !card.classList.contains("min")) setTimeout(() => Plotly.Plots.resize(plot), 50);
    }
  });
  // Esc clears the selected polygon (ignored while typing in an input)
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    const tag = (ev.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    Shiny.setInputValue("clear_selection", Date.now(), { priority: "event" });
  });
    // ---- Map-page layout: exact navbar height + no space below the map ----
  const MAP_PAGES = ["map", "barriers"];
  function syncLayout(page) {
    const nav = document.querySelector(".navbar");
    if (nav) document.documentElement.style.setProperty("--titlebar-h", nav.offsetHeight + "px");
    if (page === undefined) {
      const a = document.querySelector(".navbar .nav-link.active");
      page = a ? a.getAttribute("data-value") : "";
    }
    document.body.classList.toggle("map-page", MAP_PAGES.includes(page));
    const el = document.getElementById("screen_map");
    if (el && el.data && MAP_PAGES.includes(page)) Plotly.Plots.resize(el);
  }
  $(document).on("shiny:inputchanged", function (e) {
    if (e.name === "page") setTimeout(() => syncLayout(e.value), 0);
  });
  $(document).on("shiny:connected", () => syncLayout());
  window.addEventListener("resize", () => syncLayout());
})();
"""

APP_CSS = """
:root { --titlebar-h: 56px; --gap: 14px; --panel-w: 300px; --chart-w: 540px; --chart-h: 400px; }
body { background: #f4f5f7; overflow-x: hidden; }

/* ---- Navigation bar ---- */
.navbar { z-index: 1000; background: #fff !important; border-bottom: 1px solid #e5e7eb;
          min-height: var(--titlebar-h); }
.navbar .navbar-brand { font-size: 1.25rem; font-weight: 500; color: #111827; }
.navbar .nav-link { font-size: 0.95rem; color: #4b5563; }
.navbar .nav-link.active { color: #111827; font-weight: 600; }
.nav-meta { font-size: 0.78rem; color: #6b7280; padding: 0 8px; }
.nav-meta .qa-flag { font-size: 0.75rem; font-weight: 500; color: #8a1c1c; background: #fde2e1;
                     border-radius: 999px; padding: 2px 10px; margin-left: 6px; }
.tab-content > .tab-pane { padding: 0; }
body.bslib-page-navbar > .container-fluid { padding-left: 0; padding-right: 0; }

/* ---- Map page frame ---- */
.hero { position: relative; height: calc(100vh - var(--titlebar-h)); min-height: 640px; overflow: hidden; }
.map-bg { position: absolute; inset: 0; }
.glass {
  background: rgba(255, 255, 255, 0.88);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 14px; box-shadow: 0 8px 28px rgba(20, 30, 50, 0.18);
}

/* ---- Left panel ---- */
.float-panel {
  position: absolute; z-index: 20; top: var(--gap); left: var(--gap);
  width: var(--panel-w); max-height: calc(100% - 2 * var(--gap));
  overflow-y: auto; padding: 12px 16px 14px;
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.float-panel .form-group, .float-panel .shiny-input-container { margin-bottom: 0.55rem; width: 100%; }
.float-panel label { font-size: 0.82rem; margin-bottom: 0.2rem; }
.float-panel .form-select { font-size: 0.86rem; padding-top: 0.28rem; padding-bottom: 0.28rem; }
.float-panel hr { margin: 0.55rem 0; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem; }
.sb-head { font-weight: 600; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.05em;
           color: #6b7280; margin: 0.15rem 0 0.4rem; }
.sb-head-row { display: flex; justify-content: space-between; align-items: baseline; }
.sb-head-row a { font-size: 0.75rem; }
.panel-note { font-size: 0.74rem; color: #4b5563; }
.panel-note p { margin-bottom: 0.3rem; }
.var-desc { font-size: 0.74rem; color: #4b5563; margin: -0.3rem 0 0.45rem; }
.cut-summary { font-size: 0.76rem; color: #374151; background: rgba(178, 24, 43, 0.07);
               border-radius: 8px; padding: 6px 9px; margin-top: 2px; }
.cut-summary b { color: #8a1c1c; }

/* Segmented "selection bars" (inline radio buttons: label.radio-inline > input + span) */
.seg .shiny-input-container { margin-bottom: 0.45rem; }
.seg .shiny-options-group { display: flex !important; flex-wrap: nowrap; gap: 0 !important;
                            border: 1px solid #cfd4dc; border-radius: 8px;
                            overflow: hidden; background: #fff; }
.seg label.radio-inline { display: flex; align-items: stretch; flex: 1 1 auto;
                          margin: 0 !important; padding: 0 !important; position: relative;
                          cursor: pointer; border-left: 1px solid #cfd4dc; background: #fff; }
.seg label.radio-inline:first-child { border-left: none; }
.seg label.radio-inline input { position: absolute; opacity: 0; width: 1px; height: 1px;
                                margin: 0; pointer-events: none; }
.seg label.radio-inline span { flex: 1 1 auto; display: flex; align-items: center;
                               justify-content: center; margin: 0; padding: 4px 6px;
                               font-size: 0.78rem; line-height: 1.2; text-align: center;
                               white-space: normal; color: #374151; user-select: none; }

/* Selected: color the whole segment, not just the text */
.seg label.radio-inline:has(input:checked) { background: #B2182B; }
.seg label.radio-inline:has(input:checked) span { color: #fff; font-weight: 600; }

/* Hover / keyboard focus */
.seg label.radio-inline:not(:has(input:checked)):hover { background: #f3f4f6; }
.seg label.radio-inline:has(input:checked):hover { background: #9b1525; }
.seg label.radio-inline:has(input:focus-visible) { outline: 2px solid #1F6FB2; outline-offset: -2px; }

.panel-toggle { border: none; background: transparent; font-size: 1.1rem; line-height: 1;
                padding: 4px 8px; border-radius: 8px; color: #374151; cursor: pointer; }
.panel-toggle:hover { background: rgba(0, 0, 0, 0.06); }
.panel-open-btn { position: absolute; z-index: 21; top: var(--gap); left: var(--gap);
                  padding: 8px 14px; font-size: 0.9rem; display: none; }
.hero.panel-collapsed .float-panel { transform: translateX(calc(-100% - 2 * var(--gap))); opacity: 0; pointer-events: none; }
.hero.panel-collapsed .panel-open-btn { display: block; }

/* ---- Info boxes (compact) ---- */
.float-boxes {
  position: absolute; z-index: 15; top: var(--gap); right: var(--gap);
  left: calc(var(--panel-w) + 2 * var(--gap));
  pointer-events: none; transition: left 0.25s ease;
}
.hero.panel-collapsed .float-boxes { left: calc(var(--gap) + 130px); }
.float-boxes .bslib-grid { gap: 10px; }
.float-boxes .bslib-value-box {
  pointer-events: auto; margin-bottom: 0; height: 100%;
  border: none; border-radius: 12px; box-shadow: 0 6px 20px rgba(20, 30, 50, 0.20); opacity: 0.96;
}
#box1, #box2, #box3, #box4 { height: 100%; }
.float-boxes .value-box-area { padding: 0.45rem 0.8rem 0.5rem !important; }
.float-boxes .value-box-title { font-size: 0.78rem; margin-bottom: 0; }
.bslib-value-box .value-box-value { font-size: 1.3rem; line-height: 1.12; margin-bottom: 0.1rem; }
.bslib-value-box .value-box-value .txt { font-size: 0.98rem; font-weight: 600; line-height: 1.2; display: block; }
.vb-sub   { font-size: 0.68rem; opacity: 0.9; margin-bottom: 0.1rem; line-height: 1.2;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vb-table { font-size: 0.67rem; width: 100%; line-height: 1.25; }
.vb-table td { padding: 0 0.25rem 0 0; vertical-align: top; }
.vb-table td.num { text-align: right; white-space: nowrap; }
.vb-table tr.sel { font-weight: 700; }
.vb-table .ok { font-weight: 700; }
.chip { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px;
        border: 1px solid rgba(0,0,0,0.15); }

/* ---- Overlay chart (lower-left of the map) ---- */
.overlay-chart {
  position: absolute; z-index: 16; bottom: var(--gap);
  left: calc(var(--panel-w) + 2 * var(--gap));
  width: min(var(--chart-w), calc(100% - var(--panel-w) - 4 * var(--gap) - 150px));
  height: var(--chart-h); padding: 8px 10px 4px;
  display: flex; flex-direction: column; transition: left 0.25s ease;
}
.hero.panel-collapsed .overlay-chart { left: var(--gap); }
.overlay-chart.min { height: auto; }
.overlay-chart.min .ov-plot { display: none; }
.ov-head { display: flex; justify-content: space-between; align-items: center; gap: 8px;
           font-size: 0.8rem; font-weight: 600; color: #374151; padding: 0 2px 2px; }
.ov-head .ov-toggle { border: 1px solid #d1d5db; background: #fff; color: #374151;
                      font-size: 1.35rem; font-weight: 600; line-height: 1;
                      width: 30px; height: 30px; flex: none; padding: 0;
                      display: inline-flex; align-items: center; justify-content: center;
                      border-radius: 8px; cursor: pointer; }
.ov-head .ov-toggle:hover { background: #f3f4f6; border-color: #9ca3af; }
.ov-plot { flex: 1 1 auto; min-height: 0; }

/* Map toolbar: bottom-right, left of the color legend */
@media (min-width: 992px) {
  #screen_map .modebar-container { top: auto !important; bottom: var(--gap);
                                   left: auto !important; right: 150px !important; }
}

/* ---- Small screens: stack everything ---- */
@media (max-width: 991.98px) {
  .hero { height: auto; min-height: 0; overflow: visible; display: flex; flex-direction: column; }
  .map-bg { position: relative; inset: auto; height: 65vh; order: 2; }
  .float-panel, .float-boxes, .overlay-chart { position: static; width: auto; max-height: none;
                                               margin: var(--gap); }
  .float-panel { order: 1; }
  .float-boxes { order: 3; pointer-events: auto; }
  .overlay-chart { order: 4; height: 340px; }
  .panel-toggle, .panel-open-btn { display: none !important; }
  .hero.panel-collapsed .float-panel { transform: none; opacity: 1; pointer-events: auto; }
}

/* ---- Landing / methods pages ---- */
.lp-wrap { max-width: 1080px; margin: 0 auto; padding: 36px 20px 56px; color: #1f2937; }
.lp-hero { padding: 28px 0 8px; }
.lp-beta { display: inline-block; font-size: 0.78rem; font-weight: 600; letter-spacing: 0.03em;
           text-transform: uppercase; color: #8a1c1c; background: #fde2e1; border-radius: 999px;
           padding: 3px 12px; }
.lp-title { font-size: 2.6rem; font-weight: 600; line-height: 1.15; margin: 14px 0 10px; color: #111827; }
.lp-title-sm { font-size: 2rem; }
.lp-purpose { font-size: 1.25rem; color: #374151; max-width: 760px; margin-bottom: 22px; }
.lp-cta { display: flex; flex-wrap: wrap; gap: 12px; }
.lp-section { margin-top: 44px; }
.lp-section h2, .lp-h2-gap { font-size: 1.45rem; font-weight: 600; margin-bottom: 8px; color: #111827; }
.lp-h2-gap { margin-top: 28px; }
.lp-lead { font-size: 1rem; color: #4b5563; max-width: 820px; }
.lp-after { margin-top: 16px; }
.lp-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.lp-card { background: #fff; border-radius: 14px; padding: 18px 20px;
           box-shadow: 0 2px 10px rgba(20, 30, 50, 0.08); }
.dim-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.dim-name { font-size: 1.15rem; font-weight: 600; }
.dim-level { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: #6b7280; }
.dim-question { font-weight: 500; color: #8a1c1c; margin-bottom: 8px; }
.dim-body { font-size: 0.92rem; margin-bottom: 6px; }
.dim-note { font-size: 0.82rem; color: #6b7280; margin-bottom: 0; }
.lp-table { width: 100%; background: #fff; border-radius: 14px; overflow: hidden; margin-top: 12px;
            box-shadow: 0 2px 10px rgba(20, 30, 50, 0.08); border-collapse: collapse; }
.lp-table th { text-align: left; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em;
               color: #6b7280; padding: 10px 16px; border-bottom: 1px solid #e5e7eb; }
.lp-table td { padding: 10px 16px; font-size: 0.92rem; vertical-align: top; border-bottom: 1px solid #f0f1f3; }
.lp-table td:first-child { display: flex; align-items: baseline; }
.lp-table tr:last-child td { border-bottom: none; }
.lp-chip { display: inline-block; flex: none; width: 12px; height: 12px; border-radius: 3px; margin-right: 8px;
           border: 1px solid rgba(0, 0, 0, 0.15); vertical-align: -1px; }
.lp-class-name { font-weight: 600; }
.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 12px; }
.stat-tile { background: #fff; border-radius: 14px; padding: 18px 20px; box-shadow: 0 2px 10px rgba(20, 30, 50, 0.08); }
.stat-value { font-size: 2rem; font-weight: 600; color: #B2182B; line-height: 1.1; }
.stat-label { font-size: 0.88rem; color: #4b5563; margin-top: 4px; }
.lp-fine { font-size: 0.78rem; color: #6b7280; margin-top: 10px; }
.lp-caution { background: #fff; border-left: 4px solid #F4A261; border-radius: 10px; padding: 16px 22px;
              box-shadow: 0 2px 10px rgba(20, 30, 50, 0.06); }
.lp-links { padding-left: 1.2rem; }
@media (max-width: 767.98px) {
  .lp-title { font-size: 2rem; }
  .lp-table, .lp-table tbody, .lp-table tr, .lp-table td { display: block; width: 100%; }
  .lp-table thead { display: none; }
  .lp-table tr { padding: 10px 0; border-bottom: 1px solid #e5e7eb; }
  .lp-table td { border: none; padding: 2px 16px; }
}

/* Clear-selection button in the "Selected" box */
.vb-title-row { display: flex; justify-content: space-between; align-items: center;
                gap: 6px; width: 100%; }
.float-boxes .value-box-title:has(.vb-title-row) { width: 100%; }
.clear-sel { border: none; background: rgba(255, 255, 255, 0.55); color: inherit;
             font-size: 0.68rem; line-height: 1.4; padding: 1px 8px; border-radius: 999px;
             cursor: pointer; white-space: nowrap; }
.clear-sel:hover { background: rgba(255, 255, 255, 0.85); }
.clear-sel:focus-visible { outline: 2px solid #1F6FB2; outline-offset: 1px; }

/* Map pages: the map fills the window exactly; hide the empty tab content below it */
body.map-page .tab-content { display: none; }
body.map-page { padding-bottom: 0 !important; margin-bottom: 0 !important; }
@media (min-width: 992px) {
  body.map-page { overflow-y: hidden; }   /* no stray scrollbar from a pixel of overflow */
}
.hero { min-height: 480px; }              /* was 640px: let short laptop screens fit exactly */
"""


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
DEFAULT_LEVEL = "county"
CUT_CHOICES = {c: f"{c}th" for c in CUT_OPTS}


def seg(id_, label, choices, selected):
    """A segmented 'selection bar' (inline radio buttons styled as a button group)."""
    return ui.div(ui.input_radio_buttons(id_, label, choices, selected=selected, inline=True),
                  class_="seg")


on_pa = "input.page === 'map'"
on_b = "input.page === 'barriers'"

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
    ui.panel_conditional(on_pa, ui.input_select(
        "var_pa", "Variable (Priority & Access)", var_choices(DEFAULT_LEVEL, "pa"),
        selected=valid(DEFAULT_LEVEL, None, "pa"))),
    ui.panel_conditional(on_b, ui.input_select(
        "var_b", "Variable", var_choices(DEFAULT_LEVEL, "b"),
        selected=valid(DEFAULT_LEVEL, None, "b"))),
    ui.output_ui("var_desc"),
    (ui.input_checkbox("show_mammo", "Show mammography locations", value=False)
     if MAMMO_PATH.exists() else None),
    # ---- Page 2: cutoff options ----
    ui.panel_conditional(
        on_pa,
        ui.hr(),
        ui.div(ui.div("Cutoff options", class_="sb-head"),
               ui.input_action_link("cut_reset", "Reset to defaults"), class_="sb-head-row"),
        seg("measure", "Burden measure", MEASURES, CUT_DEF["measure"]),
        seg("scale", "Access & barrier ranking", SCALES, CUT_DEF["scale"]),
        seg("cut_b", "Burden cutoff (county pctl)", CUT_CHOICES, str(CUT_DEF["burden"])),
        seg("cut_a", "Access cutoff (distance pctl)", CUT_CHOICES, str(CUT_DEF["access"])),
        seg("cut_r", "Barrier cutoff (barrier pctl)", CUT_CHOICES, str(CUT_DEF["barriers"])),
        ui.output_ui("cut_summary"),
    ),
    # ---- Page 3: scatterplot axes ----
    ui.panel_conditional(
        on_b,
        ui.hr(),
        ui.div("Scatterplot", class_="sb-head"),
        ui.input_select("sx", "X-axis variable", scatter_choices(DEFAULT_LEVEL),
                        selected=valid(DEFAULT_LEVEL, None, "x")),
        ui.input_select("sy", "Y-axis variable", scatter_choices(DEFAULT_LEVEL),
                        selected=valid(DEFAULT_LEVEL, None, "y")),
        ui.input_checkbox("trend", "Show trend line", value=True),
    ),
    ui.hr(),
    ui.div(
        ui.markdown("Click a polygon to update the boxes and chart. Percentiles: 0 = lowest, "
                    "100 = highest; higher = **more need**. Burden is county-level context."),
        ui.panel_conditional("input.level === 'county'", ui.markdown(
            "County view: tract indicators are the **median tract**; priority is the "
            "**% of tracts classed High priority** at the selected cutoffs.")),
        class_="panel-note",
    ),
    id="ctrl_panel",
    class_="float-panel glass",
)


def overlay(cond: str, title_id: str, plot_id: str, card_id: str, minimized: bool = False):
    return ui.panel_conditional(
        cond,
        ui.div(
            ui.div(ui.output_text(title_id, inline=True),
                   ui.tags.button("+" if minimized else "–", class_="ov-toggle",
                                  title="Expand / minimize", type="button"),
                   class_="ov-head"),
            ui.div(id=plot_id, class_="ov-plot"),
            id=card_id, class_="overlay-chart glass" + (" min" if minimized else ""),
        ),
    )


map_block = ui.panel_conditional(
    "input.page === 'map' || input.page === 'barriers'",
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
        overlay(on_pa, "profile_title", "profile", "ov_profile"), #add minimized=True to hide Need Profile on page load.
        overlay(on_b, "scatter_title", "scatter", "ov_scatter"),
        id="hero",
        class_="hero",
    ),
)

# ---- Landing page inputs: class legend (same colors as the map) and headline numbers ----
CLASS_LEGEND = list(zip(CLASS_LEVELS, CLASS_COLORS))


def landing_stats() -> list[dict]:
    """Headline numbers at the default cutoffs (incidence-rate burden, urban/rural ranking)."""
    t = DF["tract"]
    hp = default_codes() == 1
    return [
        {"value": f"{int(hp.sum()):,}", "label": "census tracts in the High priority class"},
        {"value": f"{TRACT_WOMEN[hp].sum() / 1e6:,.1f} million",
         "label": "women aged 40–74 living in those tracts"},
        {"value": f"{t.loc[hp, 'state'].nunique()}" if "state" in t else "—",
         "label": "states (and DC) with at least one"},
    ]


app_ui = ui.page_navbar(
    ui.nav_panel("Home",
                 landing_ui(CLASS_LEGEND, landing_stats(),
                            "Figures at the default cutoffs (burden 90th, access 67th, barriers "
                            "67th percentile; urban/rural ranking; incidence-rate burden)."),
                 value="home"),
    ui.nav_panel("Priority & Access", value="map"),
    ui.nav_panel("Barriers", value="barriers"),
    ui.nav_panel("Data & Methods", methods_ui(), value="methods"),
    ui.nav_spacer(),
    ui.nav_control(ui.span(
        f"ACS {META.get('acs_period', '')} · Cancer burden 2019-2023",
        (ui.span(f"⚠ Data QA: {', '.join(QA_FAILED)} check(s) failed — see prep_qa.txt",
                 class_="qa-flag") if QA_FAILED else None),
        class_="nav-meta")),
    title=APP_TITLE,
    id="page",
    selected="home",
    header=ui.TagList(
        ui.head_content(
            ui.tags.script(src="plotlyjs/plotly.min.js"),
            ui.tags.style(APP_CSS),
        ),
        map_block,
    ),
    footer=ui.tags.script(BRIDGE_JS),
    navbar_options=ui.navbar_options(position="sticky-top"),
    window_title=APP_TITLE,
    fillable=False,
    padding=0,
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def server(input, output, session):
    selected = reactive.value(None)  # GEOID of clicked polygon

    # ---- Navigation ----
    @reactive.calc
    def page():
        return input.page()

    @reactive.calc
    def map_visible():
        return page() in MAP_PAGES

    @reactive.effect
    @reactive.event(input.go_map)
    def _go_map():
        ui.update_navset("page", selected="map")

    @reactive.effect
    @reactive.event(input.go_methods)
    def _go_methods():
        ui.update_navset("page", selected="methods")

    # ---- Cutoffs and classification ----
    @reactive.calc
    def cut() -> dict:
        return {"measure": input.measure(), "scale": input.scale(),
                "b": int(input.cut_b()), "a": int(input.cut_a()), "r": int(input.cut_r())}

    @reactive.effect
    @reactive.event(input.cut_reset)
    def _reset_cutoffs():
        ui.update_radio_buttons("measure", selected=CUT_DEF["measure"])
        ui.update_radio_buttons("scale", selected=CUT_DEF["scale"])
        ui.update_radio_buttons("cut_b", selected=str(CUT_DEF["burden"]))
        ui.update_radio_buttons("cut_a", selected=str(CUT_DEF["access"]))
        ui.update_radio_buttons("cut_r", selected=str(CUT_DEF["barriers"]))

    @reactive.calc
    def class_codes() -> np.ndarray:
        c, t = cut(), DF["tract"]
        return classify_at(t[f"lvl_{c['measure']}"], t[f"lvl_access_{c['scale']}"],
                           t[f"lvl_barriers_{c['scale']}"], c["b"], c["a"], c["r"])

    @reactive.calc
    def class_series() -> pd.Series:
        return pd.Series(code_labels(class_codes()), index=_tidx)

    @reactive.calc
    def county_pct_high() -> pd.Series:
        codes = class_codes()
        ok = ~np.isnan(codes) & (TRACT_CTY_POS >= 0)
        n = np.bincount(TRACT_CTY_POS[ok], minlength=len(_cidx))
        hp = np.bincount(TRACT_CTY_POS[ok], weights=(codes[ok] == 1), minlength=len(_cidx))
        with np.errstate(invalid="ignore", divide="ignore"):
            pct = np.where(n > 0, hp / n * 100, np.nan)
        return pd.Series(pct, index=_cidx)

    def column(level: str, m: dict) -> str:
        return m["by_scale"][cut()["scale"]] if m.get("by_scale") else m["id"]

    def series(level: str, var: str) -> pd.Series:
        """Full column for a variable, resolving computed and ranking-dependent ones."""
        if var == "priority_class":
            return class_series()
        if var == "pct_high_priority":
            return county_pct_high()
        return DF[level][column(level, VARMETA[level][var])]

    @render.ui
    def cut_summary():
        codes = class_codes()
        hp = codes == 1
        n_cty = len(np.unique(TRACT_CTY_POS[hp & (TRACT_CTY_POS >= 0)]))
        return ui.div(
            ui.tags.b(f"{int(hp.sum()):,}"), " High priority tracts · ",
            ui.tags.b(f"{TRACT_WOMEN[hp].sum() / 1e6:,.2f}M"), " women 40–74 · ",
            ui.tags.b(f"{n_cty:,}"), " counties (US)",
            class_="cut-summary")

    # ---- Variable selection ----
    @reactive.calc
    def cur_var() -> str:
        level = input.level()
        if page() == "barriers":
            return valid(level, input.var_b(), "b")
        return valid(level, input.var_pa(), "pa")

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
            vp, vb, x, y = input.var_pa(), input.var_b(), input.sx(), input.sy()
        ui.update_select("var_pa", choices=var_choices(level, "pa"), selected=valid(level, vp, "pa"))
        ui.update_select("var_b", choices=var_choices(level, "b"), selected=valid(level, vb, "b"))
        ui.update_select("sx", choices=scatter_choices(level), selected=valid(level, x, "x"))
        ui.update_select("sy", choices=scatter_choices(level), selected=valid(level, y, "y"))

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

    @reactive.effect
    @reactive.event(input.clear_selection)
    def _clear_selection():
        selected.set(None)

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
    # Comparison groups: row positions for each box
    # =======================================================================
    @reactive.calc
    def groups() -> list[dict]:
        level = input.level()
        gid = selected()
        data = DF[level]
        unit = "county" if level == "county" else "tract"
        units = "counties" if level == "county" else "tracts"

        def g(title, sub=CLICK_HINT, legend="", pos=None, single=False):
            return {"title": title, "sub": sub, "legend": legend, "pos": pos, "single": single}

        g1 = g(f"Selected {unit}", legend=f"Selected {unit}", single=True)
        g2 = g(f"Adjacent {units}", legend=f"Adjacent {units}")
        if level == "county":
            g3 = g("State (all counties)", legend="State")
            g4 = g("United States (all counties)", "All US counties", "US", np.arange(len(data)))
        else:
            st = input.state()
            g3 = g("County (all tracts)", legend="County")
            g4 = g("State (all tracts)", f"All tracts in {state_label(st)}",
                   state_label(st, short=True), GROUP_IDX["tract_by_state"].get(st))
        if not gid:
            return [g1, g2, g3, g4]

        name = place_name(level, gid)
        if gid in data.index:
            g1.update(sub=name, legend="Selected region", hover=name,
                      pos=int(data.index.get_loc(gid)))
        else:
            g1.update(sub=f"{name} — no data", legend="Selected region", hover=name)

        nbrs = (load_county_neighbors() if level == "county"
                else load_tract_neighbors(gid[:2])).get(gid, [])
        npos = data.index.get_indexer(nbrs)
        npos = npos[npos >= 0]
        g2.update(sub=f"{len(nbrs)} adjacent {units}", pos=npos if len(npos) else None)

        if level == "county":
            st = gid[:2]
            g3.update(sub=f"All counties in {state_label(st)}", legend=state_label(st, short=True),
                      pos=GROUP_IDX["county_by_state"].get(st))
        else:
            cty, st = gid[:5], gid[:2]
            g3.update(sub=f"All tracts in {place_name('county', cty)}",
                      legend=place_name("county", cty), pos=GROUP_IDX["tract_by_county"].get(cty))
            g4.update(sub=f"All tracts in {state_label(st)}", legend=state_label(st, short=True),
                      pos=GROUP_IDX["tract_by_state"].get(st))
        return [g1, g2, g3, g4]

    def class_dist(codes: np.ndarray):
        codes = codes[~np.isnan(codes)]
        if not len(codes):
            return None, 0
        vc = pd.Series(codes.astype(int)).value_counts()
        return vc, int(len(codes))

    def summarize(level: str, var: str, pos, single: bool) -> dict:
        """Value for one polygon (single) or a group summary."""
        m = VARMETA[level][var]
        if var == "pct_high_priority":                    # pooled over the tracts
            codes = class_codes()[tracts_in_counties(pos)]
            vc, n = class_dist(codes)
            share = (vc.get(1, 0) / n * 100) if n else None
            return {"text": fmt_value(m, share), "n": n, "kind": "pooled", "dist": vc}
        s = series(level, var)
        if single:
            return {"text": fmt_value(m, s.iloc[pos]), "value": s.iloc[pos]}
        sub = s.iloc[pos]
        if m["type"] == "nominal":
            vc = sub.dropna().value_counts()
            n = int(vc.sum())
            if not n:
                return {"text": "—", "n": 0, "dist": None}
            return {"text": str(vc.index[0]), "share": vc.iloc[0] / n * 100, "n": n, "dist": vc}
        return {"text": fmt_value(m, sub.median()), "n": int(sub.notna().sum())}

    # =======================================================================
    # Map
    # =======================================================================
    @reactive.effect
    async def _draw_map():
        if not map_visible():
            return
        level, var = input.level(), cur_var()
        state = input.state()
        gj, feats = _geo()
        m = VARMETA[level][var]

        ids = [f["properties"]["GEOID"] for f in gj["features"]]
        names = [f["properties"].get("NAME", f["properties"]["GEOID"]) for f in gj["features"]]
        vals = series(level, var).reindex(ids)
        text = [fmt_value(m, v) for v in vals]

        cb = dict(thickness=12, len=0.42, lenmode="fraction",
                  x=0.995, xanchor="right", y=0.03, yanchor="bottom",
                  bgcolor="rgba(255,255,255,0.88)", outlinewidth=0, xpad=10, ypad=8)
        title = m["label"].replace(" (", "<br>(", 1)
        if var == "priority_class":
            c = cut()
            title = (f"Priority class<br><sub>{MEASURES[c['measure']]} · B{c['b']} "
                     f"A{c['a']} R{c['r']} · {SCALES[c['scale']]}</sub>")
        elif var == "pct_high_priority":
            c = cut()
            title = (f"% tracts High priority<br><sub>{MEASURES[c['measure']]} · B{c['b']} "
                     f"A{c['a']} R{c['r']}</sub>")

        if m["type"] == "nominal":
            labels = m["levels"]
            posn = {lv: i for i, lv in enumerate(labels)}
            z = [posn.get(v) if isinstance(v, str) else None for v in vals]
            k = len(labels)
            choro = dict(z=z, zmin=-0.5, zmax=k - 0.5, colorscale=discrete_scale(m["colors"]),
                         colorbar=dict(**cb, title=dict(text=title, side="top"),
                                       tickvals=list(range(k)), ticktext=labels))
        else:
            z = [None if pd.isna(v) else float(v) for v in vals]
            choro = dict(z=z, colorscale=m.get("colorscale", "YlOrRd"),
                         colorbar=dict(**cb, title=dict(text=title, side="top")))
            if m["fmt"] == "pctl" or var == "pct_high_priority":
                choro.update(zmin=0, zmax=100)
            elif m["fmt"] == "index":
                lim = float(np.nanmax(np.abs(series(level, var)))) or 1.0
                choro.update(zmin=-lim, zmax=lim)

        with reactive.isolate():
            hl = _highlight_payload(feats, selected())
            show_mammo = bool(MAMMO_PATH.exists() and input.show_mammo())

        fig = go.Figure(data=[
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
        ])
        mammo = load_mammo()
        if mammo:  # trace index 2: always visible so its layer is created (on top) at draw time
            on = show_mammo
            fig.add_trace(go.Scattermap(
                lon=mammo["lon"] if on else [], lat=mammo["lat"] if on else [],
                text=mammo["text"] if on else [],
                mode="markers", name="Mammography facility", below="",
                marker=dict(size=7, color=MAMMO_COLOR, opacity=0.9),
                hovertemplate="%{text}<extra></extra>", showlegend=False,
            ))
        fig.update_layout(
                map=dict(
                style=MAP_STYLE, center=dict(lat=38.5, lon=-96.5), zoom=3,
                layers=([dict(sourcetype="geojson", source=load_state_lines(), type="line",
                              color=STATE_LINE_COLOR, opacity=STATE_LINE_OPACITY,
                              line=dict(width=STATE_LINE_WIDTH))]
                        if load_state_lines() else []),
            ),
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
            on, m = bool(input.show_mammo()), load_mammo()
            await session.send_custom_message("set_mammo", {
                "index": 2,
                "lon": m["lon"] if on else [], "lat": m["lat"] if on else [],
                "text": m["text"] if on else [],
            })

    # =======================================================================
    # Info boxes
    # =======================================================================
    def _class_rows(dist, n):
        color = dict(zip(range(1, len(CLASS_COLORS) + 1), CLASS_COLORS))
        rows = []
        for code, cnt in list(dist.items())[:MAX_TABLE_ROWS]:
            rows.append(ui.tags.tr(
                ui.tags.td(ui.span(class_="chip", style=f"background:{color.get(code, '#ccc')}"),
                           CLASS_LEVELS[code - 1]),
                ui.tags.td(f"{cnt / n * 100:.0f}%", class_="num")))
        return ui.tags.table(*rows, class_="vb-table")

    def _check_rows(pos: int):
        """Selected tract, priority class: why it got its class at these cutoffs."""
        c, t = cut(), DF["tract"]
        row = t.iloc[pos]
        items = [(f"Burden ({MEASURES[c['measure']].lower()})", f"pctl_{c['measure']}",
                  f"lvl_{c['measure']}", c["b"]),
                 ("Access (distance)", f"pctl_access_{c['scale']}", f"lvl_access_{c['scale']}", c["a"]),
                 ("Barriers", f"pctl_barriers_{c['scale']}", f"lvl_barriers_{c['scale']}", c["r"])]
        trs = []
        for label, pc, lc, cutv in items:
            p, lv = row.get(pc), row.get(lc)
            if pd.isna(lv):
                txt, cls = ("suppressed" if label.startswith("Burden") else "no data"), None
            else:
                hit = lv >= cutv
                txt = f"{p:.0f} {'≥' if hit else '<'} {cutv} {'✓' if hit else ''}"
                cls = "ok" if hit else None
            trs.append(ui.tags.tr(ui.tags.td(label), ui.tags.td(txt, class_=f"num {cls or ''}")))
        return ui.tags.table(*trs, class_="vb-table")

    def _make_box(d: dict, theme):
        level, var = input.level(), cur_var()
        m = VARMETA[level][var]
        pos, single = d["pos"], d["single"]
        units = "counties" if level == "county" else "tracts"
        
        title = d["title"]
        if d["single"] and selected() is not None:
            title = ui.div(
                ui.span(d["title"]),
                ui.tags.button("✕ Clear selection", class_="clear-sel", type="button",
                               title="Clear selection (Esc)"),
                class_="vb-title-row",
            )

        if pos is None or (not single and len(pos) == 0):
            return ui.value_box(title, "—", ui.div(d["sub"], class_="vb-sub"),
                                theme=theme, fill=False)

        s = summarize(level, var, pos, single)
        children = [ui.div(d["sub"], class_="vb-sub", title=d["sub"])]
        table = None

        if var == "priority_class" and single:
            table = _check_rows(pos)
        elif s.get("kind") == "pooled":
            children.append(ui.div(f"{s['n']:,} classified tracts", class_="vb-sub"))
            if s.get("dist") is not None:
                table = _class_rows(s["dist"], s["n"])
        elif m["type"] == "nominal" and not single:
            if s["n"]:
                children.append(ui.div(f"Most common: {s['share']:.0f}% of {s['n']:,} {units}",
                                       class_="vb-sub"))
                vc = s["dist"]
                if var == "priority_class":
                    vc.index = [CLASS_LEVELS.index(k) + 1 for k in vc.index]
                    table = _class_rows(vc, s["n"])
                else:
                    color = dict(zip(m["levels"], m["colors"]))
                    table = ui.tags.table(*[ui.tags.tr(
                        ui.tags.td(ui.span(class_="chip", style=f"background:{color.get(k, '#ccc')}"), k),
                        ui.tags.td(f"{c / s['n'] * 100:.0f}%", class_="num"))
                        for k, c in list(vc.items())[:MAX_TABLE_ROWS]], class_="vb-table")
        elif m["type"] != "nominal":
            if not single:
                children.append(ui.div(f"median · n = {s['n']:,}", class_="vb-sub"))
            ids = table_vars(level, var)
            if len(ids) > 1:
                trs = []
                for v in ids:
                    vs = summarize(level, v, pos, single)
                    trs.append(ui.tags.tr(ui.tags.td(short_label(level, v)),
                                          ui.tags.td(vs["text"], class_="num"),
                                          class_="sel" if v == var else None))
                table = ui.tags.table(*trs, class_="vb-table")
        if table is not None:
            children.append(table)

        value_cls = "txt" if m["type"] == "nominal" else None
        return ui.value_box(title, ui.span(s["text"], class_=value_cls), *children,
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
    # Page 2 overlay: Need profile
    # =======================================================================
    @render.text
    def profile_title():
        unit = "tract" if input.level() == "tract" else "county"
        hint = " · click a polygon to add it" if selected() is None else ""
        return f"Need profile · selected {unit} vs comparisons (| = cutoff){hint}"

    @reactive.effect
    async def _draw_profile():
        if page() != "map":
            return
        level, c = input.level(), cut()
        gs = groups()
        rows = PROFILE[level]
        cols = [r["by_scale"][c["scale"]] if r.get("by_scale") else r["id"] for r in rows]
        labels = [r["label"] + (" ◀" if r.get("measure") == c["measure"] else "") for r in rows]
        data = DF[level]

        fig = go.Figure()
        for i, d in enumerate(gs):
            pos = d["pos"]
            if pos is None or (not d["single"] and len(pos) == 0):
                continue
            if d["single"]:
                vals = [data.iloc[pos][col] for col in cols]
            else:
                vals = [data[col].iloc[pos].median() for col in cols]
            vals = [None if v is None or pd.isna(v) else float(v) for v in vals]
            fig.add_trace(go.Bar(
                y=labels, x=vals, orientation="h", name=d["legend"],
                marker_color=GROUP_COLORS[i],
                marker_line=dict(color="#7a1f1c", width=1.2) if i == 0 else None,
                hovertemplate=f"{d.get('hover', d['legend'])}<br>%{{y}}: %{{x:.0f}}<extra></extra>",
            ))
        # cutoff marks for the three classification dimensions
        cut_for = {"access": c["a"], "barriers": c["r"]}
        mx, my = [], []
        for r, lab in zip(rows, labels):
            if r["dim"] in cut_for:
                mx.append(cut_for[r["dim"]]); my.append(lab)
            elif r["dim"] == "burden" and r.get("measure") == c["measure"]:
                mx.append(c["b"]); my.append(lab)
        fig.add_trace(go.Scatter(
            x=mx, y=my, mode="markers", name="Cutoff",
            marker=dict(symbol="line-ns", size=22, line=dict(width=2.5, color=CUTOFF_MARK_COLOR)),
            hovertemplate="Cutoff: %{x}<extra></extra>",
        ))

        fig.update_layout(
            template="plotly_white", font=PLOT_FONT, barmode="group", bargap=0.2,
            bargroupgap=0.05, margin=dict(l=4, r=8, t=16, b=4),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(255,255,255,0.6)",
            xaxis=dict(range=[0, 100], title=None, tickvals=[0, 25, 50, 67, 80, 90, 100],
                       tickfont=dict(size=9), gridcolor="#e5e7eb"),
            yaxis=dict(autorange="reversed", automargin=True, tickfont=dict(size=10)),
                        legend=dict(orientation="h", x=0, xanchor="left", y=1.0, yanchor="bottom",
                        font=dict(size=9), itemsizing="constant", itemwidth=30,
                        tracegroupgap=0, bgcolor="rgba(0,0,0,0)"),
            showlegend=True,
        )
        await session.send_custom_message("draw_chart", {"id": "profile", "fig": _fig_payload(fig)})

    # =======================================================================
    # Page 3 overlay: scatterplot
    # =======================================================================
    @render.text
    def scatter_title():
        lvl = input.level()
        units = "counties" if lvl == "county" else "tracts"
        return (f"{short_label(lvl, cur_y())} vs {short_label(lvl, cur_x())} · all US {units}")

    @reactive.effect
    async def _draw_scatter():
        if page() != "barriers":
            return
        level, xv, yv = input.level(), cur_x(), cur_y()
        show_trend = input.trend()
        gid = selected()
        units = "counties" if level == "county" else "tracts"
        mx, my = VARMETA[level][xv], VARMETA[level][yv]
        xl, yl = short_label(level, xv), short_label(level, yv)

        df = DF[level][list(dict.fromkeys([xv, yv]))].dropna()
        x = df[xv].to_numpy(dtype=float)
        y = df[yv].to_numpy(dtype=float)
        names = county_names() if level == "county" else tract_names()
        labels = [names.get(g, g) for g in df.index]

        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=x, y=y, mode="markers", name=f"All US {units} (n = {len(df):,})",
            marker=dict(size=5 if level == "county" else 3, color=SCATTER_POINT_COLOR,
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
                name=f"Trend (r = {r:.2f})", line=dict(color=TREND_COLOR, width=2),
                hoverinfo="skip",
            ))

        note = "Click a polygon on the map to locate it here"
        note_color = "#6b7280"
        if gid:
            name = place_name(level, gid)
            if gid in df.index:
                sx, sy = float(df.at[gid, xv]), float(df.at[gid, yv])
                px_, py_ = (x < sx).mean() * 100, (y < sy).mean() * 100
                fig.add_hline(y=sy, line_width=1, line_dash="dot", line_color=SELECTED_COLOR)
                fig.add_vline(x=sx, line_width=1, line_dash="dot", line_color=SELECTED_COLOR)
                fig.add_trace(go.Scattergl(
                    x=[sx], y=[sy], mode="markers", name="Selected region",
                    marker=dict(size=13, color=SELECTED_COLOR, symbol="diamond",
                                line=dict(color="white", width=1.5)),
                    hovertemplate=f"<b>{name}</b><br>{xl}: {fmt_value(mx, sx)}"
                                  f"<br>{yl}: {fmt_value(my, sy)}<extra></extra>",
                ))
                note = f"{name}: above {px_:.0f}% of US {units} on X, {py_:.0f}% on Y"
                note_color = SELECTED_COLOR
            else:
                note = f"{name} has no data for {xl} and/or {yl}"
        fig.add_annotation(text=note, xref="paper", yref="paper", x=0, y=1.0, xanchor="left",
                           yanchor="bottom", showarrow=False, font=dict(size=10, color=note_color))
        fig.update_layout(
            template="plotly_white", font=PLOT_FONT, margin=dict(l=48, r=8, t=18, b=36),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(255,255,255,0.6)",
            xaxis=dict(title=dict(text=xl, font=dict(size=10)), zeroline=False),
            yaxis=dict(title=dict(text=yl, font=dict(size=10)), zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="right", x=1,
                        font=dict(size=9)),
            uirevision=f"{level}-{xv}-{yv}",
        )
        await session.send_custom_message("draw_chart", {"id": "scatter", "fig": _fig_payload(fig)})


app = App(app_ui, server, static_assets={"/plotlyjs": PLOTLY_JS_DIR})
