import os, tempfile, traceback
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app=QApplication.instance() or QApplication([])
from core.settings import AppSettings
from core.argyll_runner import ArgyllRunner
from core.file_manager import FileManager
from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
from ui.tabs.tab_chart import TabChart
from workflow.layout_engine.presets import LayoutRecipe
try:
    home=Path(tempfile.mkdtemp())
    s=AppSettings(); s._qs=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(home)); s.set("use_chromiq_layout_engine", False)
    ed=Ti2RelayoutDialog(ArgyllRunner(s), s)
    run="/Users/Basti/ChromIQ/ChromIQ-Test-Chart/runs/run1"
    ed._engine_ti1=Path(run)/"ChromIQ-Test-Chart.ti1"
    ed._engine_recipe=LayoutRecipe.from_channels_json(Path(run)/"ChromIQ-Test-Chart.channels.json")
    ed._engine_panel.set_recipe(ed._engine_recipe); ed._refresh_engine_panel_visible()
    ed._engine_panel.underline_mode.setCurrentIndex(ed._engine_panel.underline_mode.findData("segments"))
    staging=Path(tempfile.mkdtemp())
    ed._write_chart_into(staging, "EditedChart")
    print("STAGED", sorted(p.name for p in staging.iterdir()))
    t=TabChart(ArgyllRunner(s), FileManager(s), s); t._switch_mode("manual")
    if t._manual_target_name_edit is not None: t._manual_target_name_edit.setText("EditedChart")
    ok=t.apply_external_chart(staging, "EditedChart")
    print("APPLIED", ok, "engine_setting", s.get("use_chromiq_layout_engine", False),
          "underline", t._manual_layout_panel.get_recipe().underline_mode,
          "grp_shown", not t._manual_layout_grp.isHidden())
except Exception:
    traceback.print_exc()
