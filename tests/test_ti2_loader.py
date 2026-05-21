"""Tests for the .ti2 instrument-detection helpers used by auto -B selection.

The Measure tab decides whether to disable bidirectional strip recognition
(chartread -B) from the chart's TARGET_INSTRUMENT. Verified against real files:
the i1 Pro family (i1 Pro / Pro 2 / Pro 3 / Pro 3+) is all tagged
"GretagMacbeth i1 Pro" and reads both directions; the ColorMunki (and its
i1Studio rebrand) is tagged "X-Rite ColorMunki" and reads one direction only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ui.ti2_loader import disable_bidir_for_instrument, read_target_instrument


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "chart.ti2"
    p.write_text(body, encoding="utf-8")
    return p


def test_read_target_instrument_i1pro(tmp_path: Path) -> None:
    p = _write(tmp_path, 'CTI2\nKEYWORD "TARGET_INSTRUMENT"\nTARGET_INSTRUMENT "GretagMacbeth i1 Pro"\n')
    assert read_target_instrument(p) == "GretagMacbeth i1 Pro"


def test_read_target_instrument_colormunki(tmp_path: Path) -> None:
    p = _write(tmp_path, 'TARGET_INSTRUMENT "X-Rite ColorMunki"\n')
    assert read_target_instrument(p) == "X-Rite ColorMunki"


def test_read_target_instrument_missing_keyword(tmp_path: Path) -> None:
    p = _write(tmp_path, "CTI2\nNUMBER_OF_SETS 100\n")
    assert read_target_instrument(p) is None


def test_read_target_instrument_missing_file(tmp_path: Path) -> None:
    assert read_target_instrument(tmp_path / "nope.ti2") is None


@pytest.mark.parametrize(
    "name, expected",
    [
        ("GretagMacbeth i1 Pro", False),   # i1 Pro / Pro 2 / Pro 3 / Pro 3+
        ("X-Rite i1 Pro 3", False),        # robust to alternate spellings
        ("X-Rite ColorMunki", True),       # one direction only
        ("ColorMunki Photo", True),
        ("GretagMacbeth SpectroScan", False),
        (None, False),                     # unknown / no file -> bidir allowed
        ("", False),
    ],
)
def test_disable_bidir_for_instrument(name, expected) -> None:
    assert disable_bidir_for_instrument(name) is expected
