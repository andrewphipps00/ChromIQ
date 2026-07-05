"""Regression tests for two coupled bugs Knut hit (Set A / Set B drift):

1. Suggested-preset-name picked up a *stale* patch count after Overwrite-from-
   editor, because apply_external_chart / reflect_loaded_chart forgot to clear
   _builtin_ti1_path — so a previously-loaded built-in's .ti1 shadowed the live
   chart in _loaded_ti1_patch_count (1168 instead of 1575).

2. The stored creation recipe (Set B) kept the New-chart layout values even
   after the user edited the printtarg panel (Set A), so a chart whose -a was
   dialled back to fit the page still saved 1.15. _reconcile_recipe_with_chart
   re-syncs the recipe from the live chart at save time.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
import workflow.ti2_relayout as R  # noqa: E402
from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    return s


@pytest.fixture()
def tab(qapp, settings):
    return TabChart(ArgyllRunner(settings), FileManager(settings), settings)


def _mk_ti1(p: Path, n: int) -> Path:
    p.write_text(f"CTI1\n\nNUMBER_OF_SETS {n}\nBEGIN_DATA\nEND_DATA\n",
                 encoding="latin-1")
    return p


# --- Bug 1: stale built-in .ti1 must not shadow the applied/reflected chart ---

def test_apply_external_chart_clears_stale_builtin_ti1(tab, tmp_path, monkeypatch):
    tab._builtin_ti1_path = _mk_ti1(tmp_path / "builtin.ti1", 1168)
    # The actual file import is not what we're testing — stub that edge only.
    monkeypatch.setattr(tab, "_import_applied_chart", lambda *a, **k: None)
    tab.apply_external_chart(tmp_path / "stage", "chart")
    assert tab._builtin_ti1_path is None


def test_reflect_loaded_chart_clears_stale_builtin_ti1(tab, tmp_path, settings):
    settings.set("reflect_backfill_hide_warning", True)  # skip the modal note
    tab._builtin_ti1_path = _mk_ti1(tmp_path / "builtin.ti1", 1168)
    ti2 = tmp_path / "loaded.ti2"
    ti2.write_text("CTI2\n", encoding="latin-1")
    tab.reflect_loaded_chart(ti2, [])
    assert tab._builtin_ti1_path is None


def test_name_suggestion_uses_live_chart_not_stale_builtin(tab, tmp_path):
    """The exact bug: built-in (1168) loaded, then a 1575 chart applied → the
    suggested name must read 1575, never the shadowed 1168."""
    tab._switch_mode("manual")
    if tab._manual_instr_pw:
        tab._manual_instr_pw.set_value("CM")
    if tab._manual_paper_pw:
        tab._manual_paper_pw.set_value("A3")
    if tab._manual_pages_spin:
        tab._manual_pages_spin.setValue(3)
    tab._preset_ti1_path = None
    tab._builtin_ti1_path = None  # cleared by apply_external_chart in real flow
    tab._current_ti1_path = _mk_ti1(tmp_path / "applied.ti1", 1575)
    assert tab._loaded_ti1_patch_count() == 1575
    assert "1575p" in tab._suggest_target_name()
    assert "1168p" not in tab._suggest_target_name()


# --- Bug 2: recipe (Set B) re-synced from the live chart (Set A) at save ------

def test_reconcile_recipe_updates_stale_layout_and_fill_to():
    recipe = {
        "mode": "generate",
        "cb": {"cube": True},
        "sp": {"fill_to": 495, "cube_n": 9},
        "instr": "CM", "paper": "A3",
        "layout": {"patch_scale": 1.15, "margin": 5, "td": True},  # stale
    }
    ns = SimpleNamespace(
        _chart_recipe=recipe,
        _options=R.LayoutOptions(patch_scale=1.0, margin_mm=6,
                                 triple_density=False, dpi=200),
        _spec=R.ChartSpec.new("i1", "A4"),
        _program_from_grid=lambda: [("p",)] * 484,
        _engine_active=lambda: False,     # printtarg chart → full #92 sync
    )
    Ti2RelayoutDialog._reconcile_recipe_with_chart(ns)
    assert recipe["layout"]["patch_scale"] == 1.0     # was 1.15
    assert recipe["layout"]["margin"] == 6            # was 5
    assert recipe["layout"]["td"] is False            # was True
    assert recipe["sp"]["fill_to"] == 484             # was 495 → realised count
    assert recipe["instr"] == "i1" and recipe["paper"] == "A4"
    assert recipe["cb"] == {"cube": True}             # colour-set spec untouched


def test_reconcile_recipe_noop_without_recipe():
    # A chart with no stored recipe (loaded from a recipe-less file) is fine.
    ns = SimpleNamespace(_chart_recipe=None, _options=R.LayoutOptions(),
                         _spec=R.ChartSpec.new("i1", "A4"),
                         _program_from_grid=lambda: [],
                         _engine_active=lambda: False)
    Ti2RelayoutDialog._reconcile_recipe_with_chart(ns)  # must not raise
