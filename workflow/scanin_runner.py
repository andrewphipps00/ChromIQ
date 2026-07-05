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

_BOX_LINE = re.compile(r"^\s*[XY]\s+\S+\s+\S+\s+\S+\s+\S+\s+"
                       r"([\d.]+)\s+([\d.]+)\s", re.MULTILINE)
_SHRINK_LINE = re.compile(r"^(\s*BOX_SHRINK\s+)([\d.]+)", re.MULTILINE)


def sample_area_box_shrink(cht_text: str, frac: float) -> float | None:
    """The ``BOX_SHRINK`` (cht units, per side) that makes scanin read *frac* of
    each patch's AREA. A patch of side *B* sampled at area fraction *f* keeps an
    inner square of side ``B·√f``, i.e. a per-side shrink of ``B·(1−√f)/2``.
    *B* is the median box side across the chart (exact for a uniform grid, which
    every standard target is). Returns ``None`` for full-area (≥1) or no boxes."""
    frac = max(0.05, min(1.0, float(frac)))
    if frac >= 0.999:
        return 0.0
    sides: list[float] = []
    for w, h in _BOX_LINE.findall(cht_text):
        sides += [float(w), float(h)]
    if not sides:
        return None
    sides.sort()
    b = sides[len(sides) // 2]                      # median side
    return round(b * (1.0 - frac ** 0.5) / 2.0, 3)


def cht_with_sample_area(cht_text: str, frac: float) -> str:
    """Return *cht_text* with its ``BOX_SHRINK`` set for sample-area *frac*.
    Unchanged if the fraction implies no usable shrink or the file has no boxes."""
    shrink = sample_area_box_shrink(cht_text, frac)
    if shrink is None:
        return cht_text
    if _SHRINK_LINE.search(cht_text):
        return _SHRINK_LINE.sub(lambda m: f"{m.group(1)}{shrink:.3f}", cht_text, count=1)
    # No BOX_SHRINK line — insert one after the last box line.
    return cht_text.rstrip() + f"\n\nBOX_SHRINK {shrink:.3f}\n"


_TI3_STR_FIELDS = {"SAMPLE_ID", "SAMPLE_LOC", "SAMPLE_NAME"}


def _ti3_bad(tok: str) -> bool:
    """True if a token isn't a finite real — nan/inf and Windows' ``1.#IND`` /
    ``1.#QNAN`` / ``-1.#INF`` forms (which fail ``float()``)."""
    try:
        v = float(tok)
        return v != v or v in (float("inf"), float("-inf"))
    except ValueError:
        return True


def sanitize_ti3(text: str) -> tuple[str, int, int]:
    """Make a scanner ``.ti3`` safe for colprof when scanin wrote non-real values
    for a degenerate patch read (colprof otherwise rejects the *whole* file:
    ``Field 'STDEV_B' … is 'non-quoted char string'``).

    A bad ``STDEV_*`` (a patch read fine but its variance is undefined — e.g. a
    single-pixel box) is set to ``0``. A bad **value** column (``RGB_*``/``XYZ_*``/
    ``LAB_*`` — the box caught no usable pixels, so there's no real reading) makes
    the whole patch **dropped** instead of zero-filled, so it can't become a false
    "reads as black" tie point in the profile; ``NUMBER_OF_SETS`` is updated to
    match. Returns ``(new_text, n_zeroed, n_dropped)``; unchanged when clean."""
    lines = text.splitlines()
    try:
        fi = next(i for i, ln in enumerate(lines) if ln.strip() == "BEGIN_DATA_FORMAT")
        fields = lines[fi + 1].split()
        db = next(i for i, ln in enumerate(lines) if ln.strip() == "BEGIN_DATA")
        de = next(i for i, ln in enumerate(lines) if ln.strip() == "END_DATA")
    except (StopIteration, IndexError):
        return text, 0, 0
    stdev_cols = [c for c, f in enumerate(fields) if f.upper().startswith("STDEV")]
    value_cols = [c for c, f in enumerate(fields)
                  if f not in _TI3_STR_FIELDS and not f.upper().startswith("STDEV")]
    zeroed = dropped = 0
    out_rows: list[str] = []
    for li in range(db + 1, de):
        raw = lines[li]
        toks = raw.split()
        if len(toks) != len(fields):
            out_rows.append(raw)                       # leave odd lines alone
            continue
        if any(_ti3_bad(toks[c]) for c in value_cols):
            dropped += 1                               # no real reading → drop
            continue
        changed = False
        for c in stdev_cols:
            if _ti3_bad(toks[c]):
                toks[c] = "0"
                zeroed += 1
                changed = True
        out_rows.append(" ".join(toks) if changed else raw)
    if not zeroed and not dropped:
        return text, 0, 0
    new = lines[:db + 1] + out_rows + lines[de:]
    if dropped:                                        # keep NUMBER_OF_SETS honest
        kept = len(out_rows)
        for i, ln in enumerate(new):
            if ln.strip().upper().startswith("NUMBER_OF_SETS"):
                new[i] = f"NUMBER_OF_SETS {kept}"
                break
    return "\n".join(new) + ("\n" if text.endswith("\n") else ""), zeroed, dropped


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


def scanin_printer_args(scan_tif: Path, cht: Path, scan_profile: Path, pbase: Path,
                        corners: list[tuple[float, float]] | None = None,
                        perspective: bool = True, diag: Path | None = None,
                        verbose: bool = True, accumulate: bool = False) -> list[str]:
    """Build the ``scanin -c`` argument list for **printer-profile** mode.

    Instead of profiling the scanner, this turns the scan into a *printer*
    measurement: it reads ``<pbase>.ti2`` (the chart's printer device values) and,
    converting each scanned patch to real colour through *scan_profile* (a scanner
    ICC the user built earlier), writes ``<pbase>.ti3`` — which colprof turns into
    a printer profile. The flat-bed scanner acts as the measuring instrument.
    ``scanin -c [opts] input.tif recog.cht scanprofile.icc pbase [diag.tif]``."""
    args: list[str] = []
    if verbose:
        args.append("-v")
    args.append("-ca" if accumulate else "-c")   # -ca adds a page to an existing .ti3
    if corners is not None:
        args += ["-F", _fmt_corners(corners)]
    if perspective:
        args.append("-p")
    if diag is not None:
        args.append("-dipn")
    args += [str(scan_tif), str(cht), str(scan_profile), str(pbase)]
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

    # --- reference-file (.cht / .cie) failures ----------------------------
    # These files are written (and pre-validated) by ChromIQ, so in normal use
    # they can't be malformed — but a corrupted/edited file, a mismatched
    # .cht+.cie pair, or a writer regression would otherwise surface as a raw
    # Argyll dump. Collapse the many CGATS complaints into two clear messages.
    #
    # Bucket A — the scanner files are damaged / incomplete (malformed CTI2,
    # empty tables, missing COLOR_REP / SAMPLE_ID / SAMPLE_LOC, unresolvable
    # sample or location). scanin.c L623-882, L1126-1210.
    (re.compile(
        r"isn't a CTI2 format file"
        r"|doesn't contain at least one table"
        r"|doesn't (?:contain any data sets|contain any|have any)"
        r"|(?:has no|no) sets of data"
        r"|doesn't contain keyword COLOR_REPS?"
        r"|keyword COLOR_REPS? has unknown value"
        r"|doesn't contain field SAMPLE_(?:ID|LOC)"
        r"|[Ff]ield SAMPLE_(?:ID|LOC) is wrong type"
        r"|Couldn't find (?:sample|location) '[^']*'",
        re.IGNORECASE),
     "reference_damaged",
     "This chart's scanner files (.cht + .cie) look damaged or incomplete. "
     "Recreate them with Tools ▸ Create scanner target, then try again."),

    # Bucket B — the .cht/.cie don't match this chart's measurement: different
    # patch count, mismatched patch IDs/device values, or a different device
    # space (e.g. a .cht from one chart paired with another's .cie).
    # scanin.c L691, L957-970.
    (re.compile(
        r"[Dd]ifferent number of patches"
        r"|field id's don't match at patch"
        r"|device values .*don't match at patch"
        r"|has different device space",
        re.IGNORECASE),
     "reference_mismatch",
     "The scanner files don't match this chart's measurement (different "
     "patches or device type). Recreate them from this chart's own "
     "measurement with Tools ▸ Create scanner target."),

    # Bucket C — a generic CGATS read/write failure on a reference or output
    # file (permission, disk, truncation). scanin.c L596-799, L1165.
    (re.compile(r"CGATS file .*read error|[Ww]rite error to|Can't open file",
                re.IGNORECASE),
     "reference_io",
     "Couldn't read or write one of the scanner files. Check the files exist "
     "and the folder is writable, then try again."),

    # Out of memory on a very large scan. scanin.c L521, L976.
    (re.compile(r"Malloc failed|Unable to allocate", re.IGNORECASE),
     "out_of_memory",
     "Ran out of memory while processing the scan. Try scanning the chart at a "
     "lower resolution (300–600 dpi is plenty)."),
]


@dataclass
class ScaninParams:
    """One scanned page → a scanner ``.ti3``. Paths are absolute."""

    scan_tif: Path
    cht: Path
    cie: Path | None = None
    corners: list[tuple[float, float]] | None = None   # None = auto-recognise
    perspective: bool = True
    diag: Path | None = None
    robust_mean: bool = True
    # Distinct output name (via -O) so the scanner .ti3 can never collide with the
    # chart's own <stem>.ti3 / printer profile. Defaults to "<scan>-scanner.ti3".
    out_name: str | None = None
    # Printer-profile mode (scanin -c): convert the scan to real colour through a
    # scanner ICC and read <pbase>.ti2 → write <pbase>.ti3 (a printer measurement).
    scan_profile: Path | None = None
    pbase: Path | None = None
    accumulate: bool = False           # printer mode: -ca adds this page to <pbase>.ti3

    @property
    def is_printer(self) -> bool:
        return self.scan_profile is not None and self.pbase is not None

    @property
    def _out_name(self) -> str:
        return self.out_name or f"{self.scan_tif.stem}-scanner.ti3"

    @property
    def out_ti3(self) -> Path:
        """The ``.ti3`` scanin writes: in printer mode ``<pbase>.ti3``; otherwise
        the scanner ``<scan>-scanner.ti3`` (never the chart's own ``<stem>.ti3``)."""
        if self.is_printer:
            return self.pbase.with_suffix(".ti3")
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
        if p.is_printer:
            return scanin_printer_args(p.scan_tif, p.cht, p.scan_profile, p.pbase,
                                       p.corners, p.perspective, p.diag,
                                       accumulate=p.accumulate)
        return scanin_args(p.scan_tif, p.cht, p.cie, p.corners, p.perspective,
                           p.diag, p.robust_mean, out_name=p._out_name)
