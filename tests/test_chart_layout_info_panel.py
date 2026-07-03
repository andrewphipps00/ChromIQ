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
    panel.set_actual(total=768, rows=32, cols=24, pages=2, patch_w=8.0, patch_h=10.0)
    assert panel._actual_labels["rows"].text() == "32"
    assert panel._estimate_labels["cols"].text() == "24"
    # patch size shown as "w×h" on screen; estimate had no size → dash
    assert panel._actual_labels["patch"].text() == "8×10"
    assert panel._estimate_labels["patch"].text() == "—"

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


def test_patch_size_two_decimals_and_rounding_tolerance(app):
    from ui.chart_layout_info_panel import ChartLayoutInfoPanel
    panel = ChartLayoutInfoPanel()
    # 2 decimals so a derived size (7.34) is visible, not hidden by 1-dp rounding.
    panel.set_actual(total=756, rows=21, cols=36, pages=1, patch_w=7.30, patch_h=7.37)
    panel.set_estimate(total=756, rows=21, cols=36, pages=1, patch_w=7.30, patch_h=7.34)
    assert panel._actual_labels["patch"].text() == "7.3×7.37"
    assert panel._estimate_labels["patch"].text() == "7.3×7.34"
    # sub-pixel rounding (0.03 mm) is within tolerance → NOT amber.
    assert "c47f17" not in panel._estimate_labels["patch"].styleSheet()
    # a real size difference (> tolerance) is still flagged.
    panel.set_estimate(total=756, rows=21, cols=36, pages=1, patch_w=7.30, patch_h=8.5)
    assert "c47f17" in panel._estimate_labels["patch"].styleSheet()
    panel.deleteLater()


def test_panel_has_tooltip_button(app):
    from ui.chart_layout_info_panel import ChartLayoutInfoPanel
    from ui.tooltip_button import TooltipButton
    panel = ChartLayoutInfoPanel()
    assert panel.findChildren(TooltipButton), "panel must have an ⓘ tooltip"
    panel.deleteLater()
