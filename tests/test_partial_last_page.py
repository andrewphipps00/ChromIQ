"""#93 (Knut): the "last page not full" hint computation. The pure helper
returns the unused-slot count when a patch set leaves a notable gap on the last
page, else None — so it's testable without the modal."""
import pytest

pytest.importorskip("PyQt6")
import json
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from core.argyll_runner import ArgyllRunner
from core.file_manager import FileManager
from core.settings import AppSettings
from ui.tabs.tab_chart import TabChart
from workflow.layout_engine.presets import LayoutRecipe


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def tab(qapp, tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    return TabChart(ArgyllRunner(s), FileManager(s), s)


def _engine_chart(dir_: Path, n_patches: int) -> Path:
    """A minimal engine chart: <stem>.ti2 (with NUMBER_OF_SETS) + channels.json
    carrying a ChromIQ recipe (i1 / A4, fixed 8x10 patches)."""
    dir_.mkdir(parents=True, exist_ok=True)
    ti2 = dir_ / "chart.ti2"
    ti2.write_text(f'NUMBER_OF_SETS {n_patches}\n')
    rec = LayoutRecipe(instrument="i1", paper="A4", layout_mode="patch_first",
                       patch_w_mm=8.0, patch_h_mm=10.0, clip_border=True)
    (dir_ / "chart.channels.json").write_text(json.dumps(
        {"layout": {"engine": "chromiq", "recipe": rec.to_dict()}}))
    return ti2


def test_blank_reported_for_near_empty_overflow(tab, tmp_path):
    # 5 patches on an i1/A4 layout that holds hundreds → last page nearly empty.
    ti2 = _engine_chart(tmp_path / "few", 5)
    blank = tab._partial_last_page_blank(ti2)
    assert blank is not None and blank > 50


def test_no_warning_for_a_full_page(tab, tmp_path):
    # Fill exactly one page → no gap → None.
    from workflow.layout_engine import instruments, geometry, papers
    rec = LayoutRecipe(instrument="i1", paper="A4", layout_mode="patch_first",
                       patch_w_mm=8.0, patch_h_mm=10.0, clip_border=True)
    g = instruments.geom_from_build_kwargs(rec.build_kwargs())
    per = geometry.patches_per_sheet(g, *papers.dimensions_mm("A4"))
    ti2 = _engine_chart(tmp_path / "full", per)
    assert tab._partial_last_page_blank(ti2) is None


def test_none_for_printtarg_chart(tab, tmp_path):
    # No channels.json / not an engine chart → None (no hint).
    d = tmp_path / "pt"; d.mkdir()
    ti2 = d / "chart.ti2"; ti2.write_text("NUMBER_OF_SETS 100\n")
    assert tab._partial_last_page_blank(ti2) is None
