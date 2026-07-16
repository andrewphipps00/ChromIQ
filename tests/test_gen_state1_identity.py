"""State-1 (RGB) identity guard for the New-chart dialog (#72 Tier B, R1).

Captured BEFORE any Tier-B UI work, these goldens pin the RGB experience so
the device-type / colorant / ink-limit / preconditioning additions cannot
change it:

* ``gen_state_rgb_default.json`` — the exact ``_collect_gen_state()`` dict of
  a fresh dialog. With device type RGB the dict must never grow new keys
  (old ChromIQ versions and old presets must stay round-trippable).
* ``gen_program_rgb_golden.json`` — ``_build_generated_program()`` output for
  a fixed configuration, compared patch-for-patch.
* Any future N-channel row (device type, colorant slots, ink limit,
  preconditioning profile) must be invisible while the dialog is in RGB
  state — asserted over the live widget tree by object-name convention
  (``nch_``-prefixed), which Tier B's widgets must follow.

Regenerate (ONLY for an intentional, reviewed change of the RGB behaviour):
    python tests/test_gen_state1_identity.py --regenerate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui.dialogs.ti2_relayout_dialog import _NewChartDialog  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden"
STATE_GOLDEN = GOLDEN_DIR / "gen_state_rgb_default.json"
PROGRAM_GOLDEN = GOLDEN_DIR / "gen_program_rgb_golden.json"


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


def _fresh_dialog(tmp_path) -> _NewChartDialog:
    return _NewChartDialog(tmp_path, _FakeSettings())


def _fixed_program_config(dlg: _NewChartDialog) -> None:
    """A deterministic multi-set configuration for the golden program."""
    dlg._mode_generate.setChecked(True)
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(5)
    dlg._gen_skin.setChecked(True)
    dlg._gen_nearneutral.setChecked(True)
    dlg._gen_hs.setChecked(True)
    dlg._update_gen_counts()


def test_rgb_gen_state_has_exactly_the_golden_keys(qapp, tmp_path):
    st = _fresh_dialog(tmp_path)._collect_gen_state()
    want = json.loads(STATE_GOLDEN.read_text(encoding="utf-8"))
    assert sorted(st.keys()) == sorted(want.keys()), (
        "RGB _collect_gen_state grew/lost keys — #72 requires that the RGB "
        "state carries NO new keys (new keys appear only in states 2/3), so "
        "old versions and old presets stay round-trippable.")
    assert sorted(st["layout"].keys()) == sorted(want["layout"].keys())


def test_rgb_gen_state_default_values_unchanged(qapp, tmp_path):
    st = _fresh_dialog(tmp_path)._collect_gen_state()
    want = json.loads(STATE_GOLDEN.read_text(encoding="utf-8"))
    assert st == want


def test_pre_change_state_round_trips(qapp, tmp_path):
    # The captured pre-Tier-B dict applies cleanly and collects back equal:
    # forward-compat for every preset/default saved by released versions.
    dlg = _fresh_dialog(tmp_path)
    want = json.loads(STATE_GOLDEN.read_text(encoding="utf-8"))
    dlg._apply_gen_state(want)
    assert dlg._collect_gen_state() == want


def test_rgb_generated_program_matches_golden(qapp, tmp_path):
    dlg = _fresh_dialog(tmp_path)
    _fixed_program_config(dlg)
    got = [list(p) for p in dlg._build_generated_program()]
    want = json.loads(PROGRAM_GOLDEN.read_text(encoding="utf-8"))
    assert len(got) == len(want)
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == pytest.approx(w, abs=1e-9), f"patch #{i + 1} changed"


def test_no_nchannel_rows_visible_in_rgb_state(qapp, tmp_path):
    # Tier B's device-type/colorant/ink-limit/profile widgets carry an
    # 'nch_'-prefixed objectName; in RGB state none may be visible. (Vacuously
    # green before Tier B; bites the moment the rows exist.)
    dlg = _fresh_dialog(tmp_path)
    dlg.show()
    offenders = [w.objectName() for w in dlg.findChildren(QWidget)
                 if w.objectName().startswith("nch_") and not w.isHidden()]
    dlg.close()
    assert not offenders, f"N-channel rows visible in RGB state: {offenders}"


if __name__ == "__main__" and "--regenerate" in sys.argv:
    app = QApplication.instance() or QApplication([])
    import tempfile

    GOLDEN_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        dlg = _fresh_dialog(Path(td))
        STATE_GOLDEN.write_text(
            json.dumps(dlg._collect_gen_state(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        dlg2 = _fresh_dialog(Path(td))
        _fixed_program_config(dlg2)
        PROGRAM_GOLDEN.write_text(
            json.dumps([list(p) for p in dlg2._build_generated_program()]) + "\n",
            encoding="utf-8")
    print(f"regenerated {STATE_GOLDEN} and {PROGRAM_GOLDEN}")
