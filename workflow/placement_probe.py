"""Dense placement evaluation for the scanner tool's Check-alignment button
(Knut, #108 round 12).

Knut's design, adopted: instead of re-running scanin at a handful of probe
positions, measure the scan densely ONCE and evaluate his full step ladder —
24 steps of 5 % of a patch pitch in 8 directions — as pure lookups. His
proposal reached the dense measurements by generating a high-density .cht and
sending it through scanin; this implementation samples the image directly with
the same maths scanin uses for manually-placed grids (the -F homography and
per-box mean over the sample area), which produces the same numbers without
the detour, so the whole ladder costs milliseconds instead of minutes.

Every candidate position's sampled colour is compared with the patch's
expected value through a response map (fitted page-wide, so an unprofiled
scanner's response and a printer's gamut compression cancel out). Each patch
is then normalised on its OWN residual ladder (Knut, #119): its best position
anywhere is the 100 % floor; each of the 8 directions contributes its worst
residual, directions that never worsen beyond the noise floor are ignored,
and the LEAST of the remaining direction-worsts — lowered by a small buffer —
is the 0 % roof. Because every patch is ranked against itself, per-patch
offsets the page-wide response model can't express cancel out.

The page's ``agreement_pct`` is the single WORST patch (Knut — the verdict
number), ``average_pct`` is the arithmetic mean of all per-patch agreements,
and the worst patches are named. worst ≤ average holds by construction.
"""
from __future__ import annotations

import math
from pathlib import Path

__all__ = ["PlacementReport", "dense_placement_agreement"]

# Share of each FULL patch the edge check's sensing grid covers (Knut's #119
# activation-box design). The G×G sub-cell grid always spans this much of the
# patch; which of its cells actually detect is decided by the ACTIVATION box —
# the real (equal-margin) sample box plus half a sub-cell on every side — so
# detection follows the sample area's rim at every Patch-sample-area setting,
# and the outermost cells only wake up at ~80 %. 0.95 calibrated on Knut's
# real scans (0.90/0.92/0.95 tried): the grid's outer edge stays clear of a
# contiguous chart's border blur while an aligned 80 % grid passes.
FLANK_GRID_COVER = 0.95
# Largest share of the patch (per side) the activation box may reach — the
# calibrated safe zone on a contiguous chart (see its use below).
FLANK_REACH_MAX = 0.52



class PlacementReport:
    """Result of the dense evaluation: page agreement in percent (worst
    patch), the per-patch percentages, and the failing patches."""

    def __init__(self, agreement_pct: float,
                 per_patch: dict[str, float],
                 offenders: list[tuple[str, float]],
                 s_user: float | None = None,
                 s_best: float | None = None,
                 s_floor: float | None = None,
                 average_pct: float | None = None) -> None:
        self.agreement_pct = agreement_pct
        # Arithmetic mean of the per-patch agreements (Knut, #119) — shown
        # alongside the worst-patch verdict, never used for it.
        self.average_pct = (agreement_pct if average_pct is None
                            else average_pct)
        # Per-patch agreement in percent (100 = the box reads as well as the
        # ladder's best position for that patch; 0 = as badly as the roof).
        self.per_patch = per_patch
        self.offenders = offenders
        self.s_user = s_user          # raw score of the user's position
        self.s_best = s_best          # raw score of the ladder's best
        self.s_floor = s_floor        # the 0 %-end raw score (least worst)
        # Flank detection (Knut, #108): per patch, how strongly one edge of
        # the sample box deviates from the box centre — a box sitting on a
        # patch border shows its neighbour's colour along that side. Range-
        # normalised; ≈ noise for boxes fully inside flat colour.
        self.flank_by_patch: dict[str, float] = {}


def _isotonic(ys: list[float]) -> list[float]:
    """Non-decreasing least-squares fit (pool adjacent violators)."""
    pools: list[list[float]] = []
    for v in ys:
        pools.append([v, 1.0])
        while len(pools) > 1 and pools[-2][0] > pools[-1][0]:
            v2, n2 = pools.pop()
            v1, n1 = pools.pop()
            pools.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2])
    fit: list[float] = []
    for mean, count in pools:
        fit.extend([mean] * int(count))
    return fit


def _homography(src: list[tuple[float, float]],
                dst: list[tuple[float, float]]):
    """3×3 homography mapping the 4 *src* points onto *dst* (DLT, no numpy
    dependency beyond what PIL already pulls in)."""
    import numpy as np
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.asarray(a, dtype=float))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def dense_placement_agreement(
        image_path: Path,
        boxes: list,                      # cht patch boxes (x1,y1,x2,y2,name)
        corners: list[tuple[float, float]],
        expected_by_id: dict[str, float],
        sample_frac: float = 0.5,
        steps: int = 24,
        step_frac: float = 0.05,
        max_side: int = 2200,
        objective: str = "combined",
        src_quad: list[tuple[float, float]] | None = None,
        flank_min_cells: int = 6) -> PlacementReport | None:
    """Evaluate the placement *corners* of the patch grid over *image_path*
    against every position of Knut's step ladder. Returns None when the image
    can't be read or too few patches pair with the reference."""
    import numpy as np
    from PIL import Image

    try:
        img = Image.open(image_path)
        img.load()
    except Exception:  # noqa: BLE001
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))))
    arr = np.asarray(img, dtype=np.float64)
    if arr.ndim == 3:
        lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1]
               + 0.0722 * arr[..., 2])
        # Opponent chroma planes for the edge term: neighbouring patches on
        # real targets often differ in COLOUR at nearly equal luminance (an
        # IT8's vertical neighbours especially), so an edge invisible in
        # luminance still shows in red–blue or red+blue–green (Knut's
        # beta.136 test: vertical offsets sailed through a luminance-only
        # check).
        chroma = [arr[..., 0] - arr[..., 2],
                  arr[..., 0] + arr[..., 2] - 2.0 * arr[..., 1]]
    else:
        lum = arr
        chroma = []
    hgt, wdt = lum.shape

    def _integrals(plane):
        i1 = np.zeros((hgt + 1, wdt + 1))
        i1[1:, 1:] = np.cumsum(np.cumsum(plane, axis=0), axis=1)
        i2 = np.zeros((hgt + 1, wdt + 1))
        i2[1:, 1:] = np.cumsum(np.cumsum(plane * plane, axis=0), axis=1)
        return i1, i2

    integ, integ2 = _integrals(lum)

    def _box_ixs(x0: float, y0: float, x1: float, y1: float):
        xa, ya = int(round(x0)), int(round(y0))
        xb, yb = int(round(x1)), int(round(y1))
        if xa < 0 or ya < 0 or xb > wdt or yb > hgt or xb - xa < 1 or yb - ya < 1:
            return None
        return xa, ya, xb, yb

    def _stats(i1, i2, ix):
        xa, ya, xb, yb = ix
        n = (xb - xa) * (yb - ya)
        s1 = i1[yb, xb] - i1[ya, xb] - i1[yb, xa] + i1[ya, xa]
        s2 = i2[yb, xb] - i2[ya, xb] - i2[yb, xa] + i2[ya, xa]
        mean = s1 / n
        var = max(0.0, s2 / n - mean * mean)
        return float(mean), float(var ** 0.5)

    def box_stats(x0: float, y0: float, x1: float, y1: float):
        ix = _box_ixs(x0, y0, x1, y1)
        if ix is None:
            return None
        return _stats(integ, integ2, ix)

    named = [b for b in boxes if b.name in expected_by_id]
    if len(named) < 16:
        return None
    # The quad the corners were placed on: the cht's fiducial frame when
    # given (corners follow the F line, as with scanin's -F), else the
    # patch-area bounding box.
    if src_quad is not None:
        src = list(src_quad)
    else:
        minx = min(b.x1 for b in boxes); maxx = max(b.x2 for b in boxes)
        miny = min(b.y1 for b in boxes); maxy = max(b.y2 for b in boxes)
        src = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    dst = [(x * scale, y * scale) for x, y in corners]
    h = _homography(src, dst)

    def warp(x: float, y: float) -> tuple[float, float]:
        d = h[2, 0] * x + h[2, 1] * y + h[2, 2]
        return ((h[0, 0] * x + h[0, 1] * y + h[0, 2]) / d,
                (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / d)

    # pitch (cht units): smallest positive gap between box starts, per axis;
    # falls back to the median box size for single-column/row layouts.
    def _pitch(vals: list[float], fallback: float) -> float:
        """Patch pitch from the box origins. A gap only counts when it is a
        meaningful fraction of a box — boxes can't overlap, so the true
        pitch is never smaller than a box width. Sub-unit float scatter
        between same-column origins (the sample-area shrink/grow round-trip
        leaves each box with its own rounding, #119: it made the smallest
        'gap' ~0.002 units, the ladder offsets collapsed to nothing and
        every position read identically — agreement pinned at 100.00 %)
        must never be mistaken for the pitch."""
        u = sorted(set(round(v, 3) for v in vals))
        floor_gap = max(1e-3, 0.25 * fallback)
        gaps = [b - a for a, b in zip(u, u[1:]) if b - a > floor_gap]
        return min(gaps) if gaps else fallback
    widths = sorted(b.x2 - b.x1 for b in named)
    heights = sorted(b.y2 - b.y1 for b in named)
    px = _pitch([b.x1 for b in named], widths[len(widths) // 2])
    py = _pitch([b.y1 for b in named], heights[len(heights) // 2])

    # Knut's ladder: 24 steps × 5 % in 8 directions (+ the centre), reaching
    # the same 120 % of a pitch as the old 12 × 10 %.
    #
    # 5 % and not 2 %: the image is downscaled to max_side BEFORE sampling, so
    # the scan's dpi does not set the step size — the patch pitch in sampled
    # pixels does. At max_side=2200 a 4 mm patch (ChromIQ's densest scanner
    # chart, A4) spans ~30 px, so 5 % = 1.5 px but 2 % = 0.6 px. _box_ixs
    # rounds the sample box to whole pixels, so a sub-pixel rung lands on the
    # box its predecessor already measured: 60 × 2 % costs 5× the work and
    # returns duplicate positions. 5 % is the finest rung that still moves the
    # box on the smallest patch we support (Knut, #119).
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    offsets = [(0.0, 0.0)]
    for ddx, ddy in dirs:
        for k in range(1, steps + 1):
            offsets.append((ddx * k * step_frac * px, ddy * k * step_frac * py))

    # Sample every patch at every ladder position.
    reads: dict[str, list[float | None]] = {}
    spreads: dict[str, list[float | None]] = {}
    box_ixs: dict[str, list] = {}
    for b in named:
        cx, cy = (b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0
        hx = (b.x2 - b.x1) * sample_frac / 2.0
        hy = (b.y2 - b.y1) * sample_frac / 2.0
        vals: list[float | None] = []
        devs: list[float | None] = []
        ixs: list = []
        for ox, oy in offsets:
            xa, ya = warp(cx + ox - hx, cy + oy - hy)
            xb, yb = warp(cx + ox + hx, cy + oy + hy)
            ix = _box_ixs(min(xa, xb), min(ya, yb),
                          max(xa, xb), max(ya, yb))
            ixs.append(ix)
            if ix is None:
                vals.append(None)
                devs.append(None)
            else:
                mean, dev = _stats(integ, integ2, ix)
                vals.append(mean)
                devs.append(dev)
        reads[b.name] = vals
        spreads[b.name] = devs
        box_ixs[b.name] = ixs

    # Edge-check sensing boxes (Knut's #119 activation-box design): the
    # sensing grid always covers FLANK_GRID_COVER of the FULL patch —
    # activation, not geometry, follows the Patch-sample-area setting.
    flank_ixs = {}
    for b in named:
        cx, cy = (b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0
        hx = (b.x2 - b.x1) * FLANK_GRID_COVER / 2.0
        hy = (b.y2 - b.y1) * FLANK_GRID_COVER / 2.0
        ixs = []
        for ox, oy in offsets:
            xa, ya = warp(cx + ox - hx, cy + oy - hy)
            xb, yb = warp(cx + ox + hx, cy + oy + hy)
            ixs.append(_box_ixs(min(xa, xb), min(ya, yb),
                                max(xa, xb), max(ya, yb)))
        flank_ixs[b.name] = ixs

    # Chroma planes, swept one at a time (two integral images live at once —
    # keeps memory flat): the edge term takes the strongest spread across
    # luminance and both opponent planes.
    for plane in chroma:
        i1, i2 = _integrals(plane)
        for name, ixs in box_ixs.items():
            devs = spreads[name]
            for k, ix in enumerate(ixs):
                if ix is None:
                    continue
                _m, dev = _stats(i1, i2, ix)
                if devs[k] is not None and dev > devs[k]:
                    devs[k] = dev
        del i1, i2

    # Score every ladder position by page-level consistency: fit a monotone
    # response (expected → read) from THAT position's samples and take the
    # 95th-percentile |residual| (worst-rules with dust immunity). A correct
    # placement admits one clean response; sampling areas that cross into
    # neighbouring patches break it and the score climbs steeply. Pooling
    # the patches per position is what makes real targets workable: a single
    # patch's deviation from the (batch-averaged) reference data would create
    # false baselines, but it cancels in the page-level ranking.
    import bisect

    def page_score(pos_i: int):
        if objective == "combined":
            # Response + edge term (Knut's edge-detection ask): the response
            # residual is blind to blends between similar colours (an IT8's
            # columns step smoothly, so a vertical offset preserves the
            # monotone response), but a sample box that straddles a patch
            # border picks up the border's own spread. Both terms live in
            # range-normalised units, so they add meaningfully.
            r_res, per_r = page_score_response(pos_i)
            r_uni, per_u = page_score_uniformity(pos_i)
            if r_res is None or r_uni is None:
                return None, None
            per = {n: per_r.get(n, 0.0) + per_u.get(n, 0.0)
                   for n in set(per_r) | set(per_u)}
            return r_res + r_uni, per
        if objective == "uniformity":
            return page_score_uniformity(pos_i)
        return page_score_response(pos_i)

    def page_score_uniformity(pos_i: int):
            # Reference-free (printer mode): a centred sample box sits on
            # flat colour; an offset box straddles patch edges and its
            # internal spread jumps. Score = 90th-percentile per-box std.
            per = {n: d[pos_i] / lum_range for n, d in spreads.items()
                   if d[pos_i] is not None}
            if len(per) < 16:
                return None, None
            res = sorted(per.values())
            return res[min(len(res) - 1, int(len(res) * 0.90))], per

    def page_score_response(pos_i: int):
        named_now = [(n, v[pos_i]) for n, v in reads.items()
                     if v[pos_i] is not None]
        if len(named_now) < 16:
            return None, None
        first = expected_by_id[named_now[0][0]]
        if isinstance(first, (tuple, list)):
            # Reference gives full XYZ: model the scan's luminance as a
            # LINEAR mix of X, Y, Z (broadband scanner channels are close
            # to linear in XYZ). A monotone-in-Y model breaks on strongly
            # saturated targets — Knut's LaserSoft scored its aligned grid
            # at 0 % because scan lum vs reference Y simply isn't monotone
            # there.
            import numpy as np
            a = np.array([[*expected_by_id[n], 1.0] for n, _r in named_now])
            yv = np.array([r for _n, r in named_now])
            coef, *_ = np.linalg.lstsq(a, yv, rcond=None)
            pred = a @ coef
            rng = (float(yv.max() - yv.min())) or 1.0
            resid = np.abs(yv - pred) / rng
            per = {n: float(rr) for (n, _r), rr in zip(named_now, resid)}
            res = sorted(per.values())
            return res[min(len(res) - 1, int(len(res) * 0.95))], per
        pairs = sorted((expected_by_id[n], r) for n, r in named_now)
        ys = [r for _e, r in pairs]
        fit = _isotonic(ys)
        rng = (max(ys) - min(ys)) or 1.0
        res = sorted(abs(a - b) / rng for a, b in zip(ys, fit))
        per = {n: abs(v[pos_i] - fit[bisect.bisect_left(
                   [e for e, _r in pairs], expected_by_id[n],
                   0, len(fit) - 1)]) / rng
               for n, v in reads.items() if v[pos_i] is not None}
        return res[min(len(res) - 1, int(len(res) * 0.95))], per

    lums = [v for vv in reads.values() for v in vv if v is not None]
    lum_range = (max(lums) - min(lums)) or 1.0 if lums else 1.0

    # Self-gate for the response lens: on some targets (LaserSoft's
    # saturated dyes) the scan's luminance simply cannot be predicted from
    # the reference — even scanin's own correctly-placed read ranks against
    # the reference at only ρ≈0.5 there (a Wolf Faust reads ≈0.95). A lens
    # whose model can't explain the data even when perfectly placed has no
    # business voting, so the response objective steps aside and the edge
    # lens rules alone.
    if objective in ("response", "combined"):
        pairs0 = [(expected_by_id[n], v[0]) for n, v in reads.items()
                  if v[0] is not None]
        if len(pairs0) >= 16:
            def _y(e):
                return e[1] if isinstance(e, (tuple, list)) else e
            ids0 = list(range(len(pairs0)))
            by_e = sorted(ids0, key=lambda i: _y(pairs0[i][0]))
            by_r = sorted(ids0, key=lambda i: pairs0[i][1])
            re_ = {i: k for k, i in enumerate(by_e)}
            rr_ = {i: k for k, i in enumerate(by_r)}
            n0 = len(ids0)
            rho0 = 1 - 6 * sum((re_[i] - rr_[i]) ** 2 for i in ids0) / (
                n0 * (n0 * n0 - 1))
            if rho0 < 0.8:
                return None

    s_user, per_user = page_score(0)
    if s_user is None:
        return None
    scores: list[tuple[float, int]] = [(s_user, 0)]
    per_by_pos: dict[int, dict[str, float]] = {0: per_user}
    for i in range(1, len(offsets)):
        sc_i, per_i = page_score(i)
        if sc_i is not None:
            scores.append((sc_i, i))
            per_by_pos[i] = per_i
    if len(scores) < 9:
        return None
    s_best = min(scores)[0]

    # Knut's per-patch normalisation (#119): every patch is ranked on its OWN
    # ladder, so the page's numbers are honest statistics over real per-patch
    # agreements — worst = the single worst patch, average = the arithmetic
    # mean of all patches. (The earlier build pooled the residuals into one
    # page score before normalising; its "average" saturated at 100 % while
    # the worst still moved, which is impossible for a min-vs-mean pair.)
    #
    # Per patch: the FLOOR (100 %) is its best residual anywhere on the
    # ladder. Each of the 8 directions contributes its own worst residual; a
    # direction that never worsens beyond the noise floor found no worst case
    # and is IGNORED (Knut: on a 24×5 % ladder the circle reaches the
    # neighbours, but a direction along a same-colour run may stay flat). The
    # ROOF (0 %) is the LEAST of the remaining direction-worsts, lowered by a
    # small buffer so the roof is actually reachable in the direction that
    # set it (Knut: a percent or two of the floor-to-roof scale). The patch's
    # own position then ranks linearly between floor and roof, clamped.
    # Because each patch is normalised against itself, any per-patch offset
    # the page-wide response model can't express cancels out.
    npos = len(offsets)
    ladders: dict[str, list[float | None]] = {
        n: [None] * npos for n in per_user}
    for i, per_i in per_by_pos.items():
        for n, r in per_i.items():
            if n in ladders:
                ladders[n][i] = r

    # The FLOOR is the best residual within a small LOCAL radius, not the
    # whole ladder. The ladder reaches 120 % of a pitch, so its global best
    # can sit INSIDE a neighbouring patch (a shifted box on the neighbour's
    # flat colour reads as "uniform" as home) or wherever a blend happens to
    # flatter the response model — both would put the floor below anything
    # the correctly-placed box can achieve, and every patch with a bit of
    # grain would rank poorly. Within ±10 % the box still samples its own
    # patch, so the local best is the honest "this is what clean looks like
    # for THIS patch" reference (and a printed spec, which stays in the box
    # over so small a shift, cancels instead of condemning the patch).
    LOCAL_FLOOR_FRAC = 0.10
    # A direction has FOUND a worst case only when its worst residual stands
    # clearly above the patch's floor — a border crossing, not grain drift
    # (Knut, #119: directions where the reading never worsens beyond the
    # noise floor are ignored, and the roof must come from the directions
    # that did find one, never collapse to the floor). Calibrated on Knut's
    # real 600 dpi LaserSoft scan: crossing a patch border lifts the
    # range-normalised residual well past this; grain, specs and same-colour
    # drift stay below it (his aligned grid reads worst ≈ 90 %, his 2 %
    # pulled corner ≈ 5 % — the gate buys aligned margin without costing the
    # pulled-corner detection).
    EDGE_GATE = 0.08
    ROOF_BUFFER = 0.02  # roof lowered by 2 % of (roof − floor), Knut

    k_local = max(1, int(round(LOCAL_FLOOR_FRAC / step_frac)))
    per_patch: dict[str, float] = {}
    roof_dir: dict[str, int | None] = {}
    ignored_dirs: dict[str, set[int]] = {}
    for n, lad in ladders.items():
        user = lad[0]
        if user is None:
            continue
        local = [user] + [lad[1 + d * steps + k]
                          for d in range(8) for k in range(k_local)
                          if 1 + d * steps + k < npos
                          and lad[1 + d * steps + k] is not None]
        floor_n = min(local)
        # The patch's own noise floor (Knut, #119): the MEDIAN residual
        # change across the local ring, where the box still samples the very
        # same colour — pure grain/spec wobble, not placement. Sitting above
        # the local floor by no more than this is indistinguishable from
        # perfect placement, so it must not be scored (the floor is a MIN
        # over ~17 noisy readings and sits below any single reading by about
        # this much even on a flat ladder). The median is what keeps real
        # detection intact: a border ramp inflates only the few ring
        # positions pointing at the border, which a median over all of them
        # ignores, while a printed spec (Knut's Q16) wobbles most of the
        # ring and is correctly written off as noise.
        diffs = sorted(abs(v - user) for v in local[1:])
        noise_n = diffs[len(diffs) // 2] if diffs else 0.0
        # The user's position is read as the MEDIAN of itself and its eight
        # single-step neighbours. A misplaced box stays elevated across that
        # whole ±5 % ring (the border overlap barely changes over one step),
        # so detection is untouched — but a box whose edge happens to sit
        # exactly ON a printed spec is a sharp one-position peak (Knut's
        # Q16: every neighbour reads better), and the median reads through
        # it instead of condemning the patch.
        ring1 = [lad[1 + d * steps] for d in range(8)
                 if 1 + d * steps < npos and lad[1 + d * steps] is not None]
        near = sorted([user] + ring1)
        user_eff = near[len(near) // 2]
        # A placement error must be walkable downhill: if the box really
        # straddles a border, stepping 5 % towards home already reads
        # better, and the local floor sits at roughly twice that one-step
        # descent. A patch that reads FLAT in every ±5 % direction but dips
        # somewhere at ±10 % is sitting on its own printed structure (the
        # box holds a spec cluster it can only shed two steps out — Knut's
        # Q16 at a 70 % sample area), so the dip must not be scored as
        # misplacement.
        descent = (max(0.0, user_eff - min(ring1)) if ring1
                   else float("inf"))
        dir_worsts: list[tuple[float, int]] = []
        ignored: set[int] = set()
        for d in range(8):
            lo = 1 + d * steps
            vals_d = [lad[i] for i in range(lo, min(lo + steps, npos))
                      if lad[i] is not None]
            if not vals_d or max(vals_d) - floor_n <= EDGE_GATE:
                ignored.add(d)
                continue
            dir_worsts.append((max(vals_d), d))
        ignored_dirs[n] = ignored
        if not dir_worsts:
            # No direction found a worst case: nothing this patch could be
            # misplaced against is visible from here — it cannot be ranked
            # and reads clean (Knut, #119).
            per_patch[n] = 100.0
            roof_dir[n] = None
            continue
        w0, d0 = min(dir_worsts)
        roof_dir[n] = d0
        roof = w0 - ROOF_BUFFER * (w0 - floor_n)
        if roof - floor_n < 1e-12:
            per_patch[n] = 100.0
            continue
        excess = max(0.0, min(user_eff - floor_n, 2.0 * descent) - noise_n)
        per_patch[n] = max(0.0, min(100.0,
                           100.0 * (1.0 - excess / (roof - floor_n))))

    if not per_patch:
        return None
    agree = min(per_patch.values())
    agree_avg = sum(per_patch.values()) / len(per_patch)
    offenders = sorted(per_patch.items(), key=lambda t: t[1])
    rep = PlacementReport(agree, per_patch, offenders, s_user=s_user,
                          s_best=s_best, s_floor=None,
                          average_pct=agree_avg)
    # Diagnostics for the per-direction verification tests (Knut, #119):
    # which direction set each patch's roof, and which directions were
    # ignored for finding no worst case.
    rep.roof_dir_by_patch = roof_dir
    rep.ignored_dirs_by_patch = ignored_dirs
    # Flank detection, Knut's derivative design (#108): a patch border is a
    # LINE of high spatial gradient. Each sample box (at the user's
    # position) is split into a fine sub-grid and each sub-cell records its
    # PEAK gradient magnitude — peak, not mean, so the line's strength is
    # never averaged away. A sub-cell counts when its peak stands above the
    # page's own grain floor (print grain and scanner noise raise every
    # cell equally, an edge only the cells it crosses). A box is ON an edge
    # when ``flank_min_cells`` or more interconnected sub-cells are hot: a
    # border line crossing a box necessarily runs through several touching
    # sub-cells, while dust or a noise spike lights only one or two. This
    # sees the border the moment the line enters the box — even a 1–2 %
    # overlap — where any area-mean measure dilutes to nothing.
    # CENTRED derivative, two scales. Centred matters: a one-sided diff
    # assigns the edge's gradient to the right/bottom pixel of each pair,
    # shifting the whole "edge line" by the stride — boxes crossing an edge
    # leftward contained it, boxes crossing rightward didn't until much
    # deeper (Knut's beta.140 test: only the left direction triggered).
    # Two spans (4 px and 8 px) cover real transitions — ~3–4 px at
    # 300 dpi, ~5–8 px at 600 dpi and on the demo renders — so the step
    # height is recovered whatever the scan's blur, without smearing.
    def _cgrad(plane, k):
        g = np.zeros_like(plane)
        g[:, k:-k] = np.abs(plane[:, 2 * k:] - plane[:, :-2 * k])
        gy = np.abs(plane[2 * k:, :] - plane[:-2 * k, :])
        g[k:-k, :] = np.maximum(g[k:-k, :], gy)
        return g

    grad = None
    for plane in [lum] + chroma:
        g = np.maximum(_cgrad(plane, 2), _cgrad(plane, 4))
        grad = g if grad is None else np.maximum(grad, g)

    # The user's marquee quad in the (possibly downscaled) sampling space.
    # Ring cells OUTSIDE it look past the patch area — at the chart's own
    # frame, labels and margins, which are expected structure, not a
    # misplacement (aligned LaserSoft/ISO grids false-flagged their whole
    # boundary rows without this). The quad is used EXACTLY — an earlier 2 %
    # expansion sounded harmless but amounted to a quarter of a pitch at the
    # quad edge, letting the boundary rows' ring see the frame line again at
    # a large sample area (Knut, #119). Only ring cells are quad-tested, so
    # nothing inside the box is ever clipped.
    _quad = [(c[0] * scale, c[1] * scale) for c in corners]

    def _in_quad(px_, py_):
        sign = 0
        for i in range(4):
            ax, ay = _quad[i]
            bx, by = _quad[(i + 1) % 4]
            cr = (bx - ax) * (py_ - ay) - (by - ay) * (px_ - ax)
            if cr != 0:
                if sign == 0:
                    sign = 1 if cr > 0 else -1
                elif (cr > 0) != (sign > 0):
                    return False
        return True

    # Sub-grid size, decided once for the page (Knut's #119 activation-box
    # design): a G×G grid tiles FLANK_GRID_COVER of the full patch. G = 20;
    # when the scan is too small for that (sub-pixel cells), fall back to
    # 10 — coarser is better than blind.
    _sample_px = [min((x2 - x1) / 20.0, (y2 - y1) / 20.0)
                  for (x1, y1, x2, y2) in
                  (flank_ixs[b.name][0] for b in named
                   if flank_ixs[b.name][0] is not None)]
    _med_cell = (sorted(_sample_px)[len(_sample_px) // 2]
                 if _sample_px else 0.0)
    G = 20 if _med_cell >= 1.2 else 10

    def cell_peaks(ix):
        """Peak gradient per cell of the G×G grid tiling *ix* with FULL
        coverage (float edges — integer cells cropped up to 8 px at the
        right/bottom, so edges entering from those sides went unseen until
        much deeper: Knut's beta.142 asymmetry). Cells whose centre falls
        outside the user's marquee (possible when a boundary patch's box is
        probed at a nearby position) would see the chart's frame — expected
        structure — and read 0. Returns G*G values, row-major."""
        xa, ya, xb, yb = ix
        cw, ch = (xb - xa) / float(G), (yb - ya) / float(G)
        if cw < 1 or ch < 1:
            return None
        xs = [int(round(xa + i * cw)) for i in range(G + 1)]
        ys = [int(round(ya + j * ch)) for j in range(G + 1)]
        quad_safe = all(_in_quad(x_, y_) for x_, y_ in
                        ((xa, ya), (xb, ya), (xb, yb), (xa, yb)))
        H, W = grad.shape
        vals = []
        for j in range(G):
            for i in range(G):
                ax, bx = max(0, xs[i]), min(W, xs[i + 1])
                ay, by = max(0, ys[j]), min(H, ys[j + 1])
                if bx - ax < 1 or by - ay < 1:
                    vals.append(0.0)
                    continue
                if not quad_safe and \
                        not _in_quad((ax + bx) / 2.0, (ay + by) / 2.0):
                    vals.append(0.0)
                    continue
                vals.append(float(grad[ay:by, ax:bx].max()))
        return vals

    # Activation (Knut's #119 design): only the sub-cells that the SAMPLE
    # box reaches take part in edge detection — the "activation box" is the
    # real (equal-margin) read zone plus half a sub-cell on every side,
    # centred on the patch, and a sub-cell is active when that box touches
    # it. So detection follows the sample area's rim: a small sample box
    # only wakes the middle of the grid, an 80 % one reaches the outermost
    # cells, and at every setting the warning fires when a border comes
    # close to what is actually being READ — instead of at one fixed
    # sensing size for all settings. Per patch, because each patch shape
    # gets its own margin.
    from workflow.scanin_runner import sample_margin
    active_by_patch: dict[str, list[bool]] = {}
    span_by_patch: dict[str, int] = {}
    for b in named:
        w, h = b.x2 - b.x1, b.y2 - b.y1
        mg = sample_margin(w, h, sample_frac)
        axes = []
        for full in (w, h):
            cell = FLANK_GRID_COVER * full / G
            g0 = -FLANK_GRID_COVER * full / 2.0
            # The activation reach is CAPPED at the calibrated safe zone:
            # on a contiguous chart the borders' blur-and-distortion tails
            # reach a good way into each patch (measured on Knut's real
            # 600 dpi LaserSoft: cells beyond ≈ 55 % of the patch per side
            # read "edge" on a perfectly aligned grid — 13 flagged patches
            # at a 40 % sample area, 160+ at 60 %, with uncapped
            # activation). Below the cap the activation follows the sample
            # box's rim exactly as designed; a large sample area senses at
            # the cap, and the placement agreement — which always measures
            # the full area — covers what lies beyond.
            a_half = min((full / 2.0 - mg) + cell / 2.0,
                         FLANK_REACH_MAX * full / 2.0)
            axes.append([(g0 + i * cell) < a_half
                         and (g0 + (i + 1) * cell) > -a_half
                         for i in range(G)])
        ax_, ay_ = axes
        active_by_patch[b.name] = [ax_[i] and ay_[j]
                                   for j in range(G) for i in range(G)]
        span_by_patch[b.name] = min(sum(ax_), sum(ay_))

    # Grain floor from the user's own boxes — ACTIVE cells only: print
    # grain and scanner noise raise every cell; an edge only the cells it
    # crosses; inactive cells are not read at all.
    user_cells: dict[str, list[float]] = {}
    all_peaks: list[float] = []
    for b in named:
        ix = flank_ixs[b.name][0]
        if ix is None:
            continue
        c = cell_peaks(ix)
        if c is None:
            continue
        user_cells[b.name] = [float(v) for v in c]
        mask = active_by_patch[b.name]
        all_peaks.extend(v for k, v in enumerate(user_cells[b.name])
                         if mask[k])
    fbp: dict[str, float] = {}
    if all_peaks:
        gfloor = float(np.percentile(all_peaks, 75))
        thr = gfloor + 0.5 * (float(np.percentile(all_peaks, 99)) - gfloor)
        # Ring of ±20 % positions for the clean-nearby test. The radius is a
        # PHYSICAL 20 % of the patch pitch, so the ladder rung has to be
        # derived from step_frac — hard-coding rung 2 silently turned the ring
        # into ±10 % at a 24×5 % ladder and ±4 % at 60×2 %, and a ring that
        # close to the box never clears a border (Knut, #119).
        k_ring = min(steps, max(1, int(round(0.20 / step_frac))))
        ring_ix = [1 + d * steps + (k_ring - 1) for d in range(8)]

        def hot_count(vals, mask) -> int:
            return int(sum(1 for v, a in zip(vals, mask) if a and v > thr))

        # "Clean" for the clean-nearby test means AS CLEAN AS THIS PAGE GETS,
        # not literally noise-free: on a noisy scan the sensor speckle alone
        # lights a few scattered cells in every box, so a fixed "at most one
        # hot cell" bar was unreachable and NO patch could ever flag — on the
        # bundled demo scans (1.5 % sensor noise, matching Knut's real Epson)
        # every box "on an edge" was silently discarded and the warning only
        # appeared once boxes crossed far enough for some quirk to slip
        # through (Knut, #119: detection 12–15 % late on the demo targets).
        # The bar is the page's median hot-cell count, so quiet real scans
        # keep the strict ≤1 and noisy pages get their own baseline.
        _counts = sorted(hot_count(v, active_by_patch[n])
                         for n, v in user_cells.items())
        clean_bar = max(1, _counts[len(_counts) // 2] if _counts else 1)

        need = max(2, int(flank_min_cells))

        def _line_like(vals, mask, need_len) -> bool:
            """Knut's side-by-side rule (#119): a border line crossing the
            active window runs through ``flank_min_cells``+ INTERCONNECTED
            sub-cells — each hugging the next on one of its four sides —
            while dust specks scatter and don't connect. The interconnected
            cells must form a straight contiguous run (a border is a line,
            near-parallel to a box side since the grid is aligned to the
            chart); inactive cells break a run. Any separate run that
            reaches the length flags the patch, so grain elsewhere in the
            window can't mask a real edge."""
            hot = [a and v > thr for v, a in zip(vals, mask)]
            if sum(hot) < need_len:
                return False
            for j in range(G):                    # horizontal runs
                run = 0
                for i in range(G):
                    run = run + 1 if hot[j * G + i] else 0
                    if run >= need_len:
                        return True
            for i in range(G):                    # vertical runs
                run = 0
                for j in range(G):
                    run = run + 1 if hot[j * G + i] else 0
                    if run >= need_len:
                        return True
            return False

        for n, vals in user_cells.items():
            mask = active_by_patch[n]
            # The required run never exceeds what the active window can
            # hold (a 20 % sample area only wakes ~half the grid), with a
            # floor of 3 — Knut's original line-vs-dust minimum.
            need_len = max(3, min(need, span_by_patch[n]))
            if not _line_like(vals, mask, need_len):
                # dust and noise spikes light scattered cells; only a
                # connected run of hot cells is an edge (Knut).
                fbp[n] = 0.0
                continue
            # Clean-nearby test: a PLACEMENT-caused edge leaves the box at
            # some ±20 % shift; structure inside the patch itself (LaserSoft
            # bars, wedges) stays hot everywhere and must not count.
            clean_nearby = False
            for ri in ring_ix:
                ix = flank_ixs[n][ri] if ri < len(flank_ixs[n]) else None
                if ix is None:
                    continue
                c = cell_peaks(ix)
                if c is not None and hot_count(c, mask) <= clean_bar:
                    clean_nearby = True
                    break
            if not clean_nearby:
                fbp[n] = 0.0
                continue
            hot = sorted(((v - gfloor) / lum_range
                          for v, a in zip(vals, mask) if a), reverse=True)
            fbp[n] = max(0.0, hot[min(3, len(hot)) - 1])
    rep.flank_by_patch = fbp
    return rep