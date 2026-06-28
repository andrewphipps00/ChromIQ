"""Area-first layout: size the patches to fill a target grid in the usable area.

The default engine layout is *patch-first* — you set the patch size and it fits
as many as possible, so the block rarely reaches the far margin. *Area-first*
(Knut's request, #93) flips it: you say how many strips (columns) and/or patches
per strip (rows) you want, and the engine derives the patch size so the grid
fills the usable area exactly. Patch size becomes the result, not the input.

This is a pre-process on the build-kwargs: it derives ``patch_w`` / ``patch_h``
and lets the normal patch-first geometry place them — so the capacity estimate
and the render stay in lock-step (they share the derived sizes), and instrument
constraints (clip border, label band, spacers, cut lines) are honoured because
the derivation measures a real provisional geometry.
"""
from __future__ import annotations

from . import geometry, instruments, papers

_MIN_PATCH_MM = 1.0       # floor so a too-dense grid can't go degenerate


def _usable(geom, w_mm: float, h_mm: float) -> tuple[float, float]:
    """(usable_width, usable_pass_length) in mm for the patch block — mirrors
    geometry.compute()'s avail_w / arowl so the derivation matches placement."""
    g = geom
    # Clip / notes band lives inside the clip-side margin (not subtracted again) —
    # mirrors geometry.compute() (Knut beta-13, clip-inside-margin).
    iw = w_mm - g.margin_l - g.margin_r
    avail_w = iw - g.rlwi - 2.0 * g.hxew - (g.pglth if g.dopglabel else 0.0)
    txhi = g.txhisl if g.label_band_mm < 0 else g.label_band_mm
    eff_lspa = max(g.border + g.lcar, g.lspa - g.txhisl + txhi)
    mints = max(g.margin_t + txhi + g.lcar, eff_lspa)
    minbs = max(g.margin_b, g.tspa, g.bottom_reserve_mm)
    arowl = h_mm - mints - minbs - 2.0 * g.hxeh - g.strip_indicator_gap
    if arowl > g.mxrowl:
        arowl = g.mxrowl
    return max(0.0, avail_w), max(0.0, arowl)


def _fit_columns(base: dict, w_mm: float, h_mm: float, cols: int,
                 max_pw: float) -> float | None:
    """Largest patch width (mm) at which exactly *cols* strips still fit across
    the page — so the strips span the usable width. Binary search over the real
    geometry (pitch / cut-line / clip-border overhead make a closed form
    instrument-specific)."""
    def strips(pw: float) -> int:
        # Columns across the page = passes = patches_per_page / steps_in_pass
        # (strips_per_page is 1 for the single-strip instruments).
        try:
            g = instruments.geom_from_build_kwargs({**base, "patch_w": pw})
            lay = geometry.compute(g, w_mm, h_mm, 100_000)
            return (lay.patches_per_page // lay.steps_in_pass
                    if lay.steps_in_pass else 0)
        except geometry.LayoutError:
            return 0

    if strips(_MIN_PATCH_MM) < cols:
        return None                       # can't fit that many even at the floor
    lo, hi = _MIN_PATCH_MM, max(_MIN_PATCH_MM, max_pw)
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if strips(mid) >= cols:
            lo = mid                      # still fits → patches can grow
        else:
            hi = mid
    return lo


def derive_area_patch_size(kw: dict) -> tuple[float, float] | None:
    """``(patch_w_mm, patch_h_mm)`` for an area-first recipe, or None when it
    doesn't apply (patch-first, or no target given). The caller sets these into
    the build-kwargs and runs the normal patch-first pipeline."""
    if kw.get("layout_mode") != "area_first":
        return None
    cols = int(kw.get("area_cols") or 0)
    rows = int(kw.get("area_rows") or 0)
    ratio = float(kw.get("area_ratio") or 0.0)     # height : width
    min_w = float(kw.get("area_min_patch") or 0.0)
    # The calculation method selects which inputs drive the grid: "by_width" uses
    # the minimum width + height%, "by_grid" uses explicit columns + rows (#93).
    method = kw.get("area_method") or "by_width"
    if method == "by_grid":
        min_w = 0.0
    else:
        cols = rows = 0
    if cols <= 0 and rows <= 0 and min_w <= 0:
        return None
    try:
        w_mm, h_mm = papers.dimensions_mm(kw.get("paper", "A4"))
    except Exception:
        return None
    # Provisional geometry (patch-first, auto patch size) for the usable area
    # and the spacer pitch the row formula needs.
    base = {**kw, "layout_mode": "patch_first"}
    base.pop("patch_w", None)
    base.pop("patch_h", None)
    try:
        geom = instruments.geom_from_build_kwargs(base)
    except Exception:
        return None
    avail_w, arowl = _usable(geom, w_mm, h_mm)
    if avail_w <= 0 or arowl <= 0:
        return None

    ec = 1.0 if geom.edge_spacers else -1.0     # geometry.compute()'s edge term

    def _rows_filling(n: int) -> float:
        # Invert compute()'s pprow so exactly n patches fill arowl.
        return (arowl - ec * geom.pspa) / max(1, n) - geom.pspa

    def _max_rows_at(height: float) -> int:
        return max(1, int((arowl - ec * geom.pspa) / (height + geom.pspa)))

    def _cols_at(width: float) -> int:
        # Columns a chart with this patch width would lay out across the page.
        try:
            g = instruments.geom_from_build_kwargs({**base, "patch_w": width})
            lay = geometry.compute(g, w_mm, h_mm, 100_000)
            return (lay.patches_per_page // lay.steps_in_pass
                    if lay.steps_in_pass else 0)
        except geometry.LayoutError:
            return 0

    # Resolve a column target (pinned, or the most that fit at the minimum width)
    # and a row target (pinned, or the most that fit at the minimum height), then
    # SIZE the patches to fill — Knut's "min size + ratio, grow to fill" path,
    # and the explicit-count path, share this fill step.
    # ratio is HEIGHT:WIDTH (height = width * ratio); 0 = square. (The panel
    # shows it as "minimum patch height, % of width".)
    pw = ph = None
    if rows > 0:
        ph = max(_MIN_PATCH_MM, _rows_filling(rows))
    elif min_w > 0:
        h_min = (min_w * ratio) if ratio > 0 else min_w
        ph = max(_MIN_PATCH_MM, _rows_filling(_max_rows_at(h_min)))
    if cols > 0:
        pw = _fit_columns(base, w_mm, h_mm, cols, max_pw=avail_w)
    elif min_w > 0:
        c = _cols_at(min_w)
        if c > 0:
            pw = _fit_columns(base, w_mm, h_mm, c, max_pw=avail_w)

    if pw is None and ph is not None:
        pw = ph / ratio if ratio > 0 else ph
    if ph is None and pw is not None:
        # Columns pinned, rows on "auto": area-first should FILL the page, so grow
        # the patch height until the last row reaches the bottom margin, using the
        # ratio/square height only as a floor — otherwise square patches leave a
        # gap at the bottom (#93, Knut beta-13).
        base_h = pw * ratio if ratio > 0 else pw
        ph = max(_MIN_PATCH_MM, base_h, _rows_filling(_max_rows_at(base_h)))
    if pw is None or ph is None or pw <= 0 or ph <= 0:
        return None
    # Floor to 0.01 mm so rounding can't nudge the patch over the boundary and
    # drop the column/row we just fitted.
    import math
    return (math.floor(pw * 100) / 100.0, math.floor(ph * 100) / 100.0)
