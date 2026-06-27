"""The Chart-layout-information panel (#93) — placeholder vs filled readout."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_panel_placeholder_then_filled(app):
    from ui.chart_layout_info_panel import ChartLayoutInfoPanel
    panel = ChartLayoutInfoPanel()
    # starts on the placeholder (isHidden reflects explicit state offscreen)
    assert not panel._placeholder.isHidden()
    assert panel._table.isHidden()

    panel.update_info(total=768, rows=32, cols=24, pages=2)
    assert panel._placeholder.isHidden()
    assert not panel._table.isHidden()
    assert panel._value_labels["total"].text() == "768"
    assert panel._value_labels["rows"].text() == "32"
    assert panel._value_labels["cols"].text() == "24"
    assert panel._value_labels["pages"].text() == "2"

    panel.show_placeholder()
    assert not panel._placeholder.isHidden()
    panel.deleteLater()
