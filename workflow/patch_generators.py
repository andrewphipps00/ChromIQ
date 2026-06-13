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
# 2. Fitzpatrick skin tones — 6 types, a lightness ramp through each.
# ---------------------------------------------------------------------------
# Representative sRGB anchors (0..255) for the six Fitzpatrick skin phototypes,
# light (I) to dark (VI). Each type becomes a tonal ramp around its anchor so a
# group of patches spans the natural light→dark spread within that category.
_FITZPATRICK_ANCHORS = (
    (255, 224, 196),   # I   — very fair / pale white
    (241, 194, 167),   # II  — fair / white
    (224, 172, 138),   # III — medium / light brown
    (198, 134, 95),    # IV  — olive / moderate brown
    (141, 85, 56),     # V   — brown / dark brown
    (89, 56, 41),      # VI  — very dark brown
)


def skin_tones(per_group: int) -> list[tuple[float, float, float]]:
    """A skin-tone spread: ``per_group`` patches for each of the 6 Fitzpatrick
    phototypes (``6 * per_group`` total), ordered light type → dark type.

    Within a type the anchor is swept in brightness (and very slightly in
    saturation) to cover the lighter and darker shades a real face holds, while
    keeping the hue of that phototype.
    """
    per_group = max(1, int(per_group))
    out: list[tuple[float, float, float]] = []
    for r8, g8, b8 in _FITZPATRICK_ANCHORS:
        h, s, v = colorsys.rgb_to_hsv(r8 / 255.0, g8 / 255.0, b8 / 255.0)
        for i in range(per_group):
            # Sweep value 0.78×..1.06× the anchor (clamped to 1.0); nudge
            # saturation up a touch in the shadows so darker shades don't wash
            # out to grey. Single-patch groups land exactly on the anchor.
            t = i / (per_group - 1) if per_group > 1 else 0.5
            vf = (0.78 + 0.28 * t)
            sf = (1.10 - 0.20 * t)
            r, g, b = colorsys.hsv_to_rgb(h, min(1.0, s * sf), min(1.0, v * vf))
            out.append((r * 100.0, g * 100.0, b * 100.0))
    return out


def skin_tones_count(per_group: int) -> int:
    return 6 * max(1, int(per_group))


# ---------------------------------------------------------------------------
# Shared hue-region filler for the blues / greens spreads.
# ---------------------------------------------------------------------------
def _hue_region(
    count: int,
    h0: float, h1: float,
    s0: float, s1: float,
    v0: float, v1: float,
) -> list[tuple[float, float, float]]:
    """``count`` patches spread over a hue × value grid in one hue band.

    Hue sweeps ``h0``→``h1`` across the grid columns; value sweeps ``v0``→``v1``
    down the rows; saturation tracks the hue sweep ``s0``→``s1`` so the band
    can ease from a punchy edge to a softer one. The grid is chosen as square
    as possible and trimmed to exactly ``count`` patches.
    """
    count = max(1, int(count))
    n_h = max(1, round(math.sqrt(count)))
    n_v = max(1, math.ceil(count / n_h))
    out: list[tuple[float, float, float]] = []
    for vi in range(n_v):
        tv = vi / (n_v - 1) if n_v > 1 else 0.5
        v = v0 + (v1 - v0) * tv
        for hi in range(n_h):
            th = hi / (n_h - 1) if n_h > 1 else 0.5
            h = h0 + (h1 - h0) * th
            s = s0 + (s1 - s0) * th
            out.append(_hsv(h, s, v))
    return out[:count]


# ---------------------------------------------------------------------------
# 3. Enhanced blues / turquoise — for wide-gamut spaces (AdobeRGB etc.).
# ---------------------------------------------------------------------------
def blues(count: int) -> list[tuple[float, float, float]]:
    """``count`` patches concentrated in the turquoise→blue→blue-violet band
    (hue ≈ 175°–260°) at high saturation across a few brightness levels — the
    region wide-gamut spaces stretch furthest and benefit from denser sampling.
    """
    return _hue_region(count, 175.0, 260.0, 0.95, 0.80, 0.45, 1.0)


def blues_count(count: int) -> int:
    return max(1, int(count))


# ---------------------------------------------------------------------------
# 4. Enhanced greens — forest / jungle / foliage.
# ---------------------------------------------------------------------------
def greens(count: int) -> list[tuple[float, float, float]]:
    """``count`` patches across the foliage greens (hue ≈ 80°–160°), spanning
    yellow-greens through deep forest greens with varied brightness so nature
    images are well covered.
    """
    return _hue_region(count, 80.0, 160.0, 0.95, 0.65, 0.30, 0.95)


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


def near_neutral_greys(steps: int, offset: float) -> list[tuple[float, float, float]]:
    """A neutral grey ramp plus, at each step, 6 near-neutral hue variants.

    ``steps`` neutral greys are spread from black to white. At each grey level
    ``g`` the six hue vertices (R/Y/G/C/B/M) are added as a *balanced* shift of
    ``offset`` device units — the shifted channels rise and the others fall so
    the mean stays at ``g``: a true hue ring around the neutral axis rather than
    a lightness change. Total = ``steps * 7`` (1 neutral + 6 tints per step).

    ``offset`` is in device units on the 0..100 scale (e.g. 6.25 ≈ 16/256).
    """
    steps = max(1, int(steps))
    out: list[tuple[float, float, float]] = []
    for i in range(steps):
        g = (i / (steps - 1) if steps > 1 else 0.5) * 100.0
        out.append((g, g, g))
        for mask in _HUE_MASKS:
            m = sum(mask) / 3.0
            out.append(tuple(_clamp(g + offset * (c - m)) for c in mask))
    return out


def near_neutral_greys_count(steps: int) -> int:
    return max(1, int(steps)) * 7
