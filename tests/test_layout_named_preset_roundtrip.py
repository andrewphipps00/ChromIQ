"""A named Create-Chart preset carries the ChromIQ layout-engine recipe (#93).

Engine options set in the Manual module must save into / restore from the named
presets just like the printtarg options do, so the saved layout isn't lost.
"""
import tempfile
from dataclasses import replace

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402
from workflow.layout_engine.presets import LayoutRecipe  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _tab(qapp):
    s = AppSettings()
    s._qs = QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat)
    t = TabChart(ArgyllRunner(s), FileManager(s), s)
    t._switch_mode("manual")
    return t


def test_named_preset_restores_engine_recipe(qapp):
    t = _tab(qapp)
    rec = LayoutRecipe(
        instrument="i1", paper="A4", clip_border=True, pscale=0.9,
        indicator_font="Inter", indicator_bold=True, underline_mode="cycle",
        underline_thickness_mm=0.8, chart_text="{project}",
        clip_content_mode="text", clip_text="ID", clip_text_font="Inter",
        clip_border_width_mm=30.0, bit16=True, compression="zlib")
    data = {"layout_recipe": replace(rec, seed=None).to_dict(),
            "pages": 1, "auto_patches": False}
    t._restore_user_preset(data)
    out = t._manual_layout_panel.get_recipe()
    for f in ("pscale", "indicator_font", "indicator_bold", "underline_mode",
              "underline_thickness_mm", "chart_text", "clip_content_mode",
              "clip_text", "clip_border_width_mm", "bit16", "compression"):
        assert getattr(out, f) == getattr(rec, f), f
