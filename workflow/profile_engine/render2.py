"""Bijective radial rendering intents in CAM16-UCS (issue #123, W5 —
candidate ``"render2"``).

A new perceptual/saturation gamut mapping, *additive*: the bit-exact
Argyll mapping stays the default — this mapper only runs behind its
candidate token, and only for the default intent selections.

Design: **bijective by construction.** The map is a composition of two
closed-form bijections in CAM16-UCS —

1. a lightness affine (source neutral J' range → destination range,
   black-point compensation included by construction);
2. a radial knee map about a fixed focal point on the destination
   neutral axis: identity inside ``knee·R_dst(dir)``, then the strictly
   monotone C¹ rational tail

       γ(r) = k + A·(r−k)/(A + (r−k)),   A = uv/(u−v),
       u = R_src−k,  v = R_dst−k

   which reaches R_dst exactly at R_src with unit slope at the knee, and
   inverts in closed form: γ⁻¹(g) = k + A·(g−k)/(A − (g−k)). Saturation
   uses the same family run in the expansion direction.

Because the inverse is exact, colprof's ``-nI`` (inverse-mapped A2B0/2)
becomes algebra instead of fixed-point iteration — table pairs
round-trip to identity by construction.

Gamut boundaries are radial fields R(hue, elevation) from the existing
source/destination surface clouds (star-shaped about the focal point,
smoothed; the standard radial-gamut representation, Morovič).
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.ucs import print_ucs

_HUE_BINS = 36
_ELEV_BINS = 18
_KNEE = 0.75            # protected core, fraction of the dest radius
_KNEE_SAT = 0.90        # saturation intent keeps chroma longer


def _radial_field(pts: np.ndarray, focal: np.ndarray) -> np.ndarray:
    """(hue, elev) grid of max radii — the star-shaped boundary field."""
    v = pts - focal[None, :]
    r = np.linalg.norm(v, axis=1)
    hue = (np.degrees(np.arctan2(v[:, 2], v[:, 1])) % 360.0)
    elev = np.degrees(np.arctan2(v[:, 0], np.hypot(v[:, 1], v[:, 2])))
    hi = np.clip((hue / 360.0 * _HUE_BINS).astype(int), 0, _HUE_BINS - 1)
    ei = np.clip(((elev + 90.0) / 180.0 * _ELEV_BINS).astype(int),
                 0, _ELEV_BINS - 1)
    field = np.zeros((_HUE_BINS, _ELEV_BINS))
    np.maximum.at(field, (hi, ei), r)
    # Fill empty bins from neighbours (hue wraps), then smooth twice.
    for _ in range(_ELEV_BINS + _HUE_BINS):
        empty = field == 0
        if not empty.any():
            break
        grown = np.maximum.reduce([
            np.roll(field, 1, 0), np.roll(field, -1, 0),
            np.pad(field, ((0, 0), (1, 0)))[:, :-1],
            np.pad(field, ((0, 0), (0, 1)))[:, 1:]])
        field[empty] = grown[empty]
    for _ in range(2):
        field = (0.5 * field + 0.25 * np.roll(field, 1, 0)
                 + 0.25 * np.roll(field, -1, 0))
        pad = np.pad(field, ((0, 0), (1, 1)), mode="edge")
        field = 0.5 * field + 0.25 * pad[:, :-2] + 0.25 * pad[:, 2:]
    return field


def _field_lookup(field: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear radius lookup for direction vectors ``v`` (hue wraps)."""
    hue = (np.degrees(np.arctan2(v[:, 2], v[:, 1])) % 360.0)
    elev = np.degrees(np.arctan2(v[:, 0], np.hypot(v[:, 1], v[:, 2])))
    x = hue / 360.0 * _HUE_BINS
    y = np.clip((elev + 90.0) / 180.0 * _ELEV_BINS - 0.5,
                0.0, _ELEV_BINS - 1.0)
    x0 = np.floor(x).astype(int) % _HUE_BINS
    x1 = (x0 + 1) % _HUE_BINS
    fx = x - np.floor(x)
    y0 = np.clip(y.astype(int), 0, _ELEV_BINS - 2)
    fy = y - y0
    return ((1 - fx) * (1 - fy) * field[x0, y0]
            + fx * (1 - fy) * field[x1, y0]
            + (1 - fx) * fy * field[x0, y0 + 1]
            + fx * fy * field[x1, y0 + 1])


def _knee_map(r: np.ndarray, r_src: np.ndarray, r_dst: np.ndarray,
              knee: float, inverse: bool = False) -> np.ndarray:
    """The monotone C¹ radial family (see module docstring), vectorised.

    Compression where the source reaches beyond the destination,
    expansion where it falls short. Beyond the source radius the map
    continues with its end slope (a C¹ *linear extension*) — every ray
    is a bijection of the whole half-line, so ``inverse=True`` is the
    exact algebraic inverse everywhere, not just inside the gamuts.
    """
    out = r.copy()
    k = knee * np.minimum(r_dst, r_src)
    u = r_src - k                               # forward input tail
    v = r_dst - k                               # forward output tail
    ok = (u > 1e-9) & (v > 1e-9) & (np.abs(u - v) > 1e-9)
    g = r - k
    active = (g > 0) & ok
    if not active.any():
        return out
    uu, vv, gg = u[active], v[active], g[active]
    comp = uu > vv
    a = uu * vv / np.abs(uu - vv)
    if not inverse:
        # rational core γ, then linear extension past the source radius
        core = np.where(comp, a * gg / (a + gg),
                        a * gg / np.maximum(a - gg, 1e-9))
        slope = np.where(comp, (a / (a + uu)) ** 2,
                         (a / np.maximum(a - uu, 1e-9)) ** 2)
        mapped = np.where(gg <= uu, core, vv + (gg - uu) * slope)
    else:
        slope = np.where(comp, (a / (a + uu)) ** 2,
                         (a / np.maximum(a - uu, 1e-9)) ** 2)
        core = np.where(comp, a * gg / np.maximum(a - gg, 1e-9),
                        a * gg / (a + gg))
        mapped = np.where(gg <= vv, core, uu + (gg - vv) / slope)
    out[active] = k[active] + mapped
    return out


class RadialUcsMapper:
    """Bijective perceptual/saturation mapper (Lab → Lab via CAM16-UCS)."""

    expensive_map = False

    def __init__(self, src_lab: np.ndarray, dst_lab: np.ndarray,
                 intent: str = "p") -> None:
        self._ucs = print_ucs()
        src = self._ucs.lab_to_ucs(src_lab)
        dst = self._ucs.lab_to_ucs(dst_lab)
        self._knee = _KNEE_SAT if intent == "s" else _KNEE
        # Lightness map: knee compression FROM THE WHITE END — midtones
        # and highlights identity, only the shadow tail compresses into
        # the destination's black floor (a linear BPC lifts midtones by
        # the full floor difference — measured +9 L* on a matte-class
        # black, visibly washed out). Same closed-form-invertible family
        # as the radial tail. Darkest/brightest = cloud extremes (a
        # chroma-gated "neutral" subset misses the true black corner).
        self._s_hi = float(src[:, 0].max())
        self._d_hi = float(dst[:, 0].max())
        self._s_span = self._s_hi - float(src[:, 0].min())
        self._d_span = self._d_hi - float(dst[:, 0].min())
        d_lo = self._d_hi - self._d_span
        # Radial boundary fields about the destination mid-neutral focal.
        self._focal = np.array([0.5 * (d_lo + self._d_hi), 0.0, 0.0])
        src_l = src.copy()
        src_l[:, 0] = self._light_fwd(src_l[:, 0])
        self._r_src = _radial_field(src_l, self._focal)
        self._r_dst = _radial_field(dst, self._focal)

    def _light_fwd(self, jp: np.ndarray) -> np.ndarray:
        t = np.asarray(self._s_hi - jp, float)
        t2 = _knee_map(t, np.full_like(t, self._s_span),
                       np.full_like(t, self._d_span), _KNEE)
        return self._d_hi - t2

    def _light_inv(self, jp: np.ndarray) -> np.ndarray:
        t = np.asarray(self._d_hi - jp, float)
        t2 = _knee_map(t, np.full_like(t, self._s_span),
                       np.full_like(t, self._d_span), _KNEE, inverse=True)
        return self._s_hi - t2

    # -- forward -----------------------------------------------------------
    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        u = self._ucs.lab_to_ucs(np.atleast_2d(lab))
        u[:, 0] = self._light_fwd(u[:, 0])
        v = u - self._focal[None, :]
        r = np.linalg.norm(v, axis=1)
        safe = np.maximum(r, 1e-9)
        rs = _field_lookup(self._r_src, v)
        rd = _field_lookup(self._r_dst, v)
        r2 = _knee_map(r, rs, rd, self._knee)
        out = self._focal[None, :] + v * (r2 / safe)[:, None]
        return self._ucs.ucs_to_lab(out)

    # -- exact inverse (colprof -nI without fixed-point iteration) ----------
    def unmap_lab(self, lab: np.ndarray) -> np.ndarray:
        u = self._ucs.lab_to_ucs(np.atleast_2d(lab))
        v = u - self._focal[None, :]
        r = np.linalg.norm(v, axis=1)
        safe = np.maximum(r, 1e-9)
        rs = _field_lookup(self._r_src, v)
        rd = _field_lookup(self._r_dst, v)
        r2 = _knee_map(r, rs, rd, self._knee, inverse=True)
        out = self._focal[None, :] + v * (r2 / safe)[:, None]
        out[:, 0] = self._light_inv(out[:, 0])
        return self._ucs.ucs_to_lab(out)
