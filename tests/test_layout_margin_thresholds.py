"""The layout engine honours the user's margin thresholds (#93).

Thresholds (Preferences → Margin Thresholds) are per-side MINIMUMS. The engine
raises the page margins so the realised patch area meets them, before anything
is rendered — so both the capacity estimate and the chart obey the rule. Until
now the engine ignored them, so an i1Pro chart could land inside a defined
minimum (e.g. a 27.8 mm top run-up under a 38 mm rule)."""
from __future__ import annotations

import pytest

from core.settings import (
    canonical_paper_name,
    margin_combo_key,
    thresholds_for_combo,
)
from workflow.layout_engine import geometry, instruments, margins_fit, papers
from workflow.layout_engine.presets import default_recipe


def _insets(kw):
    geom = instruments.geom_from_build_kwargs(kw)
    w, h = papers.dimensions_mm(kw["paper"])
    cap = geometry.patches_per_sheet(geom, w, h)
    lay = geometry.compute(geom, w, h, cap)
    L, R, T, B = geometry.realized_margins_mm(geom, w, h, lay)
    return {"L": L, "R": R, "T": T, "B": B}, cap


def test_clamp_raises_top_to_meet_threshold():
    # patch ×0.95, 10 mm margins → a ~37 mm top run-up; threshold wants 38.
    from dataclasses import replace
    r = replace(default_recipe("i1", "A4", mode="clip"), pscale=0.95,
                margin_top=10, margin_right=10, margin_bottom=10, margin_left=10)
    kw = r.build_kwargs()
    before, _ = _insets(kw)
    assert before["T"] < 38.0

    thr = {"L": 26, "R": 9, "T": 38, "B": 10}
    kw2, notes = margins_fit.clamp_margins_to_thresholds(kw, thr)
    after, _ = _insets(kw2)
    for side, want in thr.items():
        assert after[side] >= want - 0.05, (side, after[side], want)
    assert any("Top" in n for n in notes)


def test_clamp_is_noop_without_thresholds():
    kw = default_recipe("i1", "A4", mode="clip").build_kwargs()
    out, notes = margins_fit.clamp_margins_to_thresholds(kw, None)
    assert out is kw and notes == []
    out, notes = margins_fit.clamp_margins_to_thresholds(kw, {"T": 0, "R": ""})
    assert notes == []


def test_clamp_reduces_capacity_when_it_must_reserve_more():
    # A big top minimum forces a larger top margin → fewer rows fit.
    kw = default_recipe("i1", "A4", mode="clip").build_kwargs()
    _, cap_before = _insets(kw)
    kw2, _ = margins_fit.clamp_margins_to_thresholds(kw, {"T": 60})
    after, cap_after = _insets(kw2)
    assert after["T"] >= 60 - 0.05
    assert cap_after <= cap_before


def test_geom_from_build_kwargs_applies_thresholds():
    """The chokepoint applies thresholds, so capacity reflects them too."""
    kw = default_recipe("i1", "A4", mode="clip").build_kwargs()
    w, h = papers.dimensions_mm("A4")
    plain = geometry.patches_per_sheet(
        instruments.geom_from_build_kwargs(kw), w, h)
    clamped = geometry.patches_per_sheet(
        instruments.geom_from_build_kwargs(kw, thresholds={"T": 60}), w, h)
    assert clamped <= plain


def test_thresholds_for_combo_resolves_key():
    table = {margin_combo_key("i1Pro", "A4", "Portrait"): {"T": 38, "R": 9}}
    got = thresholds_for_combo(table, "i1", 210.0, 297.0)
    assert got == {"T": 38, "R": 9}
    # landscape page → Landscape key (absent here → None)
    assert thresholds_for_combo(table, "i1", 297.0, 210.0) is None
    # unknown instrument / paper → None
    assert thresholds_for_combo(table, "i1", 999.0, 999.0) is None


def test_canonical_paper_name_tolerant():
    assert canonical_paper_name(210.0, 297.0) == "A4"
    assert canonical_paper_name(297.0, 210.0) == "A4"      # orientation-agnostic
    assert canonical_paper_name(209.0, 298.0) == "A4"      # within tolerance
    assert canonical_paper_name(500.0, 500.0) is None
