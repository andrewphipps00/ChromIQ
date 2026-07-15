"""Guide-vector construction — the ``near_smooth`` core (nearsmth.c
L1809–3970), ported per the flow skeleton in ``portmap.md`` (AGPL-3.0,
Graeme W. Gill — see package ``__init__``).

Documented deviations (each behaviour-gated by the oracle validation):

* the per-point 2D powell in the tangent plane (``m3d``) becomes a batched
  pattern search over the destination surface's (hue, inclination)
  parameterisation — same search space, all points optimised together;
* compression-focused v1: printer destinations are inside typical source
  spaces; the expansion branch (gamxknf) is not taken (usecomp=1,
  useexp=0 is also gammap.c's configuration for perceptual printer maps);
* the closing sub-surface/grid-point machinery is replaced by the maths-A
  warp fit over the guide displacements — which is what gammap.c itself
  does with the guides (rspl fit at PSMOOTH).
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port.cusps import CuspMapping
from workflow.profile_engine.gammap_port.error import aerrf, comperr
from workflow.profile_engine.gammap_port.gamutsurf import CENT, GamutSurface
from workflow.profile_engine.gammap_port.xweights import (ALXPOW, ALXTHR, AO,
                                                          DCO, DXO, RDSM,
                                                          RRDH, RRDL,
                                                          interp_xweights)


def _angles_of(pts: np.ndarray) -> np.ndarray:
    rel = np.atleast_2d(pts) - CENT[None, :]
    r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
    incl = np.arccos(np.clip(rel[:, 0] / r, -1.0, 1.0))
    hue = np.arctan2(rel[:, 2], rel[:, 1])
    return np.stack([hue, incl], 1)


def _points_of(surf: GamutSurface, ang: np.ndarray) -> np.ndarray:
    hue, incl = ang[:, 0], np.clip(ang[:, 1], 1e-4, np.pi - 1e-4)
    d = np.stack([np.cos(incl),
                  np.sin(incl) * np.cos(hue),
                  np.sin(incl) * np.sin(hue)], 1)
    return surf.radial(CENT[None, :] + d)


def _pattern_search(surf: GamutSurface, ang0: np.ndarray, objective,
                    step0: float = 0.35, iters: int = 40) -> np.ndarray:
    """Batched 2D pattern search over (hue, inclination) on the surface."""
    ang = ang0.copy()
    best = objective(_points_of(surf, ang))
    step = np.full(len(ang), step0)
    for _ in range(iters):
        improved = np.zeros(len(ang), dtype=bool)
        for dh, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            trial = ang.copy()
            trial[:, 0] += dh * step
            trial[:, 1] += di * step
            v = objective(_points_of(surf, trial))
            m = v < best
            ang[m] = trial[m]
            best[m] = v[m]
            improved |= m
        step[~improved] *= 0.5
        if (step < 1e-4).all():
            break
    return ang


def near_smooth_guides(src_cloud: np.ndarray, dst_cloud: np.ndarray,
                       xw: np.ndarray, cm: CuspMapping, *,
                       smooth_iters: int = 8,
                       n_guides: int | None = None) -> tuple[np.ndarray,
                                                             np.ndarray]:
    """Compute (source, mapped-destination) guide pairs.

    ``xw``: the (14, 23) expanded weight table; ``cm``: the cusp context.
    Returns ``(sv, dv)`` — raw source surface points and their optimised
    destination targets (both (N, 3) Lab).
    """
    src_surf = GamutSurface(src_cloud)
    dst_surf = GamutSurface(dst_cloud)

    # Guide sources: the source surface bins themselves (≈ vertex set).
    sv = src_surf.radial(src_cloud if n_guides is None
                         else src_cloud[np.random.default_rng(5).choice(
                             len(src_cloud), n_guides, replace=False)])
    sv = np.unique(np.round(sv, 3), axis=0)

    wts = interp_xweights(sv, xw, cm)
    w = wts["w"]
    ra = wts["ra"]
    rl = wts["rl"]

    # Cusp-rotated source (comp_ce per point with its own cusp block).
    # Weight fields vary per point; comp_ce is applied per unique hextant
    # blend via its scalar weights — approximate with the mean cusp block
    # of each point's weights applied pointwise (fields vary smoothly).
    csv = np.empty_like(sv)
    # process in chunks of similar weights: quantise cusp block
    cb = np.round(w[:, :5], 3)
    for key in np.unique(cb, axis=0):
        m = (cb == key[None, :]).all(1)
        csv[m] = cm.comp_ce(sv[m], cusp_weights=tuple(key))

    # White/black pinning factor (comp_naxbf).
    naxbf = cm.comp_naxbf(sv)

    # PASS 1: weighted-nearest points on the destination surface (optfunc1).
    ang0 = _angles_of(csv)
    lxpow = w[:, ALXPOW]
    lxthr = w[:, ALXTHR]

    def obj1(dtp: np.ndarray) -> np.ndarray:
        return aerrf(dtp, csv, ra, lxpow, lxthr)

    ang_wn = _pattern_search(dst_surf, ang0, obj1)
    aodv = _points_of(dst_surf, ang_wn)

    # radially mapped source (drv)
    drv = dst_surf.radial(csv)

    # PASS 2 + smoothing iterations (optfunc2 with neighbour smoothing).
    a_o = w[:, AO]
    dco = w[:, DCO]
    dxo = w[:, DXO]

    # neighbour lists within the relative-smoothing radii (r.rdl L*, r.rdh
    # hue-ish lateral), on the source surface
    rdl = w[:, RRDL]
    rdh = w[:, RRDH]
    dsm = w[:, RDSM]
    dl = np.abs(sv[:, 0][:, None] - sv[:, 0][None, :])
    dlat = np.linalg.norm(sv[:, 1:][:, None, :] - sv[:, 1:][None, :, :],
                          axis=2)
    nbr = (dl <= rdl[:, None]) & (dlat <= rdh[:, None])

    ang = ang_wn.copy()
    dv = aodv.copy()
    target = aodv
    for it in range(smooth_iters):
        def obj2(dtp: np.ndarray) -> np.ndarray:
            # depth ratios per evaluation are expensive; the C computes
            # them inside optfunc2 — here once per iteration at the current
            # dv (documented deviation, gate-checked)
            return comperr(dtp, target, drv, a_o, rl, dco, dxo,
                           dcr, dxr)

        mint, maxt, n_min, n_max = dst_surf.vector_isect(csv, dv)
        vlen = np.linalg.norm(dv - csv, axis=1)
        dcr = np.zeros(len(sv))
        dxr = np.zeros(len(sv))
        ok = ~np.isnan(mint) & ~np.isnan(maxt) & (vlen > 0.1)
        nv = np.zeros_like(dv)
        nv[ok] = (dv[ok] - csv[ok]) / vlen[ok][:, None]
        comp = ok & (mint > 1e-8) & (maxt > -1e-8)
        ang_c = (nv[comp] * n_min[comp]).sum(1) ** 2
        dcr[comp] = ang_c * 2.0 / np.maximum(maxt[comp] + mint[comp] - 2.0,
                                             1e-6)

        ang = _pattern_search(dst_surf, ang, obj2, step0=0.2, iters=25)
        dv_new = _points_of(dst_surf, ang)

        # neighbour smoothing of the mapping vectors (degree dsm); the
        # naxbf factor fades ONLY the smoothing adjustment near W/K
        # (C L3037: blend of dv vs smoothed target by naxbf — the mapping
        # itself is never scaled).
        vec = dv_new - sv
        sm_vec = np.empty_like(vec)
        for j in range(len(sv)):
            sm_vec[j] = vec[nbr[j]].mean(0)
        adj = dsm[:, None] * (sm_vec - vec)
        dv = sv + vec + naxbf[:, None] * adj
        # keep targets on/inside the destination
        out = dst_surf.nradial(dv) > 1.0
        if out.any():
            dv[out] = dst_surf.radial(dv[out])
        target = dv

    return sv, dv
