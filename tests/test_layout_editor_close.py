"""Close button + unsaved-change guard in the layout editor (#49)."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
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
    s._qs = QSettings("chromiq-test", "close-btn")
    s._qs.clear()
    ed = M.Ti2RelayoutDialog(ArgyllRunner(s), s)
    # _set_chart auto-renders an initial preview; stub it out (no Argyll).
    monkeypatch.setattr(ed, "_regenerate", lambda **k: None)
    return ed


def test_no_chart_is_clean(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    assert ed._is_dirty() is False
    # Nothing unsaved -> closing is allowed without a prompt.
    assert ed._confirm_discard() is True


def test_loaded_chart_starts_clean_then_edits_make_it_dirty(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"),
                  [(0.0, 0.0, 0.0), (100.0, 100.0, 100.0)],
                  "Loaded x", is_saved=True)
    assert ed._is_dirty() is False
    # An edit (a new patch) flips it dirty.
    ed._grid.addItem(ed._grid_item((50.0, 50.0, 50.0)))
    assert ed._is_dirty() is True
    # Recording a save clears it again.
    ed._mark_saved()
    assert ed._is_dirty() is False


def test_created_chart_is_dirty_immediately(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)], "New chart")
    # A freshly created (never-saved) chart counts as unsaved straight away.
    assert ed._is_dirty() is True


def test_layout_knob_change_makes_it_dirty(qapp, monkeypatch):
    ed = _editor(monkeypatch)
    ed._set_chart(R.ChartSpec.new("i1", "A4"), [(0.0, 0.0, 0.0)],
                  "Loaded x", is_saved=True)
    assert ed._is_dirty() is False
    ed._options = R.LayoutOptions(margin_mm=12)   # e.g. user changed the margin
    assert ed._is_dirty() is True


def test_close_button_just_closes(qapp, monkeypatch):
    """The Close button only calls close(); the unsaved-changes prompt lives in
    closeEvent so the button and the window X share a single prompt (Knut's #49
    follow-up: confirming in both asked twice)."""
    ed = _editor(monkeypatch)
    assert ed._close_btn.text()
    calls = []
    monkeypatch.setattr(ed, "_confirm_discard",
                        lambda: calls.append("confirm") or True)
    monkeypatch.setattr(ed, "close", lambda: calls.append("closed"))
    ed._on_close_clicked()
    assert calls == ["closed"]          # no direct confirm here


def test_close_event_confirms_exactly_once(qapp, monkeypatch):
    from PyQt6.QtGui import QCloseEvent
    ed = _editor(monkeypatch)
    calls = []
    # Cancel the close — closeEvent must ask exactly once and ignore the event.
    monkeypatch.setattr(ed, "_confirm_discard", lambda: calls.append(1) or False)
    ev = QCloseEvent()
    ev.accept()
    ed.closeEvent(ev)
    assert calls == [1]
    assert not ev.isAccepted()          # cancelled → window stays open
