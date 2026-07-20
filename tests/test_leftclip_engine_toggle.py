"""The legacy printtarg-only "Print info in left clip area" must not fight the
ChromIQ layout engine (Knut).

Knut's report: with "Print info in left clip area" ON he enabled "Use the
ChromIQ layout engine instead of printtarg", but the frame silently flipped
back to the printtarg parameters even though the engine checkbox still read ON —
and toggling the engine checkbox no longer switched the frame. Root cause: the
checked left-clip box forces ``use_engine`` False (it is a printtarg-only
feature the engine can't render), and nothing cleared or hid it when the engine
came on. The fix hides the row while the engine is on and clears the box (saving
the user's choice for when the engine goes back off).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_tab(qapp, tmp_path):
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "projects"))
    s.set("use_chromiq_layout_engine", False)
    tab = TabChart(ArgyllRunner(s), FileManager(s), s)
    tab._switch_mode("manual")
    if not tab._manual_panel_inited:
        tab._init_manual_layout_panel()
    return tab, s


def test_left_clip_hidden_and_cleared_when_engine_on(qapp, tmp_path):
    tab, s = _make_tab(qapp, tmp_path)
    tab.show()
    qapp.processEvents()

    # i1Pro chart, engine off → the left-clip row is available and the user
    # checks it.
    tab._set_manual_value("printtarg", "-i", "i1")
    tab._set_manual_value("printtarg", "-L", False)   # keep the left/clip border
    tab._manual_engine_check.setChecked(False)
    qapp.processEvents()
    tab._manual_left_clip_check.setChecked(True)
    tab._update_manual_lb_visibility()
    qapp.processEvents()
    assert not tab._manual_left_clip_row.isHidden(), \
        "left-clip row should be available for an i1Pro printtarg chart"

    # Enable the engine → row hides, box clears, engine panel takes over (the
    # frame must NOT flip back to printtarg).
    tab._manual_engine_check.setChecked(True)
    qapp.processEvents()
    assert tab._manual_left_clip_row.isHidden(), \
        "left-clip row must hide while the engine is on"
    assert not tab._manual_left_clip_check.isChecked(), \
        "a lingering checked box would force use_engine False (Knut's bug)"
    assert not tab._manual_layout_grp.isHidden(), \
        "engine layout panel must be visible"
    assert tab._manual_printtarg_grp.isHidden(), \
        "printtarg frame must not reappear while the engine is on"

    # Turn the engine off → row returns with the user's choice restored.
    tab._manual_engine_check.setChecked(False)
    qapp.processEvents()
    assert not tab._manual_left_clip_row.isHidden()
    assert tab._manual_left_clip_check.isChecked(), \
        "the user's left-clip choice should be restored when the engine goes off"
