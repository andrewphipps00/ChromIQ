"""B2A construction: Lab grid → device, by inverting the forward model.

Maths C of issue #122. Batched, damped Gauss–Newton over all CLUT nodes at
once (finite-difference Jacobian, one ``np.linalg.solve`` per iteration over
the whole batch — measured in the spike: in-gamut nodes converge to median
ΔE ≈ 0.007).

For more device channels than the 3 PCS dimensions the inversion is
underdetermined; the *ink policy* resolves the surplus degrees of freedom as
soft least-squares priors inside GN (a hard policy measurably shrinks the
reachable gamut — colour accuracy always dominates):

* channel 4 (K) is pulled toward a GCR-style locus ``K(L*)`` — full black
  only in the shadows, fading out by the midtones (the shape colprof's
  ``-k`` exposes);
* channels beyond 4 (O/G/V/…) are pulled toward hue gates: an ink
  participates in the hue sector around its own Lab anchor hue
  (``max(0, cos(h − h_ink))^p``), and never on neutrals.

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
                    f0: np.ndarray, h: float = 1e-3,
                    boundary_fd: bool = False) -> np.ndarray:
    """(N, 3, n_free) finite-difference Jacobian over the free channels.

    ``boundary_fd``: with the plain forward difference, a channel pinned at
    1.0 clips to itself — its Jacobian column is exactly zero and GN can
    never move it off the face (the root cause of the near-saturation
    stalls the retry pass patches over). Switching to a backward difference
    within ``h`` of the top face keeps the column alive everywhere.
    """
    jac = np.empty((len(d), 3, len(free)))
    for j, ch in enumerate(free):
        dp = d.copy()
        if boundary_fd:
            sign = np.where(d[:, ch] + h <= 1.0, 1.0, -1.0)
            dp[:, ch] = np.clip(d[:, ch] + sign * h, 0.0, 1.0)
            jac[:, :, j] = (model.predict(dp) - f0) / (sign * h)[:, None]
        else:
            dp[:, ch] = np.clip(dp[:, ch] + h, 0.0, 1.0)
            jac[:, :, j] = (model.predict(dp) - f0) / h
    return jac


def project_tac(d: np.ndarray, limit: float) -> np.ndarray:
    """Euclidean projection of device rows onto ``{x ≥ 0, Σx ≤ limit}``.

    The parity path enforces the total ink limit by scaling the whole
    vector, which lightens K along with everything else — exactly in the
    deep shadows where the limit binds. The Euclidean projection subtracts
    a common amount instead (clipped at zero), which preserves the dense
    channels and lands strictly closer to the unconstrained solution.
    Rows already under the limit are returned unchanged.
    """
    d = d.copy()
    over = d.sum(1) > limit
    if not over.any():
        return d
    sub = np.clip(d[over], 0.0, None)
    u = np.sort(sub, axis=1)[:, ::-1]
    css = np.cumsum(u, axis=1) - limit
    ks = np.arange(1, sub.shape[1] + 1)[None, :]
    rho = (u - css / ks > 0).sum(1)
    theta = css[np.arange(len(sub)), rho - 1] / rho
    d[over] = np.maximum(sub - theta[:, None], 0.0)
    return d


# Hue-preserving clip weights: error components in the (ΔL*, ΔC*, Δh)
# frame of the target colour. Hue errors are punished hardest — a clipped
# saturated colour should lose chroma, not change colour family.
_CLIP_W_L, _CLIP_W_C, _CLIP_W_H = 1.0, 0.7, 3.0


def _hue_weight_matrices(target: np.ndarray) -> np.ndarray:
    """(N,3,3) weighting matrices W so ``W·ΔLab`` measures the error in a
    hue-preserving norm at each target (identity for near-neutrals, where
    there is no hue to preserve)."""
    n = len(target)
    chroma = np.hypot(target[:, 1], target[:, 2])
    h = np.arctan2(target[:, 2], target[:, 1])
    ch, sh = np.cos(h), np.sin(h)
    w = np.zeros((n, 3, 3))
    w[:, 0, 0] = _CLIP_W_L
    w[:, 1, 1] = _CLIP_W_C * ch * ch + _CLIP_W_H * sh * sh
    w[:, 1, 2] = (_CLIP_W_C - _CLIP_W_H) * ch * sh
    w[:, 2, 1] = w[:, 1, 2]
    w[:, 2, 2] = _CLIP_W_C * sh * sh + _CLIP_W_H * ch * ch
    neutral = chroma < 5.0
    w[neutral] = np.eye(3)
    return w


def _gauss_newton(model: ForwardModel, target: np.ndarray, seed: np.ndarray,
                  free: np.ndarray, *, iters: int, damping: float,
                  ink_limit: float | None,
                  prior: np.ndarray | None = None,
                  prior_w: np.ndarray | None = None,
                  boundary_fd: bool = False,
                  tac_projection: bool = False,
                  err_weights: np.ndarray | None = None) -> np.ndarray:
    """Batched damped Gauss–Newton on the free channels.

    ``prior``/``prior_w``: optional per-channel soft targets over the free
    channels (the ink policy). They enter as extra least-squares rows, so
    colour accuracy always dominates — the priors only resolve the surplus
    degrees of freedom that n > 3 devices have.
    ``err_weights``: optional (N,3,3) matrices reshaping the Lab error norm
    per point (the hue-preserving clip); ``boundary_fd``/``tac_projection``
    are the maximum-accuracy levers (see :func:`_model_jacobian` /
    :func:`project_tac`).
    """
    d = seed.copy()
    eye = np.eye(len(free))
    for _ in range(iters):
        f0 = model.predict(d)
        r = target - f0
        jac = _model_jacobian(model, d, free, f0, boundary_fd=boundary_fd)
        if err_weights is not None:
            r = np.einsum("nij,nj->ni", err_weights, r)
            jac = np.einsum("nij,njk->nik", err_weights, jac)
        jtj = np.einsum("nik,nil->nkl", jac, jac) + damping * eye[None]
        jtr = np.einsum("nik,ni->nk", jac, r)
        if prior is not None:
            jtj += np.einsum("nk,kl->nkl", prior_w, eye)
            jtr += prior_w * (prior - d[:, free])
        step = np.linalg.solve(jtj, jtr[..., None])[..., 0]
        if boundary_fd:
            # Active-set guard: with the boundary-aware Jacobian a pinned
            # channel keeps a live column, so an *unreachable* target keeps
            # pulling it outward — the clipped joint step then distorts the
            # other channels. Drop the outward-pointing pinned columns and
            # re-solve; channels wanting to move inward stay free (that is
            # the stall fix).
            eps = 1e-9
            bad = (((d[:, free] >= 1.0 - eps) & (step > 0))
                   | ((d[:, free] <= eps) & (step < 0)))
            if bad.any():
                jac_m = jac * (~bad)[:, None, :]
                jtj = (np.einsum("nik,nil->nkl", jac_m, jac_m)
                       + damping * eye[None])
                jtr = np.einsum("nik,ni->nk", jac_m, r)
                if prior is not None:
                    jtj += np.einsum("nk,kl->nkl", prior_w, eye)
                    jtr += prior_w * (prior - d[:, free])
                step = np.linalg.solve(jtj, jtr[..., None])[..., 0]
                step[bad] = 0.0
        d[:, free] = np.clip(d[:, free] + step, 0.0, 1.0)
        if ink_limit is not None:
            if tac_projection:
                d = project_tac(d, ink_limit)
            else:
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
                     power: float = 3.0,
                     hue_override: float | None = None) -> np.ndarray:
    """Hue-gated participation 0..1 for an extra ink at each Lab target.

    ``hue_override``: the ink's *measured* hue from the chart's own solid
    patch (maximum-accuracy mode) — the anchor table is only a fallback.
    """
    hue = hue_override if hue_override is not None \
        else _EXTRA_INK_HUE.get(letter)
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
                     k_prior: dict | None = None,
                     accurate: bool = False,
                     extra_hues: dict[str, float] | None = None,
                     black_l: float | None = None,
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Invert the forward model at ``target`` Lab points.

    Returns ``(device, residual_de)`` — residual is the remaining ΔE76 after
    convergence, i.e. ~0 in gamut and the clamp distance outside (this array
    *is* the ``gamt`` table content).

    ``accurate`` (maximum-accuracy mode) switches on the boundary-aware
    Jacobian, Euclidean TAC projection and a hue-preserving re-clip of the
    out-of-gamut nodes; ``extra_hues`` carries the measured extra-ink hues.
    ``black_l`` anchors the GCR locus's full-black point on the printer's
    *measured* black L* — the in-gamut K separation then converges to the
    max-density clamp at the gamut boundary instead of jumping between
    metameric alternatives on adjacent nodes (shadow banding).
    """
    n = model.n_channels
    limit = None if ink_limit is None or is_additive else ink_limit / 100.0
    if n > 3:
        iters = max(iters, 10)      # surplus dof converge slower with priors
    if seed is None:
        seed = _seed_nearest(model, target, seed_res if n <= 4 else 5)
    d = seed.copy()

    free = np.arange(n)
    prior = prior_w = None
    if n > 3:
        # All channels stay free (a hard policy shrinks the reachable gamut
        # — measured: median 18 ΔE on random in-gamut targets). The policy
        # enters as soft least-squares priors instead: K follows the GCR
        # locus, extra inks their hue gates, C/M/Y are unconstrained.
        prior = np.zeros((len(target), n))
        prior_w = np.zeros((len(target), n))
        if k_prior is not None:
            # colprof-calibrated K behaviour (CMYK proxy oracle) — a firmer
            # prior than the generic locus, matching how colprof separates.
            prior[:, 3] = np.interp(target[:, 0], k_prior["l_axis"],
                                    k_prior["k_curve"])
            prior_w[:, 3] = 0.15
        elif accurate and black_l is not None:
            prior[:, 3] = k_locus(target[:, 0],
                                  l_full=max(float(black_l), 2.0))
            prior_w[:, 3] = 0.05
        else:
            prior[:, 3] = k_locus(target[:, 0])
            prior_w[:, 3] = 0.05
        if accurate:
            # Dark neutrals have the widest metameric freedom — a weak K
            # prior lets adjacent B2A nodes settle on different K/CMY splits
            # (visible as banding in shadow gradients). Firm the K prior up
            # where that freedom lives and fade it out with chroma, so the
            # gamut-relevant saturated targets keep their full freedom.
            chroma_t = np.hypot(target[:, 1], target[:, 2])
            neutral_w = np.exp(-(chroma_t / 25.0) ** 2)
            prior_w[:, 3] = np.maximum(prior_w[:, 3], 2.0 * neutral_w)
        hues = extra_hues or {}
        for ch in range(4, n):
            prior[:, ch] = extra_ink_amount(
                target, channel_letters[ch],
                hue_override=hues.get(channel_letters[ch]))
            prior_w[:, ch] = 0.05
        d[:, 3:] = prior[:, 3:]

    gn_kw = dict(boundary_fd=accurate, tac_projection=accurate)
    d = _gauss_newton(model, target, d, free, iters=iters, damping=damping,
                      ink_limit=limit, prior=prior, prior_w=prior_w, **gn_kw)
    residual = np.linalg.norm(model.predict(d) - target, axis=1)

    # Projected GN can stall with a channel pinned against the wrong cube
    # face (measured: ~20% of near-saturation targets, while a good seed
    # never fails; the boundary-aware Jacobian removes the root cause but
    # the retry stays as a safety net). Retry the failures from a
    # dense-cloud nearest seed and keep whichever lands closer.
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
        d_retry = _gauss_newton(
            model, sub, seeds2, free, iters=iters, damping=damping,
            ink_limit=limit,
            prior=None if prior is None else prior[retry],
            prior_w=None if prior_w is None else prior_w[retry], **gn_kw)
        res_retry = np.linalg.norm(model.predict(d_retry) - sub, axis=1)
        better = res_retry < residual[retry]
        idx = np.flatnonzero(retry)[better]
        d[idx] = d_retry[better]
        residual[idx] = res_retry[better]

    if accurate:
        # Hue-preserving clip: nodes that stay out of gamut are re-clipped
        # under a norm that punishes hue errors hardest — a clipped
        # saturated colour loses chroma instead of changing colour family.
        # The *residual* keeps the nearest-clip distance from above: the
        # ``gamt`` tag encodes distance-from-gamut and must stay metric.
        oog = residual > 1.0
        if oog.any():
            wm = _hue_weight_matrices(target[oog])
            d[oog] = _gauss_newton(
                model, target[oog], d[oog], free, iters=8, damping=damping,
                ink_limit=limit, err_weights=wm,
                prior=None if prior is None else prior[oog],
                prior_w=None if prior_w is None else prior_w[oog], **gn_kw)
    return d, residual


def build_b2a_clut(model: ForwardModel, grid: int, *,
                   channel_letters: list[str], is_additive: bool,
                   ink_limit: float | None = None,
                   node_lab: np.ndarray | None = None,
                   k_prior: dict | None = None,
                   accurate: bool = False,
                   extra_hues: dict[str, float] | None = None,
                   black_l: float | None = None,
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Full B2A CLUT: (grid³, n) device fractions + (grid³,) OOG distance.

    ``node_lab`` overrides the CLUT node targets (the XYZ-PCS grid of an
    ``-a x`` profile, expressed in Lab); default = the legacy Lab16 grid.
    """
    target = lab_grid(grid) if node_lab is None else node_lab
    return invert_to_device(model, target, channel_letters=channel_letters,
                            is_additive=is_additive, ink_limit=ink_limit,
                            k_prior=k_prior, accurate=accurate,
                            extra_hues=extra_hues, black_l=black_l)


def refine_b2a_clut(model: ForwardModel, dev_clut: np.ndarray,
                    residual: np.ndarray, grid: int, *,
                    ink_limit: float | None = None,
                    is_additive: bool = True,
                    channel_letters: list[str] | None = None,
                    samples: int = 30000, lam: float = 0.03,
                    deep_oog: float = 5.0,
                    node_lab: np.ndarray | None = None,
                    lab_to01=None,
                    k_prior: dict | None = None,
                    accurate: bool = False,
                    extra_hues: dict[str, float] | None = None,
                    black_l: float | None = None) -> np.ndarray:
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
    limit = None if ink_limit is None or is_additive else ink_limit / 100.0
    if n <= 3:
        # Bijective: any random device point is an exact inverse sample.
        dev_s = rng.uniform(0.0, 1.0, (samples, n))
        # Extra samples on the device-cube faces: the gamut boundary is their
        # image, and boundary cells are exactly where interpolation needs the
        # most support (measured: halves the worst-case round-trip error).
        nf = samples // 2
        faces = rng.uniform(0.0, 1.0, (nf, n))
        faces[np.arange(nf), rng.integers(0, n, nf)] = \
            rng.integers(0, 2, nf).astype(float)
        dev_s = np.vstack([dev_s, faces])
        if limit is not None:
            total = dev_s.sum(1)
            over = total > limit
            dev_s[over] *= (limit / total[over])[:, None]
        lab_s = model.predict(dev_s)
    else:
        # n > 3: many device values share one Lab — fitting raw random
        # samples would average competing separations and erase the ink
        # policy (measured: K ≈ 0.4 at L*=50 instead of the locus value).
        # Sample *reachable* Lab targets instead and invert them through the
        # same policy the per-node pass used; those pairs are consistent.
        probe_dev = rng.uniform(0.0, 1.0, (samples // 3, n))
        if limit is not None:
            total = probe_dev.sum(1)
            over = total > limit
            probe_dev[over] *= (limit / total[over])[:, None]
        lab_targets = model.predict(probe_dev)
        dev_s, res_s = invert_to_device(
            model, lab_targets, channel_letters=channel_letters or [],
            is_additive=is_additive, ink_limit=ink_limit, k_prior=k_prior,
            accurate=accurate, extra_hues=extra_hues, black_l=black_l)
        keep = res_s < 1.0
        dev_s, lab_s = dev_s[keep], lab_targets[keep]

    if lab_to01 is None:
        ls, ab = lab_grid_axes(grid)
        span = np.array([ls[-1] - ls[0], ab[-1] - ab[0], ab[-1] - ab[0]])
        origin = np.array([ls[0], ab[0], ab[0]])

        def to01(lab: np.ndarray) -> np.ndarray:
            return np.clip((lab - origin[None, :]) / span[None, :], 0.0, 1.0)
    else:
        to01 = lab_to01

    # Anchor rows: every node contributes its v1 value — heavy anchors deep
    # out of gamut (their clamp IS the answer there), light anchors elsewhere
    # (keep the fit stable where samples are sparse, let data win).
    anchor_w = np.where(residual > deep_oog, 4.0, 0.05)
    if node_lab is None:
        node_lab = lab_grid(grid)
    # Fit in *curve space* — the CLUT stores shaped device values (the output
    # shaper tables undo them), so interpolation accuracy must be optimised
    # in the space the CMM actually interpolates in.
    p_all = np.vstack([to01(lab_s), to01(node_lab)])
    y_all = np.vstack([model.shape_device(dev_s),
                       model.shape_device(dev_clut)])
    w_all = np.concatenate([np.ones(len(dev_s)), anchor_w])

    x0 = model.shape_device(dev_clut)
    refined = x0
    probe_dev = rng.uniform(0.0, 1.0, (4000, n)) if n <= 3 else None
    for round_ in range(3 if n <= 3 else 1):
        w, cols = _interp_weights(p_all, grid, 3)
        sw = np.sqrt(w_all)[:, None]
        refined = np.clip(_grid_solve(w * sw, cols, y_all * sw, grid, 3, lam,
                                      400, x0=refined), 0.0, 1.0)
        if probe_dev is None or round_ == 2:
            break
        # Adaptive densification: score in-gamut probes through the refined
        # grid, then support the worst regions with fresh exact samples
        # (measured: cuts the worst-case round-trip error by ~40%).
        probe_lab = model.predict(probe_dev)
        wp, cp = _interp_weights(to01(probe_lab), grid, 3)
        landed = model.predict(np.clip(
            model.unshape_device((wp[:, :, None] * refined[cp]).sum(1)),
            0.0, 1.0))
        err = np.linalg.norm(landed - probe_lab, axis=1)
        worst = np.argsort(err)[-200:]
        extra = np.clip(np.repeat(probe_dev[worst], 40, axis=0)
                        + rng.normal(0.0, 0.06, (200 * 40, n)), 0.0, 1.0)
        p_all = np.vstack([p_all, to01(model.predict(extra))])
        y_all = np.vstack([y_all, model.shape_device(extra)])
        w_all = np.concatenate([w_all, np.ones(len(extra))])
    if limit is not None:
        raw = model.unshape_device(refined)
        total = raw.sum(1)
        over = total > limit
        if accurate:
            raw = project_tac(raw, limit)
        else:
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
