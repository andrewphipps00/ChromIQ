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
expected value through a monotone response map (fitted page-wide, so an
unprofiled scanner's response and a printer's gamut compression cancel out).
A position's page score is the 95th-percentile |residual| over the patches:
worst-rules, with enough immunity that one dust speck can't condemn a page.
The ladder's best position is 100 %, the least-worst of the eight directions
is 0 %, and the grid's own position ranks between them.

So the verdict is driven by the page's WORST patches — no averaging (Knut) —
and those patches are named. ``average_pct`` re-runs the same normalisation
on the MEAN residual, purely to show alongside the verdict: it tells the user
whether a few patches are off or the whole grid is. Individual patches cannot
be ranked against each other this way — a patch's residual measures how well
the page's response model fits its COLOUR, not how well its box is placed.
"""
from __future__ import annotations

import math
from pathlib import Path

__all__ = ["PlacementReport", "dense_placement_agreement"]


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
        # Same ladder scale as agreement_pct, but driven by the page's MEAN
        # patch instead of its worst (Knut, #119) — shown alongside the
        # verdict, never used for it.
        self.average_pct = (agreement_pct if average_pct is None
                            else average_pct)
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
        objective: str = "response",
        src_quad: list[tuple[float, float]] | None = None) -> PlacementReport | None:
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
        u = sorted(set(round(v, 3) for v in vals))
        gaps = [b - a for a, b in zip(u, u[1:]) if b - a > 1e-3]
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
    # Knut's normalisation, tightened per his beta.136 analysis: each of the
    # 8 directions has its own worst value; the LEAST worst of those eight
    # is the 0 % end. Directions with worse values then fall towards (and
    # past) 0 % more easily, and the scale no longer depends on guessing
    # which octant the grid sits in.
    def _rank(by_pos: list[tuple[float, int]]) -> tuple[float, float | None]:
        """Knut's normalisation of the grid's own position (index 0) against a
        per-position score; also returns the 0 %-end score. Tightened per his
        beta.136 analysis: each of the 8 directions has its own worst value;
        the LEAST worst of those eight is the 0 % end. Directions with worse
        values then fall towards (and past) 0 % more easily, and the scale no
        longer depends on guessing which octant the grid sits in."""
        dir_worst = []
        for d in range(8):
            lo = 1 + d * steps
            vals_d = [v for v, i in by_pos if lo <= i < lo + steps]
            if vals_d:
                dir_worst.append(max(vals_d))
        if not dir_worst:
            return 100.0, None
        w0 = min(dir_worst)
        best = min(v for v, _i in by_pos)
        user = next(v for v, i in by_pos if i == 0)
        if w0 - best < 1e-9:
            return 100.0, w0
        return max(0.0, min(100.0, 100.0 * (w0 - user) / (w0 - best))), w0

    agree, s_floor = _rank(scores)

    # The page's average agreement, on the SAME ladder as the worst verdict
    # (Knut, #119). The verdict above is driven by the page's worst patches (its
    # score is the 95th-percentile residual, worst-rules with dust immunity);
    # "average" is the user grid's MEAN residual mapped through the IDENTICAL
    # (floor, best) scale. One shared ladder is what makes the pair comparable —
    # an earlier build normalised the mean on its OWN ladder, whose (floor, best)
    # differ, so "worst" could read ABOVE "average", which is impossible for a
    # min-vs-mean pair (Knut saw "worst 97.86 %, average 90.03 %"). Because the
    # mean residual ≤ the 95th percentile, mapping both through the same
    # decreasing scale gives average ≥ worst; a final max() guards the rare
    # skewed page where the mean is pulled above the 95th percentile.
    user_mean = sum(per_user.values()) / len(per_user)
    if s_floor is None or (s_floor - s_best) < 1e-9:
        agree_avg = agree
    else:
        raw_avg = 100.0 * (s_floor - user_mean) / (s_floor - s_best)
        agree_avg = max(agree, max(0.0, min(100.0, raw_avg)))

    # Per-patch report at the user's position: how far each patch reads from
    # the page's own response, as a share of the worst patch (100 = clean).
    # Used to NAME the offenders, not to rank placements (see above).
    worst_res = max(per_user.values()) or 1.0
    per_patch = {n: 100.0 * (1.0 - r / worst_res) for n, r in per_user.items()}
    offenders = sorted(per_user.items(), key=lambda t: -t[1])
    offenders = [(n, per_patch[n]) for n, _r in offenders]
    rep = PlacementReport(agree, per_patch, offenders, s_user=s_user,
                          s_best=s_best, s_floor=s_floor,
                          average_pct=agree_avg)
    # Flank detection, Knut's derivative design (#108): a patch border is a
    # LINE of high spatial gradient. Each sample box (at the user's
    # position) is split into a 9×9 sub-grid and each sub-cell records its
    # PEAK gradient magnitude — peak, not mean, so the line's strength is
    # never averaged away. A sub-cell counts when its peak stands above the
    # page's own grain floor (the median sub-cell peak: print grain and
    # scanner noise raise every cell equally, an edge only the cells it
    # crosses). A box is ON an edge when three or more of its sub-cells
    # are hot: a border line crossing a box necessarily runs through
    # several sub-cells, while dust or a noise spike lights only one or
    # two. This sees the border the moment the line enters the box — even
    # a 1–2 % overlap — where any area-mean measure dilutes to nothing.
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
    # boundary rows without this). Slightly expanded so the quad edge itself
    # doesn't clip inner cells.
    _qx = [c[0] * scale for c in corners]
    _qy = [c[1] * scale for c in corners]
    _qcx, _qcy = sum(_qx) / 4.0, sum(_qy) / 4.0
    _quad = [(_qcx + (x - _qcx) * 1.02, _qcy + (y - _qcy) * 1.02)
             for x, y in zip(_qx, _qy)]

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

    def cell_peaks(ix):
        """Peak gradient per cell of an 11×11 grid: the inner 9×9 tiles the
        sample box with FULL coverage (float edges — the old integer cells
        cropped up to 8 px at the right/bottom, so edges entering from those
        sides went unseen until much deeper: Knut's beta.142 asymmetry), and
        the outer ring of one cell width sits OUTSIDE the box, detecting a
        patch border while the box is still APPROACHING it (Knut's 11×11
        design). Returns 121 values, row-major."""
        xa, ya, xb, yb = ix
        cw, ch = (xb - xa) / 9.0, (yb - ya) / 9.0
        if cw < 1 or ch < 1:
            return None
        x0, y0 = xa - cw, ya - ch
        xs = [int(round(x0 + i * cw)) for i in range(12)]
        ys = [int(round(y0 + j * ch)) for j in range(12)]
        H, W = grad.shape
        vals = []
        for j in range(11):
            for i in range(11):
                ax, bx = max(0, xs[i]), min(W, xs[i + 1])
                ay, by = max(0, ys[j]), min(H, ys[j + 1])
                if bx - ax < 1 or by - ay < 1:
                    vals.append(0.0)
                    continue
                if (i == 0 or i == 10 or j == 0 or j == 10) and \
                        not _in_quad((ax + bx) / 2.0, (ay + by) / 2.0):
                    vals.append(0.0)
                    continue
                vals.append(float(grad[ay:by, ax:bx].max()))
        return vals

    # Grain floor from the user's own boxes: print grain and scanner noise
    # raise every sub-cell; an edge only the cells it crosses.
    user_cells: dict[str, list[float]] = {}
    all_peaks: list[float] = []
    for b in named:
        ix = box_ixs[b.name][0]
        if ix is None:
            continue
        c = cell_peaks(ix)
        if c is None:
            continue
        user_cells[b.name] = [float(v) for v in c]
        # Grain floor from the INNER 9×9 only: the outer ring's whole job is
        # to sit near borders, so its peaks are edge signal, not grain —
        # letting them into the floor inflates the threshold until real
        # edges stop registering.
        all_peaks.extend(v for k, v in enumerate(user_cells[b.name])
                         if 1 <= k // 11 <= 9 and 1 <= k % 11 <= 9)
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

        def hot_count(vals) -> int:
            return int(sum(1 for v in vals if v > thr))

        def _line_like(vals) -> bool:
            """Knut's side-by-side rule: a border line crossing the box runs
            through 3+ ADJACENT sub-cells; dust specks scatter — even several
            of them don't connect. Largest 8-connected component of hot
            cells must reach 3."""
            hot9 = [i for i, v in enumerate(vals) if v > thr]
            if len(hot9) < 3:
                return False
            cells = {(i // 11, i % 11) for i in hot9}
            seen: set = set()
            for start in cells:
                if start in seen:
                    continue
                comp, stack = 0, [start]
                seen.add(start)
                while stack:
                    r, c = stack.pop()
                    comp += 1
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            nb = (r + dr, c + dc)
                            if nb in cells and nb not in seen:
                                seen.add(nb)
                                stack.append(nb)
                if comp >= 3:
                    return True
            return False

        for n, vals in user_cells.items():
            if not _line_like(vals):
                # dust and noise spikes light scattered cells; only a
                # connected run of hot cells is an edge (Knut).
                fbp[n] = 0.0
                continue
            # Clean-nearby test: a PLACEMENT-caused edge leaves the box at
            # some ±20 % shift; structure inside the patch itself (LaserSoft
            # bars, wedges) stays hot everywhere and must not count.
            clean_nearby = False
            for ri in ring_ix:
                ix = box_ixs[n][ri] if ri < len(box_ixs[n]) else None
                if ix is None:
                    continue
                c = cell_peaks(ix)
                if c is not None and hot_count(c) <= 1:
                    clean_nearby = True
                    break
            if not clean_nearby:
                fbp[n] = 0.0
                continue
            hot = sorted(((v - gfloor) / lum_range for v in vals),
                         reverse=True)
            fbp[n] = max(0.0, hot[2])
    rep.flank_by_patch = fbp
    return rep