"""icclib geometry helpers used by gammap/nearsmth (stage-2 support).

Source: ArgyllCMS 3.5.0 ``icc/icc_util.c`` (AGPL-3.0, Graeme W. Gill — see
package ``__init__``): ``icmRotMat`` (L616–698), ``icmVecRotMat``
(L806–853), ``icmPlaneEqn3`` (L991–1024), ``icmPlaneDist3`` (L1027–1036),
plus the trivial Lab↔LCh pair. Translated line-faithfully; the unit tests
pin behaviour to the C invariants (the ``#ifdef NEVER`` self-checks in the
source: the matrices must map the defining points exactly).

Note ``icmRotMat`` is rotation *with scale* ``|t|/|s|`` — it maps vector s
onto vector t including length, so ``icmVecRotMat`` maps the segment
s0→s1 onto t0→t1 exactly.
"""
from __future__ import annotations

import numpy as np


def rot_mat(s: np.ndarray, t: np.ndarray) -> np.ndarray:
    """3×3 matrix rotating (and scaling) vector ``s`` onto ``t``
    (icmRotMat, icc_util.c L616)."""
    s = np.asarray(s, dtype=float)
    t = np.asarray(t, dtype=float)
    sl = float(np.linalg.norm(s))
    tl = float(np.linalg.norm(t))
    if sl < 1e-12 or tl < 1e-12:
        return np.eye(3)
    sn = s / sl
    tn = t / tl
    v = np.cross(sn, tn)
    e = float(sn @ tn)
    h = float(v @ v)
    if abs(h) < 1e-12:
        # (anti)parallel: pure scale, sign per direction (C L657–671)
        if float(s @ t) < 0.0:
            tl = -tl
        return np.eye(3) * (tl / sl)
    h = (1.0 - e) / h
    v0, v1, v2 = v
    m = np.array([
        [e + h * v0 * v0, h * v0 * v1 - v2, h * v0 * v2 + v1],
        [h * v0 * v1 + v2, e + h * v1 * v1, h * v1 * v2 - v0],
        [h * v0 * v2 - v1, h * v1 * v2 + v0, e + h * v2 * v2],
    ])
    return (tl / sl) * m


def vec_rot_mat(s1: np.ndarray, s0: np.ndarray, t1: np.ndarray,
                t0: np.ndarray) -> np.ndarray:
    """3×4 transform mapping segment s0→s1 onto t0→t1 (icmVecRotMat)."""
    s0 = np.asarray(s0, dtype=float)
    t0 = np.asarray(t0, dtype=float)
    rr = rot_mat(np.asarray(s1, dtype=float) - s0,
                 np.asarray(t1, dtype=float) - t0)
    m = np.zeros((3, 4))
    m[:, :3] = rr
    m[:, 3] = t0 - rr @ s0
    return m


def apply_3x4(m: np.ndarray, p: np.ndarray) -> np.ndarray:
    """icmMul3By3x4 — affine transform, vectorised over (N, 3)."""
    p = np.atleast_2d(np.asarray(p, dtype=float))
    out = p @ m[:, :3].T + m[:, 3][None, :]
    return out[0] if out.shape[0] == 1 and p.ndim == 2 and len(p) == 1 else out


def plane_eqn(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray
              ) -> np.ndarray | None:
    """Normalised plane equation [nx, ny, nz, d] through three points
    (icmPlaneEqn3); None for degenerate input."""
    p0 = np.asarray(p0, dtype=float)
    v2 = np.asarray(p1, dtype=float) - p0
    v1 = np.asarray(p2, dtype=float) - p0
    n = np.cross(v1, v2)
    ll = float(np.linalg.norm(n))
    if ll < 1e-10:
        return None
    n = n / ll
    return np.array([n[0], n[1], n[2], -float(n @ p0)])


def plane_dist(eq: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Signed distance(s) of point(s) from the plane (icmPlaneDist3)."""
    p = np.atleast_2d(np.asarray(p, dtype=float))
    d = p @ eq[:3] + eq[3]
    return float(d[0]) if len(d) == 1 else d


def lab_to_lch(lab: np.ndarray) -> np.ndarray:
    """icmLab2LCh — hue in degrees 0..360."""
    lab = np.atleast_2d(np.asarray(lab, dtype=float))
    c = np.hypot(lab[:, 1], lab[:, 2])
    h = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    out = np.stack([lab[:, 0], c, h], 1)
    return out[0] if len(out) == 1 else out


def lch_to_lab(lch: np.ndarray) -> np.ndarray:
    """icmLCh2Lab."""
    lch = np.atleast_2d(np.asarray(lch, dtype=float))
    hr = np.radians(lch[:, 2])
    out = np.stack([lch[:, 0], lch[:, 1] * np.cos(hr),
                    lch[:, 1] * np.sin(hr)], 1)
    return out[0] if len(out) == 1 else out
