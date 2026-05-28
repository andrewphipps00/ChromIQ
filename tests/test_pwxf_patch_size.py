"""Patch-size + header-length encoding for the .pwxf workflow export.

i1Profiler stores the patch size (and the i1iSis header length / "Vorlauf") as a
slider position, not a mm value: ``Percent = (mm - lo) / (hi - lo) * 100`` against
a per-device range. Writing percent 0 (our original bug) makes i1Profiler use the
slider minimum and warn. These tests pin the formula and the device ranges to the
values reverse-engineered from real i1Profiler files saved at the slider extremes.
"""
from __future__ import annotations

import pytest

from ui.dialogs.tools_dialogs import (
    _PWXF_DEVICES,
    _PWXF_VORLAUF_MM,
    _device_default_size,
    _patch_percent,
)


@pytest.mark.parametrize("mm,lo,hi,expected", [
    # Verified against the i1Pro 3 files (1.pwxf / 2.pwxf) and defaults:
    (8, 6, 25, 10.526315789473683),   # width 8 mm
    (10, 6, 25, 21.052631578947366),  # width 10 mm
    (7, 6, 12, 16.666666666666664),   # height 7 mm (i1Profiler's own default)
    (10, 6, 12, 66.66666666666666),   # height 10 mm
    # Verified against the i1iO 2 min file (non-trivial saved percents):
    (8, 6, 20, 14.285714285714285),   # width 8 mm
    (7.5, 7, 20, 3.8461538461538463), # height 7.5 mm
    # i1iSis header length (Vorlauf), range 32..80 mm; 80 mm = 100 % (screenshot):
    (80, 32, 80, 100.0),
    (32, 32, 80, 0.0),
    (56, 32, 80, 50.0),
])
def test_percent_matches_real_files(mm, lo, hi, expected):
    assert _patch_percent(mm, lo, hi) == pytest.approx(expected)


def test_percent_clamps():
    assert _patch_percent(3, 6, 25) == 0.0
    assert _patch_percent(99, 6, 25) == 100.0


def test_all_twelve_devices_present_and_well_formed():
    assert len(_PWXF_DEVICES) == 12
    for name, (wlo, whi, hlo, hhi, mode, vorlauf) in _PWXF_DEVICES.items():
        assert wlo < whi and hlo < hhi, name
        assert mode in (None, 1, 6), name           # observed modes
        assert isinstance(vorlauf, bool)
        # Only the i1iSis sheet scanners carry the header-length lead-in.
        assert vorlauf == name.startswith("i1iSis"), name


def test_default_size_is_warning_free():
    # PLUS/M3 (slider min 16) default to 20×20 — at/above their 20 mm scan min.
    assert _device_default_size(16, 40, 16, 20) == (20, 20)
    # Standard devices default to 8×7, clamped into range.
    assert _device_default_size(6, 25, 6, 12) == (8, 7)
    assert _device_default_size(7, 25, 8, 12) == (8, 8)   # i1Pro 2 (height min 8)


def test_vorlauf_range():
    assert _PWXF_VORLAUF_MM == (32, 80)
