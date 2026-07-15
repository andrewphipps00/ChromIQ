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

        # fine-tune targets (L1020–1046, BEFORE the symmetry swap): source
        # cs points scaled to the L-curve endpoints, put back in
        # pre-rotated space; dest targets on the straight dest axis
        # (note the C reuses t from the white computation for d_mt_bp)
        t = (swL - sr_cs_bp[0]) / (sr_cs_wp[0] - sr_cs_bp[0])
        self.s_mt_wp = apply_3x4(self.igrot,
                                 sr_cs_bp + t * (sr_cs_wp - sr_cs_bp))
        t = (sbL - sr_cs_wp[0]) / (sr_cs_bp[0] - sr_cs_wp[0])
        self.s_mt_bp = apply_3x4(self.igrot,
                                 sr_cs_wp + t * (sr_cs_bp - sr_cs_wp))
        t = (dwL - dr_cs_bp[0]) / (dr_cs_wp[0] - dr_cs_bp[0])
        self.d_mt_wp = dr_cs_bp + t * (dr_cs_wp - dr_cs_bp)
        self.d_mt_bp = dr_cs_wp + t * (dr_cs_bp - dr_cs_wp)

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
        # gammap.c L1183–1206 (adjust1_wb_func): linearly rescale the
        # fitted curve so black and white map exactly
        awb0 = float(np.interp(sbL, self._lx, self._lv))
        awb1 = float(np.interp(swL, self._lx, self._lv))
        self._lv = (self._lv - awb0) * (dwL - dbL) / (awb1 - awb0) + dbL

    # scat.c smf[0] (1-D optimum log-smoothness table) with its nc/ad axes
    _SMF = np.array([[-5.0, -5.3, -5.2, -4.4, -3.5, -0.8],
                     [-6.4, -5.6, -5.1, -4.5, -4.0, -3.6],
                     [-6.4, -5.9, -5.5, -4.6, -3.9, -3.3],
                     [-6.8, -6.0, -5.6, -4.9, -4.4, -3.7],
                     [-6.9, -6.2, -5.6, -4.9, -4.3, -3.5],
                     [-6.9, -5.9, -5.5, -5.1, -4.7, -4.4]])
    _NCV = np.array([5.0, 10.0, 20.0, 50.0, 100.0, 200.0])
    _ADV = np.array([0.0001, 0.0025, 0.005, 0.0125, 0.025, 0.05])

    def _fit_curve(self, pts: np.ndarray, gres: int = 256,
                   lo: float = -1.0, hi: float = 101.0, vw: float = 100.0,
                   smooth: float = 5.0, avgdev: float = 0.005) -> None:
        """The grey L curve: Argyll's 1-D fit_rspl_w objective, literally.

        Weighted least squares over a gres-point grid (multilinear data
        rows) plus a second-difference curvature penalty with scat.c's
        exact weight: cw = smooth · 10^lsm(nc, ad) · vw · (gres−1)⁴ /
        (gres−2), lsm bilinearly interpolated (in log space) from the smf
        1-D table. Validated against a compiled Argyll rspl on three lpnt
        sets: max |diff| 0.078 with no fitted constants.
        """
        def _axis(val, tab):
            if val <= tab[0]:
                return 0, 1.0
            if val >= tab[-1]:
                return len(tab) - 2, 0.0
            ix = int(np.searchsorted(tab, val, side="right")) - 1
            wt = 1.0 - ((np.log(val) - np.log(tab[ix]))
                        / (np.log(tab[ix + 1]) - np.log(tab[ix])))
            return ix, wt

        ncix, ncw = _axis(float(len(pts)), self._NCV)
        adix, adw = _axis(avgdev, self._ADV)
        lsm = (self._SMF[ncix][adix] * ncw * adw
               + self._SMF[ncix][adix + 1] * ncw * (1 - adw)
               + self._SMF[ncix + 1][adix] * (1 - ncw) * adw
               + self._SMF[ncix + 1][adix + 1] * (1 - ncw) * (1 - adw))
        cw = smooth * 10.0 ** lsm * vw * (gres - 1.0) ** 4 / (gres - 2.0)

        x, y, w = pts[:, 0], pts[:, 1], pts[:, 2]
        h = (hi - lo) / (gres - 1)
        f = (x - lo) / h
        i0 = np.clip(f.astype(int), 0, gres - 2)
        t = f - i0
        A = np.zeros((gres, gres))
        b = np.zeros(gres)
        for k in range(len(x)):
            r = np.zeros(gres)
            r[i0[k]] = 1 - t[k]
            r[i0[k] + 1] = t[k]
            A += w[k] * np.outer(r, r)
            b += w[k] * y[k] * r
        D = np.zeros((gres - 2, gres))
        for j in range(gres - 2):
            D[j, j], D[j, j + 1], D[j, j + 2] = 1.0, -2.0, 1.0
        A += cw * (D.T @ D)
        self._lx = np.linspace(lo, hi, gres)
        self._lv = np.linalg.solve(A, b)

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
