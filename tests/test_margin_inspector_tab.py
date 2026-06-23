"""Create Chart tab wiring for the margin inspector.

Regression: restoring the saved guide-line checkbox state at build time emits
``guides_toggled`` → ``_update_margin_inspector``; that must not run before
``_margin_tiffs`` is initialised (the AttributeError seen on first launch with
``margin_guides_show`` stored True).
"""
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


def _tab(tmp_path, **prefs):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    for k, v in prefs.items():
        s.set(k, v)
    return TabChart(ArgyllRunner(s), FileManager(s), s)


def test_tab_builds_with_guides_enabled(qapp, tmp_path):
    """Building the tab with the guide checkbox stored ON must not crash."""
    tab = _tab(tmp_path, margin_guides_show=True)
    assert tab._margin_tiffs == []
    assert tab._margin_panel.guides_enabled() is True


def test_toggling_guides_with_no_chart_is_safe(qapp, tmp_path):
    """Toggling the guide checkbox before any chart is generated is a no-op,
    not an AttributeError."""
    tab = _tab(tmp_path, margin_guides_show=False)
    tab._on_margin_guides_toggled(True)      # the crashing path
    tab._update_margin_inspector()
    assert tab._margin_panel is not None


def test_refresh_settings_before_generate_is_safe(qapp, tmp_path):
    """The post-Preferences refresh hook is safe with no chart loaded."""
    tab = _tab(tmp_path)
    tab.refresh_margin_inspector_settings()
    assert tab._margin_tiffs == []
