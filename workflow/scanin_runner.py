"""Reads a printed chart out of a **flatbed scan** with ArgyllCMS ``scanin``.

Given a scan of a ChromIQ chart plus the chart's ``.cht`` (patch layout) and
``.cie`` (measured reference), ``scanin`` samples each patch's scanner RGB and
writes a ``scan.ti3`` pairing scanner RGB ↔ the measured XYZ. That ``.ti3`` has
``DEVICE_CLASS "INPUT"``, so ``colprof`` then builds a **scanner input profile**
from it (reuse :mod:`workflow.profile_builder`) — the scanner roundtrip (#98).

Two registration paths:

* **Auto** — ``scanin scan.tif chart.cht chart.cie``; scanin finds the chart by
  its edge ticks + corners.
* **Manual (marquee)** — ``scanin -F x1,y1,x2,y2,x3,y3,x4,y4 -p …``; the four
  corners the user placed over the chart (``.cht`` order **TL, TR, BR, BL**),
  ``-p`` compensating for perspective. The robust path, since the engine prints
  no fiducial *marks* (the ``.cht`` ``F`` line gives ``-F`` its reference quad).

``-d`` (e.g. ``-dipn``) additionally writes a **diagnostic image** with the
recognised patch boxes drawn on it, so a mis-read can be seen before profiling.

Mirrors the other Argyll runners (:mod:`workflow.cctiff_apply`): a params
dataclass + a runner that builds the CLI and drives it through the singleton
:class:`~core.argyll_runner.ArgyllRunner`, with structured error parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

from core.logger import get_logger

log = get_logger(__name__)

# Corner order scanin -F expects, matching the .cht F line and the marquee.
CORNER_ORDER = ("top-left", "top-right", "bottom-right", "bottom-left")


def _fmt_corners(corners: list[tuple[float, float]]) -> str:
    """``x1,y1,x2,y2,x3,y3,x4,y4`` from four (x, y) image-pixel corners."""
    if len(corners) != 4:
        raise ValueError("scanin -F needs exactly four corners (TL, TR, BR, BL).")
    return ",".join(f"{v:g}" for xy in corners for v in xy)


def scanin_args(scan_tif: Path, cht: Path, cie: Path,
                corners: list[tuple[float, float]] | None = None,
                perspective: bool = True, diag: Path | None = None,
                robust_mean: bool = True, verbose: bool = True,
                out_name: str | None = None) -> list[str]:
    """Build the ``scanin`` argument list for scanner-profile mode.

    *corners* (four image-pixel (x, y), order TL/TR/BR/BL) switches on manual
    ``-F`` registration; ``None`` uses auto-recognition. *diag* writes a
    diagnostic image (extra ``-dipn`` + the diag path as the trailing arg).
    *out_name* (via ``-O``) sets the output ``.ti3`` filename — used to give the
    scanner ``.ti3`` a distinct ``-scanner`` name so it can never overwrite the
    chart's own measurement / printer profile. Default is scanin's ``<scan>.ti3``."""
    args: list[str] = []
    if verbose:
        args.append("-v")
    if not robust_mean:
        args.append("-m")
    if corners is not None:
        args += ["-F", _fmt_corners(corners)]
    if perspective:
        args.append("-p")
    if diag is not None:
        args.append("-dipn")
    if out_name is not None:
        args += ["-O", out_name]
    args += [str(scan_tif), str(cht), str(cie)]
    if diag is not None:
        args.append(str(diag))
    return args


# scanin failure messages → (key, friendly text). Line refs: scanin/scanin.c.
_SCANIN_ERROR_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"Scanin failed with code", re.IGNORECASE),
     "recognition_failed",
     "ScanIn couldn't line the chart up with the scan. Re-place the four "
     "corners over the printed patch area (or straighten/re-scan the sheet), "
     "then try again."),
    (re.compile(r"must be 8 or 16 bits", re.IGNORECASE),
     "bit_depth",
     "The scan must be an 8- or 16-bit-per-channel TIFF. Re-export it from your "
     "scanner software as a plain TIFF."),
    (re.compile(r"must be an? (?:Grey|RGB|CMYK)", re.IGNORECASE),
     "wrong_channels",
     "The scan must be a Grey, RGB or CMYK TIFF. Scan the chart as RGB."),
    (re.compile(r"must be planar", re.IGNORECASE),
     "planar",
     "The scan's pixel layout isn't supported. Re-save it as an uncompressed "
     "RGB TIFF from your scanner software."),
    (re.compile(r"might overwrite the input", re.IGNORECASE),
     "diag_clash",
     "The diagnostic image would overwrite the scan. This is an internal naming "
     "clash — please report it."),
    (re.compile(r"error opening read file '([^']+)'", re.IGNORECASE),
     "open_failed",
     "Couldn't open '{0}'. Check the scan file exists and is readable."),
]


@dataclass
class ScaninParams:
    """One scanned page → a scanner ``.ti3``. Paths are absolute."""

    scan_tif: Path
    cht: Path
    cie: Path
    corners: list[tuple[float, float]] | None = None   # None = auto-recognise
    perspective: bool = True
    diag: Path | None = None
    robust_mean: bool = True
    # Distinct output name (via -O) so the scanner .ti3 can never collide with the
    # chart's own <stem>.ti3 / printer profile. Defaults to "<scan>-scanner.ti3".
    out_name: str | None = None

    @property
    def _out_name(self) -> str:
        return self.out_name or f"{self.scan_tif.stem}-scanner.ti3"

    @property
    def out_ti3(self) -> Path:
        """The scanner ``.ti3`` scanin writes — ``<scan>-scanner.ti3`` next to
        the scan (never the chart's own ``<stem>.ti3``)."""
        return self.scan_tif.parent / self._out_name


class ScaninRunner:
    def __init__(self, runner: "ArgyllRunner") -> None:
        self._runner = runner
        self._last_log = ""
        self._matched_errors: list[tuple[str, str]] = []

    def run(self, params: ScaninParams, on_line: Callable[[str], None],
            on_finish: Callable[[int], None]) -> None:
        args = self._build_args(params)
        cwd = params.scan_tif.parent
        log.info("scanin: %s  [cwd=%s]", " ".join(args), cwd)
        self._last_log = ""
        self._matched_errors = []

        def _accumulate(line: str) -> None:
            self._last_log += line + "\n"
            self._scan_line(line)
            on_line(line)

        self._runner.run("scanin", args, cwd, on_line=_accumulate, on_finish=on_finish)

    def _scan_line(self, line: str) -> None:
        for pattern, key, fmt in _SCANIN_ERROR_PATTERNS:
            m = pattern.search(line)
            if m:
                groups = tuple(g or "" for g in m.groups())
                self._matched_errors.append((key, fmt.format(*groups)))

    def primary_failure(self) -> tuple[str, str] | None:
        return self._matched_errors[0] if self._matched_errors else None

    @property
    def last_log(self) -> str:
        return self._last_log

    def _build_args(self, p: ScaninParams) -> list[str]:
        return scanin_args(p.scan_tif, p.cht, p.cie, p.corners, p.perspective,
                           p.diag, p.robust_mean, out_name=p._out_name)
