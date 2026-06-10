"""Regression tests for lp command construction.

The PS path must NOT forward `orientation-requested` to lp — orientation is
baked into setpagedevice PageSize by PostScriptGenerator. If the option
were forwarded, Apple's pstops filter would rotate the already-landscape PS
a second time, which is what caused issues #14 / #15.

The TIFF fallback path is unaffected: raw TIFF has no inherent geometry,
so the driver needs orientation-requested to rotate it.
"""
from __future__ import annotations

from pathlib import Path

import workflow.cups_printer as cups_printer
from workflow.cups_printer import CupsRawPrinter, PrintConfig


def test_ps_command_omits_orientation_requested_even_when_set() -> None:
    cfg = PrintConfig(printer_name="Test", options={"PageSize": "A3"})
    cmd = CupsRawPrinter._build_lp_command_ps(Path("/tmp/x.ps"), cfg, orientation=4)

    assert not any("orientation-requested" in token for token in cmd), cmd
    # Sanity: the rest of the command is still well-formed.
    assert cmd[:3] == ["lp", "-d", "Test"]
    assert "PageSize=A3" in cmd
    assert cmd[-1] == "/tmp/x.ps"


def test_tiff_command_includes_orientation_requested_when_set() -> None:
    cfg = PrintConfig(printer_name="Test", options={})
    cmd = CupsRawPrinter._build_lp_command_tiff(
        Path("/tmp/x.tif"), cfg, n_ch=3, orientation=4,
    )
    assert "orientation-requested=4" in cmd, cmd


def test_tiff_command_omits_orientation_requested_when_none() -> None:
    cfg = PrintConfig(printer_name="Test", options={})
    cmd = CupsRawPrinter._build_lp_command_tiff(
        Path("/tmp/x.tif"), cfg, n_ch=3, orientation=None,
    )
    assert not any("orientation-requested" in token for token in cmd), cmd


def test_ps_command_injects_vendor_no_cm_option(monkeypatch) -> None:
    """A driver whose PPD exposes a no-CM option (e.g. Canon CNIJIntent2=1001)
    must get it forwarded to lp on the PostScript path."""
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue",
        lambda name: [("CNIJIntent2", "1001")],
    )
    cfg = PrintConfig(printer_name="Canon_PRO_300_series", options={})
    cmd = CupsRawPrinter._build_lp_command_ps(Path("/tmp/x.ps"), cfg, orientation=None)
    assert "CNIJIntent2=1001" in cmd, cmd


def test_tiff_command_injects_vendor_no_cm_option(monkeypatch) -> None:
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue",
        lambda name: [("CNIJIntent2", "1001")],
    )
    cfg = PrintConfig(printer_name="Canon_PRO_300_series", options={})
    cmd = CupsRawPrinter._build_lp_command_tiff(
        Path("/tmp/x.tif"), cfg, n_ch=3, orientation=None,
    )
    assert "CNIJIntent2=1001" in cmd, cmd


def test_all_lp_paths_send_apple_no_cm_key(monkeypatch) -> None:
    """Every lp path must carry AP_ColorMatchingMode=AP_ApplicationColorMatching:
    it stops cgpdftoraster applying the PPD's cupsICCProfile transform, which
    HP DesignJet PPDs trigger even for untagged device colour (no-ink verified
    2026-06; Canon/Epson stay bit-exact with the key set)."""
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue", lambda name: [],
    )
    cfg = PrintConfig(printer_name="HP_Designjet_Z2100", options={})
    cmds = [
        CupsRawPrinter._build_lp_command_ps(Path("/tmp/x.ps"), cfg, orientation=None),
        CupsRawPrinter._build_lp_command_pdf(Path("/tmp/x.pdf"), cfg),
        CupsRawPrinter._build_lp_command_tiff(
            Path("/tmp/x.tif"), cfg, n_ch=3, orientation=None,
        ),
    ]
    for cmd in cmds:
        assert "AP_ColorMatchingMode=AP_ApplicationColorMatching" in cmd, cmd


def test_no_vendor_option_when_ppd_has_none(monkeypatch) -> None:
    """Epson (and anything without a matching PPD option) is unaffected — the
    raster options already disable CM there, so nothing extra is injected."""
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue", lambda name: [],
    )
    cfg = PrintConfig(printer_name="EPSON_ET_8550", options={})
    cmd = CupsRawPrinter._build_lp_command_ps(Path("/tmp/x.ps"), cfg, orientation=None)
    assert not any("CNIJ" in token for token in cmd), cmd


# ---------------------------------------------------------------------------
# Exact-size PDF fallback (pdf_print_fallback setting)
# ---------------------------------------------------------------------------

import numpy as np
import tifffile


def _write_rgb_tiff(path: Path) -> None:
    arr = np.zeros((30, 20, 3), dtype=np.uint8)
    tifffile.imwrite(str(path), arr, resolution=(200, 200), resolutionunit="INCH")


def test_pdf_command_options(monkeypatch) -> None:
    """The PDF job must carry Duplex/sides and the vendor no-CM key, but
    neither ColorSync=None (PS-specific) nor orientation-requested (geometry
    is baked into the MediaBox)."""
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue",
        lambda name: [("CNIJIntent2", "1001")],
    )
    cfg = PrintConfig(printer_name="Canon_PRO_300_series", options={"PageSize": "A4"})
    cmd = CupsRawPrinter._build_lp_command_pdf(Path("/tmp/x.pdf"), cfg)
    assert cmd[:3] == ["lp", "-d", "Canon_PRO_300_series"]
    assert "PageSize=A4" in cmd
    assert "Duplex=None" in cmd
    assert "sides=one-sided" in cmd
    assert "CNIJIntent2=1001" in cmd
    assert not any("ColorSync" in tok for tok in cmd), cmd
    assert not any("orientation-requested" in tok for tok in cmd), cmd
    assert cmd[-1] == "/tmp/x.pdf"


def _reject_ps_then(monkeypatch, printer: CupsRawPrinter, calls: list[str]) -> None:
    """Make lp reject PostScript, and record which fallback runs."""
    monkeypatch.setattr(
        printer, "_run_lp_result", lambda cmd: (1, "Unsupported document-format: postscript"),
    )
    monkeypatch.setattr(printer, "_cancel_pending_jobs", lambda name: None)
    monkeypatch.setattr(
        printer, "_print_job_pdf",
        lambda *a, **k: calls.append("pdf"),
    )
    monkeypatch.setattr(
        printer, "_print_job_tiff",
        lambda *a, **k: calls.append("tiff"),
    )


def test_ps_rejection_falls_back_to_tiff_by_default(monkeypatch, tmp_path: Path) -> None:
    tiff = tmp_path / "chart.tif"
    _write_rgb_tiff(tiff)
    printer = CupsRawPrinter()
    calls: list[str] = []
    _reject_ps_then(monkeypatch, printer, calls)
    printer.print_job_ps(tiff, PrintConfig(printer_name="Test"))
    assert calls == ["tiff"]


def test_ps_rejection_falls_back_to_pdf_when_enabled(monkeypatch, tmp_path: Path) -> None:
    tiff = tmp_path / "chart.tif"
    _write_rgb_tiff(tiff)
    printer = CupsRawPrinter()
    calls: list[str] = []
    _reject_ps_then(monkeypatch, printer, calls)
    printer.print_job_ps(tiff, PrintConfig(printer_name="Test"), pdf_fallback=True)
    assert calls == ["pdf"]


def test_pdf_submission_failure_retries_as_tiff(monkeypatch, tmp_path: Path) -> None:
    tiff = tmp_path / "chart.tif"
    _write_rgb_tiff(tiff)
    printer = CupsRawPrinter()
    calls: list[str] = []
    monkeypatch.setattr(printer, "_run_lp_result", lambda cmd: (1, "boom"))
    monkeypatch.setattr(printer, "_cancel_pending_jobs", lambda name: None)
    monkeypatch.setattr(
        printer, "_print_job_tiff", lambda *a, **k: calls.append("tiff"),
    )
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue", lambda name: [],
    )
    printer._print_job_pdf(tiff, PrintConfig(printer_name="Test"))
    assert calls == ["tiff"]


def test_pdf_success_reports_zero(monkeypatch, tmp_path: Path) -> None:
    tiff = tmp_path / "chart.tif"
    _write_rgb_tiff(tiff)
    printer = CupsRawPrinter()
    results: list[int] = []
    submitted: list[list[str]] = []

    def fake_run(cmd):
        submitted.append(cmd)
        return 0, ""

    monkeypatch.setattr(printer, "_run_lp_result", fake_run)
    monkeypatch.setattr(
        cups_printer, "vendor_no_cm_settings_for_queue", lambda name: [],
    )
    printer._print_job_pdf(
        tiff, PrintConfig(printer_name="Test", options={"PageSize": "A4"}),
        on_finish=results.append, page_size_pt=(595.28, 841.89),
    )
    assert results == [0]
    assert len(submitted) == 1
    assert submitted[0][-1].endswith(".pdf")
    assert "PageSize=A4" in submitted[0]
