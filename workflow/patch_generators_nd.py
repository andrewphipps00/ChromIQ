"""N-channel (device-space) patch generators + spacing helpers (#72 Tier C).

The RGB generators in :mod:`workflow.patch_generators` stay textually
untouched (their behaviour is load-bearing for every RGB chart); this module
holds the multi-ink counterparts, all dimension-generic:

* **N-native sets** (work with no profile, state 2): per-ink ramps, ink-pair
  overprints, the device-centred near-neutral grey-balance rings (the G7-style
  construct — the RGB ``near_neutrals()`` output mapped by naive inversion),
  the device neutral ramp, and white/black anchors (ink white = all-0).
* **Spacing utilities** in N-D: dedupe / min-distance / gap-fill / counts, all
  using absolute Euclidean distance in device-% over every channel with the
  same 2.0 threshold as RGB (#72 appendix E: patch distinctness is a
  per-channel device-resolution question — 2 % of ink is 2 % of ink whether
  the device has 3 channels or 7; diagonal-relative scaling was rejected).

Channel convention: device tuples are 0..100 in the chart's canonical
colorant order — C, M, Y first and K fourth for every CMYK-based ink set
(guaranteed by ``ti2_relayout.color_rep_for_inks``); extra inks follow.

All maths follows the issue's worked appendix (rev 2.3) verbatim; the ring
clamp geometry and count identities are verified there against a real profile.
"""
from __future__ import annotations

import itertools
import math

from workflow.patch_generators import near_neutrals, neutral_ramp


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


# ---------------------------------------------------------------------------
# N-native sets (state 2 — no profile needed)
# ---------------------------------------------------------------------------

def per_ink_ramps(n_inks: int, steps: int) -> list[tuple[float, ...]]:
    """``steps`` tones of each ink alone: ``v_i = i·100/steps, i = 1…steps``.

    The 100 % endpoint is deliberately included; the cross-set dedupe absorbs
    overlap with white/black and the pair-ramp ends (#72 appendix F).
    """
    n_inks, steps = int(n_inks), int(steps)
    out: list[tuple[float, ...]] = []
    for ink in range(n_inks):
        for i in range(1, steps + 1):
            row = [0.0] * n_inks
            row[ink] = i * 100.0 / steps
            out.append(tuple(row))
    return out


def per_ink_ramps_count(n_inks: int, steps: int) -> int:
    return max(0, int(n_inks)) * max(0, int(steps))


def ink_pair_overprints(n_inks: int, steps: int,
                        ink_limit: float = 300.0) -> list[tuple[float, ...]]:
    """Two-ink overprint ramps for every ink pair: both inks at
    ``v_i = i·min(100, L/2)/steps`` — the ``L/2`` cap keeps every pair patch
    inside the ink limit *by construction* (no post-clamp; it only bites for
    limits under 200 %). ``C(n_inks, 2) × steps`` patches (#72 appendix F).
    """
    n_inks, steps = int(n_inks), int(steps)
    vmax = min(100.0, float(ink_limit) / 2.0)
    out: list[tuple[float, ...]] = []
    for a, b in itertools.combinations(range(n_inks), 2):
        for i in range(1, steps + 1):
            row = [0.0] * n_inks
            row[a] = row[b] = i * vmax / steps
            out.append(tuple(row))
    return out


def ink_pair_overprints_count(n_inks: int, steps: int) -> int:
    n = max(0, int(n_inks))
    return (n * (n - 1) // 2) * max(0, int(steps))


def _invert_rgb_to_cmy(rgb: tuple[float, float, float], n_channels: int,
                       ink_limit: float) -> tuple[float, ...]:
    """Naive inversion ``(C,M,Y) = (100−R, 100−G, 100−B)``, K/extras 0, then
    the ink-limit clamp that **shifts along the grey axis** (#72 appendix A):
    ``t = max(0, (C+M+Y−L)/3)`` subtracted from each channel. The shift vector
    is parallel to (1,1,1), so the perpendicular (ring) component — the whole
    point of the rings — is preserved exactly; uniform scaling would shrink
    it and was rejected analytically.
    """
    c, m, y = (100.0 - rgb[0], 100.0 - rgb[1], 100.0 - rgb[2])
    t = max(0.0, (c + m + y - float(ink_limit)) / 3.0)
    row = [0.0] * n_channels
    row[0], row[1], row[2] = c - t, m - t, y - t
    return tuple(row)


def near_neutrals_device(steps: int, offset: float, rings: int,
                         n_channels: int,
                         ink_limit: float = 300.0) -> list[tuple[float, ...]]:
    """Device-centred near-neutral grey-balance rings (state 2, #72).

    Runs the RGB :func:`near_neutrals` generator **unchanged** and maps each
    patch onto the equal-CMY axis by naive inversion — rings of CMY
    combinations around the device's grey balance, exactly the construct
    G7-style P2P charts use. No profile needed: the rings' job is to *bracket*
    the grey-balance region; the measurement finds the true neutral inside.
    K stays 0 (the K ramp is covered by per-ink ramps) and light inks stay 0
    (deliberate v1 simplification, noted in the issue).
    """
    if n_channels < 3:
        raise ValueError("near_neutrals_device needs a CMY-based ink set")
    return [_invert_rgb_to_cmy(p, n_channels, ink_limit)
            for p in near_neutrals(steps, offset, rings)]


# count identity verified in the issue (288 == 288): the adapter is 1:1, so
# the RGB count function stays authoritative — re-exported for symmetry.
from workflow.patch_generators import near_neutrals_count as near_neutrals_device_count  # noqa: E402,F401


def neutral_ramp_device(steps: int, n_channels: int,
                        ink_limit: float = 300.0) -> list[tuple[float, ...]]:
    """The pure grey ramp as equal-CMY device steps (naive inversion of the
    RGB :func:`neutral_ramp`, same grey-axis ink-limit clamp)."""
    if n_channels < 3:
        raise ValueError("neutral_ramp_device needs a CMY-based ink set")
    return [_invert_rgb_to_cmy(p, n_channels, ink_limit)
            for p in neutral_ramp(steps)]


from workflow.patch_generators import neutral_ramp_count as neutral_ramp_device_count  # noqa: E402,F401


def _device_anchors(n_channels: int, k_index: int | None,
                    ink_limit: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(white, black) anchor tuples: ink white = all zeros (bare paper);
    black = K alone at 100 (never composite, #72 appendix F). K-less ink
    sets fall back to equal CMY at ``min(100, limit/3)`` (appendix H)."""
    white = tuple([0.0] * n_channels)
    if k_index is not None and 0 <= k_index < n_channels:
        row = [0.0] * n_channels
        row[k_index] = 100.0
        return white, tuple(row)
    v = min(100.0, float(ink_limit) / 3.0)
    return white, tuple([v if i < 3 else 0.0 for i in range(n_channels)])


def white_black_device(count: int, n_channels: int, k_index: int | None = 3,
                       have_white: int = 0, have_black: int = 0,
                       ink_limit: float = 300.0) -> list[tuple[float, ...]]:
    """Paper-white and maximum-black anchors in ink space (#72 appendix F/H).

    Ink white = **all zeros** (bare paper); black = the K ink alone at 100
    (never composite in v1 — composite black is the ink-pair/coverage sets'
    job). ``k_index None`` (a K-less ink set, e.g. plain CMY) falls back to
    equal CMY inside the ink limit.
    """
    count = max(0, int(count))
    whites = max(0, count - max(0, int(have_white)))
    blacks = max(0, count - max(0, int(have_black)))
    white, black = _device_anchors(n_channels, k_index, ink_limit)
    return [white] * whites + [black] * blacks


def white_black_device_count(count: int = 1, have_white: int = 0,
                             have_black: int = 0) -> int:
    count = max(0, int(count))
    return (max(0, count - max(0, int(have_white)))
            + max(0, count - max(0, int(have_black))))


def count_white_black_device(patches, n_channels: int,
                             k_index: int | None = 3,
                             quantum: float = 0.5,
                             ink_limit: float = 300.0) -> tuple[int, int]:
    """``(white, black)`` anchors already present, on the dedupe grid —
    the device-space twin of ``count_white_black`` (white = all-0 ink)."""
    white, black = _device_anchors(n_channels, k_index, ink_limit)
    wk = _key(white, quantum)
    bk = _key(black, quantum)
    w = b = 0
    for p in patches:
        k = _key(p, quantum)
        if k == wk:
            w += 1
        elif k == bk:
            b += 1
    return w, b


# ---------------------------------------------------------------------------
# Spacing utilities in N-D (appendix E: absolute 2.0 device-% Euclidean)
# ---------------------------------------------------------------------------

def _key(p, quantum: float) -> tuple[int, ...]:
    return tuple(int(round(_clamp(c) / quantum)) for c in p)


def deduplicate_nd(patches, quantum: float = 0.5,
                   step: float = 1.0) -> list[tuple[float, ...]]:
    """N-D twin of ``patch_generators.deduplicate``: collisions on the
    ``quantum`` grid are nudged apart along rotating channels, order
    preserved, values clamped to 0..100."""
    seen: set[tuple[int, ...]] = set()
    out: list[tuple[float, ...]] = []
    for p in patches:
        q = [_clamp(v) for v in p]
        n = len(q)
        key = _key(q, quantum)
        tries = 0
        while key in seen and tries < 600:
            ch = tries % n
            delta = step * (1 + tries // n)
            base = q[ch]
            q[ch] = _clamp(base + delta if base + delta <= 100.0 else base - delta)
            key = _key(q, quantum)
            tries += 1
        seen.add(key)
        out.append(tuple(q))
    return out


def _min_d2_brute(q, kept) -> float:
    best = 1e18
    for k in kept:
        d2 = sum((a - b) ** 2 for a, b in zip(q, k))
        if d2 < best:
            best = d2
    return best


def _nudge_dirs(n: int) -> list[tuple[float, ...]]:
    """Deterministic search directions in N-D: ± each axis, then ± diagonals
    of every axis pair — 2n + 4·C(n,2) directions (the 3-D twin's 26-neighbour
    dart-search, generalised without the 3^n blow-up)."""
    dirs: list[tuple[float, ...]] = []
    for i in range(n):
        for s in (1.0, -1.0):
            d = [0.0] * n
            d[i] = s
            dirs.append(tuple(d))
    for i, j in itertools.combinations(range(n), 2):
        for si in (1.0, -1.0):
            for sj in (1.0, -1.0):
                d = [0.0] * n
                d[i], d[j] = si, sj
                dirs.append(tuple(d))
    return dirs


def enforce_min_distance_nd(patches, min_dist: float = 2.0, existing=None):
    """N-D twin of ``patch_generators.enforce_min_distance`` (order and count
    preserved; earlier points never disturbed). Brute-force neighbour test —
    the generator panel's programs are a few thousand patches, well within
    budget for the N-channel path."""
    if not patches:
        return []
    if min_dist <= 0:
        return [tuple(_clamp(v) for v in p) for p in patches]
    md2 = min_dist * min_dist
    kept: list[tuple[float, ...]] = [
        tuple(_clamp(v) for v in q) for q in (existing or [])]
    n = len(patches[0])
    dirs = _nudge_dirs(n)
    out: list[tuple[float, ...]] = []
    for p in patches:
        q = tuple(_clamp(v) for v in p)
        if _min_d2_brute(q, kept) >= md2:
            kept.append(q)
            out.append(q)
            continue
        best_q, best_d2, found = q, _min_d2_brute(q, kept), None
        for rmul in (1.0, 1.5, 2.0, 2.6):
            radius = min_dist * rmul
            for d in dirs:
                ln = math.sqrt(sum(c * c for c in d))
                cand = tuple(_clamp(q[i] + d[i] / ln * radius)
                             for i in range(n))
                d2 = _min_d2_brute(cand, kept)
                if d2 >= md2:
                    found = cand
                    break
                if d2 > best_d2:
                    best_d2, best_q = d2, cand
            if found:
                break
        qf = found if found is not None else best_q
        kept.append(qf)
        out.append(qf)
    return out


def count_too_close_nd(existing, new, min_dist: float = 2.0) -> int:
    """How many of ``new`` sit within ``min_dist`` of any ``existing`` point."""
    if min_dist <= 0 or not existing:
        return 0
    md2 = min_dist * min_dist
    kept = [tuple(_clamp(v) for v in q) for q in existing]
    return sum(1 for p in new
               if _min_d2_brute(tuple(_clamp(v) for v in p), kept) < md2)


def drop_too_close_nd(existing, new, min_dist: float = 2.0):
    """Only the ``new`` points at least ``min_dist`` clear of ``existing``."""
    if min_dist <= 0 or not existing:
        return [tuple(_clamp(v) for v in p) for p in new]
    md2 = min_dist * min_dist
    kept = [tuple(_clamp(v) for v in q) for q in existing]
    return [tuple(_clamp(v) for v in p) for p in new
            if _min_d2_brute(tuple(_clamp(v) for v in p), kept) >= md2]


def fill_gaps_nd(existing, total: int, n_channels: int | None = None,
                 candidates: int = 12, seed: int = 0,
                 relax: int = 4) -> list[tuple[float, ...]]:
    """N-D twin of ``patch_generators.fill_gaps`` (blue-noise seed + Lloyd
    relaxation in device space). Geometrically fine but perceptually blind in
    ink space — the issue recommends targen coverage for large N-channel
    fills; this is the small top-up fallback (e.g. the live Pages fill).
    """
    import numpy as np

    total = int(total)
    pts = [tuple(float(v) for v in p) for p in existing]
    n = n_channels or (len(pts[0]) if pts else 3)
    n_add = total - len(pts)
    if n_add <= 0:
        return []
    rng = np.random.default_rng(seed)
    fixed = np.array(pts, dtype=float) if pts else np.empty((0, n))

    arr = fixed.copy()
    added = np.empty((n_add, n), dtype=float)
    for i in range(n_add):
        cand = rng.uniform(0.0, 100.0, size=(max(1, candidates), n))
        if len(arr):
            d2 = ((cand[:, None, :] - arr[None, :, :]) ** 2).sum(2).min(axis=1)
            pick = cand[int(np.argmax(d2))]
        else:
            pick = cand[0]
        added[i] = pick
        arr = np.vstack([arr, pick[None, :]])

    base = len(fixed)
    passes = int(relax) if n_add <= 4000 else max(1, int(relax) * 4000 // n_add)
    if relax > 0 and n_add:
        from workflow.patch_generators import _nearest_site
        n_s = min(40000, max(3000, 40 * n_add))
        for _ in range(passes):
            sites = np.vstack([fixed, added]) if base else added
            samp = rng.uniform(0.0, 100.0, size=(n_s, n))
            owner = _nearest_site(samp, sites)
            for j in range(n_add):
                sel = samp[owner == base + j]
                if len(sel):
                    added[j] = sel.mean(0)

    return [tuple(float(v) for v in row) for row in added]
