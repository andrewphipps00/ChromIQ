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
# chartread prints e.g. "Scanning strip 'A01'"  or  "Strip ID: A"
_STRIP_RE = re.compile(r"[Ss]trip\s+(?:ID:\s*)?['\"]?([A-Za-z0-9]+)['\"]?")


@dataclass
class MeasureParams:
    ti1_path: Path
    disable_bidir: bool = True
    suppress_warnings: bool = True
    disable_initial_cal: bool = False
    patch_by_patch: bool = False
    high_res: bool = False
    extra_args: str = ""


class MeasureManager(QObject):
    stripe_changed = pyqtSignal(str)   # emits strip ID string e.g. "A01"

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
        )

    def send_key(self, key: str) -> None:
        """Send a keystroke to the running chartread process."""
        self._runner.write_stdin(key)

    def abort(self) -> None:
        self._runner.abort()

    # ------------------------------------------------------------------

    def _build_args(self, p: MeasureParams) -> list[str]:
        args: list[str] = []
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
        m = _STRIP_RE.search(line)
        if m:
            self.stripe_changed.emit(m.group(1))
