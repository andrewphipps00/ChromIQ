"""The Chart-layout-information panel predicts the grid BEFORE a chart is
generated, when the ChromIQ layout engine is active (#93)."""
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


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


def test_layout_info_predicted_before_generate_with_engine(qapp, tmp_path):
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    assert tab._margin_tiffs == []          # nothing generated yet
    tab._update_patch_count()               # Guided predictor runs
    panel = tab._layout_info_panel
    # The estimate column shows a real prediction; on-screen is dashed (no chart).
    assert panel._placeholder.isHidden()
    assert not panel._table.isHidden()
    total = int(panel._estimate_labels["total"].text())
    rows = int(panel._estimate_labels["rows"].text())
    cols = int(panel._estimate_labels["cols"].text())
    assert total > 0 and rows > 0 and cols > 0
    assert panel._actual_labels["total"].text() == "—"


def test_guided_predicts_regardless_of_manual_toggle(qapp, tmp_path):
    """Guided always lays engine instruments out with the ChromIQ engine, so the
    layout-info estimate is shown even when the Manual 'use engine' toggle is
    off — it must model what will actually build (Basti)."""
    tab = _tab(tmp_path, use_chromiq_layout_engine=False)   # Manual toggle OFF
    tab._update_patch_count()                                # Guided predictor
    panel = tab._layout_info_panel
    assert panel._placeholder.isHidden()                     # predicted, not blank
    assert int(panel._estimate_labels["total"].text()) > 0


def test_estimate_lays_out_onscreen_patch_count(qapp, tmp_path):
    """When a chart is on screen, the estimate lays out the SAME patch count
    under the current settings — not a capacity-fill of pages_req pages (#93,
    Knut beta-13: estimate pages/total were wrong for a loaded chart)."""
    from workflow.layout_engine import geometry, instruments, papers
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    panel = tab._layout_info_panel
    geom = instruments.geom_from_build_kwargs(
        {"instrument": "i1", "paper": "A4"})
    per = geometry.patches_per_sheet(geom, *papers.dimensions_mm("A4"))
    # A fixed, larger patch set than 1 page → must report multiple pages, total N.
    npat = per * 3 + 5
    tab._predict_layout_info(geom, "A4", pages_req=1, npat=npat)
    assert int(panel._estimate_labels["total"].text()) >= npat
    assert int(panel._estimate_labels["pages"].text()) >= 4
    # Without npat (fresh, auto-count) it fills pages_req pages instead.
    tab._predict_layout_info(geom, "A4", pages_req=1, npat=None)
    assert int(panel._estimate_labels["pages"].text()) == 1


def test_estimate_ignores_stale_count_when_auto_patches_on(qapp, tmp_path):
    """With "Auto patch count" ON the estimate must recompute the capacity-fill as
    settings change (e.g. minimum patch width), not stick on the last generated
    chart's count. With it OFF it uses that fixed count (#93, Knut beta-14
    regression)."""
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    tab._switch_mode("manual")
    if tab._manual_auto_patches_check is None:
        pytest.skip("manual auto-patches toggle not present")
    panel = tab._layout_info_panel
    tab._onscreen_patch_total = lambda: 7        # a deliberately stale small count
    # Auto ON → estimate is a full capacity-fill, independent of the stale count.
    tab._manual_auto_patches_check.setChecked(True)
    tab._refresh_manual_command_preview()
    auto_total = int(panel._estimate_labels["total"].text())
    # Auto OFF → estimate lays out the fixed on-screen count (padded to a pass).
    tab._manual_auto_patches_check.setChecked(False)
    tab._refresh_manual_command_preview()
    fixed_total = int(panel._estimate_labels["total"].text())
    assert auto_total > 100                       # a page's worth of patches
    assert fixed_total < 50                       # just the small fixed count (padded)
    assert auto_total != fixed_total


def test_estimate_refreshes_on_mode_switch(qapp, tmp_path):
    """Switching Guided ↔ Manual must re-run the active mode's predictor so the
    estimate describes the mode on screen (#93)."""
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    panel = tab._layout_info_panel
    tab._switch_mode("manual")
    man = panel._estimate_labels["total"].text()
    assert man not in ("—", "")
    tab._switch_mode("guided")
    gui = panel._estimate_labels["total"].text()
    assert gui not in ("—", "")
    # both modes produce an estimate; switching back to manual still works
    tab._switch_mode("manual")
    assert panel._estimate_labels["total"].text() not in ("—", "")
