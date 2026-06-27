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
    # The panel shows a real prediction, not the placeholder.
    assert panel._placeholder.isHidden()
    assert not panel._table.isHidden()
    total = int(panel._value_labels["total"].text())
    rows = int(panel._value_labels["rows"].text())
    cols = int(panel._value_labels["cols"].text())
    assert total > 0 and rows > 0 and cols > 0


def test_layout_info_placeholder_without_engine(qapp, tmp_path):
    tab = _tab(tmp_path, use_chromiq_layout_engine=False)
    tab._update_patch_count()
    # printtarg layout isn't predicted — placeholder until a chart is generated.
    assert not tab._layout_info_panel._placeholder.isHidden()
