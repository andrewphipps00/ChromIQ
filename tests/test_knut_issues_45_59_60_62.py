"""Regression tests for Knut's follow-ups: #59 preset overwrite, #60 Add total,
#45 editor→Create-Chart settings transfer, #62 Save & apply suggested name."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit  # noqa: E402

import workflow.ti2_relayout as R  # noqa: E402
from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    return s


# --- #59: overwrite prompt fires for custom-vs-custom -----------------------

def test_save_over_custom_preset_asks_overwrite(qapp, settings, monkeypatch):
    from ui.tabs.tab_chart import TabChart
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    presets = {"Alpha": {"auto_run": False, "attached_ti1": False},
               "Beta":  {"auto_run": False, "attached_ti1": False}}
    t._save_presets_to_settings(presets)
    t._populate_preset_combo(presets, select_name="Alpha")

    asked = []
    monkeypatch.setattr(t, "_confirm_overwrite_preset",
                        lambda n: (asked.append(n), False)[1])

    def fake_exec(self):
        for le in self.findChildren(QLineEdit):
            le.setText("Beta")          # collide with the other custom preset
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    t._on_preset_save()
    assert asked == ["Beta"]            # custom-vs-custom collision is caught


def test_save_with_invisible_char_still_matches(qapp, settings, monkeypatch):
    # #59: a name pasted with a zero-width space looked identical but didn't
    # match, so no overwrite prompt fired and a duplicate was created. The name
    # is now normalised (control/format chars dropped) before comparing.
    from ui.tabs.tab_chart import TabChart, _clean_preset_name
    assert _clean_preset_name("MyChart​") == "MyChart"
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    t._save_presets_to_settings({"MyChart": {"auto_run": True, "attached_ti1": False}})
    asked = []
    monkeypatch.setattr(t, "_confirm_overwrite_preset",
                        lambda n: (asked.append(n), False)[1])

    def fake_exec(self):
        for le in self.findChildren(QLineEdit):
            le.setText("MyChart​")     # trailing zero-width space
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    t._on_preset_save()
    assert asked == ["MyChart"]


def test_save_under_new_name_does_not_ask(qapp, settings, monkeypatch):
    from ui.tabs.tab_chart import TabChart
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    t._save_presets_to_settings({"Alpha": {"auto_run": False, "attached_ti1": False}})
    asked = []
    monkeypatch.setattr(t, "_confirm_overwrite_preset",
                        lambda n: (asked.append(n), True)[1])

    def fake_exec(self):
        for le in self.findChildren(QLineEdit):
            le.setText("BrandNew")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    t._on_preset_save()
    assert asked == []                  # no collision → no prompt


# --- #60: Add-window total = existing + built program -----------------------

def test_add_total_is_additions_shown_always(qapp, settings):
    # Knut's #60 clarification: the Total is the additions the current set
    # selection produces (NOT the existing chart), shown even with generate off,
    # and it includes white/black + fill.
    from ui.dialogs.ti2_relayout_dialog import _AddPatchesDialog
    existing = [(float(i % 101), float((i * 7) % 101), float((i * 13) % 101))
                for i in range(460)]
    d = _AddPatchesDialog(settings=settings, existing_patches=existing)
    for cb in (d._gen_cube, d._gen_skin, d._gen_blues, d._gen_greens,
               d._gen_sunrises, d._gen_greys, d._gen_edges, d._gen_hs,
               d._gen_pastel, d._gen_image, d._gen_whiteblack, d._gen_fill):
        cb.setChecked(False)
    d._gen_cube.setChecked(True)
    d._gen_cube_n.setValue(5)

    def shown_total() -> int:
        d._update_gen_counts()
        d._do_push_live_preview()
        import re
        m = re.search(r"([\d,]+)", d._gen_total.text())
        return int(m.group(1).replace(",", ""))

    # Generate OFF → still shows the additions (not 0, not the existing 460).
    d._add_mode_single.setChecked(True)
    off = shown_total()
    assert off == len(d._build_generated_program())
    assert off > 0 and off != 460

    # Generate ON → the same additions (no existing patches folded in).
    d._add_mode_gen.setChecked(True)
    assert shown_total() == len(d._build_generated_program())

    # White/black + fill are included in the additions total.
    d._gen_whiteblack.setChecked(True)
    d._gen_fill.setChecked(True)
    d._gen_fill_to.setValue(900)
    assert shown_total() == len(d._build_generated_program())

    # The Add dialog also shows the resulting chart size (existing + additions).
    import re
    m = re.search(r"([\d,]+)", d._gen_after_total.text())
    after = int(m.group(1).replace(",", ""))
    assert after == 460 + len(d._build_generated_program())


# --- #45: editor TD chart keeps custom margin / patch scale -----------------

def test_td_chart_transfers_custom_margin_and_scale(qapp, settings):
    from ui.tabs.tab_chart import TabChart
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    # Triple density is ColorMunki-only; set the instrument so the row is live.
    t._set_manual_value("printtarg", "-i", "CM")
    opts = R.LayoutOptions(triple_density=True, margin_mm=6.0, patch_scale=1.06,
                           spacer_scale=1.0, double_density=False)
    t._seed_manual_printtarg_from_layout(opts)

    def val(flag):
        for pw in t._manual_widgets["printtarg"]:
            if pw.flag == flag:
                return pw.get_raw_value()
        return None
    assert t._manual_td_check.isChecked()          # TD preserved
    assert val("-m") == 6                           # not clobbered to TD's 5
    assert abs(float(val("-a")) - 1.06) < 0.001     # not clobbered to TD's 1.3


# --- #62: Save & apply suggested / default name -----------------------------

def test_suggested_name_from_settings(qapp, settings):
    from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
    d = Ti2RelayoutDialog(ArgyllRunner(settings), settings)
    spec = R.ChartSpec.new(instrument_flag="i1", paper_flag="A4")
    spec.paper_mm = (297.0, 210.0)                  # landscape
    d._set_chart(spec, [(50.0, 50.0, 50.0)] * 480, "New chart")
    assert d._suggest_chart_name() == "i1Pro-A4-480p-Landscape"
    # Placeholder basename → use the suggestion; a real target name wins.
    assert d._default_apply_name() == "i1Pro-A4-480p-Landscape"
    d._basename = "Canon_Pro300_Baryta"
    assert d._default_apply_name() == "Canon_Pro300_Baryta"
