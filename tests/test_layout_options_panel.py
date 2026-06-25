"""The reusable LayoutOptionsPanel round-trips a LayoutRecipe."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from workflow.layout_engine.presets import LayoutRecipe


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_recipe_roundtrip(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel()
    r = LayoutRecipe(
        instrument="i1", paper="A4", spacer_mode="bw", pscale=0.9, sscale=1.1,
        spacer_width_mm=2.0, patch_w_mm=9.0, patch_h_mm=11.0, inter_patch_mm=1.0,
        strip_indicator_gap_mm=3.0, margin_top=10, margin_right=8, margin_bottom=12,
        margin_left=15, dpi=150, nolimit=True, max_strip_mm=200, offset_x_mm=4,
        offset_y_mm=5, bit16=True, compression="zlib")
    panel.set_recipe(r)
    out = panel.apply_to_recipe(LayoutRecipe(instrument="i1", paper="A4"))
    for f in ("spacer_mode", "pscale", "sscale", "spacer_width_mm", "patch_w_mm",
              "patch_h_mm", "inter_patch_mm", "strip_indicator_gap_mm", "margin_top",
              "margin_right", "margin_bottom", "margin_left", "dpi", "nolimit",
              "max_strip_mm", "offset_x_mm", "offset_y_mm", "bit16", "compression"):
        assert getattr(out, f) == getattr(r, f), f


def test_all_engine_options_roundtrip(app):
    """Every engine option exposed in the panel must survive set→get so it can
    be saved as a default / preset (parity with printtarg). Guards against a new
    field being added to the panel but forgotten in get/set/apply."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel(with_calibration=True, with_selectors=True)
    r = LayoutRecipe(
        instrument="i1", paper="A4", clip_border=True,
        pscale=0.9, sscale=1.1, spacer_mode="bw", spacer_width_mm=2.0,
        patch_w_mm=9.0, patch_h_mm=11.0, inter_patch_mm=1.0,
        strip_indicator_gap_mm=3.0, margin_top=10.0, margin_right=8.0,
        margin_bottom=12.0, margin_left=9.0, dpi=150, nolimit=True,
        max_strip_mm=200.0, offset_x_mm=4.0, offset_y_mm=5.0, bit16=True,
        compression="zlib", show_strip_indicators=True, indicator_font="Inter",
        indicator_size_mm=4.0, indicator_bold=True, indicator_italic=False,
        indicator_rotation=270,
        underline_mode="cycle", underline_thickness_mm=0.8, underline_gap_mm=1.2,
        chart_text="{project}", chart_text_font="Inter", chart_text_size_mm=3.5,
        chart_text_bold=True, chart_text_italic=False, stamp_command=True,
        clip_border_width_mm=30.0, clip_content_mode="text", clip_text="ID",
        clip_text_font="Inter", clip_image_path="/tmp/logo.png",
        strip_pattern="A-Z", patch_pattern="1-99", randomize=True, seed=12345)
    panel.set_recipe(r)
    out = panel.get_recipe()
    for f in ("instrument", "paper", "pscale", "sscale", "spacer_mode",
              "spacer_width_mm", "patch_w_mm", "patch_h_mm", "inter_patch_mm",
              "strip_indicator_gap_mm", "margin_top", "margin_right",
              "margin_bottom", "margin_left", "dpi", "nolimit", "max_strip_mm",
              "offset_x_mm", "offset_y_mm", "bit16", "compression",
              "show_strip_indicators", "indicator_font", "indicator_size_mm",
              "indicator_bold", "indicator_italic", "indicator_rotation",
              "underline_mode",
              "underline_thickness_mm", "underline_gap_mm", "chart_text",
              "chart_text_font", "chart_text_size_mm", "chart_text_bold",
              "chart_text_italic", "stamp_command", "clip_border_width_mm",
              "clip_content_mode", "clip_text", "clip_text_font",
              "clip_image_path", "strip_pattern", "patch_pattern",
              "randomize", "seed"):
        assert getattr(out, f) == getattr(r, f), f


def test_changed_signal(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel()
    fired = []
    panel.changed.connect(lambda: fired.append(1))
    panel.pscale.setValue(1.5)
    assert fired


def test_calibration_gated(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    assert LayoutOptionsPanel().cal_settings() == (None, False)        # no cal group
    p = LayoutOptionsPanel(with_calibration=True)
    assert p.cal_settings() == (None, False)                          # nothing chosen
    p.set_cal("/tmp/x.cal", "apply")
    assert p.cal_settings() == ("/tmp/x.cal", True)
    p.set_cal("/tmp/x.cal", "embed")
    assert p.cal_settings() == ("/tmp/x.cal", False)
