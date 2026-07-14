"""Editor grid + TI1 routing on a multi-ink chart (#72 Tier A).

Loads a CMYK .ti2 into the layout editor and checks the N-tuple plumbing that
Tier A wired up: grid swatches via the engine's display approximation, per-ink
tooltips, device tuples preserved through the grid, `_engine_grid_ti1` writing
the engine-format .ti1, and the RGB-only colour edits gated off. (The full
"non-RGB forces the engine panel" UX lands in Tier B.)
"""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ui.dialogs.ti2_relayout_dialog as M  # noqa: E402
from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from workflow.layout_engine import ti1_reader  # noqa: E402

_TI2_CMYK = """CTI2

ORIGINATOR "test"
TARGET_INSTRUMENT "GretagMacbeth i1 Pro"
COLOR_REP "CMYK"
PAPER_SIZE "210.0x297.0"
APPROX_WHITE_POINT "96.42 100.0 82.49"

NUMBER_OF_FIELDS 9
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC CMYK_C CMYK_M CMYK_Y CMYK_K XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 3
BEGIN_DATA
1 "A1" 0.0 0.0 0.0 0.0 96.4 100.0 82.5
2 "A2" 40.0 10.0 0.0 5.0 40.0 45.0 60.0
3 "A3" 0.0 0.0 0.0 100.0 1.0 1.0 1.0
END_DATA
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settings():
    s = AppSettings()
    s._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      "chromiq-test", "editor-nchannel")
    s._qs.clear()
    return s


@pytest.fixture
def editor_cmyk(qapp, monkeypatch, tmp_path):
    ed = M.Ti2RelayoutDialog(ArgyllRunner(_settings()), _settings())
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    ti2 = tmp_path / "chart.ti2"
    ti2.write_text(_TI2_CMYK)
    assert ed._load_chart_from(ti2) is not False
    yield ed
    ed.deleteLater()


def test_grid_holds_device_tuples_and_ink_tooltips(editor_cmyk):
    ed = editor_cmyk
    assert ed._grid.count() == 3
    prog = ed._program_from_grid()
    assert prog[1] == (40.0, 10.0, 0.0, 5.0)          # 4-tuples, not RGB
    tip = ed._grid.item(1).toolTip()
    assert "C 40" in tip and "M 10" in tip and "K 5" in tip
    # Paper-white swatch displays as white, K-only as black (display approx).
    assert ed._display_rgb((0.0, 0.0, 0.0, 0.0)) == pytest.approx((100, 100, 100))
    assert ed._display_rgb((0.0, 0.0, 0.0, 100.0)) == pytest.approx((0, 0, 0))


def test_engine_grid_ti1_writes_engine_format(editor_cmyk, tmp_path):
    out = editor_cmyk._engine_grid_ti1(tmp_path / "grid.ti1")
    tgt = ti1_reader.read_ti1(out)
    assert tgt.color_rep == "CMYK"
    assert tgt.n_channels == 4
    assert [dev for dev, _ in tgt.patches] == [
        (0.0, 0.0, 0.0, 0.0), (40.0, 10.0, 0.0, 5.0), (0.0, 0.0, 0.0, 100.0)]
    # XYZ preserved from the .ti2, not re-estimated.
    assert tgt.patches[1][1] == pytest.approx((40.0, 45.0, 60.0), abs=1e-3)


def test_rgb_colour_edits_are_gated(editor_cmyk):
    ed = editor_cmyk
    ed._grid.item(0).setSelected(True)
    before = ed._program_from_grid()
    ed._set_patch_colour()                             # must refuse, not corrupt
    ed._transform_selection(1.1)
    assert ed._program_from_grid() == before
    assert "RGB charts" in ed._status.text()


def test_write_colour_values_file_skips_non_rgb(editor_cmyk, tmp_path):
    p = tmp_path / "c-colours.txt"
    editor_cmyk._write_colour_values_file(p)           # no crash, no file
    assert not p.exists()
