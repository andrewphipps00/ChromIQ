"""Strip-recognition combo (chartread -B / -b) in TabMeasure.

Both modes expose a single 'Strip recognition' combo (Default / -B / -b) plus
an Auto toggle. Auto derives the value from the chart's instrument and locks
(but still shows) the combo; with Auto off the user picks it. This verifies the
auto/manual resolution, the i1 Pro → -b mapping, and migration from the old
two-checkbox settings/preset scheme.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import DEFAULTS  # noqa: E402
from ui.tabs.tab_measure import TabMeasure  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _StubSettings:
    def __init__(self, **overrides):
        self._d = dict(DEFAULTS)
        self._d.update(overrides)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


@pytest.fixture
def tab():
    settings = _StubSettings()
    return TabMeasure(ArgyllRunner(settings), settings)


# --- _coerce_bidir_mode: migration from the old two-boolean scheme ----------

@pytest.mark.parametrize(
    "mode, legacy_disable, legacy_force, fallback, expected",
    [
        ("force",   False, False, "default", "force"),     # new key wins
        ("disable", True,  True,  "default", "disable"),   # new key wins
        (None,      True,  False, "default", "disable"),   # legacy -B
        (None,      False, True,  "default", "force"),     # legacy -b
        (None,      False, False, "default", "default"),   # neither -> fallback
        ("bogus",   False, True,  "default", "force"),     # invalid -> legacy
        (None,      False, False, "disable", "disable"),   # honour fallback
    ],
)
def test_coerce_bidir_mode(mode, legacy_disable, legacy_force, fallback, expected):
    assert TabMeasure._coerce_bidir_mode(mode, legacy_disable, legacy_force, fallback) == expected


# --- Auto resolution per detected instrument --------------------------------

@pytest.mark.parametrize("mode", ["guided", "manual"])
def test_auto_resolves_i1pro_to_force(tab, mode):
    tab._detected_disable_bidir = False
    tab._detected_force_bidir = True
    tab._apply_bidir_auto_state(mode)
    assert tab._resolve_bidir_value(mode) == "force"
    assert tab._resolve_force_bidir(mode) is True
    assert tab._resolve_disable_bidir(mode) is False
    _auto, combo = tab._bidir_widgets(mode)
    assert combo.currentData() == "force"
    assert combo.isEnabled() is False  # locked while Auto is on


@pytest.mark.parametrize("mode", ["guided", "manual"])
def test_auto_resolves_colormunki_to_default(tab, mode):
    # The ColorMunki no longer auto-selects -B: Auto leaves it on the Argyll
    # default (mirrors the real detection via ui.ti2_loader).
    from ui.ti2_loader import disable_bidir_for_instrument, force_bidir_for_instrument
    tab._detected_disable_bidir = disable_bidir_for_instrument("X-Rite ColorMunki")
    tab._detected_force_bidir = force_bidir_for_instrument("X-Rite ColorMunki")
    tab._apply_bidir_auto_state(mode)
    assert tab._resolve_bidir_value(mode) == "default"
    assert tab._resolve_disable_bidir(mode) is False
    assert tab._resolve_force_bidir(mode) is False


@pytest.mark.parametrize("mode", ["guided", "manual"])
def test_auto_resolves_disable_flag(tab, mode):
    # Mechanism check: when the detected value is "disable" (e.g. a future
    # one-direction-only instrument), Auto resolves and locks to it.
    tab._detected_disable_bidir = True
    tab._detected_force_bidir = False
    tab._apply_bidir_auto_state(mode)
    assert tab._resolve_bidir_value(mode) == "disable"
    assert tab._resolve_disable_bidir(mode) is True
    assert tab._resolve_force_bidir(mode) is False


@pytest.mark.parametrize("mode", ["guided", "manual"])
def test_auto_resolves_unknown_to_default(tab, mode):
    tab._detected_disable_bidir = False
    tab._detected_force_bidir = False
    tab._apply_bidir_auto_state(mode)
    assert tab._resolve_bidir_value(mode) == "default"
    assert tab._resolve_disable_bidir(mode) is False
    assert tab._resolve_force_bidir(mode) is False


# --- Manual (Auto off): the combo selection is honoured ---------------------

@pytest.mark.parametrize("mode", ["guided", "manual"])
@pytest.mark.parametrize("value", ["default", "disable", "force"])
def test_manual_combo_value_honoured_when_auto_off(tab, mode, value):
    auto_cb, combo = tab._bidir_widgets(mode)
    auto_cb.setChecked(False)
    tab._apply_bidir_auto_state(mode)
    assert combo.isEnabled() is True
    combo.setCurrentIndex(combo.findData(value))
    assert tab._resolve_bidir_value(mode) == value


# --- Preset round-trip + legacy preset migration ----------------------------

def test_manual_preset_roundtrip_force(tab):
    tab._m_bidir_auto_cb.setChecked(False)
    tab._m_bidir_combo.setCurrentIndex(tab._m_bidir_combo.findData("force"))
    data = tab._m_collect_preset_data()
    assert data["bidir_mode"] == "force"
    # Apply into a fresh combo state.
    tab._m_bidir_combo.setCurrentIndex(tab._m_bidir_combo.findData("default"))
    tab._m_apply_preset_data(data)
    assert tab._m_bidir_combo.currentData() == "force"


def test_manual_legacy_preset_migrates(tab):
    tab._m_apply_preset_data({"force_bidir": True, "bidir_auto": False})
    assert tab._m_bidir_combo.currentData() == "force"
    tab._m_apply_preset_data({"bidir": True, "bidir_auto": False})
    assert tab._m_bidir_combo.currentData() == "disable"


# --- Forced-bidir-on-fixed-order-chart warning -------------------------------

from pathlib import Path  # noqa: E402

from workflow.measure_manager import MeasureParams  # noqa: E402


def _params(force_bidir):
    return MeasureParams(ti1_path=Path("/tmp/x.ti2"), instrument="1",
                         force_bidir=force_bidir)


def test_confirm_skips_when_not_forcing(tab):
    tab._detected_randomized = False
    assert tab._confirm_nonrandom_bidir(_params(False)) is True


def test_confirm_skips_when_randomized(tab):
    tab._detected_randomized = True
    assert tab._confirm_nonrandom_bidir(_params(True)) is True


def test_confirm_skips_when_suppressed(tab):
    tab._detected_randomized = False
    tab._settings.set("measure_hide_nonrandom_bidir_warning", True)
    assert tab._confirm_nonrandom_bidir(_params(True)) is True


def test_confirm_shows_dialog_and_honours_choice(tab, monkeypatch):
    # force_bidir + fixed-order chart + not suppressed -> the dialog is shown.
    from PyQt6.QtWidgets import QDialog
    tab._detected_randomized = False
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert tab._confirm_nonrandom_bidir(_params(True)) is False
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert tab._confirm_nonrandom_bidir(_params(True)) is True
