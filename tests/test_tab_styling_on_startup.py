"""Per-tab accent styling can be applied to a tab that is not current (issue #35).

The app restores the last-used tab (``active_tab``). A tab whose first show
happens while it was never the current tab used to receive its per-tab accent
stylesheet only lazily, on first navigation — on macOS the first style-polish of
the heavy Create Chart tree landed a beat late, leaving the preset combo, the
built-in-presets star and the Manual panel blank until the user switched away
and back. The fix factors that styling into ``_apply_tab_widget_styling`` and
runs it for *every* tab at construction.

These tests exercise ``_apply_tab_widget_styling`` directly against a stand-in
holding a real ``QTabWidget`` of plain pages. Building a whole ``MainWindow`` in
the shared test process is unreliable (its gamut panel pulls in QtWebEngine and
the lingering top-level window destabilises later tests), and the method only
touches ``self._tabs`` and ``self._title_bar_mode``, so the stand-in is faithful.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubWindow:
    """Minimal stand-in exposing only what _apply_tab_widget_styling reads."""

    def __init__(self, n_tabs: int, mode: str = "light") -> None:
        self._title_bar_mode = mode
        self._tabs = QTabWidget()
        for i in range(n_tabs):
            self._tabs.addTab(QWidget(), f"tab{i}")


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_styles_a_non_current_tab(qapp, mode):
    stub = _StubWindow(5, mode=mode)
    # Make tab 2 current, mirroring an app that restored to the Measure tab; the
    # Create Chart tab (index 0) is never the current tab.
    stub._tabs.setCurrentIndex(2)
    assert not stub._tabs.widget(0).styleSheet()  # unstyled to begin with

    MainWindow._apply_tab_widget_styling(stub, 0)

    qss = stub._tabs.widget(0).styleSheet()
    # The per-tab accent QSS (mode buttons + primary button) lands on a tab that
    # is not, and never was, the current one — the heart of the fix.
    assert "QPushButton#mode_btn" in qss
    assert "QPushButton#primary" in qss


def test_can_style_every_tab(qapp):
    stub = _StubWindow(5)
    for i in range(stub._tabs.count()):
        MainWindow._apply_tab_widget_styling(stub, i)
    for i in range(stub._tabs.count()):
        assert "QPushButton#primary" in stub._tabs.widget(i).styleSheet()
