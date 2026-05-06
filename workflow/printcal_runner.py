"""Orchestrates printcal to create a printer calibration file (.cal)."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)


@dataclass
class PrintcalParams:
    ti3_path: Path                   # input measurement file (base name also used for output .cal)
    mode: str = "initial"            # "initial" | "recalibrate" | "verify"
    prev_cal: str = ""               # previous .cal path (used in recalibrate/verify mode)
    verbosity: int = 1
    smoothing: float = 1.0
    resolution: int = 256
    manufacturer: str = ""
    model: str = ""
    description: str = ""
    copyright: str = ""
    extra_args: str = ""


class PrintcalRunner:
    def __init__(self, runner: "ArgyllRunner", settings: "AppSettings") -> None:
        self._runner = runner
        self._settings = settings
        self._on_finish_cb: Callable[[Path | None], None] | None = None
        self._out_cal: Path | None = None

    def run(
        self,
        params: PrintcalParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[Path | None], None],
    ) -> None:
        """Run printcal; call on_finish(cal_path) on success, on_finish(None) on failure."""
        self._on_finish_cb = on_finish

        stem = params.ti3_path.stem  # base name without extension
        work_dir = params.ti3_path.parent
        self._out_cal = work_dir / f"{stem}.cal"

        args = self._build_args(params, stem)
        log.info("printcal args: %s", args)

        self._runner.run(
            "printcal",
            args,
            work_dir,
            on_line=on_line,
            on_finish=lambda code: self._done(code, on_finish),
        )

    def _done(self, code: int, on_finish: Callable[[Path | None], None]) -> None:
        cal = self._out_cal
        success = (code == 0) and cal is not None and cal.exists()
        if success:
            log.info("printcal succeeded → %s", cal)
            on_finish(cal)
        else:
            log.error("printcal failed (code %d)", code)
            on_finish(None)

    def _build_args(self, p: PrintcalParams, stem: str) -> list[str]:
        args: list[str] = []

        args.append(f"-v{p.verbosity}")
        args.append(f"-s{p.smoothing:.1f}")

        if p.resolution != 256:
            args.append(f"-z{p.resolution}")

        if p.mode == "initial":
            args.append("-i")
        elif p.mode == "recalibrate":
            args.append("-r")
        elif p.mode == "verify":
            args.append("-e")

        if p.manufacturer:
            args += ["-A", p.manufacturer]
        if p.model:
            args += ["-M", p.model]
        if p.description:
            args += ["-D", p.description]
        if p.copyright:
            args += ["-C", p.copyright]

        if p.extra_args:
            args += shlex.split(p.extra_args)

        # previous .cal must come before the inoutfile when recalibrating/verifying
        if p.mode in ("recalibrate", "verify") and p.prev_cal:
            args.append(p.prev_cal)

        args.append(stem)
        return args
