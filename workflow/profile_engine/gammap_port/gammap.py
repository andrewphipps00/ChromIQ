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
4. gammap.c's complete final row recipe: 512 grey-axis rows with the
   bent→straight black blend (USE_GREYMAP, L1380–1459), guide rows
   w 1.01, sub-surface w2 / identity-at-depth w3 rows, and the outer
   two-layer surface-grid anchor rows (nearsmth.c L3746+, w 0.1·ww)
   over the gexp-expanded box; fit with the literal 3-D rspl port
   (:mod:`rspl3`, smooth = psmooth = 2.0).

``map_lab(x) = rspl3(greyL(rot(x)))``.
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
                                                          FX, RDSM, RRDH,
                                                          RRDL,
                                                          expand_weights,
                                                          interp_xweights)


def _sobol2(n: int) -> np.ndarray:
    """First n points of the 2-D Sobol sequence (dim 1 = van der Corput,
    dim 2 = Sobol direction numbers m = 1,3,5,15,17,51…)."""
    pts = np.empty((n, 2))
    x = np.zeros(2, dtype=np.uint32)
    # direction numbers (32-bit) for the two dimensions
    v1 = np.array([1 << (31 - k) for k in range(32)], dtype=np.uint32)
    m2 = [1, 3, 5, 15, 17, 51, 85, 255, 257, 771, 1285, 3855, 4369,
          13107, 21845, 65535]
    v2 = np.zeros(32, dtype=np.uint32)
    for k in range(32):
        if k < len(m2):
            v2[k] = np.uint32(m2[k]) << np.uint32(31 - k)
        else:
            v2[k] = v2[k - 16] ^ (v2[k - 16] >> np.uint32(16))
    for i in range(n):
        c = 0                       # index of lowest zero bit of i
        ii = i
        while ii & 1:
            ii >>= 1
            c += 1
        x[0] ^= v1[c]
        x[1] ^= v2[c]
        pts[i] = x / 2.0 ** 32
    return pts


def _ss_verts(verts: np.ndarray, tris: np.ndarray, xvra: float
              ) -> np.ndarray:
    """gamut.c nssverts/getssvert: area-stratified extra surface points.

    Extra count = (xvra − 1)·nverts distributed ∝ triangle area; within
    each triangle a low-discrepancy sequence (restarted per triangle, as
    the C resets its sobol) mapped to barycentric via tt = √u,
    (1−tt, v·tt, rest), placed on the FLAT triangle.
    """
    a = verts[tris[:, 0]]
    b = verts[tris[:, 1]]
    c = verts[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    tarea = area.sum()
    extra = (xvra - 1.0) * len(verts)
    if extra <= 0 or tarea <= 0:
        return np.empty((0, 3))
    counts = (extra / tarea * area + 0.5).astype(int)
    kmax = counts.max()
    if kmax == 0:
        return np.empty((0, 3))
    uv = _sobol2(kmax)
    tt = np.sqrt(uv[:, 0])
    tr0 = 1.0 - tt
    tr1 = uv[:, 1] * tt
    tr2 = 1.0 - tr0 - tr1
    out = []
    for k in range(1, kmax + 1):
        m = counts >= k
        out.append(tr0[k - 1] * a[m] + tr1[k - 1] * b[m]
                   + tr2[k - 1] * c[m])
    return np.vstack(out)


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

        # XVRA vertex expansion (gamut.c nssverts/getssvert): stratified
        # per-triangle sampling to xvra × nverts total guide sources —
        # low-discrepancy barycentric points, count ∝ triangle area
        guide_src = np.vstack([psrc_verts,
                               _ss_verts(psrc_verts, np.asarray(src_tris),
                                         xvra=3.0)])

        inside = isect.nradial(guide_src) <= 1.0 + 1e-4
        null_sv = guide_src[inside]
        sv = guide_src[~inside]

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

        from workflow.profile_engine.gammap_port.rspl3 import Rspl3

        # grey-axis rows (USE_GREYMAP, gammap.c L1380–1459): 512 points
        # from premapped source black to white, target blends from the
        # bent black axis into the straight one near black
        sl_wp = ga.pre_map(np.asarray(src_cs_wp, float)[None, :])[0]
        sl_bp = ga.pre_map(np.asarray(src_cs_bp, float)[None, :])[0]
        dr_wp, dr_bp, dr_be = ga.dr_cs_wp, ga.dr_cs_bp, ga.dr_be_bp
        brad = float(np.hypot(dr_be[1] - dr_bp[1], dr_be[2] - dr_bp[2]))
        tt = np.linspace(0.0, 1.0, 512)
        gp = sl_bp[None, :] + tt[:, None] * (sl_wp - sl_bp)[None, :]
        gv = np.empty_like(gp)
        gv[:, 0] = gp[:, 0]        # L already mapped by the 1-D curve
        tb = (gv[:, 0] - dr_wp[0]) / (dr_be[0] - dr_wp[0])
        bv = dr_wp[None, :] + tb[:, None] * (dr_be - dr_wp)[None, :]
        ts = (gv[:, 0] - dr_wp[0]) / (dr_bp[0] - dr_wp[0])
        sv_ax = dr_wp[None, :] + ts[:, None] * (dr_bp - dr_wp)[None, :]
        gw = np.ones(len(gp))
        if brad > 0.001:
            t = np.clip(((dr_bp[0] + brad) - gv[:, 0]) / brad, 0.0, 1.0)
            t = t * t * (3.0 - 2.0 * t)
            ty = t * t * (3.0 - 2.0 * t)     # spline blend value
            t = (1.0 - t) * ty + t * t       # spline at 0, linear at 1
            gw *= 1.0 + t * brad
            blend = t[:, None]
        else:
            blend = np.zeros((len(gp), 1))
        gv[:, 1:] = (blend * sv_ax + (1.0 - blend) * bv)[:, 1:]

        # input range: grey rows + premapped source range, then the gexp
        # box expansion that near_smooth applies (nearsmth.c L2623–2652)
        gexp, mapres = 1.10, 29
        map_il = np.minimum(np.minimum(gp.min(0), psrc_verts.min(0)),
                            np.vstack([sv, csv]).min(0)
                            if len(sv) else gp.min(0))
        map_ih = np.maximum(np.maximum(gp.max(0), psrc_verts.max(0)),
                            np.vstack([sv, csv]).max(0)
                            if len(sv) else gp.max(0))
        dmapres = int(((mapres - 1) - (mapres - 1) / gexp) / 2.0 + 0.5)
        dmapres = max(dmapres, 1)
        scale = (mapres - 1.0 - dmapres) / (mapres - 1.0 - 2 * dmapres)
        lo = scale * (map_il - map_ih) + map_ih
        hi = scale * (map_ih - map_il) + map_il
        map_il, map_ih = lo, hi

        # ---- RSPLPASSES fine-tune loop (nearsmth.c L3100–3345) ----
        # Guides are extended so the SMOOTHED rspl lands on the surface:
        # each pass fits the current guides, measures the projected
        # shortfall along the correction field, and accumulates a
        # smoothed, naxbf-scaled offset.
        in_sv = ss_d.nradial(sv) <= 1.0 + 1e-6
        in_dv = ss_d.nradial(dv) <= 1.0 + 1e-6
        nott = in_sv & in_dv
        tdst = dv.copy()
        out_i = ~nott
        if out_i.any():
            evo = evect_fn(dv[out_i])
            # nearest on dest ≈ nearest dest VERTEX (tight bound for the
            # C's `nearest` sanity gate; the radial projection is far too
            # loose and admits insane intersection targets)
            dverts = np.asarray(dst_verts, float)
            d2 = ((dv[out_i][:, None, :] - dverts[None, :, :]) ** 2
                  ).sum(2)
            nix = d2.argmin(1)
            near = dverts[nix]
            nd = np.sqrt(d2[np.arange(len(nix)), nix])
            mint, maxt, _, _ = tri_d.vector_isect(dv[out_i],
                                                  dv[out_i] + evo)
            tt2 = np.where(np.isfinite(maxt), maxt, np.nan)
            isec = dv[out_i] + tt2[:, None] * evo
            idist = np.linalg.norm(isec - dv[out_i], axis=1)
            use = np.isfinite(idist) & (idist <= nd + 5.0)
            tgt = near.copy()
            tgt[use] = isec[use]
            tdst[out_i] = tgt
        anv = dv.copy()
        coff = np.zeros_like(dv)
        rext = np.zeros(len(dv))
        fx_w = w[:, FX]
        lastmap = None
        RSPLPASSES, RSPLSCALE = 4, 1.8
        for it in range(RSPLPASSES):
            m1 = Rspl3(sv, anv, np.ones(len(sv)), map_il, map_ih,
                       gres=mapres, smooth=2.0)
            temp = m1.interp(sv)
            evi = evect_fn(temp)
            clen = ((tdst - temp) * evi).sum(1)
            minext = -20.0
            maxext = np.empty(len(dv))
            for i in range(len(dv)):
                tmpl = nbr_rw[i] * (clen[nbr_idx[i]] - minext)
                maxext[i] = max(tmpl.max(initial=0.0), 0.0) + minext
            if it == 0:
                rext = np.where(rext <= 0.0, rext + maxext,
                                rext + RSPLSCALE * maxext)
            tpoint = tdst + rext[:, None] * evi
            icgain = 1.4
            ttl = it / (RSPLPASSES - 1.0)
            cgain = (1.0 - ttl) * icgain + ttl * 0.5 * icgain
            xgain = ((1.0 - ttl) * fx_w * icgain
                     + ttl * 0.5 * fx_w * icgain)
            if it != 0:
                xgain = np.zeros(len(dv))
            gain = np.where(rext > 0.0, cgain, xgain)
            coff = coff + gain[:, None] * (tpoint - temp)
            if it + 1 == RSPLPASSES:
                lastmap = m1
            m2 = Rspl3(dv, coff, np.ones(len(dv)), map_il, map_ih,
                       gres=mapres, smooth=1.0)
            filt = m2.interp(dv)
            coff = filt
            upd = ~nott
            anv[upd] = dv[upd] + naxbf[upd, None] * filt[upd]
        dv = anv

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

        # ---- gammap.c final row assembly ----
        # surface grid anchor rows (nearsmth.c L3746+): outer two layers
        # of the half-res grid, weighted-nearest onto the source surface,
        # mapped through the last RSPLPASSES map, w1 = 0.1·ww
        hmapres = (mapres + 1) // 2
        hdmapres = (dmapres + 1) // 2
        axes = np.arange(hmapres)
        layer = ((axes == 0) | (axes == hdmapres) | (axes == hmapres - 1)
                 | (axes == hmapres - 1 - hdmapres))
        gi, gj, gk = np.meshgrid(axes, axes, axes, indexing="ij")
        sel = layer[gi] | layer[gj] | layer[gk]
        gpos = np.stack([gi[sel], gj[sel], gk[sel]], 1) / (hmapres - 1.0)
        spts = map_il[None, :] + gpos * (map_ih - map_il)[None, :]
        outside = ss_ps.nradial(spts) > 1.0 + 1e-6
        spts = spts[outside]
        if len(spts):
            wsp = interp_xweights(spts, xw, cm)
            def objsp(dtp):
                return aerrf(dtp, spts, wsp["ra"], wsp["w"][:, ALXPOW],
                             wsp["w"][:, ALXTHR])
            angsp = _pattern_search(ss_ps, _angles_of(spts), objsp,
                                    iters=25)
            cpp = _points_of(ss_ps, angsp)
            sdv = lastmap.interp(cpp)
            g2g = np.linalg.norm(sdv - cpp, axis=1)
            g2c = np.maximum(np.linalg.norm(cpp - CENT[None, :], axis=1),
                             0.1)
            ws = 0.1 * np.minimum(g2g / g2c, 1.0)

        train = [gp, sv, null_sv]
        target = [gv, dv, null_sv]
        rw = [gw, np.full(len(sv), 1.01), np.full(len(null_sv), 1.01)]
        if sub_s:
            train.append(np.array(sub_s))
            target.append(np.array(sub_t))
            rw.append(np.array(sub_w))
        if len(spts):
            train.append(spts)
            target.append(sdv)
            rw.append(ws)
        self._map = Rspl3(np.vstack(train), np.vstack(target),
                          np.concatenate(rw), map_il, map_ih,
                          gres=mapres, smooth=2.0)

        # white/black fine-tune (gammap.c L1799–1856): rigid rotate/scale
        # taking the map's ACTUAL white/black to the exact targets
        from workflow.profile_engine.gammap_port.geom import (apply_3x4,
                                                              vec_rot_mat)
        a_wp = self._map.interp(ga.pre_map(ga.s_mt_wp[None, :]))[0]
        a_bp = self._map.interp(ga.pre_map(ga.s_mt_bp[None, :]))[0]
        self._wbmat = vec_rot_mat(a_wp, a_bp, ga.d_mt_wp, ga.d_mt_bp)
        self._apply_3x4 = apply_3x4

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        out = self._map.interp(
            self._ga.pre_map(np.atleast_2d(np.asarray(lab, float))))
        return np.atleast_2d(self._apply_3x4(self._wbmat, out))
