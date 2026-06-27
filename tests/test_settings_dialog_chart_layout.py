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
