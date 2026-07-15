"""3-D scattered-data rspl fit — rspl/scat.c fit_rspl_w, literal port
(AGPL-3.0, Graeme W. Gill — see package ``__init__``).

The objective per output channel f (SMOOTH2 undef, V17 second-order):

    E(u) = Σ_n w_n (Σ_j φ_j(x_n) u_j − y_nf)²
         + cw_f · Σ_axes Σ_i (u[i−1] − 2 u[i] + u[i+1])²

with φ = multilinear basis on the gres³ grid over [il, ih] and

    cw_f = smooth · 10^lsm(nc, ad) · vw_f · (mres−1)⁴ / Π(gres−2)

where lsm is log-bilinear from scat.c's smf[di] table (nc = ndp^(1/di)),
vw_f = output value range (the "incorrect but built into the tables"
d.vw scale) and mres the geometric-mean grid res.  The 1-D specialisation
of the same expressions is validated against a compiled Argyll rspl to
max 0.078 (greyaxis.py); this module solves the identical normal
equations matrix-free with Jacobi-preconditioned conjugate gradients
(Argyll's multigrid is just a solver for the same minimum).
"""
from __future__ import annotations

import numpy as np

# scat.c smf tables: log10 optimal smoothness by [nc index][ad index]
_SMF = {
    1: (np.array([5.0, 10.0, 20.0, 50.0, 100.0, 200.0]),
        np.array([0.0001, 0.0025, 0.005, 0.0125, 0.025, 0.05]),
        np.array([[-5.0, -5.3, -5.2, -4.4, -3.5, -0.8],
                  [-6.4, -5.6, -5.1, -4.5, -4.0, -3.6],
                  [-6.4, -5.9, -5.5, -4.6, -3.9, -3.3],
                  [-6.8, -6.0, -5.6, -4.9, -4.4, -3.7],
                  [-6.9, -6.2, -5.6, -4.9, -4.3, -3.5],
                  [-6.9, -5.9, -5.5, -5.1, -4.7, -4.4]])),
    3: (np.array([2.92, 3.68, 4.22, 5.0, 6.3, 7.94, 10.0, 12.6, 20.0,
                  50.0]),
        np.array([0.0001, 0.0025, 0.005, 0.0125, 0.025, 0.05]),
        np.array([[-5.2, -5.0, -5.0, -4.9, -3.6, -2.2],
                  [-5.5, -5.6, -5.6, -5.2, -4.4, -2.4],
                  [-4.7, -4.8, -5.7, -5.9, -5.9, -2.3],
                  [-4.1, -4.1, -5.0, -3.8, -3.4, -2.6],
                  [-4.8, -4.6, -4.6, -4.1, -3.8, -3.4],
                  [-4.7, -4.7, -4.7, -3.8, -3.3, -2.9],
                  [-4.7, -4.8, -4.6, -3.9, -3.4, -3.0],
                  [-5.2, -4.7, -4.4, -4.0, -3.4, -2.9],
                  [-5.5, -5.0, -4.3, -3.6, -3.1, -2.8],
                  [-5.1, -4.7, -4.3, -3.8, -3.3, -2.8]])),
}


def _axis_lookup(val: float, tab: np.ndarray) -> tuple[int, float]:
    """scat.c opt_smooth index+weight: ratiometric (log) interpolation."""
    if val <= tab[0]:
        return 0, 1.0
    if val >= tab[-1]:
        return len(tab) - 2, 0.0
    ix = int(np.searchsorted(tab, val, side="right")) - 1
    wt = 1.0 - ((np.log(val) - np.log(tab[ix]))
                / (np.log(tab[ix + 1]) - np.log(tab[ix])))
    return ix, wt


def opt_smooth(di: int, ndp: int, avgdev: float) -> float:
    """10^lsm from the smf table (WITHOUT the d.vw output-range scale)."""
    ncv, adv, smf = _SMF[di]
    nc = float(ndp) ** (1.0 / di)
    ncix, ncw = _axis_lookup(nc, ncv)
    adix, adw = _axis_lookup(avgdev, adv)
    lsm = (smf[ncix][adix] * ncw * adw
           + smf[ncix][adix + 1] * ncw * (1 - adw)
           + smf[ncix + 1][adix] * (1 - ncw) * adw
           + smf[ncix + 1][adix + 1] * (1 - ncw) * (1 - adw))
    return 10.0 ** lsm


class Rspl3:
    """fit_rspl_w(3→3) equivalent: fit on construction, then interp()."""

    def __init__(self, pnts: np.ndarray, vals: np.ndarray, w: np.ndarray,
                 il: np.ndarray, ih: np.ndarray, *, gres: int = 29,
                 smooth: float = 2.0, avgdev: float = 0.005,
                 ol: np.ndarray | None = None, oh: np.ndarray | None = None,
                 tol: float = 1e-7, maxiter: int = 3000) -> None:
        pnts = np.asarray(pnts, float)
        vals = np.asarray(vals, float)
        w = np.asarray(w, float)
        self.il = il = np.asarray(il, float)
        self.ih = ih = np.asarray(ih, float)
        self.gres = gres
        n = len(pnts)

        # output normalisation range: setup values expanded by the data
        # (scat.c L505–590: vl/vw from vlow/vhigh then data min/max)
        vl = np.minimum(vals.min(0), 0.0 if ol is None else ol)
        vh = np.maximum(vals.max(0), 1.0 if oh is None else oh)
        vw = vh - vl

        # index-space coordinates + trilinear corner weights
        f = (pnts - il[None, :]) / (ih - il)[None, :] * (gres - 1)
        i0 = np.clip(f.astype(int), 0, gres - 2)
        t = f - i0
        corners = []       # (flat grid index, weight) per corner
        for c in range(8):
            cx, cy, cz = (c >> 2) & 1, (c >> 1) & 1, c & 1
            idx = ((i0[:, 0] + cx) * gres + (i0[:, 1] + cy)) * gres \
                + (i0[:, 2] + cz)
            wt = (np.where(cx, t[:, 0], 1 - t[:, 0])
                  * np.where(cy, t[:, 1], 1 - t[:, 1])
                  * np.where(cz, t[:, 2], 1 - t[:, 2]))
            corners.append((idx, wt))
        self._corners = corners

        # curvature weight cw_f: smooth · smval · vw_f · rsm
        smval = opt_smooth(3, n, avgdev)
        mres = gres                       # geometric mean of equal gres
        nigc = (gres - 2) ** 3
        rsm = (mres - 1.0) ** 4 / nigc
        cw = smooth * smval * vw * rsm    # per output channel

        gno = gres ** 3
        shape = (gres, gres, gres)

        def matvec(x, cwf):
            y = np.zeros(gno)
            # data term: Σ w_n φ φᵀ x
            proj = np.zeros(n)
            for idx, wt in corners:
                proj += wt * x[idx]
            proj *= w
            for idx, wt in corners:
                np.add.at(y, idx, wt * proj)
            # curvature term: cw · DᵀD x along each axis
            g = x.reshape(shape)
            acc = np.zeros(shape)
            for ax in range(3):
                gm = np.moveaxis(g, ax, 0)
                d = gm[:-2] - 2 * gm[1:-1] + gm[2:]        # D x
                dt = np.zeros_like(gm)                     # Dᵀ (D x)
                dt[:-2] += d
                dt[1:-1] -= 2 * d
                dt[2:] += d
                acc += np.moveaxis(dt, 0, ax)
            return y + cwf * acc.ravel()

        # Jacobi diagonal: data Σ w φ² + curvature row diagonal
        diag_data = np.zeros(gno)
        for idx, wt in corners:
            np.add.at(diag_data, idx, w * wt * wt)
        dcurv = np.zeros(shape)
        for ax in range(3):
            dm = np.moveaxis(dcurv, ax, 0)
            dm[:-2] += 1.0
            dm[1:-1] += 4.0
            dm[2:] += 1.0
        diag_curv = dcurv.ravel()

        self._grids = []
        for fch in range(vals.shape[1]):
            b = np.zeros(gno)
            wy = w * vals[:, fch]
            for idx, wt in corners:
                np.add.at(b, idx, wt * wy)
            cwf = cw[fch]
            diag = diag_data + cwf * diag_curv
            diag[diag < 1e-12] = 1e-12
            # preconditioned CG from a flat-field start
            x = np.full(gno, vals[:, fch].mean())
            r = b - matvec(x, cwf)
            z = r / diag
            p = r / diag
            rz = r @ z
            bnorm = max(np.linalg.norm(b), 1e-12)
            for _ in range(maxiter):
                Ap = matvec(p, cwf)
                alpha = rz / max(p @ Ap, 1e-300)
                x += alpha * p
                r -= alpha * Ap
                if np.linalg.norm(r) / bnorm < tol:
                    break
                z = r / diag
                rz_new = r @ z
                p = z + (rz_new / rz) * p
                rz = rz_new
            self._grids.append(x.reshape(shape))

    def interp(self, pnts: np.ndarray) -> np.ndarray:
        pnts = np.atleast_2d(np.asarray(pnts, float))
        gres = self.gres
        f = (pnts - self.il[None, :]) / (self.ih - self.il)[None, :] \
            * (gres - 1)
        f = np.clip(f, 0.0, gres - 1.0)
        i0 = np.clip(f.astype(int), 0, gres - 2)
        t = f - i0
        out = np.zeros((len(pnts), len(self._grids)))
        for c in range(8):
            cx, cy, cz = (c >> 2) & 1, (c >> 1) & 1, c & 1
            wt = (np.where(cx, t[:, 0], 1 - t[:, 0])
                  * np.where(cy, t[:, 1], 1 - t[:, 1])
                  * np.where(cz, t[:, 2], 1 - t[:, 2]))
            for fch, g in enumerate(self._grids):
                out[:, fch] += wt * g[i0[:, 0] + cx, i0[:, 1] + cy,
                                      i0[:, 2] + cz]
        return out
