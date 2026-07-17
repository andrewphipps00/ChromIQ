"""Bayesian-flavoured fitting support (issue #123, W3 — candidate "gp").

Three statistically principled upgrades to the maximum-accuracy fit:

* **Heteroscedastic noise model** — spectro repeatability is not uniform:
  σ blows up on dark patches (photon starvation; i1-class instruments show
  ~0.05 XYZ units on light patches vs ~0.15 near black). The amplitudes
  are *estimated from the chart's own duplicate patches* (white/black
  repeats), the exponential shape ``σ(Y) = a + b·e^(−Y/10)`` is the
  published repeatability curve of contact spectrophotometers. Whitening
  the residuals by the propagated per-patch σ turns ordinary least
  squares into GLS: the fit stops chasing dark-patch noise (the W1
  finding) and every robust threshold becomes a true z-score.

* **Continued λ search** — the CV ladder refines around its winner by
  hill-climbing in half-octave steps, so the smoothing is no longer
  pinned to five fixed factors (the pragmatic v1 of the GP marginal-
  likelihood optimisation; same criterion, finer grid).

* **Uncertainty map** — per-region 95 % bands from the whitened residual
  quantiles, reported in the build log. This is the confidence report a
  future active-design step (the held-back closed loop) would consume,
  available to the user today.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.ti3_data import lab_to_xyz

# Fallback repeatability amplitudes (XYZ units on the Y=100 scale) when a
# chart carries no duplicate patches to estimate from — i1-class contact
# instrument, published repeatability class (≈0.03 ΔE00 light patches,
# ≲1 ΔE00 deepest blacks). Never tuned to a measurement.
_DEFAULT_FLOOR = 0.02
_DEFAULT_DARK = 0.03
_DECAY_Y = 10.0                    # e-folding of the dark-noise term


def duplicate_groups(device: np.ndarray, tol: float = 1e-6) -> list[np.ndarray]:
    """Row-index groups of exactly repeated device values (≥ 3 repeats)."""
    order = np.lexsort(device.T)
    groups: list[list[int]] = []
    cur: list[int] = [int(order[0])]
    for prev, nxt in zip(order[:-1], order[1:]):
        if np.abs(device[nxt] - device[prev]).max() <= tol:
            cur.append(int(nxt))
        else:
            if len(cur) >= 3:
                groups.append(cur)
            cur = [int(nxt)]
    if len(cur) >= 3:
        groups.append(cur)
    return [np.array(g) for g in groups]


def estimate_xyz_noise(device: np.ndarray, lab: np.ndarray
                       ) -> tuple[float, float]:
    """(floor, dark) amplitudes of ``σ(Y) = floor + dark·e^(−Y/10)``,
    least-squares fitted to the duplicate groups' measured XYZ scatter;
    instrument-class defaults when the chart has no usable duplicates."""
    xyz = lab_to_xyz(lab)
    ys, sigmas = [], []
    for g in duplicate_groups(device):
        pts = xyz[g]
        s = float(pts.std(axis=0, ddof=1).mean())
        if np.isfinite(s):
            ys.append(float(pts[:, 1].mean()))
            sigmas.append(max(s, 1e-4))
    if not ys:
        return _DEFAULT_FLOOR, _DEFAULT_DARK
    basis = np.stack([np.ones(len(ys)), np.exp(-np.array(ys) / _DECAY_Y)], 1)
    if len(ys) == 1:
        # One group (usually white, where the dark term vanishes): scale
        # both default amplitudes by the observed/expected ratio.
        expect = _DEFAULT_FLOOR + _DEFAULT_DARK * basis[0, 1]
        r = sigmas[0] / expect
        return _DEFAULT_FLOOR * r, _DEFAULT_DARK * r
    coef, *_ = np.linalg.lstsq(basis, np.array(sigmas), rcond=None)
    floor = float(np.clip(coef[0], 0.005, 2.0))
    dark = float(np.clip(coef[1], 0.0, 5.0))
    return floor, dark


def patch_noise_sigma(device: np.ndarray, lab: np.ndarray,
                      space=None) -> tuple[np.ndarray, tuple[float, float]]:
    """Per-patch measurement σ in *target* units (Lab, or CAM16-UCS when
    ``space`` is given), by propagating the XYZ noise model through the
    local Jacobian of the target transform. Returns ``(σ, (floor, dark))``.
    """
    floor, dark = estimate_xyz_noise(device, lab)
    xyz = lab_to_xyz(lab)
    s_xyz = floor + dark * np.exp(-np.clip(xyz[:, 1], 0.0, None) / _DECAY_Y)

    if space is None:
        from workflow.profile_engine.ti3_data import xyz_to_lab
        f = xyz_to_lab
    else:
        f = space.xyz_to_ucs
    # Frobenius norm of dTarget/dXYZ by central differences, vectorised.
    h = 1e-2
    j2 = np.zeros(len(lab))
    for ax in range(3):
        xp = xyz.copy(); xp[:, ax] += h
        xm = xyz.copy(); xm[:, ax] = np.maximum(xm[:, ax] - h, 1e-6)
        col = (f(xp) - f(xm)) / (xp[:, ax] - xm[:, ax])[:, None]
        j2 += (col ** 2).sum(1)
    sigma = np.sqrt(j2 / 3.0) * s_xyz
    return np.clip(sigma, 1e-3, None), (floor, dark)


# ---------------------------------------------------------------------------
# Uncertainty report
# ---------------------------------------------------------------------------

_REGIONS = (
    ("shadows", lambda l, c: l < 25.0),
    ("midtones", lambda l, c: (l >= 25.0) & (l < 60.0)),
    ("highlights", lambda l, c: l >= 60.0),
    ("saturated colours", lambda l, c: c >= 45.0),
    ("neutrals", lambda l, c: c < 12.0),
)


def uncertainty_lines(lab: np.ndarray, de00: np.ndarray) -> list[str]:
    """Per-region 95 % confidence lines for the build log.

    Empirical bands from the fit residuals at the chart patches — where
    the chart is dense they reflect fit + noise; sparse regions are named
    so the user knows where the profile is least supported.
    """
    l = lab[:, 0]
    c = np.hypot(lab[:, 1], lab[:, 2])
    parts: list[str] = []
    sparse: list[str] = []
    for name, sel in _REGIONS:
        m = sel(l, c)
        n = int(m.sum())
        if n < 8:
            sparse.append(name)
            continue
        band = float(np.percentile(de00[m], 95))
        parts.append(f"{name} ±{band:.2f}")
    lines = []
    if parts:
        lines.append("Confidence map (95% of patches, ΔE2000): "
                     + ", ".join(parts) + ".")
    if sparse:
        lines.append("Sparse chart coverage in: " + ", ".join(sparse)
                     + " — consider more patches there next time.")
    return lines
