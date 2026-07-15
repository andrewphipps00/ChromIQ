"""Cusp mapping machinery — nearsmth.c ``init_ce``/``comp_ce``/``comp_naxbf``
/``comp_lvc``/``inv_comp_ce`` (L596–1160), translated per the transcription
in ``portmap.md`` (AGPL-3.0, Graeme W. Gill — see package ``__init__``).

Faithfulness notes:

* the ``src_adj`` anti-tamper canary in ``init_ce`` resolves to exactly 1.0
  (computed) — carried as the constant it is;
* ``comp_ce`` blends the **hue** component with the *chroma* weight
  ``cw_c`` (C L957–966) — an Argyll quirk preserved deliberately; ``cw_h``
  only gates activation and receives the twist scaling;
* ``inv_comp_ce`` inverts numerically in the C too (powell on a squared
  distance); here a damped fixed-point iteration with the same objective.

The C obtains the six cusps from ``gamut->getcusps``; the port computes
them from the gamut point cloud as the max-chroma point per 60°-hue sector
(:func:`cusps_from_cloud`) — same definition, cloud-based.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port.geom import (apply_3x4, lab_to_lch,
                                                      lch_to_lab, plane_dist,
                                                      plane_eqn, vec_rot_mat)

# init_ce's src_adj log-sum (nearsmth.c L606–625) — resolves to exactly 1.0.
_SAVAL = 1.0


def cusps_from_cloud(cloud: np.ndarray) -> np.ndarray | None:
    """(6, 3) Lab cusps: max-chroma point per 60° hue sector (R,Y,G,C,B,M
    order like Argyll's getcusps), or None when a sector is empty."""
    h = np.degrees(np.arctan2(cloud[:, 2], cloud[:, 1])) % 360.0
    c = np.hypot(cloud[:, 1], cloud[:, 2])
    centres = (30.0, 90.0, 150.0, 210.0, 270.0, 330.0)
    out = np.empty((6, 3))
    for k, ctr in enumerate(centres):
        m = np.abs(((h - ctr + 180.0) % 360.0) - 180.0) <= 30.0
        if not m.any():
            return None
        out[k] = cloud[m][np.argmax(c[m])]
    return out


class CuspMapping:
    """Per-(source, destination) cusp alignment context (``smthopt`` part)."""

    def __init__(self, src_cusps: np.ndarray | None,
                 dst_cusps: np.ndarray | None,
                 src_white: np.ndarray, src_black: np.ndarray,
                 dst_white: np.ndarray, dst_black: np.ndarray) -> None:
        self.docusp = src_cusps is not None and dst_cusps is not None
        wb = ((np.asarray(src_white, float), np.asarray(src_black, float)),
              (np.asarray(dst_white, float), np.asarray(dst_black, float)))
        cusps_in = (src_cusps, dst_cusps)
        self.rot = [None, None]
        self.irot = [None, None]
        self.cusps = [None, None]       # [sd][k∈0..5]=cusp, 6=W, 7=K, 8=grey
        self.cusp_lab = [None, None]
        self.cusp_lch = [None, None]
        self.cusp_pe = [[None] * 6, [None] * 6]
        self.cusp_bc = [[[None, None] for _ in range(6)] for _ in range(2)]

        ta = np.array([100.0 * _SAVAL, 0.0, 0.0])
        tc = np.zeros(3)
        for sd in range(2):
            white, black = wb[sd]
            self.rot[sd] = vec_rot_mat(white, black, ta, tc)
            self.irot[sd] = vec_rot_mat(ta, tc, white, black)

            pts = np.zeros((9, 3))
            if self.docusp:
                pts[:6] = np.asarray(cusps_in[sd], float)
            pts[6] = white
            pts[7] = black
            if self.docusp:
                al = float(pts[:6, 0].mean())
                al = (al - black[0]) / max(white[0] - black[0], 1e-9)
                al = min(max(al, 0.0), 1.0)
            else:
                al = 0.5
            # icmBlend3(grey, white, black, al): al=0 → white, 1 → black
            pts[8] = (1.0 - al) * white + al * black
            self.cusps[sd] = pts
            lab = apply_3x4(self.rot[sd], pts)
            self.cusp_lab[sd] = lab
            self.cusp_lch[sd] = lab_to_lch(lab)

            if not self.docusp:
                continue
            for k in range(6):
                m = (k + 1) % 6
                eq = plane_eqn(lab[8], lab[m], lab[k])
                if eq is None:
                    raise ValueError("degenerate cusp plane")
                self.cusp_pe[sd][k] = eq
                for n in range(2):
                    bc = np.stack([lab[k] - lab[8], lab[m] - lab[8],
                                   lab[6 + n] - lab[8]], 0).T
                    # C: transpose then (src only) invert. Building columns
                    # directly gives the transposed form.
                    if sd == 0:
                        bc = np.linalg.inv(bc)
                    self.cusp_bc[sd][k][n] = bc

    # ------------------------------------------------------------------
    def _locate(self, lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Hue segment c0 and light/dark ld per point (C L885–908)."""
        lch = np.atleast_2d(lab_to_lch(lab))
        npts = len(lch)
        c0 = np.full(npts, -1, dtype=int)
        for k in range(6):
            m = (k + 1) % 6
            h0 = self.cusp_lch[0][k][2]
            h1 = self.cusp_lch[0][m][2]
            sh = lch[:, 2].copy()
            hh1 = h1
            if h1 < h0:
                sh = np.where(sh < h1, sh + 360.0, sh)
                hh1 = h1 + 360.0
            hit = (sh >= h0 - 1e-12) & (sh < hh1 + 1e-12) & (c0 < 0)
            c0[hit] = k
        c0[c0 < 0] = 0                       # C errors out; clamp instead
        ld = np.zeros(npts, dtype=int)
        for k in range(6):
            m = c0 == k
            if m.any():
                d = plane_dist(self.cusp_pe[0][k], np.atleast_2d(lab)[m])
                ld[m] = np.where(np.atleast_1d(d) >= 0.0, 0, 1)
        return c0, ld

    def comp_ce(self, pts: np.ndarray, cusp_weights=None) -> np.ndarray:
        """Cusp-aligned source transform (C L842–974), vectorised.

        ``cusp_weights``: (cw_l, cw_c, cw_h, twist_power, chroma_expand)
        from the intent weight table's cusp block; None = 100% (all 1.0).
        """
        pts = np.atleast_2d(np.asarray(pts, float))
        if cusp_weights is None:
            cw_l = cw_c = cw_h = ctw = ccx0 = 1.0
        else:
            cw_l, cw_c, cw_h, ctw, ccx0 = (float(v) for v in cusp_weights)
        if not self.docusp or (cw_l <= 0 and cw_c <= 0 and cw_h <= 0
                               and ccx0 <= 0):
            return pts.copy()

        lab = apply_3x4(self.rot[0], pts)
        lab = np.atleast_2d(lab)
        c0, ld = self._locate(lab)
        bb = np.empty_like(lab)
        mlab = np.empty_like(lab)
        for k in range(6):
            for n in range(2):
                m = (c0 == k) & (ld == n)
                if not m.any():
                    continue
                rel = lab[m] - self.cusp_lab[0][8][None, :]
                b = rel @ self.cusp_bc[0][k][n].T
                bb[m] = b
                mlab[m] = b @ self.cusp_bc[1][k][n].T \
                    + self.cusp_lab[1][8][None, :]

        tww = np.minimum(np.abs(bb[:, 0] + bb[:, 1]), 1.0)
        ccx = 1.0 + (ccx0 - 1.0) * tww
        tpw = np.ones_like(tww) if ctw <= 0.0 else tww ** ctw
        vl = cw_l * tpw
        vc = cw_c * tpw

        mlch = np.atleast_2d(lab_to_lch(mlab))
        olch = np.atleast_2d(lab_to_lch(np.atleast_2d(
            apply_3x4(self.rot[1], pts))))

        out = np.empty_like(mlch)
        out[:, 0] = vl * mlch[:, 0] + (1.0 - vl) * olch[:, 0]
        out[:, 1] = vc * mlch[:, 1] + (1.0 - vc) * olch[:, 1]
        # C L959–963: put the two hues on the same side (±360) first.
        mh0 = mlch[:, 2]
        oh0 = olch[:, 2]
        far = np.abs(oh0 - mh0) > 180.0
        oh = np.where(far & (oh0 < mh0), oh0 + 360.0, oh0)
        mh = np.where(far & (oh0 >= mh0), mh0 + 360.0, mh0)
        # C quirk preserved: the hue blend uses cw_c, not cw_h (L957–966).
        hh = vc * mh + (1.0 - vc) * oh
        out[:, 2] = np.where(hh >= 360.0, hh - 360.0, hh)
        out[:, 1] *= ccx
        return np.atleast_2d(apply_3x4(self.irot[1],
                                       np.atleast_2d(lch_to_lab(out))))

    # ------------------------------------------------------------------
    def comp_naxbf(self, pts: np.ndarray) -> np.ndarray:
        """Neutral-axis blend factor (C L974–1008): 0 at W/K, →1 at grey."""
        pts = np.atleast_2d(np.asarray(pts, float))
        rin = np.atleast_2d(apply_3x4(self.rot[0], pts))
        grey_l = self.cusp_lab[0][8][0]
        d_w = np.linalg.norm(self.cusp_lab[0][6][None, :] - rin, axis=1)
        d_k = np.linalg.norm(self.cusp_lab[0][7][None, :] - rin, axis=1)
        ll = np.where(rin[:, 0] >= grey_l,
                      1.0 - d_w / max(100.0 - grey_l, 1e-9),
                      1.0 - d_k / max(grey_l, 1e-9))
        return np.sqrt(1.0 - np.clip(ll, 0.0, 1.0))

    def comp_lvc(self, pts: np.ndarray) -> np.ndarray:
        """L-blend value (C L1010–1085): 0 at cusp-local grey, +1 at white
        L, −1 at black L."""
        pts = np.atleast_2d(np.asarray(pts, float))
        if self.docusp:
            lab = np.atleast_2d(apply_3x4(self.rot[0], pts))
            c0, ld = self._locate(lab)
            lg = np.empty(len(pts))
            for k in range(6):
                for n in range(2):
                    m = (c0 == k) & (ld == n)
                    if not m.any():
                        continue
                    rel = lab[m] - self.cusp_lab[0][8][None, :]
                    b = rel @ self.cusp_bc[0][k][n].T
                    c1 = (k + 1) % 6
                    lg[m] = (self.cusps[0][8][0]
                             + b[:, 0] * (self.cusps[0][k][0]
                                          - self.cusps[0][8][0])
                             + b[:, 1] * (self.cusps[0][c1][0]
                                          - self.cusps[0][8][0]))
        else:
            lg = np.full(len(pts), self.cusps[0][8][0])
        white_l = self.cusps[0][6][0]
        black_l = self.cusps[0][7][0]
        up = (pts[:, 0] - lg) / np.maximum(white_l - lg, 1e-9)
        dn = -(pts[:, 0] - lg) / np.minimum(black_l - lg, -1e-9)
        return np.where(pts[:, 0] > lg, up, dn)

    def inv_comp_ce(self, pts: np.ndarray, cusp_weights=None,
                    iters: int = 30) -> np.ndarray:
        """Numeric inverse of :meth:`comp_ce` (the C also inverts
        numerically, via powell on the squared distance)."""
        pts = np.atleast_2d(np.asarray(pts, float))
        x = pts.copy()
        for _ in range(iters):
            err = pts - self.comp_ce(x, cusp_weights)
            x = x + 0.7 * err
            if np.abs(err).max() < 1e-6:
                break
        return x
