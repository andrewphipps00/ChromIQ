"""Knut's beta.29/.31 follow-ups:

1. Convert i1Profiler → TI3: picking a *second* input file must update the
   auto-filled output name (it used to stay stuck on the first file's stem).
2. Measurement Report → Add measurement: the picker must accept i1Profiler
   files (.mxf/.txt/.cxf) directly and convert them — no export step."""
import os
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    from core.settings import AppSettings

    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    return s


# --- Issue 2: Convert dialog auto-name follows each new input file -----------

def test_convert_output_name_follows_each_input(qapp, settings):
    from core.argyll_runner import ArgyllRunner
    from ui.dialogs.tools_dialogs import I1ProfilerToTi3Dialog

    dlg = I1ProfilerToTi3Dialog(ArgyllRunner(settings), settings)

    def pick(path):
        dlg._pick_input_file = lambda *a, **k: Path(path)
        dlg._pick_txt()

    pick("/data/Epson-P900_2026-01-06.txt")
    assert dlg._output.name == "Epson-P900_2026-01-06"
    # Second file → name UPDATES (the bug: it stayed on the first stem).
    pick("/data/Epson-P900_2026-01-20.txt")
    assert dlg._output.name == "Epson-P900_2026-01-20"


def test_convert_keeps_a_name_the_user_typed(qapp, settings):
    from core.argyll_runner import ArgyllRunner
    from ui.dialogs.tools_dialogs import I1ProfilerToTi3Dialog

    dlg = I1ProfilerToTi3Dialog(ArgyllRunner(settings), settings)
    dlg._pick_input_file = lambda *a, **k: Path("/data/first.txt")
    dlg._pick_txt()
    dlg._output._name_edit.setText("my-custom-name")          # user overrides
    dlg._pick_input_file = lambda *a, **k: Path("/data/second.txt")
    dlg._pick_txt()
    assert dlg._output.name == "my-custom-name"                # preserved


# --- Issue 1: Measurement Report accepts i1Profiler files directly -----------

def test_report_as_ti3_passthrough(qapp, settings, tmp_path):
    from ui.dialogs.measurement_report_dialog import MeasurementReportDialog

    host = types.SimpleNamespace(
        _settings=settings,
        _view=types.SimpleNamespace(setHtml=lambda _h: None))
    ti3 = tmp_path / "m.ti3"
    ti3.write_text("x")
    assert MeasurementReportDialog._as_ti3(host, ti3) == ti3   # used as-is


def test_report_as_ti3_converts_i1profiler(qapp, settings, tmp_path, monkeypatch):
    from ui.dialogs.measurement_report_dialog import MeasurementReportDialog

    seen = {}

    def fake_convert(src, argyll, out_dir):
        seen["src"] = Path(src)
        out = Path(out_dir) / f"{Path(src).stem}.ti3"
        out.write_text("y")
        return out

    monkeypatch.setattr(
        "workflow.reference_convert.convert_i1profiler_measurement", fake_convert)
    host = types.SimpleNamespace(
        _settings=settings,
        _view=types.SimpleNamespace(setHtml=lambda _h: None))
    txt = tmp_path / "Epson-P900_2026-01-06.txt"
    txt.write_text("z")
    out = MeasurementReportDialog._as_ti3(host, txt)
    assert out is not None and out.suffix == ".ti3"
    assert out.stem == "Epson-P900_2026-01-06"                 # stem preserved
    assert seen["src"] == txt


def test_report_as_ti3_reports_conversion_error(qapp, settings, tmp_path, monkeypatch):
    from ui.dialogs.measurement_report_dialog import MeasurementReportDialog

    def boom(src, argyll, out_dir):
        raise ValueError("not an i1Profiler file")

    monkeypatch.setattr(
        "workflow.reference_convert.convert_i1profiler_measurement", boom)
    shown = {}
    host = types.SimpleNamespace(
        _settings=settings,
        _view=types.SimpleNamespace(setHtml=lambda h: shown.setdefault("html", h)),
        _error_html=lambda msg: f"<err>{msg}</err>")
    out = MeasurementReportDialog._as_ti3(host, tmp_path / "bad.txt")
    assert out is None and "not an i1Profiler file" in shown["html"]
