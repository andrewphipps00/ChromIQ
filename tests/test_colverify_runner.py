"""Tools → "Verify against reference" (colverify wrapper).

Covers, without needing the Argyll binary:
  * parsing pasted reference tables (Lab/XYZ, index- and name-prefixed rows);
  * the reference .ti3 emitter (SAMPLE_ID 1..N, correct PCS fields, optional RGB);
  * chart patch-count cross-check reading only the first CGATS table;
  * colverify argument construction and summary/per-patch parsing.

If the colverify binary is present, an end-to-end run asserts a zero-error
self-comparison.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from workflow.colverify_runner import (
    ColverifyParams,
    ColverifyRunner,
    chart_patch_count,
    parse_reference_values,
    write_reference_ti3,
)


# --- reference table parsing ----------------------------------------------

def test_parse_plain_triples():
    rows = parse_reference_values("100 0 0\n50.5 -1.2 3.4\n")
    assert rows == [(100.0, 0.0, 0.0), (50.5, -1.2, 3.4)]


def test_parse_tolerates_index_and_name_and_separators():
    text = "# header\n1, 100, 0, 0\nGS01\t50\t0\t0\n\n  2  95.0  -1.0  2.0 \n"
    assert parse_reference_values(text) == [
        (100.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
        (95.0, -1.0, 2.0),
    ]


def test_parse_rejects_short_line():
    with pytest.raises(ValueError, match="Line 2"):
        parse_reference_values("100 0 0\n50 0\n")


def test_parse_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        parse_reference_values("\n# only a comment\n")


# --- reference .ti3 emitter ------------------------------------------------

def test_write_reference_lab(tmp_path: Path):
    out = write_reference_ti3(
        tmp_path / "ref.ti3", [(100.0, 0.0, 0.0), (50.0, 1.0, -2.0)], space="LAB"
    )
    text = out.read_text()
    assert "SAMPLE_ID LAB_L LAB_A LAB_B" in text
    assert "NUMBER_OF_SETS 2" in text
    assert 'DEVICE_CLASS "OUTPUT"' in text
    body = text.split("BEGIN_DATA\n", 1)[1].split("END_DATA", 1)[0].splitlines()
    assert body[0].split()[0] == "1"          # SAMPLE_ID starts at 1
    assert body[1].split()[0] == "2"
    assert body[0].split()[1:] == ["100.0000", "0.0000", "0.0000"]


def test_write_reference_xyz_with_rgb(tmp_path: Path):
    out = write_reference_ti3(
        tmp_path / "ref.ti3",
        [(95.0, 100.0, 108.0)],
        space="XYZ",
        rgb=[(100.0, 100.0, 100.0)],
    )
    text = out.read_text()
    assert "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z" in text
    assert 'COLOR_REP "RGB_XYZ"' in text


def test_write_reference_rgb_length_mismatch(tmp_path: Path):
    with pytest.raises(ValueError, match="doesn't match"):
        write_reference_ti3(
            tmp_path / "ref.ti3", [(1.0, 2.0, 3.0)], rgb=[(0, 0, 0), (1, 1, 1)]
        )


# --- chart patch-count cross-check ----------------------------------------

def test_chart_patch_count_first_table_only(tmp_path: Path):
    # A .ti1-like file with three tables; only the first (3 rows) is the patch list.
    chart = tmp_path / "chart.ti1"
    chart.write_text(
        "BEGIN_DATA\n1 a\n2 b\n3 c\nEND_DATA\n"
        "BEGIN_DATA\nx\ny\nEND_DATA\n"
        "BEGIN_DATA\np\nq\nr\ns\nEND_DATA\n"
    )
    assert chart_patch_count(chart) == 3


# --- colverify args + parsing ---------------------------------------------

def test_build_args_defaults(tmp_path: Path):
    p = ColverifyParams(ref_ti3=tmp_path / "ref.ti3", measured_ti3=tmp_path / "m.ti3")
    args = ColverifyRunner(None)._build_args(p)
    assert args == ["-v", "2", "-k", "-s", "ref.ti3", "m.ti3"]


def test_build_args_cie76_no_sort(tmp_path: Path):
    p = ColverifyParams(
        ref_ti3=tmp_path / "ref.ti3",
        measured_ti3=tmp_path / "m.ti3",
        de_formula="",
        sort=False,
        per_patch=False,
    )
    assert ColverifyRunner(None)._build_args(p) == ["ref.ti3", "m.ti3"]


def test_parse_results_summary_and_patches():
    log = (
        "Verify results:\n"
        "1: 100.0 0.0 0.0 <=> 99.5 0.1 -0.2  de 0.530000\n"
        "12: 50.0 1.0 -2.0 <=> 51.0 1.2 -2.1  de 1.040000\n"
        "  Total errors (CIEDE2000):     peak = 1.040000, avg = 0.785000\n"
        "  Worst 10% errors (CIEDE2000): peak = 1.040000, avg = 1.040000\n"
    )
    res = ColverifyRunner(None).parse_results(log)
    assert res.peak_de == pytest.approx(1.04)
    assert res.avg_de == pytest.approx(0.785)
    assert res.patch_errors == [("1", 0.53), ("12", 1.04)]


# --- end-to-end (binary required) -----------------------------------------

def _argyll_bin(name: str) -> str | None:
    cand = Path("/Applications/Argyll/bin") / name
    if cand.exists():
        return str(cand)
    return shutil.which(name)


def test_colverify_zero_error_self_compare(tmp_path: Path):
    binp = _argyll_bin("colverify")
    if not binp:
        pytest.skip("colverify binary not available")
    rows = [(100.0, 0.0, 0.0), (50.0, 1.0, -2.0), (20.0, -5.0, 8.0)]
    ref = write_reference_ti3(tmp_path / "ref.ti3", rows, space="LAB")
    out = subprocess.run(
        [binp, "-k", str(ref), str(ref)],
        capture_output=True, text=True, cwd=tmp_path,
    )
    res = ColverifyRunner(None).parse_results(out.stdout)
    assert res.avg_de == pytest.approx(0.0, abs=1e-4)
    assert res.peak_de == pytest.approx(0.0, abs=1e-4)
