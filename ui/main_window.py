"""Main application window."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.argyll_runner import ArgyllRunner
from core.file_manager import FileManager
from core.logger import get_logger
from core.resource_path import resource_path
from core.settings import AppSettings
from ui.dialogs.settings_dialog import SettingsDialog
from ui.styles import APP_STYLESHEET, make_dark_palette
from ui.tabs.tab_chart import TabChart
from ui.tabs.tab_measure import TabMeasure
from ui.tabs.tab_print import TabPrint
from ui.tabs.tab_profile import TabProfile

log = get_logger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings  = settings
        self._runner    = ArgyllRunner(settings, self)
        self._file_mgr  = FileManager(settings)

        self.setWindowTitle("ChromIQ — Printer Profiling")
        self.setMinimumSize(1200, 850)
        self.resize(1200, 850)

        # Central widget
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header = self._make_header()
        main_layout.addWidget(header)

        # Tabs
        self._tabs = QTabWidget(central)
        self._tabs.setDocumentMode(True)

        self._tab_chart   = TabChart(self._runner, self._file_mgr, self._settings, self)
        self._tab_print   = TabPrint(self._settings, self)
        self._tab_measure = TabMeasure(self._runner, self._settings, self)
        self._tab_profile = TabProfile(self._runner, self._settings, self)

        self._tabs.addTab(self._tab_chart,   "1. Create Chart")
        self._tabs.addTab(self._tab_print,   "2. Print Chart")
        self._tabs.addTab(self._tab_measure, "3. Measure")
        self._tabs.addTab(self._tab_profile, "4. Build Profile")

        self._tab_chart.chart_finished.connect(self._on_chart_generated)

        main_layout.addWidget(self._tabs, stretch=1)

        # Restore geometry
        geom = self._settings.get("window_geometry")
        if geom:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass

        active = int(self._settings.get("active_tab", 0))
        self._tabs.setCurrentIndex(active)

        self._check_argyll_binaries()
        log.info("MainWindow initialised")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _make_header(self) -> QWidget:
        header = QWidget(self)
        header.setFixedHeight(80)
        header.setStyleSheet("background: #161616;")

        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(0)

        # Banner image
        banner = _HeaderBanner(header)
        banner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(banner, stretch=1)

        # Settings button
        settings_btn = self._make_settings_btn(header)
        layout.addWidget(settings_btn)

        return header

    def _make_settings_btn(self, parent: QWidget) -> QToolButton:
        btn = QToolButton(parent)
        btn.setFixedSize(QSize(54, 54))
        btn.setObjectName("tooltip_btn")
        btn.setToolTip("Preferences")

        px = QPixmap(str(resource_path("assets/settings.PNG")))
        if not px.isNull():
            scaled = px.scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            btn.setIcon(QIcon(scaled))
            btn.setIconSize(QSize(32, 32))
        else:
            btn.setText("⚙")

        btn.clicked.connect(self._open_settings)
        return btn

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_chart_generated(self, tiffs: object, ti2: object) -> None:
        self._tab_print.load_tiffs(list(tiffs))
        if ti2 and Path(ti2).exists():
            self._tab_measure.set_ti1_path(Path(ti2))

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        dlg.exec()
        self._check_argyll_binaries()

    def _check_argyll_binaries(self) -> None:
        bin_dir = Path(self._settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        missing = [t for t in ("targen", "printtarg", "chartread", "colprof")
                   if not (bin_dir / t).exists()]
        if missing:
            log.warning("Missing ArgyllCMS binaries: %s", missing)
            self.statusBar().showMessage(
                f"⚠ ArgyllCMS binaries not found at {bin_dir}. "
                "Open Preferences to configure.", 0
            )
        else:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._settings.set("window_geometry", self.saveGeometry())
        self._settings.set("active_tab", self._tabs.currentIndex())
        super().closeEvent(event)


# -----------------------------------------------------------------------
# Header banner widget
# -----------------------------------------------------------------------

class _HeaderBanner(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._src = QPixmap(str(resource_path("assets/header.PNG")))
        if self._src.isNull():
            log.warning("header.PNG not found")
            self.setText("ChromIQ")
            self.setStyleSheet("color: white; font-size: 22px; font-weight: bold; padding: 8px;")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._src.isNull():
            dpr = self.devicePixelRatio()
            target_w = int(self.width() * dpr * 0.9)
            target_h = int(self.height() * dpr)
            scaled = self._src.scaledToWidth(
                target_w,
                Qt.TransformationMode.SmoothTransformation,
            )
            # Crop to header height, taking the vertical center of the image
            if scaled.height() > target_h:
                y_off = (scaled.height() - target_h) // 2
                scaled = scaled.copy(0, y_off, scaled.width(), target_h)
            scaled.setDevicePixelRatio(dpr)
            self.setPixmap(scaled)
