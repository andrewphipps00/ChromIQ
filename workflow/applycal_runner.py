"""Orchestrates applycal to apply/remove/check calibration curves on an ICC profile."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)


class ApplycalRunner:
    def __init__(self, runner: "ArgyllRunner", settings: "AppSettings") -> None:
        self._runner = runner
        self._settings = settings

    def run(
        self,
        cal_path: Path,
        in_icc: Path,
        out_icc: Path,
        mode: str,                            # "apply" | "remove" | "check"
        verbose: bool,
        on_line: Callable[[str], None],
        on_finish: Callable[[Path | None], None],
    ) -> None:
        """Run applycal; call on_finish(out_icc) on success, on_finish(None) on failure."""
        args = self._build_args(cal_path, in_icc, out_icc, mode, verbose)
        work_dir = in_icc.parent
        log.info("applycal args: %s", args)

        self._runner.run(
            "applycal",
            args,
            work_dir,
            on_line=on_line,
            on_finish=lambda code: self._done(code, out_icc, mode, on_finish),
        )

    def _done(
        self,
        code: int,
        out_icc: Path,
        mode: str,
        on_finish: Callable[[Path | None], None],
    ) -> None:
        if mode == "check":
            # Check mode doesn't write a file; success = exit 0
            on_finish(out_icc if code == 0 else None)
        else:
            success = (code == 0) and out_icc.exists()
            if success:
                log.info("applycal succeeded → %s", out_icc)
                on_finish(out_icc)
            else:
                log.error("applycal failed (code %d)", code)
                on_finish(None)

    @staticmethod
    def _build_args(
        cal_path: Path,
        in_icc: Path,
        out_icc: Path,
        mode: str,
        verbose: bool,
    ) -> list[str]:
        args: list[str] = []
        if verbose:
            args.append("-v")
        if mode == "remove":
            args.append("-u")
        elif mode == "check":
            args.append("-c")
        # "apply" is the default (-a), no flag needed
        args.append(str(cal_path))
        args.append(str(in_icc))
        if mode != "check":
            args.append(str(out_icc))
        return args
