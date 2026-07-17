"""Colour-difference metrics for the profile engine.

ΔE2000 per the CIE definition, vectorised. The implementation follows
Sharma, Wu & Dalal, "The CIEDE2000 Color-Difference Formula: Implementation
Notes, Supplementary Test Data, and Mathematical Observations" (2005) — the
test suite pins the published reference pairs to 1e-4.

The engine still *optimises* in Lab (ΔE76) — that space is what the
LUT machinery and Argyll parity are built on — but accuracy statistics are
reported in ΔE2000 as well, which weights errors the way people see them
(neutrals and skin tones count for more, saturated blues for less).
"""
from __future__ import annotations

import numpy as np


def de00_scale_factors(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                                 np.ndarray]:
    """CIEDE2000's (S_L, S_C, S_H) at each Lab point.

    These are the formula's own per-component tolerances — the larger the
    S, the less a difference in that direction is seen. ``1/S`` therefore
    is the *perceptual weight* of an error component at that colour, which
    is what the maximum-accuracy clip uses (a first-order local ΔE2000
    metric; the R_T rotation term is a refinement of the C–H cross term in
    the blue region and is deliberately omitted from the weighting).
    """
    lab = np.atleast_2d(np.asarray(lab, float))
    l, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    c = np.hypot(a, b)
    g = 0.5 * (1.0 - np.sqrt(c ** 7 / (c ** 7 + 25.0 ** 7)))
    cp = np.hypot((1.0 + g) * a, b)
    hp = np.where((a == 0) & (b == 0), 0.0,
                  np.degrees(np.arctan2(b, (1.0 + g) * a)) % 360.0)
    t = (1.0 - 0.17 * np.cos(np.radians(hp - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hp))
         + 0.32 * np.cos(np.radians(3.0 * hp + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hp - 63.0)))
    sl = 1.0 + 0.015 * (l - 50.0) ** 2 / np.sqrt(20.0 + (l - 50.0) ** 2)
    sc = 1.0 + 0.045 * cp
    sh = 1.0 + 0.015 * cp * t
    return sl, sc, sh


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray,
                 kl: float = 1.0, kc: float = 1.0, kh: float = 1.0
                 ) -> np.ndarray:
    """(N,3) Lab × (N,3) Lab → (N,) CIEDE2000 colour differences."""
    lab1 = np.atleast_2d(np.asarray(lab1, float))
    lab2 = np.atleast_2d(np.asarray(lab2, float))
    l1, a1, b1 = lab1[:, 0], lab1[:, 1], lab1[:, 2]
    l2, a2, b2 = lab2[:, 0], lab2[:, 1], lab2[:, 2]

    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    cbar = 0.5 * (c1 + c2)
    g = 0.5 * (1.0 - np.sqrt(cbar ** 7 / (cbar ** 7 + 25.0 ** 7)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = np.hypot(a1p, b1)
    c2p = np.hypot(a2p, b2)
    h1p = np.where((a1p == 0) & (b1 == 0), 0.0,
                   np.degrees(np.arctan2(b1, a1p)) % 360.0)
    h2p = np.where((a2p == 0) & (b2 == 0), 0.0,
                   np.degrees(np.arctan2(b2, a2p)) % 360.0)

    dlp = l2 - l1
    dcp = c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    dh = np.where(c1p * c2p == 0.0, 0.0, dh)
    dhp = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh) / 2.0)

    lbar = 0.5 * (l1 + l2)
    cbarp = 0.5 * (c1p + c2p)
    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hbar = np.where(c1p * c2p == 0.0, hsum,
                    np.where(habs <= 180.0, 0.5 * hsum,
                             np.where(hsum < 360.0, 0.5 * (hsum + 360.0),
                                      0.5 * (hsum - 360.0))))
    t = (1.0 - 0.17 * np.cos(np.radians(hbar - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hbar))
         + 0.32 * np.cos(np.radians(3.0 * hbar + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hbar - 63.0)))
    dtheta = 30.0 * np.exp(-(((hbar - 275.0) / 25.0) ** 2))
    rc = 2.0 * np.sqrt(cbarp ** 7 / (cbarp ** 7 + 25.0 ** 7))
    sl = 1.0 + 0.015 * (lbar - 50.0) ** 2 / np.sqrt(20.0 + (lbar - 50.0) ** 2)
    sc = 1.0 + 0.045 * cbarp
    sh = 1.0 + 0.015 * cbarp * t
    rt = -np.sin(np.radians(2.0 * dtheta)) * rc

    return np.sqrt((dlp / (kl * sl)) ** 2
                   + (dcp / (kc * sc)) ** 2
                   + (dhp / (kh * sh)) ** 2
                   + rt * (dcp / (kc * sc)) * (dhp / (kh * sh)))
