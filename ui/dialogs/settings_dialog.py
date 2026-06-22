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
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.argyll_detect import find_argyll_bin_path
from core.logger import get_logger
from core.platform_paths import (
    argyll_download_page,
    default_argyll_bin_dir,
    is_macos,
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
from core.i18n import tr


class SettingsDialog(QDialog):
    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._update_checker: UpdateChecker | None = None
        self.setWindowTitle(tr("ChromIQ Preferences"))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()
        self._load_settings()
        # Size to the content's natural width with a comfortable floor. The
        # bottom-row buttons render wider on macOS than the headless fallback
        # font suggests, so a fixed width clipped the row once #56 added the
        # "Request a Feature…" button — fit the real sizeHint instead.
        _w = max(1040, self.sizeHint().width())
        self.setMinimumWidth(_w)
        self.resize(_w, self.sizeHint().height())

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        outer.setContentsMargins(20, 16, 20, 16)

        # The preferences are split across tabs; the per-combo Margin Thresholds
        # editor lives on its own tab (Knut's request). The credits + button row
        # stay below the tabs so they're shared. All existing group boxes are
        # added to the General page via the local ``layout`` below, unchanged.
        self._tabs = QTabWidget(self)
        outer.addWidget(self._tabs)
        general_page = QWidget()
        layout = QVBoxLayout(general_page)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # ---- ArgyllCMS ----
        argyll_grp = QGroupBox(tr("ArgyllCMS Binaries"), self)
        ag = QVBoxLayout(argyll_grp)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel(tr("Binary path:"), self))
        self._argyll_edit = QLineEdit(self)
        path_row.addWidget(self._argyll_edit, stretch=1)
        browse_btn = make_browse_button(self, tr("Select ArgyllCMS bin folder"), icon="folder")
        browse_btn.clicked.connect(self._browse_argyll)
        path_row.addWidget(browse_btn)
        path_row.addWidget(TooltipButton(
            tr("ArgyllCMS Binary Path"),
            tr("Directory containing targen, printtarg, chartread, and colprof.\n"
               "Default: {path}\n"
               "You can download the latest version from argyllcms.com."
               ).format(path=default_argyll_bin_dir()),
            self,
        ))
        ag.addLayout(path_row)

        btn_row = QHBoxLayout()
        test_btn = QPushButton(tr("Test binaries"), self)
        test_btn.clicked.connect(self._test_argyll)
        detect_btn = QPushButton(tr("Auto-detect"), self)
        detect_btn.clicked.connect(self._auto_detect)
        dl_btn = QPushButton(tr("Download latest ArgyllCMS…"), self)
        dl_btn.clicked.connect(self._open_argyll_download)
        btn_row.addWidget(test_btn)
        btn_row.addWidget(detect_btn)
        btn_row.addWidget(dl_btn)

        if _sys.platform == "win32":
            driver_btn = QPushButton(tr("Install USB Driver…"), self)
            driver_btn.setToolTip(
                tr("Install the WinUSB driver for your colorimeter — "
                "no test-signing mode required, works on x64 and ARM64")
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
        folder_grp = QGroupBox(tr("Output Folder"), self)
        fl = QVBoxLayout(folder_grp)

        folder_lbl = QLabel(
            tr("Default output folder (leave blank to use ~/ChromIQ/):"), self
        )
        folder_lbl.setWordWrap(True)
        fl.addWidget(folder_lbl)

        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit(self)
        self._folder_edit.setPlaceholderText(tr("~/ChromIQ/  (default)"))
        folder_row.addWidget(self._folder_edit, stretch=1)
        folder_browse = make_browse_button(self, tr("Select output folder"), icon="folder")
        folder_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(folder_browse)
        fl.addLayout(folder_row)

        layout.addWidget(folder_grp)

        # ---- i1Pro chart defaults ----
        from data.patch_db import I1PRO_DEFAULT_PRESETS, I1PRO_PRESET_LABELS
        i1pro_grp = QGroupBox(tr("i1Pro Chart Defaults"), self)
        i1g = QVBoxLayout(i1pro_grp)

        # Row 1: default layout preset
        i1_preset_row = QHBoxLayout()
        i1_preset_row.addWidget(QLabel(tr("Default layout:"), self))
        self._i1pro_preset_combo = NoScrollComboBox(self)
        for key in ("m10_a0.95", "m10_a1.0", "m6_a1.0"):
            self._i1pro_preset_combo.addItem(I1PRO_PRESET_LABELS[key], key)
        self._i1pro_preset_combo.setMinimumWidth(320)
        i1_preset_row.addWidget(self._i1pro_preset_combo)
        i1_preset_row.addStretch()
        i1_preset_row.addWidget(TooltipButton(
            tr("i1Pro Chart Defaults"),
            tr("Sets the default printtarg layout flags (−m / −M margin and −a patch "
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
            "preset values above."),
            self,
            min_width=620,
        ))
        i1g.addLayout(i1_preset_row)

        # Row 2: ChromIQ-style clipping border checkbox
        i1_clip_row = QHBoxLayout()
        self._chromiq_clip_check = QCheckBox(
            tr("Use ChromIQ-style clipping border"), self
        )
        i1_clip_row.addWidget(self._chromiq_clip_check)
        i1_clip_row.addStretch()
        i1_clip_row.addWidget(TooltipButton(
            tr("ChromIQ-Style Clipping Border"),
            tr("Replaces printtarg's plain white i1Pro clip strip with a "
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
            "ignored and the chart is generated normally."),
            self,
            min_width=620,
        ))
        i1g.addLayout(i1_clip_row)

        layout.addWidget(i1pro_grp)

        # ---- Neutral patches ----
        neutral_grp = QGroupBox(tr("Neutral Patches"), self)
        ng = QVBoxLayout(neutral_grp)
        gr_row = QHBoxLayout()
        gr_row.addWidget(QLabel(tr("Grey ramp reference:"), self))
        self._grey_ref_spin = NoScrollSpinBox(self)
        self._grey_ref_spin.setRange(200, 2000)
        self._grey_ref_spin.setSingleStep(10)
        self._grey_ref_spin.setSuffix(" patches")
        self._grey_ref_spin.setMinimumWidth(140)
        gr_row.addWidget(self._grey_ref_spin)
        gr_row.addStretch()
        gr_row.addWidget(TooltipButton(
            tr("Grey Ramp Reference"),
            tr("Controls how many neutral patches (the grey ramp plus the white and "
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
            "Auto −g / −e / −B checkboxes are on). Default: 560."),
            self,
            min_width=600,
        ))
        ng.addLayout(gr_row)
        layout.addWidget(neutral_grp)

        # ---- Behaviour ----
        # Options are laid out in two equal-width columns to keep the dialog
        # short; each option (checkbox + optional tooltip) is one grid cell.
        behaviour_grp = QGroupBox(tr("Behaviour"), self)
        bh = QGridLayout(behaviour_grp)
        bh.setHorizontalSpacing(100)
        bh.setColumnStretch(0, 1)
        bh.setColumnStretch(1, 1)

        def _bh_cell(check: QCheckBox, tooltip: TooltipButton | None = None) -> QWidget:
            cell = QWidget(self)
            row = QHBoxLayout(cell)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(check)
            row.addStretch()
            if tooltip is not None:
                row.addWidget(tooltip)
            return cell

        self._restore_tab_check = QCheckBox(
            tr("Restore last active tab on launch"), self
        )
        restore_tab_tip = TooltipButton(
            tr("Restore Last Active Tab on Launch"),
            tr("ChromIQ is organised as five numbered steps along the top of the "
            "window:\n\n"
            "  1. Create Chart\n"
            "  2. Print Chart\n"
            "  3. Measure\n"
            "  4. Build Profile\n"
            "  5. Check & Refine\n\n"
            "When this option is ON, ChromIQ reopens on whichever step you were "
            "looking at when you last closed the app. This is handy if you tend to "
            "work in several sittings — for example, you print a chart, quit, and "
            "later come back to measure it: the app opens straight on the Measure "
            "step instead of sending you back to the beginning.\n\n"
            "When OFF, ChromIQ always starts on step 1 (Create Chart).\n\n"
            "This only remembers which step was open — it does not reload any of "
            "your files. To have your files come back too, also turn on "
            "\"Restore last session on launch\"."),
            self,
            min_width=600,
        )

        self._restore_session_check = QCheckBox(
            tr("Restore last session on launch"), self
        )
        restore_session_tip = TooltipButton(
            tr("Restore Last Session on Launch"),
            tr("When this option is ON, ChromIQ remembers the files you were working "
            "with and reloads them automatically the next time you start the app, "
            "so you can carry on exactly where you left off.\n\n"
            "What gets restored:\n\n"
            "  • The chart / target you created (its name and the .ti1 file)\n"
            "  • The printable chart images (TIFF files) in the working folder\n"
            "  • Your measurement data (the .ti3 file)\n"
            "  • The ICC profile you built (.icc)\n"
            "  • The calibration measurements, if calibration options are enabled\n\n"
            "These files are simply re-opened from where they are saved on disk — "
            "nothing is copied, changed, or re-measured. If you have since moved or "
            "deleted a file, ChromIQ just skips that one and loads the rest.\n\n"
            "When OFF, ChromIQ starts with an empty session every time and you load "
            "the files you need by hand. This is the default — turn the option on if "
            "you usually continue the same job across several sessions."),
            self,
            min_width=560,
        )

        self._themed_colors_check = QCheckBox(
            tr("Use app theme colors for 3D gamut viewer"), self
        )
        themed_colors_tip = TooltipButton(
            tr("Use App Theme Colours for 3D Gamut Viewer"),
            tr("On the Check & Refine step, ChromIQ can show a rotatable 3D model of "
            "your printer and paper's colour range (its \"gamut\") — the full set of "
            "colours that combination can actually reproduce.\n\n"
            "When this option is ON, the colours of that 3D model are recoloured to "
            "match ChromIQ's own accent palette, and the very brightest points are "
            "toned down slightly so they don't wash out to pure white. The result "
            "blends in neatly with the app's light or dark theme.\n\n"
            "When OFF, the model keeps the viewer's natural colours, where each point "
            "is drawn roughly in the colour it represents.\n\n"
            "This setting is purely cosmetic. It changes only how the 3D preview "
            "looks — it has no effect whatsoever on your measurements or on the ICC "
            "profile ChromIQ builds."),
            self,
            min_width=560,
        )

        self._update_notify_check = QCheckBox(
            tr("Check for updates on startup"), self
        )
        update_notify_tip = TooltipButton(
            tr("Check for Updates on Startup"),
            tr("When this is on, ChromIQ quietly checks for a newer version each "
            "time it starts and, if one is available, shows a small popup that "
            "links to the download page.\n\n"
            "It never downloads or installs anything on its own — it only lets "
            "you know. You can also turn this off straight from that popup (the "
            "\"Don't remind me of new available versions\" box), and turn it back "
            "on again here."),
            self,
            min_width=560,
        )

        self._cal_mode_check = QCheckBox(tr("Enable calibration options"), self)
        cal_tip = TooltipButton(
            tr("Enable Calibration Options"),
            tr("Unlocks the full printer calibration workflow (printcal / applycal).\n\n"
            "Most users do NOT need this — consumer and prosumer inkjet printers "
            "typically produce better results from a direct profiling run without "
            "any hardware calibration step.\n\n"
            "Enable this only if you know your printer requires linearisation curves "
            "before profiling, or if you are an advanced user following an explicit "
            "ArgyllCMS calibration guide.\n\n"
            "When active: the guided modes in all tabs are hidden, a calibration "
            "target option appears in Create Chart, and a full printcal → applycal "
            "workflow is added to the Calibration & Profiling tab."),
            self,
            min_width=620,
        )

        self._chromiq_refine_check = QCheckBox(
            tr("ChromIQ-style refinement process"), self
        )
        refine_tip = TooltipButton(
            tr("ChromIQ-style refinement process"),
            tr("Builds a more accurate profile by REUSING the measurements you "
            "already made for an earlier profile, instead of throwing them away.\n\n"
            "Normally, every profiling run starts from scratch: you print a chart, "
            "measure it, and build a profile from only those patches. If you later "
            "make a second, refined chart for the same printer and paper, the "
            "measurements from the first run are not used again.\n\n"
            "With this option ON, ChromIQ can carry those earlier measurements "
            "forward. Here is the whole journey, step by step:\n\n"
            "  1. In Create Chart, you tick \"Refinement profile\" and pick the ICC "
            "profile from your earlier run. ChromIQ quietly keeps a copy of that "
            "profile's measurement data (a \"pre_…\" file) inside the working folder, "
            "so it is not deleted when the new chart is generated.\n\n"
            "  2. You print and measure the new chart as usual. In the Measure tab a "
            "new option appears: \"Also use measurement data from the pre-conditioning "
            "profile\". It only shows up when such saved data is actually present.\n\n"
            "  3. When you build the profile, ChromIQ combines the new measurements "
            "with the saved earlier ones and builds from the larger, combined set. "
            "More measured colours generally means a more accurate profile.\n\n"
            "Your freshly measured file is never altered — the combining happens on a "
            "separate copy only at build time, so you can re-measure or refine "
            "individual strips in Check & Refine exactly as before. The guided "
            "Check & Refine analysis still looks only at the strips you physically "
            "printed, so it will never ask you to re-measure a patch that came from "
            "the earlier run.\n\n"
            "When this option is OFF, ChromIQ behaves exactly as it always has — "
            "nothing in your normal workflow changes. Leave it off unless you "
            "specifically want to reuse measurements across refinement runs."),
            self,
            min_width=680,
        )

        self._averaging_check = QCheckBox(
            tr("Enable measurement averaging"), self
        )
        averaging_tip = TooltipButton(
            tr("Enable Measurement Averaging"),
            tr("Lets you read the SAME printed chart more than once and combine the "
            "readings, to even out the small random errors every measuring device "
            "makes. The more times you read a chart, the closer the averaged "
            "result gets to its \"true\" colour — which can make for a slightly "
            "more accurate profile, especially with budget instruments or tricky "
            "papers.\n\n"
            "You do NOT print a new chart. You measure the one already in front of "
            "you a second (or third, or fourth) time. Because the printed colours "
            "never change, only the tiny reading-to-reading wobble of the "
            "instrument does, averaging those reads cancels most of that wobble "
            "out.\n\n"
            "Here is the whole journey, step by step:\n\n"
            "  1. You measure your chart as usual in the Measure tab.\n\n"
            "  2. When the read finishes, a new completion window appears. As well "
            "as continuing to Build Profile, it offers \"Measure again\" — put the "
            "very same chart back on the table and read it once more.\n\n"
            "  3. You can repeat this as many times as you like. ChromIQ keeps each "
            "read safely side by side in the working folder (named …_read1, "
            "…_read2, and so on).\n\n"
            "  4. Once you have two or more reads, the window lets you either build "
            "from just the last read, or average all of the reads together and "
            "build from that combined result (saved as …_average).\n\n"
            "Mean vs. Median: averaging normally uses the plain mean (the ordinary "
            "average). The window also offers Median, which ignores the odd "
            "stray reading and only behaves differently once you have three or "
            "more reads — handy if one read was disturbed (a bump, a smudge) and "
            "you don't want it dragging the result.\n\n"
            "When this option is OFF (the default), ChromIQ behaves exactly as it "
            "always has: a finished measurement takes you straight on to Build "
            "Profile with no extra window and no extra files. Turn it on only if "
            "you want the option to read charts repeatedly for extra precision.\n\n"
            "Tip: two reads already remove most of the random noise; three or four "
            "give diminishing returns. There is no benefit to averaging reads of "
            "DIFFERENT charts — this is only for re-reading one and the same chart.\n\n"
            "With thanks to Alan Goldhammer, who suggested this feature."),
            self,
            min_width=660,
        )

        self._native_print_check = QCheckBox(tr("Use default macOS printer dialog"), self)
        native_tip = TooltipButton(
            tr("Use default macOS printer dialog"),
            tr("When enabled, clicking Print in the Print Chart tab opens the standard\n"
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
            "automatically with no extra steps required."),
            self,
            min_width=620,
        )

        self._pdf_fallback_check = QCheckBox(
            tr("Exact-size PDF fallback (ChromIQ printing)"), self
        )
        pdf_fallback_tip = TooltipButton(
            tr("Exact-size PDF fallback"),
            tr("Applies only to ChromIQ's own printing pipeline (the default when "
            "the macOS printer dialog below is disabled).\n\n"
            "ChromIQ first sends every chart as PostScript. Most home and photo "
            "printers do not understand PostScript, so macOS rejects it and "
            "ChromIQ resends the chart in another format:\n\n"
            "  • OFF — resend as a plain TIFF. macOS then decides the size "
            "itself and SHRINKS a full-page chart by about 3% so it fits "
            "inside the printer's margins. The printed patches end up "
            "slightly smaller and shifted compared to the on-screen layout.\n\n"
            "  • ON — resend as a PDF built by ChromIQ with the chart placed "
            "at exactly 100% scale. Anything that would fall into the "
            "printer's unprintable margin is simply cut off (charts keep "
            "white margins there, so nothing of value is lost). This matches "
            "how Apple's ColorSync Utility prints.\n\n"
            "Colour is unaffected either way — both formats reach the printer "
            "without any colour conversion.\n\n"
            "Greyed out while the macOS printer dialog is enabled, because no "
            "fallback is involved on that path."),
            self,
            min_width=620,
        )

        self._confirm_print_check = QCheckBox(
            tr("Confirm print settings before printing"), self
        )
        confirm_tip = TooltipButton(
            tr("Confirm Print Settings"),
            tr("When enabled, ChromIQ shows a summary dialog of every option that "
            "will be sent to CUPS before each print job:\n\n"
            "  • Printer, paper size, media type, quality, tray, borderless\n"
            "  • Auto-detected orientation (portrait or landscape)\n"
            "  • The forced-off state of duplex and colour management\n"
            "  • Any detected mismatches (e.g. paper size ≠ chart size)\n\n"
            "Highly recommended — profiling targets waste expensive paper and "
            "ink when printed with the wrong settings."),
            self,
            min_width=560,
        )

        # The CUPS preflight summary and the PDF fallback only apply to
        # ChromIQ's own print pipeline. When the macOS print dialog is in use,
        # that dialog is the confirmation step and no lp fallback ever runs,
        # so grey both options out.
        self._native_print_check.toggled.connect(self._sync_print_path_options)

        # Collect the options that apply on this platform, in order, then place
        # them two per row. Platform-specific options are simply omitted (rather
        # than hidden) so they leave no empty cell in the grid.
        bh_cells = [
            _bh_cell(self._restore_tab_check, restore_tab_tip),
            _bh_cell(self._restore_session_check, restore_session_tip),
            _bh_cell(self._update_notify_check, update_notify_tip),
            _bh_cell(self._themed_colors_check, themed_colors_tip),
            _bh_cell(self._cal_mode_check, cal_tip),
            _bh_cell(self._chromiq_refine_check, refine_tip),
            _bh_cell(self._averaging_check, averaging_tip),
        ]
        if native_print_supported():
            bh_cells.append(_bh_cell(self._native_print_check, native_tip))
        # The exact-size PDF fallback addresses a macOS-specific CUPS filter
        # behaviour (cgimagetopdf); skip it elsewhere.
        if is_macos():
            bh_cells.append(_bh_cell(self._pdf_fallback_check, pdf_fallback_tip))
        # The CUPS preflight summary is a macOS/Linux concept; skip it on Windows.
        if not is_windows():
            bh_cells.append(_bh_cell(self._confirm_print_check, confirm_tip))

        for i, cell in enumerate(bh_cells):
            bh.addWidget(cell, i // 2, i % 2)

        # The platform-gated print options above are constructed unconditionally
        # (their attributes are referenced by _load_settings / _save_and_close /
        # _sync_print_path_options), but only wrapped in a _bh_cell — which
        # reparents them into the grid — on the platforms that use them. On the
        # others they keep parent=self with no layout, so Qt floats them at the
        # dialog's top-left corner, where they pile up over the first group box.
        # Hide whatever wasn't placed.
        for widget in (
            self._native_print_check, native_tip,
            self._pdf_fallback_check, pdf_fallback_tip,
            self._confirm_print_check, confirm_tip,
        ):
            if widget.parent() is self:
                widget.hide()

        layout.addWidget(behaviour_grp)

        # ---- Appearance & Language ----
        # "&&" — QGroupBox treats a single "&" as a mnemonic marker.
        # Same two-column grid geometry as the Behaviour section above so the
        # cells line up visually.
        appearance_grp = QGroupBox(tr("Appearance && Language"), self)
        ap = QGridLayout(appearance_grp)
        ap.setHorizontalSpacing(100)
        ap.setColumnStretch(0, 1)
        ap.setColumnStretch(1, 1)

        def _ap_cell(label: str, combo: NoScrollComboBox,
                     tooltip: TooltipButton) -> QWidget:
            cell = QWidget(self)
            row = QHBoxLayout(cell)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(label, self))
            row.addWidget(combo)
            row.addStretch()
            row.addWidget(tooltip)
            return cell

        self._appearance_combo = NoScrollComboBox(self)
        # data values map combo index -> setting string
        self._appearance_combo.addItem(tr("System (Auto)"), "auto")
        self._appearance_combo.addItem(tr("Light"),        "light")
        self._appearance_combo.addItem(tr("Dark"),         "dark")
        self._appearance_combo.setMinimumWidth(180)
        self._appearance_combo.currentIndexChanged.connect(self._on_appearance_preview)
        appearance_tip = TooltipButton(
            tr("Appearance"),
            tr("Switches the entire app between light and dark visuals.\n\n"
            "  • System (Auto) — follow your macOS Appearance setting and "
            "react if you change it while ChromIQ is running.\n"
            "  • Light — force the light theme even if your system is dark.\n"
            "  • Dark  — force the dark theme even if your system is light.\n\n"
            "Changes preview instantly. Click OK to keep them, or Cancel to revert."),
            self,
            min_width=520,
        )

        self._language_combo = NoScrollComboBox(self)
        from core.i18n import available_languages
        for code, native_name in available_languages():
            self._language_combo.addItem(native_name, code)
        self._language_combo.setMinimumWidth(180)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_tip = TooltipButton(
            tr("Language"),
            tr("Choose the language for everything ChromIQ shows you — menus, "
            "buttons, dialogs, help texts and tooltips.\n\n"
            "The change takes effect the next time you start ChromIQ, so "
            "nothing on screen jumps around mid-session.\n\n"
            "Output from the ArgyllCMS tools in the log view stays in "
            "English — it comes from the tools themselves, not from ChromIQ."),
            self,
            min_width=520,
        )

        ap.addWidget(_ap_cell(tr("Theme:"), self._appearance_combo,
                              appearance_tip), 0, 0)
        ap.addWidget(_ap_cell(tr("Language:"), self._language_combo,
                              language_tip), 0, 1)

        self._language_restart_hint = QLabel(
            tr("Takes effect after you restart ChromIQ."), self)
        self._language_restart_hint.setStyleSheet("color: #e6a23c; font-size: 11px;")
        self._language_restart_hint.setVisible(False)
        ap.addWidget(self._language_restart_hint, 1, 0, 1, 2)

        layout.addWidget(appearance_grp)
        layout.addStretch()

        self._tabs.addTab(general_page, tr("General"))
        self._tabs.addTab(self._build_margin_thresholds_tab(), tr("Margin Thresholds"))

        # ---- About / Updates (below the tabs) ----
        credit1 = QLabel(tr("ChromIQ v{APP_VERSION} · Created by Sebastian Reiprich").format(APP_VERSION=APP_VERSION), self)
        credit1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit1.setStyleSheet("color: #606060; font-size: 11px;")
        outer.addWidget(credit1)

        credit2 = QLabel(
            tr("Built on ArgyllCMS by Graeme Gill · Made possible by Knut Georg Larsson · "
            "Testing & feedback: Nelson (Pharmacist), Alan Goldhammer"), self
        )
        credit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit2.setStyleSheet("color: #606060; font-size: 11px;")
        outer.addWidget(credit2)

        self._update_status = QLabel("", self)
        self._update_status.setStyleSheet("font-size: 11px;")
        self._update_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status.setFixedHeight(QFontMetrics(self._update_status.font()).height())
        outer.addWidget(self._update_status)

        # ---- Bottom row: Restore Defaults | Report a Bug | Check for Updates  ...  Cancel / OK ----
        bottom_row = QHBoxLayout()
        reset_btn = QPushButton(tr("Restore Factory Defaults"), self)
        reset_btn.setObjectName("reset_defaults")
        reset_btn.clicked.connect(self._restore_defaults)
        bottom_row.addWidget(reset_btn)

        from core.issue_report import build_bug_report_url, build_feature_request_url
        bug_btn = QPushButton(tr("Report a Bug…"), self)
        bug_btn.setToolTip(tr("Open the bug-report form on GitHub in your browser."))
        bug_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(build_bug_report_url(
                self._settings.get("argyll_bin_path", "")))))
        bottom_row.addWidget(bug_btn)

        feature_btn = QPushButton(tr("Request a Feature…"), self)
        feature_btn.setToolTip(
            tr("Open the feature-request form on GitHub in your browser."))
        feature_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(build_feature_request_url())))
        bottom_row.addWidget(feature_btn)

        self._update_btn = QPushButton(tr("Check for Updates"), self)
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

        outer.addLayout(bottom_row)

        from ui.theme import resolve_mode
        self._apply_indicator_theme(resolve_mode(self._settings.get("appearance", "auto")))

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Margin Thresholds tab
    # ------------------------------------------------------------------
    # Instruments and paper+orientation choices the per-combo thresholds can be
    # defined for. The instrument labels match what the Create Chart inspector
    # derives from the chart (see core.settings.margin_combo_key).
    _MARGIN_INSTRUMENTS = ("i1Pro", "i1Pro 3+", "ColorMunki", "SpectroScan")
    _MARGIN_PAPERS = ("A4", "Letter", "A3", "A3+", "A2", "Tabloid")
    _MARGIN_ORIENTS = ("Portrait", "Landscape")

    def _build_margin_thresholds_tab(self) -> QWidget:
        """Per-(instrument, paper+orientation) minimum-margin editor.

        Instrument + paper pulldowns pick the active combo; below them a
        free-text description and a small L/R/T/B table hold that combo's
        editable minimums. The two behaviour checkboxes gate the Create Chart
        inspector; "Notify…" is greyed out while the frame is hidden.
        """
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(12, 12, 12, 12)

        intro = QLabel(tr(
            "Warn when a generated chart's measured page margins fall below the "
            "minimum needed for your measuring ruler / jig. Values are minimums "
            "in millimetres (paper edge → patch area), in the printed (preview) "
            "orientation. These are editable starting points — adjust them to "
            "your own rig."), self)
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #909090; font-size: 11px;")
        v.addWidget(intro)

        # In-memory working copy; committed to settings on Save. Read via the
        # generic get + module parser so any settings object (incl. test doubles
        # with only get/set) works.
        from core.settings import parse_margin_thresholds
        self._margin_table = parse_margin_thresholds(
            self._settings.get("margin_thresholds", ""))

        # ---- behaviour checkboxes ----
        self._margin_show_check = QCheckBox(
            tr("Show the “Measured from Preview” frame in Create Chart"), self)
        self._margin_notify_check = QCheckBox(
            tr("Notify when a measured margin is below its threshold"), self)
        self._margin_show_check.toggled.connect(self._sync_margin_notify_enabled)
        v.addWidget(self._margin_show_check)
        v.addWidget(self._margin_notify_check)

        # ---- combo selectors ----
        sel = QGridLayout()
        sel.addWidget(QLabel(tr("Instrument:"), self), 0, 0)
        self._margin_instr = NoScrollComboBox(self)
        self._margin_instr.addItems(list(self._MARGIN_INSTRUMENTS))
        sel.addWidget(self._margin_instr, 0, 1)
        sel.addWidget(QLabel(tr("Paper size:"), self), 0, 2)
        self._margin_paper = NoScrollComboBox(self)
        self._margin_paper.addItems(
            [f"{p} {o}" for p in self._MARGIN_PAPERS for o in self._MARGIN_ORIENTS])
        sel.addWidget(self._margin_paper, 0, 3)
        sel.setColumnStretch(1, 1)
        sel.setColumnStretch(3, 1)
        v.addLayout(sel)

        # ---- description ----
        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel(tr("Description:"), self))
        self._margin_desc = QLineEdit(self)
        self._margin_desc.setPlaceholderText(
            tr("e.g. which ruler / jig these margins are for"))
        desc_row.addWidget(self._margin_desc, stretch=1)
        v.addLayout(desc_row)

        # ---- L/R/T/B value table ----
        grid = QGridLayout()
        self._margin_fields: dict[str, NoScrollSpinBox] = {}
        for col, (key, label) in enumerate(
                (("L", tr("Left")), ("R", tr("Right")),
                 ("T", tr("Top")), ("B", tr("Bottom")))):
            grid.addWidget(QLabel(label, self), 0, col, Qt.AlignmentFlag.AlignHCenter)
            sb = NoScrollSpinBox(self)
            sb.setRange(0, 100)
            sb.setSuffix(" mm")
            sb.valueChanged.connect(self._on_margin_field_changed)
            self._margin_fields[key] = sb
            grid.addWidget(sb, 1, col)
        v.addLayout(grid)
        v.addStretch()

        # React to combo changes (load that combo's values).
        self._margin_instr.currentIndexChanged.connect(self._load_margin_combo)
        self._margin_paper.currentIndexChanged.connect(self._load_margin_combo)
        self._margin_desc.textChanged.connect(self._on_margin_desc_changed)
        # Default selection → i1Pro A4 Landscape if present.
        self._margin_paper.setCurrentText("A4 Landscape")
        self._loading_margin_combo = False
        self._load_margin_combo()
        return page

    def _current_margin_key(self) -> str:
        from core.settings import margin_combo_key

        instr = self._margin_instr.currentText()
        paper_orient = self._margin_paper.currentText()
        # paper_orient is "A4 Landscape" → split into paper + orientation
        parts = paper_orient.rsplit(" ", 1)
        paper, orient = (parts[0], parts[1]) if len(parts) == 2 else (paper_orient, "")
        return margin_combo_key(instr, paper, orient)

    def _load_margin_combo(self) -> None:
        """Populate the description + L/R/T/B fields from the selected combo."""
        self._loading_margin_combo = True
        entry = self._margin_table.get(self._current_margin_key(), {})
        self._margin_desc.setText(str(entry.get("desc", "")))
        for key, sb in self._margin_fields.items():
            try:
                sb.setValue(int(round(float(entry.get(key, 0)))))
            except (TypeError, ValueError):
                sb.setValue(0)
        self._loading_margin_combo = False

    def _on_margin_field_changed(self) -> None:
        if getattr(self, "_loading_margin_combo", False):
            return
        self._commit_margin_combo()

    def _on_margin_desc_changed(self) -> None:
        if getattr(self, "_loading_margin_combo", False):
            return
        self._commit_margin_combo()

    def _commit_margin_combo(self) -> None:
        """Write the visible fields back into the in-memory table.

        A combo with all-zero margins and no description is dropped (treated as
        "no thresholds defined") so the inspector skips it cleanly.
        """
        key = self._current_margin_key()
        vals = {k: sb.value() for k, sb in self._margin_fields.items()}
        desc = self._margin_desc.text().strip()
        if not any(vals.values()) and not desc:
            self._margin_table.pop(key, None)
            return
        entry = {k: v for k, v in vals.items()}
        entry["desc"] = desc
        self._margin_table[key] = entry

    def _sync_margin_notify_enabled(self) -> None:
        """Notify-on-violation is meaningless when the frame is hidden."""
        self._margin_notify_check.setEnabled(self._margin_show_check.isChecked())

    def _sync_print_path_options(self) -> None:
        """Grey out the options that only apply to ChromIQ's own lp pipeline
        while the macOS print dialog is selected — that dialog is its own
        confirmation step, and no PS→PDF/TIFF fallback runs on its path."""
        if native_print_supported():
            lp_path_active = not self._native_print_check.isChecked()
            self._confirm_print_check.setEnabled(lp_path_active)
            self._pdf_fallback_check.setEnabled(lp_path_active)

    def _load_settings(self) -> None:
        s = self._settings
        self._argyll_edit.setText(s.get("argyll_bin_path", default_argyll_bin_dir()))
        self._folder_edit.setText(s.get("custom_output_path", ""))
        self._restore_tab_check.setChecked(s.get("restore_last_tab", True))
        self._restore_session_check.setChecked(bool(s.get("restore_last_session", False)))
        self._update_notify_check.setChecked(bool(s.get("update_notify", True)))
        self._themed_colors_check.setChecked(bool(s.get("gamut_themed_colors", True)))
        self._cal_mode_check.setChecked(bool(s.get("calibration_mode", False)))
        self._chromiq_refine_check.setChecked(bool(s.get("chromiq_refinement", False)))
        self._averaging_check.setChecked(bool(s.get("averaging_enabled", False)))
        self._native_print_check.setChecked(bool(s.get("use_native_print_dialog", False)))
        self._pdf_fallback_check.setChecked(bool(s.get("pdf_print_fallback", False)))
        self._confirm_print_check.setChecked(bool(s.get("confirm_before_printing", True)))
        self._sync_print_path_options()
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
        # Language: restart-to-apply, no preview — just select the saved code.
        lang = str(s.get("language", "en"))
        lang_idx = self._language_combo.findData(lang)
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        self._language_combo.blockSignals(False)
        self._language_restart_hint.setVisible(False)
        # Margin inspector behaviour checkboxes (the per-combo table is loaded
        # into the tab's own working copy in _build_margin_thresholds_tab).
        self._margin_show_check.setChecked(bool(s.get("margin_inspector_show", True)))
        self._margin_notify_check.setChecked(bool(s.get("margin_violation_notify", True)))
        self._sync_margin_notify_enabled()

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
        # Shared with the Tools dialogs so every dialog highlights controls the
        # same neutral way (checkboxes, radios and the focus ring on text/number/
        # combo inputs).
        from ui.dialogs.tools_dialogs import neutral_controls_qss
        # neutral_controls_qss restyles :checked indicators in the neutral colour,
        # which would otherwise keep a *disabled* checked box looking active. Add a
        # higher-specificity :checked:disabled rule so it greys out like the rest
        # of the app (matching the global QCheckBox::indicator:disabled greys).
        dis_bg, dis_border = (
            ("#eeece8", "#d0ccc6") if mode == "light" else ("#1f1f1f", "#3a3a3a")
        )
        disabled_qss = (
            f"QCheckBox::indicator:checked:disabled {{"
            f" background: {dis_bg}; border-color: {dis_border}; }}"
        )
        self.setStyleSheet(neutral_controls_qss(indicator) + disabled_qss)
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

    def _on_language_changed(self, _index: int) -> None:
        # Language is restart-to-apply (strings are translated at widget
        # construction) — just surface the hint when the pick differs from
        # what's persisted.
        picked = str(self._language_combo.currentData())
        stored = str(self._settings.get("language", "en"))
        self._language_restart_hint.setVisible(picked != stored)

    def reject(self) -> None:  # type: ignore[override]
        # Revert any live theme preview to whatever was persisted on open.
        # Only when the theme was actually previewed to something different —
        # apply_appearance re-applies the app-wide stylesheet and re-polishes
        # every widget, which is slow, so skip it when nothing changed.
        original = getattr(self, "_appearance_original", None)
        if original is not None and str(self._appearance_combo.currentData()) != original:
            from ui.theme import apply_appearance
            app = QApplication.instance()
            if app is not None:
                apply_appearance(app, self.parent(), original)
        super().reject()

    def _save_and_close(self) -> None:
        s = self._settings
        s.set("argyll_bin_path",       self._argyll_edit.text().strip())
        s.set("custom_output_path",    self._folder_edit.text().strip())
        s.set("restore_last_tab",          self._restore_tab_check.isChecked())
        s.set("restore_last_session",      self._restore_session_check.isChecked())
        s.set("update_notify",             self._update_notify_check.isChecked())
        s.set("gamut_themed_colors",       self._themed_colors_check.isChecked())
        s.set("calibration_mode",          self._cal_mode_check.isChecked())
        s.set("chromiq_refinement",        self._chromiq_refine_check.isChecked())
        s.set("averaging_enabled",         self._averaging_check.isChecked())
        s.set("use_native_print_dialog",   self._native_print_check.isChecked())
        s.set("pdf_print_fallback",        self._pdf_fallback_check.isChecked())
        s.set("confirm_before_printing",   self._confirm_print_check.isChecked())
        s.set("appearance",                str(self._appearance_combo.currentData()))
        s.set("language",                  str(self._language_combo.currentData()))
        s.set("i1pro_default_preset",      str(self._i1pro_preset_combo.currentData()))
        s.set("i1pro_chromiq_clip_style",  self._chromiq_clip_check.isChecked())
        s.set("grey_ramp_reference",       int(self._grey_ref_spin.value()))
        # Margin inspector: behaviour flags + the per-combo threshold table.
        self._commit_margin_combo()   # flush the currently-shown combo's edits
        s.set("margin_inspector_show",     self._margin_show_check.isChecked())
        s.set("margin_violation_notify",   self._margin_notify_check.isChecked())
        from core.settings import serialize_margin_thresholds
        s.set("margin_thresholds", serialize_margin_thresholds(self._margin_table))
        log.info("Settings saved")
        self.accept()

    def _browse_argyll(self) -> None:
        d = open_dir_dialog(
            self, tr("Select ArgyllCMS bin directory"),
            start_dir=self._argyll_edit.text() or "/Applications",
        )
        if d:
            self._argyll_edit.setText(d)

    def _browse_folder(self) -> None:
        d = open_dir_dialog(
            self, tr("Select output folder"),
            start_dir=self._folder_edit.text() or str(Path.home()),
        )
        if d:
            self._folder_edit.setText(d)

    def _auto_detect(self) -> None:
        detected = find_argyll_bin_path()
        if detected:
            self._argyll_edit.setText(str(detected))
            self._argyll_status.setStyleSheet("color: #4caf50;")
            self._argyll_status.setText(tr("Auto-detected at {detected}").format(detected=detected))
        else:
            self._argyll_status.setStyleSheet("color: #ff5252;")
            self._argyll_status.setText(
                tr("ArgyllCMS not found in any known location. "
                "Install it or set the path manually.")
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
                        stdin=subprocess.DEVNULL,
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
            tr("Opening argyllcms.com — download the latest version ({hint}), then unpack and set the bin path above.").format(hint=hint)
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
            dlg.setWindowTitle(tr("Install USB Driver"))
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
            refresh_btn = btn_box.addButton(tr("Refresh"), QDialogButtonBox.ButtonRole.ResetRole)
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
            outcome_dlg.setWindowTitle(tr("Driver Installation"))
            outcome_dlg.setMinimumWidth(420)
            ol = QVBoxLayout(outcome_dlg)
            ol.setContentsMargins(24, 20, 24, 20)
            ol.setSpacing(14)
            lbl = QLabel(outcome_text, outcome_dlg)
            lbl.setWordWrap(True)
            ol.addWidget(lbl)
            obox = QDialogButtonBox()
            if offer_zadig:
                zadig_btn = obox.addButton(tr("Try Zadig"), QDialogButtonBox.ButtonRole.AcceptRole)
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
        self._update_btn.setText(tr("Checking…"))
        self._update_status.setText("")

        self._update_checker = UpdateChecker(self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.up_to_date.connect(self._on_up_to_date)
        self._update_checker.check_failed.connect(self._on_update_failed)
        self._update_checker.check_async()

    def _on_update_available(self, latest: str) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText(tr("Check for Updates"))
        self._update_status.setStyleSheet("font-size: 11px; color: #e67e00;")
        self._update_status.setText(
            tr("{latest} available — <a href=\"{_RELEASES_PAGE}\">open GitHub Releases</a>").format(latest=latest, _RELEASES_PAGE=_RELEASES_PAGE)
        )
        self._update_status.setOpenExternalLinks(True)

    def _on_up_to_date(self) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText(tr("Check for Updates"))
        self._update_status.setStyleSheet("font-size: 11px; color: #4caf50;")
        self._update_status.setText(tr("You're up to date."))

    def _on_update_failed(self, reason: str) -> None:
        self._update_btn.setEnabled(True)
        self._update_btn.setText(tr("Check for Updates"))
        self._update_status.setStyleSheet("font-size: 11px; color: #888;")
        self._update_status.setText(tr("Check failed: {reason}").format(reason=reason))
