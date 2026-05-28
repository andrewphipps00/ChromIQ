"""Expert Manual-mode rows must persist their *enable-checkbox* state, not just
their value, through Save-as-Defaults and presets.

Expert non-boolean rows (ink limit -l, OFPS adaptation -A, cube steps -m/-M/-b,
the distribution selector) keep the enable-checkbox separate from the value
control. ``get_raw_value()`` returns only the control's value, so before the fix
saving a default/preset recorded the value but never whether the flag was armed;
on restore the value came back but the checkbox stayed off and ``build_args``
dropped the flag. These tests pin the round-trip end-to-end (save → fresh tab →
``_collect_manual``) and the preset save/restore paths.
"""
from __future__ import annotations

import os
import shlex

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs import tab_chart as tab_chart_mod  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    """An AppSettings backed by an isolated ini file (never the user's config)."""
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "chromiq_test.ini"), QSettings.Format.IniFormat)
    return s


def _make_tab(qapp, settings) -> TabChart:
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    return t


def _targen_pw(tab, flag):
    for pw in tab._manual_widgets.get("targen", []):
        if pw.flag == flag:
            return pw
    raise AssertionError(f"No targen widget for flag {flag!r}")


def _arm(tab, flag, value):
    """Set an expert row's value and tick its enable-checkbox, like a user would."""
    pw = _targen_pw(tab, flag)
    pw.set_value(value)
    assert pw.has_separate_enable, f"{flag} is not an expert non-boolean row"
    pw.set_user_enabled(True)


def _extra_args(tab) -> list[str]:
    return shlex.split(tab._collect_manual().extra_targen_args)


# ---------------------------------------------------------------------------
# Save-as-Defaults round-trip
# ---------------------------------------------------------------------------

def test_save_defaults_roundtrips_enable_state(qapp, settings):
    tab1 = _make_tab(qapp, settings)
    _arm(tab1, "-l", 280)
    tab1._on_save_defaults()

    # A brand-new tab on the same (persisted) settings restores via __init__.
    tab2 = _make_tab(qapp, settings)
    assert _targen_pw(tab2, "-l").is_enabled_by_user is True
    args = _extra_args(tab2)
    assert "-l" in args
    assert args[args.index("-l") + 1] == "280"


def test_save_defaults_roundtrips_distribution_selector(qapp, settings):
    tab1 = _make_tab(qapp, settings)
    _arm(tab1, "__targen_distribution__", "-R")
    tab1._on_save_defaults()

    tab2 = _make_tab(qapp, settings)
    assert "-R" in _extra_args(tab2)


def test_save_defaults_unarmed_flag_stays_off(qapp, settings):
    # Default: nothing armed → nothing persisted enabled → fresh tab emits none.
    _make_tab(qapp, settings)._on_save_defaults()
    tab2 = _make_tab(qapp, settings)
    assert _targen_pw(tab2, "-l").is_enabled_by_user is False
    assert "-l" not in _extra_args(tab2)


# ---------------------------------------------------------------------------
# Default entry in the preset dropdown re-applies saved defaults
# ---------------------------------------------------------------------------

def test_default_preset_entry_reapplies_armed_flag(qapp, settings):
    tab = _make_tab(qapp, settings)
    _arm(tab, "-l", 250)
    tab._on_save_defaults()
    # Disarm in the live UI, then pick "Default" (index 0) — it must come back.
    _targen_pw(tab, "-l").set_user_enabled(False)
    tab._on_preset_selected(0)
    assert _targen_pw(tab, "-l").is_enabled_by_user is True
    assert "-l" in _extra_args(tab)


def test_default_preset_entry_reverts_flag_absent_from_saved_defaults(qapp, settings):
    """The reported bug: a row the user armed but that wasn't part of the saved
    defaults (e.g. saved before the option existed) must revert when picking
    "Default" — not stay stuck on."""
    tab = _make_tab(qapp, settings)
    tab._on_save_defaults()              # defaults captured with -l NOT armed
    _arm(tab, "-l", 280)                 # user arms it afterwards
    assert "-l" in _extra_args(tab)
    tab._on_preset_selected(0)           # pick "Default"
    assert _targen_pw(tab, "-l").is_enabled_by_user is False
    assert "-l" not in _extra_args(tab)


def test_default_preset_entry_reverts_changed_value_without_saved_default(qapp, settings):
    """A non-expert value the user changed but never saved must also revert to
    its factory default when picking "Default" (fresh settings, nothing saved)."""
    tab = _make_tab(qapp, settings)
    f_pw = _targen_pw(tab, "-f")
    factory = f_pw.get_raw_value()
    f_pw.set_value(factory + 137)
    assert f_pw.get_raw_value() != factory
    tab._on_preset_selected(0)
    assert f_pw.get_raw_value() == factory


# ---------------------------------------------------------------------------
# Preset save / restore
# ---------------------------------------------------------------------------

def test_preset_save_and_restore_roundtrips_enable_state(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    _arm(tab, "-A", 0.5)

    # Drive the Save-Preset dialog headlessly: fill the name field, accept.
    def fake_exec(self):
        edit = self.findChild(QLineEdit)
        if edit is not None:
            edit.setText("MyExpertPreset")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(tab_chart_mod.QDialog, "exec", fake_exec, raising=True)
    tab._on_preset_save()

    saved = tab._load_presets_from_settings().get("MyExpertPreset")
    assert saved is not None
    assert saved.get("targen_-A_enabled") is True

    # Disarm live, then restore the preset — the flag must come back armed.
    _targen_pw(tab, "-A").set_user_enabled(False)
    tab._restore_user_preset(saved)
    assert _targen_pw(tab, "-A").is_enabled_by_user is True
    args = _extra_args(tab)
    assert "-A" in args
    assert float(args[args.index("-A") + 1]) == 0.5


def test_legacy_preset_without_enable_key_stays_off(qapp, settings):
    """A preset saved before this fix stored the value but no *_enabled key.
    Restoring it must not silently turn the flag on."""
    tab = _make_tab(qapp, settings)
    tab._restore_user_preset({"targen_-l": 300})  # no targen_-l_enabled
    assert _targen_pw(tab, "-l").is_enabled_by_user is False
    assert "-l" not in _extra_args(tab)
