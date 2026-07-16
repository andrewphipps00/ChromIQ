"""Top-level gamut mapping — the assembled gammap port (AGPL-3.0, Graeme
W. Gill — see package ``__init__``).

Faithful composition per gammap.c (every stage validated against
instrumented Argyll internals — see ``portmap.md`` for the numbers):

1. grey-axis rotation + bendBP + 1-D L map (:mod:`greyaxis`), applied to
   the source gamut AND to every mapped colour (domap order);
2. guide construction on the pre-mapped source: hextant weights, cusp
   mapping, pass-1 weighted-nearest on the src∩dst surface (validated
   0.006/0.038 vs Argyll), exact shrunk-gamut evectors (cos 0.993),
   the Gauss-Seidel VECADJ neighbour-smoothing loop (0.038 median vs
   Argyll's own dumped output on identical inputs);
3. knee sub-surface rows (gamcknf); inside-isect sources are rejected
   outright (the C keeps NO interior guide rows);
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
from workflow.profile_engine.gammap_port.gamutsurf import (
    CENT, IntersectSurface, SampledSurface, TriSurface)
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


_RSPLPASSES_ON = True      # nearsmth.c RSPLPASSES fine-tune stage
_REJECT_MARGIN = 1e-4      # inside-isect guide rejection depth (diagnostic)


_SOBOL_MAXBIT = 30                 # numlib/sobol.h
_SOBOL_POLY = (1, 3)               # sobol_poly[0..1]
_SOBOL_VINIT0 = (0, 1)            # vinit[0][0..1] (only row 0 needed, m≤1)


def _sobol2_dir() -> list[list[int]]:
    """Build Argyll's 2-D Sobol direction table (numlib/sobol.c new_sobol,
    dims 0 and 1), including the ×2^k column scaling."""
    mb = _SOBOL_MAXBIT
    d = [[0, 0] for _ in range(mb)]
    for i in range(2):
        if i == 0:
            for j in range(mb):
                d[j][0] = 1
        else:
            m, pm = 0, _SOBOL_POLY[i] >> 1
            while pm:
                m += 1
                pm >>= 1
            for j in range(m):
                d[j][i] = _SOBOL_VINIT0[i]      # vinit[0][i] (m == 1)
            pm = _SOBOL_POLY[i]
            for j in range(m, mb):
                newv = d[j - m][i]
                for k in range(m):
                    if pm & (1 << (m - k - 1)):
                        newv ^= d[j - k - 1][i] << (k + 1)
                d[j][i] = newv
    p = 2
    for j in range(mb - 2, -1, -1):
        d[j][0] *= p
        d[j][1] *= p
        p <<= 1
    return d


_SOBOL_DIR = _sobol2_dir()


def _sobol2(n: int) -> np.ndarray:
    """First n points of Argyll's 2-D Sobol sequence — a faithful port of
    numlib/sobol.c next_sobol (reset state: count=0, lastq=0), so the
    stratified guide-vertex positions match Argyll's exactly. Per call:
    count++, p = trailing-zero count of the (1-based) counter, lastq ^=
    dir[p], value = lastq · 2^−30."""
    recipd = 1.0 / (1 << _SOBOL_MAXBIT)
    lastq = [0, 0]
    out = np.empty((n, 2))
    count = 0
    for idx in range(n):
        count += 1
        c, p = count, 0
        while (c & 1) == 0:
            p += 1
            c >>= 1
        lastq[0] ^= _SOBOL_DIR[p][0]
        lastq[1] ^= _SOBOL_DIR[p][1]
        out[idx, 0] = lastq[0] * recipd
        out[idx, 1] = lastq[1] * recipd
    return out


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



def _nearest_on_mesh(pts: np.ndarray, verts: np.ndarray, tris: np.ndarray
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Exact nearest point on a triangle mesh, per query point (used only
    for the few tdst fallback/borderline points — the C's gamut nearest).
    Returns (nearest points, distances)."""
    a = verts[tris[:, 0]]
    ab = verts[tris[:, 1]] - a
    ac = verts[tris[:, 2]] - a
    out = np.empty_like(pts)
    dist = np.empty(len(pts))
    for k, q in enumerate(pts):
        ap = q[None, :] - a
        d1 = (ab * ap).sum(1)
        d2 = (ac * ap).sum(1)
        d00 = (ab * ab).sum(1)
        d01 = (ab * ac).sum(1)
        d11 = (ac * ac).sum(1)
        den = np.maximum(d00 * d11 - d01 * d01, 1e-12)
        v = np.clip((d11 * d1 - d01 * d2) / den, 0.0, 1.0)
        w = np.clip((d00 * d2 - d01 * d1) / den, 0.0, 1.0)
        s = v + w
        over = s > 1.0
        sdiv = np.where(over, s, 1.0)
        v = np.where(over, v / sdiv, v)
        w = np.where(over, w / sdiv, w)
        # clamp to the three edges where the interior projection left
        # the triangle (barycentric clamp above is approximate but the
        # per-edge projections below make it exact)
        cand = [a + v[:, None] * ab + w[:, None] * ac]
        for e0, ev in ((a, ab), (a, ac),
                       (verts[tris[:, 1]], verts[tris[:, 2]]
                        - verts[tris[:, 1]])):
            tt = np.clip(((q[None, :] - e0) * ev).sum(1)
                         / np.maximum((ev * ev).sum(1), 1e-12), 0.0, 1.0)
            cand.append(e0 + tt[:, None] * ev)
        best = None
        bestd = None
        for c in cand:
            dd = ((c - q[None, :]) ** 2).sum(1)
            j = dd.argmin()
            if bestd is None or dd[j] < bestd:
                bestd = dd[j]
                best = c[j]
        out[k] = best
        dist[k] = np.sqrt(bestd)
    return out, dist


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
                 intent: str = "p", surf_cache: dict | None = None,
                 dst_gam_wp=None, dst_gam_bp=None, progress=None,
                 exact_geometry: bool = True) -> None:
        def _tick(msg):
            if progress:
                progress(msg)
        table = (wtab.SATURATION_WEIGHTS if intent in ("s", "ms")
                 else wtab.PERCEPTUAL_WEIGHTS)
        xw = expand_weights(table)
        # xicc.c gmi entries, verbatim ("p" L2186+, "s" L2299+)
        if intent in ("s", "ms"):
            gamcknf, gamxknf = 1.1, 0.5
            useexp = True
            smooth = 4.0             # gamswf(1.0) · ssmooth(4.0)
            satenh = 0.9 if intent == "s" else 0.0
        else:
            gamcknf, gamxknf = 1.0, 0.0
            useexp = False
            smooth = 2.0             # gampwf(1.0) · psmooth(2.0)
            satenh = 0.0
        tri_d = TriSurface(dst_verts, dst_tris)
        self._ga = ga = GreyAxis(src_cs_wp, src_cs_bp, dst_cs_wp, dst_cs_bp,
                                 tri_d)

        # pre-mapped source (scl_gam equivalent). Surface queries use
        # EITHER Argyll's exact triangle geometry (exact_geometry=True:
        # TriSurface + IntersectSurface — most faithful, pass-1 aodv 0.005
        # vs Argyll, but ~5× slower to query) OR the sampled hue×inclination
        # table (fast: near-identical output, 0.043 pass-1, a few seconds).
        # Both run the identical mapping ALGORITHM; only the surface-lookup
        # precision differs, and the resulting profiles are perceptually
        # indistinguishable (the difference is below the ICC file's own
        # rounding). See portmap.md for the measurements.
        self.exact_geometry = exact_geometry
        psrc_verts = ga.pre_map(src_verts)
        tri_ps = TriSurface(psrc_verts, src_tris)
        _tick("Gamut mapping: preparing colour surfaces…")
        if exact_geometry:
            ss_d = tri_d
            ss_ps = tri_ps
        else:
            ss_d = _sampled(tri_d, cache=surf_cache, key="dest")
            ss_ps = _sampled(tri_ps, cache=surf_cache, key="psrc")
        # init_ce's dest black is the C's near_smooth `d_bp` = dr_cs_bp
        # (the fully-adapted destination black), NOT the bent dr_be_bp.
        # Using the bent point offsets the dest cusp grey/rotation, which
        # the saturation intent's chroma blend (cw_c) exposes as a ~0.4 ΔE
        # error in the cusp-mapped source (validated: 0.40 → 0.003 vs
        # Argyll's own csv dump). Perceptual (cw_c=0) barely notices.
        cm = CuspMapping(ga.pre_map(src_cusps), np.asarray(dst_cusps, float),
                         src_white=ga.pre_map(np.asarray(src_cs_wp,
                                                         float)[None, :])[0],
                         src_black=ga.pre_map(np.asarray(src_cs_bp,
                                                         float)[None, :])[0],
                         dst_white=np.asarray(dst_cs_wp, float),
                         dst_black=ga.dr_cs_bp)

        if exact_geometry:
            isect = IntersectSurface(tri_d, tri_ps)
        else:
            isect = SampledSurface.__new__(SampledSurface)
            isect._tri, isect.nh, isect.nb = tri_d, ss_d.nh, ss_d.nb
            isect.tab = np.minimum(ss_d.tab, ss_ps.tab)

        # XVRA vertex expansion (gamut.c nssverts/getssvert): stratified
        # per-triangle sampling to xvra × nverts total guide sources —
        # low-discrepancy barycentric points, count ∝ triangle area
        guide_src = np.vstack([psrc_verts,
                               _ss_verts(psrc_verts, np.asarray(src_tris),
                                         xvra=3.0)])

        # points strictly inside the isect are REJECTED outright
        # (nearsmth.c L1989: "double back/convex" points are ignored —
        # they produce NO rows at all; the interior is shaped only by the
        # grey rows, sd3 anchors, surface-grid rows and rspl smoothing)
        r_pt = np.linalg.norm(guide_src - CENT[None, :], axis=1)
        r_is = np.linalg.norm(isect.radial(guide_src) - CENT[None, :],
                              axis=1)
        sv = guide_src[~(r_is > r_pt + _REJECT_MARGIN)]

        wts = interp_xweights(sv, xw, cm)
        w = wts["w"]
        ra = wts["ra"]
        naxbf = cm.comp_naxbf(sv)
        csv = np.empty_like(sv)
        cb = np.round(w[:, :5], 3)
        for key in np.unique(cb, axis=0):
            m = (cb == key[None, :]).all(1)
            csv[m] = cm.comp_ce(sv[m], cusp_weights=tuple(key))

        _tick("Gamut mapping: matching source colours to the printer…")
        # pass 1 (pass 2 measured as a no-op in colprof's configuration).
        # For expansion intents the optimisation target is the FULL dest
        # (nearsmth.c: dst_gam = dc_gam when there is no image gamut).
        opt_surf = ss_d if useexp else isect
        def obj1(dtp):
            return aerrf(dtp, csv, ra, w[:, ALXPOW], w[:, ALXTHR])
        ang = _pattern_search(opt_surf, _angles_of(csv), obj1, iters=30)
        aodv = _points_of(opt_surf, ang)
        if useexp:
            # expansion swap (nearsmth.c L2410+): where the radial dest
            # lies beyond the source, the roles swap — the guide SOURCE
            # becomes the weighted-nearest point on the source surface
            # seen from the radial dest point, and the target is that
            # radial dest point itself
            drv = ss_d.radial(csv)      # radial dest = the swap target
            r_c = np.linalg.norm(csv - CENT[None, :], axis=1)
            # Argyll's exact swap test (nearsmth.c L2410): dr = |nearest(dgam,
            # sv)| > sr = |sv|. The radial-point radius the port used before
            # over-triggered the swap for saturated colours (radial overshoots
            # the true nearest point), inflating the saturation-intent tail.
            ndv = tri_d.nearest(csv)
            r_d = np.linalg.norm(ndv - CENT[None, :], axis=1)
            swap = r_d > r_c + 1e-9
            if swap.any():
                tgt = drv[swap]
                ras, pows, thrs = (ra[swap], w[swap, ALXPOW],
                                   w[swap, ALXTHR])
                def objx(dtp):
                    return aerrf(dtp, tgt, ras, pows, thrs)
                angx = _pattern_search(ss_ps, _angles_of(tgt), objx,
                                       iters=30)
                new_sv = _points_of(ss_ps, angx)
                sv[swap] = new_sv
                cbx = np.round(w[swap][:, :5], 3)
                csw = np.empty_like(new_sv)
                for key in np.unique(cbx, axis=0):
                    m = (cbx == key[None, :]).all(1)
                    csw[m] = cm.comp_ce(new_sv[m], cusp_weights=tuple(key))
                csv[swap] = csw
                aodv[swap] = tgt

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

        # shrunk gamut for the evector field (SHRINK=5)
        tri_sh = TriSurface(doshrink(dst_verts), dst_tris)
        ss_sh = tri_sh if exact_geometry else _sampled(
            tri_sh, cache=surf_cache, key=f"shrunk_{intent}")

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
        _tick("Gamut mapping: smoothing the colour transitions…")
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

        _tick("Gamut mapping: fine-tuning the fit to the gamut edge…")
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
            dvo = dv[out_i]
            # tdst lives on smp[i].dgam: the INTERSECTION gamut for
            # compression intents, the FULL dest for expansion intents
            # (dst_gam = dc_gam when useexp with no image gamut).
            # Nearest ≈ nearest vertex of that gamut.
            if useexp:
                iverts = np.asarray(dst_verts, float)
            else:
                iverts = np.vstack([
                    psrc_verts[ss_d.nradial(psrc_verts) <= 1.0 + 1e-3],
                    np.asarray(dst_verts, float)[
                        ss_ps.nradial(np.asarray(dst_verts, float))
                        <= 1.0 + 1e-3]])
                if len(iverts) == 0:
                    iverts = np.asarray(dst_verts, float)
            d2 = ((dvo[:, None, :] - iverts[None, :, :]) ** 2).sum(2)
            nix = d2.argmin(1)
            near = iverts[nix]
            nd = np.sqrt(d2[np.arange(len(nix)), nix])
            # line ∩ isect via per-surface interval intersection, with
            # vintersect2 semantics: inside → segment ENTRY (behind, −ve
            # direction); outside → first +ve crossing
            if useexp:
                t_in, t_out, _, _ = tri_d.vector_isect(dvo, dvo + evo)
                p1_in = ss_d.nradial(dvo) <= 1.0 + 1e-6
            else:
                mint_s2, maxt_s2, _, _ = tri_ps.vector_isect(dvo,
                                                             dvo + evo)
                mint_d2, maxt_d2, _, _ = tri_d.vector_isect(dvo,
                                                            dvo + evo)
                t_in = np.fmax(mint_s2, mint_d2)
                t_out = np.fmin(maxt_s2, maxt_d2)
                p1_in = isect.nradial(dvo) <= 1.0 + 1e-6
            ok = np.isfinite(t_in) & np.isfinite(t_out) & (t_in <= t_out)
            tt2 = np.where(p1_in, t_in,
                           np.where(t_in >= -1e-8, t_in, t_out))
            tt2 = np.where(ok, tt2, np.nan)
            isec = dvo + tt2[:, None] * evo
            idist = np.linalg.norm(isec - dvo, axis=1)
            use = np.isfinite(idist) & (idist <= nd + 5.0)
            # refine with the EXACT nearest-on-surface (the C's gamut
            # nearest) where the vertex approximation decides the result:
            # fallback targets and borderline sanity-gate points
            border = (~use) | (np.isfinite(idist)
                               & (np.abs(idist - nd) <= 6.0))
            if useexp and border.any():
                npts, ndist = _nearest_on_mesh(
                    dvo[border], np.asarray(dst_verts, float),
                    np.asarray(dst_tris))
                near[border] = npts
                nd[border] = ndist
                use = np.isfinite(idist) & (idist <= nd + 5.0)
            tgt = near.copy()
            tgt[use] = isec[use]
            tdst[out_i] = tgt
        anv = dv.copy()
        coff = np.zeros_like(dv)
        rext = np.zeros(len(dv))
        fx_w = w[:, FX]
        lastmap = None
        RSPLPASSES, RSPLSCALE = (4 if _RSPLPASSES_ON else 0), 1.8
        for it in range(RSPLPASSES):
            m1 = Rspl3(sv, anv, np.ones(len(sv)), map_il, map_ih,
                       gres=mapres, smooth=smooth)
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
        if lastmap is None:      # RSPLPASSES disabled: plain guide fit
            lastmap = Rspl3(sv, dv, np.ones(len(sv)), map_il, map_ih,
                            gres=mapres, smooth=smooth)

        # sub-surface rows (nearsmth.c L3390–3685, CYLIN_SUBVEC +
        # SUBVEC_SMOOTHING both defined — validated vs the in-frame nsm
        # dump: sd3 exact, dv2 0.15 median, vflag 93.8% agreement)
        # line∩intersection-gamut as interval intersection of the exact
        # per-surface crossings: inside(isect) = inside(src) ∧ inside(dst)
        if useexp:
            # dgam is the FULL dest for expansion intents
            i_mint, i_maxt, _, _ = tri_d.vector_isect(sv, dv)
        else:
            mint_s, maxt_s, _, _ = tri_ps.vector_isect(sv, dv)
            mint_d, maxt_d, _, _ = tri_d.vector_isect(sv, dv)
            i_mint = np.fmax(mint_s, mint_d)
            i_maxt = np.fmin(maxt_s, maxt_d)
        got_iv = np.isfinite(i_mint) & np.isfinite(i_maxt)
        wp_g = np.asarray(dst_gam_wp if dst_gam_wp is not None
                          else dst_cs_wp, float)
        bp_g = np.asarray(dst_gam_bp if dst_gam_bp is not None
                          else dst_cs_bp, float)
        nsub = len(sv)
        sub2_s = np.zeros_like(sv)
        sub2_t = np.zeros_like(sv)
        sub3 = np.zeros_like(sv)
        sub_w2 = np.zeros(nsub)
        sub_w3 = np.zeros(nsub)
        sub_valid = np.zeros(nsub, bool)
        sub_surf = ss_d if useexp else isect
        rr_dst = np.linalg.norm(sub_surf.radial(dv) - CENT[None, :],
                                axis=1)
        rr_src = np.linalg.norm(ss_ps.radial(dv) - CENT[None, :], axis=1)
        mvl = np.linalg.norm(dv - sv, axis=1)
        u_ax = wp_g - bp_g
        for i in range(nsub):
            ml = mvl[i]
            if ml <= 0.1 or not got_iv[i]:
                continue
            mi, ma = i_mint[i], i_maxt[i]
            mv = dv[i] - sv[i]
            # closest point on the dest gamut W-B axis to the guide line
            w0 = bp_g - sv[i]
            a11 = u_ax @ u_ax
            a12 = u_ax @ mv
            a22 = mv @ mv
            den = a11 * a22 - a12 * a12
            s_ax = ((a12 * (mv @ w0) - a22 * (u_ax @ w0)) / den
                    if abs(den) > 1e-12 else 0.0)
            nap = bp_g + s_ax * u_ax
            comp = ((mi > 1e-8 and ma > -1e-8)
                    or (mi < -1e-8 and ma > -1e-8
                        and abs(mi) < abs(ma) - 1e-8))
            nap = nap.copy()
            # J half-blended toward dv (compression) / sv (expansion),
            # then clipped by REPLACING with the endpoint
            nap[0] = 0.5 * nap[0] + 0.5 * (dv[i][0] if comp else sv[i][0])
            if nap[0] < bp_g[0]:
                nap = bp_g.copy()
            elif nap[0] > wp_g[0]:
                nap = wp_g.copy()
            adepth2 = np.linalg.norm(nap - (dv[i] if comp else sv[i]))
            if mi >= -1e-8 and ma > 1e-8:
                if (abs(mi - 1.0) < abs(ma) - 1.0
                        and rr_dst[i] < rr_src[i]):
                    sknf = gamcknf * 0.6
                    adepth1 = ml * 0.5 * (ma + mi - 2.0)
                    adepth = adepth2 * 0.9      # CYLIN_SUBVEC
                    if adepth1 < 0.5 * adepth2:
                        continue
                    sub_valid[i] = True
                    sub2_s[i] = dv[i]
                    ml2 = ml * (1.0 - sknf)
                    adepth *= (1.0 - sknf)
                    sml = min(ml2, adepth)
                    nat = nap - sub2_s[i]
                    nn = max(np.linalg.norm(nat), 1e-9)
                    sub2_t[i] = sub2_s[i] + nat / nn * sml
                    sub_w2[i] = 0.7
                    sub3[i] = 0.4 * sub2_t[i] + 0.6 * nap
                    sub_w3[i] = 0.4 * gamcknf
            elif mi < -1e-8 and ma > 1e-8:
                # gamut expansion & vector expansion (nearsmth.c L3555+)
                adepth1 = ml * 0.5 * -mi
                adepth = adepth2 * 0.9          # CYLIN_SUBVEC
                if adepth1 < 0.6 * adepth2:
                    continue
                sub_valid[i] = True
                sub2_t[i] = sv[i]               # sub DEST is guide src
                ml2 = ml * (1.0 - gamxknf)
                adepth *= (1.0 - gamxknf)
                sml = min(ml2, adepth)
                nat = sub2_t[i] - nap
                nn = max(np.linalg.norm(nat), 1e-9)
                sub2_s[i] = sub2_t[i] - nat / nn * sml   # CYLIN direction
                sub_w2[i] = 0.8
                sub3[i] = 0.5 * sub2_s[i] + 0.5 * nap
                sub_w3[i] = 0.3 * gamcknf
            else:
                dv[i] = aodv[i]          # nonsense vector: clip to aodv
        # SUBVEC_SMOOTHING: neighbour-filtered dv2 with cylindrical
        # feature scaling; invalid neighbours contribute zeros (the C
        # callocs nsm, so their sv2/dv2 are zero — replicate exactly)
        sub2_t_s = sub2_t.copy()
        for i in np.flatnonzero(sub_valid):
            j = nbr_idx[i]
            ww = nbr_w[i][:, None]
            sav = (ww * sub2_s[j]).sum(0)
            dav = (ww * sub2_t[j]).sum(0)
            scr = np.hypot(sav[1], sav[2])
            dcr = np.hypot(dav[1], dav[2])
            scf = dcr / max(scr, 1e-9)
            tmp = sub2_s[i] - sav
            tmp[1] *= scf
            tmp[2] *= scf
            sub2_t_s[i] = tmp + dav
        sub_s, sub_t, sub_w = [], [], []
        for i in np.flatnonzero(sub_valid):
            sub_s.append(sub2_s[i])
            sub_t.append(sub2_t_s[i])
            sub_w.append(sub_w2[i])
            sub_s.append(sub3[i])        # identity row (gammap.c L1694)
            sub_t.append(sub3[i])
            sub_w.append(sub_w3[i])

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

        train = [gp, sv]
        target = [gv, dv]
        rw = [gw, np.full(len(sv), 1.01)]
        if sub_s:
            train.append(np.array(sub_s))
            target.append(np.array(sub_t))
            rw.append(np.array(sub_w))
        if len(spts):
            train.append(spts)
            target.append(sdv)
            rw.append(ws)
        _tick("Gamut mapping: building the final colour table…")
        # row provenance for diagnostics: (kind, p, v, w) per block
        self._row_blocks = {
            "grey": (gp, gv, gw),
            "guide": (sv, dv, np.full(len(sv), 1.01)),
        }
        if sub_s:
            self._row_blocks["sub"] = (np.array(sub_s), np.array(sub_t),
                                       np.array(sub_w))
        if len(spts):
            self._row_blocks["surf"] = (spts, sdv, ws)
        self._map_il, self._map_ih, self._mapres = map_il, map_ih, mapres
        self._map = Rspl3(np.vstack(train), np.vstack(target),
                          np.concatenate(rw), map_il, map_ih,
                          gres=mapres, smooth=smooth)

        # white/black fine-tune (gammap.c L1799–1856): rigid rotate/scale
        # taking the map's ACTUAL white/black to the exact targets
        from workflow.profile_engine.gammap_port.geom import (apply_3x4,
                                                              vec_rot_mat)
        a_wp = self._map.interp(ga.pre_map(ga.s_mt_wp[None, :]))[0]
        a_bp = self._map.interp(ga.pre_map(ga.s_mt_bp[None, :]))[0]
        self._wbmat = vec_rot_mat(a_wp, a_bp, ga.d_mt_wp, ga.d_mt_bp)
        self._apply_3x4 = apply_3x4
        # satenh (gammap.c: satenh applied BEFORE the wb fine-tune, with
        # wp/bp = the map's own W/B; here both compose at eval time)
        self._satenh = satenh
        self._sat_wp, self._sat_bp = a_wp, a_bp
        self._sat_dst = tri_d

    def _adjust_sat(self, out: np.ndarray) -> np.ndarray:
        """adjust_sat_func (gammap.c L2745+): radially push values toward
        the dest surface, blended to spare near-neutrals."""
        wp, bp, se = self._sat_wp, self._sat_bp, self._satenh
        rr = (out[:, 0] - bp[0]) / (wp[0] - bp[0])
        cp = np.stack([out[:, 0], bp[1] + rr * (wp[1] - bp[1]),
                       bp[2] + rr * (wp[2] - bp[2])], 1)
        mint, maxt, _, _ = self._sat_dst.vector_isect(cp, out)
        ok = np.isfinite(maxt) & (maxt > 1.0)
        p1 = np.where(ok, 1.0 / np.where(ok, maxt, 1.0), 0.0)
        ep1 = (p1 + se * p1) / (1.0 + se * p1)
        pp, g0 = 4.0, 2.0
        vv = p1 / (pp - pp * p1 + 1.0)
        vv = vv * 2.0
        sec = np.floor(vv)
        g = np.where((sec.astype(int) & 1) == 1, -g0, g0)
        vv = vv - sec
        vv = np.where(g >= 0.0, vv / (g - g * vv + 1.0),
                      (vv - g * vv) / (1.0 - g * vv))
        vv = (vv + sec) * 0.5
        bf = (vv + pp * vv) / (1.0 + pp * vv)
        p1n = (1.0 - bf) * p1 + bf * ep1
        t1 = cp + maxt[:, None] * (out - cp)     # surface point
        adj = cp + (t1 - cp) * p1n[:, None]
        return np.where(ok[:, None], adj, out)

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        out = self._map.interp(
            self._ga.pre_map(np.atleast_2d(np.asarray(lab, float))))
        if self._satenh > 0.0:
            out = self._adjust_sat(out)
        return np.atleast_2d(self._apply_3x4(self._wbmat, out))
