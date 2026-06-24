"""Integration test: ChartCreator routes through the ChromIQ layout engine
when ``use_chromiq_layout_engine`` is on (covers both Guided and Manual, since
they share generate())."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from workflow.chart_creator import ChartCreator, ChartParams


def _real_ti1(path: Path, n: int = 60) -> None:
    rows = []
    vals = [0.0, 33.0, 66.0, 100.0]
    i = 0
    rows.append((100.0, 100.0, 100.0))  # white (media)
    for r in vals:
        for g in vals:
            for b in vals:
                if len(rows) >= n:
                    break
                rows.append((r, g, b))
    lines = ['CTI1', 'COLOR_REP "iRGB"',
             'NUMBER_OF_FIELDS 7', 'BEGIN_DATA_FORMAT',
             'SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z', 'END_DATA_FORMAT',
             f'NUMBER_OF_SETS {len(rows)}', 'BEGIN_DATA']
    for i, (r, g, b) in enumerate(rows, 1):
        lines.append(f'{i} {r:.4f} {g:.4f} {b:.4f} '
                     f'{r*0.95:.4f} {g:.4f} {b*1.08:.4f}')
    lines += ['END_DATA', '']
    path.write_text("\n".join(lines))


class _EngineRunner:
    """targen writes a real .ti1; the engine (not printtarg) finishes the chart."""

    def run(self, tool, args, cwd, on_line=None, on_finish=None):
        cwd = Path(cwd)
        stem = args[-1]
        if tool == "targen":
            _real_ti1(cwd / f"{stem}.ti1")
        if on_finish:
            on_finish(0)


class _MockFileManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._project = None

    def ensure_folder(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def clean_folder(self, exts):
        pass

    def project(self):
        from core.file_manager import Project
        if self._project is None:
            self._project = Project.create_or_load(self.root, self.root.name)
        return self._project

    def cwd_for_chart(self, *, cal_target: bool) -> Path:
        return self.project().current_run().ensure_dir()

    def chart_stem(self, *, cal_target: bool) -> str:
        return self.project().current_run().stem


class _EngineSettings:
    def get(self, key, default=None):
        if key == "use_chromiq_layout_engine":
            return True
        return default


def test_generate_uses_engine(tmp_path: Path) -> None:
    work_dir = tmp_path / "engine_proj"
    creator = ChartCreator(_EngineRunner(), _MockFileManager(work_dir), _EngineSettings())
    finished: list[list[Path]] = []
    creator.generate(
        ChartParams(instrument="i1", paper="A4", device_type="2", tiff_dpi=150),
        on_line=lambda _l: None,
        on_finish=lambda tiffs: finished.append(tiffs),
    )
    assert finished, "on_finish must fire"
    tiffs = finished[0]
    assert tiffs and all(p.exists() for p in tiffs), "engine must produce TIFF(s)"

    run_dir = work_dir / "runs" / "run1"
    stem = "engine_proj"
    assert (run_dir / f"{stem}.ti2").exists(), "engine must write the .ti2"
    sidecar = json.loads((run_dir / f"{stem}.channels.json").read_text())
    assert "layout" in sidecar, "channels.json must carry the layout geometry"
    layout = sidecar["layout"]
    assert layout["strips"] and layout["patches"], "strip + patch rects present"
    assert "seed" in layout and isinstance(layout["recipe"], dict)
    assert layout["recipe"]["instrument"] == "i1"
    # the standalone .strips.json is folded into channels.json
    assert not (run_dir / f"{stem}.strips.json").exists()


def test_engine_build_kwargs_mapping(tmp_path: Path) -> None:
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    kw = creator._engine_build_kwargs(
        ChartParams(instrument="CM", paper="A3", triple_density=True,
                    no_spacers=True, patch_scale=0.9, tiff_dpi=600))
    assert kw["instrument"] == "CM" and kw["paper"] == "A3"
    assert kw["density"] == 3            # triple density
    assert kw["spacer_on"] is False
    assert kw["pscale"] == 0.9 and kw["dpi"] == 600
