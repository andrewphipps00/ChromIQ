"""Save Preset dialog defaults (#50): suggest target name, default options on."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def tab(qapp, tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    t = TabChart(ArgyllRunner(s), FileManager(s), s)
    t._switch_mode("manual")
    return t


def test_new_preset_suggests_target_name_and_defaults_on(tab):
    tab._manual_target_name_edit.setText("Canon_Pro1000_Baryta")
    name, run, attach, from_target = tab._preset_save_prefill()
    assert name == "Canon_Pro1000_Baryta"
    assert run is True            # "Generate immediately" on by default
    assert attach is True         # "Build from loaded set" on by default
    assert from_target is True


def test_new_preset_no_target_name_is_blank(tab):
    tab._manual_target_name_edit.setText("")
    name, run, attach, from_target = tab._preset_save_prefill()
    assert name == ""
    assert from_target is False
    # The "do it now" options still default on for a fresh preset.
    assert run is True and attach is True


def test_target_name_wins_over_a_stale_selected_preset(tab):
    """Knut's #50 follow-up: a preset is still selected in the combo, but the
    user has since named a different target. The target name must be the basis,
    not the selected preset's name."""
    presets = {"OldPreset": {"auto_run": False, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name="OldPreset")
    tab._manual_target_name_edit.setText("test")
    name, run, attach, from_target = tab._preset_save_prefill()
    assert name == "test"          # target name, not "OldPreset"
    assert from_target is True
    assert run is True and attach is True   # new name → fresh-preset defaults


def test_overwriting_matching_preset_reuses_its_options(tab):
    """When the target name equals an existing preset (you're overwriting it),
    that preset's stored options are the defaults."""
    presets = {"Foo": {"auto_run": False, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name="Foo")
    tab._manual_target_name_edit.setText("Foo")
    name, run, attach, _ = tab._preset_save_prefill()
    assert name == "Foo"
    assert run is False and attach is False   # reused Foo's stored options


def test_falls_back_to_selected_preset_when_no_target(tab):
    presets = {"Bar": {"auto_run": True, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name="Bar")
    tab._manual_target_name_edit.setText("")
    name, run, attach, from_target = tab._preset_save_prefill()
    assert name == "Bar"           # fallback to the selected preset's name
    assert from_target is False
    assert run is True             # reused Bar's stored options
