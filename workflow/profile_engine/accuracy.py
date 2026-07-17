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

Both loops judge residuals in **ΔE2000**, not Euclidean Lab: near black the
cube-root lightness slope blows ordinary Lab residuals up for differences
nobody can see, so a ΔE76 criterion over-smooths shadows and cries wolf on
dark patches. The robust scale uses the textbook constants — σ from the
median absolute deviation (1.4826·MAD) and Huber's k = 1.345σ (the 95%-
Gaussian-efficiency tuning constant) — with a floor at instrument
repeatability so a clean chart is never touched.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from workflow.profile_engine.forward_model import (ForwardModel,
                                                   fit_forward_model)
from workflow.profile_engine.metrics import delta_e_2000

# λ search ladder, as factors on the parity table's value (settings -r
# included — it scales the base before the search).
_LAMBDA_FACTORS = (0.25, 0.5, 1.0, 2.0, 4.0)
_HOLDOUT_MIN_PATCHES = 120     # below this a CV split starves the fit
_CG_RTOL = 1e-12               # squared-residual scale → ~1e-6 relative


def fit_forward_model_accurate(
        device: np.ndarray, lab: np.ndarray, *, grid: int, base_lam: float,
        curve_rounds: int = 2,
        progress: Callable[[str], None] | None = None,
        ucs: bool = False,
        ) -> tuple[ForwardModel, np.ndarray, float]:
    """Cross-validated, outlier-robust forward fit.

    Returns ``(model, outlier_indices, lam_used)`` — outliers are patch row
    indices whose residual stayed far above the bulk even after the robust
    refit (worth remeasuring; they carry almost no weight in the fit).

    ``ucs`` (candidate ``"ucs"``, issue #123): fit in CAM16-UCS instead of
    CIELAB — residuals, curvature penalty and CV criterion all become
    perceptually uniform *by construction* (Euclidean UCS ≈ ΔE2000), and
    the ΔE00 formula drops out of the loops. The returned model's nodes
    are converted back to Lab, so everything downstream is unchanged.
    """
    npts = len(device)
    lam = base_lam
    space = None
    if ucs:
        from workflow.profile_engine.ucs import print_ucs
        space = print_ucs()
        lab = space.lab_to_ucs(lab)

    def dist(pred: np.ndarray, ref: np.ndarray) -> np.ndarray:
        if ucs:
            return np.linalg.norm(pred - ref, axis=1)
        return delta_e_2000(pred, ref)

    if npts >= _HOLDOUT_MIN_PATCHES:
        rng = np.random.default_rng(4242)
        idx = rng.permutation(npts)
        nho = max(30, npts // 10)
        ho, trn = idx[:nho], idx[nho:]
        best_err, best_lam = np.inf, base_lam
        for ci, f in enumerate(_LAMBDA_FACTORS):
            if progress is not None:
                progress(f"Fitting the printer model: smoothing search "
                         f"{ci + 1}/{len(_LAMBDA_FACTORS)}…")
            m = fit_forward_model(device[trn], lab[trn], grid=grid,
                                  lam=base_lam * f, cg_iters=350,
                                  curve_rounds=min(curve_rounds, 1),
                                  cg_rtol=_CG_RTOL)
            err = float(np.median(dist(m.predict(device[ho]),
                                       lab[ho])))
            if err < best_err:
                best_err, best_lam = err, base_lam * f
        lam = best_lam
        if progress is not None:
            progress(f"Smoothing chosen by cross-validation: "
                     f"×{lam / base_lam:g} of the standard value "
                     f"(held-out median {best_err:.2f} ΔE2000).")

    # Outlier scan on a deliberately STIFF fit: a smudge cannot hide from
    # the residuals of a stiff surface, whereas a low cross-validated λ can
    # absorb it locally and mask it. Weights: Huber (1 inside the scale,
    # scale/r beyond), scale = Huber's k = 1.345 × the MAD estimate of σ,
    # floored at instrument repeatability (≈0.35 ΔE2000); gross outliers
    # (beyond 8× scale) are rejected outright, and rejections are sticky —
    # once out, a patch cannot pull itself back in through the refit.
    if progress is not None:
        progress("Fitting the printer model: scanning for misread "
                 "patches…")
    scan = fit_forward_model(device, lab, grid=grid,
                             lam=max(4.0 * base_lam, lam),
                             curve_rounds=min(curve_rounds, 1),
                             cg_iters=350, cg_rtol=_CG_RTOL)
    res = dist(scan.predict(device), lab)
    sigma = 1.4826 * float(np.median(np.abs(res - np.median(res))))
    scale = max(1.345 * sigma, 0.35)
    w = np.minimum(1.0, scale / np.maximum(res, 1e-9))
    w[res > 8.0 * scale] = 0.0

    if progress is not None:
        progress("Fitting the printer model: robust fit 1/2…")
    model = fit_forward_model(device, lab, grid=grid, lam=lam,
                              curve_rounds=curve_rounds,
                              weights=w if (w < 0.999).any() else None,
                              cg_rtol=_CG_RTOL)
    res = dist(model.predict(device), lab)
    # One tightening pass against the final fit (never loosening).
    w2 = np.minimum(w, np.minimum(1.0, scale / np.maximum(res, 1e-9)))
    w2[res > 8.0 * scale] = 0.0
    if (w2 < w - 1e-9).any():
        if progress is not None:
            progress("Fitting the printer model: robust fit 2/2…")
        model = fit_forward_model(device, lab, grid=grid, lam=lam,
                                  curve_rounds=curve_rounds, weights=w2,
                                  cg_rtol=_CG_RTOL)
        res = dist(model.predict(device), lab)
        w = w2

    # Report likely misreads: everything rejected outright, plus whatever
    # still sits clearly visible (3 ΔE2000) above the bulk after the refit.
    named = res > max(6.0 * float(np.median(res)), 3.0)
    outliers = np.flatnonzero(named | (w == 0.0))
    if space is not None:
        # The fit lived in UCS; hand back a Lab-speaking model so the
        # writer, inversion seeds and statistics stay unchanged.
        model.nodes = space.ucs_to_lab(model.nodes)
    return model, outliers, lam
