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
