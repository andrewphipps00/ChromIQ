"""Settings / Preferences dialog."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
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
from core.platform_paths import (
    argyll_download_page,
    default_argyll_bin_dir,
    is_windows,
    native_print_supported,
)
from core.updater import UpdateChecker, _RELEASES_PAGE
from core.version import APP_VERSION
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollSpinBox, make_browse_button, open_dir_dialog

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

import sys as _sys


class SettingsDialog(QDialog):
    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._update_checker: UpdateChecker | None = None
        self.setWindowTitle("ChromIQ Preferences")
        self.setMinimumWidth(900)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()
        self._load_settings()
        self.resize(900, self.sizeHint().height())

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
            f"Default: {default_argyll_bin_dir()}\n"
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

        # ---- i1Pro chart defaults ----
        from data.patch_db import I1PRO_DEFAULT_PRESETS, I1PRO_PRESET_LABELS
        i1pro_grp = QGroupBox("i1Pro Chart Defaults", self)
        i1g = QVBoxLayout(i1pro_grp)

        # Row 1: default layout preset
        i1_preset_row = QHBoxLayout()
        i1_preset_row.addWidget(QLabel("Default layout:", self))
        self._i1pro_preset_combo = NoScrollComboBox(self)
        for key in ("m10_a0.95", "m10_a1.0", "m6_a1.0"):
            self._i1pro_preset_combo.addItem(I1PRO_PRESET_LABELS[key], key)
        self._i1pro_preset_combo.setMinimumWidth(320)
        i1_preset_row.addWidget(self._i1pro_preset_combo)
        i1_preset_row.addStretch()
        i1_preset_row.addWidget(TooltipButton(
            "i1Pro Chart Defaults",
            "Sets the default printtarg layout flags (−m / −M margin and −a patch "
            "scale) used by the Create Chart tab whenever the active instrument is "
            "an i1Pro (i1Pro / i1Pro 2 / i1Pro 3).\n\n"
            "  • −m 10  −a 0.95  — recommended. Wider margin protects strip optics "
            "from drifting onto paper at the trailing edge; smaller patches let "
            "~9% more colours fit per sheet.\n"
            "  • −m 10  −a 1.0   — full-size patches with the wider margin.\n"
            "  • −m 6   −a 1.0   — tightest layout. Higher risk of 'not enough "
            "patches read' errors on some printers when the strip's last patch "
            "lands too close to the bare paper edge.\n\n"
            "Other instruments (i1Pro 3 Plus, ColorMunki, SpectroScan) are not "
            "affected by this setting — they keep their own defaults.\n\n"
            "Changes apply to both Guided and Manual mode. A custom margin or "
            "patch-scale you set manually is preserved — switching instruments "
            "only updates the value if it currently matches one of the three "
            "preset values above.",
            self,
            min_width=620,
        ))
        i1g.addLayout(i1_preset_row)

        # Row 2: ChromIQ-style clipping border checkbox
        i1_clip_row = QHBoxLayout()
        self._chromiq_clip_check = QCheckBox(
            "Use ChromIQ-style clipping border", self
        )
        i1_clip_row.addWidget(self._chromiq_clip_check)
        i1_clip_row.addStretch()
        i1_clip_row.addWidget(TooltipButton(
            "ChromIQ-Style Clipping Border",
            "Replaces printtarg's plain white i1Pro clip strip with a "
            "ChromIQ-branded version that includes a spectrum accent and "
            "three columns of useful info (chart summary + print reminders, "
            "a fill-in-the-blank form for archival notes, and scanning-table "
            "orientation instructions).\n\n"
            "How it works behind the scenes:\n"
            "  1. printtarg is always told to suppress the native clip strip "
            "(-L), so it can use the whole page width for patches.\n"
            "  2. ChromIQ then shifts the patch block to the right inside the "
            "TIFF, opening up roughly the same amount of space on the LEFT as "
            "printtarg would have reserved natively (~28 mm).\n"
            "  3. The ChromIQ left-strip content is stamped into that new "
            "white area.\n\n"
            "Trade-off: Argyll's small vertical ID line on the RIGHT edge of "
            "the chart gets pushed off the page by the shift, so the right-"
            "margin command/notes stamp is disabled while this is on (those "
            "options are hidden in the Create Chart tab).\n\n"
            "Only takes effect when the chart uses an i1Pro / i1Pro 2 / "
            "i1Pro 3 / i1Pro 3 Plus AND paper is A4 / Letter or larger. "
            "On smaller paper or other instruments the setting is silently "
            "ignored and the chart is generated normally.",
            self,
            min_width=620,
        ))
        i1g.addLayout(i1_clip_row)

        layout.addWidget(i1pro_grp)

        # ---- Neutral patches ----
        neutral_grp = QGroupBox("Neutral Patches", self)
        ng = QVBoxLayout(neutral_grp)
        gr_row = QHBoxLayout()
        gr_row.addWidget(QLabel("Grey ramp reference:", self))
        self._grey_ref_spin = NoScrollSpinBox(self)
        self._grey_ref_spin.setRange(200, 2000)
        self._grey_ref_spin.setSingleStep(10)
        self._grey_ref_spin.setSuffix(" patches")
        self._grey_ref_spin.setMinimumWidth(140)
        gr_row.addWidget(self._grey_ref_spin)
        gr_row.addStretch()
        gr_row.addWidget(TooltipButton(
            "Grey Ramp Reference",
            "Controls how many neutral patches (the grey ramp plus the white and "
            "black anchors) ChromIQ adds, relative to the size of the chart.\n\n"
            "It is the patch count at which a chart gets the standard set of "
            "32 grey + 4 white + 4 black. Bigger charts get proportionally more; "
            "smaller charts get fewer.\n\n"
            "  • Lower this number for DENSER neutrals on every chart — better "
            "grey balance and shadow detail, at the cost of fewer colour patches.\n"
            "  • Raise it for SPARSER neutrals — more of the chart spent on "
            "colour, fewer on greys.\n\n"
            "Small charts always keep a sensible minimum — they never receive the "
            "full neutral set, so a tiny target won't be swamped by greys.\n\n"
            "Applies to both Guided and Manual mode (Manual only when the "
            "Auto −g / −e / −B checkboxes are on). Default: 560.",
            self,
            min_width=600,
        ))
        ng.addLayout(gr_row)
        layout.addWidget(neutral_grp)

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
        self._themed_colors_check = QCheckBox(
            "Use app theme colors for 3D gamut viewer", self
        )
        bh.addWidget(self._themed_colors_check)

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
        native_print_container.setVisible(native_print_supported())
        bh.addWidget(native_print_container)

        confirm_row = QHBoxLayout()
        self._confirm_print_check = QCheckBox(
            "Confirm print settings before sending to printer", self
        )
        confirm_row.addWidget(self._confirm_print_check)
        confirm_row.addStretch()
        confirm_row.addWidget(TooltipButton(
            "Confirm Print Settings",
            "When enabled, ChromIQ shows a summary dialog of every option that "
            "will be sent to CUPS before each print job:\n\n"
            "  • Printer, paper size, media type, quality, tray, borderless\n"
            "  • Auto-detected orientation (portrait or landscape)\n"
            "  • The forced-off state of duplex and colour management\n"
            "  • Any detected mismatches (e.g. paper size ≠ chart size)\n\n"
            "Highly recommended — profiling targets waste expensive paper and "
            "ink when printed with the wrong settings.",
            self,
            min_width=560,
        ))
        confirm_row.setContentsMargins(0, 0, 0, 0)
        # The CUPS preflight summary is a macOS/Linux concept; hide this option
        # on Windows, where printing goes through a different path.
        confirm_container = QWidget(self)
        confirm_container.setLayout(confirm_row)
        confirm_container.setVisible(not is_windows())
        bh.addWidget(confirm_container)

        layout.addWidget(behaviour_grp)

        # ---- Appearance ----
        appearance_grp = QGroupBox("Appearance", self)
        ap = QHBoxLayout(appearance_grp)
        ap.addWidget(QLabel("Theme:", self))
        self._appearance_combo = NoScrollComboBox(self)
        # data values map combo index -> setting string
        self._appearance_combo.addItem("System (Auto)", "auto")
        self._appearance_combo.addItem("Light",        "light")
        self._appearance_combo.addItem("Dark",         "dark")
        self._appearance_combo.setMinimumWidth(180)
        self._appearance_combo.currentIndexChanged.connect(self._on_appearance_preview)
        ap.addWidget(self._appearance_combo)
        ap.addStretch()
        ap.addWidget(TooltipButton(
            "Appearance",
            "Switches the entire app between light and dark visuals.\n\n"
            "  • System (Auto) — follow your macOS Appearance setting and "
            "react if you change it while ChromIQ is running.\n"
            "  • Light — force the light theme even if your system is dark.\n"
            "  • Dark  — force the dark theme even if your system is light.\n\n"
            "Changes preview instantly. Click OK to keep them, or Cancel to revert.",
            self,
            min_width=520,
        ))
        layout.addWidget(appearance_grp)

        # ---- About / Updates ----
        credit1 = QLabel(f"ChromIQ v{APP_VERSION} · Created by Sebastian Reiprich", self)
        credit1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit1.setStyleSheet("color: #606060; font-size: 11px;")
        layout.addWidget(credit1)

        credit2 = QLabel(
            "Built on ArgyllCMS by Graeme Gill · Made possible by Knut Georg Larsson · "
            "Testing & feedback: Nelson (Pharmacist), Alan Goldhammer", self
        )
        credit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit2.setStyleSheet("color: #606060; font-size: 11px;")
        layout.addWidget(credit2)

        self._update_status = QLabel("", self)
        self._update_status.setStyleSheet("font-size: 11px;")
        self._update_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status.setFixedHeight(QFontMetrics(self._update_status.font()).height())
        layout.addWidget(self._update_status)

        # ---- Bottom row: Restore Defaults | Report a Bug | Check for Updates  ...  Cancel / OK ----
        bottom_row = QHBoxLayout()
        reset_btn = QPushButton("Restore Factory Defaults", self)
        reset_btn.setObjectName("reset_defaults")
        reset_btn.clicked.connect(self._restore_defaults)
        bottom_row.addWidget(reset_btn)

        bug_btn = QPushButton("Report a Bug…", self)
        bug_btn.setToolTip("Open the bug-report form on GitHub in your browser.")
        bug_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/itsab1989/ChromIQ/issues/new?template=bug_report.yml")))
        bottom_row.addWidget(bug_btn)

        self._update_btn = QPushButton("Check for Updates", self)
        self._update_btn.clicked.connect(self._check_for_updates)
        bottom_row.addWidget(self._update_btn)
        bottom_row.addStretch()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        bb.accepted.connect(self._save_and_close)
        bb.rejected.connect(self.reject)
        bottom_row.addWidget(bb)

        # Match the gap between left-side buttons to QDialogButtonBox's own
        # internal spacing so Restore↔Bug↔Update reads the same as OK↔Cancel.
        bb_layout = bb.layout()
        bottom_row.setSpacing(bb_layout.spacing() if bb_layout else 6)

        layout.addLayout(bottom_row)

        from ui.theme import resolve_mode
        self._apply_indicator_theme(resolve_mode(self._settings.get("appearance", "auto")))

    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        s = self._settings
        self._argyll_edit.setText(s.get("argyll_bin_path", default_argyll_bin_dir()))
        self._folder_edit.setText(s.get("custom_output_path", ""))
        self._restore_tab_check.setChecked(s.get("restore_last_tab", True))
        self._restore_session_check.setChecked(bool(s.get("restore_last_session", False)))
        self._themed_colors_check.setChecked(bool(s.get("gamut_themed_colors", True)))
        self._cal_mode_check.setChecked(bool(s.get("calibration_mode", False)))
        self._native_print_check.setChecked(bool(s.get("use_native_print_dialog", False)))
        self._confirm_print_check.setChecked(bool(s.get("confirm_before_printing", True)))
        from data.patch_db import I1PRO_DEFAULT_PRESET_KEY
        i1pro_key = str(s.get("i1pro_default_preset", I1PRO_DEFAULT_PRESET_KEY))
        idx = self._i1pro_preset_combo.findData(i1pro_key)
        self._i1pro_preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._chromiq_clip_check.setChecked(
            bool(s.get("i1pro_chromiq_clip_style", False))
        )
        self._grey_ref_spin.setValue(int(s.get("grey_ramp_reference", 560)))
        # Appearance: capture current value so Cancel can revert any live preview.
        current = str(s.get("appearance", "auto"))
        self._appearance_original = current
        idx = self._appearance_combo.findData(current)
        # Block signals so loading the saved value doesn't fire a preview.
        self._appearance_combo.blockSignals(True)
        self._appearance_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._appearance_combo.blockSignals(False)

    def _apply_indicator_theme(self, mode: str) -> None:
        """Apply the neutral indicator colour to checkboxes, line-edit focus and
        tooltip ⓘ icons for the given resolved mode ('light'/'dark').

        Overrides the global APP_STYLESHEET ACCENT (SPEC_CYAN), which would read
        as the Build Profile cyan inside a dialog body where there's no tab
        accent to anchor it. Re-run on live theme preview so the colours switch
        without reopening the dialog.
          Light: masthead "Chrom" wordmark.  Dark: neutral grey (Restore border).
        """
        indicator = "#1c1b18" if mode == "light" else "#d0d0d0"
        self.setStyleSheet(
            f"QLineEdit:focus {{ border-color: {indicator}; }}"
            f"QCheckBox::indicator:checked {{ background: {indicator}; border-color: {indicator}; }}"
            f"QCheckBox::indicator:hover {{ border-color: {indicator}; }}"
        )
        for btn in self.findChildren(TooltipButton):
            btn._color_override = indicator
            btn._set_icon()

    def _on_appearance_preview(self, _index: int) -> None:
        """Apply the picked theme immediately without persisting it."""
        from ui.theme import apply_appearance
        app = QApplication.instance()
        if app is None:
            return
        # The dialog is parented to the main window — use that for masthead/title-bar updates.
        main_window = self.parent()
        setting = self._appearance_combo.currentData()
        mode = apply_appearance(app, main_window, str(setting))
        self._apply_indicator_theme(mode)

    def reject(self) -> None:  # type: ignore[override]
        # Revert any live theme preview to whatever was persisted on open.
        if getattr(self, "_appearance_original", None) is not None:
            from ui.theme import apply_appearance
            app = QApplication.instance()
            if app is not None:
                apply_appearance(app, self.parent(), self._appearance_original)
        super().reject()

    def _save_and_close(self) -> None:
        s = self._settings
        s.set("argyll_bin_path",       self._argyll_edit.text().strip())
        s.set("custom_output_path",    self._folder_edit.text().strip())
        s.set("restore_last_tab",          self._restore_tab_check.isChecked())
        s.set("restore_last_session",      self._restore_session_check.isChecked())
        s.set("gamut_themed_colors",       self._themed_colors_check.isChecked())
        s.set("calibration_mode",          self._cal_mode_check.isChecked())
        s.set("use_native_print_dialog",   self._native_print_check.isChecked())
        s.set("confirm_before_printing",   self._confirm_print_check.isChecked())
        s.set("appearance",                str(self._appearance_combo.currentData()))
        s.set("i1pro_default_preset",      str(self._i1pro_preset_combo.currentData()))
        s.set("i1pro_chromiq_clip_style",  self._chromiq_clip_check.isChecked())
        s.set("grey_ramp_reference",       int(self._grey_ref_spin.value()))
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
        elif _sys.platform == "darwin":
            hint = "arm64 for Apple Silicon, osx64 for Intel"
        else:
            hint = "the binary tar.bz2 matching your distro's architecture (x86_64 or aarch64) — " \
                   "or install via your package manager (e.g. sudo apt install argyll)"
        self._argyll_status.setText(
            f"Opening argyllcms.com — download the latest version ({hint}), "
            "then unpack and set the bin path above."
        )
        QDesktopServices.openUrl(QUrl(argyll_download_page()))

    def _show_usb_installer(self) -> None:
        if _sys.platform != "win32":
            return
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
        from core.usb_driver_installer import (
            enumerate_connected, install_winusb, launch_zadig, unbound_targets,
        )
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
                if not needs_install:
                    # Every detected device already has a WinUSB/libusb0 driver.
                    # Don't promise an installer the old code wouldn't show a
                    # button for; explain that and still offer a manual repair
                    # path (forum #148275: dialog mentioned Zadig but had no
                    # button when the device reported the driver as installed).
                    action_text = (
                        "The driver is already installed for the device(s) above. "
                        "If ChromIQ or Argyll still can't open your instrument, click "
                        "<b>Reinstall Driver</b> to run the installer again."
                    )
                elif _wdi_available:
                    action_text = (
                        "Click <b>Install Driver</b> to install the Microsoft WinUSB driver "
                        "automatically. A Windows security prompt will appear — click Yes to "
                        "continue.<br><br>"
                        "<i>No test-signing mode required. Works on x64 and ARM64.</i>"
                    )
                else:
                    action_text = (
                        "Click <b>Open Zadig</b> and ChromIQ will launch <b>Zadig</b>, a free "
                        "USB driver tool. In Zadig:<br>"
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
            if devices:
                if not needs_install:
                    btn_label = "Reinstall Driver" if _wdi_available else "Open Zadig"
                else:
                    btn_label = "Install Driver" if _wdi_available else "Open Zadig"
                install_btn = btn_box.addButton(btn_label, QDialogButtonBox.ButtonRole.AcceptRole)
                install_btn.setObjectName("primary")
            refresh_btn = btn_box.addButton("Refresh", QDialogButtonBox.ButtonRole.ResetRole)
            refresh_btn.clicked.connect(lambda checked=False, d=dlg: d.done(_REFRESH))
            btn_box.addButton(QDialogButtonBox.StandardButton.Close)
            # The install/reinstall/Open-Zadig button uses AcceptRole, which
            # fires QDialogButtonBox.accepted — wire it to the dialog's accept()
            # or clicking it does nothing (the dialog never returns Accepted).
            btn_box.accepted.connect(dlg.accept)
            btn_box.rejected.connect(dlg.reject)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _COLOR)

            result = dlg.exec()

            if result == _REFRESH:
                continue   # rebuild with fresh device list

            if result != QDialog.DialogCode.Accepted or not devices:
                break   # Close button or nothing connected

            # ---- run installation ----
            # "Reinstall Driver" (no device needs install) repairs every detected
            # device; otherwise only the ones missing a driver are targeted.
            targets = needs_install or devices
            if _wdi_available:
                ran_ok = all(install_winusb(d) for d in targets)
                # wdi-simple can report success (exit 0) without actually binding
                # the driver to the live device — a stale ghost instance from a
                # previous USB port can misdirect it. Verify by re-enumerating
                # before claiming success, and fall back to Zadig if it didn't bind.
                still_unbound = unbound_targets(targets)
                if ran_ok and not still_unbound:
                    outcome_text = "WinUSB driver installed successfully."
                    offer_zadig = False
                elif not ran_ok:
                    outcome_text = (
                        "Automatic installation failed or was cancelled.<br>"
                        "Click <b>Try Zadig</b> to install it manually using the guided tool."
                    )
                    offer_zadig = True
                else:
                    names = ", ".join(d.name for d in still_unbound) or "the instrument"
                    outcome_text = (
                        "Windows reported the install finished, but the driver still "
                        f"isn't bound to {names}. This often happens when the device "
                        "was previously plugged into a different USB port.<br><br>"
                        "Click <b>Try Zadig</b> to install it reliably: pick your "
                        "instrument in Zadig, choose <b>WinUSB</b> (or libusb-win32), "
                        "then click <b>Replace Driver</b>. Unplugging and replugging the "
                        "instrument first can also help."
                    )
                    offer_zadig = True
            else:
                status = launch_zadig()
                if status == "launched":
                    outcome_text = (
                        "Zadig is open. Select your colorimeter, choose WinUSB, "
                        "then click Install Driver."
                    )
                elif status == "download_page":
                    outcome_text = (
                        "Zadig isn't bundled with this build, so its download page "
                        "has been opened in your browser.<br>"
                        "Download and run <b>Zadig</b>, then: Options → List All Devices → "
                        "select your colorimeter → choose WinUSB → Install Driver."
                    )
                else:
                    outcome_text = (
                        "Could not open Zadig or its download page. Visit "
                        "<b>https://zadig.akeo.ie</b> manually, or try running ChromIQ "
                        "as Administrator."
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
