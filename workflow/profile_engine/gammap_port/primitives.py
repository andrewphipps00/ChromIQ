"""nearsmth.c primitives, translated faithfully (stage 1 of the port map).

Source: ArgyllCMS 3.5.0 ``gamut/nearsmth.c`` L126–361 (AGPL-3.0, Graeme W.
Gill — see package ``__init__``). Conditional branches follow the compiled
configuration: ``EMPH_NEUTRAL`` and ``HACK`` are ``#undef`` in the source,
so those paths are intentionally absent here.

All functions are vectorised over (N, 3) Lab arrays where the C operates on
single points; the unit tests pin each against the literal C expressions.
"""
from __future__ import annotations

import numpy as np


def spow(arg: np.ndarray, ex: float) -> np.ndarray:
    """Sign-preserving power (nearsmth.c L346–351)."""
    arg = np.asarray(arg, dtype=float)
    return np.sign(arg) * np.abs(arg) ** ex


def spow3(vec: np.ndarray, ex: float) -> np.ndarray:
    """Per-component sign-preserving power (nearsmth.c L353–361)."""
    return spow(vec, ex)


def _dl_dc_dh_sq(in1: np.ndarray, in2: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The shared ΔL²/ΔC²/ΔH² split (DE94-style; nearsmth.c L140–166)."""
    d = in1 - in2
    dlsq = d[:, 0] ** 2
    desq = dlsq + d[:, 1] ** 2 + d[:, 2] ** 2
    c1 = np.hypot(in1[:, 1], in1[:, 2])
    c2 = np.hypot(in2[:, 1], in2[:, 2])
    dcsq = (c1 - c2) ** 2
    dhsq = np.maximum(desq - dlsq - dcsq, 0.0)
    return dlsq, dcsq, dhsq


def wdesq(in1: np.ndarray, in2: np.ndarray, lweight: float, cweight: float,
          hweight: float, sumpow: float = 0.0) -> np.ndarray:
    """Weighted delta-E squared between dest ``in1`` and source ``in2``
    (nearsmth.c L126–198). ``sumpow`` 0.0 means the normal sum of squares."""
    dlsq, dcsq, dhsq = _dl_dc_dh_sq(np.atleast_2d(in1), np.atleast_2d(in2))
    if sumpow == 0.0 or sumpow == 2.0:
        vv = lweight * dlsq + cweight * dcsq + hweight * dhsq
        return np.abs(vv)
    sp = sumpow * 0.5
    vv = (lweight * dlsq ** sp + cweight * dcsq ** sp
          + hweight * dhsq ** sp)
    return np.abs(vv) ** (1.0 / sp)


def diff_lch_sq(in1: np.ndarray, in2: np.ndarray) -> np.ndarray:
    """(N, 3) of (ΔL², ΔC², ΔH²) — nearsmth.c ``diffLChsq`` L200–252."""
    dlsq, dcsq, dhsq = _dl_dc_dh_sq(np.atleast_2d(in1), np.atleast_2d(in2))
    return np.stack([dlsq, dcsq, dhsq], 1)
