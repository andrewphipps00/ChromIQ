"""Manual tab: the ChromIQ engine panel's instrument/paper stays in step with
the canonical Manual selection (printtarg -i/-p), so loading a preset then
enabling the engine carries Instrument/Paper across and the threshold lookup
uses the right combo (#93, Knut beta-13 cluster 1)."""
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


def _tab(tmp_path, **prefs):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    for k, v in prefs.items():
        s.set(k, v)
    return TabChart(ArgyllRunner(s), FileManager(s), s)


def _set_printtarg(tab, flag, value):
    for pw in tab._manual_widgets.get("printtarg", []):
        if pw.flag == flag:
            pw.set_value(value)
            return True
    return False


def test_engine_panel_follows_manual_selection(qapp, tmp_path):
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    tab._manual_btn.setChecked(True)
    # Simulate a loaded preset's instrument/paper landing on the printtarg side.
    assert _set_printtarg(tab, "-i", "CM")
    assert _set_printtarg(tab, "-p", "A3")
    # Forward sync (what the off→on engine transition runs) pulls them across.
    tab._sync_engine_panel_selection()
    p = tab._manual_layout_panel
    assert p.instr.currentData() == "CM"
    assert p.paper.currentData() == "A3"
    # …and the threshold lookup now resolves the CURRENT combo, not a stale one.
    assert tab.current_layout_combo()[:2] == ("CM", "A3")


def test_panel_change_mirrors_back_to_printtarg(qapp, tmp_path):
    tab = _tab(tmp_path, use_chromiq_layout_engine=True)
    tab._manual_btn.setChecked(True)
    p = tab._manual_layout_panel
    p.instr.setCurrentIndex(p.instr.findData("SS"))   # fires the reverse mirror
    p.paper.setCurrentIndex(p.paper.findData("A4"))
    flag = {pw.flag: pw.get_raw_value() for pw in tab._manual_widgets.get("printtarg", [])}
    assert flag.get("-i") == "SS"
    assert flag.get("-p") == "A4"
