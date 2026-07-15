"""CIECAM02 forward/inverse (CIE 159:2004) + Argyll's viewing-condition
presets — the appearance space for viewing-condition-aware gamut mapping.

The forward transform was validated against Argyll's own CAM (issue #122:
identical device values through ``xicclu -pj`` vs this code at the ``pp``
conditions agree to median ΔJab 0.98) — and, measured there too: do NOT
pre-mix Argyll's display glare into the input XYZ, it is internal to their
CAM. The inverse is gated by an exact round-trip check at import-critical
call sites (``jab_to_xyz(xyz_to_jab(x)) ≈ x``).

Preset values lifted verbatim from ``xicc/xicc.c`` (ArgyllCMS 3.5.0),
``La``/``Yb`` per preset; surround class per the viewing environment.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_MCAT = np.array([[0.7328, 0.4296, -0.1624],
                  [-0.7036, 1.6975, 0.0061],
                  [0.0030, 0.0136, 0.9834]])
_MHPE = np.array([[0.38971, 0.68898, -0.07868],
                  [-0.22981, 1.18340, 0.04641],
                  [0.0, 0.0, 1.0]])
_MCAT_I = np.linalg.inv(_MCAT)
_MHPE_I = np.linalg.inv(_MHPE)

# surround: (F, c, Nc)
_SURROUND = {"average": (1.0, 0.69, 1.0),
             "dim": (0.9, 0.59, 0.9),
             "dark": (0.8, 0.525, 0.8)}


@dataclass(frozen=True)
class ViewingConditions:
    La: float = 32.0                 # adapting luminance cd/m²
    Yb: float = 20.0                 # background Y relative to white=100
    surround: str = "average"

    @property
    def fcn(self) -> tuple[float, float, float]:
        return _SURROUND[self.surround]


# Argyll's enumerated viewing conditions (xicc.c, values verbatim; Yb 0.2 →
# 20 on the Y=100 scale). Surround classes per the environment description.
VIEWING_PRESETS: dict[str, ViewingConditions] = {
    "pc": ViewingConditions(127.0, 20.0, "average"),
    "pp": ViewingConditions(32.0, 20.0, "average"),
    "pe": ViewingConditions(30.0, 20.0, "average"),
    "mt": ViewingConditions(22.0, 20.0, "average"),
    "mb": ViewingConditions(42.0, 20.0, "average"),
    "md": ViewingConditions(10.0, 20.0, "dim"),
    "jm": ViewingConditions(10.0, 20.0, "dim"),
    "jd": ViewingConditions(8.0, 20.0, "dark"),
    "pcd": ViewingConditions(320.0, 20.0, "average"),
    "ob": ViewingConditions(2000.0, 20.0, "average"),
    "cx": ViewingConditions(53.0, 20.0, "dim"),
}
DEFAULT_VC = VIEWING_PRESETS["pp"]     # colprof's print default


class Cam02:
    """Vectorised CIECAM02 under fixed viewing conditions (white = D50)."""

    def __init__(self, vc: ViewingConditions,
                 white_xyz100: np.ndarray | None = None) -> None:
        w = np.array([96.42, 100.0, 82.49]) if white_xyz100 is None \
            else np.asarray(white_xyz100, dtype=float)
        F, c, Nc = vc.fcn
        self._w = w
        self._c = c
        self._Nc = Nc
        La, Yb = vc.La, vc.Yb
        self._D = np.clip(F * (1 - (1 / 3.6) * np.exp(-(La + 42) / 92)), 0, 1)
        k = 1.0 / (5 * La + 1)
        self._FL = (0.2 * k ** 4 * 5 * La
                    + 0.1 * (1 - k ** 4) ** 2 * (5 * La) ** (1 / 3))
        n = Yb / w[1]
        self._Nbb = self._Ncb = 0.725 * (1 / n) ** 0.2
        self._z = 1.48 + np.sqrt(n)
        self._n = n
        rgbw = _MCAT @ w
        self._rgbw = rgbw
        self._Dfac = self._D * w[1] / rgbw + 1 - self._D
        rgbaw = self._adapt_post(rgbw * self._Dfac)
        self._Aw = (2 * rgbaw[0] + rgbaw[1] + 0.05 * rgbaw[2] - 0.305) \
            * self._Nbb

    # -- forward ---------------------------------------------------------
    def _adapt_post(self, rgbc: np.ndarray) -> np.ndarray:
        rgbp = (_MHPE @ (_MCAT_I @ rgbc.reshape(-1, 3).T)).T
        t = (self._FL * np.abs(rgbp) / 100.0) ** 0.42
        out = np.sign(rgbp) * 400.0 * t / (t + 27.13) + 0.1
        return out[0] if out.shape[0] == 1 and rgbc.ndim == 1 else out

    def xyz_to_jab(self, xyz100: np.ndarray) -> np.ndarray:
        rgb = xyz100 @ _MCAT.T
        rgba = self._adapt_post(rgb * self._Dfac[None, :])
        a = rgba[:, 0] - 12 * rgba[:, 1] / 11 + rgba[:, 2] / 11
        b = (rgba[:, 0] + rgba[:, 1] - 2 * rgba[:, 2]) / 9
        h = np.degrees(np.arctan2(b, a)) % 360.0
        A = (2 * rgba[:, 0] + rgba[:, 1] + 0.05 * rgba[:, 2] - 0.305) \
            * self._Nbb
        J = 100.0 * np.clip(A / self._Aw, 0, None) ** (self._c * self._z)
        hr = np.radians(h)
        et = 0.25 * (np.cos(hr + 2) + 3.8)
        num = (50000.0 / 13.0 * self._Nc * self._Ncb * et
               * np.hypot(a, b))
        den = rgba[:, 0] + rgba[:, 1] + 21.0 * rgba[:, 2] / 20.0
        t = np.clip(num / np.where(np.abs(den) < 1e-6, 1e-6, den), 0, None)
        C = t ** 0.9 * np.sqrt(np.clip(J, 0, None) / 100.0) \
            * (1.64 - 0.29 ** self._n) ** 0.73
        return np.stack([J, C * np.cos(hr), C * np.sin(hr)], 1)

    # -- inverse ---------------------------------------------------------
    def jab_to_xyz(self, jab: np.ndarray) -> np.ndarray:
        J = np.clip(jab[:, 0], 1e-6, None)
        C = np.hypot(jab[:, 1], jab[:, 2])
        h = np.degrees(np.arctan2(jab[:, 2], jab[:, 1])) % 360.0
        hr = np.radians(h)
        t = np.power(
            np.clip(C, 0, None)
            / (np.sqrt(J / 100.0) * (1.64 - 0.29 ** self._n) ** 0.73),
            1.0 / 0.9, where=C > 1e-12, out=np.zeros_like(C))
        A = self._Aw * (J / 100.0) ** (1.0 / (self._c * self._z))
        et = 0.25 * (np.cos(hr + 2) + 3.8)
        p1 = (50000.0 / 13.0 * self._Nc * self._Ncb) * et \
            / np.where(t < 1e-12, 1e-12, t)
        p2 = A / self._Nbb + 0.305
        p3 = 21.0 / 20.0
        sin_h = np.sin(hr)
        cos_h = np.cos(hr)
        # CIE 159:2004 inverse relations (a, b from t, A, h)
        big = np.abs(sin_h) >= np.abs(cos_h)
        a = np.empty_like(J)
        b = np.empty_like(J)
        with np.errstate(divide="ignore", invalid="ignore"):
            # |sin| branch
            p4 = p1 / np.where(sin_h == 0, 1e-12, sin_h)
            bb = (p2 * (2 + p3) * (460.0 / 1403.0)
                  / (p4 + (2 + p3) * (220.0 / 1403.0) * (cos_h / sin_h)
                     - (27.0 / 1403.0) + p3 * (6300.0 / 1403.0)))
            aa = bb * (cos_h / sin_h)
            # |cos| branch
            p5 = p1 / np.where(cos_h == 0, 1e-12, cos_h)
            aa2 = (p2 * (2 + p3) * (460.0 / 1403.0)
                   / (p5 + (2 + p3) * (220.0 / 1403.0)
                      - ((27.0 / 1403.0) - p3 * (6300.0 / 1403.0))
                      * (sin_h / cos_h)))
            bb2 = aa2 * (sin_h / cos_h)
        a[big] = aa[big]
        b[big] = bb[big]
        a[~big] = aa2[~big]
        b[~big] = bb2[~big]
        zero = t < 1e-12
        a[zero] = 0.0
        b[zero] = 0.0
        rgba = np.stack([
            (460.0 * p2 + 451.0 * a + 288.0 * b) / 1403.0,
            (460.0 * p2 - 891.0 * a - 261.0 * b) / 1403.0,
            (460.0 * p2 - 220.0 * a - 6300.0 * b) / 1403.0,
        ], 1)
        x = rgba - 0.1
        mag = (27.13 * np.abs(x)) / np.clip(400.0 - np.abs(x), 1e-9, None)
        rgbp = np.sign(x) * 100.0 / self._FL * mag ** (1.0 / 0.42)
        rgbc = (_MCAT @ (_MHPE_I @ rgbp.T)).T
        rgb = rgbc / self._Dfac[None, :]
        return rgb @ _MCAT_I.T

    def roundtrip_error(self, xyz100: np.ndarray) -> float:
        """Max |XYZ − inv(fwd(XYZ))| — the correctness gate for the inverse."""
        back = self.jab_to_xyz(self.xyz_to_jab(xyz100))
        return float(np.abs(back - xyz100).max())
