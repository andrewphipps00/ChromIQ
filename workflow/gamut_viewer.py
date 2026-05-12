"""Runs iccgamut to compute gamut volume and generate an X3DOM 3D visualization."""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

_VOLUME_RE = re.compile(r"Total volume of gamut is ([\d.]+)")


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


@dataclass
class GamutViewerParams:
    icc_path: Path
    intent: str = "a"       # -i  a=absolute, r=relative, p=perceptual, s=saturation
    pcs: str = "l"          # -p  l=Lab, j=CIECAM02 Jab
    sres: float = 4.0       # -d  surface resolution
    axes: bool = True       # omit -n when True
    cusps: bool = False     # -k
    edges: bool = False     # -e
    function: str = "f"     # -f  f=forward, b=backward


class GamutViewer(QObject):
    """Wraps iccgamut to produce gamut volume + X3DOM HTML in a temp directory."""

    finished = pyqtSignal(float, str, str)   # (volume_cc, html_path, gam_path)
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner    = runner
        self._log_lines: list[str] = []
        self._work_dir: Path | None = None

    def run(
        self,
        params: GamutViewerParams,
        on_line:   Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        if self._runner.is_running:
            self.error.emit("Another process is already running.")
            return

        self._log_lines = []
        self._params    = params

        # iccgamut writes output next to the input file — use a temp dir to
        # avoid polluting the profile folder and to get a known output path.
        work_dir = Path(tempfile.mkdtemp(prefix="chromiq_gamut_"))
        self._work_dir = work_dir
        icc_copy = work_dir / params.icc_path.name
        try:
            shutil.copy2(params.icc_path, icc_copy)
        except OSError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            self.error.emit(f"Cannot copy ICC file: {exc}")
            return

        args = self._build_args(params, icc_copy)
        log.info("iccgamut: %s  [cwd=%s]", " ".join(args), work_dir)

        def _accumulate(line: str) -> None:
            self._log_lines.append(line)
            on_line(line)

        def _done(code: int) -> None:
            self._on_done(code, icc_copy, on_finish)

        self._runner.run("iccgamut", args, work_dir, on_line=_accumulate, on_finish=_done)

    def _on_done(
        self,
        code: int,
        icc_copy: Path,
        on_finish: Callable[[int], None],
    ) -> None:
        full_log = "\n".join(self._log_lines)
        m = _VOLUME_RE.search(full_log)

        if m:
            volume = float(m.group(1))
            stem   = icc_copy.stem
            html   = icc_copy.parent / f"{stem}.x3d.html"
            gam    = icc_copy.parent / f"{stem}.gam"
            html_path = str(html) if html.exists() else ""
            gam_path  = str(gam)  if gam.exists()  else ""
            if not html_path:
                log.warning("iccgamut: HTML not found at %s", html)
            else:
                _patch_html(html)
            log.info("iccgamut: volume=%.1f cc, html=%s, gam=%s", volume, html_path, gam_path)
            self.finished.emit(volume, html_path, gam_path)
        elif code != 0:
            self.error.emit(f"iccgamut exited with code {code}.")
        else:
            self.error.emit("Could not parse gamut volume from iccgamut output — try running with -v flag.")


        on_finish(code)

    @staticmethod
    def _build_args(p: GamutViewerParams, icc_path: Path) -> list[str]:
        args: list[str] = ["-v", "-w"]  # verbose (prints volume) + X3DOM HTML
        if p.intent and p.intent != "a":
            args.append(f"-i{p.intent}")
        if p.pcs and p.pcs != "l":
            args.append(f"-p{p.pcs}")
        if p.sres != 4.0:
            args += ["-d", f"{p.sres:.1f}"]
        if not p.axes:
            args.append("-n")
        if p.cusps:
            args.append("-k")
        if p.edges:
            args.append("-e")
        if p.function and p.function != "f":
            args.append(f"-f{p.function}")
        args.append(str(icc_path.name))
        return args
