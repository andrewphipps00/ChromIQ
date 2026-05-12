"""Runs viewgam to combine two gamut files and compute coverage statistics."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

_INTERSECT_RE   = re.compile(r"Intersecting volume\s*=\s*([\d.]+)")


def _patch_html(html_path: Path) -> None:
    """Inject dark background and expand X3D canvas to fill the full viewport."""
    try:
        text = html_path.read_text(encoding="utf-8")
        style = (
            "<style>\n"
            "  html, body { background: #111111; margin: 0; padding: 0;"
            " overflow: hidden; }\n"
            "</style>\n"
        )
        text = text.replace("</head>", style + "</head>", 1)
        text = text.replace("height: 70%;", "height: 100vh;", 1)
        text = text.replace("height='70%'", "height='100vh'", 1)
        html_path.write_text(text, encoding="utf-8")
    except OSError:
        pass
_COVERAGE_RE    = re.compile(r"'[^']+' volume = ([\d.]+) cubic units, intersect = ([\d.]+)%")


@dataclass
class ViewgamResult:
    html_path: str = ""
    intersection_volume: float | None = None
    primary_coverage_pct: float | None = None   # % of primary gamut covered by compare
    compare_coverage_pct: float | None = None   # % of compare gamut covered by primary


class ViewgamRunner(QObject):
    """Wraps viewgam to produce combined X3DOM visualization + coverage statistics."""

    finished = pyqtSignal(object)   # ViewgamResult
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner    = runner
        self._log_lines: list[str] = []

    def run(
        self,
        primary_gam:  Path,
        compare_gam:  Path,
        on_line:      Callable[[str], None],
        on_finish:    Callable[[int], None],
    ) -> None:
        if self._runner.is_running:
            self.error.emit("Another process is already running.")
            return

        self._log_lines = []
        work_dir    = Path(tempfile.mkdtemp(prefix="chromiq_viewgam_"))
        output_base = str(work_dir / "combined")

        args = [
            "-i",
            "-c", "r", str(primary_gam),
            "-c", "w", str(compare_gam),
            output_base,
        ]
        log.info("viewgam: %s  [cwd=%s]", " ".join(args), work_dir)

        def _accumulate(line: str) -> None:
            self._log_lines.append(line)
            on_line(line)

        def _done(code: int) -> None:
            self._on_done(code, work_dir, on_finish)

        self._runner.run("viewgam", args, work_dir, on_line=_accumulate, on_finish=_done)

    def _on_done(
        self,
        code: int,
        work_dir: Path,
        on_finish: Callable[[int], None],
    ) -> None:
        full_log = "\n".join(self._log_lines)
        result   = ViewgamResult()

        html = work_dir / "combined.x3d.html"
        if html.exists():
            result.html_path = str(html)

        m_int = _INTERSECT_RE.search(full_log)
        if m_int:
            result.intersection_volume = float(m_int.group(1))

        coverages = _COVERAGE_RE.findall(full_log)
        if len(coverages) >= 2:
            result.primary_coverage_pct = float(coverages[0][1])
            result.compare_coverage_pct = float(coverages[1][1])

        if result.html_path:
            _patch_html(work_dir / "combined.x3d.html")

        if result.html_path or result.intersection_volume is not None:
            log.info(
                "viewgam: intersect=%.0f cc, A→B=%.1f%%, B→A=%.1f%%",
                result.intersection_volume or 0,
                result.primary_coverage_pct or 0,
                result.compare_coverage_pct or 0,
            )
            self.finished.emit(result)
        elif code != 0:
            self.error.emit(f"viewgam exited with code {code}.")
        else:
            self.error.emit("viewgam produced no usable output.")

        on_finish(code)
