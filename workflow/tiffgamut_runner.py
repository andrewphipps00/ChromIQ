"""Runs Argyll ``tiffgamut`` to build the gamut surface of an *image*.

Given an image (TIFF/JPEG) and the profile that describes its colour space
(usually sRGB, or the image's embedded profile), ``tiffgamut`` produces the
gamut hull of the colours actually present in the image — a ``.gam`` file plus
an X3DOM ``.x3d.html`` we can show in the same viewer the ICC gamut viewer uses.

In the soft-proof tool this hull is overlaid on the *printer's* gamut (built by
:class:`workflow.gamut_viewer.GamutViewer`) so the user sees which image colours
sit outside what the printer can reproduce.

Mirrors :mod:`workflow.gamut_viewer` (temp dir, async ``ArgyllRunner.run``,
HTML patching for theme + background). To keep runs fast and bounded — so the
3D view can never hang the UI — the caller should downsample large images and
pass a popularity ``filter_perc``; ``tiffgamut`` itself is launched async via
``ArgyllRunner`` (QProcess), so it never blocks the event loop.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.i18n import tr
from core.logger import get_logger
from workflow.gamut_viewer import _patch_html  # reuse HTML theming/background

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

_VOLUME_RE = re.compile(r"Total volume of gamut is ([\d.]+)")


@dataclass
class TiffgamutParams:
    image_path: Path
    profile_path: Path        # source-space profile (sRGB / AdobeRGB / embedded)
    intent: str = "r"         # -i  r=relative (a profile's default works too)
    sres: float = 10.0        # -d  surface resolution (coarse = fast)
    filter_perc: float = 0.0  # -f  popularity filter %, 0 = off
    # -p j → build the gamut in CIECAM02 Jab *appearance* space (not Lab) so it
    # lines up with collink's perceptual gamut mapping. Argyll's documented
    # image-dependent device-link workflow uses this (`tiffgamut -pj -cmt …`);
    # the soft-proof overlay leaves it Lab, so it stays off by default.
    appearance: bool = False
    viewcond: str = ""        # -c  CIECAM02 viewing conditions (match collink -c)
    # Build one shared gamut from a *set* of images (e.g. an exhibition series).
    # When given, overrides image_path; tiffgamut takes them all as trailing args.
    image_paths: list[Path] | None = None


class TiffgamutRunner(QObject):
    """Wraps tiffgamut to produce an image-gamut .gam + X3DOM HTML."""

    finished = pyqtSignal(float, str, str)   # (volume_cc, html_path, gam_path)
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._log_lines: list[str] = []

    def run(
        self,
        params: TiffgamutParams,
        on_line:   Callable[[str], None] | None = None,
        on_finish: Callable[[int], None] | None = None,
        themed:    bool = True,
        bg:        str  = "#111111",
    ) -> None:
        if self._runner.is_running:
            self.error.emit(tr("Another process is already running."))
            return
        images = list(params.image_paths) if params.image_paths else [params.image_path]
        missing = next((p for p in images if not p.exists()), None)
        if missing is not None:
            self.error.emit(tr("Image file not found: {p}").format(p=missing))
            return
        if not params.profile_path.exists():
            self.error.emit(tr("Source profile not found: {p}").format(p=params.profile_path))
            return

        self._log_lines = []
        self._themed = themed
        self._bg = bg

        work_dir = Path(tempfile.mkdtemp(prefix="chromiq_imggamut_"))
        self._work_dir = work_dir
        base = work_dir / "imagegamut"

        args = ["-v", "-w", "-d", f"{params.sres:.1f}"]
        if params.filter_perc and params.filter_perc > 0:
            args += ["-f", f"{params.filter_perc:.0f}"]
        if params.intent:
            args += ["-i", params.intent]
        if params.appearance:
            args += ["-p", "j"]
        if params.viewcond:
            args += ["-c", params.viewcond]
        args += ["-O", str(base), str(params.profile_path)]
        args += [str(p) for p in images]
        log.info("tiffgamut: %s  [cwd=%s]", " ".join(args), work_dir)

        def _accumulate(line: str) -> None:
            self._log_lines.append(line)
            if on_line:
                on_line(line)

        def _done(code: int) -> None:
            self._on_done(code, base, on_finish)

        self._runner.run("tiffgamut", args, work_dir, on_line=_accumulate, on_finish=_done)

    def _on_done(self, code: int, base: Path, on_finish: Callable[[int], None] | None) -> None:
        # tiffgamut -O <base> (no extension) writes the gamut to <base> itself,
        # not <base>.gam (unlike iccgamut). Probe the likely names so a future
        # Argyll tweak to the naming doesn't break us.
        gam = next(
            (c for c in (base, base.with_suffix(".gam"), Path(str(base) + ".gam"))
             if c.exists()),
            None,
        )
        html = Path(str(base) + ".x3d.html")
        full_log = "\n".join(self._log_lines)
        m = _VOLUME_RE.search(full_log)
        volume = float(m.group(1)) if m else 0.0

        if gam is not None:
            html_path = str(html) if html.exists() else ""
            if html_path:
                _patch_html(html, self._themed, self._bg)
            log.info("tiffgamut: volume=%.1f cc, html=%s, gam=%s", volume, html_path, gam)
            self.finished.emit(volume, html_path, str(gam))
        else:
            tool_err = next(
                (l for l in reversed(self._log_lines) if "Error" in l or "error" in l), "")
            suffix = f"\ntiffgamut reported: {tool_err}" if tool_err else ""
            self.error.emit(tr("tiffgamut could not build the image gamut.{s}").format(s=suffix))

        if on_finish:
            on_finish(code)
