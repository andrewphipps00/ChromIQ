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
                       n_guides: int | None = None
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute (source, mapped-destination) guide pairs.

    ``xw``: the (14, 23) expanded weight table; ``cm``: the cusp context.
    Returns ``(sv, dv)`` — raw source surface points and their optimised
    destination targets (both (N, 3) Lab).
    """
    src_surf = GamutSurface(src_cloud)
    dc_surf = GamutSurface(dst_cloud)

    # Compression target = INTERSECTION of source and destination
    # (nexpintersect, C +109): per-direction min radius. With useexp=0 the
    # destination's bulges beyond the source are never mapped onto.
    dst_surf = GamutSurface(dst_cloud)
    dst_surf.tab = np.minimum(dst_surf.tab, src_surf.tab)

    # Guide sources: the source surface bins themselves (≈ vertex set).
    sv = src_surf.radial(src_cloud if n_guides is None
                         else src_cloud[np.random.default_rng(5).choice(
                             len(src_cloud), n_guides, replace=False)])
    sv = np.unique(np.round(sv, 3), axis=0)

    # Null mapping for source points inside the target (C: "Rejecting
    # point because it's inside destination" — they aren't optimised);
    # they re-enter the warp fit as identity pairs.
    inside = dst_surf.nradial(sv) <= 1.0 + 1e-4
    null_sv = sv[inside]
    sv = sv[~inside]

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

    # Sub-surface knee vectors (C +1581, compression branch, gamcknf=1.1
    # for the perceptual intent — xicc.c): each moved guide spawns weighted
    # sub-rows that pull the fitted map's interior response inward, which
    # is how colprof's net surface targets land INSIDE the surface
    # (measured: 66% of its plotted guides at nradial < 0.98).
    gamcknf = 1.1
    mv = dv - sv
    ml = np.linalg.norm(mv, axis=1)
    moved = ml > 2.0
    sub_s = []
    sub_t = []
    sub_w = []
    if moved.any():
        mint, maxt, n_min, n_max = dst_surf.vector_isect(sv[moved], dv[moved])
        # neutral-axis point: closest point on the W-K axis to the mapping
        # line, blended 0.5 with the horizontal-L point, clipped (C exact).
        wpt = cm.cusps[1][6]
        bpt = cm.cusps[1][7]
        axis = wpt - bpt
        for k, i in enumerate(np.flatnonzero(moved)):
            if np.isnan(mint[k]) or not (mint[k] >= -1e-8 and maxt[k] > 1e-8):
                continue
            d_line = mv[i] / ml[i]
            # line-line closest point on the neutral axis
            w0 = sv[i] - bpt
            a11 = axis @ axis
            a12 = axis @ d_line
            b1 = axis @ w0
            b2 = d_line @ w0
            den = a11 - a12 * a12
            t_ax = (b1 - a12 * b2) / den if abs(den) > 1e-9 else 0.5
            nap = bpt + np.clip(t_ax, 0.0, 1.0) * axis
            nap = 0.5 * nap + 0.5 * np.array([sv[i][0], nap[1], nap[2]])
            nap[0] = np.clip(nap[0], bpt[0], wpt[0])
            adepth2 = np.linalg.norm(nap - sv[i])
            adepth1 = ml[i] * 0.5 * (maxt[k] + mint[k] - 2.0)
            adepth = min(adepth1, adepth2) * 0.9
            if adepth1 < 0.5 * adepth2 or adepth <= 0:
                continue
            sknf = gamcknf * 0.6
            sv2 = dv[i]
            mml = ml[i] * (1.0 - sknf)
            adepth *= (1.0 - sknf)
            sml = min(mml, adepth)
            dv2 = sv2 + d_line * sml
            natarg = nap - sv2
            nn = np.linalg.norm(natarg)
            if nn > 1e-9:
                natarg = sv2 + natarg / nn * sml
                dv2 = (1.0 - sml / adepth2) * dv2 + (sml / adepth2) * natarg
            sub_s.append(sv2)
            sub_t.append(dv2)
            sub_w.append(0.7)
            sd3 = 0.4 * dv2 + 0.6 * nap
            sub_s.append(sv2)
            sub_t.append(sd3)
            sub_w.append(0.4 * gamcknf)

    sv_all = np.vstack([sv, null_sv] + ([np.array(sub_s)] if sub_s else []))
    dv_all = np.vstack([dv, null_sv] + ([np.array(sub_t)] if sub_t else []))
    w_all = np.concatenate([np.ones(len(sv) + len(null_sv)),
                            np.array(sub_w) if sub_w else np.empty(0)])
    return sv_all, dv_all, w_all


def build_neighbours(sv: np.ndarray, rdl: np.ndarray, rdh: np.ndarray):
    """Gaussian-filter neighbourhoods (C +350, exact): ellipse metric with
    per-point radii, opposite-hue exclusion, radius growth ×1.5 until ≥8
    neighbours, smoothstep weights normalised to 1."""
    n = len(sv)
    idx_list = []
    w_list = []
    rw_list = []
    for i in range(n):
        il = max(rdl[i], 1e-3)
        ih = max(rdh[i], 1e-3)
        for _ in range(10):
            dot = sv[:, 1] * sv[i, 1] + sv[:, 2] * sv[i, 2]
            dd = (((sv[:, 0] - sv[i, 0]) / il) ** 2
                  + ((sv[:, 1] - sv[i, 1]) / ih) ** 2
                  + ((sv[:, 2] - sv[i, 2]) / ih) ** 2)
            m = (dot >= 0.0) & (dd <= 1.0)
            if m.sum() >= 8:
                break
            il *= 1.5
            ih *= 1.5
        r = np.sqrt(dd[m])
        w = 1.0 - r
        w = w * w * (3.0 - 2.0 * w)
        tw = w.sum()
        idx_list.append(np.flatnonzero(m))
        rw_list.append(w.copy())            # C nd[].rw — never normalised
        w_list.append(w / max(tw, 1e-12))
    return idx_list, w_list, rw_list


def vecadj_loop(sv: np.ndarray, dv0: np.ndarray, naxbf: np.ndarray,
                dsm: np.ndarray, nbr_idx, nbr_w, dest_surf, evect_fn,
                passes: int = 8) -> np.ndarray:
    """The VECADJPASSES smoothing loop (C +2970, exact translation).

    ``dv0``: pass-2 result (nrdv); ``dest_surf``: FULL destination surface
    (clipping happens against dc_gam, not the intersection); ``evect_fn``:
    inward clip-direction field (the evectmap rspl equivalent).
    """
    n = len(sv)
    dv = dv0.copy()
    anv = dv0.copy()

    # nscale (C 2900–2956): per-component ddev/sdev with the C's guards
    nscale = np.ones_like(sv)
    for i in range(n):
        j = nbr_idx[i]
        w = nbr_w[i][:, None]
        sav = (w * sv[j]).sum(0)
        dav = (w * dv0[j]).sum(0)
        sdev = (np.abs(sav[None, :] - sv[j]) * w).sum(0)
        ddev = (np.abs(dav[None, :] - dv0[j]) * w).sum(0)
        scev = (np.linalg.norm(sav[None, :] - sv[j], axis=1) * nbr_w[i]).sum()
        dcev = (np.linalg.norm(dav[None, :] - dv0[j], axis=1) * nbr_w[i]).sum()
        if scev < 1e-3 or dcev < 1e-3:
            scev = dcev = 1e-3
        low = (sdev < 1e-3) | (ddev < 1e-3)
        sdev[low] = scev
        ddev[low] = dcev
        nscale[i] = ddev / sdev

    rdsm = 1.0 - np.sqrt(dsm)
    for _ in range(passes):
        # GAUSS-SEIDEL, exactly as the C: each point's anv update (with
        # its clip and naxbf blend) is written before later points in the
        # same pass read it. dv stays the pass-2 value throughout.
        for i in range(n):
            j = nbr_idx[i]
            w = nbr_w[i][:, None]
            sav = (w * sv[j]).sum(0)
            # J is not iterated (uses dv); a/b use the iterating anv
            dav = (w * np.stack([dv[j][:, 0], anv[j][:, 1],
                                 anv[j][:, 2]], 1)).sum(0)
            tmp = (sv[i] - sav) * nscale[i] + dav
            tmp = (1.0 - rdsm[i]) * tmp + rdsm[i] * dv[i]
            # clip against the FULL destination along the evector
            if dest_surf.nradial(tmp[None, :])[0] > 1.0 + 1e-6:
                dirs = evect_fn(tmp[None, :])
                mint, maxt, _, _ = dest_surf.vector_isect(
                    tmp[None, :], tmp[None, :] + dirs)
                if not np.isnan(mint[0]):
                    tt = (mint[0] if np.isnan(maxt[0])
                          or abs(mint[0]) < abs(maxt[0]) else maxt[0])
                    tmp = tmp + tt * dirs[0]
                else:
                    tmp = dest_surf.radial(tmp[None, :])[0]
            # W/K pinning blend, written in place (visible within pass)
            anv[i] = (1.0 - naxbf[i]) * dv[i] + naxbf[i] * tmp
    return anv


def rsplpasses_loop(sv: np.ndarray, dv: np.ndarray, naxbf: np.ndarray,
                    fx: np.ndarray, nbr_idx, nbr_rw, dest_surf, evect_fn,
                    passes: int = 4, rsplscale: float = 1.8) -> np.ndarray:
    """The RSPLPASSES fine-tuning stage (C L3100–3360, per portmap
    transcription). ``dv``: VECADJ output; ``fx``: per-point wt.f.x;
    ``nbr_rw``: UNnormalised neighbour weights. WarpMapper plays rspl."""
    from workflow.profile_engine.gamut_map import WarpMapper
    n = len(sv)
    inside_sv = dest_surf.nradial(sv) <= 1.0 + 1e-6
    inside_dv = dest_surf.nradial(dv) <= 1.0 + 1e-6
    nott = inside_sv & inside_dv
    tdst = dv.copy()
    tune = ~nott
    if tune.any():
        dirs = evect_fn(dv[tune])
        mint, maxt, _, _ = dest_surf.vector_isect(dv[tune], dv[tune] + dirs)
        tt = np.where(np.isnan(maxt), mint,
                      np.where(np.abs(mint) < np.abs(maxt), mint, maxt))
        hit = ~np.isnan(tt)
        cand = dv[tune] + np.where(hit, tt, 0.0)[:, None] * dirs
        rad = dest_surf.radial(dv[tune])
        nd = np.linalg.norm(rad - dv[tune], axis=1)
        idd = np.linalg.norm(cand - dv[tune], axis=1)
        use = hit & (idd <= nd + 5.0)
        t2 = tdst[tune]
        t2[use] = cand[use]
        t2[~use] = rad[~use]
        tdst[tune] = t2
    coff = np.zeros_like(dv)
    rext = np.zeros(n)
    anv = dv.copy()
    for it in range(passes):
        warp = WarpMapper(sv, anv, grid=13, lam=0.01)
        temp = warp.map_lab(sv)
        evect = evect_fn(temp)
        clen = (evect * (tdst - temp)).sum(1)
        # local weighted-max extension over neighbours (rw unnormalised)
        maxext = np.empty(n)
        for i in range(n):
            t = nbr_rw[i] * (clen[nbr_idx[i]] + 20.0)
            maxext[i] = max(t.max() if len(t) else 0.0, 0.0) - 20.0
        if it == 0:
            rext = np.where(rext <= 0.0, rext + maxext,
                            rext + rsplscale * maxext)
        tpoint = tdst + rext[:, None] * evect
        icg = 1.4
        ixg = fx * icg
        ttt = it / (passes - 1.0)
        cgain = (1 - ttt) * icg + ttt * 0.5 * icg
        xgain = ((1 - ttt) * ixg + ttt * 0.5 * ixg) if it == 0 else 0.0
        gain = np.where(rext > 0.0, cgain, xgain)
        coff = coff + gain[:, None] * (tpoint - temp)
        warp2 = WarpMapper(dv, dv + coff, grid=13, lam=0.01)
        sm_coff = warp2.map_lab(dv) - dv
        anv = dv + naxbf[:, None] * sm_coff
        anv[nott] = dv[nott]
    return anv
