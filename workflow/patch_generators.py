"""Device-RGB patch-set generators for the chart layout editor.

Each generator returns a list of ``(R, G, B)`` device-value tuples on the
**0..100** scale — exactly the "device-value program" the editor mutates
(see :mod:`workflow.ti2_relayout`). They are pure: no Qt, no Argyll, no I/O,
so they unit-test cleanly and can be combined and spliced into a program in
any order.

The five generators back the New-chart dialog's "Generate colour sets" mode
(GitHub #37): an even RGB cube, Fitzpatrick skin-tone ramps, enhanced blues /
turquoise, enhanced foliage greens, and near-neutral greys.

A note on "device RGB": ChromIQ builds *unprofiled* test charts, so these are
printer device codes, not colorimetric targets. The skin / blue / green
palettes are sRGB-reasoned starting points chosen to concentrate patches where
the issue asks for denser coverage; they are deliberately easy to retune.
"""
from __future__ import annotations

import colorsys
import math


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 100.0 if v > 100.0 else v


def _hsv(h_deg: float, s: float, v: float) -> tuple[float, float, float]:
    """HSV (hue in degrees, s/v in 0..1) → device RGB on the 0..100 scale."""
    r, g, b = colorsys.hsv_to_rgb((h_deg % 360.0) / 360.0, s, v)
    return (r * 100.0, g * 100.0, b * 100.0)


# ---------------------------------------------------------------------------
# 1. Even RGB cube — N steps per axis ⇒ N**3 patches.
# ---------------------------------------------------------------------------
def rgb_cube(n: int) -> list[tuple[float, float, float]]:
    """An evenly spaced ``n`` × ``n`` × ``n`` device-RGB cube (``n**3`` patches).

    ``n`` is the number of steps **per axis** (the issue's "N×N"), so each of
    R/G/B is sampled at ``n`` evenly spaced levels from 0 to 100 inclusive.
    """
    n = max(2, int(n))
    levels = [i / (n - 1) * 100.0 for i in range(n)]
    return [(r, g, b) for r in levels for g in levels for b in levels]


def rgb_cube_count(n: int) -> int:
    return max(2, int(n)) ** 3


# ---------------------------------------------------------------------------
# 2. Fitzpatrick skin tones — 6 types × parallel hue ramps × a lightness sweep.
# ---------------------------------------------------------------------------
# Representative sRGB anchors (0..255) for the six Fitzpatrick skin phototypes,
# light (I) to dark (VI). Each type becomes one or more tonal ramps around its
# anchor so a group of patches spans the natural light→dark spread of that
# category. The light end is pulled paler (towards porcelain white) and the
# dark end is given a faint cool/blue undertone — both ranges the original
# single ramp missed (GitHub #37 follow-up).
_FITZPATRICK_ANCHORS = (
    (255, 224, 196),   # I   — very fair / pale white
    (241, 194, 167),   # II  — fair / white
    (224, 172, 138),   # III — medium / light brown
    (198, 134, 95),    # IV  — olive / moderate brown
    (141, 85, 56),     # V   — brown / dark brown
    (84, 56, 47),      # VI  — very dark brown (faintly cool/blue undertone)
)


def skin_tones(per_type: int, ranges: int = 3) -> list[tuple[float, float, float]]:
    """A skin-tone spread for the 6 Fitzpatrick phototypes, light → dark.

    Each phototype gets ``ranges`` *parallel* ramps offset slightly in hue
    (cooler ↔ warmer) so the spread covers the natural hue variation within a
    category rather than a single line through it; every ramp is then swept in
    brightness (and a touch in saturation) over ``per_type`` patches to reach
    the paler highlights and deeper shadows real skin holds. The lightest ramps
    drift toward porcelain and the darkest pick up a faint cool undertone.

    The light end of the sweep reaches *further toward the cube centre the
    darker the anchor is*, so the deep-brown phototypes (V/VI) span a comparable
    tonal length to the pale ones instead of bunching into a short, dense ramp
    near the dark corner (GitHub #37 follow-up — Knut's "darkest range is too
    dense" note). Pale anchors are essentially unchanged.

    Total = ``6 * ranges * per_type``, ordered type-by-type, then ramp-by-ramp,
    each ramp dark → light. ``ranges = 1`` reproduces a single central ramp.
    """
    per_type = max(1, int(per_type))
    ranges = max(1, int(ranges))
    out: list[tuple[float, float, float]] = []
    for r8, g8, b8 in _FITZPATRICK_ANCHORS:
        h, s, v = colorsys.rgb_to_hsv(r8 / 255.0, g8 / 255.0, b8 / 255.0)
        # Absolute value endpoints for this anchor's ramp. The dark end stays a
        # fixed fraction below the anchor; the light end is lifted toward the
        # cube centre, with the lift scaled by (1 - v) so dark anchors stretch
        # the most and a near-white type I barely moves.
        v_dark = v * 0.74
        v_light = min(1.0, v * 1.08 + 0.22 * (1.0 - v))
        for ri in range(ranges):
            # Parallel ramps fanned ±9° in hue around the anchor; the centre
            # ramp keeps the exact phototype hue.
            tr = (ri / (ranges - 1) - 0.5) if ranges > 1 else 0.0
            dh = tr * 18.0 / 360.0
            for i in range(per_type):
                # Sweep value v_dark → v_light; lift saturation a little in the
                # shadows so darker shades don't wash to grey, and ease it down
                # toward the lighter (centre-ward) end so it goes porcelain-pale.
                t = i / (per_type - 1) if per_type > 1 else 0.5
                val = v_dark + (v_light - v_dark) * t
                sf = 1.14 - 0.30 * t
                r, g, b = colorsys.hsv_to_rgb(
                    (h + dh) % 1.0, min(1.0, max(0.0, s * sf)),
                    min(1.0, max(0.0, val))
                )
                out.append((r * 100.0, g * 100.0, b * 100.0))
    return out


def skin_tones_count(per_type: int, ranges: int = 3) -> int:
    return 6 * max(1, int(ranges)) * max(1, int(per_type))


# ---------------------------------------------------------------------------
# Shared layered hue-region filler for the blues / greens spreads.
# ---------------------------------------------------------------------------
def _hue_region_layered(
    count: int,
    h0: float, h1: float,
    s_lo: float, s_hi: float,
    v0: float, v1: float,
    layers: int = 1,
) -> list[tuple[float, float, float]]:
    """``count`` patches spread over ``layers`` non-parallel sheets in one band.

    A single sheet sweeps hue ``h0``→``h1`` across its columns and value
    ``v0``→``v1`` down its rows. With ``layers`` > 1 the ``count`` is split
    across that many sheets, each sitting at a **different saturation shell**
    (from the punchy ``s_hi`` edge in toward the softer ``s_lo`` core) and
    **tilted** so its hue skews with brightness in a per-layer direction — the
    sheets fan out rather than stacking parallel, filling the 3-D wedge of the
    band instead of a single flat blanket. The grid of each sheet is kept as
    square as possible and the whole is trimmed to exactly ``count``.
    """
    count = max(1, int(count))
    layers = max(1, int(layers))
    base, rem = divmod(count, layers)
    out: list[tuple[float, float, float]] = []
    for li in range(layers):
        n = base + (1 if li < rem else 0)
        if n <= 0:
            continue
        tl = li / (layers - 1) if layers > 1 else 0.5
        s_layer = s_hi - (s_hi - s_lo) * tl     # each layer its own saturation
        skew = (tl - 0.5) * 22.0                # degrees of hue tilt vs value
        n_h = max(1, round(math.sqrt(n)))
        n_v = max(1, math.ceil(n / n_h))
        sheet: list[tuple[float, float, float]] = []
        for vi in range(n_v):
            tv = vi / (n_v - 1) if n_v > 1 else 0.5
            v = v0 + (v1 - v0) * tv
            for hi in range(n_h):
                th = hi / (n_h - 1) if n_h > 1 else 0.5
                h = h0 + (h1 - h0) * th + skew * (tv - 0.5)
                sheet.append(_hsv(h, min(1.0, max(0.0, s_layer)), v))
        out.extend(sheet[:n])
    return out[:count]


# ---------------------------------------------------------------------------
# 3. Enhanced blues / turquoise — for wide-gamut spaces (AdobeRGB etc.).
# ---------------------------------------------------------------------------
def blues(count: int, layers: int = 3) -> list[tuple[float, float, float]]:
    """``count`` patches concentrated in the green-turquoise→blue→blue-violet
    band (hue ≈ 150°–262°) — the corner wide-gamut spaces stretch furthest. The
    band now reaches down into the **greenish turquoise** the original spread
    missed, and ``layers`` non-parallel saturation shells give the turquoise
    wedge real volume coverage instead of one flat blanket.
    """
    return _hue_region_layered(count, 150.0, 262.0, 0.55, 0.98, 0.45, 1.0, layers)


def blues_count(count: int) -> int:
    return max(1, int(count))


# ---------------------------------------------------------------------------
# 4. Enhanced greens — forest / jungle / foliage.
# ---------------------------------------------------------------------------
def greens(count: int, layers: int = 2) -> list[tuple[float, float, float]]:
    """``count`` patches across the foliage greens (hue ≈ 80°–160°), spanning
    yellow-greens through deep forest greens with varied brightness so nature
    images are well covered. ``layers`` non-parallel saturation shells fill the
    green wedge in depth rather than as a single sheet.
    """
    return _hue_region_layered(count, 80.0, 160.0, 0.50, 0.95, 0.30, 0.95, layers)


def greens_count(count: int) -> int:
    return max(1, int(count))


# ---------------------------------------------------------------------------
# 5. Near-neutral greys — neutral axis + 6 hue-shifted rings.
# ---------------------------------------------------------------------------
# Unit channel masks for the six hue vertices around the neutral axis:
# Red, Yellow, Green, Cyan, Blue, Magenta.
_HUE_MASKS = (
    (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1), (1, 0, 1),
)


# Orthonormal basis of the plane perpendicular to the neutral axis (both sum to
# zero, unit length, mutually orthogonal) — so cosθ·U + sinθ·V traces a true
# circle of unit Euclidean radius in "constant-mean" (pure-chroma) space.
_PLANE_U = (1.0 / math.sqrt(2), -1.0 / math.sqrt(2), 0.0)
_PLANE_V = (1.0 / math.sqrt(6), 1.0 / math.sqrt(6), -2.0 / math.sqrt(6))


def _ring_tints(g: float, radius: float, n: int,
                phase: float) -> list[tuple[float, float, float]]:
    """``n`` balanced hue tints on a ring of Euclidean radius ``radius``.

    The tints sit at evenly spaced hue angles (starting at ``phase`` degrees) in
    the plane perpendicular to the neutral axis, so the ring is a pure hue
    excursion around grey ``g`` — the mean of R/G/B stays at ``g``, not a
    lightness change. ``radius`` is the RGB-space (Euclidean) distance from the
    neutral point, in device units on the 0..100 scale.
    """
    out: list[tuple[float, float, float]] = []
    for k in range(n):
        th = math.radians(phase + k * 360.0 / n)
        cos_t, sin_t = math.cos(th), math.sin(th)
        out.append(tuple(
            _clamp(g + radius * (cos_t * _PLANE_U[j] + sin_t * _PLANE_V[j]))
            for j in range(3)))
    return out


def near_neutral_greys(steps: int, offset: float,
                       rings: int = 1) -> list[tuple[float, float, float]]:
    """A neutral grey ramp plus, at each step, ``rings`` rings of hue tints.

    ``steps`` neutral greys are spread from black to white. At each grey level
    ``g`` one or more concentric hue rings circle the neutral axis: ring *n* has
    ``6 * n`` tints (6, 12, 18, …) at chroma radius ``n * offset``, so outer
    rings keep roughly the same angular spacing as the inner one and fill the
    near-neutral disk rather than forming spokes (each ring is phase-rotated to
    interleave with its neighbours). Every tint is a *balanced* shift — the mean
    of R/G/B stays at ``g`` — a true hue excursion, not a lightness change.

    With ``rings == 1`` this is the original 6-tint hexagon, identical to before.
    Total = ``steps * (1 + 6 + 12 + … )`` = ``steps * (1 + 3 * rings * (rings+1))``.

    ``offset`` is in device units on the 0..100 scale (e.g. 6.25 ≈ 16/256).
    """
    steps = max(1, int(steps))
    rings = max(1, int(rings))
    out: list[tuple[float, float, float]] = []
    for i in range(steps):
        g = (i / (steps - 1) if steps > 1 else 0.5) * 100.0
        out.append((g, g, g))
        if rings == 1:
            # Preserve the exact original R/Y/G/C/B/M hexagon (and its values).
            for mask in _HUE_MASKS:
                m = sum(mask) / 3.0
                out.append(tuple(_clamp(g + offset * (c - m)) for c in mask))
            continue
        # Hexagon vertices sit sqrt(6)/3 · offset from neutral in RGB space;
        # match that for ring 1 so the offset control feels the same in both
        # modes, then space outer rings at integer multiples.
        base_radius = math.sqrt(6) / 3.0 * offset
        for r in range(1, rings + 1):
            n = 6 * r
            phase = (r - 1) * (360.0 / n) / 2.0   # interleave with inner ring
            out.extend(_ring_tints(g, r * base_radius, n, phase))
    return out


def near_neutral_greys_count(steps: int, rings: int = 1) -> int:
    rings = max(1, int(rings))
    tints = 3 * rings * (rings + 1)               # 6 + 12 + … = sum(6r)
    return max(1, int(steps)) * (1 + tints)


# ---------------------------------------------------------------------------
# 6. Gamut edges — the wireframe of the RGB cube (the device's gamut boundary).
# ---------------------------------------------------------------------------
# The eight cube corners and the twelve edges joining them. A printer's most
# saturated reproducible colours live on this surface, and it is where profiles
# carry the most error — so sampling the boundary densely is worthwhile.
_CUBE_CORNERS = {
    "K": (0.0, 0.0, 0.0),     "R": (100.0, 0.0, 0.0),
    "G": (0.0, 100.0, 0.0),   "B": (0.0, 0.0, 100.0),
    "Y": (100.0, 100.0, 0.0), "C": (0.0, 100.0, 100.0),
    "M": (100.0, 0.0, 100.0), "W": (100.0, 100.0, 100.0),
}
_CUBE_EDGES = (
    ("K", "R"), ("K", "G"), ("K", "B"),   # black → primaries
    ("R", "Y"), ("R", "M"), ("G", "Y"),   # primaries → secondaries
    ("G", "C"), ("B", "C"), ("B", "M"),
    ("Y", "W"), ("C", "W"), ("M", "W"),   # secondaries → white
)


def gamut_edges(per_edge: int) -> list[tuple[float, float, float]]:
    """``per_edge`` patches along each of the 12 edges of the RGB cube.

    This traces the device's gamut boundary — the black→primary, primary→
    secondary and secondary→white ramps that bound everything the printer can
    reproduce. Endpoints (the cube corners) are included, so adjacent edges
    share corners; combined with 'Ensure unique colours' those collapse to one.

    Total = ``12 * per_edge``.
    """
    per_edge = max(1, int(per_edge))
    out: list[tuple[float, float, float]] = []
    for a, b in _CUBE_EDGES:
        ca, cb = _CUBE_CORNERS[a], _CUBE_CORNERS[b]
        for i in range(per_edge):
            t = i / (per_edge - 1) if per_edge > 1 else 0.5
            out.append(tuple(_clamp(ca[j] + (cb[j] - ca[j]) * t) for j in range(3)))
    return out


def gamut_edges_count(per_edge: int) -> int:
    return 12 * max(1, int(per_edge))


# The 6 faces of the cube (one channel pinned at 0 or 100) — the gamut *surface*,
# of which the 12 edges are just the wireframe. (channel-to-pin, pinned-value).
_CUBE_FACES = (
    (0, 0.0), (0, 100.0), (1, 0.0), (1, 100.0), (2, 0.0), (2, 100.0),
)


def gamut_faces(per_face: int) -> list[tuple[float, float, float]]:
    """An ``per_face × per_face`` interior grid on each of the cube's 6 faces.

    Where :func:`gamut_edges` traces the gamut wireframe, this samples the gamut
    *surface* — the saturated boundary the printer can just reach. Points sit at
    cell centres ``(i + 0.5) / per_face`` so they fall strictly inside each face,
    not on the edges already covered by :func:`gamut_edges`. ``per_face = 0``
    means no face sampling.

    Total = ``6 * per_face**2``.
    """
    per_face = max(0, int(per_face))
    if per_face == 0:
        return []
    out: list[tuple[float, float, float]] = []
    for fixed, val in _CUBE_FACES:
        free = [k for k in range(3) if k != fixed]
        for i in range(per_face):
            for j in range(per_face):
                p = [0.0, 0.0, 0.0]
                p[fixed] = val
                p[free[0]] = (i + 0.5) / per_face * 100.0
                p[free[1]] = (j + 0.5) / per_face * 100.0
                out.append((p[0], p[1], p[2]))
    return out


def gamut_faces_count(per_face: int) -> int:
    pf = max(0, int(per_face))
    return 6 * pf * pf


# ---------------------------------------------------------------------------
# 7. Highlight & shadow detail — the two tonal ends, across the hue wheel.
# ---------------------------------------------------------------------------
def highlight_shadow_detail(per_end: int,
                            reach: float = 16.0,
                            *,
                            greys_enabled: bool = False,
                            greys_offset: float = 5.0,
                            greys_rings: int = 1) -> list[tuple[float, float, float]]:
    """``per_end`` patches packed into the extreme highlights + ``per_end`` into
    the extreme shadows, as two **mirror-image** end-caps around the neutral
    axis.

    The cube's even grid samples the very-light and very-dark tones coarsely,
    yet those two ends are where printers band (highlights) and block up at the
    ink limit (shadows), so this concentrates fine tonal steps there. The shadow
    cap is the exact point-inversion of the highlight cap
    (``(r,g,b) → (100-r, 100-g, 100-b)``); because the RGB cube is symmetric
    about its centre, the two ends therefore come out the *same* shape and
    spacing — no lopsided cones.

    ``reach`` (device units, 0..100) is how far in from each end the cap
    reaches; ``per_end`` is the patch count at each end (total ``2 * per_end``).

    The cap interlocks with the near-neutral greys set so the two never sample
    the same colour:

    * ``greys_enabled=True`` keeps every tint *outside* the greys' outermost
      ring (sized from ``greys_offset`` / ``greys_rings``) — greys own the
      central neutral tube, H&S the dense chromatic rim around the two ends.
    * ``greys_enabled=False`` lets the caps reach in to the neutral axis, so
      H&S also covers the near-neutral light/dark tones nothing else would.
    """
    per_end = max(1, int(per_end))
    reach = max(2.0, min(45.0, float(reach)))
    unit = math.sqrt(6) / 3.0          # RGB-space chroma radius of a unit hue mask

    # Inner chroma floor: clear the greys' outermost ring when that set is on
    # (with half a ring of headroom so they never touch), else start on the
    # neutral axis so the near-neutral tones are covered here instead.
    r_inner = 0.0
    if greys_enabled:
        off = max(0.0, float(greys_offset))
        greys_outer = max(1, int(greys_rings)) * unit * off
        r_inner = greys_outer + 0.5 * unit * max(1.0, off)

    # Spread per_end over a few tonal levels (favour tonal resolution — the whole
    # point of the feature) × a hue ring at each level.
    n_lev = max(1, round(math.sqrt(per_end * 0.6)))
    counts = [per_end // n_lev + (1 if i < per_end % n_lev else 0)
              for i in range(n_lev)]

    # Lightness span of the highlight cap: from `inset` below white down to
    # `reach` below white. The inset keeps the lightest level off pure white
    # (where no chroma fits) and, when greys are on, far enough in that the ring
    # clears them with gamut headroom to spare.
    inset = min(max(reach / (n_lev + 1), r_inner), reach * 0.5)
    L_top = 100.0 - inset
    L_bot = 100.0 - reach

    highlights: list[tuple[float, float, float]] = []
    for i in range(n_lev):
        tl = i / (n_lev - 1) if n_lev > 1 else 0.0
        L = L_top - (L_top - L_bot) * tl
        head = (100.0 - L) * 1.3                # ≳ max balanced chroma that fits
        radius = min(head, max(r_inner, head * 0.7))
        phase = i * 27.0                        # rotate levels so hues interleave
        highlights.extend(_ring_tints(L, radius, counts[i], phase))

    shadows = [(100.0 - r, 100.0 - g, 100.0 - b) for (r, g, b) in highlights]
    return highlights + shadows


def highlight_shadow_detail_count(per_end: int) -> int:
    return 2 * max(1, int(per_end))


# ---------------------------------------------------------------------------
# 8. Image palette — the most representative colours of a loaded image.
# ---------------------------------------------------------------------------
def _srgb_to_lab(rgb01):
    """(N,3) sRGB in 0..1 → (N,3) CIELab (D65). NumPy-vectorised."""
    import numpy as np
    a = np.asarray(rgb01, dtype=float)
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    f = xyz / white
    f = np.where(f > 0.008856, np.cbrt(f), 7.787 * f + 16.0 / 116.0)
    L = 116.0 * f[:, 1] - 16.0
    return np.stack([L, 500.0 * (f[:, 0] - f[:, 1]),
                     200.0 * (f[:, 1] - f[:, 2])], axis=1)


def image_palette(pixels, count: int,
                  seed: int = 0) -> list[tuple[float, float, float]]:
    """The ``count`` most representative colours of ``pixels``, as device RGB.

    ``pixels`` is an ``(N, 3)`` array-like of 0..255 RGB (e.g. a decoded,
    down-sampled image). The colours are clustered perceptually (k-means in
    CIELab with a k-means++ start) so the result spans the image's real colour
    families rather than over-counting one busy area. Cluster centres are
    returned on the 0..100 device scale, brightest first.

    Pure and deterministic (fixed ``seed``): the UI handles loading / decoding
    the file and hands the pixels here, so this stays unit-testable.
    """
    import numpy as np

    count = max(1, int(count))
    px = np.asarray(pixels, dtype=float).reshape(-1, 3)
    if px.size == 0:
        return []
    if px.max() > 1.0:
        px = px / 255.0
    lab = _srgb_to_lab(px)

    rng = np.random.default_rng(seed)
    uniq = np.unique(lab, axis=0)
    k = min(count, len(uniq))
    # k-means++ seeding on the unique colours.
    centres = [uniq[rng.integers(len(uniq))]]
    for _ in range(1, k):
        d2 = np.min([np.sum((uniq - c) ** 2, axis=1) for c in centres], axis=0)
        tot = d2.sum()
        probs = d2 / tot if tot > 0 else None
        centres.append(uniq[rng.choice(len(uniq), p=probs)])
    C = np.array(centres)
    # A few Lloyd iterations over the full pixel set.
    for _ in range(16):
        assign = np.argmin(((lab[:, None, :] - C[None, :, :]) ** 2).sum(2), axis=1)
        newC = np.array([lab[assign == j].mean(0) if np.any(assign == j) else C[j]
                         for j in range(k)])
        if np.allclose(newC, C):
            break
        C = newC

    # Map each Lab centre back to device RGB via the nearest source pixel
    # (avoids an inverse-Lab round-trip and keeps centres in-gamut for real
    # image colours).
    out: list[tuple[float, float, float]] = []
    for c in C:
        idx = int(np.argmin(((lab - c) ** 2).sum(1)))
        r, g, b = px[idx]
        out.append((float(r) * 100.0, float(g) * 100.0, float(b) * 100.0))
    out.sort(key=lambda p: 0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2], reverse=True)
    return out


def image_palette_count(count: int, has_image: bool) -> int:
    return max(1, int(count)) if has_image else 0


# ---------------------------------------------------------------------------
# 9. Pastels — low-chroma midtones, the bulk of real photographic content.
# ---------------------------------------------------------------------------
def pastels(count: int, layers: int = 1) -> list[tuple[float, float, float]]:
    """``count`` gentle, low-chroma colours across the hue wheel and mid-to-light
    tones — the muted, desaturated region most photos actually live in.

    This fills the gap between the near-neutral greys (almost no chroma) and the
    vivid sets (full chroma): soft pinks, sages, dusty blues, taupes and the like.
    ``layers`` splits the patches across that many **chroma shells**, from barely
    tinted near-greys out to fuller pastels, so the muted band is filled in depth
    rather than as a single sheet (each shell's hues interleave with its
    neighbours'). With ``layers == 1`` it is one shell at a moderate pastel chroma.

    Total = ``count``.
    """
    count = max(1, int(count))
    layers = max(1, int(layers))
    base, rem = divmod(count, layers)
    out: list[tuple[float, float, float]] = []
    for li in range(layers):
        n = base + (1 if li < rem else 0)
        if n <= 0:
            continue
        tl = li / (layers - 1) if layers > 1 else 0.5
        s_shell = 0.12 + 0.18 * tl                # near-grey → fuller pastel
        n_h = max(1, round(math.sqrt(n)))         # hues across, tone down
        n_r = max(1, math.ceil(n / n_h))
        sheet: list[tuple[float, float, float]] = []
        for ri in range(n_r):
            tr = ri / (n_r - 1) if n_r > 1 else 0.5
            v = 0.55 + 0.34 * tr                  # mid → light
            for hi in range(n_h):
                h = ((hi + li / layers) / n_h) * 360.0   # interleave shells in hue
                sheet.append(_hsv(h, s_shell, v))
        out.extend(sheet[:n])
    return out[:count]


def pastels_count(count: int) -> int:
    return max(1, int(count))


# ---------------------------------------------------------------------------
# 10. Fill the gaps — blue-noise top-up of whatever the other sets left sparse.
# ---------------------------------------------------------------------------
def fill_gaps(existing, total: int, candidates: int = 12,
              seed: int = 0) -> list[tuple[float, float, float]]:
    """Add patches until the program reaches ``total``, placed where it is sparse.

    Given the already-chosen ``existing`` patches, this returns ``total -
    len(existing)`` new device-RGB patches via best-candidate (Mitchell)
    sampling: each new point is the farthest of several random candidates from
    everything chosen so far. The result is an even, lattice-free "blue-noise"
    fill of the cube's empty regions — coverage without near-duplicates. Returns
    ``[]`` if the program already meets or exceeds ``total``.
    """
    import numpy as np

    total = int(total)
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in existing]
    n_add = total - len(pts)
    if n_add <= 0:
        return []
    rng = np.random.default_rng(seed)
    arr = np.array(pts, dtype=float) if pts else np.empty((0, 3))
    added: list[tuple[float, float, float]] = []
    for _ in range(n_add):
        cand = rng.uniform(0.0, 100.0, size=(max(1, candidates), 3))
        if len(arr):
            d2 = ((cand[:, None, :] - arr[None, :, :]) ** 2).sum(2).min(axis=1)
            pick = cand[int(np.argmax(d2))]
        else:
            pick = cand[0]
        added.append((float(pick[0]), float(pick[1]), float(pick[2])))
        arr = np.vstack([arr, pick[None, :]]) if len(arr) else pick[None, :]
    return added


def fill_gaps_count(existing_count: int, total: int) -> int:
    return max(0, int(total) - int(existing_count))


# ---------------------------------------------------------------------------
# Cross-set de-duplication — keep every patch unique when sets are combined.
# ---------------------------------------------------------------------------
def _dedupe_key(p: tuple[float, float, float], quantum: float) -> tuple[int, int, int]:
    return tuple(int(round(c / quantum)) for c in p)  # type: ignore[return-value]


def deduplicate(
    patches: list[tuple[float, float, float]],
    quantum: float = 0.5,
    step: float = 1.0,
) -> list[tuple[float, float, float]]:
    """Return ``patches`` with near-duplicates nudged apart so each is unique.

    Two patches collide when they round to the same point on a ``quantum``-unit
    grid (device units, 0..100). On a collision the later patch is nudged by a
    growing multiple of ``step`` along rotating channels — toward whichever side
    has room — until it lands on a free cell, staying within 0..100. Input order
    is preserved, so combining e.g. a cube with a grey ramp keeps shared corners
    from being printed twice (GitHub #37 follow-up).
    """
    seen: set[tuple[int, int, int]] = set()
    out: list[tuple[float, float, float]] = []
    for p in patches:
        r, g, b = (_clamp(p[0]), _clamp(p[1]), _clamp(p[2]))
        key = _dedupe_key((r, g, b), quantum)
        tries = 0
        while key in seen and tries < 600:
            ch = tries % 3
            delta = step * (1 + tries // 3)
            base = (r, g, b)[ch]
            cand = base + delta if base + delta <= 100.0 else base - delta
            nud = [r, g, b]
            nud[ch] = _clamp(cand)
            r, g, b = nud
            key = _dedupe_key((r, g, b), quantum)
            tries += 1
        seen.add(key)
        out.append((r, g, b))
    return out
