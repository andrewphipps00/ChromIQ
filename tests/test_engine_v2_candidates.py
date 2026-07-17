"""W2/W4/W5 tests (issue #123): joint-sep, spectral hybrid, render2."""
import numpy as np
import pytest

from benchmarks.synthetic import PRINTERS, halton, make_chart, measure, \
    write_ti3
from workflow.profile_engine.builder import BuildSettings, build_profile
from workflow.profile_engine.gamut_map import source_surface_lab
from workflow.profile_engine.joint_sep import _laplacian, joint_separation
from workflow.profile_engine.metrics import delta_e_2000
from workflow.profile_engine.render2 import RadialUcsMapper, _knee_map
from workflow.profile_engine.ti3_data import read_ti3


# ---------------------------------------------------------------------------
# W2 — joint separation
# ---------------------------------------------------------------------------

def test_laplacian_annihilates_constants():
    grid = 5
    x = np.ones((grid ** 3, 4)) * 0.37
    assert np.abs(_laplacian(x, grid)).max() < 1e-12


def test_laplacian_positive_semidefinite():
    grid = 4
    rng = np.random.default_rng(2)
    for _ in range(5):
        x = rng.normal(size=(grid ** 3, 2))
        assert (x * _laplacian(x, grid)).sum() >= -1e-9


def _cmyk_setup(tmp_path):
    p = PRINTERS["S3"]
    chart = make_chart(p, 700)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "s3.ti3", p, chart, xyz, refl)
    return p, ti3


def test_joint_separation_feasible_and_smooth(tmp_path):
    from workflow.profile_engine import b2a as b2a_mod
    from workflow.profile_engine.forward_model import fit_forward_model
    p = PRINTERS["S3"]
    rng = np.random.default_rng(1)
    dev = rng.uniform(0, 1, (500, 4))
    from workflow.profile_engine.b2a import project_tac
    dev = project_tac(dev, 2.8)
    model = fit_forward_model(dev, p.lab_relative_true(dev), grid=7,
                              lam=0.05)
    grid = 9
    node_lab = b2a_mod.lab_grid(grid)
    dev0, res = b2a_mod.build_b2a_clut(
        model, grid, channel_letters=list("CMYK"), is_additive=False,
        ink_limit=280.0, accurate=True, black_l=10.0)
    prior, prior_w = b2a_mod.ink_priors(node_lab, 4,
                                        channel_letters=list("CMYK"),
                                        accurate=True, black_l=10.0)
    out = joint_separation(model, node_lab, dev0, res, grid,
                           ink_limit=280.0, prior=prior, prior_w=prior_w)
    assert out.shape == dev0.shape
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert out.sum(1).max() <= 2.8 + 1e-6            # TAC feasible
    # Smoothness: total neighbour variation not worse than per-node.
    def tv(x):
        return float(np.abs(np.diff(x.reshape(grid, grid, grid, 4),
                                    axis=0)).sum())
    assert tv(out) <= tv(dev0) * 1.05


def test_joint_sep_build_changes_bytes(tmp_path):
    p, ti3 = _cmyk_setup(tmp_path)
    from datetime import datetime, timezone
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = tmp_path / "a.icc"
    b = tmp_path / "b.icc"
    build_profile(ti3, a, BuildSettings(quality="l", gammap_mode="accurate",
                                        ink_limit=280.0, timestamp=ts,
                                        description="x"))
    build_profile(ti3, b, BuildSettings(
        quality="l", gammap_mode="accurate", ink_limit=280.0, timestamp=ts,
        description="x", engine_candidates=frozenset({"joint-sep"})))
    assert a.read_bytes() != b.read_bytes()


# ---------------------------------------------------------------------------
# W4 — spectral hybrid
# ---------------------------------------------------------------------------

def test_ynsn_fits_halftone_physics(tmp_path):
    from workflow.profile_engine.spectral_model import fit_ynsn
    p, ti3 = _cmyk_setup(tmp_path)
    meas = read_ti3(ti3)
    rng = np.random.default_rng(1717)
    holdout = rng.permutation(len(meas.device))[:90]
    m = fit_ynsn(meas, holdout)
    assert m is not None
    err = delta_e_2000(m.lab_relative(meas.device[holdout]),
                       meas.lab_relative[holdout])
    assert float(np.median(err)) < 1.6      # physics fits a halftone chart
    assert 1.0 <= m.nu <= 10.0


def test_spectral_inapplicable_paths(tmp_path):
    from workflow.profile_engine.spectral_model import fit_spectral_hybrid
    p = PRINTERS["S1"]                       # RGB → not applicable
    chart = make_chart(p, 400)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "rgb.ti3", p, chart, xyz, refl)
    meas = read_ti3(ti3)
    from workflow.profile_engine.forward_model import fit_forward_model
    base = fit_forward_model(meas.device, meas.lab_relative, grid=9,
                             lam=0.03)
    assert fit_spectral_hybrid(meas, base, base_lam=0.03) is None

    p2, ti3b = _cmyk_setup(tmp_path)          # no SPEC columns
    meas2 = read_ti3(ti3b)
    meas2.spectral = None
    base2 = fit_forward_model(meas2.device, meas2.lab_relative, grid=5,
                              lam=0.05)
    assert fit_spectral_hybrid(meas2, base2, base_lam=0.05) is None


def test_spectral_challenge_wins_sparse_multiink(tmp_path):
    # 6 channels × 700 patches = sparse coverage: physics must win the
    # held-out challenge there (the whole W4 pitch).
    from workflow.profile_engine.accuracy import fit_forward_model_accurate
    from workflow.profile_engine.spectral_model import fit_spectral_hybrid
    p = PRINTERS["S5"]
    chart = make_chart(p, 700)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "s5.ti3", p, chart, xyz, refl)
    meas = read_ti3(ti3)
    meas.average_endpoints()
    base, _, _ = fit_forward_model_accurate(
        meas.device, meas.lab_relative, grid=9, base_lam=0.03,
        curve_rounds=1)
    ch = fit_spectral_hybrid(meas, base, base_lam=0.03)
    assert ch is not None
    model, line = ch
    assert "wins the held-out challenge" in line
    assert model.grid == base.grid and model.n_channels == 6


# ---------------------------------------------------------------------------
# W5 — bijective radial intents
# ---------------------------------------------------------------------------

def _mapper(intent="p"):
    src = source_surface_lab("adobe")
    p = PRINTERS["S1"]
    dev = halton(2500, 3, seed=3)
    face = halton(1500, 3, seed=4)
    rng = np.random.default_rng(0)
    face[np.arange(1500), rng.integers(0, 3, 1500)] = \
        rng.integers(0, 2, 1500).astype(float)
    dst = p.lab_relative_true(np.vstack([dev, face]))
    return RadialUcsMapper(src, dst, intent)


def test_knee_map_exact_inverse():
    rng = np.random.default_rng(3)
    r = rng.uniform(0.0, 120.0, 500)
    rs = rng.uniform(40.0, 120.0, 500)
    rd = rng.uniform(20.0, 110.0, 500)
    fwd = _knee_map(r, rs, rd, 0.75)
    back = _knee_map(fwd, rs, rd, 0.75, inverse=True)
    assert np.abs(back - r).max() < 1e-9
    # monotone in r for fixed geometry
    rr = np.linspace(0.0, 150.0, 400)
    m = _knee_map(rr, np.full_like(rr, 100.0), np.full_like(rr, 60.0), 0.75)
    assert (np.diff(m) > 0).all()


def test_radial_mapper_bijective_on_print_domain():
    m = _mapper()
    rng = np.random.default_rng(5)
    l = rng.uniform(3, 99, 2000)
    c = rng.uniform(0, 1, 2000) * np.minimum(120.0, 4.0 * l + 10.0)
    h = rng.uniform(0, 2 * np.pi, 2000)
    lab = np.column_stack([l, c * np.cos(h), c * np.sin(h)])
    rt = m.unmap_lab(m.map_lab(lab))
    assert np.abs(rt - lab).max() < 1e-3


def test_radial_mapper_midtones_protected():
    m = _mapper()
    core = np.array([[50.0, 10.0, 5.0], [65.0, -8.0, 12.0]])
    moved = np.linalg.norm(m.map_lab(core) - core, axis=1)
    assert moved.max() < 1.5
    white = m.map_lab(np.array([[100.0, 0.0, 0.0]]))[0]
    assert white[0] > 95.0 and abs(white[1]) < 1 and abs(white[2]) < 1


def test_render2_build_distinct_and_invertible(tmp_path):
    p = PRINTERS["S1"]
    chart = make_chart(p, 500)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "c.ti3", p, chart, xyz, refl)
    icc = tmp_path / "c.icc"
    res = build_profile(ti3, icc, BuildSettings(
        quality="l", gammap_mode="accurate",
        engine_candidates=frozenset({"render2"}),
        source_gamut="assets/profiles/ClayRGB1998.icm",
        inverse_gamut_a2b=True))
    assert res.perceptual_distinct
    from benchmarks.iccread import IccProfile
    prof = IccProfile(icc)
    assert prof.tags["B2A0"] != prof.tags["B2A1"]
    assert prof.tags["A2B0"] != prof.tags["A2B1"]
    rng = np.random.default_rng(9)
    dev = rng.uniform(0.15, 0.85, (500, 3))
    lab_in = prof.a2b_lab(dev, "A2B1")
    rt = prof.a2b_lab(prof.b2a_device(lab_in, "B2A0"), "A2B0")
    de = delta_e_2000(rt, lab_in)
    # -nI identity by algebra: the residual is grid-9 table interpolation
    # only (measured med 1.06 at -ql; 0.31 at -qm), no fixed-point error.
    assert float(np.median(de)) < 1.5
    assert float(de.max()) < 5.0


def test_render2_defers_to_argyll_on_explicit_intents(tmp_path):
    # An explicit -t intent keeps the Argyll-matched path (mapper is not
    # the radial one) — render2 only covers the default selections.
    from workflow.profile_engine.gamut_map import build_mapped_b2a
    import inspect
    src_txt = inspect.getsource(build_mapped_b2a)
    assert 'not getattr(settings, "perc_intent", "")' in src_txt
