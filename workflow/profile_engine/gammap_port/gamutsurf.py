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


class TriSurface:
    """Argyll's actual triangulated gamut surface (from a ``.gam`` file) —
    ray/triangle intersection replaces the binned radial approximation.

    ``radial``/``nradial``/``vector_isect`` match :class:`GamutSurface`'s
    interfaces; intersections are exact Möller–Trumbore over the file's own
    triangles, so the port sees the identical surface Argyll's near_smooth
    sees (measured: the binned table alone cost 0.8 ΔE median).
    """

    def __init__(self, vertices: np.ndarray, triangles: np.ndarray) -> None:
        self.v0 = vertices[triangles[:, 0]]
        e1 = vertices[triangles[:, 1]] - self.v0
        e2 = vertices[triangles[:, 2]] - self.v0
        self.e1 = e1
        self.e2 = e2
        self.normal = np.cross(e1, e2)
        nl = np.linalg.norm(self.normal, axis=1)
        keep = nl > 1e-9
        self.v0, self.e1, self.e2 = self.v0[keep], self.e1[keep], self.e2[keep]
        self.normal = self.normal[keep] / nl[keep][:, None]

    def _ray_hits(self, orig: np.ndarray, d: np.ndarray, chunk: int = 128):
        """Batched Möller–Trumbore: per point, (sorted ts, triangle idx)."""
        out = []
        for lo in range(0, len(orig), chunk):
            o = orig[lo:lo + chunk][:, None, :]     # (C,1,3)
            dd = d[lo:lo + chunk][:, None, :]
            p = np.cross(dd, self.e2[None, :, :])
            det = (self.e1[None, :, :] * p).sum(2)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tvec = o - self.v0[None, :, :]
            u = (tvec * p).sum(2) * inv
            q = np.cross(tvec, self.e1[None, :, :])
            v = (dd * q).sum(2) * inv
            t = (self.e2[None, :, :] * q).sum(2) * inv
            hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
            for j in range(hit.shape[0]):
                hj = np.flatnonzero(hit[j])
                ts = t[j, hj]
                order = np.argsort(ts)
                out.append((ts[order], hj[order]))
        return out

    def _ray_ts(self, orig: np.ndarray, d: np.ndarray):
        return self._ray_hits(orig, d)

    def nearest(self, pts: np.ndarray, chunk: int = 256) -> np.ndarray:
        """Closest point on the mesh surface to each query point.

        Argyll's ``gamut->nearest`` (Ericson closest-point-on-triangle over all
        triangles, per query). Used by near_smooth's expansion swap test
        (``dr = |nearest(dgam, sv)|``), which the radial approximation
        over-triggered for saturated colours.
        """
        pts = np.atleast_2d(np.asarray(pts, float))
        a, ab, ac = self.v0[None], self.e1[None], self.e2[None]  # (1,M,3)
        b, c = a + ab, a + ac
        tiny = 1e-30
        out = np.empty((len(pts), 3))
        for lo in range(0, len(pts), chunk):
            P = pts[lo:lo + chunk][:, None, :]              # (C,1,3)
            ap, bp, cp = P - a, P - b, P - c
            d1 = (ab * ap).sum(-1); d2 = (ac * ap).sum(-1)  # (C,M)
            d3 = (ab * bp).sum(-1); d4 = (ac * bp).sum(-1)
            d5 = (ab * cp).sum(-1); d6 = (ac * cp).sum(-1)
            va = d3 * d6 - d5 * d4
            vb = d5 * d2 - d1 * d6
            vc = d1 * d4 - d3 * d2
            denom = 1.0 / (va + vb + vc + tiny)
            v = (vb * denom)[..., None]; w = (vc * denom)[..., None]
            res = a + ab * v + ac * w                       # face (C,M,3)
            # edges (override face)
            mAB = ((vc <= 0) & (d1 >= 0) & (d3 <= 0))[..., None]
            res = np.where(mAB, a + ab * (d1 / (d1 - d3 + tiny))[..., None], res)
            mAC = ((vb <= 0) & (d2 >= 0) & (d6 <= 0))[..., None]
            res = np.where(mAC, a + ac * (d2 / (d2 - d6 + tiny))[..., None], res)
            mBC = ((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0))[..., None]
            tBC = ((d4 - d3) / ((d4 - d3) + (d5 - d6) + tiny))[..., None]
            res = np.where(mBC, b + (c - b) * tBC, res)
            # vertices (win)
            res = np.where(((d1 <= 0) & (d2 <= 0))[..., None],
                           np.broadcast_to(a, res.shape), res)
            res = np.where(((d3 >= 0) & (d4 <= d3))[..., None],
                           np.broadcast_to(b, res.shape), res)
            res = np.where(((d6 >= 0) & (d5 <= d6))[..., None],
                           np.broadcast_to(c, res.shape), res)
            d2m = ((res - P) ** 2).sum(2)                   # (C,M)
            j = d2m.argmin(1)
            out[lo:lo + chunk] = res[np.arange(res.shape[0]), j]
        return out

    def surface_radius(self, d: np.ndarray, chunk: int = 4096) -> np.ndarray:
        """Radius along unit directions from the centre (max ray crossing).

        Fully vectorised Möller–Trumbore from CENT — no per-point Python
        loop (the loopy version made the exact-geometry guide pipeline
        impractically slow). Rays start at the gamut centre, so all
        crossings have t > 0; the max is the outer surface radius.
        """
        d = np.atleast_2d(np.asarray(d, float))
        n = len(d)
        out = np.full(n, 1e-9)
        v0, e1, e2 = self.v0, self.e1, self.e2
        for lo in range(0, n, chunk):
            dd = d[lo:lo + chunk][:, None, :]           # (C,1,3)
            p = np.cross(dd, e2[None, :, :])
            det = (e1[None, :, :] * p).sum(2)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tvec = CENT[None, None, :] - v0[None, :, :]
            u = (tvec * p).sum(2) * inv
            q = np.cross(np.broadcast_to(tvec, (dd.shape[0],) + v0.shape),
                         e1[None, :, :])
            v = (dd * q).sum(2) * inv
            t = (e2[None, :, :] * q).sum(2) * inv
            hit = (ok & (u >= -1e-9) & (v >= -1e-9)
                   & (u + v <= 1 + 1e-9) & (t > 1e-9))
            tt = np.where(hit, t, -1.0)
            out[lo:lo + chunk] = np.maximum(tt.max(1), 1e-9)
        return out

    def radial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        d = rel / r[:, None]
        rr = self.surface_radius(d)
        return CENT[None, :] + d * rr[:, None]

    def nradial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        rs = self.surface_radius(rel / r[:, None])
        return r / np.maximum(rs, 1e-9)

    def vector_isect(self, sv: np.ndarray, dv: np.ndarray, samples: int = 0
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        sv = np.atleast_2d(np.asarray(sv, float))
        dv = np.atleast_2d(np.asarray(dv, float))
        d = dv - sv
        hits = self._ray_ts(sv, d)
        n = len(sv)
        mint = np.full(n, np.nan)
        maxt = np.full(n, np.nan)
        n_min = np.zeros((n, 3))
        n_max = np.zeros((n, 3))
        for i, (ts, idx) in enumerate(hits):
            if len(ts) == 0:
                continue
            mint[i] = ts[0]
            maxt[i] = ts[-1]
            n_min[i] = self.normal[idx[0]]
            n_max[i] = self.normal[idx[-1]]
        return mint, maxt, n_min, n_max


class IntersectSurface:
    """Exact intersection of two triangulated gamuts (per-direction min
    radius) — Argyll's nedst_gam = intersection of source and destination.
    ``radial``/``nradial``/``surface_radius`` take the smaller of the two
    surfaces' exact radii along each direction; ``vector_isect`` uses the
    primary (destination) surface, matching how gammap.c clips."""

    def __init__(self, tri_primary: "TriSurface", tri_other: "TriSurface"
                 ) -> None:
        self._tri = tri_primary
        self._other = tri_other

    def surface_radius(self, d: np.ndarray) -> np.ndarray:
        return np.minimum(self._tri.surface_radius(d),
                          self._other.surface_radius(d))

    def radial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        dd = rel / r[:, None]
        return CENT[None, :] + dd * self.surface_radius(dd)[:, None]

    def nradial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.linalg.norm(rel, axis=1)
        dd = rel / np.maximum(r, 1e-9)[:, None]
        return r / np.maximum(self.surface_radius(dd), 1e-9)

    def vector_isect(self, a: np.ndarray, b: np.ndarray, samples: int = 0):
        return self._tri.vector_isect(a, b)


class SampledSurface:
    """Fast exact-ish surface: the TriSurface's radial field sampled once
    onto a fine (hue × inclination) grid (default 0.5°), then bilinear —
    tri accuracy (smooth field, dense sampling) at table speed. Falls back
    to the TriSurface for vector_isect (exact crossings needed there)."""

    def __init__(self, tri: "TriSurface", nh: int = 720, nb: int = 360
                 ) -> None:
        self._tri = tri
        self.nh = nh
        self.nb = nb
        hue = (np.arange(nh) + 0.5) / nh * 2 * np.pi
        incl = (np.arange(nb) + 0.5) / nb * np.pi
        hh, bb = np.meshgrid(hue, incl, indexing="ij")
        d = np.stack([np.cos(bb), np.sin(bb) * np.cos(hh),
                      np.sin(bb) * np.sin(hh)], -1).reshape(-1, 3)
        self.tab = tri.surface_radius(d).reshape(nh, nb)

    def _radius(self, dirs: np.ndarray) -> np.ndarray:
        r = np.maximum(np.linalg.norm(dirs, axis=1), 1e-9)
        incl = np.arccos(np.clip(dirs[:, 0] / r, -1, 1))
        hue = np.arctan2(dirs[:, 2], dirs[:, 1]) % (2 * np.pi)
        fh = hue / (2 * np.pi) * self.nh - 0.5
        fb = np.clip(incl / np.pi * self.nb - 0.5, 0.0, self.nb - 1.0)
        h0 = np.floor(fh).astype(int) % self.nh
        h1 = (h0 + 1) % self.nh
        b0 = fb.astype(int)
        b1 = np.minimum(b0 + 1, self.nb - 1)
        wh = fh - np.floor(fh)
        wb = fb - b0
        return ((1 - wh) * (1 - wb) * self.tab[h0, b0]
                + wh * (1 - wb) * self.tab[h1, b0]
                + (1 - wh) * wb * self.tab[h0, b1]
                + wh * wb * self.tab[h1, b1])

    def radial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.maximum(np.linalg.norm(rel, axis=1), 1e-9)
        rr = self._radius(rel)
        return CENT[None, :] + rel * (rr / r)[:, None]

    def nradial(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        rel = pts - CENT[None, :]
        r = np.linalg.norm(rel, axis=1)
        return r / np.maximum(self._radius(rel), 1e-9)

    def vector_isect(self, a: np.ndarray, b: np.ndarray, samples: int = 0):
        return self._tri.vector_isect(a, b)
