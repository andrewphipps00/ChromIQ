"""Top-level gamut mapping — the assembled gammap port (AGPL-3.0, Graeme
W. Gill — see package ``__init__``).

Faithful composition per gammap.c (every stage validated against
instrumented Argyll internals — see ``portmap.md`` for the numbers):

1. grey-axis rotation + bendBP + 1-D L map (:mod:`greyaxis`), applied to
   the source gamut AND to every mapped colour (domap order);
2. guide construction on the pre-mapped source: hextant weights, cusp
   mapping, pass-1 weighted-nearest on the src∩dst surface (validated
   0.006/0.038 vs Argyll), exact shrunk-gamut evectors (cos 0.993),
   the VECADJ neighbour-smoothing loop (0.518 vs Argyll's own output);
3. knee sub-surface rows (gamcknf) + null interior guides;
4. the final 3-D fit over gammap.c's exact row recipe (guides w 1.01,
   sub-surface w2, identity-at-depth w3), with the maths-A warp playing
   rspl (calibrated: 0.094 vs Argyll's fit of identical data).

``map_lab(x) = warp(greyL(rot(x)))``.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port import weights as wtab
from workflow.profile_engine.gammap_port.cusps import CuspMapping
from workflow.profile_engine.gammap_port.error import aerrf
from workflow.profile_engine.gammap_port.gamutsurf import (CENT,
                                                           SampledSurface,
                                                           TriSurface)
from workflow.profile_engine.gammap_port.greyaxis import GreyAxis
from workflow.profile_engine.gammap_port.nearsmth import (_angles_of,
                                                          _pattern_search,
                                                          _points_of,
                                                          build_neighbours,
                                                          vecadj_loop)
from workflow.profile_engine.gammap_port.xweights import (ALXPOW, ALXTHR,
                                                          RDSM, RRDH, RRDL,
                                                          expand_weights,
                                                          interp_xweights)


def _sampled(tri: TriSurface, nh: int = 360, nb: int = 180,
             cache: dict | None = None, key: str = "") -> SampledSurface:
    if cache is not None and key in cache:
        ss = SampledSurface.__new__(SampledSurface)
        ss._tri, ss.nh, ss.nb, ss.tab = tri, nh, nb, cache[key]
        return ss
    ss = SampledSurface(tri, nh, nb)
    if cache is not None:
        cache[key] = ss.tab
    return ss


class GammapMapper:
    """gammap-ported source→destination perceptual/saturation mapping.

    Inputs are gamut descriptions: vertices (+ triangles where available;
    without them a dense-cloud SampledSurface is built), colourspace
    white/black points and the six cusps.
    """

    def __init__(self, src_verts, src_tris, src_cs_wp, src_cs_bp, src_cusps,
                 dst_verts, dst_tris, dst_cs_wp, dst_cs_bp, dst_cusps, *,
                 intent: str = "p", surf_cache: dict | None = None) -> None:
        table = (wtab.SATURATION_WEIGHTS if intent in ("s", "ms")
                 else wtab.PERCEPTUAL_WEIGHTS)
        xw = expand_weights(table)
        gamcknf = 1.0        # xicc.c perceptual/saturation entries
        tri_d = TriSurface(dst_verts, dst_tris)
        self._ga = ga = GreyAxis(src_cs_wp, src_cs_bp, dst_cs_wp, dst_cs_bp,
                                 tri_d)

        # pre-mapped source (scl_gam equivalent)
        psrc_verts = ga.pre_map(src_verts)
        tri_ps = TriSurface(psrc_verts, src_tris)
        ss_d = _sampled(tri_d, cache=surf_cache, key="dest")
        ss_ps = _sampled(tri_ps, cache=surf_cache, key="psrc")
        cm = CuspMapping(ga.pre_map(src_cusps), np.asarray(dst_cusps, float),
                         src_white=ga.pre_map(np.asarray(src_cs_wp,
                                                         float)[None, :])[0],
                         src_black=ga.pre_map(np.asarray(src_cs_bp,
                                                         float)[None, :])[0],
                         dst_white=np.asarray(dst_cs_wp, float),
                         dst_black=ga.dr_be_bp)

        isect = SampledSurface.__new__(SampledSurface)
        isect._tri, isect.nh, isect.nb = tri_d, ss_d.nh, ss_d.nb
        isect.tab = np.minimum(ss_d.tab, ss_ps.tab)

        inside = isect.nradial(psrc_verts) <= 1.0 + 1e-4
        null_sv = psrc_verts[inside]
        sv = psrc_verts[~inside]

        wts = interp_xweights(sv, xw, cm)
        w = wts["w"]
        ra = wts["ra"]
        naxbf = cm.comp_naxbf(sv)
        csv = np.empty_like(sv)
        cb = np.round(w[:, :5], 3)
        for key in np.unique(cb, axis=0):
            m = (cb == key[None, :]).all(1)
            csv[m] = cm.comp_ce(sv[m], cusp_weights=tuple(key))

        # pass 1 (pass 2 measured as a no-op in colprof's configuration)
        def obj1(dtp):
            return aerrf(dtp, csv, ra, w[:, ALXPOW], w[:, ALXTHR])
        ang = _pattern_search(isect, _angles_of(csv), obj1, iters=30)
        aodv = _points_of(isect, ang)

        # exact shrunk-gamut evector field
        def cvect(p):
            rr = interp_xweights(p, xw, cm)["ra"]
            wl, wc = rr[:, 0], rr[:, 1]
            tot = np.abs(wl + wc)
            bad = tot < 1e-7
            wl = np.where(bad, 0.5, wl / np.maximum(tot, 1e-7))
            wc = np.where(bad, 0.5, wc / np.maximum(tot, 1e-7))
            wpt, bpt, grey = cm.cusps[0][6], cm.cusps[0][7], cm.cusps[0][8]
            vv = (p[:, 0] - bpt[0]) / max(wpt[0] - bpt[0], 1e-9)
            lv = np.stack([p[:, 0], vv * (wpt[1] - bpt[1]) + bpt[1],
                           vv * (wpt[2] - bpt[2]) + bpt[2]], 1)
            return wl[:, None] * lv + wc[:, None] * grey[None, :]

        def doshrink(pts, shrink=5.0):
            p2 = cvect(pts)
            rad = np.hypot(pts[:, 1], pts[:, 2])
            ln = np.where(rad < 2 * shrink, rad * 0.5, shrink)
            d = p2 - pts
            n = np.maximum(np.linalg.norm(d, axis=1), 1e-9)
            return pts + d * (ln / n)[:, None]

        ss_sh = _sampled(TriSurface(doshrink(dst_verts), dst_tris),
                         cache=surf_cache, key="shrunk")

        def objsh(dtp):
            return aerrf(dtp, aodv, ra, w[:, ALXPOW], w[:, ALXTHR])
        angs = _pattern_search(ss_sh, _angles_of(aodv), objsh, iters=25)
        shpt = _points_of(ss_sh, angs)
        rad_n = tri_d.vector_isect(np.repeat(CENT[None, :], len(aodv), 0),
                                   aodv)[3]
        inward = CENT[None, :] - aodv
        flip = (rad_n * inward).sum(1) < 0
        rad_n[flip] = -rad_n[flip]
        ev = shpt - aodv + 0.1 * rad_n
        ev = ev / np.maximum(np.linalg.norm(ev, axis=1), 1e-9)[:, None]
        from workflow.profile_engine.gamut_map import WarpMapper
        efield = WarpMapper(aodv, aodv + ev, grid=29, lam=0.05)

        def evect_fn(pts):
            d = efield.map_lab(pts) - pts
            return d / np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]

        nbr_idx, nbr_w, nbr_rw = build_neighbours(csv, w[:, RRDL],
                                                  w[:, RRDH])
        dv = vecadj_loop(csv, aodv, naxbf, w[:, RDSM], nbr_idx, nbr_w,
                         ss_d, evect_fn, passes=8)

        # knee sub-surface rows (nearsmth.c L3460+, compression branch)
        mv = dv - sv
        ml = np.linalg.norm(mv, axis=1)
        moved = ml > 2.0
        sub_s, sub_t, sub_w = [], [], []
        if moved.any():
            mint, maxt, _, _ = tri_d.vector_isect(sv[moved], dv[moved])
            wpt = np.asarray(dst_cs_wp, float)
            bpt = ga.dr_be_bp
            axis = wpt - bpt
            for k, i in enumerate(np.flatnonzero(moved)):
                if np.isnan(mint[k]) or not (mint[k] >= -1e-8
                                             and maxt[k] > 1e-8):
                    continue
                d_line = mv[i] / ml[i]
                w0 = sv[i] - bpt
                a11 = axis @ axis
                a12 = axis @ d_line
                den = a11 - a12 * a12
                t_ax = ((axis @ w0 - a12 * (d_line @ w0)) / den
                        if abs(den) > 1e-9 else 0.5)
                nap = bpt + np.clip(t_ax, 0, 1) * axis
                nap = 0.5 * nap + 0.5 * np.array([sv[i][0], nap[1], nap[2]])
                nap[0] = np.clip(nap[0], bpt[0], wpt[0])
                adepth2 = np.linalg.norm(nap - sv[i])
                adepth1 = ml[i] * 0.5 * (maxt[k] + mint[k] - 2.0)
                adepth = min(adepth1, adepth2) * 0.9
                if adepth1 < 0.5 * adepth2 or adepth <= 0:
                    continue
                sknf = gamcknf * 0.6
                sv2 = dv[i]
                sml = min(ml[i] * (1 - sknf), adepth * (1 - sknf))
                dv2 = sv2 + d_line * sml
                natarg = nap - sv2
                nn = np.linalg.norm(natarg)
                if nn > 1e-9:
                    natarg = sv2 + natarg / nn * sml
                    dv2 = ((1 - sml / adepth2) * dv2
                           + (sml / adepth2) * natarg)
                sub_s.append(sv2)
                sub_t.append(dv2)
                sub_w.append(0.7)
                sd3 = 0.4 * dv2 + 0.6 * nap
                sub_s.append(sd3)          # identity row (gammap.c L1694)
                sub_t.append(sd3)
                sub_w.append(0.4 * gamcknf)

        train = [sv, null_sv]
        target = [dv, null_sv]
        rw = [np.full(len(sv), 1.01), np.full(len(null_sv), 1.01)]
        if sub_s:
            train.append(np.array(sub_s))
            target.append(np.array(sub_t))
            rw.append(np.array(sub_w))
        self._warp = WarpMapper(np.vstack(train), np.vstack(target),
                                grid=29, lam=0.05,
                                row_weights=np.concatenate(rw))

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        return self._warp.map_lab(
            self._ga.pre_map(np.atleast_2d(np.asarray(lab, float))))
