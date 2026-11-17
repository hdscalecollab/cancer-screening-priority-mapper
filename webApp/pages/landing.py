"""Landing page content.

The page is plain Shiny UI with no server logic of its own. app.py passes in the class
legend (from the variable catalog, so the colors match the map) and optional headline
numbers, and wires the two buttons ("go_map", "go_methods") to the navigation bar.
"""

from __future__ import annotations

from shiny import ui

TITLE = "Cancer Screening Priority Mapper"
PURPOSE = ("A flexible multi-scale geospatial decision support tool to identify "
           "high-priority cancer screening areas.")
BETA_TAG = "Beta version · Breast cancer"

# Three dimensions (Burden / Access / Barriers). Wording follows the project's methods
# (percentile cutoffs, urban/rural ranking); see docs/data_dictionary.md.
DIMENSIONS = [
    dict(name="Burden", level="County",
         question="Is late-stage breast cancer common here?",
         body=("Late-stage breast cancer, 2019–2023 (State Cancer Profiles). Choose the "
               "measure: incidence rate (relative burden), average annual cases (absolute "
               "burden) or percent diagnosed at a late stage."),
         note="Every tract in a county shares the county's burden value."),
    dict(name="Access", level="Census tract",
         question="How far is the nearest mammography facility?",
         body="Distance from the tract to the nearest FDA-certified mammography facility.",
         note="A longer distance means lower access."),
    dict(name="Barriers", level="Census tract",
         question="What else could keep women from getting screened?",
         body=("Four American Community Survey measures: women 35–74 without health "
               "insurance, households without a vehicle, women 25+ without a high school "
               "diploma, and limited English-speaking households."),
         note="Combined into a single barrier index (the average of the four percentiles)."),
]

# Example interventions for each priority class (project's Table 2).
CLASS_INFO = {
    "High priority": dict(
        pattern="Elevated burden · access gap · high barriers",
        intervention="Mobile mammography plus patient navigation"),
    "Access gap": dict(
        pattern="Elevated burden · access gap · lower barriers",
        intervention="New or mobile screening capacity; transportation"),
    "Barrier gap": dict(
        pattern="Elevated burden · good access · high barriers",
        intervention="Navigation, insurance enrollment, outreach"),
    "Monitor": dict(
        pattern="Elevated burden · good access · lower barriers",
        intervention="Look for other causes, such as follow-up after abnormal results"),
    "Lower priority": dict(
        pattern="Burden below the cutoff",
        intervention="Not in the highest-burden counties at this cutoff. This does not mean no need."),
    "Burden suppressed: gaps present": dict(
        pattern="County burden suppressed · access gap or high barriers",
        intervention="Consider access or barrier interventions; burden unknown"),
    "Burden suppressed: no gaps": dict(
        pattern="County burden suppressed · good access · lower barriers",
        intervention="Monitor as data allow"),
}

CAUTION = ("County burden is regional context, not a tract-level cancer rate. Classes show "
           "where screening gaps fall within high-burden counties. They do not estimate "
           "cancer risk for a tract or for any person.")


def _dimension_card(d: dict):
    return ui.div(
        ui.div(ui.span(d["name"], class_="dim-name"), ui.span(d["level"], class_="dim-level"),
               class_="dim-head"),
        ui.div(d["question"], class_="dim-question"),
        ui.p(d["body"], class_="dim-body"),
        ui.p(d["note"], class_="dim-note"),
        class_="lp-card",
    )


def _class_table(class_legend: list[tuple[str, str]]):
    rows = []
    for name, color in class_legend:
        info = CLASS_INFO.get(name)
        if info is None:
            continue
        rows.append(ui.tags.tr(
            ui.tags.td(ui.span(class_="lp-chip", style=f"background:{color}"),
                       ui.span(name, class_="lp-class-name")),
            ui.tags.td(info["pattern"]),
            ui.tags.td(info["intervention"]),
        ))
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Priority class"), ui.tags.th("Pattern"),
                                 ui.tags.th("Example intervention"))),
        ui.tags.tbody(*rows),
        class_="lp-table",
    )


def _stat_tiles(stats: list[dict]):
    return ui.div(*[ui.div(ui.div(s["value"], class_="stat-value"),
                           ui.div(s["label"], class_="stat-label"), class_="stat-tile")
                    for s in stats], class_="stat-row")


def landing_ui(class_legend: list[tuple[str, str]], stats: list[dict] | None = None,
               stats_caption: str = ""):
    """class_legend: [(class name, hex color)] in display order.
    stats: [{"value": "...", "label": "..."}] shown under "At a glance"; None hides the block."""
    return ui.div(
        # ---- Hero ----
        ui.div(
            ui.span(BETA_TAG, class_="lp-beta"),
            ui.h1(TITLE, class_="lp-title"),
            ui.p(PURPOSE, class_="lp-purpose"),
            ui.div(
                ui.input_action_button("go_map", "Explore the map", class_="btn btn-primary btn-lg"),
                ui.input_action_button("go_methods", "Data & methods",
                                       class_="btn btn-outline-secondary btn-lg"),
                class_="lp-cta",
            ),
            class_="lp-hero",
        ),
        # ---- How it works ----
        ui.div(
            ui.h2("How priority areas are identified"),
            ui.p("Burden, access and barriers are measured separately, not blended into one "
                 "score, because low access and high barriers call for different interventions.",
                 class_="lp-lead"),
            ui.div(*[_dimension_card(d) for d in DIMENSIONS], class_="lp-grid"),
            ui.p("Each measure is turned into a national percentile, and a place is flagged when "
                 "it is at or above the cutoff you choose: the 67th (top third), 80th or 90th "
                 "percentile. Access and barriers are compared with similar tracts, urban with "
                 "urban and rural with rural, so rural tracts are not all flagged for distance.",
                 class_="lp-lead lp-after"),
            class_="lp-section",
        ),
        # ---- Priority classes ----
        ui.div(
            ui.h2("Priority classes and example interventions"),
            ui.p("Each tract is placed in one class. Classes are read from top to bottom and "
                 "the first match applies.", class_="lp-lead"),
            _class_table(class_legend),
            class_="lp-section",
        ),
        # ---- At a glance (example; remove if not wanted) ----
        (ui.div(
            ui.h2("At a glance"),
            _stat_tiles(stats),
            ui.p(stats_caption, class_="lp-fine") if stats_caption else None,
            class_="lp-section",
        ) if stats else None),
        # ---- Use with care ----
        ui.div(
            ui.h2("Use with care"),
            ui.p(CAUTION, class_="lp-lead"),
            ui.p("Suggested uses: planning outreach, placing mobile screening, and prioritizing "
                 "navigation and insurance enrollment efforts.", class_="lp-lead"),
            class_="lp-section lp-caution",
        ),
        class_="lp-wrap",
    )
