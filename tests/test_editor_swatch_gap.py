"""Patch-set editor swatch gap controls (#93, Knut): independent H/V gap
spinboxes (enabled only with "show gap"), and a visible selection border so
selection shows even with numbers + gaps off."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

import ui.dialogs.ti2_relayout_dialog as M
from core.argyll_runner import ArgyllRunner
from core.settings import AppSettings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _editor():
    s = AppSettings()
    s._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      "chromiq-test", "gap")
    s._qs.clear()
    return M.Ti2RelayoutDialog(ArgyllRunner(s), s)


def test_gap_spinboxes_drive_independent_h_v(qapp):
    ed = _editor()
    ed._show_gap_check.setChecked(True)
    ed._gap_h_spin.setValue(10)
    ed._gap_v_spin.setValue(4)
    assert ed._delegate.h_gap == 10 and ed._delegate.v_gap == 4
    # the cell reserves exactly the swatch + each gap (grid spacing is 0)
    sz = ed._delegate.sizeHint(None, None)
    ed._delegate.show_label = False
    sz2 = ed._delegate.sizeHint(None, None)
    assert sz2.width() == ed._delegate.swatch_size + 10
    assert sz2.height() == ed._delegate.swatch_size + 4


def test_gap_off_zeroes_gap_and_disables_spinboxes(qapp):
    ed = _editor()
    ed._show_gap_check.setChecked(True)
    ed._gap_h_spin.setValue(8)
    ed._show_gap_check.setChecked(False)
    assert ed._delegate.h_gap == 0 and ed._delegate.v_gap == 0
    assert not ed._gap_h_spin.isEnabled() and not ed._gap_v_spin.isEnabled()
    ed._show_gap_check.setChecked(True)
    assert ed._gap_h_spin.isEnabled()
    assert ed._delegate.h_gap == 8           # restored from the spinbox


def test_show_numbers_toggles_label_row(qapp):
    ed = _editor()
    ed._set_show_numbers(True)
    with_lbl = ed._delegate.sizeHint(None, None).height()
    ed._set_show_numbers(False)
    assert ed._delegate.sizeHint(None, None).height() < with_lbl
