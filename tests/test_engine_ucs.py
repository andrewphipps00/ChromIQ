"""W1 tests (issue #123): CAM16-UCS space + candidate "ucs" integration."""
import numpy as np
import pytest

from benchmarks.synthetic import PRINTERS, make_chart, measure, write_ti3
from workflow.profile_engine.b2a import _UcsView, invert_to_device
from workflow.profile_engine.builder import BuildSettings, build_profile
from workflow.profile_engine.forward_model import fit_forward_model
from workflow.profile_engine.metrics import delta_e_2000
from workflow.profile_engine.ucs import Cam16, Cam16Ucs, print_ucs


def test_cam16_pinned_to_published_example():
    # colour-science / Li et al. 2017 reference case.
    cam = Cam16(La=318.31, Yb=20.0,
                white_xyz100=np.array([95.05, 100.0, 108.88]))
    j, m, h = cam.xyz_to_jmh(np.array([[19.01, 20.00, 21.78]]))[0]
    assert j == pytest.approx(41.73121, abs=1e-4)
    assert h == pytest.approx(217.06796, abs=1e-4)
    assert m == pytest.approx(0.107437, abs=1e-5)
    assert m / cam.FL ** 0.25 == pytest.approx(0.103356, abs=1e-5)


def test_ucs_roundtrip_print_domain():
    u = print_ucs()                      # raises if the import gate fails
    rng = np.random.default_rng(7)
    l = rng.uniform(3.0, 100.0, 500)
    c = rng.uniform(0.0, 1.0, 500) * np.minimum(140.0, 4.0 * l + 10.0)
    h = rng.uniform(0, 2 * np.pi, 500)
    lab = np.column_stack([l, c * np.cos(h), c * np.sin(h)])
    assert np.abs(u.ucs_to_lab(u.lab_to_ucs(lab)) - lab).max() < 1e-4


def test_ucs_white_is_j100():
    u = Cam16Ucs()
    ucs = u.lab_to_ucs(np.array([[100.0, 0.0, 0.0]]))[0]
    assert ucs[0] == pytest.approx(100.0, abs=1e-6)      # J' = J = 100
    # Incomplete adaptation (D<1 at print luminance) leaves D50 white a
    # touch chromatic in CAM16 — that is the model, not a bug.
    assert np.hypot(ucs[1], ucs[2]) < 1.5


def test_ucs_distance_tracks_de00():
    u = print_ucs()
    rng = np.random.default_rng(2)
    l = rng.uniform(10, 95, 2000)
    c = rng.uniform(0, 1, 2000) * np.minimum(90.0, 3.0 * l + 10.0)
    h = rng.uniform(0, 2 * np.pi, 2000)
    base = np.column_stack([l, c * np.cos(h), c * np.sin(h)])
    d = rng.normal(0, 1.0, (2000, 3))
    de00 = delta_e_2000(base, base + d)
    deu = np.linalg.norm(u.lab_to_ucs(base) - u.lab_to_ucs(base + d), axis=1)
    r = deu / np.maximum(de00, 1e-9)
    assert 0.9 < np.median(r) < 1.5          # consistent scale
    assert np.percentile(r, 95) < 2.2        # bounded disagreement


def _small_rgb_model():
    rng = np.random.default_rng(5)
    dev = rng.uniform(0, 1, (400, 3))
    p = PRINTERS["S1"]
    return fit_forward_model(dev, p.lab_relative_true(dev), grid=9,
                             lam=0.03), p


def test_ucs_view_wraps_model():
    model, _ = _small_rgb_model()
    view = _UcsView(model, print_ucs())
    dev = np.random.default_rng(1).uniform(0, 1, (50, 3))
    expect = print_ucs().lab_to_ucs(model.predict(dev))
    assert np.allclose(view.predict(dev), expect)
    assert view.n_channels == 3


def test_invert_converges_in_ucs_mode():
    model, p = _small_rgb_model()
    rng = np.random.default_rng(3)
    targets = model.predict(rng.uniform(0.15, 0.85, (80, 3)))  # in gamut
    for ucs_flag in (False, True):
        _, res = invert_to_device(model, targets, channel_letters=list("RGB"),
                                  is_additive=True, accurate=True,
                                  ucs=ucs_flag)
        assert np.median(res) < 0.2
        assert res.max() < 1.5


def test_build_with_ucs_candidate(tmp_path):
    p = PRINTERS["S1"]
    chart = make_chart(p, 500)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "c.ti3", p, chart, xyz, refl)
    base = tmp_path / "base.icc"
    cand = tmp_path / "ucs.icc"
    from datetime import datetime, timezone
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    build_profile(ti3, base, BuildSettings(
        quality="l", gammap_mode="accurate", timestamp=ts))
    res = build_profile(ti3, cand, BuildSettings(
        quality="l", gammap_mode="accurate", timestamp=ts,
        engine_candidates=frozenset({"ucs"})))
    assert res.icc_path.exists()
    # The candidate genuinely changes the pipeline…
    assert base.read_bytes() != cand.read_bytes()
    # …and still produces a sane profile.
    assert res.fit_median_de00 < 1.0


def test_candidates_ignored_outside_accurate(tmp_path):
    p = PRINTERS["S1"]
    chart = make_chart(p, 400)
    xyz, refl, _ = measure(p, chart)
    ti3 = write_ti3(tmp_path / "c.ti3", p, chart, xyz, refl)
    from datetime import datetime, timezone
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = tmp_path / "a.icc"
    b = tmp_path / "b.icc"
    build_profile(ti3, a, BuildSettings(quality="l", gammap_mode="fast",
                                        timestamp=ts, description="same"))
    build_profile(ti3, b, BuildSettings(quality="l", gammap_mode="fast",
                                        timestamp=ts, description="same",
                                        engine_candidates=frozenset({"ucs"})))
    assert a.read_bytes() == b.read_bytes()   # fast mode: candidates inert
