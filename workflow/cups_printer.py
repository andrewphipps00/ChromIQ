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
_PS_JOB_OPTIONS: dict[str, str] = {
    "ColorSync": "None",   # belt-and-suspenders alongside %cupsJobTicket
    "Duplex":    "None",
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
    },
    3: {  # RGB — original _COLOR_MGMT_OFF, confirmed working for ET-8550
        "ColorSync":        "None",
        "cupsColorSpace":   "DeviceRGB",
        "cupsColorOrder":   "0",
        "ColorModel":       "RGB",
        "cupsCompression":  "None",
        "cupsBitsPerColor": "8",
        "Duplex":           "None",
    },
    4: {  # CMYK
        "ColorSync":        "None",
        "cupsColorSpace":   "6",          # CUPS CMYK
        "cupsColorOrder":   "0",
        "ColorModel":       "CMYK",
        "cupsCompression":  "None",
        "cupsBitsPerColor": "8",
        "Duplex":           "None",
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
    ) -> None:
        """Convert *tiff_path* to PostScript and send to the printer in *config*.

        ink_channels: ordered ink codes for the TIFF channels — required for
        correct DeviceN naming when the target has more than 4 colorants.
        Falls back to PDF automatically if CUPS rejects PostScript.
        """
        try:
            ps_text = PostScriptGenerator().generate(tiff_path, ink_channels=ink_channels)
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
            cmd = self._build_lp_command_ps(ps_path, config)
            log.info("CUPS PS print: %s", " ".join(str(c) for c in cmd))
            code, stderr = self._run_lp_result(cmd)
        finally:
            ps_path.unlink(missing_ok=True)

        if code == 1 and "postscript" in stderr.lower():
            log.warning("PostScript rejected by CUPS — retrying as TIFF")
            self._cancel_pending_jobs(config.printer_name)
            self._print_job_tiff(tiff_path, config, on_finish)
            return

        if on_finish:
            on_finish(code)

    def _print_job_tiff(
        self,
        tiff_path: Path,
        config: PrintConfig,
        on_finish: Callable[[int], None] | None = None,
    ) -> None:
        """Submit the TIFF directly with colour-space-aware CUPS raster options.

        This mirrors the original _COLOR_MGMT_OFF approach but picks the correct
        cupsColorSpace / ColorModel for 1-, 3-, and 4-channel TIFFs so CUPS does
        not apply ColorSync or ICC transforms before handing off to the driver.
        N-channel (> 4) falls back to RGB options — those targets are typically
        only used on PostScript-capable RIPs that accept the PS path above.
        """
        n_ch = self._tiff_n_channels(tiff_path)
        cmd = self._build_lp_command_tiff(tiff_path, config, n_ch)
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
            result = subprocess.run(cmd, capture_output=True, timeout=30)
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
    def _build_lp_command_ps(ps_path: Path, cfg: PrintConfig) -> list[str]:
        merged = {**cfg.options, **_PS_JOB_OPTIONS}
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
    def _build_lp_command_tiff(tiff_path: Path, cfg: PrintConfig, n_ch: int) -> list[str]:
        opts = _TIFF_RASTER_OPTIONS.get(n_ch, _TIFF_RASTER_OPTIONS[3])
        merged = {**cfg.options, **opts}
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
