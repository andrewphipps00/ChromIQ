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


def test_auto_count_uses_engine_capacity(tmp_path: Path) -> None:
    """Guided (_lookup_patches) and Manual-auto (estimate_patches) request the
    engine's own capacity × pages so the chart fills the page."""
    from workflow.layout_engine import geometry, instruments, papers
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    p = ChartParams(instrument="i1", paper="A4", pages=1)
    kw = creator._engine_build_kwargs(p)
    geom = instruments.geom_from_build_kwargs(kw)
    cap = geometry.patches_per_sheet(geom, *papers.dimensions_mm(p.paper))
    assert cap > 0
    assert creator._engine_total_patches(p) == cap
    assert creator._lookup_patches(p) == cap          # guided generation count
    assert creator.estimate_patches(p) == cap         # manual-auto count
    # pages multiply the requested count
    p2 = ChartParams(instrument="i1", paper="A4", pages=3)
    assert creator._lookup_patches(p2) == cap * 3


def test_engine_build_kwargs_mapping(tmp_path: Path) -> None:
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    # Guided/Manual ColorMunki triple density → engine extra-high density (3)
    kw = creator._engine_build_kwargs(
        ChartParams(instrument="CM", paper="A3", triple_density=True,
                    no_spacers=True, patch_scale=0.9, tiff_dpi=600))
    assert kw["instrument"] == "CM" and kw["paper"] == "A3"
    assert kw["density"] == 3            # triple density → extra-high
    assert kw["spacer_on"] is False
    assert kw["pscale"] == 0.9 and kw["dpi"] == 600
    assert ChartParams(instrument="CM", double_density=True) and \
        creator._engine_build_kwargs(ChartParams(instrument="CM", double_density=True))["density"] == 2


def test_engine_kwargs_uses_full_recipe(tmp_path: Path) -> None:
    from workflow.layout_engine.presets import LayoutRecipe
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    recipe = LayoutRecipe(instrument="i1", paper="A4", margin_top=10,
                          patch_w_mm=9.0, offset_x_mm=4.0, spacer_mode="bw")
    params = ChartParams(instrument="i1", paper="Letter",
                         layout_recipe=recipe, engine_cal_path="/tmp/c.cal",
                         engine_apply_cal=True)
    kw = creator._engine_kwargs(params)
    # recipe drives the layout; instrument/paper come from ChartParams
    assert kw["margins"][0] == 10 and kw["patch_w"] == 9.0 and kw["offset_x"] == 4.0
    assert kw["spacer_mode"] == "bw"
    assert kw["paper"] == "Letter"          # ChartParams wins for paper
    assert kw["cal_path"] == "/tmp/c.cal" and kw["apply_cal"] is True


class _ThresholdSettings(_EngineSettings):
    """Engine on, plus a margin-threshold table (the real settings API)."""

    def get_margin_thresholds(self):
        from core.settings import margin_combo_key
        return {margin_combo_key("i1Pro", "A4", "Portrait"): {"T": 60, "R": 9}}


def test_engine_kwargs_enforces_margin_thresholds(tmp_path: Path) -> None:
    """_engine_kwargs raises the margins to meet the user's thresholds, and the
    same clamp drives the capacity estimate (so count and chart agree, #93)."""
    from workflow.layout_engine import geometry, instruments, papers
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _ThresholdSettings())
    p = ChartParams(instrument="i1", paper="A4", pages=1)
    kw = creator._engine_kwargs(p)
    geom = instruments.geom_from_build_kwargs(kw)
    w, h = papers.dimensions_mm("A4")
    cap = geometry.patches_per_sheet(geom, w, h)
    lay = geometry.compute(geom, w, h, cap)
    L, R, T, B = geometry.realized_margins_mm(geom, w, h, lay)
    assert T >= 60 - 0.05 and R >= 9 - 0.05
    assert creator._threshold_notes, "an adjustment note must be recorded"
    # capacity estimate uses the same clamped kwargs → drops vs no thresholds
    plain = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "q"),
                         _EngineSettings())._engine_total_patches(p)
    assert creator._engine_total_patches(p) <= plain


def test_full_recipe_chart_builds(tmp_path: Path) -> None:
    work_dir = tmp_path / "rp"
    creator = ChartCreator(_EngineRunner(), _MockFileManager(work_dir), _EngineSettings())
    from workflow.layout_engine.presets import LayoutRecipe
    finished: list[list[Path]] = []
    creator.generate(
        ChartParams(instrument="i1", paper="A4", device_type="2", tiff_dpi=120,
                    layout_recipe=LayoutRecipe(instrument="i1", paper="A4",
                                               margin_top=12, patch_h_mm=11.0,
                                               bit16=True, compression="zlib")),
        on_line=lambda _l: None, on_finish=lambda t: finished.append(t))
    assert finished and finished[0] and finished[0][0].exists()
    sidecar = json.loads(
        (work_dir / "runs" / "run1" / "rp.channels.json").read_text())
    assert sidecar["layout"]["engine"] == "chromiq"
    assert sidecar["layout"]["recipe"]["margin_top"] == 12


def test_guided_clip_border_uses_notes_when_kept(tmp_path: Path) -> None:
    """Guided/basic path: keeping the i1/p3 clip border fills it with the notes
    record; suppressing it leaves no clip content (#93)."""
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    kept = creator._engine_build_kwargs(
        ChartParams(instrument="i1", paper="A4", disable_left_border=False))
    assert kept["clip_content_mode"] == "notes" and kept["nolpcbord"] is False
    supp = creator._engine_build_kwargs(
        ChartParams(instrument="i1", paper="A4", disable_left_border=True))
    assert supp["clip_content_mode"] == "off" and supp["nolpcbord"] is True
    # non-clip instruments don't set it
    assert "clip_content_mode" not in creator._engine_build_kwargs(
        ChartParams(instrument="CM", paper="A4"))


def test_guided_uses_edge_spacers_for_strip_readers(tmp_path: Path) -> None:
    """Guided/basic path brackets each strip with edge spacers for i1Pro /
    i1Pro 3+ / ColorMunki, but not SpectroScan (#93)."""
    creator = ChartCreator(_EngineRunner(), _MockFileManager(tmp_path / "p"),
                           _EngineSettings())
    for inst in ("i1", "p3", "CM"):
        kw = creator._engine_build_kwargs(ChartParams(instrument=inst, paper="A4"))
        assert kw.get("edge_spacers") is True, inst
    assert creator._engine_build_kwargs(
        ChartParams(instrument="SS", paper="A4")).get("edge_spacers") is not True
