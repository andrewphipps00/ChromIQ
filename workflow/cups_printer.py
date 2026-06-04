"""Send a TIFF print target to a CUPS printer via PostScript."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Callable

from core.logger import get_logger
from workflow.postscript_generator import PostScriptGenerator
from workflow.ppd_color import vendor_no_cm_setting_for_queue

log = get_logger(__name__)

try:
    import cups as _cups_mod
    CUPS_AVAILABLE = True
except ImportError:
    _cups_mod = None  # type: ignore[assignment]
    CUPS_AVAILABLE = False
    if sys.platform != "win32":
        log.warning("pycups not available — CUPS printing disabled")

# Options injected into PS print jobs.
# Colour space is declared in the PS document itself; only neutral job-ticket
# options go here.  ColorSync=None is PS-specific — do NOT use for PDF.
# Duplex/sides are also baked into the PS via setpagedevice — these are the
# CUPS-level belt-and-suspenders for PPDs that ignore the PS directive or
# strip it during filtering.
_PS_JOB_OPTIONS: dict[str, str] = {
    "ColorSync": "None",   # belt-and-suspenders alongside %cupsJobTicket
    "Duplex":    "None",
    "sides":     "one-sided",
}

# TIFF fallback options per channel count.  These mirror the original
# _COLOR_MGMT_OFF dict but are now colour-space-aware.  They tell CUPS
# exactly how to format the raster data for the printer driver — bypassing
# ColorSync — without hardcoding DeviceRGB for every job.
_TIFF_RASTER_OPTIONS: dict[int, dict[str, str]] = {
    1: {
        "ColorSync":        "None",
        "cupsColorSpace":   "0",          # CUPS Gray
        "cupsColorOrder":   "0",
        "ColorModel":       "Gray",
        "cupsCompression":  "None",
        "cupsBitsPerColor": "8",
        "Duplex":           "None",
        "sides":            "one-sided",
    },
    3: {  # RGB — original _COLOR_MGMT_OFF, confirmed working for ET-8550
        "ColorSync":        "None",
        "cupsColorSpace":   "DeviceRGB",
        "cupsColorOrder":   "0",
        "ColorModel":       "RGB",
        "cupsCompression":  "None",
        "cupsBitsPerColor": "8",
        "Duplex":           "None",
        "sides":            "one-sided",
    },
    4: {  # CMYK
        "ColorSync":        "None",
        "cupsColorSpace":   "6",          # CUPS CMYK
        "cupsColorOrder":   "0",
        "ColorModel":       "CMYK",
        "cupsCompression":  "None",
        "cupsBitsPerColor": "8",
        "Duplex":           "None",
        "sides":            "one-sided",
    },
}

# ---------------------------------------------------------------------------
# Kept for reference only — superseded by print_job_ps().
# The TIFF path hardcodes DeviceRGB which breaks CMYK and N-channel targets.
# ---------------------------------------------------------------------------
_COLOR_MGMT_OFF: dict[str, str] = {
    "ColorSync":        "None",
    "cupsColorSpace":   "DeviceRGB",
    "cupsColorOrder":   "0",
    "ColorModel":       "RGB",
    "cupsCompression":  "None",
    "cupsBitsPerColor": "8",
    "Duplex":           "None",
}


@dataclass
class PrintConfig:
    printer_name: str
    options: dict[str, str] = _field(default_factory=dict)


class CupsRawPrinter:
    """Submit a profiling target to a CUPS printer via PostScript."""

    def print_job_ps(
        self,
        tiff_path: Path,
        config: PrintConfig,
        ink_channels: list[str] | None = None,
        on_finish: Callable[[int], None] | None = None,
        orientation: int | None = None,
        page_size_pt: tuple[float, float] | None = None,
    ) -> None:
        """Convert *tiff_path* to PostScript and send to the printer in *config*.

        ink_channels: ordered ink codes for the TIFF channels — required for
        correct DeviceN naming when the target has more than 4 colorants.
        orientation: CUPS orientation-requested (3=portrait, 4=landscape).
        Only used by the TIFF fallback below; the PS path bakes orientation
        into setpagedevice instead, since pstops double-rotates if it sees
        both a landscape PS and orientation-requested=4.
        page_size_pt: physical media size (w_pt, h_pt) — passed to the PS
        setpagedevice line so the PS doc and `lp -o PageSize=...` agree.
        Falls back to TIFF automatically if CUPS rejects PostScript.
        """
        try:
            ps_text = PostScriptGenerator().generate(
                tiff_path,
                ink_channels=ink_channels,
                page_size_pt=page_size_pt,
            )
        except Exception as exc:
            log.error("PS generation failed for %s: %s", tiff_path.name, exc)
            if on_finish:
                on_finish(-1)
            return

        fd, ps_str = tempfile.mkstemp(suffix=".ps")
        ps_path = Path(ps_str)
        code, stderr = -1, ""
        try:
            os.close(fd)
            ps_path.write_text(ps_text, encoding="ascii")
            cmd = self._build_lp_command_ps(ps_path, config, orientation)
            log.info("CUPS PS print: %s", " ".join(str(c) for c in cmd))
            code, stderr = self._run_lp_result(cmd)
        finally:
            ps_path.unlink(missing_ok=True)

        if code == 1 and "postscript" in stderr.lower():
            log.warning("PostScript rejected by CUPS — retrying as TIFF")
            self._cancel_pending_jobs(config.printer_name)
            self._print_job_tiff(tiff_path, config, on_finish, orientation)
            return

        if on_finish:
            on_finish(code)

    def _print_job_tiff(
        self,
        tiff_path: Path,
        config: PrintConfig,
        on_finish: Callable[[int], None] | None = None,
        orientation: int | None = None,
    ) -> None:
        """Submit the TIFF directly with colour-space-aware CUPS raster options.

        This mirrors the original _COLOR_MGMT_OFF approach but picks the correct
        cupsColorSpace / ColorModel for 1-, 3-, and 4-channel TIFFs so CUPS does
        not apply ColorSync or ICC transforms before handing off to the driver.
        N-channel (> 4) falls back to RGB options — those targets are typically
        only used on PostScript-capable RIPs that accept the PS path above.
        """
        n_ch = self._tiff_n_channels(tiff_path)
        cmd = self._build_lp_command_tiff(tiff_path, config, n_ch, orientation)
        log.info("CUPS TIFF print (fallback, %d-ch): %s", n_ch, " ".join(str(c) for c in cmd))
        self._run_lp(cmd, on_finish)

    # Superseded by print_job_ps() — kept temporarily for reference.
    def print_job(
        self,
        tiff_path: Path,
        config: PrintConfig,
        on_finish: Callable[[int], None] | None = None,
    ) -> None:
        """Send *tiff_path* to the printer named in *config* via lp."""
        cmd = self._build_lp_command(tiff_path, config)
        log.info("CUPS print: %s", " ".join(str(c) for c in cmd))
        self._run_lp(cmd, on_finish)

    def _run_lp_result(self, cmd: list[str]) -> tuple[int, str]:
        """Run lp, return (returncode, stderr_text)."""
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
            stderr = result.stderr.decode("utf-8", errors="replace")
            if result.returncode != 0:
                log.error("lp failed (code %d): %s", result.returncode, stderr)
            else:
                log.info("lp submitted (stdout: %s)",
                         result.stdout.decode("utf-8", errors="replace").strip())
            return result.returncode, stderr
        except subprocess.TimeoutExpired:
            log.error("lp timed out")
            return -1, ""
        except Exception as exc:
            log.error("lp exception: %s", exc)
            return -1, ""

    def _run_lp(
        self,
        cmd: list[str],
        on_finish: Callable[[int], None] | None,
    ) -> None:
        code, _ = self._run_lp_result(cmd)
        if on_finish:
            on_finish(code)

    @staticmethod
    def _cancel_pending_jobs(printer_name: str) -> None:
        if not CUPS_AVAILABLE:
            return
        try:
            conn = _cups_mod.Connection()
            jobs = conn.getJobs(which_jobs="not-completed", my_jobs=False)
            for job_id, attrs in jobs.items():
                if printer_name not in attrs.get("job-printer-uri", ""):
                    continue
                try:
                    conn.cancelJob(job_id)
                    log.info("Cancelled queued job %d for %s before TIFF retry", job_id, printer_name)
                except Exception:
                    pass
        except Exception as exc:
            log.warning("_cancel_pending_jobs error: %s", exc)

    @staticmethod
    def _apply_vendor_no_cm(opts: dict[str, str], printer_name: str) -> None:
        """Force the driver's own "no colour adjustment" option into *opts*.

        ``ColorSync=None`` only stops CUPS' *own* ColorSync transform; a driver
        whose colour engine lives below that (e.g. Canon, whose PPD exposes
        ``CNIJIntent2=1001`` / "No Color Correction") keeps re-profiling the
        chart unless its key is set explicitly.  Mirrors the backstop the native
        macOS print path applies.  Best-effort: silently does nothing if the PPD
        isn't found or exposes no such option (e.g. Epson, already handled by the
        raster options above).
        """
        try:
            vendor = vendor_no_cm_setting_for_queue(printer_name)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("vendor no-CM lookup failed for %s: %s", printer_name, exc)
            return
        if vendor:
            opts[vendor[0]] = vendor[1]
            log.info("CUPS print: driver no-colour option %s=%s", *vendor)

    @staticmethod
    def _build_lp_command_ps(
        ps_path: Path,
        cfg: PrintConfig,
        orientation: int | None = None,
    ) -> list[str]:
        # `orientation` is intentionally ignored on the PS path: the page
        # geometry is baked into setpagedevice PageSize by PostScriptGenerator.
        # Apple's pstops filter rotates a second time if it sees both a
        # landscape PS and orientation-requested=4 — clipping the chart or
        # silently dropping the job. The param stays in the signature for
        # symmetry with _build_lp_command_tiff, which still needs it.
        del orientation
        merged = {**cfg.options, **_PS_JOB_OPTIONS}
        CupsRawPrinter._apply_vendor_no_cm(merged, cfg.printer_name)
        cmd = ["lp", "-d", cfg.printer_name]
        for key, val in merged.items():
            if val:
                cmd += ["-o", f"{key}={val}"]
        cmd.append(str(ps_path))
        return cmd

    @staticmethod
    def _tiff_n_channels(tiff_path: Path) -> int:
        import tifffile
        with tifffile.TiffFile(str(tiff_path)) as tif:
            shape = tif.pages[0].shape
        return shape[2] if len(shape) == 3 else 1

    @staticmethod
    def _build_lp_command_tiff(
        tiff_path: Path,
        cfg: PrintConfig,
        n_ch: int,
        orientation: int | None = None,
    ) -> list[str]:
        opts = _TIFF_RASTER_OPTIONS.get(n_ch, _TIFF_RASTER_OPTIONS[3])
        merged = {**cfg.options, **opts}
        if orientation is not None:
            merged["orientation-requested"] = str(orientation)
        CupsRawPrinter._apply_vendor_no_cm(merged, cfg.printer_name)
        cmd = ["lp", "-d", cfg.printer_name]
        for key, val in merged.items():
            if val:
                cmd += ["-o", f"{key}={val}"]
        cmd.append(str(tiff_path))
        return cmd

    @staticmethod
    def _build_lp_command(tiff_path: Path, cfg: PrintConfig) -> list[str]:
        merged = {**cfg.options, **_COLOR_MGMT_OFF}
        cmd = ["lp", "-d", cfg.printer_name]
        for key, val in merged.items():
            if val:
                cmd += ["-o", f"{key}={val}"]
        cmd.append(str(tiff_path))
        return cmd

    @staticmethod
    def is_printer_reachable(printer_name: str) -> bool:
        """Return True if the printer is idle or printing (state 3 or 4)."""
        if not CUPS_AVAILABLE:
            return True  # fail open on platforms without CUPS
        try:
            attrs = _cups_mod.Connection().getPrinters().get(printer_name, {})
            return attrs.get("printer-state", 5) in (3, 4)
        except Exception:
            return True  # fail open — let lp surface the real error
