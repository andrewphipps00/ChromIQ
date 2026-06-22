"""Measure the realised page margins of a rendered chart preview.

The *patch area* is the block of patches and spacers printtarg lays out on the
sheet — **not** the strip-label band (A B C…), the rotated "ArgyllCMS…" title
down the right margin, nor any page label. Those are text outside the patch
area and are deliberately ignored (the i1Pro/jig only cares about where the
patches/spacers sit and how much white run-up surrounds them).

A *margin* is the white gap between a paper edge and the nearest patch or
spacer, on each of the four sides. Because printtarg only exposes a single
uniform ``-m`` margin (plus a fixed 26 mm i1Pro left clip border), the realised
per-side margins are an **output** of the whole layout — patch scale, spacer
size, ``-m``/``-M``, ``-L``, strip-length limit, paper size and orientation all
contribute. The only reliable way to know them is to measure the rendered page,
which is what this module does.

All values are reported in the **printtarg (TIFF) orientation** — i.e. exactly
what the preview shows. The jig is rotated 90° relative to this; that mapping is
explained in the startup help, not encoded here.

The patch-area bounding box is recovered with the same single-render geometry
the editor and Measure tab already use (:func:`workflow.ti2_relayout._patch_grid_bbox`),
so no black-and-white twin render is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from core.logger import get_logger
from core.strip_utils import parse_passes_per_page
from workflow.ti2_relayout import _imread_rgb, _patch_grid_bbox

log = get_logger(__name__)

_MM_PER_INCH = 25.4
_DEFAULT_DPI = 300.0
_WHITE = 240            # luminance ≥ this = bare paper (matches _patch_grid_bbox)
_PATCH_ROW_FILL = 0.5   # a patch row is ≥50% inked across the patch x-band


@dataclass(frozen=True)
class MarginReport:
    """Realised margins of one rendered page, in millimetres (printtarg frame).

    ``left/right/top/bottom`` are the white gaps from each paper edge to the
    patch area. ``strip_width_mm`` is the estimated per-patch extent along the
    instrument's reading (scan) direction, or ``None`` when it can't be derived.
    ``page_w_mm`` / ``page_h_mm`` are the full sheet size as rendered.
    """

    left_mm: float
    right_mm: float
    top_mm: float
    bottom_mm: float
    strip_width_mm: Optional[float]
    page_w_mm: float
    page_h_mm: float

    def as_dict(self) -> dict[str, float | None]:
        return {
            "L": self.left_mm, "R": self.right_mm,
            "T": self.top_mm, "B": self.bottom_mm,
            "strip": self.strip_width_mm,
        }


@dataclass(frozen=True)
class Violation:
    """One margin that fell below its configured minimum threshold."""

    edge: str           # "Left" | "Right" | "Top" | "Bottom"
    measured_mm: float
    threshold_mm: float


# edge label → MarginReport attribute + threshold-dict key
_EDGES = (
    ("Left", "left_mm", "L"),
    ("Right", "right_mm", "R"),
    ("Top", "top_mm", "T"),
    ("Bottom", "bottom_mm", "B"),
)


def check_violations(
    report: MarginReport, thresholds: dict | None,
) -> list[Violation]:
    """Edges whose measured margin is below the per-edge minimum threshold.

    ``thresholds`` is a ``{"L":mm, "R":mm, "T":mm, "B":mm, "desc":…}`` mapping
    (the entry for this chart's instrument+paper+orientation combo), or ``None``
    when no thresholds are defined for the combo — in which case nothing is
    checked (the inspector still shows the measured values). Thresholds are
    minimums to meet-or-exceed; there is no upper bound.
    """
    if not thresholds:
        return []
    out: list[Violation] = []
    for label, attr, key in _EDGES:
        raw = thresholds.get(key)
        if raw in (None, ""):
            continue
        try:
            thr = float(raw)
        except (TypeError, ValueError):
            continue
        measured = float(getattr(report, attr))
        if measured + 1e-6 < thr:
            out.append(Violation(label, measured, thr))
    return out


def _tiff_dpi(path: Path, fallback: float) -> float:
    """Resolution printtarg baked into the TIFF, or ``fallback`` when absent."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            dpi = im.info.get("dpi")
        if dpi and dpi[0] and float(dpi[0]) > 1.0:
            return float(dpi[0])
    except Exception as exc:  # pragma: no cover - corrupt/odd TIFFs
        log.debug("Could not read TIFF dpi from %s: %s", path, exc)
    return float(fallback)


def _estimate_strip_width_mm(
    block_w_mm: float, block_h_mm: float,
    n_strips: int, steps: int,
) -> Optional[float]:
    """Per-patch extent along the reading (scan) direction, in mm.

    The patch block is ``n_strips`` strips × ``steps`` patches-per-strip, but
    which pixel axis carries which count depends on orientation. printtarg lays
    patches out roughly square, so we pick the axis assignment whose resulting
    cell is closest to square, then report the cell dimension along the *steps*
    (reading) axis — that is the size the instrument must resolve per patch.
    """
    if n_strips < 1 or steps < 1:
        return None
    # Assignment A: strips tile across width, steps run down height.
    cell_a_cross = block_w_mm / n_strips   # cross-scan pitch
    cell_a_read = block_h_mm / steps       # reading-direction patch length
    # Assignment B: strips tile across height, steps run across width.
    cell_b_cross = block_h_mm / n_strips
    cell_b_read = block_w_mm / steps

    def _squareness(a: float, b: float) -> float:
        if a <= 0 or b <= 0:
            return float("inf")
        return abs((a / b) - 1.0)

    skew_a = _squareness(cell_a_cross, cell_a_read)
    skew_b = _squareness(cell_b_cross, cell_b_read)
    return cell_a_read if skew_a <= skew_b else cell_b_read


def measure_margins(
    tif_path: "Path | str",
    *,
    dpi: float | None = None,
    ti2_path: "Path | str | None" = None,
    paper_w_mm: float | None = None,
    paper_h_mm: float | None = None,
) -> Optional[MarginReport]:
    """Measure the four page margins of a single rendered chart page.

    ``tif_path`` is one printtarg page TIFF (full print resolution). ``dpi`` is
    used only when the TIFF carries no resolution tag. ``ti2_path`` (optional)
    enables the estimated strip-width readout via the chart's strip/step counts.

    ``paper_w_mm`` / ``paper_h_mm`` are the true sheet size. ChromIQ renders
    charts with printtarg ``-M`` (the page margin is *included* in the TIFF, so
    the TIFF already spans the full sheet) — in that case they're optional. But
    plain ``-m`` *subtracts* the margin from the raster, leaving a TIFF cropped
    to the imageable area; when the true paper size is supplied and the TIFF is
    smaller, the symmetric ``(paper − tiff) / 2`` margin printtarg trimmed is
    added back so the reported margins are paper-edge to patch on both modes.

    Returns ``None`` when the patch area can't be located (caller shows a
    "couldn't measure" placeholder rather than wrong numbers).
    """
    tif_path = Path(tif_path)
    if not tif_path.is_file():
        return None
    try:
        arr = _imread_rgb(tif_path)
    except Exception as exc:
        log.warning("Margin inspector: cannot read %s: %s", tif_path, exc)
        return None

    h_px, w_px = arr.shape[:2]
    res = _tiff_dpi(tif_path, dpi if dpi else _DEFAULT_DPI)
    px2mm = _MM_PER_INCH / res
    tif_w_mm = w_px * px2mm
    tif_h_mm = h_px * px2mm

    # printtarg -m crops the margin out of the TIFF; -M keeps it. When the true
    # paper size is known and the TIFF is smaller, restore the symmetric margin
    # printtarg trimmed so margins read paper-edge to patch in both modes.
    off_x = max(0.0, (paper_w_mm - tif_w_mm) / 2.0) if paper_w_mm else 0.0
    off_y = max(0.0, (paper_h_mm - tif_h_mm) / 2.0) if paper_h_mm else 0.0
    page_w_mm = paper_w_mm if (paper_w_mm and paper_w_mm >= tif_w_mm) else tif_w_mm
    page_h_mm = paper_h_mm if (paper_h_mm and paper_h_mm >= tif_h_mm) else tif_h_mm

    bbox = _patch_area_bbox(arr)
    if bbox is None:
        log.debug("Margin inspector: patch area not found in %s", tif_path)
        return None
    y0, y1, x0, x1 = bbox   # inclusive pixel coords

    left_mm = x0 * px2mm + off_x
    right_mm = (w_px - 1 - x1) * px2mm + off_x
    top_mm = y0 * px2mm + off_y
    bottom_mm = (h_px - 1 - y1) * px2mm + off_y

    strip_width_mm: Optional[float] = None
    if ti2_path is not None:
        try:
            passes = parse_passes_per_page(ti2_path)
            steps = _steps_in_pass(ti2_path)
            page_ix = _page_index_of(tif_path)
            if passes and steps and 0 <= page_ix < len(passes):
                block_w_mm = (x1 - x0 + 1) * px2mm
                block_h_mm = (y1 - y0 + 1) * px2mm
                strip_width_mm = _estimate_strip_width_mm(
                    block_w_mm, block_h_mm, passes[page_ix], steps,
                )
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Margin inspector: strip width skipped: %s", exc)

    return MarginReport(
        left_mm=max(0.0, left_mm), right_mm=max(0.0, right_mm),
        top_mm=max(0.0, top_mm), bottom_mm=max(0.0, bottom_mm),
        strip_width_mm=strip_width_mm,
        page_w_mm=page_w_mm, page_h_mm=page_h_mm,
    )


def _patch_area_bbox(arr: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """Inclusive ``(y0, y1, x0, x1)`` pixel box of the patches+spacers block.

    ``_patch_grid_bbox`` gives a reliable **horizontal** extent (it drops the
    rotated right-margin title), but its vertical extent is contaminated by that
    same title (the editor only ever uses its x-range). So we take x from it and
    derive y ourselves: inside the patch x-band a patch row is almost fully
    inked (patches/spacers tile edge-to-edge), whereas the strip-label band, the
    bottom page label and any stray text are sparse. The contiguous run of
    well-filled rows is the patch block — which is exactly the patch-area edge
    "where paper white meets a patch or spacer", with text excluded.
    """
    grid = _patch_grid_bbox(arr)
    if grid is None:
        return None
    _, _, x0, x1 = grid
    if x1 <= x0:
        return None
    gray = arr.mean(axis=2)
    band = gray[:, x0:x1 + 1]
    row_fill = (band < _WHITE).mean(axis=1)
    rows = np.where(row_fill > _PATCH_ROW_FILL)[0]
    if rows.size == 0:
        return None
    y0, y1 = int(rows[0]), int(rows[-1])
    return (y0, y1, x0, x1)


def _steps_in_pass(ti2_path: "Path | str") -> int | None:
    import re

    try:
        text = Path(ti2_path).read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r'STEPS_IN_PASS\s+"?(\d+)"?', text)
    return int(m.group(1)) if m else None


def _page_index_of(tif_path: Path) -> int:
    """0-based page index from a ``<stem>_NN.tif`` filename (single page → 0)."""
    import re

    m = re.search(r"_(\d+)$", tif_path.stem)
    if not m:
        return 0
    # printtarg numbers pages from 1 (_01, _02, …); _patch_grid_bbox runs on
    # the per-page TIFF, so the index into `passes` is NN-1.
    return max(0, int(m.group(1)) - 1)
