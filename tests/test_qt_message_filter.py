"""The shutdown Qt-message filter drops only the benign WebEngine/Chromium
teardown lines, and only once shutdown has begun (core.qt_message_filter).

Guards the two ways it could regress: suppressing too early (hiding warnings
during a normal session) or suppressing too much (swallowing a genuine
warning at exit)."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import qInstallMessageHandler, qWarning  # noqa: E402

import core.qt_message_filter as qmf  # noqa: E402


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _emit(qapp, capsys, *, shutting: bool) -> str:
    qmf._shutting_down = False
    try:
        qmf.install_qt_message_filter(qapp)
        qmf._shutting_down = shutting
        qWarning("QDxgiVSyncService not destroyed in time")   # benign teardown
        qWarning("QThreadStorage: entry 1 destroyed before end of thread 0x1")
        qWarning("QThread: Destroyed while thread is still running")  # a real one
        qWarning("Some genuine application warning")
    finally:
        qInstallMessageHandler(None)          # restore Qt's default handler
        qmf._shutting_down = False
    return capsys.readouterr().err


def test_benign_passes_through_before_shutdown(qapp, capsys):
    err = _emit(qapp, capsys, shutting=False)
    # Nothing is suppressed until shutdown has begun.
    assert "QDxgiVSyncService not destroyed in time" in err
    assert "QThreadStorage: entry" in err
    assert "Some genuine application warning" in err


def test_benign_suppressed_only_at_shutdown(qapp, capsys):
    err = _emit(qapp, capsys, shutting=True)
    # The known-benign teardown lines are dropped …
    assert "QDxgiVSyncService" not in err
    assert "QThreadStorage: entry" not in err
    # … but a real thread-still-running warning and app warnings are NOT
    # (we match "QWaitCondition:", never the distinct "QThread: … still running").
    assert "QThread: Destroyed while thread is still running" in err
    assert "Some genuine application warning" in err
