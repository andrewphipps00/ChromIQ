"""The preset combo's popup anchors at the combo (#108-era polish, Basti).

macOS positions a menu-style combo popup so the SELECTED item overlaps the
combo — after picking an entry near the end of the long built-in preset list,
the popup frame started at the top of the screen, and _CappedComboBox's
height cap shrank it in place without moving it back. showPopup now
re-anchors the frame below the combo (above/clamped when there's no room),
like a plain dropdown.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_popup_anchors_below_combo_with_late_selection(_app):
    from ui.tabs.tab_chart import _CappedComboBox
    w = QWidget()
    v = QVBoxLayout(w)
    combo = _CappedComboBox(w)
    for i in range(12):
        combo.addItem(f"Preset {i:02d}")
    combo.setCurrentIndex(11)               # selection near the end of the list
    v.addWidget(combo)
    v.addStretch(1)
    w.move(0, 0)
    w.resize(500, 560)
    w.show()
    _app.processEvents()
    try:
        combo.showPopup()
        _app.processEvents()
        cont = combo.view().window()
        below = combo.mapToGlobal(combo.rect().bottomLeft())
        assert abs(cont.y() - below.y()) <= 2, \
            "popup frame drifted away from the combo (selected-item alignment)"
        combo.hidePopup()
    finally:
        w.deleteLater()


def test_popup_capped_and_on_screen_with_long_list(_app):
    from ui.tabs.tab_chart import _CappedComboBox
    w = QWidget()
    v = QVBoxLayout(w)
    combo = _CappedComboBox(w)
    for i in range(80):
        combo.addItem(f"Preset {i:02d}")
    combo.setCurrentIndex(75)
    v.addSpacing(250)
    v.addWidget(combo)
    w.resize(500, 400)
    w.show()
    _app.processEvents()
    try:
        combo.showPopup()
        _app.processEvents()
        cont = combo.view().window()
        scr = combo.screen().availableGeometry()
        row_h = combo.view().sizeHintForRow(0) or 22
        assert cont.height() <= row_h * combo._MAX_ROWS + 4
        assert cont.y() >= scr.top()        # never stranded above the screen
        combo.hidePopup()
    finally:
        w.deleteLater()
