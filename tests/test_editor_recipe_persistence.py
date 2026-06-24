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
# Generating from a preset that carries a recipe propagates it to the run
# meta.json, so reopening the chart in the editor seeds New chart / Add (#70).
# ---------------------------------------------------------------------------

def _chart_tab(tmp_path):
    from PyQt6.QtCore import QSettings
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings
    from ui.tabs.tab_chart import TabChart
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "projects"))
    fm = FileManager(s)
    tab = TabChart(ArgyllRunner(s), fm, s)
    tab._switch_mode("manual")
    return tab, fm


def test_preset_recipe_propagates_to_run_meta(qapp, tmp_path, monkeypatch):
    from workflow.chart_creator import ChartParams
    tab, fm = _chart_tab(tmp_path)
    recipe = {"mode": "generate", "cb": {"cube": True}, "sp": {"cube_n": 5},
              "edges_auto": True}
    # A user preset that bundles a recipe → tab remembers it as pending (Set B).
    pdata = {"editor_recipe": recipe, "targen_-f": 800}
    rec = pdata.get("editor_recipe")
    tab._pending_editor_recipe = rec if isinstance(rec, dict) and rec else None

    fm.set_target_name("MyProfile")
    run = fm.project().current_run()

    class _Spec:
        instrument_flag = "i1"
        paper_flag = "A4"
    monkeypatch.setattr(R.ChartSpec, "from_ti2", staticmethod(lambda p: _Spec()))
    tab._last_params = ChartParams()
    ti2 = run.dir / f"{run.stem}.ti2"
    ti2.write_text("x")
    tab._stamp_chart_meta(ti2)
    # The recipe rode into meta.json → the editor will seed New chart / Add.
    assert R.load_editor_recipe(ti2) == recipe


def test_plain_targen_chart_gets_no_recipe(qapp, tmp_path, monkeypatch):
    from workflow.chart_creator import ChartParams
    tab, fm = _chart_tab(tmp_path)
    tab._pending_editor_recipe = None          # Default / plain targen
    fm.set_target_name("Plain")
    run = fm.project().current_run()

    class _Spec:
        instrument_flag = "i1"
        paper_flag = "A4"
    monkeypatch.setattr(R.ChartSpec, "from_ti2", staticmethod(lambda p: _Spec()))
    tab._last_params = ChartParams()
    ti2 = run.dir / f"{run.stem}.ti2"
    ti2.write_text("x")
    tab._stamp_chart_meta(ti2)
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
    # Custom presets (ignoring the bundled built-ins, which are starred).
    custom = [n for n in d._preset_recipes if not n.startswith("★")]
    assert custom == ["with recipe"]


def test_builtin_fulllayout_recipes_appear_starred(qapp):
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    starred = [n for n in d._preset_recipes if n.startswith("★")]
    # Every Full-layout-setup chart (#63) carries a sidecar recipe.json.
    assert len(starred) == 15
    assert any("A4-924p" in n for n in starred)


def test_custom_identical_to_builtin_is_skipped(qapp, monkeypatch):
    import core.preset_store as ps
    from ui.tabs.tab_chart import builtin_recipe_choices
    name = "i1Pro A4-924p-2pages-Portrait-w7.5mm"
    rec = builtin_recipe_choices()[name]   # the preset's bundled sidecar recipe
    monkeypatch.setattr(ps, "load_presets", lambda tab, settings=None:
                        {name: {"editor_recipe": rec}})
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    assert f"★ {name}" in d._preset_recipes   # built-in shown
    assert name not in d._preset_recipes       # identical custom dropped


def test_custom_differing_from_builtin_is_kept(qapp, monkeypatch):
    import core.preset_store as ps
    name = "i1Pro A4-924p-2pages-Portrait-w7.5mm"
    monkeypatch.setattr(ps, "load_presets", lambda tab, settings=None:
                        {name: {"editor_recipe": {"mode": "generate",
                                                  "sp": {"cube_n": 3}}}})
    d = _NewChartDialog(Path("/x"), _FakeSettings())
    assert f"★ {name}" in d._preset_recipes   # built-in
    assert name in d._preset_recipes           # custom kept (different recipe)


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
