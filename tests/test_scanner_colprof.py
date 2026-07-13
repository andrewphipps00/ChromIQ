"""Scanner colprof settings (#121, Knut): algorithm mapping, command building,
and the Advanced dialog round-trip."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.dialogs import scanner_colprof as sc  # noqa: E402
from workflow.profile_builder import ProfileBuilder  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_ptype_choices_are_colprof_algo_letters():
    """The profile type IS the colprof -a algorithm (XYZ/Lab folded in as the two
    cLUT variants, per Knut — no separate 'colour space' control)."""
    assert [d for d, _ in sc.PTYPE_CHOICES] == ["s", "m", "x", "l"]
    assert set(sc.CLUT_ALGOS) == {"x", "l"}       # quality applies only to these


def test_make_profile_params_default_matches_previous_output():
    """Defaults must reproduce the previous scanner build (-as -qm, ChromIQ)."""
    p = sc.make_profile_params(Path("x.ti3"), "My scanner",
                               {"ptype": "s", "quality": "m"}, {})
    args = ProfileBuilder(None)._build_args(p)
    assert "-as" in args and "-qm" in args
    assert args[args.index("-A") + 1] == "ChromIQ"       # unchanged default metadata
    assert "-r" not in " ".join(a for a in args if a.startswith("-r"))  # default smoothing hidden


def test_make_profile_params_full_advanced_reaches_command():
    p = sc.make_profile_params(
        Path("x.ti3"), "Epson V850 scanner",
        {"ptype": "l", "quality": "h"},                  # cLUT Lab, high
        {"-r": 1.5, "-ni": True, "-A": "Epson", "-C": "(c) me", "extra_args": "-U 1.0"})
    cmd = " ".join(ProfileBuilder(None)._build_args(p))
    assert "-al" in cmd and "-qh" in cmd          # cLUT Lab, high
    assert "-r1.50" in cmd                         # smoothing surfaced (non-default)
    assert "-ni" in cmd                            # no input curves
    assert "-A Epson" in cmd and "-C (c) me" in cmd
    assert "-U 1.0" in cmd                         # free-form extra args


def test_advanced_dialog_roundtrip_and_restore(_app):
    seed = {"-r": 2.0, "-ni": True, "-A": "Canon", "-C": "x", "extra_args": "-U 0.5"}
    dlg = sc.ScannerAdvancedDialog(seed)
    try:
        # seeded values are shown and returned unchanged
        out = dlg.values()
        assert out["-A"] == "Canon" and out["-ni"] is True
        assert abs(float(out["-r"]) - 2.0) < 1e-6
        assert out["extra_args"] == "-U 0.5"
        # Restore defaults zeroes everything back to the param defaults
        dlg._restore_defaults()
        out2 = dlg.values()
        assert out2["-A"] == "" and out2["-ni"] is False
        assert abs(float(out2["-r"]) - 0.5) < 1e-6
        assert out2["extra_args"] == ""
    finally:
        dlg.deleteLater()
