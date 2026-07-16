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


# ---------------------------------------------------------------------------
# cusp machinery (stage 2) — invariants from the C's own sanity checks
# ---------------------------------------------------------------------------

def _make_cusp_mapping(scale=0.8):
    from workflow.profile_engine.gammap_port.cusps import CuspMapping
    # source: idealised cusps at 60° spacing; dest: same, chroma scaled,
    # slightly compressed L range — a clean synthetic pair
    hues = np.radians([30, 90, 150, 210, 270, 330])
    src = np.stack([np.array([55 + 15*np.cos(3*h), 70*np.cos(h),
                              70*np.sin(h)]) for h in hues])
    dst = np.stack([np.array([53 + 12*np.cos(3*h), scale*70*np.cos(h),
                              scale*70*np.sin(h)]) for h in hues])
    return CuspMapping(src, dst,
                       src_white=np.array([100.0, 0.0, 0.0]),
                       src_black=np.array([2.0, 1.0, -1.0]),
                       dst_white=np.array([96.0, 0.5, -0.5]),
                       dst_black=np.array([8.0, 0.0, 2.0]))


def test_comp_ce_full_weights_maps_cusps_to_cusps():
    """C sanity check (nearsmth.c #ifdef NEVER, L797): at 100% weights each
    source cusp maps (close) to the corresponding destination cusp."""
    cm = _make_cusp_mapping()
    out = cm.comp_ce(cm.cusps[0][:6], cusp_weights=(1, 1, 1, 0, 1))
    d = np.linalg.norm(out - cm.cusps[1][:6], axis=1)
    assert d.max() < 1e-6, d


def test_comp_ce_zero_weights_is_identity():
    cm = _make_cusp_mapping()
    pts = np.array([[50.0, 20.0, -30.0], [70.0, -40.0, 10.0]])
    out = cm.comp_ce(pts, cusp_weights=(0.0, 0.0, 0.0, 2.0, 0.0))
    assert np.allclose(out, pts)


def test_comp_ce_neutral_axis_stays_put_under_twist():
    """With twist power > 0 the mapping fades to nothing at the neutral
    axis (tww → 0 ⇒ tpw → 0 and ccx → 1)."""
    cm = _make_cusp_mapping()
    grey = cm.cusps[0][8][None, :]
    out = cm.comp_ce(grey, cusp_weights=(1.0, 1.0, 1.0, 2.0, 1.4))
    # at grey: mapping weight ~0 → result = grey in dest-aligned frame,
    # i.e. the unchanged source point
    assert np.linalg.norm(out - grey) < 0.75


def test_comp_naxbf_bounds():
    cm = _make_cusp_mapping()
    # at the white point: 0.0; near grey: → 1.0 (C comment)
    w = cm.comp_naxbf(cm.cusps[0][6][None, :])
    g = cm.comp_naxbf(cm.cusps[0][8][None, :])
    assert w[0] < 1e-6
    assert g[0] > 0.99


def test_comp_lvc_signs():
    cm = _make_cusp_mapping()
    # +1 at white L, −1 at black L, ~0 at grey (C comment L1012–1014)
    assert abs(cm.comp_lvc(cm.cusps[0][6][None, :])[0] - 1.0) < 1e-9
    assert abs(cm.comp_lvc(cm.cusps[0][7][None, :])[0] + 1.0) < 1e-9
    assert abs(cm.comp_lvc(cm.cusps[0][8][None, :])[0]) < 0.35


def test_inv_comp_ce_roundtrip():
    cm = _make_cusp_mapping()
    pts = np.array([[60.0, 30.0, 20.0], [40.0, -25.0, -35.0],
                    [75.0, 5.0, 60.0]])
    w = (0.8, 0.6, 0.7, 2.0, 1.2)
    fwd = cm.comp_ce(pts, w)
    back = cm.inv_comp_ce(fwd, w)
    assert np.abs(cm.comp_ce(back, w) - fwd).max() < 1e-4


# ---------------------------------------------------------------------------
# stage 3: weight expansion / interpolation / error functions
# ---------------------------------------------------------------------------

def test_expand_weights_light_yellow_override():
    from workflow.profile_engine.gammap_port.xweights import (CWL, RDSM,
                                                              expand_weights)
    xw = expand_weights(weights.PERCEPTUAL_WEIGHTS)
    assert xw.shape == (14, 23)
    # slot 1 = light_yellow: cusp l overridden to 0.9, smoothing degree 0.5;
    # -1 fields inherit the default (e.g. depth weights stay 5.0)
    assert xw[1][CWL] == 0.9 and xw[1][RDSM] == 0.5
    assert xw[1][20] == 5.0 and xw[1][21] == 5.0
    # every other slot = pure default
    assert xw[0][CWL] == 0.1 and xw[13][RDSM] == 0.9


def test_comp_iweight_matches_c():
    from workflow.profile_engine.gammap_port.xweights import comp_iweight
    o, h, l = 1.0, 0.8, 0.45
    lc = 1.0 - h
    c = (1.0 - l) * lc
    ll = l * lc
    oo = o / np.sqrt(ll*ll + c*c + h*h)
    wl, wc, wh = comp_iweight(np.array([o]), np.array([h]), np.array([l]))
    assert abs(wl[0] - oo*ll) < 1e-12
    assert abs(wc[0] - oo*c) < 1e-12
    assert abs(wh[0] - oo*h) < 1e-12


def test_interp_xweights_structure():
    from workflow.profile_engine.gammap_port.xweights import interp_xweights, expand_weights
    cm = _make_cusp_mapping()
    xw = expand_weights(weights.PERCEPTUAL_WEIGHTS)
    pts = np.array([[95.0, 1.0, 1.0],     # near white
                    [50.0, 60.0, 10.0],   # saturated mid
                    [10.0, 3.0, -2.0]])   # near black
    out = interp_xweights(pts, xw, cm)
    assert out["w"].shape == (3, 23) and out["ra"].shape == (3, 3)
    # near-white point gets higher L-dominance than the mid grey one
    # (a.wl 0.8 vs a.gl 0.45): ra_l/(ra_l+ra_c) larger at white
    frac = out["ra"][:, 0] / (out["ra"][:, 0] + out["ra"][:, 1])
    assert frac[0] > frac[1]


def test_aerrf_and_comperr_match_c_expressions():
    from workflow.profile_engine.gammap_port.error import aerrf, comperr
    dv = np.array([[52.0, 12.0, -8.0]])
    sv = np.array([[50.0, 20.0, -10.0]])
    ra = np.array([[0.5, 0.3, 0.9]])
    # literal C: diffLChsq then the powered-L sum
    dl = 2.0; dlsq = dl*dl
    c1 = np.hypot(12.0, -8.0); c2 = np.hypot(20.0, -10.0)
    dcsq = (c1-c2)**2
    desq = dlsq + 8.0**2 + 2.0**2
    dhsq = max(desq - dlsq - dcsq, 0.0)
    expo = 1.0 + (1.5-1.0)*dl/(dl+10.0)
    want = 0.5*dlsq**expo + 0.3*dcsq + 0.9*dhsq
    got = aerrf(dv, sv, ra, np.array([1.5]), np.array([10.0]))[0]
    assert abs(got - want) < 1e-9
    # comperr sums absolute + radial + depth
    got = comperr(dv, sv, sv, np.array([1.0]), np.array([[0.2, 0.3, 0.4]]),
                  np.array([5.0]), np.array([5.0]),
                  np.array([0.1]), np.array([0.0]))[0]
    va = 1.0*dlsq + 1.0*dcsq + 1.0*dhsq
    vr = 0.2*dlsq + 0.3*dcsq + 0.4*dhsq
    assert abs(got - (va + vr + 5.0*0.01)) < 1e-9


# ---------------------------------------------------------------------------
# gamut surface substrate (stage 4 support)
# ---------------------------------------------------------------------------

def _sphere_cloud(radius=40.0, n=4000):
    rng = np.random.default_rng(3)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1)[:, None]
    from workflow.profile_engine.gammap_port.gamutsurf import CENT
    return CENT[None, :] + radius * v


def test_gamut_surface_radial_on_sphere():
    from workflow.profile_engine.gammap_port.gamutsurf import (CENT,
                                                               GamutSurface)
    gs = GamutSurface(_sphere_cloud())
    pts = np.array([[50.0, 60.0, 0.0], [80.0, 5.0, -5.0], [50.0, 0.0, 10.0]])
    out = gs.radial(pts)
    r = np.linalg.norm(out - CENT[None, :], axis=1)
    assert np.abs(r - 40.0).max() < 1.5          # binning tolerance
    # direction preserved
    d0 = (pts - CENT[None, :]); d1 = (out - CENT[None, :])
    cos = (d0 * d1).sum(1) / (np.linalg.norm(d0, axis=1)
                              * np.linalg.norm(d1, axis=1))
    assert cos.min() > 0.9999


def test_gamut_surface_vector_isect_sphere():
    from workflow.profile_engine.gammap_port.gamutsurf import GamutSurface
    gs = GamutSurface(_sphere_cloud())
    sv = np.array([[50.0, 0.0, 0.0]])            # centre
    dv = np.array([[50.0, 80.0, 0.0]])           # outside along +a
    mint, maxt, n_min, n_max = gs.vector_isect(sv, dv)
    # ray leaves the sphere at |a| = 40 → t = 0.5 both directions
    assert abs(maxt[0] - 0.5) < 0.05
    assert abs(mint[0] + 0.5) < 0.05
    # outward normal at the +a crossing points along +a
    assert n_max[0][1] > 0.9


# ---------------------------------------------------------------------------
# grey-axis 1-D L map (stage 6): Argyll fit_rspl_w objective, literal port
# ---------------------------------------------------------------------------

def test_grey_curve_matches_argyll_rspl():
    """GreyAxis._fit_curve must reproduce Argyll's own 1-D rspl fit.

    Reference values dumped from a compiled Argyll 3.5.0 rspl
    (scratchpad greyfit harness, gammap.c L1160-1180 parameters) for the
    exact ET-8550/ClayRGB lpnts. The knee anchors carry weights 0.5-2.25
    vs 10 at the endpoints, so the curve must stay near the endpoint
    line, NOT interpolate the anchors (the earlier PCHIP bug).
    """
    from workflow.profile_engine.gammap_port.greyaxis import GreyAxis
    ga = GreyAxis.__new__(GreyAxis)
    lpnts = np.array([[100.0, 100.0, 10.0],
                      [0.0, 4.670256, 10.0],
                      [50.0, 50.0, 0.5],
                      [85.0, 86.961847, 1.0],
                      [7.5, 7.5, 2.25]])
    ga._fit_curve(lpnts)
    # (x, f(x)) pairs from the compiled Argyll dump
    ref = [(0.0, 4.208220), (10.0, 11.889386), (20.0, 20.419438),
           (30.0, 29.695150), (50.0, 49.682398), (70.0, 70.346970),
           (90.0, 90.434742), (100.0, 100.086823)]
    for x, y in ref:
        got = float(np.interp(x, ga._lx, ga._lv))
        assert abs(got - y) < 0.1, (x, got, y)


def test_grey_axis_endpoints_exact_after_wb_adjust():
    """After adjust1_wb the composed curve maps source black/white L to
    the destination targets exactly (gammap.c fine-tune step)."""
    from workflow.profile_engine.gammap_port.gamutsurf import TriSurface
    from workflow.profile_engine.gammap_port.greyaxis import GreyAxis

    # UV-sphere mesh around the neutral axis (closed, watertight)
    nh, nb = 24, 12
    verts = [[98.0, 0.0, 0.0]]                       # top pole (L high)
    for j in range(1, nb):
        th = np.pi * j / nb
        for i in range(nh):
            ph = 2 * np.pi * i / nh
            verts.append([50.0 + 48.0 * np.cos(th),
                          48.0 * np.sin(th) * np.cos(ph),
                          48.0 * np.sin(th) * np.sin(ph)])
    verts.append([2.0, 0.0, 0.0])                    # bottom pole
    verts = np.array(verts)
    tris = []
    for i in range(nh):                              # pole caps
        tris.append([0, 1 + i, 1 + (i + 1) % nh])
        base = 1 + (nb - 2) * nh
        tris.append([len(verts) - 1, base + (i + 1) % nh, base + i])
    for j in range(nb - 2):                          # quad strips
        r0, r1 = 1 + j * nh, 1 + (j + 1) * nh
        for i in range(nh):
            i2 = (i + 1) % nh
            tris.append([r0 + i, r1 + i, r1 + i2])
            tris.append([r0 + i, r1 + i2, r0 + i2])
    surf = TriSurface(verts, np.array(tris))
    ga = GreyAxis([100.0, 0.0, 0.0], [0.0, 0.0, 0.0],
                  [100.0, 0.0, 0.0], [4.7, 2.6, -4.1], surf)
    # the pin is on the ROTATED source L (domap order: rot then grey_l),
    # so check through pre_map with the colourspace endpoints
    lo = ga.pre_map(np.array([[0.0, 0.0, 0.0]]))[0, 0]
    hi = ga.pre_map(np.array([[100.0, 0.0, 0.0]]))[0, 0]
    assert abs(hi - 100.0) < 1e-6
    # black maps exactly to the fully adapted dest black L (= dr_be_bp L)
    assert abs(lo - ga.dr_be_bp[0]) < 1e-6
    assert abs(lo - 4.7) < 1e-6


# ---------------------------------------------------------------------------
# cam02 appearance space (stage 6): literal xicc/cam02.c port
# ---------------------------------------------------------------------------

def test_cam02_appearance_matches_xicclu_reference():
    """Appearance (rel Lab → Jab) pinned against xicclu -ir -pj values
    computed with a compiled Argyll (ClayRGB media white = D65).
    The port matched xicclu to 0.0001 median on 200 probes; these rows
    pin a spread of the gamut including the HK/bluelin-affected regions.
    """
    from workflow.profile_engine.gammap_port.cam02 import Appearance
    ap = Appearance([0.95045471, 1.0, 1.08905029])
    labs = np.array([[50.0, 0.0, 0.0], [20.0, 40.0, -60.0],
                     [95.0, -5.0, 80.0], [5.0, 2.0, -4.0],
                     [70.0, 60.0, 20.0]])
    ref = np.array([[40.664, -1.1087, -0.6716],
                    [21.0219, 10.6664, -54.1876],
                    [92.8148, -9.7015, 65.5641],
                    [10.4033, 1.3721, -4.4101],
                    [65.7552, 61.61, 17.1564]])
    got = ap.lab_to_jab(labs)
    assert np.abs(got - ref).max() < 2e-3
    # exact analytic inverse
    back = ap.jab_to_lab(got)
    assert np.abs(back - labs).max() < 1e-5


def test_cam02_printer_white_roundtrip():
    """Roundtrip on PHYSICALLY VALID colours (XYZ ≥ 0). Impossible
    colours (negative XYZ) hit Argyll's one-way COMPR soft-clip, which
    deliberately has no inverse (ENABLE_DECOMPR is undef in the C)."""
    from workflow.profile_engine.gammap_port.cam02 import (Appearance,
                                                           lab_to_xyz)
    ap = Appearance([0.81098938, 0.84335327, 0.73251343])
    rng = np.random.default_rng(11)
    labs = rng.uniform([2, -80, -80], [99, 80, 80], (400, 3))
    labs = labs[(lab_to_xyz(labs) >= 0.0).all(1)]
    assert len(labs) > 200
    back = ap.jab_to_lab(ap.lab_to_jab(labs))
    # bluelin's backward hue inverse iterates to ~0.02 in c1 (like the C)
    assert np.abs(back - labs).max() < 2e-3
