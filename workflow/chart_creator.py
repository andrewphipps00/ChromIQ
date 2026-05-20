"""Orchestrates targen + printtarg to create a test chart."""
from __future__ import annotations

import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from core.logger import get_logger
from data.patch_db import SUPPORTED_PATCH_SCALES, query_patches

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings

log = get_logger(__name__)


def _chromiq_clip_active(p: "ChartParams") -> bool:
    """True when ChromIQ-style clipping border applies to this chart.

    Gating: setting enabled AND instrument is i1Pro family AND paper is large
    enough for the rotated text to be legible. Mirrors the gating used by the
    plain left-clip-info stamp so the two features stay in sync.
    """
    from workflow.tiff_metadata import ALLOWED_LEFT_CLIP_PAPERS
    return (
        p.chromiq_clip_style
        and p.instrument in {"i1", "p3"}
        and p.paper in ALLOWED_LEFT_CLIP_PAPERS
    )


def _effective_suppress_lb(p: "ChartParams") -> bool:
    """The -L state used for patch-capacity lookups.

    ChromIQ-style forces -L, so the page is laid out at full (-L-enabled)
    capacity even when the user left "Suppress left clip border" unchecked.
    """
    return p.disable_left_border or _chromiq_clip_active(p)


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
    patches: int = 0            # 0 = auto-compute from DB / binary search (guided); pass -f0 to targen (manual)
    is_manual: bool = False     # True = manual mode; skip patch DB lookup, pass patches directly to targen
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
    chart_notes: str = ""             # free-text user notes stamped onto the TIFF
    stamp_commands: bool = True       # also stamp the targen + printtarg commands used
    # When True (and gating holds: instrument in {i1, p3}, -L off, paper in
    # ALLOWED_LEFT_CLIP_PAPERS), fill the left clip strip with two rotated
    # info columns: chart context + archival form fields + jig-orientation note.
    left_clip_info: bool = False
    # ChromIQ-style clipping border (Preferences → i1Pro). When True and gating
    # holds (instrument in {i1, p3} + paper in ALLOWED_LEFT_CLIP_PAPERS), the
    # workflow forces -L, shifts patches right by ~28 mm to create a fresh
    # left strip, and always stamps the ChromIQ left-clip content there. The
    # right-margin command/notes stamp is skipped because its target area
    # gets pushed off the page by the shift.
    chromiq_clip_style: bool = False
    cal_target: bool = False          # when True, preserve existing cal_* files during cleanup
    # When True, the .icc/.ti3 from a prior session in the same working folder
    # are renamed to pre_*.icc/pre_*.ti3 instead of being overwritten on the
    # following measure / colprof runs. Set by the guided tab when the user
    # arrived via "Use as pre-conditioning profile" in a result dialog.
    preserve_as_preconditioning: bool = False


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
        if params.cal_target:
            # Starting a calibration target run — full wipe, no exceptions
            self._file_mgr.clean_folder(["ti1", "ti2", "tif", "cht", "ps", "json", "cal"])
            for _f in work_dir.iterdir():
                if _f.is_file() and _f.stem.startswith("pre_") and _f.suffix.lower() in (".icc", ".icm"):
                    try:
                        _f.unlink()
                    except OSError as _exc:
                        log.warning("Could not delete %s: %s", _f, _exc)
        else:
            # Normal profiling run — preserve any cal_* files from a prior calibration step
            if params.preserve_as_preconditioning:
                params.extra_targen_args = self._promote_v1_to_preconditioning(
                    params.extra_targen_args, work_dir,
                )
            _exts = {"ti1", "ti2", "tif", "cht", "ps", "json", "cal"}
            _deleted = 0
            for _f in work_dir.iterdir():
                if _f.is_file() and _f.suffix.lstrip(".").lower() in _exts and not _f.stem.startswith("cal_"):
                    try:
                        _f.unlink()
                        _deleted += 1
                    except OSError as _exc:
                        log.warning("Could not delete %s: %s", _f, _exc)
            log.debug("Cleaned %d file(s), preserved cal_* files", _deleted)
            params.extra_targen_args = self._stage_precond_profile(params.extra_targen_args, work_dir)

        if params.is_manual:
            patch_count = params.patches  # pass value as-is; 0 lets targen decide
        else:
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
        if any(abs(params.patch_scale - s) <= 0.01 for s in SUPPORTED_PATCH_SCALES):
            per_sheet = query_patches(params.instrument, params.paper, params.double_density,
                                      suppress_lb=_effective_suppress_lb(params),
                                      margin_mm=params.margin_mm,
                                      patch_scale=params.patch_scale)
            if per_sheet is not None:
                n = per_sheet * params.pages
                if progress_cb:
                    progress_cb(
                        f"Lookup: {per_sheet} patches/sheet × {params.pages} = {n}"
                    )
                return n

        # Binary search for unsupported patch_scale / margin_mm
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
        # _printtarg_done writes the <stem>.channels.json sidecar only when
        # _pending_params is set; otherwise the preview can't identify inks
        # in a future session. Mirror the generate() path so this entry point
        # produces the same artifacts.
        self._pending_params = params
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

    @staticmethod
    def _promote_v1_to_preconditioning(extra_args: str, work_dir: Path) -> str:
        """Rename a prior session's .icc/.ti3 to pre_* so the next run can use them.

        Triggered when the user clicked "Use as pre-conditioning profile" in a
        result dialog. The picked profile is the v1 .icc inside the working
        folder; renaming it (and its matching .ti3) preserves the v1 record
        instead of letting the upcoming measure/colprof runs overwrite it.

        Any existing pre_*.icc / pre_*.ti3 from a previous refinement pass are
        overwritten — we only keep one generation of history.
        """
        if not extra_args:
            return extra_args
        parts = shlex.split(extra_args)
        try:
            idx = parts.index("-c")
        except ValueError:
            return extra_args
        if idx + 1 >= len(parts):
            return extra_args

        src = Path(parts[idx + 1])
        # Only promote when the picked profile is INSIDE the working folder and
        # not already a pre_* file. External paths are handled later by
        # _stage_precond_profile() which copies them in.
        try:
            in_work = src.resolve().parent == work_dir.resolve()
        except OSError:
            in_work = False
        if not in_work or src.stem.startswith("pre_"):
            return extra_args
        if not src.is_file():
            return extra_args

        icc_dest = work_dir / f"pre_{src.name}"
        try:
            if icc_dest.exists():
                icc_dest.unlink()
            src.rename(icc_dest)
            log.info("Promoted v1 profile to pre-conditioning: %s", icc_dest.name)
        except OSError as exc:
            log.warning("Could not rename %s -> %s: %s", src, icc_dest, exc)
            return extra_args

        ti3_src = src.with_suffix(".ti3")
        if ti3_src.is_file():
            ti3_dest = work_dir / f"pre_{ti3_src.name}"
            try:
                if ti3_dest.exists():
                    ti3_dest.unlink()
                ti3_src.rename(ti3_dest)
                log.info("Promoted v1 measurements to %s", ti3_dest.name)
            except OSError as exc:
                log.warning("Could not rename %s -> %s: %s", ti3_src, ti3_dest, exc)

        parts[idx + 1] = str(icc_dest)
        return shlex.join(parts)

    @staticmethod
    def _stage_precond_profile(extra_args: str, work_dir: Path) -> str:
        """Copy the -c ICC/ICM file into work_dir with a pre_ prefix if needed."""
        if not extra_args:
            return extra_args
        parts = shlex.split(extra_args)
        try:
            idx = parts.index("-c")
        except ValueError:
            return extra_args
        if idx + 1 >= len(parts):
            return extra_args
        src = Path(parts[idx + 1])
        if not src.is_file():
            return extra_args
        dest_name = src.name if src.stem.startswith("pre_") else f"pre_{src.name}"
        dest = work_dir / dest_name
        if src != dest:
            import shutil
            shutil.copy2(src, dest)
            log.info("Staged pre-conditioning profile to %s", dest)
            parts[idx + 1] = str(dest)
            return shlex.join(parts)
        return extra_args

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
        # Set-comprehension dedupes Windows' case-insensitive glob matches
        # (chart.tif matches both *.tif and *.TIF), which otherwise made the
        # preview display "Page 1/2" for a single-file chart (forum #148124).
        tiffs = sorted({
            *work_dir.glob(f"{stem}*.tif"),
            *work_dir.glob(f"{stem}*.TIF"),
            *work_dir.glob(f"{stem}*.tiff"),
        })
        log.info("printtarg produced %d TIFF(s) in %s", len(tiffs), work_dir)
        if not tiffs:
            log.warning("No TIFFs found; searched %s for chart*.tif", work_dir)

        if tiffs and self._pending_params is not None:
            self._write_channel_sidecar(work_dir, stem, self._pending_params)
            self._stamp_tiff_metadata(tiffs, self._pending_params)

        if on_finish:
            on_finish(tiffs)

    def _stamp_tiff_metadata(self, tiffs: list[Path], params: "ChartParams") -> None:
        """Stamp the actual targen/printtarg commands (and optional notes) into each TIFF."""
        try:
            from core.version import APP_VERSION
            from workflow.tiff_metadata import (
                ALLOWED_LEFT_CLIP_PAPERS,
                shift_patches_for_chromiq_clip,
                stamp_chart_metadata,
                stamp_left_clip_info,
            )
        except Exception as exc:
            log.warning("Could not import metadata stamper: %s", exc)
            return

        patch_count = self._count_patches_in_ti1(
            tiffs[0].parent / f"{params.target_name}.ti1"
        ) or params.patches or 0

        chromiq_clip = _chromiq_clip_active(params)

        # Build the command/notes lines once. They go to the right margin in
        # normal mode, or into a clip-border column under ChromIQ-style.
        cmd_lines: list[str] = []
        if params.chart_notes:
            cmd_lines.append(params.chart_notes)
        if params.stamp_commands:
            cmd_lines.append("targen " + shlex.join(
                self._build_targen_args(params, patch_count)
            ))
            cmd_lines.append("printtarg " + shlex.join(
                self._build_printtarg_args(params)
            ))
            cmd_lines.append(f"ChromIQ {APP_VERSION}")

        if not chromiq_clip:
            # Normal mode: commands/notes go to the right margin.
            if cmd_lines:
                stamp_chart_metadata(tiffs, cmd_lines)
        else:
            # ChromIQ-style: shift the patch block right by ~28 mm so the left
            # side becomes a fresh white strip ready for the left-clip stamp.
            shift_patches_for_chromiq_clip(tiffs)

        # Left clip info stamp. Active when:
        #   • ChromIQ-style clipping border is on (always stamp the branded
        #     content into the freshly-shifted white strip), OR
        #   • the user opted in via the per-chart "Print info in left clip
        #     area" toggle AND -L is off (the native i1Pro clip strip exists).
        instrument_ok = params.instrument in {"i1", "p3"}
        paper_ok = params.paper in ALLOWED_LEFT_CLIP_PAPERS
        plain_left_clip = (
            params.left_clip_info
            and instrument_ok
            and not params.disable_left_border
            and paper_ok
        )
        if chromiq_clip or plain_left_clip:
            from data.patch_db import PAPER_LABELS
            paper_label = PAPER_LABELS.get(params.paper, params.paper)
            patches_label = f"{patch_count}-patch" if patch_count else "RGB"
            header_lines = [
                f"ArgyllCMS {patches_label} RGB target on {paper_label}",
                "PRINT: borderless, 100% size (no scaling), color management OFF",
            ]
            # Each field gets its own ": ___" with the underscore length
            # sized to the paper inside stamp_left_clip_info().
            form_fields = [
                "date",
                "printer",
                "ink set",
                "profile name",
                "paper type",
                "driver/resolution",
            ]
            # Under ChromIQ-style the commands/notes become a clip-border
            # column instead of the (now off-page) right margin.
            command_lines = cmd_lines if chromiq_clip else None
            stamp_left_clip_info(
                tiffs, header_lines, form_fields, inner_lines=[],
                command_lines=command_lines,
            )

    @staticmethod
    def _count_patches_in_ti1(ti1_path: Path) -> int | None:
        """Best-effort patch-count parse from a targen .ti1 file (NUMBER_OF_SETS)."""
        if not ti1_path.is_file():
            return None
        try:
            for line in ti1_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("NUMBER_OF_SETS"):
                    return int(line.split()[-1])
        except Exception as exc:
            log.debug("Could not parse patch count from %s: %s", ti1_path, exc)
        return None

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
        # -h is only meaningful for CM (double density) and SS (hexagon
        # patches). -L only affects strip instruments (i1, p3). Filter
        # so leftover UI state can't append no-op flags to the recorded
        # command in stamped TIFF metadata.
        if p.double_density and p.instrument in {"CM", "SS"}:
            args.append("-h")
        # ChromIQ-style clipping border forces -L regardless of the per-chart
        # toggle so printtarg fills the page with patches; we manufacture the
        # clip strip post-process in _stamp_tiff_metadata.
        force_l = _chromiq_clip_active(p)
        if (p.disable_left_border or force_l) and p.instrument in {"i1", "p3"}:
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
        eff_lb = _effective_suppress_lb(p)
        if any(abs(p.patch_scale - s) <= 0.01 for s in SUPPORTED_PATCH_SCALES):
            per_sheet = query_patches(p.instrument, p.paper, p.double_density,
                                      suppress_lb=eff_lb,
                                      margin_mm=p.margin_mm,
                                      patch_scale=p.patch_scale)
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

        eff_lb = _effective_suppress_lb(p)
        if not targen_bin.exists():
            log.warning("targen not found for binary search, returning estimate")
            return (query_patches(p.instrument, p.paper, p.double_density,
                                  suppress_lb=eff_lb,
                                  margin_mm=p.margin_mm,
                                  patch_scale=p.patch_scale)
                    or query_patches(p.instrument, p.paper, p.double_density,
                                     suppress_lb=eff_lb)
                    or 500)

        pt_args_base = self._build_printtarg_args(p)[:-1]  # strip trailing target name

        est_raw = (query_patches(p.instrument, p.paper, p.double_density,
                                 suppress_lb=eff_lb,
                                 margin_mm=p.margin_mm,
                                 patch_scale=p.patch_scale)
                   or query_patches(p.instrument, p.paper, p.double_density,
                                    suppress_lb=eff_lb)
                   or 400)
        # Per-sheet capacity scales roughly as 1/patch_scale² — each patch
        # occupies patch_scale² of the standard cell area. Without this
        # adjustment the [0.5×, 2.5×] window misses the real value for
        # patch_scale > ~1.4, the loop never sees pages==1, and `best`
        # stays at its initial sentinel.
        scale_sq = max(p.patch_scale ** 2, 0.01)
        est = max(20, int(est_raw / scale_sq))

        lo = max(20, int(est * 0.5))
        hi = max(lo + 50, int(est * 2.5))
        lo_init, hi_init = lo, hi
        best = 0

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

        if best == 0:
            log.warning("_binary_search found no valid capacity in [%d, %d]; "
                        "falling back to scaled estimate %d", lo_init, hi_init, est)
            return est
        return best

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
