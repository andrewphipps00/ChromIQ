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


def test_list_project_reports_gathers_across_runs(tmp_path: Path) -> None:
    """#40: the printer's history spans every run of the project, oldest first."""
    import json
    from workflow.measurement_report import list_project_reports
    from core.file_manager import REPORTS_DIRNAME
    runs = tmp_path / "runs"
    for run, created in (("run1", "2026-01-01T09:00:00"),
                         ("run2", "2026-03-01T09:00:00"),
                         ("run1", "2026-02-01T09:00:00")):
        d = runs / run / REPORTS_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / f"report_{created.replace(':', '-')}.json").write_text(
            json.dumps({"created": created, "chart": "P"}))
    got = list_project_reports(runs / "run2")           # any run dir
    assert len(got) == 3
    stamps = [json.loads(p.read_text())["created"] for p in got]
    assert stamps == sorted(stamps)                     # oldest-first, cross-run


def test_report_trend_series_extracts_plottable_metrics() -> None:
    from workflow.measurement_report import report_trend
    reports = [
        {"created": "2026-01-01", "chart": "P",
         "de00": {"mean": 3.0, "max": 7.0, "p95": 5.0},
         "paper_white": {"lab": [96.0, 0, 0]}, "max_black": {"lab": [12.0, 0, 0]}},
        {"created": "2026-02-01", "chart": "P",              # no reference
         "paper_white": {"lab": [95.0, 0, 0]}, "max_black": {"lab": [12.5, 0, 0]}},
        {"created": "2026-03-01", "chart": "P", "patches": 100},  # nothing plottable
    ]
    tr = report_trend(reports)
    assert len(tr) == 2                                 # third has no metric
    assert tr[0]["mean"] == 3.0 and tr[0]["white_L"] == 96.0
    assert "mean" not in tr[1] and tr[1]["white_L"] == 95.0


def test_trend_chart_widget_visibility(qapp) -> None:
    from ui.dialogs.measurement_report_dialog import _TrendChart
    w = _TrendChart()
    w.set_series([{"created": "2026-01-01", "mean": 3.0}], dark=True)
    assert not w.has_trend() and not w.isVisible()      # one point → hidden
    w.set_series([{"created": "2026-01-01", "mean": 3.0},
                  {"created": "2026-02-01", "mean": 2.5, "max": 6.0}], dark=True)
    assert w.has_trend()
    w.resize(400, 200)
    w.grab()                                            # paints without error
