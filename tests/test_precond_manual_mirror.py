"""'Use as pre-conditioning profile' also pre-fills the Manual module's
targen expert option (-c), not just the Guided checkbox+path (#44)."""
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


def test_apply_preconditioning_fills_manual_targen_c(qapp, tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    tab = TabChart(ArgyllRunner(s), FileManager(s), s)
    icc = tmp_path / "printer.icc"
    icc.write_bytes(b"\0" * 128)

    tab.apply_preconditioning(icc)

    # guided side (existing behaviour)
    assert tab._guided_precond_check.isChecked()
    assert tab._guided_precond_path.text() == str(icc)
    # manual side (new): targen expert -c mirrors the same profile
    assert tab._manual_targen_c_pw is not None
    assert tab._manual_targen_c_pw.get_value() == str(icc)
