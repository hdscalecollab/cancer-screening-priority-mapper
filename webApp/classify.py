"""
classify.py — priority-class rule shared by app.py and prep_mvp_data.py.

Follows "Priority classes" in data_dictionary.md. Rules are applied in order and
the first match sets the class:

    NA (no data)                     access or barrier level missing
    6 Burden suppressed: gaps present  burden NA; access gap OR high barriers
    7 Burden suppressed: no gaps       burden NA; no access gap AND low barriers
    5 Lower priority                   burden level < burden cutoff
    1 High priority                    elevated burden; access gap; high barriers
    2 Access gap                       elevated burden; access gap; low barriers
    3 Barrier gap                      elevated burden; no access gap; high barriers
    4 Monitor                          elevated burden; no access gap; low barriers

A domain is "high" at a cutoff when its level (lvl_*: 0 / 67 / 80 / 90) >= cutoff.
"""

from __future__ import annotations

import numpy as np

CLASS_LEVELS = [
    "High priority", "Access gap", "Barrier gap", "Monitor", "Lower priority",
    "Burden suppressed: gaps present", "Burden suppressed: no gaps",
]   # index + 1 = class code (class_codes.csv)
CLASS_COLORS = ["#B2182B", "#F4A261", "#8E5EA2", "#F6D55C", "#9DC3E6", "#7F7F7F", "#D9D9D9"]
CUTOFFS = (67, 80, 90)


def level_of(pctl) -> np.ndarray:
    """Highest cutoff (0 / 67 / 80 / 90) a percentile clears; NaN stays NaN."""
    p = np.asarray(pctl, dtype=float)
    out = np.where(np.isnan(p), np.nan, 0.0)
    for c in CUTOFFS:
        out = np.where(p >= c - 1e-9, float(c), out)
    return out


def classify_at(lvl_burden, lvl_access, lvl_barriers, burden_cut: int, access_cut: int,
                barrier_cut: int) -> np.ndarray:
    """Vectorized class codes (1-7, NaN = no data) at the given cutoffs."""
    lb = np.asarray(lvl_burden, dtype=float)
    la = np.asarray(lvl_access, dtype=float)
    lr = np.asarray(lvl_barriers, dtype=float)
    code = np.full(lb.shape, np.nan)

    has = ~np.isnan(la) & ~np.isnan(lr)
    with np.errstate(invalid="ignore"):
        gap_a = la >= access_cut
        gap_r = lr >= barrier_cut
        elevated = lb >= burden_cut
    supp = has & np.isnan(lb)
    ok = has & ~np.isnan(lb)

    code[supp & (gap_a | gap_r)] = 6
    code[supp & ~(gap_a | gap_r)] = 7
    code[ok & ~elevated] = 5
    el = ok & elevated
    code[el & gap_a & gap_r] = 1
    code[el & gap_a & ~gap_r] = 2
    code[el & ~gap_a & gap_r] = 3
    code[el & ~gap_a & ~gap_r] = 4
    return code
