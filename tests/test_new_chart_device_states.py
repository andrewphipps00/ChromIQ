"""New-chart dialog three-state device model (#72 Tier B).

State 1 (RGB) identity is locked by test_gen_state1_identity.py; this file
covers states 2/3 and the transitions: row visibility, source-mode gating,
ink-set derivation, inline profile validation, persistence round-trip and the
_on_ok threading into ChartSpec + targen args.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ui.dialogs.ti2_relayout_dialog as M  # noqa: E402
from ui.dialogs.ti2_relayout_dialog import _NewChartDialog  # noqa: E402

GENERIC_CMYK = Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc")
needs_cmyk_profile = pytest.mark.skipif(
    not GENERIC_CMYK.exists(), reason="Generic CMYK profile not installed")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


@pytest.fixture
def dlg(qapp, tmp_path):
    d = _NewChartDialog(tmp_path, _FakeSettings())
    yield d
    d.deleteLater()


def _pick_device(d, data: str) -> None:
    d._device_type.setCurrentIndex(d._device_type.findData(data))


def test_state2_reveals_rows_and_gates_modes(dlg):
    assert dlg._nch_state() == 1
    assert dlg._nch_limit_row.isHidden() and dlg._nch_prof_row.isHidden()
    _pick_device(dlg, "cmyk")
    assert dlg._nch_state() == 2
    assert not dlg._nch_limit_row.isHidden()
    assert not dlg._nch_prof_row.isHidden()
    assert dlg._nch_inks_row.isHidden()          # plain CMYK: no extra slots
    # Paste/generate are RGB constructs until Tier C — seed is forced.
    assert not dlg._mode_paste.isEnabled()
    assert not dlg._mode_generate.isEnabled()
    assert dlg._mode_seed.isChecked()
    assert dlg._ink_limit.value() == 300         # the safe default


def test_extra_ink_chips_and_ink_set_label(dlg):
    _pick_device(dlg, "cmykplus")
    assert not dlg._nch_inks_row.isHidden()
    dlg._on_add_ink(dlg._nch_add_ink.findData("g"))
    dlg._on_add_ink(dlg._nch_add_ink.findData("o"))
    assert dlg._extra_inks == ["g", "o"]
    assert dlg._nch_ink_codes() == ["c", "m", "y", "k", "g", "o"]
    # Label shows the canonical targen order (CMYKOG), not the click order.
    assert "CMYKOG" in dlg._nch_inkset_lbl.text()
    dlg._remove_ink("g")
    assert dlg._extra_inks == ["o"]
    assert "CMYKO" in dlg._nch_inkset_lbl.text()


@needs_cmyk_profile
def test_profile_validation_match_and_mismatch(dlg):
    _pick_device(dlg, "cmyk")
    dlg._precond_path = str(GENERIC_CMYK)
    dlg._refresh_nch_state()
    assert dlg._nch_state() == 3
    assert dlg._nch_prof_status.text().startswith("✓")
    # Same profile on a 6-channel chart: channel mismatch, back to state 2.
    _pick_device(dlg, "cmykplus")
    dlg._on_add_ink(dlg._nch_add_ink.findData("o"))
    dlg._on_add_ink(dlg._nch_add_ink.findData("g"))
    assert dlg._nch_state() == 2
    assert dlg._nch_prof_status.text().startswith("✗")


def test_garbage_profile_is_rejected_inline(dlg, tmp_path):
    bad = tmp_path / "not_a_profile.icc"
    bad.write_bytes(b"garbage")
    _pick_device(dlg, "cmyk")
    dlg._precond_path = str(bad)
    dlg._refresh_nch_state()
    assert dlg._nch_state() == 2
    assert dlg._nch_prof_status.text().startswith("✗")


def test_device_state_persists_and_round_trips(dlg, qapp, tmp_path):
    _pick_device(dlg, "cmykplus")
    dlg._on_add_ink(dlg._nch_add_ink.findData("o"))
    dlg._ink_limit.setValue(280)
    st = dlg._collect_gen_state()
    assert st["device"] == {"type": "cmykplus", "extra_inks": ["o"],
                            "ink_limit": 280, "precond": ""}
    fresh = _NewChartDialog(tmp_path, _FakeSettings())
    fresh._apply_gen_state(st)
    assert fresh._device_type.currentData() == "cmykplus"
    assert fresh._extra_inks == ["o"]
    assert fresh._ink_limit.value() == 280
    fresh.deleteLater()


def test_full_cycle_returns_to_golden_rgb_state(dlg):
    import json
    golden = json.loads(
        (Path(__file__).parent / "golden" / "gen_state_rgb_default.json")
        .read_text(encoding="utf-8"))
    _pick_device(dlg, "cmyk")
    _pick_device(dlg, "cmykplus")
    dlg._on_add_ink(dlg._nch_add_ink.findData("v"))
    dlg._ink_limit.setValue(260)
    _pick_device(dlg, "rgb")
    st = dlg._collect_gen_state()
    assert "device" not in st                      # state 1 carries no new keys
    assert sorted(st.keys()) == sorted(golden.keys())
    assert dlg._mode_paste.isEnabled() and dlg._mode_generate.isEnabled()


def test_on_ok_threads_device_into_spec_and_targen(dlg, monkeypatch):
    calls = {}

    def fake_seed(bin_dir, n, *, device="2", grey_steps=0, good_mode=True,
                  extra_args=None):
        calls["device"] = device
        calls["extra_args"] = list(extra_args or [])
        return [(0.0, 0.0, 0.0, 0.0), (10.0, 20.0, 30.0, 40.0)]

    monkeypatch.setattr(M.R, "seed_from_targen", fake_seed)
    _pick_device(dlg, "cmykplus")
    dlg._on_add_ink(dlg._nch_add_ink.findData("o"))
    dlg._on_add_ink(dlg._nch_add_ink.findData("g"))
    dlg._ink_limit.setValue(280)
    dlg._mode_seed.setChecked(True)
    dlg._on_ok()
    assert calls["device"] == "4"
    assert "-D5" in calls["extra_args"] and "-D7" in calls["extra_args"]
    assert "-l280" in calls["extra_args"]
    assert "-c" not in calls["extra_args"]         # no profile set
    assert dlg.result_spec.color_rep == "CMYKOG"
    assert dlg.result_spec.ink_limit == 280.0
    assert dlg.result_recipe["device"]["type"] == "cmykplus"
    assert dlg.result_program == [(0.0, 0.0, 0.0, 0.0), (10.0, 20.0, 30.0, 40.0)]


def test_on_ok_rgb_unchanged(dlg, monkeypatch):
    calls = {}

    def fake_seed(bin_dir, n, *, device="2", grey_steps=0, good_mode=True,
                  extra_args=None):
        calls["device"] = device
        calls["extra_args"] = list(extra_args or [])
        return [(0.0, 0.0, 0.0)]

    monkeypatch.setattr(M.R, "seed_from_targen", fake_seed)
    dlg._mode_seed.setChecked(True)
    dlg._on_ok()
    assert calls["device"] == "2"
    assert calls["extra_args"] == []
    assert dlg.result_spec.color_rep == "iRGB"
    assert dlg.result_spec.ink_limit is None
