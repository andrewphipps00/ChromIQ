"""Regression tests for the ChartCreator sidecar pipeline.

Issue #15 surfaced a gap: `_printtarg_done` writes the `<stem>.channels.json`
sidecar only when `self._pending_params` is non-None. The `generate()` entry
point sets it, but `load_ti1_and_generate_preview()` did not — so loading a
chart from an existing .ti1 produced no sidecar, leaving the preview unable
to identify inks in a future session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data.patch_db import INSTRUMENT_DEFAULT_MARGIN, query_patches
from workflow.chart_creator import ChartCreator, ChartParams


class _MockRunner:
    """Synchronously fire on_finish(0) and stage the files printtarg would create."""

    def run(self, tool, args, cwd, on_line=None, on_finish=None):
        cwd = Path(cwd)
        stem = args[-1]
        if tool == "targen":
            (cwd / f"{stem}.ti1").write_text("FAKE TI1")
        elif tool == "printtarg":
            (cwd / f"{stem}.ti2").write_text("FAKE TI2")
            arr = np.zeros((100, 100, 3), dtype=np.uint8)
            tifffile.imwrite(
                str(cwd / f"{stem}_01.tif"),
                arr,
                resolution=(200, 200),
                resolutionunit="INCH",
            )
        if on_finish:
            on_finish(0)


class _MockFileManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_folder(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def clean_folder(self, exts: list[str]) -> None:
        for f in self.root.iterdir():
            if f.is_file() and f.suffix.lstrip(".").lower() in exts:
                f.unlink()


class _MockSettings:
    def get(self, key, default=None):
        return default


def _make_creator(tmp_path: Path) -> tuple[ChartCreator, Path]:
    work_dir = tmp_path / "chart_proj"
    return ChartCreator(_MockRunner(), _MockFileManager(work_dir), _MockSettings()), work_dir


def test_generate_writes_channels_sidecar(tmp_path: Path) -> None:
    creator, work_dir = _make_creator(tmp_path)
    finished: list[list[Path]] = []
    creator.generate(
        ChartParams(target_name="mychart", device_type="2"),
        on_line=lambda _: None,
        on_finish=lambda tiffs: finished.append(tiffs),
    )
    sidecar = work_dir / "mychart.channels.json"
    assert sidecar.exists(), "generate() must write the channels sidecar"
    assert json.loads(sidecar.read_text())["ink_channels"] == ["r", "g", "b"]


def test_query_patches_margin10_i1_a4_with_left_border() -> None:
    """margin=10 i1/A4 must return the measured table value (not the m=6 baseline)."""
    n_m6 = query_patches("i1", "A4", suppress_lb=True, margin_mm=6)
    n_m10 = query_patches("i1", "A4", suppress_lb=True, margin_mm=10)
    assert n_m6 is not None and n_m10 is not None
    assert n_m10 < n_m6, "margin=10 must fit fewer patches than margin=6"
    assert n_m10 == 483, "regression guard against accidental table edits"


def test_query_patches_margin10_respects_left_border_flag() -> None:
    """The -L vs no-L distinction must propagate through margin=10 lookups."""
    with_l = query_patches("i1", "A4", suppress_lb=True, margin_mm=10)
    without_l = query_patches("i1", "A4", suppress_lb=False, margin_mm=10)
    assert with_l is not None and without_l is not None
    assert with_l > without_l, "-L must yield more patches than no-L"


def test_query_patches_unsupported_margin_returns_none() -> None:
    """Margin values outside {6, 10} must return None so callers fall back to binary search."""
    assert query_patches("i1", "A4", margin_mm=8) is None
    assert query_patches("i1", "A4", margin_mm=15) is None


def test_query_patches_margin10_only_for_i1_and_p3() -> None:
    """CM and SS don't change defaults, so their margin=10 lookups should be missing."""
    assert query_patches("CM", "A4", margin_mm=10) is None
    assert query_patches("SS", "A4", margin_mm=10) is None


def test_instrument_default_margin_keys() -> None:
    """i1/p3 default to 10mm; CM/SS keep 6mm."""
    assert INSTRUMENT_DEFAULT_MARGIN["i1"] == 10
    assert INSTRUMENT_DEFAULT_MARGIN["p3"] == 10
    assert INSTRUMENT_DEFAULT_MARGIN["CM"] == 6
    assert INSTRUMENT_DEFAULT_MARGIN["SS"] == 6


def test_printtarg_done_dedupes_case_insensitive_glob(tmp_path: Path) -> None:
    """Single chart.tif must not appear twice in the preview list on Windows.

    Regression guard for forum bug #148124's "Page 1/2 from one file" symptom:
    pathlib.Path.glob is case-insensitive on Windows, so the prior code's
    sorted([*glob('*.tif'), *glob('*.TIF'), *glob('*.tiff')]) returned the
    same file two or three times.
    """
    creator, work_dir = _make_creator(tmp_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Pretend printtarg produced a single TIFF for a one-page chart
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    tifffile.imwrite(
        str(work_dir / "single.tif"), arr,
        resolution=(200, 200), resolutionunit="INCH",
    )

    captured: list[list[Path]] = []
    creator._pending_params = ChartParams(target_name="single", device_type="2")
    creator._printtarg_done(0, work_dir, lambda t: captured.append(t), "single")

    assert captured, "on_finish was never called"
    assert len(captured[0]) == 1, f"expected 1 TIFF, got {len(captured[0])}: {captured[0]}"


def test_load_ti1_writes_channels_sidecar(tmp_path: Path) -> None:
    creator, work_dir = _make_creator(tmp_path)
    work_dir.mkdir(parents=True)
    src_ti1 = work_dir / "imported.ti1"
    src_ti1.write_text("FAKE TI1")

    finished: list[list[Path]] = []
    creator.load_ti1_and_generate_preview(
        src_ti1,
        ChartParams(target_name="imported", device_type="2"),
        on_line=lambda _: None,
        on_finish=lambda tiffs: finished.append(tiffs),
    )
    sidecar = work_dir / "imported.channels.json"
    assert sidecar.exists(), (
        "load_ti1_and_generate_preview() must set _pending_params so the "
        "sidecar is written — regression guard for the second half of #15"
    )
    assert json.loads(sidecar.read_text())["ink_channels"] == ["r", "g", "b"]
