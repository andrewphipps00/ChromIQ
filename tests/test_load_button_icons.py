"""Load buttons on the Measure / Build Profile tabs are icon-only glyph
buttons (Sebastian), matching the Create Chart / Print load buttons — no text,
a friendly tooltip, painted in the tab's accent colour."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPixmap, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _paints_without_error(btn) -> bool:
    pm = QPixmap(btn.size())
    pm.fill()
    p = QPainter(pm)
    try:
        btn.render(p)
    finally:
        p.end()
    return True


def test_measured_chart_button_paints(_app):
    from ui.widgets import MeasuredChartButton
    btn = MeasuredChartButton("#37bcd6")
    assert btn.text() == ""                       # icon-only
    assert _paints_without_error(btn)
    btn._hover = True                              # hover branch also paints
    assert _paints_without_error(btn)


class _Settings:
    def __init__(self, **kw):
        self._d = dict(kw)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v


def test_measure_load_button_is_icon_only_strip(_app):
    from core.argyll_runner import ArgyllRunner
    from ui.tabs.tab_measure import TabMeasure
    from ui.widgets import StripReadButton
    tab = TabMeasure(ArgyllRunner(_Settings()), _Settings())
    assert isinstance(tab._load_ti1_btn, StripReadButton)   # strip+arrow glyph
    assert tab._load_ti1_btn.text() == ""         # no label, just the glyph
    assert tab._load_ti1_btn.toolTip()            # but a helpful tooltip


def test_stacked_pages_button_paints(_app):
    from ui.widgets import StackedPagesButton
    btn = StackedPagesButton("#e0447b")
    assert btn.text() == ""                       # icon-only
    assert _paints_without_error(btn)
    btn._hover = True                              # hover branch (opaque knockout)
    assert _paints_without_error(btn)


def test_create_chart_load_profile_is_stacked_pages(_app):
    from core.settings import AppSettings
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from ui.tabs.tab_chart import TabChart
    from ui.widgets import StackedPagesButton
    s = AppSettings()
    tab = TabChart(ArgyllRunner(s), FileManager(s), s)
    assert isinstance(tab._load_profile_btn, StackedPagesButton)
    assert tab._load_profile_btn.text() == ""     # icon-only
    assert tab._load_profile_btn.toolTip()        # friendly tooltip


def test_build_load_buttons_are_icon_only_measured_glyph(_app):
    from core.argyll_runner import ArgyllRunner
    from ui.tabs.tab_profile import TabProfile
    from ui.widgets import MeasuredChartButton
    tab = TabProfile(ArgyllRunner(_Settings()), _Settings())
    for btn in (tab._load_btn, tab._pc_load_btn):
        assert isinstance(btn, MeasuredChartButton)
        assert btn.text() == ""
        assert btn.toolTip()
