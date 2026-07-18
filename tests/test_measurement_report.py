"""Measurement report: stats, worst patches, white/black, over-time compare."""
from __future__ import annotations

from pathlib import Path

import pytest

from workflow.measurement_report import (
    build_report, compare_reports, list_reports, save_report,
)

_TI2 = """CTI1

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT
NUMBER_OF_SETS 3
BEGIN_DATA
1 "A1" 100 100 100 95.0 100.0 108.0
2 "A2" 0 0 0 1.0 1.0 1.0
3 "A3" 100 0 0 41.0 21.0 2.0
END_DATA
"""

# Measured: white & black spot-on, red patch off by a visible amount.
_TI3 = """CTI3

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT
NUMBER_OF_SETS 3
BEGIN_DATA
1 "A1" 100 100 100 95.0 100.0 108.0
2 "A2" 0 0 0 1.0 1.0 1.0
3 "A3" 100 0 0 36.0 18.0 3.0
END_DATA
"""


@pytest.fixture()
def chart(tmp_path: Path) -> Path:
    (tmp_path / "c.ti2").write_text(_TI2)
    (tmp_path / "c.ti3").write_text(_TI3)
    return tmp_path / "c.ti3"


def test_build_report_stats_and_worst(chart):
    r = build_report(chart)
    assert r["patches"] == 3
    assert r["de00"]["n"] == 3
    # The red patch is the worst; white/black are near-zero.
    assert r["worst_patches"][0]["loc"] == "A3"
    assert r["worst_patches"][0]["de"] == pytest.approx(r["de00"]["max"], abs=0.01)
    assert set(r["worst_patches"][0]) >= {"expected_hex", "measured_hex", "de"}
    # White is the lightest, black the darkest.
    assert r["paper_white"]["loc"] == "A1"
    assert r["max_black"]["loc"] == "A2"


def test_report_without_reference(tmp_path):
    # No .ti2 → no ΔE stats, but white/black still reported.
    (tmp_path / "c.ti3").write_text(_TI3)
    r = build_report(tmp_path / "c.ti3")
    assert "de00" not in r
    assert r["paper_white"]["loc"] == "A1"


def test_save_list_and_compare(chart, tmp_path):
    r1 = build_report(chart)
    p1 = save_report(r1, tmp_path)
    assert p1.exists() and list_reports(tmp_path) == [p1]
    # A second, drifted measurement (red patch worse).
    (tmp_path / "c.ti3").write_text(_TI3.replace("36.0 18.0 3.0", "30.0 15.0 4.0"))
    r2 = build_report(chart)
    cmp = compare_reports(r1, r2)
    assert "de00_mean_delta" in cmp
    assert cmp["de00_max_delta"] > 0          # it drifted worse
    assert "paper_white_de" in cmp
