"""Repeated-read averaging (the "Read again & average" Measure-tab flow).

Covers:
  * FileManager's read-variant naming helpers (<base>_read{N}.ti3, _average,
    accumulation index, exclusion of pre_/cal_-prefixed files);
  * AverageRunner argument construction (mean vs. median);
  * an end-to-end `average` run when the binary is available, asserting the
    measured PCS is the true mean and device RGB is untouched.

See docs/dev_averaging.md for the design and the analysis of `average`.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.file_manager import FileManager
from core.resource_path import argyll_binary
from core.settings import DEFAULTS
from workflow.average_runner import AverageParams, AverageRunner


# ----------------------------------------------------------------------
# Master switch — the whole flow is opt-in, off by default
# ----------------------------------------------------------------------

def test_averaging_disabled_by_default() -> None:
    # The Measure-tab completion dialog / read-again flow is gated behind this
    # toggle; shipping it on would change the default experience for everyone.
    assert DEFAULTS["averaging_enabled"] is False


# ----------------------------------------------------------------------
# FileManager naming helpers
# ----------------------------------------------------------------------

def test_read_variant_and_average_paths(tmp_path: Path) -> None:
    base = tmp_path / "chart.ti3"
    assert FileManager.read_variant_path(base, 2).name == "chart_read2.ti3"
    assert FileManager.average_path(base).name == "chart_average.ti3"


def test_existing_variants_sorted_and_filtered(tmp_path: Path) -> None:
    for nm in (
        "chart_read1.ti3", "chart_read2.ti3", "chart_read10.ti3",
        "pre_chart_read3.ti3",   # refinement file — excluded
        "cal_chart_read4.ti3",   # calibration file — excluded
        "chart_average.ti3",     # not a read — excluded
        "other_read1.ti3",       # different base — excluded
    ):
        (tmp_path / nm).write_text("x")

    variants = FileManager.existing_read_variants(tmp_path, "chart")
    assert [p.name for p in variants] == [
        "chart_read1.ti3", "chart_read2.ti3", "chart_read10.ti3",  # numeric sort
    ]


def test_next_read_index(tmp_path: Path) -> None:
    assert FileManager.next_read_index(tmp_path, "chart") == 1
    (tmp_path / "chart_read1.ti3").write_text("x")
    (tmp_path / "chart_read10.ti3").write_text("x")
    assert FileManager.next_read_index(tmp_path, "chart") == 11


# ----------------------------------------------------------------------
# AverageRunner argument construction
# ----------------------------------------------------------------------

def test_build_args_mean() -> None:
    r = AverageRunner(runner=None)
    p = AverageParams(
        inputs=[Path("/w/a_read1.ti3"), Path("/w/a_read2.ti3")],
        output=Path("/w/a_average.ti3"),
        method="mean",
    )
    assert r._build_args(p) == ["-v", "a_read1.ti3", "a_read2.ti3", "a_average.ti3"]


def test_build_args_median_adds_e() -> None:
    r = AverageRunner(runner=None)
    p = AverageParams(
        inputs=[Path("/w/a_read1.ti3"), Path("/w/a_read2.ti3")],
        output=Path("/w/a_average.ti3"),
        method="median",
    )
    assert r._build_args(p)[:2] == ["-v", "-e"]


def test_run_rejects_single_input(tmp_path: Path) -> None:
    r = AverageRunner(runner=None)  # runner never reached
    captured: list = []
    r.run(
        AverageParams(inputs=[tmp_path / "only.ti3"], output=tmp_path / "out.ti3"),
        on_line=lambda _ln: None,
        on_finish=captured.append,
    )
    assert captured == [None]


# ----------------------------------------------------------------------
# End-to-end against the real binary (skipped if ArgyllCMS isn't installed)
# ----------------------------------------------------------------------

def _average_available() -> bool:
    return Path(argyll_binary("average")).exists() or shutil.which("average") is not None


_FMT = (
    "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z"
)


def _make_ti3(path: Path, xyz_x: float) -> None:
    path.write_text(
        'CTI3\n\nDEVICE_CLASS "OUTPUT"\nCOLOR_REP "iRGB_XYZ"\n'
        "NUMBER_OF_FIELDS 8\nBEGIN_DATA_FORMAT\n" + _FMT + "\nEND_DATA_FORMAT\n"
        "NUMBER_OF_SETS 1\nBEGIN_DATA\n"
        f'1 "A1" 50.0 50.0 50.0 {xyz_x:.6f} 20.0 30.0 \n'
        "END_DATA\n"
    )


@pytest.mark.skipif(not _average_available(), reason="ArgyllCMS `average` not installed")
def test_average_is_true_mean_and_leaves_rgb(tmp_path: Path) -> None:
    _make_ti3(tmp_path / "chart_read1.ti3", 40.0)
    _make_ti3(tmp_path / "chart_read2.ti3", 42.0)
    out = tmp_path / "chart_average.ti3"

    binary = Path(argyll_binary("average"))
    binary = str(binary) if binary.exists() else "average"
    res = subprocess.run(
        [binary, "-v", "chart_read1.ti3", "chart_read2.ti3", "chart_average.ti3"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert out.exists()

    row = next(
        l for l in out.read_text().splitlines()
        if l.strip().startswith("1 ")
    ).split()
    # XYZ_X is field index 5; mean(40, 42) == 41. RGB (idx 2-4) untouched.
    assert abs(float(row[5]) - 41.0) < 1e-4
    assert [float(row[2]), float(row[3]), float(row[4])] == [50.0, 50.0, 50.0]
