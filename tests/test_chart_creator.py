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
