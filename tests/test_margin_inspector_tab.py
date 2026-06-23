"""Create Chart tab wiring for the margin inspector.

Regression: restoring the saved guide-line checkbox state at build time emits
``guides_toggled`` → ``_update_margin_inspector``; that must not run before
``_margin_tiffs`` is initialised (the AttributeError seen on first launch with
``margin_guides_show`` stored True).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


def _printtarg():
    p = shutil.which("printtarg") or "/Applications/Argyll/bin/printtarg"
    return p if Path(p).is_file() else None


_PT = _printtarg()
requires_argyll = pytest.mark.skipif(_PT is None, reason="printtarg not installed")
_I1_TI1 = (Path(__file__).resolve().parent.parent
           / "assets/charts/knut/rgb/fulllayout/fls_i1pro_a4_484p_1page_portrait/chart.ti1")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _tab(tmp_path, **prefs):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    for k, v in prefs.items():
        s.set(k, v)
    return TabChart(ArgyllRunner(s), FileManager(s), s)


def test_tab_builds_with_guides_enabled(qapp, tmp_path):
    """Building the tab with the guide checkbox stored ON must not crash."""
    tab = _tab(tmp_path, margin_guides_show=True)
    assert tab._margin_tiffs == []
    assert tab._margin_panel.guides_enabled() is True


def test_toggling_guides_with_no_chart_is_safe(qapp, tmp_path):
    """Toggling the guide checkbox before any chart is generated is a no-op,
    not an AttributeError."""
    tab = _tab(tmp_path, margin_guides_show=False)
    tab._on_margin_guides_toggled(True)      # the crashing path
    tab._update_margin_inspector()
    assert tab._margin_panel is not None


def test_refresh_settings_before_generate_is_safe(qapp, tmp_path):
    """The post-Preferences refresh hook is safe with no chart loaded."""
    tab = _tab(tmp_path)
    tab.refresh_margin_inspector_settings()
    assert tab._margin_tiffs == []


def test_current_margin_combo_reports_selection(qapp, tmp_path):
    """#80: the tab reports its instrument + paper + orientation so Preferences
    can preselect the matching Margin Thresholds row."""
    tab = _tab(tmp_path, chart_instrument="i1", chart_paper="A4")
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("i1"))
    tab._paper_combo.setCurrentIndex(tab._paper_combo.findData("A4"))
    assert tab.current_margin_combo() == ("i1Pro", "A4", "Portrait")


def test_settings_dialog_preselects_margin_combo(qapp, tmp_path):
    """The Margin Thresholds tab opens on the combo it was handed (#80)."""
    from core.settings import AppSettings
    from ui.dialogs.settings_dialog import SettingsDialog
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    dlg = SettingsDialog(s, None, margin_combo=("ColorMunki", "A3", "Landscape"))
    assert dlg._margin_instr.currentText() == "ColorMunki"
    assert dlg._margin_paper.currentText() == "A3 Landscape"


@requires_argyll
def test_changing_threshold_moves_preview_guides(qapp, tmp_path):
    """#81: editing the threshold for the loaded chart's combo and refreshing
    must re-push new guide-line positions to the preview."""
    shutil.copy(_I1_TI1, tmp_path / "chart.ti1")
    subprocess.run([_PT, "-ii1", "-pA4", "-t300", "-P", "-L", "-M8", "chart"],
                   cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tif = tmp_path / "chart.tif"

    tab = _tab(tmp_path, margin_guides_show=True, chart_instrument="i1")
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("i1"))
    tab._set_margin_chart([tif], tmp_path / "chart.ti2")
    guides_before = list(tab._preview._margin_guides)
    assert guides_before, "guides should be drawn for an i1Pro A4 chart"

    table = tab._settings.get_margin_thresholds()
    table["i1Pro|A4 Portrait"] = {"L": 45, "R": 45, "T": 45, "B": 45, "desc": ""}
    tab._settings.set_margin_thresholds(table)
    tab.refresh_margin_inspector_settings()
    guides_after = list(tab._preview._margin_guides)

    assert guides_after != guides_before, "guide positions must follow the new thresholds"
