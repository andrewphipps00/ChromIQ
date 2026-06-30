"""Guided → Manual settings transfer (#79).

When a chart is generated in Guided mode and the user opens Manual, the Manual
panel is seeded with the same recipe so they can edit the settings used.
"""
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
    return TabChart(ArgyllRunner(s), FileManager(s), s)


def _manual(tab, tool, flag):
    for pw in tab._manual_widgets.get(tool, []):
        if pw.flag == flag:
            return pw.get_raw_value()
    return None


def test_transfer_copies_instrument_paper_pages_and_density(tab):
    # Configure Guided mode.
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("CM"))
    tab._paper_combo.setCurrentIndex(tab._paper_combo.findData("A4"))
    tab._pages_spin.setValue(2)
    tab._dd_check.setChecked(True)
    tab._target_name_edit.setText("MyPrinter Glossy")

    tab._transfer_guided_to_manual()

    assert _manual(tab, "printtarg", "-i") == "CM"
    assert _manual(tab, "printtarg", "-p") == "A4"
    assert tab._manual_pages_spin.value() == 2
    assert bool(_manual(tab, "printtarg", "-h")) is True
    # Patch count mirrors Guided's Auto.
    assert tab._manual_auto_patches_check.isChecked() is True
    # Printer profile name carried over.
    assert tab._manual_target_name_edit.text().strip() == "MyPrinter Glossy"


def test_transfer_is_armed_only_after_guided_generate(tab):
    """The one-shot flag fires once on the guided→manual switch, then clears."""
    tab._switch_mode("guided")
    tab._guided_transfer_pending = True
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("i1"))
    tab._paper_combo.setCurrentIndex(tab._paper_combo.findData("A4"))

    tab._switch_mode("manual")
    assert tab._guided_transfer_pending is False
    assert _manual(tab, "printtarg", "-i") == "i1"


def test_no_transfer_without_arming(tab):
    """Switching modes without a fresh guided generate doesn't overwrite manual
    edits."""
    tab._switch_mode("manual")
    tab._set_manual_value("printtarg", "-p", "A3")
    tab._switch_mode("guided")
    tab._switch_mode("manual")          # flag not armed → no transfer
    assert _manual(tab, "printtarg", "-p") == "A3"


def test_switch_carries_changed_instrument_both_ways(tab):
    """Editing a shared setting in one tab and switching carries it to the other
    (Knut #9). Snapshot/diff means only what the user changed moves."""
    tab._switch_mode("guided")
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("CM"))
    tab._dd_check.setChecked(True)
    tab._switch_mode("manual")
    assert _manual(tab, "printtarg", "-i") == "CM"
    assert bool(_manual(tab, "printtarg", "-h")) is True
    # Now change it in Manual and switch back — Guided reflects it.
    tab._set_manual_value("printtarg", "-i", "p3")
    tab._switch_mode("guided")
    assert tab._instr_combo.currentData() == "p3"


def test_switch_does_not_clobber_unrepresentable_paper(tab):
    """A paper only Manual offers (A3 portrait, which Guided lacks) must survive a
    round-trip through Guided — it's never 'changed' there, so it can't clobber."""
    tab._switch_mode("manual")
    tab._set_manual_value("printtarg", "-p", "A3")
    tab._switch_mode("guided")          # Guided can't show A3 → keeps its own
    tab._switch_mode("manual")          # …so it can't overwrite Manual's A3
    assert _manual(tab, "printtarg", "-p") == "A3"


def test_engine_panel_follows_transfer(tab):
    """With the ChromIQ engine ON, a Manual chart is built from the layout panel,
    not the printtarg widgets — so a Guided→Manual transfer must also push the
    instrument/paper/pages into the panel, or the generated chart ignores them
    (Knut #9)."""
    tab._settings.set("use_chromiq_layout_engine", True)
    tab._switch_mode("guided")
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("CM"))
    tab._paper_combo.setCurrentIndex(tab._paper_combo.findData("A4R"))
    tab._pages_spin.setValue(2)
    tab._guided_transfer_pending = True
    tab._switch_mode("manual")
    panel = tab._manual_layout_panel
    assert panel.instr.currentData() == "CM"
    assert panel.paper.currentData() == "A4R"
    assert panel.pages.value() == 2
    r = panel.get_recipe()
    assert r.instrument == "CM" and r.paper == "A4R"


def test_engine_recipe_carries_clip_suppression_and_layout(tab):
    """Post-Generate transfer loads the FULL engine recipe Guided used (not just
    instrument/paper) — clip-border suppression, margins, scale, edge spacers —
    so Manual reproduces the chart instead of re-adding a clip border (Knut)."""
    tab._settings.set("use_chromiq_layout_engine", True)
    tab._switch_mode("guided")
    tab._instr_combo.setCurrentIndex(tab._instr_combo.findData("i1"))
    tab._paper_combo.setCurrentIndex(tab._paper_combo.findData("A4"))
    tab._lb_check.setChecked(True)            # disable left border → suppress clip
    tab._guided_transfer_pending = True
    tab._switch_mode("manual")
    r = tab._manual_layout_panel.get_recipe()
    assert r.clip_border is False             # suppressed, not re-added
    assert r.clip_content_mode == "off"
    assert r.edge_spacers is True             # guided brackets strips
    # keep the left border → clip comes back on
    tab._switch_mode("guided")
    tab._lb_check.setChecked(False)
    tab._guided_transfer_pending = True
    tab._switch_mode("manual")
    r2 = tab._manual_layout_panel.get_recipe()
    assert r2.clip_border is True
    assert r2.clip_content_mode == "notes"
