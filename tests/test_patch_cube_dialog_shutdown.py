"""PatchCubeDialog drains its QWebEngineView on close (issue #38).

The 3D RGB-cube popup (Edit Color Chart → 3D distribution) embeds a
QWebEngineView. The dialog is parented, so when ``.exec()`` returns it is not
garbage-collected — its Chromium subtree stayed alive until the app quit and was
then torn down during ``_Py_Finalize``, where SIP followed a dangling pointer
and crashed (EXC_BAD_ACCESS). PatchCubeDialog now drains the view on close, the
same way GamutPanel and DriftPlotDialog already do.

The drain logic is exercised against a stub view: constructing a real
QWebEngineView headless is unreliable (it spawns a Chromium child process and
destabilises the rest of the suite), and ``_teardown_webengine`` only touches
``self._web_view`` plus the view's own methods, so the stub is faithful.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.dialogs.patch_cube_dialog import PatchCubeDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeSignal:
    def disconnect(self):
        pass


class _FakePage:
    def __init__(self):
        self.reparented = False
        self.deleted = False

    def setParent(self, parent):
        self.reparented = parent is None

    def deleteLater(self):
        self.deleted = True


class _FakeView:
    def __init__(self):
        self.loadFinished = _FakeSignal()
        self.stopped = False
        self.url = None
        self._page = _FakePage()
        self.reparented = False
        self.deleted = False

    def stop(self):
        self.stopped = True

    def setUrl(self, url):
        self.url = url

    def page(self):
        return self._page

    def setParent(self, parent):
        self.reparented = parent is None

    def deleteLater(self):
        self.deleted = True


class _Stub:
    """Bare object carrying only the attribute _teardown_webengine reads."""


def test_teardown_drains_and_clears_the_view(qapp):
    stub = _Stub()
    view = _FakeView()
    stub._web_view = view

    PatchCubeDialog._teardown_webengine(stub)

    assert stub._web_view is None                 # cleared so it can't run twice
    assert view.stopped                           # loading halted
    assert view.url is not None and "about:blank" in view.url.toString()
    assert view._page.reparented and view._page.deleted
    assert view.reparented and view.deleted


def test_teardown_is_idempotent_and_safe_without_a_view(qapp):
    stub = _Stub()
    stub._web_view = None
    # No WebEngine (placeholder label) — must be a no-op, not an error.
    PatchCubeDialog._teardown_webengine(stub)
    assert stub._web_view is None

    # A second call after the view is already cleared is harmless.
    stub._web_view = _FakeView()
    PatchCubeDialog._teardown_webengine(stub)
    PatchCubeDialog._teardown_webengine(stub)
    assert stub._web_view is None
