"""The "Use the operating system's file browser" setting (Windows-speed option).

Covers both halves: the shared dialog helpers honour the preference (native →
skip the DontUseNativeDialog option + our custom sidebar/preview), and the
Settings checkbox round-trips the value.
"""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel  # noqa: E402

import ui.widgets as widgets  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _captured_dialog(qapp, monkeypatch, native: bool):
    """Drive open_file_dialog without ever showing a modal: force the preference,
    capture the QFileDialog, and stub exec() to cancel."""
    monkeypatch.setattr(widgets, "_prefer_native_dialogs", lambda: native)
    seen = {}
    orig_exec = QFileDialog.exec

    def _fake_exec(self):
        seen["dlg"] = self
        return QFileDialog.DialogCode.Rejected.value

    monkeypatch.setattr(QFileDialog, "exec", _fake_exec)
    try:
        widgets.open_file_dialog(None, "Pick", name_filter="Images (*.tif)",
                                 preview=True)
    finally:
        monkeypatch.setattr(QFileDialog, "exec", orig_exec)
    return seen["dlg"]


def test_native_pref_skips_dontusenative_option(qapp, monkeypatch):
    dlg = _captured_dialog(qapp, monkeypatch, native=True)
    assert not (dlg.options() & QFileDialog.Option.DontUseNativeDialog)
    # No injected preview pane in native mode.
    assert dlg.findChild(QLabel, "imagePreview") is None


def test_themed_pref_injects_preview_pane(qapp, monkeypatch):
    dlg = _captured_dialog(qapp, monkeypatch, native=False)
    # Themed mode with preview=True adds our image-preview QLabel.
    assert dlg.findChild(QLabel, "imagePreview") is not None


def test_themed_pref_sets_dontusenative_option(qapp, monkeypatch):
    dlg = _captured_dialog(qapp, monkeypatch, native=False)
    assert dlg.options() & QFileDialog.Option.DontUseNativeDialog


def test_preference_reads_setting(qapp, monkeypatch, tmp_path):
    from PyQt6.QtCore import QSettings
    from core.settings import AppSettings
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("core.settings.AppSettings", lambda: s)
    s.set("use_native_file_dialogs", True)
    assert widgets._prefer_native_dialogs() is True
    s.set("use_native_file_dialogs", False)
    assert widgets._prefer_native_dialogs() is False


def test_settings_checkbox_round_trips(qapp, tmp_path, monkeypatch):
    import core.preset_store as ps
    from pathlib import Path
    from unittest import mock
    from PyQt6.QtCore import QSettings
    from core.settings import AppSettings
    from ui.dialogs.settings_dialog import SettingsDialog

    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path)):
        dlg = SettingsDialog(s, None)
        try:
            assert dlg._native_files_check.isChecked() is False   # default
            dlg._native_files_check.setChecked(True)
            dlg._save_and_close()
        finally:
            dlg.deleteLater()
    assert s.get("use_native_file_dialogs") is True
