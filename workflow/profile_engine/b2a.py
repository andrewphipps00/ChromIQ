"""B2A construction: Lab grid → device, by inverting the forward model.

Maths C of issue #122. Batched, damped Gauss–Newton over all CLUT nodes at
once (finite-difference Jacobian, one ``np.linalg.solve`` per iteration over
the whole batch — measured in the spike: in-gamut nodes converge to median
ΔE ≈ 0.007).

For more device channels than the 3 PCS dimensions the inversion is
underdetermined; the *ink policy* pins the surplus channels before GN solves
the remainder:

* channel 4 (K) follows a GCR-style locus ``K(L*)`` — full black only in the
  shadows, fading out by the midtones (the shape colprof's ``-k`` exposes);
* channels beyond 4 (O/G/V/…) are hue-gated: an ink participates only in the
  hue sector around its own Lab anchor hue (``max(0, cos(h − h_ink))^p``).

Out-of-gamut nodes clamp to the nearest printable colour; their residual ΔE
doubles as the ``gamt`` gamut-distance table (ColorSync requires that tag).
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.forward_model import ForwardModel
from workflow.profile_engine.icc_writer import lab_grid_axes

# Lab hue anchors for extra inks, keyed by COLOR_REP letter. Measured hues of
# the EXTRA_INK display anchors used across ChromIQ (ui.tiff_preview).
_EXTRA_INK_HUE = {
    "O": 55.0, "R": 30.0, "G": 136.0, "B": 260.0, "V": 300.0,
}


def lab_grid(grid: int) -> np.ndarray:
    """(grid³, 3) Lab CLUT node targets over the legacy-encoding axes."""
    ls, ab = lab_grid_axes(grid)
    return np.stack(np.meshgrid(ls, ab, ab, indexing="ij"), -1).reshape(-1, 3)


def _model_jacobian(model: ForwardModel, d: np.ndarray, free: np.ndarray,
                    f0: np.ndarray, h: float = 1e-3) -> np.ndarray:
    """(N, 3, n_free) finite-difference Jacobian over the free channels."""
    jac = np.empty((len(d), 3, len(free)))
    for j, ch in enumerate(free):
        dp = d.copy()
        dp[:, ch] = np.clip(dp[:, ch] + h, 0.0, 1.0)
        jac[:, :, j] = (model.predict(dp) - f0) / h
    return jac


def _gauss_newton(model: ForwardModel, target: np.ndarray, seed: np.ndarray,
                  free: np.ndarray, *, iters: int, damping: float,
                  ink_limit: float | None) -> np.ndarray:
    d = seed.copy()
    eye = np.eye(len(free))
    for _ in range(iters):
        f0 = model.predict(d)
        r = target - f0
        jac = _model_jacobian(model, d, free, f0)
        jtj = np.einsum("nik,nil->nkl", jac, jac) + damping * eye[None]
        jtr = np.einsum("nik,ni->nk", jac, r)
        step = np.linalg.solve(jtj, jtr[..., None])[..., 0]
        d[:, free] = np.clip(d[:, free] + step, 0.0, 1.0)
        if ink_limit is not None:
            total = d.sum(1)
            over = total > ink_limit
            if over.any():
                d[over] *= (ink_limit / total[over])[:, None]
    return d


def _seed_nearest(model: ForwardModel, target: np.ndarray, seed_res: int
                  ) -> np.ndarray:
    """Seed each target with the nearest point of a coarse device mesh."""
    n = model.n_channels
    axes = [np.linspace(0.0, 1.0, seed_res)] * n
    mesh = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, n)
    mesh_lab = model.predict(mesh)
    out = np.empty((len(target), n))
    for lo in range(0, len(target), 4096):      # chunked distance search
        chunk = target[lo:lo + 4096]
        d2 = ((mesh_lab[None, :, :] - chunk[:, None, :]) ** 2).sum(2)
        out[lo:lo + 4096] = mesh[np.argmin(d2, 1)]
    return out


def k_locus(lightness: np.ndarray, *, k_max: float = 1.0,
            l_start: float = 60.0, l_full: float = 5.0,
            gamma: float = 1.6) -> np.ndarray:
    """GCR-style black amount as a function of target L* (0 above ``l_start``,
    ``k_max`` at ``l_full``, smooth power ramp between)."""
    t = np.clip((l_start - lightness) / max(l_start - l_full, 1e-6), 0.0, 1.0)
    return k_max * t ** gamma


def extra_ink_amount(target: np.ndarray, letter: str, *,
                     power: float = 3.0) -> np.ndarray:
    """Hue-gated participation 0..1 for an extra ink at each Lab target."""
    hue = _EXTRA_INK_HUE.get(letter)
    if hue is None:
        return np.zeros(len(target))
    chroma = np.hypot(target[:, 1], target[:, 2])
    h = np.degrees(np.arctan2(target[:, 2], target[:, 1])) % 360.0
    gate = np.maximum(0.0, np.cos(np.radians(h - hue))) ** power
    # Only saturated colours pull the spot ink in; neutrals never do.
    sat = np.clip((chroma - 15.0) / 60.0, 0.0, 1.0)
    return gate * sat


def invert_to_device(model: ForwardModel, target: np.ndarray, *,
                     channel_letters: list[str], is_additive: bool,
                     ink_limit: float | None = None,
                     iters: int = 6, damping: float = 0.05,
                     seed_res: int = 7,
                     seed: np.ndarray | None = None,
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Invert the forward model at ``target`` Lab points.

    Returns ``(device, residual_de)`` — residual is the remaining ΔE76 after
    convergence, i.e. ~0 in gamut and the clamp distance outside (this array
    *is* the ``gamt`` table content).
    """
    n = model.n_channels
    limit = None if ink_limit is None or is_additive else ink_limit / 100.0
    if seed is None:
        seed = _seed_nearest(model, target, seed_res)
    d = seed.copy()

    if n <= 3:
        free = np.arange(n)
    else:
        # Policy channels first: K from the L* locus, extras from hue gates;
        # GN then solves the first three (C, M, Y) exactly.
        free = np.arange(3)
        d[:, 3] = k_locus(target[:, 0])
        for ch in range(4, n):
            d[:, ch] = extra_ink_amount(target, channel_letters[ch])

    d = _gauss_newton(model, target, d, free, iters=iters, damping=damping,
                      ink_limit=limit)
    residual = np.linalg.norm(model.predict(d) - target, axis=1)

    # Projected GN can stall with a channel pinned against the wrong cube
    # face (measured: ~20% of near-saturation targets, while a good seed
    # never fails). Retry the failures from a dense-cloud nearest seed and
    # keep whichever lands closer.
    retry = residual > 0.5
    if retry.any():
        rng = np.random.default_rng(1234)
        cloud = rng.uniform(0.0, 1.0, (min(40000, 6000 * n), n))
        if limit is not None:
            total = cloud.sum(1)
            over = total > limit
            cloud[over] *= (limit / total[over])[:, None]
        cloud_lab = model.predict(cloud)
        sub = target[retry]
        seeds2 = np.empty((len(sub), n))
        cl2 = (cloud_lab ** 2).sum(1)
        for lo in range(0, len(sub), 2048):
            chunk = sub[lo:lo + 2048]
            d2 = cl2[None, :] - 2.0 * chunk @ cloud_lab.T
            seeds2[lo:lo + 2048] = cloud[np.argmin(d2, 1)]
        if n > 3:
            seeds2[:, 3] = d[retry][:, 3]
            seeds2[:, 4:] = d[retry][:, 4:]
        d_retry = _gauss_newton(model, sub, seeds2, free, iters=iters,
                                damping=damping, ink_limit=limit)
        res_retry = np.linalg.norm(model.predict(d_retry) - sub, axis=1)
        better = res_retry < residual[retry]
        idx = np.flatnonzero(retry)[better]
        d[idx] = d_retry[better]
        residual[idx] = res_retry[better]
    return d, residual


def build_b2a_clut(model: ForwardModel, grid: int, *,
                   channel_letters: list[str], is_additive: bool,
                   ink_limit: float | None = None,
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Full B2A CLUT: (grid³, n) device fractions + (grid³,) OOG distance."""
    target = lab_grid(grid)
    return invert_to_device(model, target, channel_letters=channel_letters,
                            is_additive=is_additive, ink_limit=ink_limit)


def refine_b2a_clut(model: ForwardModel, dev_clut: np.ndarray,
                    residual: np.ndarray, grid: int, *,
                    ink_limit: float | None = None,
                    is_additive: bool = True,
                    samples: int = 30000, lam: float = 0.03,
                    deep_oog: float = 5.0) -> np.ndarray:
    """Refit the B2A CLUT as one smooth field over exact inverse samples.

    Every random device point is an *exact* sample of the inverse function
    (its Lab comes from the forward model, its device value is known), so the
    whole B2A grid can be least-squares fitted to tens of thousands of them —
    trilinear interpolation between nodes is then accurate by construction,
    which removes the boundary-cell kink that per-node inversion leaves
    (measured: round-trip max 15 ΔE → the kink cells mix converged and
    clamped nodes). Nodes deep out of gamut keep their nearest-surface clamp
    values via strong anchors; near-boundary nodes get weak anchors so the
    fit may extrapolate smoothly across the gamut surface.
    """
    from workflow.profile_engine.forward_model import (_grid_solve,
                                                       _interp_weights)
    n = model.n_channels
    rng = np.random.default_rng(99)
    dev_s = rng.uniform(0.0, 1.0, (samples, n))
    # Extra samples on the device-cube faces: the gamut boundary is their
    # image, and boundary cells are exactly where interpolation needs the
    # most support (measured: halves the worst-case round-trip error).
    nf = samples // 2
    faces = rng.uniform(0.0, 1.0, (nf, n))
    faces[np.arange(nf), rng.integers(0, n, nf)] = \
        rng.integers(0, 2, nf).astype(float)
    dev_s = np.vstack([dev_s, faces])
    limit = None if ink_limit is None or is_additive else ink_limit / 100.0
    if limit is not None:
        total = dev_s.sum(1)
        over = total > limit
        dev_s[over] *= (limit / total[over])[:, None]
    lab_s = model.predict(dev_s)

    ls, ab = lab_grid_axes(grid)
    span = np.array([ls[-1] - ls[0], ab[-1] - ab[0], ab[-1] - ab[0]])
    origin = np.array([ls[0], ab[0], ab[0]])

    def to01(lab: np.ndarray) -> np.ndarray:
        return np.clip((lab - origin[None, :]) / span[None, :], 0.0, 1.0)

    # Anchor rows: every node contributes its v1 value — heavy anchors deep
    # out of gamut (their clamp IS the answer there), light anchors elsewhere
    # (keep the fit stable where samples are sparse, let data win).
    anchor_w = np.where(residual > deep_oog, 4.0, 0.05)
    node_lab = lab_grid(grid)
    # Fit in *curve space* — the CLUT stores shaped device values (the output
    # shaper tables undo them), so interpolation accuracy must be optimised
    # in the space the CMM actually interpolates in.
    p_all = np.vstack([to01(lab_s), to01(node_lab)])
    y_all = np.vstack([model.shape_device(dev_s),
                       model.shape_device(dev_clut)])
    w_all = np.concatenate([np.ones(len(dev_s)), anchor_w])

    w, cols = _interp_weights(p_all, grid, 3)
    sw = np.sqrt(w_all)[:, None]
    refined = _grid_solve(w * sw, cols, y_all * sw, grid, 3, lam, 400,
                          x0=model.shape_device(dev_clut))
    refined = np.clip(refined, 0.0, 1.0)
    if limit is not None:
        raw = model.unshape_device(refined)
        total = raw.sum(1)
        over = total > limit
        raw[over] *= (limit / total[over])[:, None]
        refined[over] = model.shape_device(raw[over])
    return refined


def inverse_curves(curves: np.ndarray, knots: int = 256) -> np.ndarray:
    """Per-channel inverse of monotone 0..1 shaper curves (for B2A out tables).

    Storing *curve-space* device values in the B2A CLUT and undoing them in
    the output shaper tables linearises the CLUT contents — the same device
    non-linearity the A2B input curves absorb would otherwise sit as
    curvature inside the B2A grid cells and show up as interpolation error
    (measured: the high-chroma boundary-cell tail).
    """
    n, k = curves.shape
    xs = np.linspace(0.0, 1.0, knots)
    xp = np.linspace(0.0, 1.0, k)
    out = np.empty((n, knots))
    for c in range(n):
        out[c] = np.interp(xs, curves[c], xp)   # swap axes = inverse
    return out


def smooth_oog(device: np.ndarray, residual: np.ndarray, grid: int,
               threshold: float = 1.0, rounds: int = 2) -> np.ndarray:
    """Average out-of-gamut nodes with their 6-neighbourhood.

    In-gamut nodes are authoritative (GN converged there); OOG clamps can land
    on different cube faces from one node to the next, so a little diffusion
    keeps the separations smooth where the data doesn't constrain them.
    """
    n = device.shape[1]
    dev = device.reshape(grid, grid, grid, n).copy()
    oog = (residual > threshold).reshape(grid, grid, grid)
    pad_spec = [(1, 1)] * 3 + [(0, 0)]
    for _ in range(rounds):
        padded = np.pad(dev, pad_spec, mode="edge")
        acc = np.zeros_like(dev)
        for ax in range(3):
            sl_lo = [slice(1, -1)] * 3 + [slice(None)]
            sl_hi = [slice(1, -1)] * 3 + [slice(None)]
            sl_lo[ax] = slice(0, -2)
            sl_hi[ax] = slice(2, None)
            acc += padded[tuple(sl_lo)] + padded[tuple(sl_hi)]
        blur = acc / 6.0
        dev[oog] = 0.5 * dev[oog] + 0.5 * blur[oog]
    out = dev.reshape(-1, n)
    return np.clip(out, 0.0, 1.0)
