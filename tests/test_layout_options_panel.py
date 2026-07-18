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
        instrument="i1", paper="A4", clip_border=True, cm_stagger=True,
        pscale=0.9, sscale=1.1, spacer_mode="bw", spacer_width_mm=2.0,
        edge_spacers=True, patch_area_align="bottom-right",
        layout_mode="area_first", area_method="by_grid", area_cols=20,
        area_rows=30, area_ratio=1.5, area_min_patch_mm=7.5,
        patch_w_mm=9.0, patch_h_mm=11.0, inter_patch_mm=1.0, strip_gap_mm=2.5,
        strip_label_offset_mm=-4.0,
        strip_indicator_gap_mm=3.0, margin_top=10.0, margin_right=8.0,
        margin_bottom=12.0, margin_left=9.0, use_instrument_margins=True,
        dpi=150, nolimit=True,
        max_strip_mm=200.0, offset_x_mm=4.0, offset_y_mm=5.0, bit16=True,
        compression="zlib", export_pdf=True,
        show_strip_indicators=True, indicator_font="Inter",
        # Font sizes now quantise to whole points in the UI, so use
        # point-grid-aligned mm here (4.23 mm = 12 pt) for an exact round-trip.
        indicator_size_mm=4.23, indicator_bold=True, indicator_italic=False,
        indicator_rotation=270, indicator_align="center",
        underline_mode="cycle", underline_thickness_mm=0.8, underline_gap_mm=1.2,
        chart_text="{project}", chart_text_font="Inter", chart_text_size_mm=3.53,  # 10 pt
        chart_text_bold=True, chart_text_italic=False, text_edge_mm=7.0,
        text_edge_top_mm=5.0, text_edge_clip_mm=6.0,
        stamp_command=True,
        clip_border_width_mm=30.0, clip_content_mode="text", clip_text="ID",
        clip_side="right",
        clip_text_font="Inter", clip_image_path="/tmp/logo.png",
        clip_image_rotation=90, clip_image_scale=60.0,
        clip_image_offset_x_mm=3.0, clip_image_offset_y_mm=4.0,
        strip_pattern="A-Z", patch_pattern="1-99", randomize=True, seed=12345)
    panel.set_recipe(r)
    out = panel.get_recipe()
    for f in ("instrument", "paper", "pscale", "sscale", "spacer_mode",
              "spacer_palette", "spacer_overrides", "edge_spacers", "cm_stagger",
              "patch_area_align", "layout_mode", "area_method", "area_cols",
              "area_rows", "area_ratio", "area_min_patch_mm",
              "spacer_width_mm", "patch_w_mm", "patch_h_mm", "inter_patch_mm",
              "strip_gap_mm", "strip_label_offset_mm",
              "strip_indicator_gap_mm", "margin_top", "margin_right",
              "margin_bottom", "margin_left", "use_instrument_margins",
              "dpi", "nolimit", "max_strip_mm",
              "offset_x_mm", "offset_y_mm", "bit16", "compression", "export_pdf",
              "show_strip_indicators", "indicator_font", "indicator_size_mm",
              "indicator_bold", "indicator_italic", "indicator_rotation",
              "indicator_align", "underline_mode",
              "underline_thickness_mm", "underline_gap_mm", "chart_text",
              "chart_text_font", "chart_text_size_mm", "chart_text_bold",
              "chart_text_italic", "text_edge_mm", "text_edge_top_mm",
              "text_edge_clip_mm", "stamp_command", "clip_border_width_mm",
              "clip_content_mode", "clip_side", "clip_text", "clip_text_font",
              "clip_image_path", "clip_image_rotation", "clip_image_scale",
              "clip_image_offset_x_mm", "clip_image_offset_y_mm",
              "strip_pattern", "patch_pattern",
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


def test_colormunki_density_hidden_in_area_first(app):
    """The Density selector is HIDDEN for ColorMunki in area-first (it doesn't
    define the grid there — hidden, not greyed, like the Calculation-method rows
    in patch-first), but stays shown for i1 clip / patch-first (#93, Knut)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    p = LayoutOptionsPanel(with_selectors=True)
    p.show()
    p.instr.setCurrentIndex(p.instr.findData("CM"))
    p.layout_mode.setCurrentIndex(p.layout_mode.findData("patch_first"))
    assert p.mode.isVisibleTo(p)                    # density matters patch-first
    p.layout_mode.setCurrentIndex(p.layout_mode.findData("area_first"))
    assert not p.mode.isVisibleTo(p)               # moot in area-first → hidden
    p.instr.setCurrentIndex(p.instr.findData("i1"))
    assert p.mode.isVisibleTo(p)                    # i1 clip still matters


def test_cm_stagger_lives_in_layout_frame(app):
    """"Offset every second strip" is a layout option, so it sits in the Layout
    frame (Basic), not Patches & spacers (Expert). CM-only visibility (Knut)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from PyQt6.QtWidgets import QGroupBox
    p = LayoutOptionsPanel(with_selectors=True)
    p.show()
    p.instr.setCurrentIndex(p.instr.findData("CM"))
    assert p.cm_stagger_cb.isVisibleTo(p)          # shown for ColorMunki
    # Its enclosing QGroupBox is the "Layout" frame, not "Patches & spacers".
    box = p.cm_stagger_cb
    while box is not None and not isinstance(box, QGroupBox):
        box = box.parentWidget()
    assert box is not None and box.title() == "Layout"
    p.instr.setCurrentIndex(p.instr.findData("i1"))
    assert not p.cm_stagger_cb.isVisibleTo(p)      # ColorMunki-only


def test_cm_ss_clip_enable_selector(app):
    """CM/SS get an extra clip-border On/Off selector (i1/p3 use their Mode
    selector). It's hidden for i1, drives the content on/off, and hides the
    content group when off (#93, Knut)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from workflow.layout_engine.presets import LayoutRecipe
    p = LayoutOptionsPanel(with_selectors=True)
    p.show()
    p.instr.setCurrentIndex(p.instr.findData("i1"))
    assert not p.clip_enable.isVisibleTo(p)             # i1 uses its Mode combo
    p.instr.setCurrentIndex(p.instr.findData("CM"))
    assert p.clip_enable.isVisibleTo(p)
    assert p.clip_enable.currentData() == "off"          # default off
    # The clip-content group lives in the (collapsed) Expert section now, so test
    # its own shown/hidden state rather than isVisibleTo (which the collapsed
    # ancestor would always make False).
    assert p._clip_content_grp.isHidden()                # group hidden when off
    p.clip_enable.setCurrentIndex(p.clip_enable.findData("on"))
    assert p.clip_content_mode.currentData() == "notes"  # seeds a notes band
    assert not p._clip_content_grp.isHidden()
    p.clip_enable.setCurrentIndex(p.clip_enable.findData("off"))
    assert p.clip_content_mode.currentData() == "off"
    assert p._clip_content_grp.isHidden()
    # set_recipe with a band on reflects in the selector
    p.set_recipe(LayoutRecipe(instrument="SS", paper="A4", clip_content_mode="notes"))
    assert p.clip_enable.currentData() == "on"


def _has_conflict(spin) -> bool:
    """True when the spin currently carries the red conflict outline (#125)."""
    return "d9534f" in spin.styleSheet()


def test_clip_width_does_not_mutate_margin(app):
    """The clip band must NOT copy the clip width into the margin box any more
    (Knut #125): the margin keeps the user's value; the smaller of the two
    fields is flagged instead. The engine still reserves max(width, margin)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from workflow.layout_engine.presets import default_recipe
    p = LayoutOptionsPanel(with_selectors=True)
    r = default_recipe("CM", "A4", mode="extrahigh")
    r.use_instrument_margins = False
    r.margin_left = 6.0
    r.clip_border_width_mm = 26.0
    r.clip_content_mode = "off"
    p.set_recipe(r)
    assert p.margins["l"].value() == 6.0
    p.clip_enable.setCurrentIndex(p.clip_enable.findData("on"))
    # Margin box is left at the user's 6 mm — no silent copy to 26.
    assert p.margins["l"].value() == 6.0
    # clip width (26) > left margin (6) → the MARGIN is flagged as overridden.
    assert _has_conflict(p.margins["l"])
    assert not _has_conflict(p.clip_width)
    # Turning the band off clears the flag; margin unchanged.
    p.clip_enable.setCurrentIndex(p.clip_enable.findData("off"))
    assert p.margins["l"].value() == 6.0
    assert not _has_conflict(p.margins["l"])


def test_mode_tooltip_is_instrument_specific(app):
    """The Mode ⓘ describes only the option the current instrument has, and the
    extra clip-border ⓘ shows only for CM/SS (no orphan tooltip on i1) (#93)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    p = LayoutOptionsPanel(with_selectors=True)
    p.show()
    p.instr.setCurrentIndex(p.instr.findData("i1"))
    assert "clip border" in p._mode_tip._title.lower()
    assert "ColorMunki" not in p._mode_tip._body      # no other instruments
    assert "SpectroScan" not in p._mode_tip._body
    assert not p._clip_enable_tip.isVisibleTo(p)       # no second tooltip on i1
    p.instr.setCurrentIndex(p.instr.findData("CM"))
    assert "density" in p._mode_tip._title.lower()
    assert "SpectroScan" not in p._mode_tip._body
    assert p._clip_enable_tip.isVisibleTo(p)
    p.instr.setCurrentIndex(p.instr.findData("SS"))
    assert "shape" in p._mode_tip._title.lower()
    assert "ColorMunki" not in p._mode_tip._body


def test_clip_width_margin_conflict_flagging(app):
    """Clip width and the clip-side margin are independent; the field that loses
    the max() is flagged with a red outline, and the winner switches as the
    values cross (Knut #125)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    p = LayoutOptionsPanel(with_selectors=True)
    p.show()
    p.instr.setCurrentIndex(p.instr.findData("i1"))   # clip on by default
    p.margins["l"].setValue(9.0)
    p.clip_width.setValue(30.0)
    # width (30) > left margin (9): margin overridden, margin flagged.
    assert p.margins["l"].value() == 9.0
    assert _has_conflict(p.margins["l"]) and not _has_conflict(p.clip_width)
    # Now make the margin the larger one: the CLIP WIDTH is ignored → flagged.
    p.margins["l"].setValue(40.0)
    assert _has_conflict(p.clip_width) and not _has_conflict(p.margins["l"])
    # Clip off → all flags cleared.
    p.mode.setCurrentIndex(p.mode.findData("noclip"))
    assert not _has_conflict(p.margins["l"]) and not _has_conflict(p.clip_width)
    # Right-side clip flags the RIGHT margin instead of the left.
    p.mode.setCurrentIndex(p.mode.findData("clip"))
    p.clip_side.setCurrentIndex(p.clip_side.findData("right"))
    p.margins["r"].setValue(8.0)
    p.clip_width.setValue(28.0)
    assert _has_conflict(p.margins["r"]) and not _has_conflict(p.margins["l"])


def test_use_instrument_margins_fills_and_locks(app):
    """Ticking "Use instrument margins" fills the four margins from the wired
    threshold lookup and locks them read-only (#93, Knut)."""
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel(with_selectors=True)
    panel.set_threshold_lookup(lambda inst, paper: {"L": 26, "R": 9, "T": 38, "B": 10})
    # the user's own margins, remembered before ticking
    for k, v in (("t", 7.0), ("r", 8.0), ("b", 9.0), ("l", 5.0)):
        panel.margins[k].setValue(v)
    panel.use_instr_margins.setChecked(True)
    assert panel.margins["l"].value() == 26 and panel.margins["t"].value() == 38
    assert panel.margins["r"].value() == 9 and panel.margins["b"].value() == 10
    assert not panel.margins["t"].isEnabled()        # locked while ticked
    panel.use_instr_margins.setChecked(False)
    assert panel.margins["t"].isEnabled()            # editable again
    # unticking restores the user's own margins (not the threshold values)
    assert (panel.margins["t"].value(), panel.margins["r"].value(),
            panel.margins["b"].value(), panel.margins["l"].value()) == (7, 8, 9, 5)
    # hidden when no lookup is wired
    bare = LayoutOptionsPanel(with_selectors=True)
    assert not bare.use_instr_margins.isVisibleTo(bare)


def test_i1_clip_on_defaults_to_notes(app):
    """Turning the i1/p3 clip border ON (its Mode combo) defaults the clip content
    to the notes box, not 'none' — mirroring CM/SS. A loaded recipe that keeps the
    clip border on with no content is left alone (Knut)."""
    from dataclasses import replace
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from workflow.layout_engine.presets import default_recipe
    p = LayoutOptionsPanel(with_selectors=True)
    # chart made with the clip suppressed (content off), user turns it back on
    p.set_recipe(replace(default_recipe("i1", "A4"),
                         clip_border=False, clip_content_mode="off"))
    p.mode.setCurrentIndex(p.mode.findData("clip"))
    assert p.clip_content_mode.currentData() == "notes"
    p.mode.setCurrentIndex(p.mode.findData("noclip"))
    assert p.clip_content_mode.currentData() == "off"
    # a deliberately content-less clip border survives loading unchanged
    p2 = LayoutOptionsPanel(with_selectors=True)
    p2.set_recipe(replace(default_recipe("i1", "A4"),
                          clip_border=True, clip_content_mode="off"))
    assert p2.clip_content_mode.currentData() == "off"


def test_clip_image_rows_only_visible_for_image(app):
    """Image path / rotate / scale / move are shown only when the clip content
    type is 'Imported image' (Knut), not merely greyed out."""
    from dataclasses import replace
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    from workflow.layout_engine.presets import default_recipe
    p = LayoutOptionsPanel(with_selectors=True)
    rows = (p._clip_image_row, p._clip_image_fit_row, p._clip_image_move_row)
    shown = lambda: [not w.isHidden() for row in rows for w in row]
    p.set_recipe(replace(default_recipe("i1", "A4"), clip_content_mode="notes"))
    assert not any(shown())
    p.clip_content_mode.setCurrentIndex(p.clip_content_mode.findData("image"))
    assert all(shown())
    p.clip_content_mode.setCurrentIndex(p.clip_content_mode.findData("text"))
    assert not any(shown())
