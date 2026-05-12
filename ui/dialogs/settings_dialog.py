"""Settings / Preferences dialog."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.argyll_detect import find_argyll_bin_path
from core.logger import get_logger
from core.updater import UpdateChecker, _RELEASES_PAGE
from core.version import APP_VERSION
from ui.tooltip_button import TooltipButton
from ui.widgets import make_browse_button, open_dir_dialog

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

import sys as _sys
_ARGYLL_DOWNLOAD_PAGE = (
    "https://www.argyllcms.com/downloadwin.html"
    if _sys.platform == "win32"
    else "https://www.argyllcms.com/downloadmac.html"
)


class SettingsDialog(QDialog):
    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._update_checker: UpdateChecker | None = None
        self.setWindowTitle("ChromIQ Preferences")
        self.setMinimumWidth(840)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()
        self._load_settings()
        self.resize(840, self.sizeHint().height())

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ---- ArgyllCMS ----
        argyll_grp = QGroupBox("ArgyllCMS Binaries", self)
        ag = QVBoxLayout(argyll_grp)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Binary path:", self))
        self._argyll_edit = QLineEdit(self)
        path_row.addWidget(self._argyll_edit, stretch=1)
        browse_btn = make_browse_button(self, "Select ArgyllCMS bin folder", icon="folder")
        browse_btn.clicked.connect(self._browse_argyll)
        path_row.addWidget(browse_btn)
        path_row.addWidget(TooltipButton(
            "ArgyllCMS Binary Path",
            "Directory containing targen, printtarg, chartread, and colprof.\n"
            "Default: /Applications/Argyll/bin\n"
            "You can download the latest version from argyllcms.com.",
            self,
        ))
        ag.addLayout(path_row)

        btn_row = QHBoxLayout()
        test_btn = QPushButton("Test binaries", self)
        test_btn.clicked.connect(self._test_argyll)
        detect_btn = QPushButton("Auto-detect", self)
        detect_btn.clicked.connect(self._auto_detect)
        dl_btn = QPushButton("Download latest ArgyllCMS…", self)
        dl_btn.clicked.connect(self._open_argyll_download)
        btn_row.addWidget(test_btn)
        btn_row.addWidget(detect_btn)
        btn_row.addWidget(dl_btn)

        if _sys.platform == "win32":
            driver_btn = QPushButton("Install USB Driver…", self)
            driver_btn.setToolTip(
                "Install the WinUSB driver for your colorimeter — "
                "no test-signing mode required, works on x64 and ARM64"
            )
            driver_btn.clicked.connect(self._show_usb_installer)
            btn_row.addWidget(driver_btn)

        btn_row.addStretch()
        ag.addLayout(btn_row)

        self._argyll_status = QLabel("", self)
        self._argyll_status.setWordWrap(True)
        ag.addWidget(self._argyll_status)

        layout.addWidget(argyll_grp)

        # ---- Output folder ----
        folder_grp = QGroupBox("Output Folder", self)
        fl = QVBoxLayout(folder_grp)

        folder_lbl = QLabel(
            "Default output folder (leave blank to use ~/ChromIQ/):", self
        )
        folder_lbl.setWordWrap(True)
        fl.addWidget(folder_lbl)

        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit(self)
        self._folder_edit.setPlaceholderText("~/ChromIQ/  (default)")
        folder_row.addWidget(self._folder_edit, stretch=1)
        folder_browse = make_browse_button(self, "Select output folder", icon="folder")
        folder_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(folder_browse)
        fl.addLayout(folder_row)

        layout.addWidget(folder_grp)

        # ---- Behaviour ----
        behaviour_grp = QGroupBox("Behaviour", self)
        bh = QVBoxLayout(behaviour_grp)
        self._restore_tab_check = QCheckBox(
            "Restore last active tab on launch", self
        )
        bh.addWidget(self._restore_tab_check)

        self._restore_session_check = QCheckBox(
            "Restore last session on launch (reload previously loaded files)", self
        )
        bh.addWidget(self._restore_session_check)

        cal_row = QHBoxLayout()
        self._cal_mode_check = QCheckBox("Enable calibration options", self)
        cal_row.addWidget(self._cal_mode_check)
        cal_row.addStretch()
        cal_row.addWidget(TooltipButton(
            "Enable Calibration Options",
            "Unlocks the full printer calibration workflow (printcal / applycal).\n\n"
            "Most users do NOT need this — consumer and prosumer inkjet printers "
            "typically produce better results from a direct profiling run without "
            "any hardware calibration step.\n\n"
            "Enable this only if you know your printer requires linearisation curves "
            "before profiling, or if you are an advanced user following an explicit "
            "ArgyllCMS calibration guide.\n\n"
            "When active: the guided modes in all tabs are hidden, a calibration "
            "target option appears in Create Chart, and a full printcal → applycal "
            "workflow is added to the Calibration & Profiling tab.",
            self,
            min_width=620,
        ))
        bh.addLayout(cal_row)

        native_print_row = QHBoxLayout()
        self._native_print_check = QCheckBox("Use default macOS printer dialog", self)
        native_print_row.addWidget(self._native_print_check)
        native_print_row.addStretch()
        native_print_row.addWidget(TooltipButton(
            "Use default macOS printer dialog",
            "When enabled, clicking Print in the Print Chart tab opens the standard\n"
            "macOS print dialog instead of ChromIQ's built-in PostScript / CUPS pipeline.\n\n"
            "⚠  IMPORTANT: You MUST disable colour management manually in the\n"
            "printer driver panel every time you print — otherwise the printer applies\n"
            "its own colour corrections, which will corrupt the measurement chart\n"
            "and make your ICC profile inaccurate.\n\n"
            "How to disable colour management in the macOS print dialog:\n"
            "After clicking Print, open the dropdown in the middle of the dialog\n"
            "(it usually shows your printer's name or 'Color Matching') and look\n"
            "for a colour-management section:\n\n"
            "  • Epson:  'Epson Color Controls' → Off (No Color Adjustment)\n"
            "  • Canon:  'Color Options' → Manual → set to None\n"
            "  • HP:     'Color Options' → Application Managed Colors\n"
            "  • Others: look for 'No Color Management', 'Off', or\n"
            "            'Application Controlled'\n\n"
            "If you are unsure, leave this option disabled and use ChromIQ's\n"
            "default printing method instead — it disables colour management\n"
            "automatically with no extra steps required.",
            self,
            min_width=620,
        ))
        native_print_row.setContentsMargins(0, 0, 0, 0)
        native_print_container = QWidget(self)
        native_print_container.setLayout(native_print_row)
        native_print_container.setVisible(_sys.platform != "win32")
        bh.addWidget(native_print_container)

        layout.addWidget(behaviour_grp)

        # ---- About / Updates ----
        credit1 = QLabel(f"ChromIQ v{APP_VERSION} · Created by Sebastian Reiprich", self)
        credit1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit1.setStyleSheet("color: #606060; font-size: 11px;")
        layout.addWidget(credit1)

        credit2 = QLabel(
            "Built on ArgyllCMS by Graeme Gill · With thanks to Knut Georg Larsson", self
        )
        credit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit2.setStyleSheet("color: #606060; font-size: 11px;")
        layout.addWidget(credit2)

        self._update_status = QLabel("", self)
        self._update_status.setStyleSheet("font-size: 11px;")
        self._update_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status.setFixedHeight(QFontMetrics(self._update_status.font()).height())
        layout.addWidget(self._update_status)

        # ---- Bottom row: Restore Defaults | Check for Updates | Cancel / OK ----
        bottom_row = QHBoxLayout()
        reset_btn = QPushButton("Restore Factory Defaults", self)
        reset_btn.setStyleSheet(
            "QPushButton { background: #f4f4f4; color: #121212; border: 1px solid #d0d0d0; }"
            "QPushButton:hover { background: #e0e0e0; border-color: #bbbbbb; }"
        )
        reset_btn.clicked.connect(self._restore_defaults)
        bottom_row.addWidget(reset_btn)
        bottom_row.addStretch()

        self._update_btn = QPushButton("Check for Updates", self)
        self._update_btn.clicked.connect(self._check_for_updates)
        bottom_row.addWidget(self._update_btn)
        bottom_row.addSpacing(8)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        bb.accepted.connect(self._save_and_close)
        bb.rejected.connect(self.reject)
        bottom_row.addWidget(bb)
        layout.addLayout(bottom_row)

        self.setStyleSheet(
            "QLineEdit:focus { border-color: #f4f4f4; }"
            "QCheckBox::indicator:checked { background: #f4f4f4; border-color: #d0d0d0; }"
            "QCheckBox::indicator:hover { border-color: #f4f4f4; }"
        )

    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        s = self._settings
        self._argyll_edit.setText(s.get("argyll_bin_path", "/Applications/Argyll/bin"))
        self._folder_edit.setText(s.get("custom_output_path", ""))
        self._restore_tab_check.setChecked(s.get("restore_last_tab", True))
        self._restore_session_check.setChecked(bool(s.get("restore_last_session", False)))
        self._cal_mode_check.setChecked(bool(s.get("calibration_mode", False)))
        self._native_print_check.setChecked(bool(s.get("use_native_print_dialog", False)))

    def _save_and_close(self) -> None:
        s = self._settings
        s.set("argyll_bin_path",       self._argyll_edit.text().strip())
        s.set("custom_output_path",    self._folder_edit.text().strip())
        s.set("restore_last_tab",          self._restore_tab_check.isChecked())
        s.set("restore_last_session",      self._restore_session_check.isChecked())
        s.set("calibration_mode",          self._cal_mode_check.isChecked())
        s.set("use_native_print_dialog",   self._native_print_check.isChecked())
        log.info("Settings saved")
        self.accept()

    def _browse_argyll(self) -> None:
        d = open_dir_dialog(
            self, "Select ArgyllCMS bin directory",
            start_dir=self._argyll_edit.text() or "/Applications",
        )
        if d:
            self._argyll_edit.setText(d)

    def _browse_folder(self) -> None:
        d = open_dir_dialog(
            self, "Select output folder",
            start_dir=self._folder_edit.text() or str(Path.home()),
        )
        if d:
            self._folder_edit.setText(d)

    def _auto_detect(self) -> None:
        detected = find_argyll_bin_path()
        if detected:
            self._argyll_edit.setText(str(detected))
            self._argyll_status.setStyleSheet("color: #4caf50;")
            self._argyll_status.setText(f"Auto-detected at {detected}")
        else:
            self._argyll_status.setStyleSheet("color: #ff5252;")
            self._argyll_status.setText(
                "ArgyllCMS not found in any known location. "
                "Install it or set the path manually."
            )
        log.info("ArgyllCMS auto-detect: %s", detected)

    def _test_argyll(self) -> None:
        from core.resource_path import argyll_binary
        bin_dir = Path(self._argyll_edit.text().strip())
        results = []
        for tool in ("targen", "printtarg", "chartread", "colprof",
                     "profcheck", "printcal", "applycal", "iccgamut", "viewgam"):
            p = bin_dir / argyll_binary(tool)
            if tool == "chartread":
                # chartread probes USB hardware even with -?, causing a hang.
                # Existence + executable check is sufficient here.
                executable = p.exists() and (_sys.platform == "win32" or os.access(str(p), os.X_OK))
                if executable:
                    results.append(f"✓ {tool}")
                else:
                    results.append(f"✗ {tool} (not found)")
                continue
            if p.exists():
                try:
                    subprocess.run(
                        [str(p), "-?"], capture_output=True, timeout=5,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    results.append(f"✓ {tool}")
                except Exception:
                    results.append(f"✗ {tool} (error)")
            else:
                results.append(f"✗ {tool} (not found)")
        msg = "  ".join(results)
        all_ok = all(r.startswith("✓") for r in results)
        self._argyll_status.setStyleSheet(
            "color: #4caf50;" if all_ok else "color: #ff9800;"
        )
        self._argyll_status.setText(msg)
        log.info("ArgyllCMS test: %s", msg)

    def _open_argyll_download(self) -> None:
        self._argyll_status.setStyleSheet("")
        if _sys.platform == "win32":
            hint = "win64 for x64 (Intel/AMD) or arm64 for ARM-based devices (Snapdragon)"
        else:
            hint = "arm64 for Apple Silicon, osx64 for Intel"
        self._argyll_status.setText(
            f"Opening argyllcms.com — download the latest version ({hint}), "
            "then unpack and set the bin path above."
        )
        QDesktopServices.openUrl(QUrl(_ARGYLL_DOWNLOAD_PAGE))

    def _show_usb_installer(self) -> None:
        if _sys.platform != "win32":
            return
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
        from core.usb_driver_installer import enumerate_connected, install_winusb, launch_zadig
        from core.resource_path import resource_path as _rp
        from ui.widgets import tint_dialog_primary

        _wdi_available = _rp("assets/wdi_simple.exe").exists()

        _COLOR = "#56d6a5"
        _REFRESH = 2   # custom dlg.done() code, distinct from Accepted(1)/Rejected(0)

        while True:
            devices = enumerate_connected()
            needs_install = [d for d in devices if not d.has_winusb]

            dlg = QDialog(self)
            dlg.setWindowTitle("Install USB Driver")
            dlg.setMinimumWidth(500)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(14)
            layout.setContentsMargins(24, 20, 24, 20)

            if not devices:
                msg_text = (
                    "<b>No colorimeter detected.</b><br><br>"
                    "Make sure your device is plugged in via USB, "
                    "then click <b>Refresh</b>."
                )
            else:
                lines = [
                    f"&nbsp;&nbsp;• {d.name} — "
                    f"<i>{'WinUSB ✓' if d.has_winusb else 'driver not installed'}</i>"
                    for d in devices
                ]
                if _wdi_available:
                    action_text = (
                        "Click <b>Install Driver</b> to install the Microsoft WinUSB driver "
                        "automatically. A Windows security prompt will appear — click Yes to "
                        "continue.<br><br>"
                        "<i>No test-signing mode required. Works on x64 and ARM64.</i>"
                    )
                else:
                    action_text = (
                        "ChromIQ will open <b>Zadig</b>, a free USB driver tool. In Zadig:<br>"
                        "&nbsp;&nbsp;1. Click <b>Options → List All Devices</b><br>"
                        "&nbsp;&nbsp;2. Find your colorimeter in the dropdown<br>"
                        "&nbsp;&nbsp;3. Select <b>WinUSB</b> as the driver and click "
                        "<b>Install Driver</b>"
                    )
                msg_text = (
                    "<b>Connected colorimeter(s):</b><br>"
                    + "<br>".join(lines)
                    + "<br><br>"
                    + action_text
                )

            msg = QLabel(msg_text, dlg)
            msg.setWordWrap(True)
            layout.addWidget(msg)

            btn_box = QDialogButtonBox()
            if needs_install:
                btn_label = "Install Driver" if _wdi_available else "Open Zadig"
                install_btn = btn_box.addButton(btn_label, QDialogButtonBox.ButtonRole.AcceptRole)
                install_btn.setObjectName("primary")
            refresh_btn = btn_box.addButton("Refresh", QDialogButtonBox.ButtonRole.ResetRole)
            refresh_btn.clicked.connect(lambda checked=False, d=dlg: d.done(_REFRESH))
            btn_box.addButton(QDialogButtonBox.StandardButton.Close)
            btn_box.rejected.connect(dlg.reject)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _COLOR)

            result = dlg.exec()

            if result == _REFRESH:
                continue   # rebuild with fresh device list

            if result != QDialog.DialogCode.Accepted or not needs_install:
                break   # Close button or nothing to install

            # ---- run installation ----
            if _wdi_available:
                all_ok = all(install_winusb(d) for d in needs_install)
                if all_ok:
                    outcome_text = "WinUSB driver installed successfully."
                    offer_zadig = False
                else:
                    outcome_text = (
                        "Automatic installation failed or was cancelled.<br>"
                        "Click <b>Try Zadig</b> to install manually using the guided tool."
                    )
                    offer_zadig = True
            else:
                all_ok = launch_zadig()
                outcome_text = (
                    "Zadig is open. Select your colorimeter, choose WinUSB, "
                    "then click Install Driver."
                    if all_ok else
                    "Could not launch Zadig. Try running ChromIQ as Administrator."
                )
                offer_zadig = False

            outcome_dlg = QDialog(self)
            outcome_dlg.setWindowTitle("Driver Installation")
            outcome_dlg.setMinimumWidth(420)
            ol = QVBoxLayout(outcome_dlg)
            ol.setContentsMargins(24, 20, 24, 20)
            ol.setSpacing(14)
            lbl = QLabel(outcome_text, outcome_dlg)
            lbl.setWordWrap(True)
            ol.addWidget(lbl)
            obox = QDialogButtonBox()
            if offer_zadig:
                zadig_btn = obox.addButton("Try Zadig", QDialogButtonBox.ButtonRole.AcceptRole)
                zadig_btn.setObjectName("primary")
                zadig_btn.clicked.connect(lambda: launch_zadig())
            obox.addButton(QDialogButtonBox.StandardButton.Ok)
            obox.accepted.connect(outcome_dlg.accept)
            obox.rejected.connect(outcome_dlg.reject)
            ol.addWidget(obox)
            outcome_dlg.exec()
            break

    def _restore_defaults(self) -> None:
        self._settings.reset_to_defaults()
        self._load_settings()
        log.info("Factory defaults restored")

    def _check_for_updates(self) -> None:
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Checking…")
        self._update_status.setText("")

        self._update_checker = UpdateChecker(self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.up_to_date.connect(self._on_up_to_date)
        self._update_checker.check_failed.connect(self._on_update_failed)
        self._update_checker.check_async()

    def _on_update_available(self, latest: str) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText("Check for Updates")
        self._update_status.setStyleSheet("font-size: 11px; color: #e67e00;")
        self._update_status.setText(
            f'{latest} available — <a href="{_RELEASES_PAGE}">open GitHub Releases</a>'
        )
        self._update_status.setOpenExternalLinks(True)

    def _on_up_to_date(self) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText("Check for Updates")
        self._update_status.setStyleSheet("font-size: 11px; color: #4caf50;")
        self._update_status.setText("You're up to date.")

    def _on_update_failed(self, reason: str) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText("Check for Updates")
        self._update_status.setStyleSheet("font-size: 11px; color: #888;")
        self._update_status.setText(f"Check failed: {reason}")
