"""Top-level gamut mapping — gammap.c's flow on the ported machinery
(AGPL-3.0, Graeme W. Gill — see package ``__init__``).

Flow (gammap.c ~L700–1600, compression configuration):

1. grey-axis alignment: an affine map taking the source black→white axis
   onto the destination's (the cusp context's rotation frames compose to
   exactly this);
2. guide vectors via :func:`near_smooth_guides` on the aligned source;
3. a smooth 3-D warp fitted through the guide displacements — gammap.c
   fits an rspl at ``PSMOOTH``; the port uses the maths-A fitter
   (equivalence measured, issue #122 iteration 4).

The mapper exposes ``map_lab`` like the engine's other mappers, so
``build_mapped_b2a`` can use it interchangeably.
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.gammap_port import weights as wtab
from workflow.profile_engine.gammap_port.cusps import (CuspMapping,
                                                       cusps_from_cloud)
from workflow.profile_engine.gammap_port.geom import apply_3x4
from workflow.profile_engine.gammap_port.nearsmth import near_smooth_guides
from workflow.profile_engine.gammap_port.xweights import expand_weights


def _wb_from_cloud(cloud: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """White/black points: extreme-L near-neutral points of the cloud."""
    c = np.hypot(cloud[:, 1], cloud[:, 2])
    neut = cloud[c < np.percentile(c, 20)]
    if len(neut) == 0:
        neut = cloud
    return neut[np.argmax(neut[:, 0])], neut[np.argmin(neut[:, 0])]


class GammapMapper:
    """gammap-ported source→destination gamut mapping."""

    def __init__(self, src_cloud: np.ndarray, dst_cloud: np.ndarray, *,
                 intent: str = "p", smooth_iters: int = 6) -> None:
        table = (wtab.SATURATION_WEIGHTS if intent in ("s", "ms")
                 else wtab.PERCEPTUAL_WEIGHTS)
        xw = expand_weights(table)
        src_w, src_k = _wb_from_cloud(src_cloud)
        dst_w, dst_k = _wb_from_cloud(dst_cloud)
        cm = CuspMapping(cusps_from_cloud(src_cloud),
                         cusps_from_cloud(dst_cloud),
                         src_white=src_w, src_black=src_k,
                         dst_white=dst_w, dst_black=dst_k)
        self._cm = cm

        # Guide vectors on the RAW source cloud — the grey-axis/cusp
        # alignment happens inside via the rotation frames (comp_ce), as in
        # the C: gammap.c hands near_smooth the unaligned source gamut.
        # (Pre-aligning too applied the axis transform twice — measured:
        # guide error 7.6 median.)
        sv, dv = near_smooth_guides(src_cloud, dst_cloud, xw, cm,
                                    smooth_iters=smooth_iters)

        # 3. smooth displacement warp through the guides (rspl / PSMOOTH
        #    equivalent). Interior anchors: aligned-space points well
        #    inside both gamuts stay put (displacement 0, light weight) —
        #    the same role as rspl's smoothness prior over the grid.
        from workflow.profile_engine.gamut_map import WarpMapper
        rng = np.random.default_rng(11)
        # Deep-core anchors only (0.35 radius): identity there is safe —
        # colprof's own map leaves the protected core untouched; anchoring
        # further out fights the guides (measured: over-stiff interior).
        core = 0.35 * (sv - np.array([50.0, 0.0, 0.0])) \
            + np.array([50.0, 0.0, 0.0])
        idx = rng.choice(len(core), min(len(core), 400), replace=False)
        train = np.vstack([sv, core[idx]])
        target = np.vstack([dv, core[idx]])
        self._warp = WarpMapper(train, target)

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        return self._warp.map_lab(np.atleast_2d(np.asarray(lab, float)))
