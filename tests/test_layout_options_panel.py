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
        spacer_palette=["#112233", "#445566", "#778899", "#aabbcc", "#ddeeff"],
        spacer_overrides={"0": "#ff00ff", "7": "#00ffff"},
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
        edge_spacers=True, patch_area_align="bottom-right",
        layout_mode="area_first", area_method="by_grid", area_cols=20,
        area_rows=30, area_ratio=1.5, area_min_patch_mm=7.5,
        patch_w_mm=9.0, patch_h_mm=11.0, inter_patch_mm=1.0, strip_gap_mm=2.5,
        strip_label_offset_mm=-4.0,
        strip_indicator_gap_mm=3.0, margin_top=10.0, margin_right=8.0,
        margin_bottom=12.0, margin_left=9.0, dpi=150, nolimit=True,
        max_strip_mm=200.0, offset_x_mm=4.0, offset_y_mm=5.0, bit16=True,
        compression="zlib", show_strip_indicators=True, indicator_font="Inter",
        indicator_size_mm=4.0, indicator_bold=True, indicator_italic=False,
        indicator_rotation=270, indicator_align="center",
        underline_mode="cycle", underline_thickness_mm=0.8, underline_gap_mm=1.2,
        chart_text="{project}", chart_text_font="Inter", chart_text_size_mm=3.5,
        chart_text_bold=True, chart_text_italic=False, stamp_command=True,
        clip_border_width_mm=30.0, clip_content_mode="text", clip_text="ID",
        clip_side="right",
        clip_text_font="Inter", clip_image_path="/tmp/logo.png",
        strip_pattern="A-Z", patch_pattern="1-99", randomize=True, seed=12345)
    panel.set_recipe(r)
    out = panel.get_recipe()
    for f in ("instrument", "paper", "pscale", "sscale", "spacer_mode",
              "spacer_palette", "spacer_overrides", "edge_spacers",
              "patch_area_align", "layout_mode", "area_method", "area_cols",
              "area_rows", "area_ratio", "area_min_patch_mm",
              "spacer_width_mm", "patch_w_mm", "patch_h_mm", "inter_patch_mm",
              "strip_gap_mm", "strip_label_offset_mm",
              "strip_indicator_gap_mm", "margin_top", "margin_right",
              "margin_bottom", "margin_left", "dpi", "nolimit", "max_strip_mm",
              "offset_x_mm", "offset_y_mm", "bit16", "compression",
              "show_strip_indicators", "indicator_font", "indicator_size_mm",
              "indicator_bold", "indicator_italic", "indicator_rotation",
              "indicator_align", "underline_mode",
              "underline_thickness_mm", "underline_gap_mm", "chart_text",
              "chart_text_font", "chart_text_size_mm", "chart_text_bold",
              "chart_text_italic", "stamp_command", "clip_border_width_mm",
              "clip_content_mode", "clip_side", "clip_text", "clip_text_font",
              "clip_image_path", "strip_pattern", "patch_pattern",
              "randomize", "seed"):
        assert getattr(out, f) == getattr(r, f), f


def test_save_as_defaults_recipe_dict_roundtrip(app):
    """Mirror the Create-Chart "Save as Defaults" path: a panel recipe survives
    to_dict() → (settings) → from_dict() → set_recipe(), including paper and the
    newer options. Guards the #93 bug where engine options reset after restart."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from workflow.layout_engine.presets import LayoutRecipe
    src = LayoutOptionsPanel(with_selectors=True)
    r = LayoutRecipe(instrument="i1", paper="A4R", margin_top=14.0,
                     margin_bottom=14.0, strip_gap_mm=3.0,
                     strip_label_offset_mm=-2.0, indicator_rotation=90,
                     chart_text="{project} {page}")
    src.set_recipe(r)
    blob = src.get_recipe().to_dict()          # what _on_save_defaults stores

    dst = LayoutOptionsPanel(with_selectors=True)
    dst.set_recipe(LayoutRecipe.from_dict(blob))   # what _init_… restores
    out = dst.get_recipe()
    for f in ("paper", "margin_top", "margin_bottom", "strip_gap_mm",
              "strip_label_offset_mm", "indicator_rotation", "chart_text"):
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


def test_custom_spacer_palette_includes_white_and_black(app):
    """The custom spacer palette offers the 5 accents + white + black, and the
    full 7-colour set round-trips through the recipe (#96 follow-up)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel(with_selectors=True)
    assert len(panel._spacer_swatches) == 7
    hexes = [b.property("hexcol").lower() for b in panel._spacer_swatches]
    assert "#ffffff" in hexes and "#000000" in hexes
    # enable custom and round-trip the 7-colour palette
    panel.custom_spacer_cb.setChecked(True)
    out = panel.get_recipe()
    assert len(out.spacer_palette) == 7
    panel.set_recipe(out)
    assert [b.property("hexcol").lower() for b in panel._spacer_swatches] == \
        [c.lower() for c in out.spacer_palette]


def test_engine_info_line_from_recipe_reflects_recipe():
    """The Manual info box must read engine values from the recipe, not the
    printtarg widgets — clip border, custom patch size and per-edge margins all
    have to show through (#93)."""
    from ui.tabs.tab_chart import TabChart
    line = TabChart._engine_info_line_from_recipe

    # Paper carries its orientation, not just the size.
    assert "A4 portrait" in line(LayoutRecipe(instrument="i1", paper="A4"))
    assert "A4 landscape" in line(LayoutRecipe(instrument="i1", paper="A4R"))

    # Clip border ON must say "on" (the bug showed "off").
    r = LayoutRecipe(instrument="i1", paper="A4", clip_border=True)
    assert "clip border on" in line(r)
    r = LayoutRecipe(instrument="i1", paper="A4", clip_border=False)
    assert "clip border off" in line(r)

    # Explicit patch width/height wins over a uniform scale factor (patch-first).
    r = LayoutRecipe(instrument="i1", paper="A4", layout_mode="patch_first",
                     patch_w_mm=7.5, patch_h_mm=9.0, pscale=0.95)
    s = line(r)
    assert "patch 7.5×9 mm" in s
    assert "×0.95" not in s

    # No explicit size → fall back to the scale factor (patch-first).
    r = LayoutRecipe(instrument="i1", paper="A4", layout_mode="patch_first",
                     pscale=0.95)
    assert "patch ×0.95" in line(r)

    # Per-edge margins must not collapse to a single value.
    r = LayoutRecipe(instrument="i1", paper="A4", margin_top=27.8,
                     margin_right=4, margin_bottom=10.8, margin_left=26)
    s = line(r)
    assert "margins 27.8/4/10.8/26 mm" in s
    assert "margin 10 mm" not in s

    # Uniform margins collapse.
    r = LayoutRecipe(instrument="i1", paper="A4", margin_top=10, margin_right=10,
                     margin_bottom=10, margin_left=10)
    assert "margin 10 mm" in line(r)

    # Area-first shows the target grid instead of a patch size.
    r = LayoutRecipe(instrument="i1", paper="A4", layout_mode="area_first",
                     area_method="by_grid", area_cols=24, area_rows=30,
                     patch_w_mm=9, patch_h_mm=9)
    s = line(r)
    assert "area-fit 24×30" in s
    assert "9×9 mm" not in s
