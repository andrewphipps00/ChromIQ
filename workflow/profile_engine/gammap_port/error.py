"""Guide-point error functions — nearsmth.c ``aerrf`` (L364–410) and
``comperr`` (L254–338), vectorised (AGPL-3.0, Graeme W. Gill).

``SUM_POW`` is 2.0 in the source (L80) — the "normal sum of squares"
branch; ``LINEAR_HUE_SUM`` is not defined — that branch is absent, per the
compiled configuration.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port.primitives import (_dl_dc_dh_sq,
                                                            wdesq)

SUM_POW = 2.0


def aerrf(dv: np.ndarray, sv: np.ndarray, ra: np.ndarray,
          lxpow: np.ndarray, lxthr: np.ndarray) -> np.ndarray:
    """Absolute error with the extra-L-power term (aerrf, exact).

    ``ra``: (N, 3) per-point raw absolute weights (from interp_xweights);
    ``lxpow``/``lxthr``: per-point a.lxpow / a.lxthr fields.
    """
    dv = np.atleast_2d(dv)
    sv = np.atleast_2d(sv)
    dlsq, dcsq, dhsq = _dl_dc_dh_sq(dv, sv)
    del_l = np.sqrt(dlsq)
    expo = 1.0 + (lxpow - 1.0) * del_l / (del_l + lxthr)
    return (ra[:, 0] * dlsq ** expo
            + ra[:, 1] * dcsq
            + ra[:, 2] * dhsq)


def comperr(dtp: np.ndarray, aodv: np.ndarray, drv: np.ndarray,
            a_o: np.ndarray, rl: np.ndarray,
            dco: np.ndarray, dxo: np.ndarray,
            dcratio: np.ndarray, dxratio: np.ndarray) -> np.ndarray:
    """Composite guide error (comperr, exact): absolute + radial + depth.

    ``dtp``: dest test points; ``aodv``: weighted-nearest targets;
    ``drv``: radially mapped source; ``a_o``: per-point a.o weight;
    ``rl``: (N, 3) radial component weights; ``dco``/``dxo``: depth
    weights; ``dcratio``/``dxratio``: depth compression/expansion ratios.
    """
    dtp = np.atleast_2d(dtp)
    # wdesq with per-point weights, expanded inline (SUM_POW = 2 branch):
    dlsq, dcsq, dhsq = _dl_dc_dh_sq(dtp, np.atleast_2d(aodv))
    va = np.abs(a_o * dlsq + a_o * dcsq + a_o * dhsq)
    dlsq, dcsq, dhsq = _dl_dc_dh_sq(dtp, np.atleast_2d(drv))
    vr = np.abs(rl[:, 0] * dlsq + rl[:, 1] * dcsq + rl[:, 2] * dhsq)
    vd = dco * dcratio ** 2 + dxo * dxratio ** 2
    return va + vr + vd
