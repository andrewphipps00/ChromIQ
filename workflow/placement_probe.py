"""Dense placement evaluation for the scanner tool's Check-alignment button
(Knut, #108 round 12).

Knut's design, adopted: instead of re-running scanin at a handful of probe
positions, measure the scan densely ONCE and evaluate his full step ladder —
12 steps of 10 % of a patch pitch in 8 directions — as pure lookups. His
proposal reached the dense measurements by generating a high-density .cht and
sending it through scanin; this implementation samples the image directly with
the same maths scanin uses for manually-placed grids (the -F homography and
per-box mean over the sample area), which produces the same numbers without
the detour, so the whole ladder costs milliseconds instead of minutes.

Per patch (his ranking): every candidate position's sampled colour is compared
with the patch's expected value through a monotone response map (fitted
page-wide, so an unprofiled scanner's response and a printer's gamut
compression cancel out; because the ranking is per patch, any per-patch offset
the response map can't express cancels too). The closest position within the
1.5-patch search circumference is the baseline (100 %), the worst position in
the octant the grid sits in from the baseline is 0 %, and the grid's own
position ranks between them. The page's agreement is the WORST patch — no
averaging (Knut) — and the patches below the floor are named.
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
                 s_floor: float | None = None) -> None:
        self.agreement_pct = agreement_pct
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
        steps: int = 12,
        step_frac: float = 0.10,
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

    # Knut's ladder: 12 steps × 10 % in 8 directions (+ the centre).
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
    for i in range(1, len(offsets)):
        sc_i, _ = page_score(i)
        if sc_i is not None:
            scores.append((sc_i, i))
    if len(scores) < 9:
        return None
    s_best, i_best = min(scores)
    # Knut's normalisation, tightened per his beta.136 analysis: each of the
    # 8 directions has its own worst value; the LEAST worst of those eight
    # is the 0 % end. Directions with worse values then fall towards (and
    # past) 0 % more easily, and the scale no longer depends on guessing
    # which octant the grid sits in.
    per_dir_worst: list[float] = []
    for d in range(8):
        lo = 1 + d * steps
        vals_d = [sc_i for sc_i, i in scores if lo <= i < lo + steps]
        if vals_d:
            per_dir_worst.append(max(vals_d))
    if not per_dir_worst:
        agree = 100.0
    else:
        w0 = min(per_dir_worst)
        agree = (100.0 if w0 - s_best < 1e-9
                 else 100.0 * (w0 - s_user) / (w0 - s_best))
        agree = max(0.0, min(100.0, agree))
    # Per-patch report at the user's position: how far each patch reads from
    # the page's own response, as a share of the worst patch (100 = clean).
    worst_res = max(per_user.values()) or 1.0
    per_patch = {n: 100.0 * (1.0 - r / worst_res) for n, r in per_user.items()}
    offenders = sorted(per_user.items(), key=lambda t: -t[1])
    offenders = [(n, per_patch[n]) for n, _r in offenders]
    rep = PlacementReport(agree, per_patch, offenders, s_user=s_user,
                          s_best=s_best,
                          s_floor=min(per_dir_worst) if per_dir_worst else None)
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
    # Derivative over a 3-pixel stride, not adjacent pixels: scan optics
    # spread a border step over several pixels, and the adjacent-pixel
    # difference of a blurred edge is a fraction of the true step. A wider
    # baseline recovers most of the step height regardless of the blur.
    stride = 2
    grad = None
    for plane in [lum] + chroma:
        g = np.zeros_like(plane)
        g[:, stride:] = np.abs(plane[:, stride:] - plane[:, :-stride])
        gy = np.abs(plane[stride:, :] - plane[:-stride, :])
        g[stride:, :] = np.maximum(g[stride:, :], gy)
        grad = g if grad is None else np.maximum(grad, g)

    def cell_peaks(ix):
        xa, ya, xb, yb = ix
        w9, h9 = (xb - xa) // 9, (yb - ya) // 9
        if w9 < 1 or h9 < 1:
            return None
        sub = grad[ya:ya + h9 * 9, xa:xa + w9 * 9]
        return sub.reshape(9, h9, 9, w9).max(axis=(1, 3)).ravel()

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
        all_peaks.extend(user_cells[b.name])
    fbp: dict[str, float] = {}
    if all_peaks:
        gfloor = float(np.percentile(all_peaks, 75))
        thr = gfloor + 0.5 * (float(np.percentile(all_peaks, 99)) - gfloor)
        # ring of ±20 % positions (ladder step k=2) for the clean-nearby test
        ring_ix = [1 + d * steps + 1 for d in range(8)]

        def hot_count(vals) -> int:
            return int(sum(1 for v in vals if v > thr))

        for n, vals in user_cells.items():
            if hot_count(vals) < 3:
                # fewer than 3 hot sub-cells: a crossing border line always
                # runs through several; one or two are dust or noise.
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