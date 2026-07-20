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


def test_report_cube_corners(chart):
    r = build_report(chart)
    corners = r["corners"]
    assert [c["name"] for c in corners] == ["W", "K", "R", "G", "B", "C", "M", "Y"]
    by = {c["name"]: c for c in corners}
    # Nearest patch by device RGB to each corner.
    assert by["W"]["loc"] == "A1" and by["K"]["loc"] == "A2" and by["R"]["loc"] == "A3"
    # A corner with a reference carries expected + measured + ΔE00.
    assert {"expected_hex", "hex", "de"} <= set(by["R"])
    # ref_xyz regression (the old double-×100 overflowed _srgb_hex to white):
    # the RED corner's EXPECTED swatch must be a real red, not clipped white.
    assert by["R"]["expected_hex"].lower() != "#ffffff"
    er, eg, eb = (int(by["R"]["expected_hex"][i:i + 2], 16) for i in (1, 3, 5))
    assert er > eg and er > eb                          # clearly reddish


def test_report_with_design_reference_is_marked(chart):
    # A .ti2 beside the .ti3 → compared against the chart's design.
    r = build_report(chart)
    assert r["reference_source"] == "design"


def test_report_without_reference_uses_device_values(tmp_path):
    # No .ti2 → the report is self-contained: it derives the expected colour
    # from the measurement's own device RGB, so ΔE is still available (Knut).
    (tmp_path / "c.ti3").write_text(_TI3)
    r = build_report(tmp_path / "c.ti3")
    assert r["reference_source"] == "device"
    assert r["de00"]["n"] == 3
    assert r["paper_white"]["loc"] == "A1"
    # White/black measured near their sRGB estimate → small ΔE; the red patch,
    # measured off from sRGB red, is the worst.
    assert r["worst_patches"][0]["loc"] == "A3"


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


def test_export_pdf_writes_file(qapp, chart, tmp_path, monkeypatch) -> None:
    from PyQt6.QtWidgets import QFileDialog
    from ui.dialogs.measurement_report_dialog import MeasurementReportDialog
    out = tmp_path / "report.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PDF (*.pdf)")))
    dlg = MeasurementReportDialog({"appearance": "light"}, initial_ti3=chart)
    dlg._export_pdf()
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[:5] == b"%PDF-"


def test_trend_chart_widget_visibility(qapp) -> None:
    from PyQt6.QtGui import QColor
    from ui.dialogs.measurement_report_dialog import _TrendChart
    metrics = [("Average", QColor("#56d6a5"), lambda pt: pt.get("mean")),
               ("Worst", QColor("#e0864b"), lambda pt: pt.get("max"))]
    w = _TrendChart()
    w.set_data([{"created": "2026-01-01", "mean": 3.0}], metrics, dark=True)
    assert not w.has_trend()                            # one point → no trend
    w.set_data([{"created": "2026-01-01", "mean": 3.0},
                {"created": "2026-02-01", "mean": 2.5, "max": 6.0}], metrics, dark=True)
    assert w.has_trend()
    w.resize(400, 200)
    w.grab()                                            # paints without error


def test_stats_split_metrics():
    from workflow.measurement_report import _stats
    # 20 patches: nineteen at 1.0, one at 10.0 (the worst 5%).
    s = _stats([1.0] * 19 + [10.0])
    assert s["avg_all"] == pytest.approx(1.45, abs=0.01)
    assert s["max_all"] == 10.0
    assert s["avg_low95"] == pytest.approx(1.0, abs=0.01)   # best 95% are all 1.0
    assert s["max_low95"] == pytest.approx(1.0, abs=0.01)   # worst-5% (the 10) excluded
    assert s["avg_high5"] == pytest.approx(10.0, abs=0.01)  # the single worst patch


def test_accuracy_verdict_pass_fail():
    from workflow.measurement_report import accuracy_verdict
    de = {"avg_all": 1.5, "avg_low95": 1.0, "avg_high5": 2.5,
          "max_all": 3.5, "max_low95": 2.0}
    rows, all_pass = accuracy_verdict(de, avg_thr=2.0, max_thr=3.0)
    got = {r["key"]: r["pass"] for r in rows}
    assert got["avg_all"] and got["avg_low95"]           # ≤ 2.0
    assert not got["avg_high5"]                          # 2.5 > 2.0 → fail
    assert not got["max_all"]                            # 3.5 > 3.0 → fail
    assert got["max_low95"]                              # 2.0 ≤ 3.0
    assert all_pass is False


def test_report_scope_warnings():
    from workflow.measurement_report import report_scope
    def mk(chart, created, inst, present_all=True):
        corners = [{"name": n, "present": (present_all or n not in ("C", "M"))}
                   for n in ("W", "K", "R", "G", "B", "C", "M", "Y")]
        return {"chart": chart, "created": created, "instrument": inst, "corners": corners}
    runs = [mk("P1", "2026-01-01T09:00:00", "i1 Pro"),
            mk("P1", "2026-02-01T09:00:00", "i1 Pro"),
            mk("P2", "2026-03-01T09:00:00", "ColorMunki"),          # odd instrument
            mk("P1", "2026-04-01T09:00:00", "i1 Pro", present_all=False)]  # missing C,M
    sc = report_scope(runs)
    assert sc["total"] == 4
    assert sc["date_range"] == ("2026-01-01", "2026-04-01")
    assert {p["name"] for p in sc["profiles"]} == {"P1", "P2"}
    kinds = {w["kind"] for w in sc["warnings"]}
    assert kinds == {"instrument", "corners"}
    inst_w = next(w for w in sc["warnings"] if w["kind"] == "instrument")
    assert inst_w["dominant"] == "i1 Pro" and len(inst_w["runs"]) == 1
    corn_w = next(w for w in sc["warnings"] if w["kind"] == "corners")
    assert corn_w["runs"][0]["missing"] == ["C", "M"]


def test_report_scope_clean_no_warnings():
    from workflow.measurement_report import report_scope
    corners = [{"name": n, "present": True} for n in "WKRGBCMY"]
    runs = [{"chart": "P", "created": "2026-01-01T09:00:00",
             "instrument": "i1 Pro", "corners": corners}]
    assert report_scope(runs)["warnings"] == []
