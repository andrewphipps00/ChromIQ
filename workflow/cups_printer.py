"""Send a TIFF print target to a CUPS printer."""
from __future__ import annotations

from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Callable
import subprocess

from core.logger import get_logger

log = get_logger(__name__)

# Injected into every print job to disable all color management in CUPS.
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
    """Submit a TIFF file as a CUPS print job with user-selected driver options."""

    def print_job(
        self,
        tiff_path: Path,
        config: PrintConfig,
        on_finish: Callable[[int], None] | None = None,
    ) -> None:
        """Send *tiff_path* to the printer named in *config* via lp."""
        cmd = self._build_lp_command(tiff_path, config)
        log.info("CUPS print: %s", " ".join(str(c) for c in cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            code = result.returncode
            if code != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                log.error("lp failed (code %d): %s", code, stderr)
            else:
                log.info("lp submitted successfully (job id in stdout: %s)",
                         result.stdout.decode("utf-8", errors="replace").strip())
        except subprocess.TimeoutExpired:
            log.error("lp timed out")
            code = -1
        except Exception as exc:
            log.error("lp exception: %s", exc)
            code = -1

        if on_finish:
            on_finish(code)

    @staticmethod
    def _build_lp_command(tiff_path: Path, cfg: PrintConfig) -> list[str]:
        # Color-management options always override user selections.
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
        try:
            import cups
            attrs = cups.Connection().getPrinters().get(printer_name, {})
            return attrs.get("printer-state", 5) in (3, 4)
        except Exception:
            return True  # fail open — let lp surface the real error
