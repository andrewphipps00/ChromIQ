"""Orchestrates targen + printtarg to create a test chart."""
from __future__ import annotations

import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from core.logger import get_logger
from data.patch_db import query_patches

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings

log = get_logger(__name__)


@dataclass
class ChartParams:
    # Guided-mode selections
    instrument: str = "i1"
    paper: str = "A4"
    pages: int = 1
    double_density: bool = False
    disable_left_border: bool = True

    # targen params
    device_type: str = "2"
    patches: int = 0            # 0 = auto-compute from DB / binary search
    white_patches: int = 4
    black_patches: int = 4
    good_mode: bool = True
    grey_steps: int = 0
    single_channel_steps: int = 0
    extra_targen_args: str = ""

    # printtarg params
    tiff_dpi: int = 300
    tiff_16bit: bool = False
    patch_scale: float = 1.0
    margin_mm: int = 6
    no_randomise: bool = False
    bw_spacers: bool = False
    no_strip_limit: bool = False
    extra_printtarg_args: str = ""

    target_name: str = "chart"


class ChartCreator:
    def __init__(
        self,
        runner: "ArgyllRunner",
        file_mgr: "FileManager",
        settings: "AppSettings",
    ) -> None:
        self._runner = runner
        self._file_mgr = file_mgr
        self._settings = settings

        self._pending_on_finish: Callable[[list[Path]], None] | None = None
        self._pending_params: ChartParams | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        params: ChartParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[list[Path]], None],
    ) -> None:
        """Run targen then printtarg; call on_finish(tiff_paths) on completion."""
        self._pending_on_finish = on_finish
        self._pending_params = params

        work_dir = self._file_mgr.ensure_folder()
        self._file_mgr.clean_folder(["ti1", "ti2", "tif", "cht", "ps"])

        patch_count = params.patches if params.patches > 0 else self._lookup_patches(params)
        log.info("Chart generation: %d patches, instrument=%s paper=%s disable_lb=%s",
                 patch_count, params.instrument, params.paper, params.disable_left_border)

        targen_args = self._build_targen_args(params, patch_count)
        log.info("targen args: %s", targen_args)

        self._runner.run(
            "targen",
            targen_args,
            work_dir,
            on_line=on_line,
            on_finish=lambda code: self._targen_done(code, params, on_line, work_dir),
        )

    def estimate_patches(
        self,
        params: ChartParams,
        progress_cb: Callable[[str], None] | None = None,
    ) -> int:
        """Return max patches for the given params (fast lookup or binary search)."""
        if abs(params.patch_scale - 1.0) <= 0.01 and params.margin_mm == 6:
            per_sheet = query_patches(params.instrument, params.paper, params.double_density,
                                      suppress_lb=params.disable_left_border)
            if per_sheet is not None:
                n = per_sheet * params.pages
                if progress_cb:
                    progress_cb(
                        f"Lookup: {per_sheet} patches/sheet × {params.pages} = {n}"
                    )
                return n

        # Binary search for non-default patch_scale or margin_mm
        if progress_cb:
            progress_cb("Custom layout detected — running binary search…")
        per_sheet = self._binary_search(params, progress_cb)
        return per_sheet * params.pages

    def load_ti1_and_generate_preview(
        self,
        ti1_path: Path,
        params: ChartParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[list[Path]], None],
    ) -> None:
        """Run printtarg only on an existing .ti1 file."""
        import shutil
        work_dir = self._file_mgr.ensure_folder()
        stem = params.target_name or "chart"
        dest = work_dir / f"{stem}.ti1"
        if ti1_path != dest:
            shutil.copy(ti1_path, dest)

        self._file_mgr.clean_folder(["ti2", "tif", "cht", "ps"])
        pt_args = self._build_printtarg_args(params)
        log.debug("printtarg args (from ti1): %s", pt_args)
        self._runner.run(
            "printtarg",
            pt_args,
            work_dir,
            on_line=on_line,
            on_finish=lambda code: self._printtarg_done(code, work_dir, on_finish, stem),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _targen_done(
        self,
        exit_code: int,
        params: ChartParams,
        on_line: Callable[[str], None],
        work_dir: Path,
    ) -> None:
        if exit_code != 0:
            log.error("targen failed with code %d", exit_code)
            on_line(f"[ERROR] targen exited with code {exit_code}")
            if self._pending_on_finish:
                self._pending_on_finish([])
            return

        pt_args = self._build_printtarg_args(params)
        log.info("printtarg args: %s", pt_args)
        self._runner.run(
            "printtarg",
            pt_args,
            work_dir,
            on_line=on_line,
            on_finish=lambda code: self._printtarg_done(
                code, work_dir, self._pending_on_finish, params.target_name
            ),
        )

    def _printtarg_done(
        self,
        exit_code: int,
        work_dir: Path,
        on_finish: Callable[[list[Path]], None] | None,
        target_name: str = "chart",
    ) -> None:
        if exit_code != 0:
            log.error("printtarg failed with code %d", exit_code)
            if on_finish:
                on_finish([])
            return

        stem = target_name or "chart"
        tiffs = sorted([
            *work_dir.glob(f"{stem}*.tif"),
            *work_dir.glob(f"{stem}*.TIF"),
            *work_dir.glob(f"{stem}*.tiff"),
        ])
        log.info("printtarg produced %d TIFF(s) in %s", len(tiffs), work_dir)
        if not tiffs:
            log.warning("No TIFFs found; searched %s for chart*.tif", work_dir)

        if tiffs and self._pending_params is not None:
            self._write_channel_sidecar(work_dir, stem, self._pending_params)

        if on_finish:
            on_finish(tiffs)

    def _write_channel_sidecar(
        self, work_dir: Path, stem: str, params: "ChartParams"
    ) -> None:
        """Write <stem>.channels.json so the preview can identify inks in future sessions."""
        import json
        from ui.tiff_preview import resolve_ink_channels
        channels = resolve_ink_channels(params.device_type, params.extra_targen_args)
        sidecar = work_dir / f"{stem}.channels.json"
        try:
            sidecar.write_text(json.dumps({"ink_channels": channels}))
            log.debug("Wrote channel sidecar %s: %s", sidecar.name, channels)
        except Exception as exc:
            log.warning("Could not write channel sidecar: %s", exc)

    # ------------------------------------------------------------------
    # Arg builders
    # ------------------------------------------------------------------

    def _build_targen_args(self, p: ChartParams, patches: int) -> list[str]:
        args: list[str] = [f"-d{p.device_type}"]
        args += [f"-f{patches}"]
        args += [f"-e{p.white_patches}"]
        args += [f"-B{p.black_patches}"]
        if p.good_mode:
            args.append("-G")
        if p.grey_steps > 0:
            args += [f"-g{p.grey_steps}"]
        if p.single_channel_steps > 0:
            args += [f"-s{p.single_channel_steps}"]
        if p.extra_targen_args:
            args += shlex.split(p.extra_targen_args)
        args.append(p.target_name or "chart")
        return args

    def _build_printtarg_args(self, p: ChartParams) -> list[str]:
        args: list[str] = []
        # printtarg uses "3p" for i1Pro 3 Plus; help text lists "p3" but that's a typo
        pt_instr = "3p" if p.instrument == "p3" else p.instrument
        args.append(f"-i{pt_instr}")
        args.append(f"-p{p.paper}")
        dpi_flag = "-T" if p.tiff_16bit else "-t"
        args.append(f"{dpi_flag}{p.tiff_dpi}")
        if p.double_density:
            args.append("-h")
        if p.disable_left_border:
            args.append("-L")
        if abs(p.patch_scale - 1.0) > 0.01:
            args += [f"-a{p.patch_scale:.2f}"]
        if p.margin_mm != 6:
            args += [f"-m{p.margin_mm}"]
        args.append(f"-M{p.margin_mm}")
        if p.no_randomise:
            args.append("-r")
        if p.bw_spacers:
            args.append("-b")
        if p.no_strip_limit:
            args.append("-P")
        if p.extra_printtarg_args:
            args += shlex.split(p.extra_printtarg_args)
        args.append(p.target_name or "chart")
        return args

    # ------------------------------------------------------------------
    # Patch count helpers
    # ------------------------------------------------------------------

    def _lookup_patches(self, p: ChartParams) -> int:
        if abs(p.patch_scale - 1.0) <= 0.01 and p.margin_mm == 6:
            per_sheet = query_patches(p.instrument, p.paper, p.double_density,
                                      suppress_lb=p.disable_left_border)
            if per_sheet is not None:
                return per_sheet * p.pages
        return self._binary_search(p) * p.pages

    def _binary_search(
        self,
        p: ChartParams,
        progress_cb: Callable[[str], None] | None = None,
    ) -> int:
        bin_dir = Path(self._settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        targen_bin    = bin_dir / "targen"
        printtarg_bin = bin_dir / "printtarg"

        if not targen_bin.exists():
            log.warning("targen not found for binary search, returning estimate")
            return query_patches(p.instrument, p.paper, p.double_density,
                                 suppress_lb=p.disable_left_border) or 500

        pt_args_base = self._build_printtarg_args(p)[:-1]  # strip trailing target name

        est = query_patches(p.instrument, p.paper, p.double_density,
                            suppress_lb=p.disable_left_border) or 400
        lo, hi = max(50, int(est * 0.5)), int(est * 2.5)
        best = 50

        with tempfile.TemporaryDirectory() as tmp_str:
            tmpdir = Path(tmp_str)
            total_steps = max(1, (hi - lo).bit_length())
            step = 0

            while lo <= hi:
                mid = (lo + hi) // 2
                step += 1
                if progress_cb:
                    progress_cb(f"Step {step}/{total_steps} — probing {mid} patches…")

                pages = self._probe(targen_bin, printtarg_bin, mid, p.device_type,
                                    pt_args_base + ["calc"], tmpdir)
                self._cleanup_probe(tmpdir)

                if pages == 0:
                    hi = mid - 1
                elif pages == 1:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

        return max(best, 50)

    @staticmethod
    def _probe(
        targen_bin: Path,
        printtarg_bin: Path,
        patches: int,
        device_type: str,
        pt_args: list[str],
        tmpdir: Path,
    ) -> int:
        tg_args = [f"-d{device_type}", f"-f{patches}", "calc"]
        r = subprocess.run(
            [str(targen_bin)] + tg_args,
            capture_output=True, timeout=60, cwd=str(tmpdir),
        )
        if r.returncode != 0 or not (tmpdir / "calc.ti1").exists():
            return 0
        pt = subprocess.run(
            [str(printtarg_bin)] + pt_args,
            capture_output=True, timeout=60, cwd=str(tmpdir),
        )
        if pt.returncode != 0:
            return 0
        return len(list(tmpdir.glob("calc*.tif")))

    @staticmethod
    def _cleanup_probe(tmpdir: Path) -> None:
        for f in tmpdir.glob("calc*"):
            try:
                f.unlink()
            except OSError:
                pass
