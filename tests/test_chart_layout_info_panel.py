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

    # Estimate only (no chart yet): on-screen column dashed, estimate filled.
    panel.set_estimate(total=768, rows=32, cols=24, pages=2)
    assert panel._placeholder.isHidden()
    assert not panel._table.isHidden()
    assert panel._estimate_labels["total"].text() == "768"
    assert panel._actual_labels["total"].text() == "—"

    # A chart on screen fills the on-screen column.
    panel.set_actual(total=768, rows=32, cols=24, pages=2)
    assert panel._actual_labels["rows"].text() == "32"
    assert panel._estimate_labels["cols"].text() == "24"

    panel.show_placeholder()
    assert not panel._placeholder.isHidden()
    panel.deleteLater()


def test_estimate_flagged_when_it_differs(app):
    from ui.chart_layout_info_panel import ChartLayoutInfoPanel
    panel = ChartLayoutInfoPanel()
    panel.set_actual(total=768, rows=32, cols=24, pages=2)
    panel.set_estimate(total=600, rows=32, cols=19, pages=2)
    # differing rows get the amber highlight; matching ones stay muted
    assert "c47f17" in panel._estimate_labels["total"].styleSheet()
    assert "c47f17" in panel._estimate_labels["cols"].styleSheet()
    assert "c47f17" not in panel._estimate_labels["rows"].styleSheet()
    panel.deleteLater()
