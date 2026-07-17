"""CAM16-UCS — the perceptually uniform optimisation space (issue #123, W1).

CIELAB is not perceptually uniform (ΔE2000 exists precisely to patch that),
but ΔE2000 is a *formula*, not a space — it cannot be the inner metric of a
least-squares pipeline. CAM16-UCS (Li et al. 2017, "Comprehensive color
solutions: CAM16, CAT16, and CAM16-UCS") is a true metric space: Euclidean
distance ≈ perceived difference (agreement with ΔE00 within ~10 % over the
print gamut). Fitting, inverting and smoothing there makes every existing
least-squares component perceptually correct *by construction* — the
per-point ΔE00 weighting machinery collapses to identity.

CAM16 replaces CIECAM02's CAT02+HPE two-matrix dance with the single M16
matrix (fixing CIECAM02's blue-corner failure); everything downstream of
the adaptation is unchanged. Implementation validated against the
published numerical example (J=41.7312, C=0.10336, h=217.0680 for
XYZ=(19.01, 20, 21.78) under L_A=318.31) and gated by a round-trip
self-test on first use, like :mod:`gammap_port.cam02`.

Viewing conditions for print profiles: D50 booth — L_A = 63.66 cd/m²
(318.3 lux / 5 per the usual print-viewing convention), Y_b = 20, average
surround. One place, documented; never tuned against a measurement.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.ti3_data import (D50_XYZ100, lab_to_xyz,
                                              xyz_to_lab)

_M16 = np.array([[0.401288, 0.650173, -0.051461],
                 [-0.250268, 1.204414, 0.045854],
                 [-0.002079, 0.048952, 0.953127]])
_M16_I = np.linalg.inv(_M16)

# surround: (F, c, Nc) — identical to CIECAM02.
_SURROUND = {"average": (1.0, 0.69, 1.0),
             "dim": (0.9, 0.59, 0.9),
             "dark": (0.8, 0.525, 0.8)}


class Cam16:
    """Vectorised CAM16 forward/inverse under fixed viewing conditions."""

    def __init__(self, La: float = 63.66, Yb: float = 20.0,
                 surround: str = "average",
                 white_xyz100: np.ndarray | None = None) -> None:
        w = D50_XYZ100.copy() if white_xyz100 is None \
            else np.asarray(white_xyz100, float)
        F, c, Nc = _SURROUND[surround]
        self._w, self._c, self._Nc = w, c, Nc
        self._D = float(np.clip(
            F * (1 - (1 / 3.6) * np.exp(-(La + 42) / 92)), 0, 1))
        k = 1.0 / (5 * La + 1)
        self.FL = (0.2 * k ** 4 * 5 * La
                   + 0.1 * (1 - k ** 4) ** 2 * (5 * La) ** (1 / 3))
        n = Yb / w[1]
        self._n = n
        self._Nbb = self._Ncb = 0.725 * (1 / n) ** 0.2
        self._z = 1.48 + np.sqrt(n)
        rgbw = _M16 @ w
        self._Dfac = self._D * w[1] / rgbw + 1 - self._D
        rgbaw = self._adapt_post((rgbw * self._Dfac)[None, :])[0]
        self._Aw = (2 * rgbaw[0] + rgbaw[1] + 0.05 * rgbaw[2] - 0.305) \
            * self._Nbb

    def _adapt_post(self, rgbc: np.ndarray) -> np.ndarray:
        t = (self.FL * np.abs(rgbc) / 100.0) ** 0.42
        return np.sign(rgbc) * 400.0 * t / (t + 27.13) + 0.1

    def xyz_to_jmh(self, xyz100: np.ndarray) -> np.ndarray:
        """(N,3) XYZ (Y=100) → (N,3) [J, M, h°]."""
        xyz100 = np.atleast_2d(np.asarray(xyz100, float))
        rgba = self._adapt_post(xyz100 @ _M16.T * self._Dfac[None, :])
        a = rgba[:, 0] - 12 * rgba[:, 1] / 11 + rgba[:, 2] / 11
        b = (rgba[:, 0] + rgba[:, 1] - 2 * rgba[:, 2]) / 9
        h = np.degrees(np.arctan2(b, a)) % 360.0
        A = (2 * rgba[:, 0] + rgba[:, 1] + 0.05 * rgba[:, 2] - 0.305) \
            * self._Nbb
        J = 100.0 * np.clip(A / self._Aw, 0.0, None) ** (self._c * self._z)
        hr = np.radians(h)
        et = 0.25 * (np.cos(hr + 2) + 3.8)
        den = rgba[:, 0] + rgba[:, 1] + 21.0 * rgba[:, 2] / 20.0
        t = np.clip((50000.0 / 13.0 * self._Nc * self._Ncb * et
                     * np.hypot(a, b))
                    / np.where(np.abs(den) < 1e-9, 1e-9, den), 0.0, None)
        C = t ** 0.9 * np.sqrt(J / 100.0) * (1.64 - 0.29 ** self._n) ** 0.73
        return np.stack([J, C * self.FL ** 0.25, h], 1)

    def jmh_to_xyz(self, jmh: np.ndarray) -> np.ndarray:
        jmh = np.atleast_2d(np.asarray(jmh, float))
        J = np.clip(jmh[:, 0], 1e-6, None)
        C = np.clip(jmh[:, 1], 0.0, None) / self.FL ** 0.25
        hr = np.radians(jmh[:, 2])
        t = np.power(
            C / (np.sqrt(J / 100.0) * (1.64 - 0.29 ** self._n) ** 0.73),
            1.0 / 0.9, where=C > 1e-12, out=np.zeros_like(C))
        A = self._Aw * (J / 100.0) ** (1.0 / (self._c * self._z))
        et = 0.25 * (np.cos(hr + 2) + 3.8)
        p1 = (50000.0 / 13.0 * self._Nc * self._Ncb) * et \
            / np.where(t < 1e-12, 1e-12, t)
        p2 = A / self._Nbb + 0.305
        p3 = 21.0 / 20.0
        sin_h, cos_h = np.sin(hr), np.cos(hr)
        a = np.empty_like(J)
        b = np.empty_like(J)
        big = np.abs(sin_h) >= np.abs(cos_h)
        with np.errstate(divide="ignore", invalid="ignore"):
            p4 = p1 / np.where(sin_h == 0, 1e-12, sin_h)
            bb = (p2 * (2 + p3) * (460.0 / 1403.0)
                  / (p4 + (2 + p3) * (220.0 / 1403.0) * (cos_h / sin_h)
                     - (27.0 / 1403.0) + p3 * (6300.0 / 1403.0)))
            aa = bb * (cos_h / sin_h)
            p5 = p1 / np.where(cos_h == 0, 1e-12, cos_h)
            aa2 = (p2 * (2 + p3) * (460.0 / 1403.0)
                   / (p5 + (2 + p3) * (220.0 / 1403.0)
                      - ((27.0 / 1403.0) - p3 * (6300.0 / 1403.0))
                      * (sin_h / cos_h)))
            bb2 = aa2 * (sin_h / cos_h)
        a[big], b[big] = aa[big], bb[big]
        a[~big], b[~big] = aa2[~big], bb2[~big]
        zero = t < 1e-12
        a[zero] = b[zero] = 0.0
        rgba = np.stack([
            (460.0 * p2 + 451.0 * a + 288.0 * b) / 1403.0,
            (460.0 * p2 - 891.0 * a - 261.0 * b) / 1403.0,
            (460.0 * p2 - 220.0 * a - 6300.0 * b) / 1403.0,
        ], 1)
        x = rgba - 0.1
        mag = (27.13 * np.abs(x)) / np.clip(400.0 - np.abs(x), 1e-9, None)
        rgbc = np.sign(x) * 100.0 / self.FL * mag ** (1.0 / 0.42)
        return (rgbc / self._Dfac[None, :]) @ _M16_I.T


class Cam16Ucs:
    """CAM16-UCS coordinates (J', a', b') — Euclidean ≈ perceptual."""

    def __init__(self, cam: Cam16 | None = None) -> None:
        self.cam = cam or Cam16()

    def xyz_to_ucs(self, xyz100: np.ndarray) -> np.ndarray:
        jmh = self.cam.xyz_to_jmh(xyz100)
        jp = 1.7 * jmh[:, 0] / (1.0 + 0.007 * jmh[:, 0])
        mp = np.log1p(0.0228 * jmh[:, 1]) / 0.0228
        hr = np.radians(jmh[:, 2])
        return np.stack([jp, mp * np.cos(hr), mp * np.sin(hr)], 1)

    def ucs_to_xyz(self, ucs: np.ndarray) -> np.ndarray:
        ucs = np.atleast_2d(np.asarray(ucs, float))
        jp = np.clip(ucs[:, 0], 1e-4, None)
        j = jp / (1.7 - 0.007 * jp)
        mp = np.hypot(ucs[:, 1], ucs[:, 2])
        m = np.expm1(0.0228 * mp) / 0.0228
        h = np.degrees(np.arctan2(ucs[:, 2], ucs[:, 1])) % 360.0
        return self.cam.jmh_to_xyz(np.stack([j, m, h], 1))

    # -- Lab bridges (media-relative D50 Lab, the engine's house basis) ----
    def lab_to_ucs(self, lab: np.ndarray) -> np.ndarray:
        return self.xyz_to_ucs(lab_to_xyz(np.atleast_2d(lab)))

    def ucs_to_lab(self, ucs: np.ndarray) -> np.ndarray:
        return xyz_to_lab(self.ucs_to_xyz(ucs))


_PRINT_UCS: Cam16Ucs | None = None


def print_ucs() -> Cam16Ucs:
    """The engine's shared print-viewing CAM16-UCS, round-trip-gated once."""
    global _PRINT_UCS
    if _PRINT_UCS is None:
        u = Cam16Ucs()
        rng = np.random.default_rng(1)
        # The print-colour domain (dark colours carry bounded chroma —
        # the CAM16 inverse is exact there; it only degenerates on
        # non-colours like Y≈0.3 with Z≈80, beyond the monochromatic
        # limit, which no print or A2B node value can reach).
        l = rng.uniform(3.0, 100.0, 400)
        c = rng.uniform(0.0, 1.0, 400) * np.minimum(140.0, 4.0 * l + 10.0)
        h = rng.uniform(0.0, 2 * np.pi, 400)
        lab = np.column_stack([l, c * np.cos(h), c * np.sin(h)])
        back = u.ucs_to_lab(u.lab_to_ucs(lab))
        err = float(np.abs(back - lab).max())
        if err > 1e-4:
            raise RuntimeError(f"CAM16-UCS round-trip failed ({err:g})")
        _PRINT_UCS = u
    return _PRINT_UCS
