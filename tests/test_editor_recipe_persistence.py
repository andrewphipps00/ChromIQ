"""Per-chart "creation recipe" persistence (feature 1).

The New chart / Add window state that produced a chart is stored on the chart
(meta.json ``editor_recipe``) so it can be reloaded into those windows later to
tweak / recreate the design — separate from ``editor_layout`` (the printtarg
layout the Create Chart tab edits).
"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from workflow import ti2_relayout as R  # noqa: E402
from ui.dialogs.ti2_relayout_dialog import (  # noqa: E402
    _AddPatchesDialog, _NewChartDialog,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


# ---------------------------------------------------------------------------
# meta.json round-trip
# ---------------------------------------------------------------------------

def test_recipe_round_trips_through_meta():
    with tempfile.TemporaryDirectory() as tmp:
        ti2 = Path(tmp) / "chart.ti2"
        ti2.write_text("")  # only the parent folder's meta.json is used
        spec = R.ChartSpec.new("i1", "A4")
        recipe = {"mode": "generate", "cb": {"cube": True},
                  "sp": {"cube_n": 5}, "edges_auto": True}
        R.save_editor_meta(ti2, spec, R.LayoutOptions(), "mychart", recipe=recipe)
        assert R.load_editor_recipe(ti2) == recipe


def test_recipe_none_preserves_existing():
    # A layout-only save (recipe=None) must not wipe a stored recipe.
    with tempfile.TemporaryDirectory() as tmp:
        ti2 = Path(tmp) / "chart.ti2"
        ti2.write_text("")
        spec = R.ChartSpec.new("i1", "A4")
        recipe = {"mode": "generate", "sp": {"cube_n": 7}}
        R.save_editor_meta(ti2, spec, R.LayoutOptions(), "c", recipe=recipe)
        R.save_editor_meta(ti2, spec, R.LayoutOptions(), "c", recipe=None)
        assert R.load_editor_recipe(ti2) == recipe


def test_divergence_layout_save_updates_set_a_keeps_set_b():
    """#54: a layout-only save (a Create Chart printtarg edit, recipe=None)
    updates editor_layout (Set A) yet preserves editor_recipe (Set B), so the
    two data sets diverge exactly as specified."""
    with tempfile.TemporaryDirectory() as tmp:
        ti2 = Path(tmp) / "chart.ti2"
        ti2.write_text("")
        spec = R.ChartSpec.new("i1", "A4")
        recipe = {"mode": "generate", "sp": {"cube_n": 8}}
        R.save_editor_meta(ti2, spec, R.LayoutOptions(margin_mm=6), "c",
                           recipe=recipe)
        # Create Chart changes the margin and regenerates → layout-only save.
        R.save_editor_meta(ti2, spec, R.LayoutOptions(margin_mm=12), "c",
                           recipe=None)
        opts, _ = R.load_editor_meta(ti2)
        assert opts.margin_mm == 12                  # Set A followed the edit
        assert R.load_editor_recipe(ti2) == recipe   # Set B stayed pristine


def test_recipe_absent_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        ti2 = Path(tmp) / "chart.ti2"
        ti2.write_text("")
        R.save_editor_meta(ti2, R.ChartSpec.new("i1", "A4"),
                           R.LayoutOptions(), "c")  # no recipe given
        assert R.load_editor_recipe(ti2) is None


# ---------------------------------------------------------------------------
# Dialog: produce a recipe, and reopen pre-loaded with one
# ---------------------------------------------------------------------------

def test_new_chart_reports_and_reapplies_recipe(qapp):
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    d._mode_generate.setChecked(True)
    d._gen_cube_n.setValue(5)
    recipe = d._collect_gen_state()
    assert recipe["sp"]["cube_n"] == 5

    reopened = _NewChartDialog(Path("/x"), _FakeSettings(), initial_recipe=recipe)
    assert reopened._gen_cube_n.value() == 5


def test_add_dialog_prefers_chart_recipe(qapp):
    recipe = {"cb": {n: False for n in _AddPatchesDialog._GEN_CHECKS},
              "sp": {"cube_n": 5}, "edges_auto": True}
    recipe["cb"]["cube"] = True
    dlg = _AddPatchesDialog(_FakeSettings(), initial_recipe=recipe)
    assert dlg._gen_cube_n.value() == 5


# ---------------------------------------------------------------------------
# #55 — "Load setup from preset" dropdown
# ---------------------------------------------------------------------------

def test_dropdown_lists_only_presets_with_recipe(qapp, monkeypatch):
    recipe = {"mode": "generate", "cb": {"cube": True}, "sp": {"cube_n": 5},
              "edges_auto": True}
    import core.preset_store as ps
    monkeypatch.setattr(ps, "load_presets", lambda tab, settings=None: {
        "with recipe": {"editor_recipe": recipe},
        "empty recipe": {"editor_recipe": {}},      # skipped
        "no recipe": {"targen_-f": 800},            # skipped
    })
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    assert list(d._preset_recipes) == ["with recipe"]
    # None + the one qualifying preset
    assert d._preset_setup_combo.count() == 2


def test_dropdown_select_applies_recipe(qapp, monkeypatch):
    recipe = {"mode": "generate", "cb": {"cube": True}, "sp": {"cube_n": 5},
              "edges_auto": True}
    import core.preset_store as ps
    monkeypatch.setattr(ps, "load_presets", lambda tab, settings=None:
                        {"p": {"editor_recipe": recipe}})
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    d._gen_cube_n.setValue(8)                       # move away from the recipe
    idx = d._preset_setup_combo.findData("p")
    d._on_preset_setup_selected(idx)
    assert d._gen_cube_n.value() == 5               # recipe applied
