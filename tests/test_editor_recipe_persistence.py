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
