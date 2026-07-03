"""Building the scanin command line + error parsing for the scanner roundtrip
(#98). No Argyll binary is invoked."""
from pathlib import Path

import pytest

from workflow.scanin_runner import ScaninParams, ScaninRunner, scanin_args


def test_auto_args_no_fiducials(tmp_path):
    args = scanin_args(tmp_path / "scan.tif", tmp_path / "c.cht",
                       tmp_path / "c.cie", corners=None)
    assert "-F" not in args
    assert args[-3:] == [str(tmp_path / "scan.tif"), str(tmp_path / "c.cht"),
                         str(tmp_path / "c.cie")]
    assert "-p" in args and "-v" in args


def test_manual_fiducial_formatting(tmp_path):
    corners = [(10.0, 20.0), (200.0, 22.0), (198.0, 300.0), (12.0, 298.0)]
    args = scanin_args(tmp_path / "s.tif", tmp_path / "c.cht", tmp_path / "c.cie",
                       corners=corners)
    i = args.index("-F")
    assert args[i + 1] == "10,20,200,22,198,300,12,298"   # x1,y1..x4,y4, TL→BL


def test_diag_adds_flag_and_trailing_path(tmp_path):
    diag = tmp_path / "diag.tif"
    args = scanin_args(tmp_path / "s.tif", tmp_path / "c.cht", tmp_path / "c.cie",
                       diag=diag)
    assert "-dipn" in args
    assert args[-1] == str(diag)          # diag is the trailing positional
    # cht/cie still precede the diag image
    assert args[-2] == str(tmp_path / "c.cie")


def test_bad_corner_count_raises(tmp_path):
    with pytest.raises(ValueError):
        scanin_args(tmp_path / "s.tif", tmp_path / "c.cht", tmp_path / "c.cie",
                    corners=[(0, 0), (1, 1)])


def test_out_ti3_never_collides_with_printer_profile(tmp_path):
    # Even if the scan is named exactly like the chart and sits in the run
    # folder, the scanner .ti3 gets a distinct -scanner name (never <stem>.ti3).
    p = ScaninParams(tmp_path / "MyChart.tif", tmp_path / "c.cht", tmp_path / "c.cie")
    assert p.out_ti3 == tmp_path / "MyChart-scanner.ti3"
    assert p.out_ti3 != tmp_path / "MyChart.ti3"
    # scanin gets -O with that name so it writes there, not the default
    args = ScaninRunner(runner=None)._build_args(p)
    assert "-O" in args and args[args.index("-O") + 1] == "MyChart-scanner.ti3"


def test_error_parsing_recognition_and_depth():
    r = ScaninRunner(runner=None)
    r._scan_line("Scanin failed with code 0x5, no reference located")
    r._scan_line("TIFF Input file 'x.tif' must be 8 or 16 bits/channel")
    keys = [k for k, _ in r._matched_errors]
    assert "recognition_failed" in keys and "bit_depth" in keys
    # the friendly text for the first failure mentions re-placing the corners
    key, msg = r.primary_failure()
    assert key == "recognition_failed" and "corners" in msg.lower()


# Exact messages copied from Argyll 3.5.0 scanin/scanin.c, each mapped to the
# friendly bucket it should collapse into.
def _first_key(*lines):
    r = ScaninRunner(runner=None)
    for ln in lines:
        r._scan_line(ln)
    fail = r.primary_failure()
    return fail[0] if fail else None


def test_reference_damaged_messages():
    for ln in (
        "Input file 'chart.cie' isn't a CTI2 format file",
        "Input file 'chart.cie' doesn't contain at least one table",
        "Input file 'chart.cie' doesn't contain any data sets",
        "Input file 'chart.cie' doesn't contain keyword COLOR_REP",
        "Input file 'chart.cie' keyword COLOR_REP has unknown value",
        "Input file 'chart.cie' doesn't contain field SAMPLE_ID",
        "Input file 'chart.cie' Field SAMPLE_LOC is wrong type",
        "Couldn't find location 'A1' in 'chart.cie'",
        "Couldn't find sample 'A1' in 'chart.ti3'",
    ):
        assert _first_key(ln) == "reference_damaged", ln


def test_reference_mismatch_messages():
    for ln in (
        "Different number of patches in 'x.ti3' (10) to expected(12)",
        "'a.cie' and 'b.ti3' field id's don't match at patch 3",
        "'a.cie' and 'b.ti3' device values (1.0 2.0) don't match at patch 3 4",
        "File 'a.cie' has different device space to 'b.ti3'",
    ):
        assert _first_key(ln) == "reference_mismatch", ln


def test_reference_io_and_oom_messages():
    assert _first_key("CGATS file 'x.cie' read error : unexpected EOF") == "reference_io"
    assert _first_key("Write error to 'out.ti3' : disk full") == "reference_io"
    assert _first_key("Unable to allocate scanrd object") == "out_of_memory"
    assert _first_key("Malloc failed!") == "out_of_memory"
