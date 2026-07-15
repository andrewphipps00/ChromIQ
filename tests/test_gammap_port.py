"""gammap port, stage 1 (P4b, #122): primitives pinned to the C expressions.

Each expected value below is the literal C computation from nearsmth.c
(ArgyllCMS 3.5.0) evaluated by hand/one-liner — not by the ported code — so
any translation slip fails loudly.
"""
from __future__ import annotations

import math

import numpy as np

from workflow.profile_engine.gammap_port import weights
from workflow.profile_engine.gammap_port.primitives import (diff_lch_sq,
                                                            spow, wdesq)


def test_spow_matches_c():
    # C: spow(-2.0, 0.5) = -pow(2.0, 0.5)
    assert spow(np.array([-2.0]), 0.5)[0] == -math.pow(2.0, 0.5)
    assert spow(np.array([3.0]), 2.0)[0] == 9.0
    assert spow(np.array([0.0]), 1.7)[0] == 0.0


def test_wdesq_sum_of_squares_matches_c():
    in1 = np.array([[50.0, 20.0, -10.0]])       # destination
    in2 = np.array([[55.0, 10.0, -4.0]])        # source
    # literal C: dlsq=25; desq=25+100+36=161
    c1 = math.hypot(20.0, -10.0)
    c2 = math.hypot(10.0, -4.0)
    dcsq = (c1 - c2) ** 2
    dhsq = 161.0 - 25.0 - dcsq
    want = 1.0 * 25.0 + 0.5 * dcsq + 2.0 * dhsq
    got = wdesq(in1, in2, 1.0, 0.5, 2.0, 0.0)[0]
    assert abs(got - want) < 1e-12


def test_wdesq_sumpow_branch_matches_c():
    in1 = np.array([[30.0, 5.0, 5.0]])
    in2 = np.array([[20.0, -5.0, 15.0]])
    dlsq = 100.0
    c1 = math.hypot(5.0, 5.0)
    c2 = math.hypot(-5.0, 15.0)
    dcsq = (c1 - c2) ** 2
    desq = dlsq + 100.0 + 100.0
    dhsq = max(desq - dlsq - dcsq, 0.0)
    sp = 3.0 * 0.5
    want = (0.7 * dlsq ** sp + 1.0 * dcsq ** sp + 1.3 * dhsq ** sp) ** (1 / sp)
    got = wdesq(in1, in2, 0.7, 1.0, 1.3, 3.0)[0]
    assert abs(got - want) < 1e-9


def test_diff_lch_sq_negative_hue_clamps():
    # identical chroma+L along a: ΔH² absorbs the rest, never negative
    a = np.array([[50.0, 10.0, 0.0]])
    out = diff_lch_sq(a, a)[0]
    assert np.all(out == 0.0)
    b = np.array([[50.0, 0.0, 10.0]])   # same C, same L, pure hue difference
    out = diff_lch_sq(a, b)[0]
    assert out[0] == 0.0 and out[1] < 1e-12 and out[2] > 0.0


def test_weight_tables_extracted():
    for table in (weights.PERCEPTUAL_WEIGHTS, weights.SATURATION_WEIGHTS):
        tags = [t for t, _ in table]
        assert tags[0] == "gmm_default"
        assert all(len(v) == 23 for _, v in table)
    # spot values from gammap.c pweights (L207–248): cusp align l=0.1,
    # abs overall weight 1.0, grey l dominance 0.45, smoothing 20/30/0.9
    vals = dict(weights.PERCEPTUAL_WEIGHTS)["gmm_default"]
    assert vals[0] == 0.1 and vals[8] == 1.0 and vals[11] == 0.45
    assert vals[17:20] == [20.0, 30.0, 0.9]
    assert weights.PSMOOTH == 2.0 and weights.XVRA == 3.0


# ---------------------------------------------------------------------------
# geometry helpers (stage 2 support) — pinned to the C self-check invariants
# ---------------------------------------------------------------------------

def test_rot_mat_maps_s_onto_t_with_scale():
    from workflow.profile_engine.gammap_port.geom import rot_mat
    s = np.array([3.0, -1.0, 2.0])
    t = np.array([-1.0, 4.0, 0.5])
    m = rot_mat(s, t)
    # the C self-check: m·s must equal t exactly (rotation includes scale)
    assert np.allclose(m @ s, t, atol=1e-10)


def test_rot_mat_antiparallel_branch():
    from workflow.profile_engine.gammap_port.geom import rot_mat
    s = np.array([2.0, 0.0, 0.0])
    t = np.array([-4.0, 0.0, 0.0])
    m = rot_mat(s, t)
    assert np.allclose(m @ s, t, atol=1e-12)


def test_vec_rot_mat_maps_segment_endpoints():
    from workflow.profile_engine.gammap_port.geom import apply_3x4, vec_rot_mat
    s0 = np.array([5.0, 2.0, -3.0])
    s1 = np.array([90.0, -4.0, 7.0])
    t0 = np.array([0.0, 0.0, 0.0])
    t1 = np.array([100.0, 0.0, 0.0])
    m = vec_rot_mat(s1, s0, t1, t0)
    # the icmVecRotMat self-check: both defining points map exactly
    assert np.allclose(apply_3x4(m, s0), t0, atol=1e-9)
    assert np.allclose(apply_3x4(m, s1), t1, atol=1e-9)


def test_plane_eqn_and_dist_match_c_convention():
    from workflow.profile_engine.gammap_port.geom import plane_dist, plane_eqn
    p0 = np.array([0.0, 0.0, 0.0])
    p1 = np.array([1.0, 0.0, 0.0])
    p2 = np.array([0.0, 1.0, 0.0])
    eq = plane_eqn(p0, p1, p2)
    # C normal = v1 × v2 with v1 = p2−p0, v2 = p1−p0 → (0,0,-1)
    assert np.allclose(eq, [0.0, 0.0, -1.0, 0.0], atol=1e-12)
    assert plane_dist(eq, np.array([0.0, 0.0, -2.0])) == 2.0
    assert plane_eqn(p0, p1, p1) is None


def test_lab_lch_roundtrip():
    from workflow.profile_engine.gammap_port.geom import lab_to_lch, lch_to_lab
    lab = np.array([[50.0, -20.0, 30.0], [10.0, 0.0, -5.0]])
    back = lch_to_lab(lab_to_lch(lab))
    assert np.allclose(back, lab, atol=1e-12)
