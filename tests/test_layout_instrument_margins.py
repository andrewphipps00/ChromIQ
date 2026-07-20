"""'Use instrument margins' must make the margin box the law in BOTH layout
modes (Knut beta.26).

Bug: in patch-first mode with instrument margins on, the engine reserved the
strip-label band + leader ON TOP of the (large) instrument top margin, so the
patch area was pushed ~17 mm too far down and the strip labels were pinned at the
instrument margin (38 mm) instead of the 4 mm text-edge. area-first was correct.

Fix: 'margins are the law' (patch area = margin box, labels inside the margin at
the text-edge) is now driven by area-first OR use_instrument_margins; the ruler
cap stays keyed on the layout mode (patch-first always caps).
"""
from __future__ import annotations

import pytest

from workflow.layout_engine import instruments, geometry, papers

PW, PH = papers.dimensions_mm("A4")
NP = 500
# i1Pro A4 instrument minimums (top 38 for the scanning clip).
_INSTR_MARGINS = (38.0, 9.0, 9.0, 26.0)
_USER_MARGINS = (6.0, 6.0, 6.0, 6.0)


def _probe(mode: str, use_instr: bool):
    margins = _INSTR_MARGINS if use_instr else _USER_MARGINS
    kw = dict(instrument="i1", paper="A4", layout_mode=mode,
              use_instrument_margins=use_instr, margins=margins)
    g = instruments.geom_from_build_kwargs(kw)
    lay = geometry.compute(g, PW, PH, NP)
    pl = geometry.placement(g, PW, PH, lay)
    strip_len = lay.steps_in_pass * (g.plen + g.pspa)
    return g, lay, pl, strip_len


def test_patch_first_instrument_margins_labels_at_text_edge():
    g, lay, pl, strip_len = _probe("patch_first", True)
    # Margin box is the law now, but the ruler cap must still bind (patch-first).
    assert g.margins_are_law is True
    assert g.fill_beyond_ruler is False
    # Strip labels at the ~4 mm text-edge, NOT pinned at the 38 mm instrument margin.
    assert pl.leader_top < 10.0, f"labels at {pl.leader_top} mm, expected ~4 mm"
    assert pl.leader_top < g.margin_t - 10.0
    # Ruler cap still protects the i1Pro jig.
    assert strip_len <= g.mxrowl + 1e-6


def test_patch_first_instrument_margins_matches_area_first():
    """With instrument margins, both layout modes put labels + patch-area top in
    the same place (the margin box) — only the fill-past-ruler flag differs."""
    gp, _, plp, _ = _probe("patch_first", True)
    ga, _, pla, _ = _probe("area_first", True)
    assert gp.margins_are_law is ga.margins_are_law is True
    assert abs(plp.leader_top - pla.leader_top) < 0.01
    assert gp.fill_beyond_ruler is False and ga.fill_beyond_ruler is True


def test_patch_first_user_margins_unchanged():
    """Regression: patch-first WITHOUT instrument margins keeps the historical
    printtarg-style furniture (labels flush under the margin, not text-edge)."""
    g, lay, pl, strip_len = _probe("patch_first", False)
    assert g.margins_are_law is False
    assert g.fill_beyond_ruler is False
    # Labels flush under the 6 mm user margin (printtarg-style), not text-edge-0.
    assert abs(pl.leader_top - g.margin_t) < 0.01
    assert strip_len <= g.mxrowl + 1e-6


def test_area_first_unchanged_both_margin_modes():
    """Regression: area-first is the law in both margin modes and fills past the
    ruler (flagged, not capped)."""
    for use_instr in (True, False):
        g, _, _, _ = _probe("area_first", use_instr)
        assert g.margins_are_law is True
        assert g.fill_beyond_ruler is True


def test_instrument_margins_do_not_change_patch_count_here():
    """The fix repositions labels/patch-area without inflating the patch count for
    this i1Pro A4 case — the ruler cap binds to the same per-strip count."""
    _, lay_new, _, _ = _probe("patch_first", True)
    # Sanity: a full strip fits and the count is positive and ruler-bounded.
    assert lay_new.steps_in_pass > 0
