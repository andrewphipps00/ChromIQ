"""Clip-border 'Example custom table' (Knut): selecting it pre-fills the editable
Text box with a record replicating the legacy 'Print info in left clip area'
table, switches to Custom text, and the multi-line text round-trips through the
recipe."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from workflow.layout_engine.presets import LayoutRecipe


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _panel():
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    return LayoutOptionsPanel()


def test_example_table_prefills_text_and_reverts_to_custom_text(app):
    panel = _panel()
    panel.clip_content_mode.setCurrentIndex(panel.clip_content_mode.findData("example"))
    app.processEvents()
    # Not a persistent mode: it reverted to Custom text…
    assert panel.clip_content_mode.currentData() == "text"
    # …and filled the box with the multi-line template.
    text = panel.clip_text.toPlainText()
    assert text.count("\n") >= 4
    assert "{patchcount}" in text and "{paper}" in text
    assert "borderless" in text
    for field in ("date", "printer", "ink set", "profile name",
                  "paper type", "driver/resolution"):
        assert field in text
    # Custom text is editable now.
    assert panel.clip_text.isEnabled()


def test_example_table_matches_knut_approved_layout(app):
    """Knut's approved layout: a header line (chart summary + PRINT reminder),
    then two field rows with a BLANK line between each for hand-writing."""
    panel = _panel()
    lines = panel._example_clip_table_text().splitlines()
    assert len(lines) == 5
    # header line: tokens + the verbatim print reminder
    assert "{patchcount}" in lines[0] and "{paper}" in lines[0]
    assert "PRINT: borderless, 100% size (no scaling), color management OFF" in lines[0]
    assert not lines[1].strip() and not lines[3].strip()      # blank writing rows
    # two field rows, each three underscored fields in order
    def fields(row):
        import re
        return [f.strip() for f, _ in re.findall(r"([a-z /]+): (_+)", row)]
    assert fields(lines[2]) == ["date", "printer", "ink set"]
    assert fields(lines[4]) == ["profile name", "paper type", "driver/resolution"]


def test_multiline_clip_text_roundtrips_through_recipe(app):
    panel = _panel()
    multi = "line one\nline two: ____\n{project} — {date}"
    panel.clip_text.setPlainText(multi)
    out = panel.apply_to_recipe(LayoutRecipe(instrument="i1", paper="A4"))
    assert out.clip_text == multi                     # newlines preserved
    # and load restores them
    panel2 = _panel()
    panel2.set_recipe(out)
    assert panel2.clip_text.toPlainText() == multi


def test_new_line_insert_available_only_for_multiline(app):
    panel = _panel()
    # clip insert button targets the multi-line clip_text → offers "New line";
    # the single-line sheet-text insert button does not.
    def actions(btn):
        return [a.text() for a in btn.menu().actions()]
    assert any("New line" in t for t in actions(panel.clip_insert_btn))
    assert not any("New line" in t for t in actions(panel.insert_token_btn))


def test_clip_text_fills_to_edge_distance():
    """A long clip line fills (almost) the full clip length before shrinking —
    the clip area already keeps the text-edge distance from the page edge, so the
    renderer must not inset a second time and stop the text well short (Knut)."""
    from workflow.layout_engine.raster import _vtext
    H = 2000
    line = "profile name: " + "_" * 43 + "  paper type: " + "_" * 36
    overlay = _vtext(line, "Inter", 300, H)
    bbox = overlay.getbbox()               # black-ink bounds; text reads up strip
    assert bbox is not None
    span = bbox[3] - bbox[1]               # extent along the clip length
    assert span > 0.96 * H                 # reaches near the area edge (was ≤0.95)


def test_clip_text_grows_to_fill_thickness_when_length_has_room():
    """Short clip text used to stop at a fixed fraction of the strip WIDTH (it
    only ever shrank from a fixed start, never grew), leaving big side margins
    even with length to spare. Now it grows to fill the thickness up to the clip
    text-edge too, not just top/bottom (Knut)."""
    from workflow.layout_engine.raster import _vtext
    W, H = 300, 2000
    overlay = _vtext("date:", "Inter", W, H)      # one short line, length to spare
    bbox = overlay.getbbox()                       # image is width_px × height_px
    assert bbox is not None
    thickness_span = bbox[2] - bbox[0]             # stacked lines span the WIDTH
    assert thickness_span > 0.5 * W                # grows to fill (was ~0.34·W)


def test_clip_text_justifies_lines_to_fill_thickness_when_length_bound():
    """A long line caps the font size (length-bound); the few lines must still
    spread across the strip thickness so the outer lines reach the text-edge on
    the sides, not sit packed in the middle (Knut, 8.1 mm → ~4 mm)."""
    from workflow.layout_engine.raster import _vtext
    W, H = 260, 3413
    long_header = ("ChromIQ Chart 667 RGB target on A4 - PRINT: borderless, "
                   "100% size (no scaling), color management OFF")
    txt = "\n".join([long_header, "date: ____", "profile name: ____"])
    bbox = _vtext(txt, "Inter", W, H).getbbox()
    assert bbox is not None
    assert (bbox[2] - bbox[0]) > 0.9 * W        # lines fill the strip thickness


def test_clip_flip_180_persists_and_flips_render(app):
    """The clip flip-180 toggle round-trips through the recipe (so it saves as a
    default and inside a preset) and actually turns the clip content over (Knut)."""
    import numpy as np
    from dataclasses import replace
    from workflow.layout_engine import geometry, instruments, raster
    from workflow.layout_engine.ti1_reader import ColorTarget

    # Persistence: both the recipe dict (defaults / presets) and the build-kwargs
    # dict carry the flag.
    r = replace(LayoutRecipe(instrument="i1", paper="A4"), clip_flip_180=True)
    assert LayoutRecipe.from_dict(r.to_dict()).clip_flip_180 is True
    assert LayoutRecipe.from_build_kwargs(r.build_kwargs()).clip_flip_180 is True

    # Render: same side, flip toggled → the clip region is exactly the 180° turn.
    target = ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                         patches=[((50.0, 50.0, 50.0), (40.0, 45.0, 50.0))
                                  for _ in range(60)])
    geom = instruments.build("i1")            # clip_side defaults to "left"
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    ax, ay, aw, ah = geometry.clip_area_px(geom, 297.0, 150, 210.0)

    def _clip(flip):
        res = raster.render_pages(
            target, lay, geom, seed=1, randomize=False,
            paper_w_mm=210.0, paper_h_mm=297.0, dpi=150,
            clip_content_mode="text", clip_text="TOP edge", clip_flip_180=flip)
        return np.asarray(res.images[0])[ay:ay + ah, ax:ax + aw]

    a0, a1 = _clip(False), _clip(True)
    assert not np.array_equal(a0, a1)                 # the toggle changed the strip
    assert np.array_equal(a1, a0[::-1, ::-1])         # …by exactly 180°


def test_clip_text_keeps_interior_blank_lines():
    """Blank lines between text lines survive (writing space for hand-filled
    fields); leading/trailing blanks are trimmed (Knut beta.28)."""
    from workflow.layout_engine.raster import clip_text_lines
    assert clip_text_lines("date: ___\n \nprinter: ___") == \
        ["date: ___", " ", "printer: ___"]
    assert clip_text_lines("\n\nA\n\nB\n\n") == ["A", "", "B"]
    assert clip_text_lines("   \n  ") == []
    assert clip_text_lines(None) == []
