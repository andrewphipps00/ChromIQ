"""Preset panel locks + override checkboxes (Create Chart → Manual).

Selecting a preset that supplies a fixed patch set (.ti1) or a fixed layout
(prebuilt-files) greys the matching parameter panel. An override checkbox above
each panel lets the user unlock it:

  • ti1 preset      → only targen greyed; one "Edit patch recipe" box.
  • prebuilt preset → both greyed; "Edit patch recipe" + "Edit page layout".

Unlocking + editing changes what "Generate Chart" does (fresh targen / re-lay
the bundled patches / copy verbatim). These tests pin the wiring without
shelling out to ArgyllCMS (generation is stubbed).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import (  # noqa: E402
    KNUT_PRESET_KEYS,
    PREBUILT_PRESETS,
    TabChart,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "chromiq_test.ini"), QSettings.Format.IniFormat)
    return s


def _make_tab(qapp, settings) -> TabChart:
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    return t


def _targen_enabled(tab) -> bool:
    return all(w.isEnabled() for w in tab._manual_targen_content)


def _printtarg_enabled(tab) -> bool:
    return all(w.isEnabled() for w in tab._manual_printtarg_content)


# ---------------------------------------------------------------------------
# Default state: nothing locked
# ---------------------------------------------------------------------------

def test_default_no_locks(qapp, settings):
    tab = _make_tab(qapp, settings)
    assert tab._override_targen_row.isHidden()
    assert tab._override_printtarg_row.isHidden()
    assert _targen_enabled(tab)
    assert _printtarg_enabled(tab)


# ---------------------------------------------------------------------------
# ti1 preset: targen greyed, printtarg editable
# ---------------------------------------------------------------------------

def test_knut_preset_locks_targen_only(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_generate_from_ti1", lambda *a, **k: None)
    key = next(iter(KNUT_PRESET_KEYS))
    tab._apply_knut_preset(key, "lock-test")

    assert tab._knut_active
    # targen panel greyed, printtarg stays editable
    assert not _targen_enabled(tab)
    assert _printtarg_enabled(tab)
    # only the targen override row is shown
    assert not tab._override_targen_row.isHidden()
    assert tab._override_printtarg_row.isHidden()
    assert not tab._override_targen_check.isChecked()


def test_knut_targen_override_unlocks(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_generate_from_ti1", lambda *a, **k: None)
    tab._apply_knut_preset(next(iter(KNUT_PRESET_KEYS)), "lock-test")

    tab._override_targen_check.setChecked(True)   # no dialog (programmatic)
    assert _targen_enabled(tab)
    tab._override_targen_check.setChecked(False)
    assert not _targen_enabled(tab)


# ---------------------------------------------------------------------------
# Prebuilt preset: both greyed, two override boxes
# ---------------------------------------------------------------------------

def test_prebuilt_preset_locks_both(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_create_prebuilt_target", lambda *a, **k: None)
    key = next(iter(PREBUILT_PRESETS))
    tab._apply_prebuilt_preset(key, "prebuilt-test")

    assert tab._prebuilt_active
    assert not _targen_enabled(tab)
    assert not _printtarg_enabled(tab)
    assert not tab._override_targen_row.isHidden()
    assert not tab._override_printtarg_row.isHidden()
    # baselines were snapshotted for the Generate-time decision
    assert tab._prebuilt_targen_sig is not None
    assert tab._prebuilt_printtarg_sig is not None


def test_prebuilt_overrides_unlock_independently(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_create_prebuilt_target", lambda *a, **k: None)
    tab._apply_prebuilt_preset(next(iter(PREBUILT_PRESETS)), "prebuilt-test")

    tab._override_printtarg_check.setChecked(True)
    assert _printtarg_enabled(tab)
    assert not _targen_enabled(tab)          # targen still locked

    tab._override_targen_check.setChecked(True)
    assert _targen_enabled(tab)


# ---------------------------------------------------------------------------
# Generate-time routing for a prebuilt preset
# ---------------------------------------------------------------------------

def _route_prebuilt(qapp, settings, monkeypatch, edit=None):
    """Apply a prebuilt preset, optionally edit a panel, return which path ran.

    Returns one of "copy", "relayout", "fresh"."""
    calls: list[str] = []
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_create_prebuilt_target",
                        lambda *a, **k: calls.append("copy"))
    monkeypatch.setattr(tab, "_generate_from_ti1",
                        lambda *a, **k: calls.append("relayout"))
    # The fresh-targen path falls through; abort it cleanly before it runs.
    monkeypatch.setattr(tab, "_handle_target_rename", lambda *a, **k: False)
    tab._apply_prebuilt_preset(next(iter(PREBUILT_PRESETS)), "route-test")
    calls.clear()   # the initial apply copies the bundle; only score the Generate
    if edit == "printtarg":
        tab._override_printtarg_check.setChecked(True)
        tab._set_manual_value("printtarg", "-m", 15)
    elif edit == "targen":
        tab._override_targen_check.setChecked(True)
        tab._set_manual_value("targen", "-f", 333)
    tab._on_generate()
    if not calls:
        return "fresh"
    return calls[-1]


def test_prebuilt_generate_copies_when_untouched(qapp, settings, monkeypatch):
    assert _route_prebuilt(qapp, settings, monkeypatch) == "copy"


def test_prebuilt_generate_relayout_on_printtarg_change(qapp, settings, monkeypatch):
    assert _route_prebuilt(qapp, settings, monkeypatch, edit="printtarg") == "relayout"


def test_prebuilt_generate_fresh_on_targen_change(qapp, settings, monkeypatch):
    assert _route_prebuilt(qapp, settings, monkeypatch, edit="targen") == "fresh"


# ---------------------------------------------------------------------------
# Leaving a preset clears the locks
# ---------------------------------------------------------------------------

def test_leaving_prebuilt_restores_panels(qapp, settings, monkeypatch):
    tab = _make_tab(qapp, settings)
    monkeypatch.setattr(tab, "_create_prebuilt_target", lambda *a, **k: None)
    tab._apply_prebuilt_preset(next(iter(PREBUILT_PRESETS)), "prebuilt-test")
    tab._override_printtarg_check.setChecked(True)

    tab._leave_prebuilt()
    assert not tab._prebuilt_active
    assert _targen_enabled(tab)
    assert _printtarg_enabled(tab)
    assert tab._override_targen_row.isHidden()
    assert tab._override_printtarg_row.isHidden()
    assert not tab._override_printtarg_check.isChecked()
