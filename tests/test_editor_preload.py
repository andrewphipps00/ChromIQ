"""Layout editor pre-loads the Create Chart tab's current chart (#45)."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ui.dialogs.ti2_relayout_dialog as M  # noqa: E402
from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from workflow import ti2_relayout as R  # noqa: E402

_TI2 = """CTI2

ORIGINATOR "test"
TARGET_INSTRUMENT "GretagMacbeth i1 Pro"
COLOR_REP "iRGB"
PAPER_SIZE "210.0x297.0"
APPROX_WHITE_POINT "95.1 100.0 108.8"

NUMBER_OF_FIELDS 8
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 3
BEGIN_DATA
1 "A1" 100.0 100.0 100.0 95.1 100.0 108.8
2 "A2" 0.0 0.0 0.0 0.0 0.0 0.0
3 "A3" 100.0 0.0 0.0 41.2 21.3 1.9
END_DATA
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settings():
    s = AppSettings()
    s._qs = QSettings("chromiq-test", "editor-preload")
    s._qs.clear()
    return s


def _stage(tmp_path, opts):
    ti2 = tmp_path / "chart.ti2"
    ti2.write_text(_TI2)
    R.save_editor_meta(ti2, R.ChartSpec.from_ti2(ti2), opts, "chart")
    return ti2


def test_load_chart_from_restores_layout_options(qapp, monkeypatch, tmp_path):
    ed = M.Ti2RelayoutDialog(ArgyllRunner(_settings()), _settings())
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    ti2 = _stage(tmp_path, R.LayoutOptions(margin_mm=12, patch_scale=0.85,
                                           tiff_16bit=True))
    assert ed._load_chart_from(ti2) is True
    assert ed._spec is not None
    assert ed._options.margin_mm == 12
    assert abs(ed._options.patch_scale - 0.85) < 1e-9
    assert ed._options.tiff_16bit is True
    assert ed._basename == "chart"
    # A pre-loaded chart is clean — closing it without edits won't warn (#49).
    assert ed._is_dirty() is False


def test_initial_chart_preloads_on_open(qapp, monkeypatch, tmp_path):
    ti2 = _stage(tmp_path, R.LayoutOptions(margin_mm=9))
    ed = M.Ti2RelayoutDialog(ArgyllRunner(_settings()), _settings(),
                             initial_chart=ti2)
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    assert ed._spec is None              # deferred — not loaded during __init__
    qapp.processEvents()                 # fire the QTimer.singleShot(0)
    assert ed._spec is not None
    assert ed._options.margin_mm == 9


def test_no_initial_chart_opens_empty(qapp, monkeypatch):
    ed = M.Ti2RelayoutDialog(ArgyllRunner(_settings()), _settings())
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    qapp.processEvents()
    assert ed._spec is None
