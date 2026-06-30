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
    """(usable_width, usable_pass_length) in mm for the patch block.

    Area-first is "margins are the law" (Knut #93): the usable patch area is
    exactly the margin box — no hidden leader/trailer/label-band reserve (strip
    labels live inside the top margin) and no instrument ruler cap (the box is
    filled even past the ruler; a violation warns). This mirrors
    geometry.compute()'s law branch so the derived patch size fills the same area
    the renderer places into. The clip / notes band lives inside the clip-side
    margin (build() floors that margin to the clip width), so it is not
    subtracted again here.
    """
    g = geom
    iw = w_mm - g.margin_l - g.margin_r
    avail_w = iw - g.rlwi - 2.0 * g.hxew - (g.pglth if g.dopglabel else 0.0)
    arowl = h_mm - g.margin_t - g.margin_b - 2.0 * g.hxeh
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
    # Both methods always fill the box now (Knut): "Minimum patch width = auto"
    # is not "don't fill" — it means "use the instrument's natural width as the
    # minimum, then grow to fill", exactly like a typed value. (by_grid likewise
    # always fills.) So area-first never falls back to the loose patch-first size.
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

    # "Reasonable" default patch size an auto dimension aims for: the instrument's
    # natural patch geometry — the patch dimensions the device was designed to read
    # (its aperture and the along-strip spacing). Knut #93: this is a sufficient
    # auto target on its own, so there's no separate user-facing size table; the
    # area_default_w/h kwargs only let a caller override it for tests. An auto
    # dimension is sized toward this rather than whatever falls out of the page.
    default_w = float(kw.get("area_default_w") or 0.0) or geom.pwid
    default_h = float(kw.get("area_default_h") or 0.0) or geom.plen

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
    if method != "by_grid":                         # --- by_width: min width + % ---
        # "auto" (no minimum typed) uses the instrument's natural width as the
        # minimum, so it still grows to fill the box (Knut: the label says
        # "minimum", so it's a floor that grows, never a fixed under-fill).
        if min_w <= 0:
            min_w = default_w
        target = int(kw.get("area_target_count") or 0)
        if target > 0:
            # COUNT-AWARE fill (Knut: the margin box is law and must be FULLY
            # filled): size the patches so this many fill the box. Choose a column
            # count that balances the grid to the box and patch aspect, then size
            # the patches to fill the width (those columns) AND the height (the
            # rows that count needs) — so a fixed patch set fills the area even
            # when the minimum width alone would leave a big gap on the right. The
            # minimum width is a FLOOR: never go below it (cap the columns so the
            # filled width stays ≥ the minimum).
            import math
            r_ = ratio if ratio > 0 else 1.0
            cmax = _cols_at(min_w) or 1          # most columns that fit at the min
            cols = max(1, min(cmax, round(
                math.sqrt(target * r_ * avail_w / max(1e-6, arowl)))))
            pw = _fit_columns(base, w_mm, h_mm, cols, max_pw=avail_w)
            rows = max(1, math.ceil(target / cols))
            ph = max(_MIN_PATCH_MM, _rows_filling(rows))
        else:
            # No fixed count (auto patch count): fit as many strips as possible at
            # the minimum, then grow the patch width so they span the box; height
            # follows from the GROWN width via the height-% and grows to fill the
            # usable height — both dimensions fill the margin box (Knut).
            c = _cols_at(min_w)
            if c > 0:
                pw = _fit_columns(base, w_mm, h_mm, c, max_pw=avail_w)
            h_min = (pw * ratio) if (pw is not None and ratio > 0) else (
                (min_w * ratio) if ratio > 0 else (pw or min_w))
            ph = max(_MIN_PATCH_MM, _rows_filling(_max_rows_at(h_min)))
    else:                                           # --- by_grid: columns / rows ---
        # Pinned dimensions size their patches to fill exactly that many; an "auto"
        # dimension picks a count that gives a REASONABLE patch size — the ratio-
        # linked size when the other dimension is pinned (so the shape is kept),
        # otherwise the instrument's NATURAL patch size (its built-in width/height,
        # the per-instrument "default size" reference) — then fills to that count
        # (Knut #93). Columns first, so rows-auto can use the resolved width.
        if rows > 0:
            ph = max(_MIN_PATCH_MM, _rows_filling(rows))
        if cols > 0:
            pw = _fit_columns(base, w_mm, h_mm, cols, max_pw=avail_w)
        if cols <= 0:                               # columns auto → fill the width
            target_w = (ph / ratio) if (ph is not None and ratio > 0) else default_w
            c = _cols_at(target_w)
            if c > 0:
                pw = _fit_columns(base, w_mm, h_mm, c, max_pw=avail_w)
        if rows <= 0:                               # rows auto → fill the height
            target_h = (pw * ratio) if (pw is not None and ratio > 0) else default_h
            ph = max(_MIN_PATCH_MM, _rows_filling(_max_rows_at(target_h)))

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
