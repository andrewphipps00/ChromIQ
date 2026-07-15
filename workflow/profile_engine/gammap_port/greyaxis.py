"""Grey-axis alignment + 1-D L map — gammap.c L700–1200, literal port
(AGPL-3.0, Graeme W. Gill — see package ``__init__``).

Perceptual intent parameters (xicc.c "p — Perceptual", verbatim):
greymf = glumwcpf = glumwexf = glumbcpf = glumbexf = glumknf = 1.0,
bph = bendBP, gamcpf = 1.0, gamexf = 0.0, gamcknf = 1.0.

Composition (gammap.c ``domap``): out = map3D([greyL(rot(x).L), rot(x).a,
rot(x).b]) — the rotation and L map run BEFORE the 3-D mapping, and the
source gamut handed to near_smooth is likewise pre-transformed.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port.geom import (apply_3x4, rot_mat,
                                                      vec_rot_mat)


class GreyAxis:
    def __init__(self, s_cs_wp, s_cs_bp, d_cs_wp, d_cs_bp, dest_surf, *,
                 greymf=1.0, glumwcpf=1.0, glumwexf=1.0, glumbcpf=1.0,
                 glumbexf=1.0, glumknf=1.0, bend_bp=True) -> None:
        s_cs_wp = np.asarray(s_cs_wp, float)
        s_cs_bp = np.asarray(s_cs_bp, float)
        d_cs_wp = np.asarray(d_cs_wp, float)
        d_cs_bp = np.asarray(d_cs_bp, float)

        def same_l(l0, p0, p1):
            t = (l0 - p0[0]) / (p1[0] - p0[0])
            return p0 + t * (p1 - p0)

        # L741–748: source wb at destination L values
        sswp = same_l(d_cs_wp[0], s_cs_bp, s_cs_wp)
        ssbp = same_l(d_cs_bp[0], s_cs_wp, s_cs_bp)
        # L751–755: raw target = greymf blend (1.0 → destination cs points)
        dr_cs_wp = greymf * d_cs_wp + (1.0 - greymf) * sswp
        dr_cs_bp = greymf * d_cs_bp + (1.0 - greymf) * ssbp
        # fully adapted targets; greymf > 0.99 special case (L764–770)
        fawp = d_cs_wp.copy()
        fabp = d_cs_bp.copy()
        # half adapted: white rotation applied to source black (L846–851)
        wrot = rot_mat(sswp, dr_cs_wp)
        habp = wrot @ s_cs_bp
        hawp = dr_cs_wp.copy()
        # clip the half-adapted axis to the destination (vector_isect)
        mint, maxt, _, _ = dest_surf.vector_isect(habp[None, :],
                                                  hawp[None, :])
        if not np.isnan(mint[0]):
            d = hawp - habp
            habp2 = habp + max(mint[0], 0.0) * d
            hawp2 = habp + min(maxt[0], 1.0) * d
        else:
            habp2, hawp2 = habp, hawp

        if bend_bp:
            # bendBP (L834–847): fully adapted targets; bent black = the
            # half-adapted black extended to the target L
            dr_cs_wp = fawp
            dr_cs_bp = fabp
            t = (dr_cs_bp[0] - dr_cs_wp[0]) / (habp2[0] - dr_cs_wp[0])
            self.dr_be_bp = dr_cs_wp + t * (habp2 - dr_cs_wp)
        else:
            self.dr_be_bp = dr_cs_bp.copy()

        # L938–962: same-L source white + equal-length source black
        sswp = same_l(dr_cs_wp[0], s_cs_bp, s_cs_wp)
        svl = np.linalg.norm(sswp - s_cs_bp)
        dvl = np.linalg.norm(dr_cs_wp - dr_cs_bp)
        ssbp = sswp + dvl / svl * (s_cs_bp - sswp)
        # the general grey-axis rotation (rotation FIRST on all source pts)
        self.grot = vec_rot_mat(sswp, ssbp, dr_cs_wp, dr_cs_bp)
        self.igrot = vec_rot_mat(dr_cs_wp, dr_cs_bp, sswp, ssbp)
        sr_cs_wp = apply_3x4(self.grot, s_cs_wp)
        sr_cs_bp = apply_3x4(self.grot, s_cs_bp)
        self.dr_cs_wp = dr_cs_wp
        self.dr_cs_bp = dr_cs_bp

        # ---- 1-D L map targets (L975–1010) ----
        if sr_cs_wp[0] <= dr_cs_wp[0]:
            swL = sr_cs_wp[0]
            dwL = glumwexf * dr_cs_wp[0] + (1.0 - glumwexf) * sr_cs_wp[0]
        elif sr_cs_wp[0] > dr_cs_wp[0]:
            swL = (1.0 - glumwcpf) * dr_cs_wp[0] + glumwcpf * sr_cs_wp[0]
            dwL = dr_cs_wp[0]
        else:
            swL = dwL = sr_cs_wp[0]
        if sr_cs_bp[0] >= dr_cs_bp[0]:
            sbL = sr_cs_bp[0]
            dbL = glumbexf * dr_cs_bp[0] + (1.0 - glumbexf) * sr_cs_bp[0]
        elif sr_cs_bp[0] < dr_cs_bp[0]:
            sbL = (1.0 - glumbcpf) * dr_cs_bp[0] + glumbcpf * sr_cs_bp[0]
            dbL = dr_cs_bp[0]
        else:
            sbL = dbL = sr_cs_bp[0]

        # symmetry swap (L1040–1046); the fitted curve is then inverted
        self.revrspl = (dwL - dbL) > (swL - sbL)
        if self.revrspl:
            swL, dwL = dwL, swL
            sbL, dbL = dbL, sbL

        # lpnts (L1048–1130): endpoints w10, centre w0.5, knee points
        cppos, kpwpos, kpbpos = 0.50, 0.30, 0.15
        cplv = cppos * (swL - sbL) + sbL
        kwl = kpwpos * (cplv - swL) + swL
        kbl = kpbpos * (cplv - sbL) + sbL
        kwx = 0.6 * (dbL - sbL) + 1.0 * (swL - dwL)
        kbx = 1.0 * (dbL - sbL) + 0.6 * (swL - dwL)
        kwv = (dwL + kwx - cplv) * (kwl - cplv) / (swL - cplv) + cplv
        kwv = min(kwv, dwL)
        kbv = (dbL - kbx - cplv) * (kbl - cplv) / (sbL - cplv) + cplv
        kbv = max(kbv, dbL)
        pts = np.array([[swL, dwL, 10.0], [sbL, dbL, 10.0],
                        [cplv, cplv, 0.5],
                        [kwl, kwv, glumknf * glumknf],
                        [kbl, kbv, (1.5 * glumknf) ** 2]])
        self._fit_curve(pts)

    def _fit_curve(self, pts: np.ndarray, gres: int = 256,
                   lam: float = 2.0) -> None:
        """1-D weighted smoothing fit (the grey rspl, smoothing 5.0)."""
        lo = min(pts[:, 0].min(), pts[:, 1].min()) - 1.0
        hi = max(pts[:, 0].max(), pts[:, 1].max()) + 1.0
        x = np.linspace(lo, hi, gres)
        # weighted least squares on grid nodes + curvature penalty; strong
        # endpoint weights make it near-exact where it matters
        idxf = (pts[:, 0] - lo) / (hi - lo) * (gres - 1)
        i0 = np.clip(idxf.astype(int), 0, gres - 2)
        fr = idxf - i0
        a = np.zeros((gres, gres))
        b = np.zeros(gres)
        for k in range(len(pts)):
            w = pts[k, 2]
            row = np.zeros(gres)
            row[i0[k]] = 1 - fr[k]
            row[i0[k] + 1] = fr[k]
            a += w * np.outer(row, row)
            b += w * row * pts[k, 1]
        d2 = np.zeros((gres - 2, gres))
        for j in range(gres - 2):
            d2[j, j], d2[j, j + 1], d2[j, j + 2] = 1.0, -2.0, 1.0
        a += lam * d2.T @ d2 * ((gres - 1) / (hi - lo)) ** 0  # scale-free
        # anchor the overall linear trend so extrapolation stays sane
        a += 1e-8 * np.eye(gres)
        v = np.linalg.solve(a + 1e-9 * np.eye(gres), b)
        self._lo, self._hi = lo, hi
        self._lx, self._lv = x, v

    def grey_l(self, l_in: np.ndarray) -> np.ndarray:
        if self.revrspl:
            # fitted the swapped (inverse) curve: invert by interpolation
            return np.interp(l_in, self._lv, self._lx)
        return np.interp(l_in, self._lx, self._lv)

    def pre_map(self, lab: np.ndarray) -> np.ndarray:
        """rot + grey-L — the transform applied before the 3-D mapping and
        to the source gamut handed to near_smooth (domap order)."""
        rin = np.atleast_2d(apply_3x4(self.grot, np.atleast_2d(lab)))
        out = rin.copy()
        out[:, 0] = self.grey_l(rin[:, 0])
        return out
