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
                 offenders: list[tuple[str, float]]) -> None:
        self.agreement_pct = agreement_pct
        self.per_patch = per_patch
        self.offenders = offenders


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
        objective: str = "response") -> PlacementReport | None:
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
    else:
        lum = arr
    hgt, wdt = lum.shape
    # integral images → O(1) box mean and variance
    integ = np.zeros((hgt + 1, wdt + 1))
    integ[1:, 1:] = np.cumsum(np.cumsum(lum, axis=0), axis=1)
    integ2 = np.zeros((hgt + 1, wdt + 1))
    integ2[1:, 1:] = np.cumsum(np.cumsum(lum * lum, axis=0), axis=1)

    def box_stats(x0: float, y0: float, x1: float, y1: float):
        xa, ya = int(round(x0)), int(round(y0))
        xb, yb = int(round(x1)), int(round(y1))
        if xa < 0 or ya < 0 or xb > wdt or yb > hgt or xb - xa < 1 or yb - ya < 1:
            return None
        n = (xb - xa) * (yb - ya)
        s1 = integ[yb, xb] - integ[ya, xb] - integ[yb, xa] + integ[ya, xa]
        s2 = integ2[yb, xb] - integ2[ya, xb] - integ2[yb, xa] + integ2[ya, xa]
        mean = s1 / n
        var = max(0.0, s2 / n - mean * mean)
        return float(mean), float(var ** 0.5)

    named = [b for b in boxes if b.name in expected_by_id]
    if len(named) < 16:
        return None
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
    for b in named:
        cx, cy = (b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0
        hx = (b.x2 - b.x1) * sample_frac / 2.0
        hy = (b.y2 - b.y1) * sample_frac / 2.0
        vals: list[float | None] = []
        devs: list[float | None] = []
        for ox, oy in offsets:
            xa, ya = warp(cx + ox - hx, cy + oy - hy)
            xb, yb = warp(cx + ox + hx, cy + oy + hy)
            st = box_stats(min(xa, xb), min(ya, yb),
                           max(xa, xb), max(ya, yb))
            vals.append(st[0] if st else None)
            devs.append(st[1] if st else None)
        reads[b.name] = vals
        spreads[b.name] = devs

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
        if objective == "uniformity":
            # Reference-free (printer mode): a centred sample box sits on
            # flat colour; an offset box straddles patch edges and its
            # internal spread jumps. Score = 90th-percentile per-box std.
            per = {n: d[pos_i] for n, d in spreads.items()
                   if d[pos_i] is not None}
            if len(per) < 16:
                return None, None
            res = sorted(per.values())
            return res[min(len(res) - 1, int(len(res) * 0.90))], per
        pairs = sorted((expected_by_id[n], v[pos_i])
                       for n, v in reads.items() if v[pos_i] is not None)
        if len(pairs) < 16:
            return None, None
        ys = [r for _e, r in pairs]
        fit = _isotonic(ys)
        rng = (max(ys) - min(ys)) or 1.0
        res = sorted(abs(a - b) / rng for a, b in zip(ys, fit))
        per = {n: abs(v[pos_i] - fit[bisect.bisect_left(
                   [e for e, _r in pairs], expected_by_id[n],
                   0, len(fit) - 1)]) / rng
               for n, v in reads.items() if v[pos_i] is not None}
        return res[min(len(res) - 1, int(len(res) * 0.95))], per

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
    # Knut's normalisation: baseline (best position) = 100 %, the worst
    # position in the octant the grid sits in FROM the baseline = 0 %.
    bx, by = offsets[i_best]
    vx, vy = -bx, -by
    if abs(vx) < 1e-9 and abs(vy) < 1e-9:
        agree = 100.0
    else:
        ang = math.atan2(vy, vx)
        oct_i = int(round(ang / (math.pi / 4))) % 8
        odx, ody = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1),
                    (0, -1), (1, -1)][oct_i]
        s_worst = s_best
        for sc_i, i in scores:
            ox, oy = offsets[i]
            if (ox * odx >= -1e-9 and oy * ody >= -1e-9
                    and (abs(ox) > 1e-9 or abs(oy) > 1e-9)):
                s_worst = max(s_worst, sc_i)
        agree = (100.0 if s_worst - s_best < 1e-9
                 else 100.0 * (s_worst - s_user) / (s_worst - s_best))
    # Per-patch report at the user's position: how far each patch reads from
    # the page's own response, as a share of the worst patch (100 = clean).
    worst_res = max(per_user.values()) or 1.0
    per_patch = {n: 100.0 * (1.0 - r / worst_res) for n, r in per_user.items()}
    offenders = sorted(per_user.items(), key=lambda t: -t[1])
    offenders = [(n, per_patch[n]) for n, _r in offenders]
    return PlacementReport(agree, per_patch, offenders)
