"""Orchestrates chartread for interactive measurement."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

# Regex to detect which strip chartread is currently asking for.
# Handles formats:
#   "Ready to read strip pass A"   (Argyll 3.x default)
#   "Scanning strip 'A01'"
#   "Strip ID: B"
_STRIP_RE = re.compile(
    r"[Ss]trip\s+(?:pass\s+|ID:\s*'?|'?)([A-Za-z]{1,3}\d*)(?:')?(?![A-Za-z0-9])"
)

_ALL_DONE_RE       = re.compile(r"ALL\s+ROWS\s+READ",    re.IGNORECASE)
_CALIBRATION_RE    = re.compile(r"Calibration\s+complete", re.IGNORECASE)


@dataclass
class MeasureParams:
    ti1_path: Path
    instrument: str = "1"
    disable_bidir: bool = True
    suppress_warnings: bool = True
    disable_initial_cal: bool = False
    patch_by_patch: bool = False
    high_res: bool = False
    extra_args: str = ""


class MeasureManager(QObject):
    stripe_changed    = pyqtSignal(str)  # emits strip ID string e.g. "A01"
    all_stripes_done  = pyqtSignal()    # emitted when chartread reports all rows read
    calibration_done  = pyqtSignal()    # emitted when instrument calibration completes

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner

    # ------------------------------------------------------------------

    def start(
        self,
        params: MeasureParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        args = self._build_args(params)
        cwd  = params.ti1_path.parent
        log.info("chartread: %s  [cwd=%s]", " ".join(args), cwd)

        self._runner.run(
            "chartread",
            args,
            cwd,
            on_line=lambda line: self._handle_line(line, on_line),
            on_finish=on_finish,
            use_pty=True,
        )

    def send_key(self, key: str) -> None:
        """Send a keystroke to the running chartread process."""
        self._runner.write_stdin(key)

    def abort(self) -> None:
        self._runner.abort()

    # ------------------------------------------------------------------

    def _build_args(self, p: MeasureParams) -> list[str]:
        args: list[str] = ["-c", p.instrument]
        if p.disable_bidir:
            args.append("-B")
        if p.suppress_warnings:
            args.append("-S")
        if p.disable_initial_cal:
            args.append("-N")
        if p.patch_by_patch:
            args.append("-p")
        if p.high_res:
            args.append("-H")
        if p.extra_args:
            args += shlex.split(p.extra_args)
        # Base name without extension
        args.append(str(p.ti1_path.with_suffix("")))
        return args

    def _handle_line(self, line: str, on_line: Callable[[str], None]) -> None:
        on_line(line)
        matches = _STRIP_RE.findall(line)
        if matches:
            self.stripe_changed.emit(matches[-1])
        if _ALL_DONE_RE.search(line):
            self.all_stripes_done.emit()
        if _CALIBRATION_RE.search(line):
            self.calibration_done.emit()
