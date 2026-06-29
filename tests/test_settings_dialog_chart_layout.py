"""Settings → Chart Layout tab: preselect to the active combo, and the
save → reopen round-trip.

Regression for #93: the tab always opened on i1/A4, so a preset edited under
any other instrument/paper/mode was saved correctly but invisible on reopen —
which looked like "saving a preset did not work" (Knut)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.settings import DEFAULTS  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self, **overrides):
        self._store = {**DEFAULTS, **overrides, "use_chromiq_layout_engine": True}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value


def test_chart_layout_tab_preselects_active_combo(_app, tmp_path):
    import core.preset_store as ps
    from ui.dialogs.settings_dialog import SettingsDialog
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        dlg = SettingsDialog(_FakeSettings(), None,
                             layout_combo=("i1", "A4R", "noclip"))
        try:
            assert dlg._layout_instr.currentData() == "i1"
            assert dlg._layout_paper.currentData() == "A4R"
            assert dlg._layout_mode.currentData() == "noclip"
        finally:
            dlg.deleteLater()


def test_chart_layout_preset_saves_and_reopens_visible(_app, tmp_path):
    """Edit a preset under a non-default combo, save, reopen preselected to that
    combo — the edited value must be shown (not the i1/A4 default)."""
    import core.preset_store as ps
    from ui.dialogs.settings_dialog import SettingsDialog
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        combo = ("CM", "A3", "high")
        dlg = SettingsDialog(_FakeSettings(), None, layout_combo=combo)
        try:
            assert dlg._layout_selection() == combo
            # Edit a field on the live panel, then persist via the panel signal.
            dlg._layout_panel.margins["t"].setValue(33.0)
            dlg._on_layout_field_changed()
            dlg._save_and_close()
        finally:
            dlg.deleteLater()

        # Reopen: a fresh dialog, preselected to the same combo, must show 33.
        dlg2 = SettingsDialog(_FakeSettings(), None, layout_combo=combo)
        try:
            assert dlg2._layout_selection() == combo
            assert dlg2._recipe_from_fields().margin_top == 33.0
        finally:
            dlg2.deleteLater()


def test_engine_and_clip_border_mutually_exclusive_on_open(_app, tmp_path):
    """#93: the engine can't share the page with the old printtarg ChromIQ
    clip-border. The engine toggle now lives in Create Chart, so the Settings
    dialog only enforces the rule when it OPENS: with the engine already on, an
    existing both-on config self-heals (clip unchecked + disabled, saved off)."""
    import core.preset_store as ps
    from ui.dialogs.settings_dialog import SettingsDialog
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        # Both persisted on (the stuck state). _FakeSettings forces the engine on.
        dlg = SettingsDialog(_FakeSettings(i1pro_chromiq_clip_style=True),
                             None, layout_combo=("i1", "A4", "noclip"))
        try:
            # Opening with the engine on disables + clears the clip checkbox.
            assert not dlg._chromiq_clip_check.isChecked()
            assert not dlg._chromiq_clip_check.isEnabled()
            # Saving persists the conflict resolved.
            dlg._save_and_close()
            assert dlg._settings.get("use_chromiq_layout_engine") is True
            assert dlg._settings.get("i1pro_chromiq_clip_style") is False
        finally:
            dlg.deleteLater()


def test_chart_layout_clip_enable_for_cm_ss(_app, tmp_path):
    """The CM/SS clip-border On/Off selector is shown only for CM/SS, drives the
    embedded panel's content mode, and persists through save → reopen (#93)."""
    import core.preset_store as ps
    from ui.dialogs.settings_dialog import SettingsDialog
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        combo = ("SS", "A4", "flat")
        dlg = SettingsDialog(_FakeSettings(), None, layout_combo=combo)
        try:
            # hidden for i1, shown for SS (isHidden reflects the explicit
            # setVisible call without needing the tab to be the current one)
            dlg._layout_instr.setCurrentIndex(dlg._layout_instr.findData("i1"))
            assert dlg._layout_clip_enable.isHidden()
            dlg._layout_instr.setCurrentIndex(dlg._layout_instr.findData("SS"))
            assert not dlg._layout_clip_enable.isHidden()
            dlg._layout_paper.setCurrentIndex(dlg._layout_paper.findData("A4"))
            # turn clip on → panel content becomes a band, persist
            dlg._layout_clip_enable.setCurrentIndex(
                dlg._layout_clip_enable.findData("on"))
            assert dlg._layout_panel.clip_enabled()
            assert dlg._recipe_from_fields().clip_content_mode != "off"
            dlg._save_and_close()
        finally:
            dlg.deleteLater()

        dlg2 = SettingsDialog(_FakeSettings(), None, layout_combo=("SS", "A4", "flat"))
        try:
            assert dlg2._layout_clip_enable.currentData() == "on"
            assert dlg2._layout_panel.clip_enabled()
        finally:
            dlg2.deleteLater()


def test_indicator_style_settings_round_trip(_app, tmp_path):
    """The global strip-indicator styling controls (moved out of Create Chart,
    Knut #93) save to the strip_indicator_* / strip_underline_* keys and reload."""
    import core.preset_store as ps
    from ui.dialogs.settings_dialog import SettingsDialog
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        st = _FakeSettings()
        dlg = SettingsDialog(st, None)
        try:
            dlg._isty_bold.setChecked(True)
            dlg._isty_size.setValue(3.5)
            dlg._isty_rotation.setCurrentIndex(dlg._isty_rotation.findData(90))
            dlg._isty_align.setCurrentIndex(dlg._isty_align.findData("center"))
            dlg._isty_underline.setCurrentIndex(
                dlg._isty_underline.findData("black"))
            dlg._isty_offset.setValue(2.0)
            dlg._save_and_close()
        finally:
            dlg.deleteLater()
    assert st.get("strip_indicator_bold") is True
    assert st.get("strip_indicator_size_mm") == 3.5
    assert st.get("strip_indicator_rotation") == 90
    assert st.get("strip_indicator_align") == "center"
    assert st.get("strip_underline_mode") == "black"
    assert st.get("strip_label_offset_mm") == 2.0


def test_apply_indicator_style_overlays_recipe():
    """AppSettings.apply_indicator_style overlays the global styling onto a recipe
    (used to seed fresh charts; presets keep their own styling)."""
    from core.settings import AppSettings, INDICATOR_STYLE_KEYS
    from workflow.layout_engine.presets import default_recipe
    s = _FakeSettings(strip_indicator_bold=True, strip_indicator_rotation=270,
                      strip_underline_mode="black")
    # Borrow the real methods on the fake store.
    s.indicator_style = AppSettings.indicator_style.__get__(s)
    s.apply_indicator_style = AppSettings.apply_indicator_style.__get__(s)
    r = s.apply_indicator_style(default_recipe("i1", "A4"))
    assert r.indicator_bold is True
    assert r.indicator_rotation == 270
    assert r.underline_mode == "black"
    assert set(INDICATOR_STYLE_KEYS) <= set(vars(r))
