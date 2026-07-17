"""Black generation (colprof -k/-K) — UI, plumbing and engine behaviour.

Covers the whole chain: the Manual-module widgets (dropdown, -K checkbox,
custom-curve spinboxes with visibility), collection into ProfileParams,
colprof argument construction, hand-typed extra-args parsing, defaults +
preset persistence, the faithful ``icxKcurveNF`` port, and the engine
actually separating differently under different rules.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_profile import TabProfile  # noqa: E402
from workflow.engine_builder import (ExtraArgsError, _apply_extra_args,
                                     settings_from_params)  # noqa: E402
from workflow.profile_builder import ProfileBuilder, ProfileParams  # noqa: E402
from workflow.profile_engine import BuildSettings  # noqa: E402
from workflow.profile_engine.b2a import (K_RULE_PARAMS, argyll_k_curve,
                                         invert_to_device)  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _tab(tmp_path, **prefs):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    for k, v in prefs.items():
        s.set(k, v)
    return TabProfile(ArgyllRunner(s), s)


# ---------------------------------------------------------------------------
# The curve port (icxKcurveNF)
# ---------------------------------------------------------------------------

def test_k_curve_letter_rules():
    ls = np.linspace(0.0, 100.0, 21)
    assert np.allclose(argyll_k_curve(ls, params=K_RULE_PARAMS["z"]), 0.0)
    assert np.allclose(argyll_k_curve(ls, params=K_RULE_PARAMS["h"]), 0.5)
    assert np.allclose(argyll_k_curve(ls, params=K_RULE_PARAMS["x"]), 1.0)
    ramp = argyll_k_curve(ls, params=K_RULE_PARAMS["r"], l_min=0.0,
                          l_max=100.0)
    assert ramp[0] == pytest.approx(1.0)       # black end → full K
    assert ramp[-1] == pytest.approx(0.0)      # white end → no K
    assert (np.diff(ramp) <= 1e-12).all()      # monotone in L*
    # shape=1 keeps the ramp straight regardless of the skew mapping.
    assert ramp[10] == pytest.approx(0.5, abs=1e-9)


def test_k_curve_custom_breakpoints_and_shape():
    params = (0.1, 0.2, 0.8, 0.9, 1.0)
    l = np.array([100.0, 90.0, 50.0, 5.0, 0.0])
    k = argyll_k_curve(l, params=params, l_min=0.0, l_max=100.0)
    assert k[0] == pytest.approx(0.1)          # above stpo: start level
    assert k[1] == pytest.approx(0.1)          # p=0.1 < stpo=0.2
    assert k[2] == pytest.approx(0.5, abs=1e-9)  # midpoint, straight shape
    assert k[3] == pytest.approx(0.9)          # p=0.95 > enpo
    assert k[4] == pytest.approx(0.9)
    # concave vs convex bend around the straight line at the midpoint.
    k_conc = argyll_k_curve(np.array([50.0]), params=(0.1, 0.2, 0.8, 0.9,
                                                      0.4),
                            l_min=0.0, l_max=100.0)[0]
    k_conv = argyll_k_curve(np.array([50.0]), params=(0.1, 0.2, 0.8, 0.9,
                                                      1.6),
                            l_min=0.0, l_max=100.0)[0]
    assert k_conc < 0.5 < k_conv
    # swapped breakpoints are reordered like Argyll does.
    k_sw = argyll_k_curve(l, params=(0.9, 0.8, 0.2, 0.1, 1.0),
                          l_min=0.0, l_max=100.0)
    assert np.allclose(k_sw, k)


# ---------------------------------------------------------------------------
# Plumbing: ProfileParams → colprof args / engine settings / extra args
# ---------------------------------------------------------------------------

def test_colprof_args_carry_k_flags(tmp_path):
    p = ProfileParams(ti3_path=tmp_path / "x.ti3", k_rule="x")
    args = ProfileBuilder(None)._build_args(p)
    assert "-kx" in args
    p = ProfileParams(ti3_path=tmp_path / "x.ti3", k_rule="p", k_locus=True,
                      k_stle=0.0, k_stpo=0.1, k_enpo=0.9, k_enle=1.0,
                      k_shape=0.5)
    args = ProfileBuilder(None)._build_args(p)
    i = args.index("-Kp")
    assert args[i + 1:i + 6] == ["0", "0.1", "0.9", "1", "0.5"]
    # unset → no flag at all (colprof's own default ramp).
    args = ProfileBuilder(None)._build_args(
        ProfileParams(ti3_path=tmp_path / "x.ti3"))
    assert not any(a.startswith("-k") or a.startswith("-K") for a in args)


def test_settings_from_params_maps_k(tmp_path):
    p = ProfileParams(ti3_path=tmp_path / "x.ti3", k_rule="p", k_locus=True,
                      k_stle=0.2, k_stpo=0.1, k_enpo=0.8, k_enle=0.9,
                      k_shape=1.2)
    s = settings_from_params(p)
    assert s.k_rule == "p" and s.k_locus is True
    assert s.k_curve_params == (0.2, 0.1, 0.8, 0.9, 1.2)
    s = settings_from_params(ProfileParams(ti3_path=tmp_path / "x.ti3",
                                           k_rule="x"))
    assert s.k_rule == "x" and s.k_curve_params is None


def test_extra_args_parse_k_flags():
    s = BuildSettings()
    _apply_extra_args("-kx", s)
    assert s.k_rule == "x" and s.k_locus is False
    s = BuildSettings()
    _apply_extra_args("-Kp 0 0.1 0.9 1 0.5", s)
    assert s.k_rule == "p" and s.k_locus is True
    assert s.k_curve_params == (0.0, 0.1, 0.9, 1.0, 0.5)
    with pytest.raises(ExtraArgsError):
        _apply_extra_args("-kq", BuildSettings())
    with pytest.raises(ExtraArgsError):
        _apply_extra_args("-kp 0 0.1", BuildSettings())   # missing values


# ---------------------------------------------------------------------------
# Engine behaviour: the rule actually steers the separation
# ---------------------------------------------------------------------------

def test_engine_separation_follows_k_rule():
    from tests.test_profile_engine import synth_xyz
    from workflow.profile_engine.forward_model import fit_forward_model
    from workflow.profile_engine.ti3_data import xyz_to_lab
    rng = np.random.default_rng(6)
    dev = rng.uniform(0.0, 1.0, (500, 4))
    lab = xyz_to_lab(synth_xyz(dev, additive=False))
    model = fit_forward_model(dev, lab, grid=5, lam=0.05, curve_rounds=0)
    # dark-ish neutral targets, comfortably in gamut
    targets = np.stack([np.linspace(25.0, 55.0, 12),
                        np.zeros(12), np.zeros(12)], 1)
    kw = dict(channel_letters=list("CMYK"), is_additive=False)
    d_z, r_z = invert_to_device(model, targets,
                                k_gen={"rule": "z", "params": None}, **kw)
    d_x, r_x = invert_to_device(model, targets,
                                k_gen={"rule": "x", "params": None}, **kw)
    ok = (r_z < 1.0) & (r_x < 1.0)             # both in gamut
    assert ok.sum() >= 6
    # Maximum-black separation uses clearly more K than zero-black — while
    # both stay colour-accurate (the prior only resolves the null space).
    assert d_x[ok, 3].mean() > d_z[ok, 3].mean() + 0.25


# ---------------------------------------------------------------------------
# UI: widgets, collection, defaults and preset persistence
# ---------------------------------------------------------------------------

def test_manual_ui_collects_and_persists_k(qapp, tmp_path):
    tab = _tab(tmp_path)
    try:
        # custom-curve row only shows for the "p" rule; -K needs a rule.
        assert tab._m_kgen_curve_widget.isHidden() or \
            not tab._m_kgen_curve_widget.isVisible()
        assert not tab._m_kgen_locus_cb.isEnabled()
        tab._m_kgen_combo.setCurrentIndex(tab._m_kgen_combo.findData("p"))
        assert tab._m_kgen_locus_cb.isEnabled()
        tab._m_kgen_locus_cb.setChecked(True)
        tab._m_kgen_spins["stle"].setValue(0.15)
        tab._m_kgen_spins["shape"].setValue(0.6)
        tab._ti3_path = tmp_path / "m.ti3"

        params = tab._collect_manual_profile()
        assert params.k_rule == "p" and params.k_locus is True
        assert params.k_stle == pytest.approx(0.15)
        assert params.k_shape == pytest.approx(0.6)

        # Save as Defaults (manual branch) → reload via preset index 0.
        tab._switch_mode("manual")
        tab._on_save_defaults()
        tab._m_kgen_combo.setCurrentIndex(tab._m_kgen_combo.findData(""))
        tab._on_m_preset_selected(0)
        assert tab._m_kgen_combo.currentData() == "p"
        assert tab._m_kgen_locus_cb.isChecked()
        assert tab._m_kgen_spins["stle"].value() == pytest.approx(0.15)

        # Preset data roundtrip carries the whole rule.
        data = tab._m_collect_preset_data()
        assert data["kgen_rule"] == "p" and data["kgen_locus"] is True
        tab._m_kgen_combo.setCurrentIndex(tab._m_kgen_combo.findData("x"))
        tab._m_kgen_locus_cb.setChecked(False)
        tab._m_apply_preset_data(data)
        assert tab._m_kgen_combo.currentData() == "p"
        assert tab._m_kgen_locus_cb.isChecked()
        assert tab._m_kgen_spins["shape"].value() == pytest.approx(0.6)
        # clearing the rule unticks and disables -K.
        tab._m_kgen_combo.setCurrentIndex(tab._m_kgen_combo.findData(""))
        assert not tab._m_kgen_locus_cb.isEnabled()
        assert not tab._m_kgen_locus_cb.isChecked()
    finally:
        tab.deleteLater()
