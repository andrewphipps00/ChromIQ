"""Settings dialog — print-path option gating.

The CUPS preflight confirmation and the exact-size PDF fallback only apply to
ChromIQ's own lp pipeline. While "Use default macOS printer dialog" is ticked,
no lp job (and therefore no PS→PDF/TIFF fallback) ever runs, so both options
must grey out — and come back when the dialog path is unticked.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.platform_paths import native_print_supported  # noqa: E402
from core.settings import DEFAULTS  # noqa: E402
from ui.dialogs.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeSettings:
    """Dict-backed stand-in so the test never touches the user's QSettings."""

    def __init__(self, **overrides):
        self._store = {**DEFAULTS, **overrides}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value


@pytest.mark.skipif(
    not native_print_supported(),
    reason="gating only runs where the native print dialog is supported",
)
def test_lp_only_options_grey_out_with_native_dialog(_app) -> None:
    dlg = SettingsDialog(_FakeSettings(use_native_print_dialog=True), None)
    try:
        assert not dlg._pdf_fallback_check.isEnabled()
        assert not dlg._confirm_print_check.isEnabled()

        dlg._native_print_check.setChecked(False)
        assert dlg._pdf_fallback_check.isEnabled()
        assert dlg._confirm_print_check.isEnabled()

        dlg._native_print_check.setChecked(True)
        assert not dlg._pdf_fallback_check.isEnabled()
    finally:
        dlg.deleteLater()


@pytest.mark.skipif(
    not native_print_supported(),
    reason="gating only runs where the native print dialog is supported",
)
def test_pdf_fallback_round_trips_through_save(_app) -> None:
    settings = _FakeSettings(use_native_print_dialog=False, pdf_print_fallback=True)
    dlg = SettingsDialog(settings, None)
    try:
        assert dlg._pdf_fallback_check.isChecked()
        dlg._pdf_fallback_check.setChecked(False)
        dlg._save_and_close()
        assert settings.get("pdf_print_fallback") is False
    finally:
        dlg.deleteLater()
