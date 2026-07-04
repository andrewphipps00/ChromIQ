"""Drop the known-benign QtWebEngine/Chromium teardown warnings at quit.

On Windows, once the app takes its deliberate ``os._exit`` path
(``main._hard_exit`` — which avoids a SIP-finalize ``SIGBUS``; see
:mod:`core.webengine_shutdown`), Chromium's process-global threads are still
running, so Qt prints, to stderr::

    QWaitCondition: Destroyed while threads are still waiting
    QDxgiVSyncService not destroyed in time
    QThreadStorage: entry N destroyed before end of thread 0x...
    Release of profile requested but WebEnginePage still not deleted...

These are cosmetic: every bit of the app's own cleanup has already run inside
``MainWindow.closeEvent`` and the OS reclaims the process. They come from the
process-global Chromium GPU service, not from any individual view, so per-view
draining does **not** remove them (verified) — the only way to avoid them is to
abandon the crash-safe ``os._exit`` strategy, which is worse.

So we drop exactly these lines — and only after shutdown has begun — leaving
every other Qt message (including a genuine ``QThread: Destroyed while thread is
still running``, which we do *not* match) untouched.
"""
from __future__ import annotations

import sys

_shutting_down = False

# Substrings identifying the benign teardown lines (the numbers/addresses vary).
_BENIGN = (
    "QWaitCondition: Destroyed while threads are still waiting",
    "QDxgiVSyncService not destroyed in time",
    "QThreadStorage: entry",
    "Release of profile requested but WebEnginePage still not deleted",
)


def install_qt_message_filter(app) -> None:
    """Install a Qt message handler that suppresses the benign WebEngine
    teardown warnings once the app is quitting, and passes everything else
    straight through to stderr (Qt's default sink)."""
    from PyQt6.QtCore import qInstallMessageHandler

    def _handler(_mode, _ctx, message: str) -> None:
        if _shutting_down and any(s in message for s in _BENIGN):
            return
        try:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    qInstallMessageHandler(_handler)

    def _mark_shutdown() -> None:
        global _shutting_down
        _shutting_down = True

    app.aboutToQuit.connect(_mark_shutdown)
