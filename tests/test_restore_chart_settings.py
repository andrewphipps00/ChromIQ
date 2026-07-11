"""Loading a chart restores the settings it was made with (#mavtop, forum):
an engine chart's ``channels.json`` carries its full layout recipe, so the
Create-Chart panels can show the chart's own patch size, spacers, margins,
seed, notes and patch count instead of stale defaults."""
import json

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


def _fake_chart(tmp_path, with_recipe=True):
    ti2 = tmp_path / "c.ti2"
    ti2.write_text("CTI2\nNUMBER_OF_SETS 546\nBEGIN_DATA\nEND_DATA\n")
    if with_recipe:
        recipe = {
            "instrument": "CM", "paper": "A3", "dpi": 360,
            "randomize": True, "seed": 142,
            "use_instrument_margins": False,
            "spacer_on": True, "spacer_mode": "bw",
            "margin_top": 12.0, "margin_right": 11.0,
            "margin_bottom": 10.0, "margin_left": 9.0,
            "patch_w_mm": 8.3, "patch_h_mm": 9.5,
            "chart_text": "Epson XP-15000 / Ferrania Optijet",
        }
        patches = [{"loc": "A1", "page": 0, "x": 0, "y": 0, "w": 10, "h": 10},
                   {"loc": "A2", "page": 1, "x": 0, "y": 0, "w": 10, "h": 10}]
        (tmp_path / "c.channels.json").write_text(json.dumps(
            {"layout": {"engine": "chromiq", "recipe": recipe,
                        "patches": patches}}))
    return ti2


def _targen_count(tab):
    for pw in tab._manual_widgets.get("targen", []):
        if pw.flag == "-f":
            return int(pw.get_raw_value())
    return None


def test_engine_chart_restores_everything(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._manual_btn.setChecked(True)
    ti2 = _fake_chart(tmp_path, with_recipe=True)
    assert tab._restore_chart_settings(ti2) is True
    # Engine came on and the panel took the chart's recipe.
    assert tab._manual_engine_check.isChecked()
    p = tab._manual_layout_panel
    r = tab._current_layout_recipe()
    assert r.instrument == "CM" and r.paper == "A3"
    assert r.seed == 142 and r.randomize is True
    assert (r.margin_top, r.margin_right, r.margin_bottom, r.margin_left) \
        == (12.0, 11.0, 10.0, 9.0)
    assert abs(r.patch_w_mm - 8.3) < 1e-9 and abs(r.patch_h_mm - 9.5) < 1e-9
    # Notes, pages and the pinned patch count follow the chart.
    assert tab._manual_chart_notes_edit.text() == \
        "Epson XP-15000 / Ferrania Optijet"
    assert tab._manual_pages_spin.value() == 2
    assert _targen_count(tab) == 546
    assert not tab._manual_auto_patches_check.isChecked()
    tab.deleteLater()


def test_printtarg_chart_restores_count_only(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._manual_btn.setChecked(True)
    engine_before = tab._manual_engine_check.isChecked()
    ti2 = _fake_chart(tmp_path, with_recipe=False)
    assert tab._restore_chart_settings(ti2) is False   # no recipe to restore
    assert tab._manual_engine_check.isChecked() == engine_before
    assert _targen_count(tab) == 546                   # count still recovered
    tab.deleteLater()
