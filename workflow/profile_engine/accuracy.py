"""Maximum-accuracy model fitting (gammap_mode "accurate").

Two statistical upgrades over the parity fit, both closed-loop instead of
open-loop:

* **Cross-validated smoothing** — the parity fit's λ table is tuned on the
  trusted fixtures; papers, inks and instruments the table has never seen
  may want a very different value. Here a held-out patch subset picks the
  λ that actually generalises best for *this* measurement. The ``-r``
  (avgdev) setting still matters: it sets the centre of the search, so a
  user hint shifts the whole candidate ladder.

* **Robust refit (Huber IRLS)** — plain least squares lets a single
  misread patch pull the local grid nodes and, through the inverse, a whole
  B2A neighbourhood. Down-weighting patches whose residual is far above the
  bulk makes the fit resistant to smudges and misreads, and the patches
  that were down-weighted are reported so the user can remeasure them.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from workflow.profile_engine.forward_model import (ForwardModel,
                                                   fit_forward_model)

# λ search ladder, as factors on the parity table's value (settings -r
# included — it scales the base before the search).
_LAMBDA_FACTORS = (0.25, 0.5, 1.0, 2.0, 4.0)
_HOLDOUT_MIN_PATCHES = 120     # below this a CV split starves the fit
_CG_RTOL = 1e-12               # squared-residual scale → ~1e-6 relative


def fit_forward_model_accurate(
        device: np.ndarray, lab: np.ndarray, *, grid: int, base_lam: float,
        curve_rounds: int = 2,
        progress: Callable[[str], None] | None = None,
        ) -> tuple[ForwardModel, np.ndarray, float]:
    """Cross-validated, outlier-robust forward fit.

    Returns ``(model, outlier_indices, lam_used)`` — outliers are patch row
    indices whose residual stayed far above the bulk even after the robust
    refit (worth remeasuring; they carry almost no weight in the fit).
    """
    npts = len(device)
    lam = base_lam

    if npts >= _HOLDOUT_MIN_PATCHES:
        rng = np.random.default_rng(4242)
        idx = rng.permutation(npts)
        nho = max(30, npts // 10)
        ho, trn = idx[:nho], idx[nho:]
        best_err, best_lam = np.inf, base_lam
        for f in _LAMBDA_FACTORS:
            m = fit_forward_model(device[trn], lab[trn], grid=grid,
                                  lam=base_lam * f, cg_iters=350,
                                  curve_rounds=min(curve_rounds, 1),
                                  cg_rtol=_CG_RTOL)
            err = float(np.median(np.linalg.norm(
                m.predict(device[ho]) - lab[ho], axis=1)))
            if err < best_err:
                best_err, best_lam = err, base_lam * f
        lam = best_lam
        if progress is not None:
            progress(f"Smoothing chosen by cross-validation: "
                     f"×{lam / base_lam:g} of the standard value "
                     f"(held-out median {best_err:.2f} ΔE).")

    model = fit_forward_model(device, lab, grid=grid, lam=lam,
                              curve_rounds=curve_rounds, cg_rtol=_CG_RTOL)
    res = np.linalg.norm(model.predict(device) - lab, axis=1)

    # Robust IRLS: Huber weights (1 inside the scale, scale/r beyond it)
    # with a redescending cut — a *gross* outlier gets weight zero outright,
    # otherwise a low-smoothing fit keeps chasing it across the rounds and
    # a partial weight never lets go. The scale rides on the bulk residual
    # level so a clean chart is left untouched (all weights 1 → no refit).
    for _ in range(3):
        scale = max(2.5 * float(np.median(res)), 0.75)
        w = np.minimum(1.0, scale / np.maximum(res, 1e-9))
        w[res > 8.0 * scale] = 0.0
        if not (w < 0.999).any():
            break
        model = fit_forward_model(device, lab, grid=grid, lam=lam,
                                  curve_rounds=curve_rounds, weights=w,
                                  cg_rtol=_CG_RTOL)
        res = np.linalg.norm(model.predict(device) - lab, axis=1)

    # Report only likely misreads. Dark patches carry legitimately large
    # Lab noise (the cube-root slope amplifies XYZ noise near black), so the
    # naming threshold sits well above the down-weighting scale — IRLS
    # quietly handles the tail either way.
    outliers = np.flatnonzero(res > max(6.0 * float(np.median(res)), 4.0))
    return model, outliers, lam
