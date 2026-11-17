"""Data & Methods page (placeholder).

Planned: source table (source, geography, vintage, link, limitations), links to the process
documentation, the plain-language methods summary, and the limitations / appropriate-use text.
"""

from __future__ import annotations

from shiny import ui

REPO = "https://github.com/hdscalecollab/cancer-screening-priority-mapper"


def methods_ui():
    return ui.div(
        ui.h1("Data & methods", class_="lp-title lp-title-sm"),
        ui.p("This page will list every data source with its link, vintage and limitations, "
             "and link to the process documentation.", class_="lp-lead"),
        ui.span("Coming soon", class_="lp-beta"),
        ui.h2("In the meantime", class_="lp-h2-gap"),
        ui.tags.ul(
            ui.tags.li(ui.tags.a("Project README", href=REPO, target="_blank", rel="noopener")),
            ui.tags.li(ui.tags.a("Data dictionary", href=f"{REPO}/blob/main/docs/data_dictionary.md",
                                 target="_blank", rel="noopener")),
            class_="lp-links",
        ),
        class_="lp-wrap lp-methods",
    )
