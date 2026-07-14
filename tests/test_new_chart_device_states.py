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
    # Paste is hex/RGB-only; generate works via the multi-ink rows (Tier C).
    assert not dlg._mode_paste.isEnabled()
    assert dlg._mode_generate.isEnabled()
    assert dlg._ink_limit.value() == 300         # the safe default
    # Row gating: RGB-cube constructs off, look-based off (no profile),
    # N-native rows + the multi-ink rows on. (Generate mode first — the whole
    # panel is greyed in seed mode, which would mask the per-row state.)
    dlg._mode_generate.setChecked(True)
    dlg._update_gen_counts()
    assert not dlg._gen_cube.isEnabled() and not dlg._gen_edges.isEnabled()
    assert not dlg._gen_skin.isEnabled() and not dlg._gen_blues.isEnabled()
    assert dlg._gen_neutral.isEnabled() and dlg._gen_nearneutral.isEnabled()
    assert dlg._gen_whiteblack.isEnabled()
    assert not dlg._nch_gen_box.isHidden()
    assert "grey balance" in dlg._gen_nearneutral.toolTip()


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


@needs_cmyk_profile
def test_state3_unlocks_perceptual_rows(dlg):
    dlg._mode_generate.setChecked(True)
    _pick_device(dlg, "cmyk")
    dlg._precond_path = str(GENERIC_CMYK)
    dlg._refresh_nch_state()
    assert dlg._nch_state() == 3
    assert dlg._gen_skin.isEnabled() and dlg._gen_blues.isEnabled()
    assert not dlg._gen_cube.isEnabled()           # cube-bound stays off forever
    # Back to state 2: perceptual re-locks with the needs-profile tooltip.
    dlg._clear_precond()
    assert not dlg._gen_skin.isEnabled()
    assert "preconditioning profile" in dlg._gen_skin.toolTip()
    # And back to RGB: original tooltips restored.
    _pick_device(dlg, "rgb")
    assert dlg._gen_cube.isEnabled() and dlg._gen_skin.isEnabled()
    assert "preconditioning" not in dlg._gen_skin.toolTip()


def test_nch_program_builds_device_tuples(dlg, monkeypatch):
    # State 2, no targen (stubbed out): ramps + pairs + rings + white/black.
    monkeypatch.setattr(M.R, "seed_from_targen",
                        lambda *a, **k: [(1.0, 2.0, 3.0, 4.0)])
    _pick_device(dlg, "cmyk")
    dlg._mode_generate.setChecked(True)
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_nearneutral.setChecked(True)
    dlg._gen_nearneutral_n.setValue(8)
    dlg._gen_whiteblack.setChecked(True)
    dlg._gen_unique.setChecked(True)
    dlg._nch_targen.setChecked(True)
    dlg._nch_targen_n.setValue(50)
    dlg._nch_perink.setChecked(True)
    dlg._nch_perink_n.setValue(4)
    dlg._nch_pairs.setChecked(True)
    dlg._nch_pairs_n.setValue(2)
    prog = dlg._build_generated_program()
    assert prog and all(len(p) == 4 for p in prog)
    # targen stub (1) + per-ink 16 + pairs 12 + rings + white/black anchors.
    from workflow.patch_generators import near_neutrals_count
    rings = near_neutrals_count(8, 1)
    # White/black tops up to N of each — a ring patch that already sits on an
    # anchor cell counts toward it, hence >= …+1 not a fixed +2.
    assert len(prog) >= 1 + 16 + 12 + rings + 1
    # Ticked-but-disabled RGB rows contribute nothing.
    dlg._gen_cube.setChecked(True)
    assert len(dlg._build_generated_program()) == len(prog)
    # White/black anchors present: ink white = all-0, black = K-only.
    assert (0.0, 0.0, 0.0, 0.0) in prog
    assert (0.0, 0.0, 0.0, 100.0) in prog


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
