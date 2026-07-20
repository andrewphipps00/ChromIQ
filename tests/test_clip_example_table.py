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
    assert text.count("\n") >= 6
    assert "{patchcount}" in text and "{paper}" in text
    assert "borderless" in text
    for field in ("date", "printer", "ink set", "profile name",
                  "paper type", "driver/resolution"):
        assert field in text
    # Custom text is editable now.
    assert panel.clip_text.isEnabled()


def test_example_table_matches_legacy_left_clip_fields(app):
    """The template must stay in sync with chart_creator's legacy left-clip
    header lines and form fields."""
    panel = _panel()
    text = panel._example_clip_table_text()
    # header line 2 is verbatim from chart_creator.py
    assert "PRINT: borderless, 100% size (no scaling), color management OFF" in text
    # the six fill-in fields, in order
    lines = text.splitlines()
    fields = [ln.split(":")[0] for ln in lines[2:]]
    assert fields == ["date", "printer", "ink set", "profile name",
                      "paper type", "driver/resolution"]
    # each field line carries an underline run to write on
    assert all("_" in ln for ln in lines[2:])


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


def test_clip_text_keeps_interior_blank_lines():
    """Blank lines between text lines survive (writing space for hand-filled
    fields); leading/trailing blanks are trimmed (Knut beta.28)."""
    from workflow.layout_engine.raster import clip_text_lines
    assert clip_text_lines("date: ___\n \nprinter: ___") == \
        ["date: ___", " ", "printer: ___"]
    assert clip_text_lines("\n\nA\n\nB\n\n") == ["A", "", "B"]
    assert clip_text_lines("   \n  ") == []
    assert clip_text_lines(None) == []
