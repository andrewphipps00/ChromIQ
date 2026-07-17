"""Globally optimal separation for multi-ink B2A grids (issue #123, W2 —
candidate ``"joint-sep"``).

The per-node Gauss–Newton inversion resolves each node's surplus degrees
of freedom locally; soft priors keep neighbours *mostly* consistent, but
the formulation stays local — two adjacent nodes may still settle on
different metameric ink splits. Here the whole B2A grid is solved as ONE
optimisation problem:

    min_D  Σᵢ wᵢ‖f(Dᵢ) − Tᵢ‖²  +  λ_s Σ_{(i,j)∈E} ‖Dᵢ − Dⱼ‖²
           +  Σᵢ,c pᵢ_c (Dᵢ_c − priorᵢ_c)²
    s.t.   0 ≤ D ≤ 1,   Σ_c Dᵢ_c ≤ TAC

with E the 6-neighbour grid graph in PCS space. Smoothness lives *in the
objective*, so metameric banding is impossible by construction rather
than smoothed away afterwards.

Solver: ADMM. The x-step is a Gauss–Newton-linearised least-squares over
the full grid, solved by CG with structured operators (the graph
Laplacian is the same stencil family as the forward fit's curvature
penalty); the z-step is the existing Euclidean box+TAC projection
(:func:`b2a.project_tac`); the dual update ties them. Warm-started from
the per-node result, so a handful of outer relinearisations suffice.

λ_s is scaled from the data stiffness itself (a fixed fraction of the
median per-node trace(JᵀJ)/n): metameric directions cost the data term
nothing, so any positive λ_s resolves them smoothly, while real colour
gradients — where the data term is stiff — stay data-dominated.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.b2a import _model_jacobian, project_tac
from workflow.profile_engine.forward_model import ForwardModel

# Smoothness as a fraction of the data stiffness (see module docstring).
_SMOOTH_FRACTION = 0.02
_ADMM_ITERS = 30
_GN_OUTER = 3
_CG_ITERS = 60


def _laplacian(x: np.ndarray, grid: int) -> np.ndarray:
    """Graph-Laplacian ·x on the grid³ lattice, per channel (6-neighbour
    first differences; symmetric PSD; null space = constants)."""
    shape = (grid, grid, grid, -1)
    x3 = x.reshape(shape)
    o = np.zeros_like(x3)
    for ax in range(3):
        lo = [slice(None)] * 4
        hi = [slice(None)] * 4
        lo[ax] = slice(0, -1)
        hi[ax] = slice(1, None)
        d = x3[tuple(lo)] - x3[tuple(hi)]
        o[tuple(lo)] += d
        o[tuple(hi)] -= d
    return o.reshape(x.shape)


def joint_separation(model: ForwardModel, target: np.ndarray,
                     dev0: np.ndarray, residual: np.ndarray, grid: int, *,
                     ink_limit: float | None,
                     prior: np.ndarray | None,
                     prior_w: np.ndarray | None,
                     gn_model=None,
                     progress=None) -> np.ndarray:
    """Jointly re-solve the whole B2A separation field.

    ``dev0``/``residual`` come from the per-node inversion (warm start;
    out-of-gamut nodes keep chasing their *reachable* clip colour).
    ``gn_model`` optionally redirects the residual space (the CAM16-UCS
    view, which also carries the matching target transform); geometry
    (priors, OOG classification) stays as computed in Lab.
    """
    space = gn_model if gn_model is not None else model
    n = model.n_channels
    limit = None if ink_limit is None else ink_limit / 100.0

    t_all = space.to_space(target) if gn_model is not None else target
    # Reachable pseudo-targets for the clip region: the joint solve then
    # smooths one consistent field across the gamut boundary instead of
    # chasing colours that do not exist.
    oog = residual > 1.0
    if oog.any():
        t_all = t_all.copy()
        t_all[oog] = space.predict(dev0[oog])

    z = dev0.copy()
    u = np.zeros_like(z)
    x = z.copy()
    free = np.arange(n)

    for outer in range(_GN_OUTER):
        if progress is not None:
            progress(f"Inverting the model: joint separation "
                     f"{outer + 1}/{_GN_OUTER}…")
        f0 = space.predict(z)
        jac = _model_jacobian(space, z, free, f0, boundary_fd=True)
        r = t_all - f0
        # Linearised data rhs: J x ≈ J z + r  ⇒  y = J z + r.
        y = np.einsum("nij,nj->ni", jac, z) + r
        jtj = np.einsum("nik,nil->nkl", jac, jac)      # (G³, n, n)
        jty = np.einsum("nik,ni->nk", jac, y)
        stiff = float(np.median(np.trace(jtj, axis1=1, axis2=2))) / n
        lam_s = _SMOOTH_FRACTION * stiff
        rho = 0.5 * lam_s + 1e-6

        def amul(v: np.ndarray) -> np.ndarray:
            o = np.einsum("nkl,nl->nk", jtj, v)
            o += lam_s * _laplacian(v, grid)
            if prior_w is not None:
                o += prior_w * v
            return o + rho * v

        b_fixed = jty.copy()
        if prior_w is not None and prior is not None:
            b_fixed += prior_w * prior

        for it in range(_ADMM_ITERS):
            b = b_fixed + rho * (z - u)
            # CG on A x = b, warm-started at the current x.
            rvec = b - amul(x)
            p = rvec.copy()
            rs = float((rvec * rvec).sum())
            for _ in range(_CG_ITERS):
                ap = amul(p)
                alpha = rs / max(float((p * ap).sum()), 1e-12)
                x += alpha * p
                rvec -= alpha * ap
                rs2 = float((rvec * rvec).sum())
                if rs2 < 1e-10:
                    break
                p = rvec + (rs2 / rs) * p
                rs = rs2
            x_hat = np.clip(x + u, 0.0, 1.0)
            z_new = project_tac(x_hat, limit) if limit is not None else x_hat
            u += x - z_new
            if float(np.abs(z_new - z).max()) < 1e-5 and it > 3:
                z = z_new
                break
            z = z_new
    return z
