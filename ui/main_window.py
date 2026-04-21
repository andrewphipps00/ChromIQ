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

from core.argyll_detect import all_tools_present, find_argyll_bin_path
from core.argyll_runner import ArgyllRunner
from core.file_manager import FileManager
from core.logger import get_logger
from core.resource_path import resource_path
from core.settings import AppSettings
from ui.dialogs.settings_dialog import SettingsDialog
from ui.styles import APP_STYLESHEET, make_dark_palette
from ui.tabs.tab_chart import TabChart
from ui.tabs.tab_check_refine import TabCheckRefine
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
        self.setMinimumSize(1390, 970)
        self.resize(1390, 970)

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
        self._tab_check   = TabCheckRefine(self._runner, self._settings, self)

        self._tabs.addTab(self._tab_chart,   "1. Create Chart")
        self._tabs.addTab(self._tab_print,   "2. Print Chart")
        self._tabs.addTab(self._tab_measure, "3. Measure")
        self._tabs.addTab(self._tab_profile, "4. Build Profile")
        self._tabs.addTab(self._tab_check,   "5. Check && Refine")

        self._tab_chart.chart_finished.connect(self._on_chart_generated)
        self._tab_measure.measure_finished.connect(self._on_measure_done)
        self._tab_measure.proceed_to_profile.connect(self._on_proceed_to_profile)
        self._tab_profile.profile_built.connect(self._tab_check.set_paths)
        self._tab_check.guide_refinement_requested.connect(self._on_guide_refinement)

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

        self._check_argyll_binaries(initial=True)
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

    def _on_measure_done(self, ti3: Path) -> None:
        self._tab_profile.set_ti3_path(ti3)

    def _on_proceed_to_profile(self) -> None:
        self._tabs.setCurrentWidget(self._tab_profile)

    def _on_guide_refinement(self, ti3: Path, strips_file: Path) -> None:
        self._tabs.setCurrentWidget(self._tab_measure)
        self._tab_measure.start_guided_refinement(ti3, strips_file)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        dlg.exec()
        self._check_argyll_binaries()

    def _check_argyll_binaries(self, initial: bool = False) -> None:
        bin_dir = Path(self._settings.get("argyll_bin_path", "/Applications/Argyll/bin"))

        if all_tools_present(bin_dir):
            self.statusBar().clearMessage()
            return

        if initial:
            # Try to auto-detect a working installation
            detected = find_argyll_bin_path()
            if detected:
                self._settings.set("argyll_bin_path", str(detected))
                log.info("ArgyllCMS auto-configured to %s", detected)
                self.statusBar().clearMessage()
                return

        # Not found — show status bar warning and, on first launch, a popup
        log.warning("ArgyllCMS binaries not found at %s", bin_dir)
        self.statusBar().showMessage(
            "⚠ ArgyllCMS not found. Open Preferences (⚙) to set the path.", 0
        )
        if initial:
            self._show_argyll_not_found_dialog()

    def _show_argyll_not_found_dialog(self) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("ArgyllCMS Not Found")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>ArgyllCMS could not be found on your system.</b><br><br>"
            "ChromIQ requires ArgyllCMS to create and measure ICC profiles. "
            "It was not detected in any of the usual locations.<br><br>"
            "<b>To install ArgyllCMS:</b><br>"
            "&nbsp;&nbsp;1. Download ArgyllCMS from "
            "<a href='https://www.argyllcms.com'>argyllcms.com</a><br>"
            "&nbsp;&nbsp;2. Extract the archive and move the folder to "
            "<span style='font-family:monospace'>/Applications</span><br>"
            "&nbsp;&nbsp;3. Restart ChromIQ — it will detect the installation "
            "automatically.<br><br>"
            "If ArgyllCMS is already installed in a custom location, click "
            "<b>Open Preferences</b> to set the path manually.",
            dlg,
        )
        msg.setOpenExternalLinks(True)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_box = QDialogButtonBox()
        prefs_btn = btn_box.addButton("Open Preferences", QDialogButtonBox.ButtonRole.ActionRole)
        btn_box.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.accepted.connect(dlg.accept)
        prefs_btn.clicked.connect(dlg.accept)
        prefs_btn.clicked.connect(self._open_settings)
        layout.addWidget(btn_box)

        dlg.exec()

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
