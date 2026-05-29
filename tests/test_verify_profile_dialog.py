"""Tools → "Verify a profile (independent check)" dialog.

The colour-difference parsing and grading live in workflow/profcheck_runner.py
(tested elsewhere); here we cover the dialog's wiring without the Argyll binary:
  * Run is gated until both a profile and a measurement are chosen.
  * It hands profcheck the picked files and options.
  * A finished run renders a plain-language verdict.

Also checks the shared neutral-control styling helper used by every tool dialog.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from ui.dialogs.tools_dialogs import (  # noqa: E402
    VerifyProfileDialog,
    neutral_controls_qss,
)
from workflow.profcheck_runner import ProfcheckRunner  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QApplication.instance() or QApplication([])
    yield app


_PROFCHECK_LOG = (
    "Profile check complete, errors(CIEDE2000): max. = 3.50, avg. = 1.20\n"
    "  [1.20] 1 @ A1: ...\n"
    "  [3.50] 2 @ B2: ...\n"
)


class _FakeChecker:
    """Stand-in for ProfcheckRunner: records params, replays a canned log."""

    def __init__(self, log: str) -> None:
        self._log = log
        self.captured = None

    def run(self, params, on_line, on_finish):
        self.captured = params
        for line in self._log.splitlines():
            on_line(line)
        on_finish(1)  # profcheck returns 1 when it finds colour errors — normal

    def parse_results(self):
        return ProfcheckRunner(None).parse_results(self._log)

    def captured_warnings(self):
        return []

    def primary_failure(self):
        return None


def _make_dialog(tmp_path: Path) -> VerifyProfileDialog:
    dlg = VerifyProfileDialog(runner=SimpleNamespace(is_running=False), settings=AppSettings())
    return dlg


def test_run_gated_until_both_files_chosen(_app, tmp_path):
    dlg = _make_dialog(tmp_path)
    assert dlg._can_run() is False
    dlg._profile = tmp_path / "p.icc"
    assert dlg._can_run() is False          # profile alone isn't enough
    dlg._measured = tmp_path / "m.ti3"
    assert dlg._can_run() is True


def test_execute_passes_files_and_renders_verdict(_app, tmp_path):
    dlg = _make_dialog(tmp_path)
    dlg._profile = tmp_path / "p.icc"
    dlg._measured = tmp_path / "m.ti3"
    fake = _FakeChecker(_PROFCHECK_LOG)
    dlg._checker = fake

    dlg._execute()

    # profcheck was handed the picked files and the dialog's chosen options
    assert fake.captured.icc_path == dlg._profile
    assert fake.captured.ti3_path == dlg._measured
    assert fake.captured.de_formula == "-k"   # CIEDE2000 default
    assert fake.captured.intent == "a"
    # a verdict + the numbers made it into the banner
    text = dlg._banner.text()
    assert "Verdict:" in text
    assert "1.20" in text


def test_neutral_controls_qss_uses_given_colour():
    qss = neutral_controls_qss("#d0d0d0")
    assert "QCheckBox::indicator:checked" in qss
    assert "#d0d0d0" in qss
    # focus ring covers text, combo and spin inputs
    assert "QComboBox:focus" in qss and "QSpinBox:focus" in qss
