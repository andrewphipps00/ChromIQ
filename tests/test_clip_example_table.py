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


def test_clip_text_keeps_interior_blank_lines():
    """Blank lines between text lines survive (writing space for hand-filled
    fields); leading/trailing blanks are trimmed (Knut beta.28)."""
    from workflow.layout_engine.raster import clip_text_lines
    assert clip_text_lines("date: ___\n \nprinter: ___") == \
        ["date: ___", " ", "printer: ___"]
    assert clip_text_lines("\n\nA\n\nB\n\n") == ["A", "", "B"]
    assert clip_text_lines("   \n  ") == []
    assert clip_text_lines(None) == []
