"""Gamut surface substrate for the port — Argyll ``gamut->radial`` /
``vector_isect`` equivalents over a point cloud.

Argyll's gamut object is a triangulated sphere-parameterised mesh; the port
uses a dense (hue × inclination) radial table around the same fixed centre
Argyll uses (L 50, a 0, b 0 — ``gamut.h`` default ``cent``), bilinearly
interpolated. This is the same substrate-substitution as rspl → maths-A
fitter: behaviour validated by the end-to-end gates, structure documented.

``radial(p)``: project p onto the surface along the ray from the centre.
``vector_isect(sv, dv)``: ray/surface crossing parameters (min t, max t)
with approximate surface normals — what ``comp_depth`` consumes.
"""
from __future__ import annotations

import numpy as np

CENT = np.array([50.0, 0.0, 0.0])


class GamutSurface:
    def __init__(self, cloud: np.ndarray, nh: int = 90, nb: int = 45) -> None:
        self.nh = nh
        self.nb = nb
        rel = np.asarray(cloud, float) - CENT[None, :]
        r = np.linalg.norm(rel, axis=1)
        # inclination from +L axis (0..pi), hue around the L axis
        incl = np.arccos(np.clip(rel[:, 0] / np.maximum(r, 1e-9), -1, 1))
        hue = np.arctan2(rel[:, 2], rel[:, 1]) % (2 * np.pi)
        hi = np.minimum((hue / (2 * np.pi) * nh).astype(int), nh - 1)
        bi = np.minimum((incl / np.pi * nb).astype(int), nb - 1)
        tab = np.zeros((nh, nb))
        np.maximum.at(tab, (hi, bi), r)
        # fill empty bins from neighbours (hue wraps, inclination clamps)
        for _ in range(max(nh, nb)):
            empty = tab == 0.0
            if not empty.any():
                break
            acc = np.zeros_like(tab)
            cnt = np.zeros_like(tab)
            for sh, ax in ((1, 0), (-1, 0)):
                nb_t = np.roll(tab, sh, axis=ax)
                m = nb_t > 0
                acc[m] += nb_t[m]
                cnt[m] += 1
            for sh in (1, -1):
                nb_t = np.roll(tab, sh, axis=1)
                if sh == 1:
                    nb_t[:, 0] = 0
                else:
                    nb_t[:, -1] = 0
                m = nb_t > 0
                acc[m] += nb_t[m]
                cnt[m] += 1
            fill = empty & (cnt > 0)
            tab[fill] = acc[fill] / cnt[fill]
        # light smoothing to knock down cloud sampling noise
        sm = tab.copy()
        sm = (sm + np.roll(sm, 1, 0) + np.roll(sm, -1, 0)) / 3.0
        inner = sm[:, 1:-1]
        sm[:, 1:-1] = (inner + sm[:, :-2] + sm[:, 2:]) / 3.0
        self.tab = sm

    # ------------------------------------------------------------------
    def _radius(self, dirs: np.ndarray) -> np.ndarray:
        """Bilinear surface radius for unit-ish direction vectors."""
        r = np.linalg.norm(dirs, axis=1)
        incl = np.arccos(np.clip(dirs[:, 0] / np.maximum(r, 1e-9), -1, 1))
        hue = np.arctan2(dirs[:, 2], dirs[:, 1]) % (2 * np.pi)
        fh = hue / (2 * np.pi) * self.nh
        fb = np.clip(incl / np.pi * self.nb - 0.5, 0.0, self.nb - 1.0)
        h0 = fh.astype(int) % self.nh
        h1 = (h0 + 1) % self.nh
        b0 = fb.astype(int)
        b1 = np.minimum(b0 + 1, self.nb - 1)
        wh = fh - fh.astype(int)
        wb = fb - b0
        return ((1 - wh) * (1 - wb) * self.tab[h0, b0]
                + wh * (1 - wb) * self.tab[h1, b0]
                + (1 - wh) * wb * self.tab[h0, b1]
                + wh * wb * self.tab[h1, b1])

    def radial(self, pts: np.ndarray) -> np.ndarray:
        """Project points onto the surface along the ray from the centre."""
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        rr = self._radius(rel)
        return CENT[None, :] + rel * (rr / r)[:, None]

    def nradial(self, pts: np.ndarray) -> np.ndarray:
        """Normalised radius (>1 = outside), Argyll's ``nradial``."""
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.linalg.norm(rel, axis=1)
        return r / np.maximum(self._radius(rel), 1e-9)

    def vector_isect(self, sv: np.ndarray, dv: np.ndarray, samples: int = 64
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Ray p(t)=sv+t·(dv−sv) vs surface: (mint, maxt, n_min, n_max).

        Crossing parameters bracket where the ray is inside the gamut
        (Argyll returns intersection pairs the same way for comp_depth);
        normals approximated from the radial field gradient.
        """
        sv = np.atleast_2d(np.asarray(sv, float))
        dv = np.atleast_2d(np.asarray(dv, float))
        ts = np.linspace(-2.0, 4.0, samples)
        # f(t) > 0 outside the surface
        f = np.empty((len(sv), samples))
        for i, t in enumerate(ts):
            p = sv + t * (dv - sv)
            f[:, i] = self.nradial(p) - 1.0
        inside = f <= 0.0
        mint = np.full(len(sv), np.nan)
        maxt = np.full(len(sv), np.nan)
        for j in range(len(sv)):
            idx = np.flatnonzero(inside[j])
            if len(idx) == 0:
                continue
            # linear refine at the two boundary crossings
            i0, i1 = idx[0], idx[-1]
            if i0 > 0:
                a, b = f[j, i0 - 1], f[j, i0]
                mint[j] = ts[i0 - 1] + (ts[i0] - ts[i0 - 1]) * a / (a - b)
            else:
                mint[j] = ts[0]
            if i1 < samples - 1:
                a, b = f[j, i1], f[j, i1 + 1]
                maxt[j] = ts[i1] + (ts[i1 + 1] - ts[i1]) * a / (a - b)
            else:
                maxt[j] = ts[-1]
        # normals: gradient of the surface at the crossing points
        def normal_at(t):
            p = sv + np.where(np.isnan(t), 0.0, t)[:, None] * (dv - sv)
            eps = 0.5
            g = np.empty_like(p)
            for k in range(3):
                dp = np.zeros(3)
                dp[k] = eps
                g[:, k] = self.nradial(p + dp) - self.nradial(p - dp)
            n = g / np.maximum(np.linalg.norm(g, axis=1), 1e-9)[:, None]
            return n
        return mint, maxt, normal_at(mint), normal_at(maxt)
