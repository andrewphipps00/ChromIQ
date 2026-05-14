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
