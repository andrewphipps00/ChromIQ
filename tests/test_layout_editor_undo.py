"""Undo/redo history in the layout editor.

The capture is debounced through a QTimer; tests drive _capture_undo() directly
(what the timer fires) so they don't need a spinning event loop. _regenerate is
stubbed out so no Argyll process is launched on restore.
"""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ui.dialogs.ti2_relayout_dialog as M  # noqa: E402
from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from workflow import ti2_relayout as R  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _editor(monkeypatch):
    s = AppSettings()
    s._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      "chromiq-test", "undo")
    s._qs.clear()
    ed = M.Ti2RelayoutDialog(ArgyllRunner(s), s)
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    return ed


def _program(ed):
    return ed._program_from_grid()


def test_no_chart_has_no_history(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    assert ed._undo_btn.isEnabled() is False
    assert ed._redo_btn.isEnabled() is False


def test_set_chart_baselines_history(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"),
                  [(0.0, 0.0, 0.0), (100.0, 100.0, 100.0)],
                  "Loaded", is_saved=True)
    # One baseline snapshot, nothing to undo/redo yet.
    assert ed._undo_index == 0
    assert len(ed._undo_stack) == 1
    assert ed._undo_btn.isEnabled() is False
    assert ed._redo_btn.isEnabled() is False


def test_added_patches_undo_brings_them_back_out(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"),
                  [(0.0, 0.0, 0.0)], "Loaded", is_saved=True)
    # Add two patches, then commit the step.
    ed._grid.addItem(ed._grid_item((50.0, 50.0, 50.0)))
    ed._grid.addItem(ed._grid_item((25.0, 25.0, 25.0)))
    ed._capture_undo()
    assert len(_program(ed)) == 3
    assert ed._undo_btn.isEnabled() is True

    ed._undo()
    assert len(_program(ed)) == 1          # the additions are gone
    assert _program(ed) == [(0.0, 0.0, 0.0)]
    assert ed._redo_btn.isEnabled() is True

    ed._redo()
    assert len(_program(ed)) == 3          # and come back on redo


def test_deleted_patches_undo_restores_them(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    original = [(0.0, 0.0, 0.0), (50.0, 50.0, 50.0), (100.0, 100.0, 100.0)]
    ed._set_chart(R.ChartSpec.new("i1", "A4"), list(original), "Loaded",
                  is_saved=True)
    # Remove the middle patch, commit.
    ed._grid.takeItem(1)
    ed._capture_undo()
    assert _program(ed) == [(0.0, 0.0, 0.0), (100.0, 100.0, 100.0)]

    ed._undo()
    assert _program(ed) == original        # deleted patch recovered, in place


def test_layout_knob_change_is_undoable(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "Loaded",
                  is_saved=True)
    ed._options = R.LayoutOptions(margin_mm=12)
    ed._capture_undo()
    assert ed._options.margin_mm == 12
    ed._undo()
    assert ed._options.margin_mm != 12     # back to the default margin


def test_new_edit_discards_redo_branch(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "Loaded",
                  is_saved=True)
    ed._grid.addItem(ed._grid_item((50.0, 50.0, 50.0)))
    ed._capture_undo()
    ed._undo()                              # redo now available
    assert ed._redo_btn.isEnabled() is True
    # A fresh edit after an undo throws the redo branch away.
    ed._grid.addItem(ed._grid_item((10.0, 10.0, 10.0)))
    ed._capture_undo()
    assert ed._redo_btn.isEnabled() is False


def test_history_is_capped(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "Loaded",
                  is_saved=True)
    for i in range(M._UNDO_DEPTH + 10):     # more steps than the cap
        ed._grid.addItem(ed._grid_item((float(i % 100), 0.0, 0.0)))
        ed._capture_undo()
    # Baseline + _UNDO_DEPTH steps, never more.
    assert len(ed._undo_stack) == M._UNDO_DEPTH + 1


def test_identical_state_is_not_recaptured(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "Loaded",
                  is_saved=True)
    before = len(ed._undo_stack)
    ed._capture_undo()                      # nothing changed
    ed._capture_undo()
    assert len(ed._undo_stack) == before


def test_load_resets_history_across_charts(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "First",
                  is_saved=True)
    ed._grid.addItem(ed._grid_item((50.0, 50.0, 50.0)))
    ed._capture_undo()
    assert ed._undo_btn.isEnabled() is True
    # Loading a different chart wipes the previous chart's history.
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(10.0, 10.0, 10.0)], "Second",
                  is_saved=True)
    assert ed._undo_index == 0
    assert len(ed._undo_stack) == 1
    assert ed._undo_btn.isEnabled() is False


def test_close_clears_history(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "Loaded",
                  is_saved=True)
    ed._grid.addItem(ed._grid_item((50.0, 50.0, 50.0)))
    ed._capture_undo()
    assert ed._undo_stack
    monkeypatch.setattr(ed, "_confirm_discard", lambda: True)
    ev = QCloseEvent()
    ev.accept()
    ed.closeEvent(ev)
    assert ed._undo_stack == []
    assert ed._undo_index == -1
