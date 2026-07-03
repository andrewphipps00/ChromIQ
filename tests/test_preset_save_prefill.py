"""Save Preset dialog defaults. A preset names a CHART LAYOUT, so the suggested
name comes from the layout generator, not the printer-profile name (#70, Knut)."""
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


def test_preset_name_comes_from_layout_generator_not_profile_name(tab):
    # #70 (Knut): the profile name in the Output frame must NOT drive the preset
    # name — the chart-layout generator does.
    gen = tab._suggest_target_name()
    assert gen                                    # something like i1Pro-A4-1page-Portrait
    tab._manual_target_name_edit.setText("Canon_Pro1000_Baryta")   # profile name
    name, run, attach, from_gen = tab._preset_save_prefill()
    assert name == gen                            # the layout name, not the profile name
    assert run is True and attach is True         # fresh preset → both on
    assert from_gen is True


def test_changing_profile_name_does_not_change_preset_name(tab):
    gen = tab._suggest_target_name()
    tab._manual_target_name_edit.setText("anything-else")
    assert tab._preset_save_prefill()[0] == gen


def test_overwriting_matching_layout_preset_reuses_its_options(tab):
    # A preset named like the generated layout name → overwriting reuses options.
    gen = tab._suggest_target_name()
    presets = {gen: {"auto_run": False, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name=gen)
    name, run, attach, _ = tab._preset_save_prefill()
    assert name == gen
    assert run is False and attach is False       # reused the existing options


def test_falls_back_to_selected_preset_when_no_generator(tab, monkeypatch):
    # Edge: with no generator name, fall back to the selected user preset.
    monkeypatch.setattr(tab, "_suggest_target_name", lambda: "")
    presets = {"Bar": {"auto_run": True, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name="Bar")
    name, run, attach, from_gen = tab._preset_save_prefill()
    assert name == "Bar"
    assert from_gen is False
    assert run is True


def test_loading_printtarg_preset_selects_printtarg_engine(tab):
    """#93 (Knut): a Manual preset is printtarg-made (no layout_recipe), so
    selecting it must switch the layout engine OFF — otherwise the ChromIQ engine
    would render it and the preview is wrong."""
    tab._settings.set("use_chromiq_layout_engine", True)
    tab._manual_engine_check.setChecked(True)
    assert tab._manual_engine_check.isChecked()
    presets = {"MyChart-A4": {"auto_run": False, "attached_ti1": False}}
    tab._save_presets_to_settings(presets)
    tab._populate_preset_combo(presets, select_name="MyChart-A4")
    idx = tab._preset_combo.findData("MyChart-A4")
    assert idx > 0
    tab._on_preset_selected(idx)
    assert tab._manual_engine_check.isChecked() is False
    assert bool(tab._settings.get("use_chromiq_layout_engine")) is False
