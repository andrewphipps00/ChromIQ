"""ChromIQ — Printer Profiling GUI.  Entry point."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# Windows ARM: GPU is blocklisted for WebGL but the software compositor works fine.
# --ignore-gpu-blocklist re-enables WebGL; --disable-gpu-compositing keeps the
# compositor on the software path so the blocklist bypass doesn't break rendering.
if sys.platform == "win32":
    _existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    _extra = "--ignore-gpu-blocklist --disable-gpu-compositing"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{_existing} {_extra}".strip()

# QtWebEngine must be imported before QApplication is instantiated.
# Wrapped in try/except so the app still starts if the package is absent.
try:
    import PyQt6.QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass

from PyQt6.QtGui import QFontDatabase
from core.logger import configure_logging, get_logger
from core.resource_path import resource_path
from core.settings import AppSettings
from ui.main_window import MainWindow
from ui.styles import APP_STYLESHEET, WinButtonLayoutStyle, make_dark_palette
from ui.widgets import ButtonFontFilter


def main() -> int:
    configure_logging()
    log = get_logger("chromiq")
    log.info("ChromIQ starting")

    app = QApplication(sys.argv)
    app.setApplicationName("ChromIQ")
    app.setOrganizationName("ChromIQ")
    app.setApplicationDisplayName("ChromIQ — Printer Profiling")

    try:
        for font_path in resource_path("assets/fonts").glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_path))
    except Exception:
        pass  # fonts dir missing — app falls back to system fonts

    app.setStyle(WinButtonLayoutStyle("Fusion"))
    app.setPalette(make_dark_palette())
    app.setStyleSheet(APP_STYLESHEET)

    _btn_font_filter = ButtonFontFilter(app)
    app.installEventFilter(_btn_font_filter)

    settings = AppSettings()

    icon_path = resource_path("assets/app_icon.png")
    log.debug("App icon: %s  exists=%s", icon_path, icon_path.exists())
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    win = MainWindow(settings)
    win.show()

    log.info("Event loop starting")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
