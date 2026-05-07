"""Tab 4: Create ICC Profile."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.resource_path import resource_path
from ui.tab_header import TabHeader
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox, load_folder_icon, make_browse_button, open_file_dialog, tint_dialog_primary
from ui.spectrum_progress import SpectrumSegmentsBar
from workflow.profile_builder import ProfileBuilder, ProfileParams
from workflow.printcal_runner import PrintcalRunner, PrintcalParams, ChannelTarget
from workflow.applycal_runner import ApplycalRunner

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)

_TAB_COLOR = "#37bcd6"  # Build Profile tab accent
from ui.styles import SPEC_CYAN, TAB_COLORS

_ILLUMINANTS = [
    ("Default (D50)", ""),
    ("D50M2 (D50 with UV filter)", "D50M2"),
    ("D65 (daylight 6500 K)", "D65"),
    ("D65M2 (D65 with UV filter)", "D65M2"),
    ("A (tungsten / incandescent)", "A"),
    ("C (daylight sim., older)", "C"),
    ("F5 (fluorescent CWF)", "F5"),
    ("F8 (fluorescent D50 sim.)", "F8"),
    ("F10 (fluorescent Ultralume)", "F10"),
]

_INTENTS = [
    ("Default", ""),
    ("Perceptual Preferred (p)", "p"),
    ("Perceptual Appearance (pa)", "pa"),
    ("Luminance Preserving Perceptual (lp)", "lp"),
    ("Relative Colorimetric / ICC (r)", "r"),
    ("Lab White-point Matched (rl)", "rl"),
    ("Saturation (ms)", "ms"),
    ("Enhanced Saturation / ICC (s)", "s"),
    ("Absolute Colorimetric (a)", "a"),
    ("Absolute + white scaling (aw)", "aw"),
    ("Absolute Appearance (aa)", "aa"),
    ("Lab Colorimetric (al)", "al"),
]


class TabProfile(QWidget):
    """Step 4: build and install ICC profile from .ti3 measurements."""

    profile_built    = pyqtSignal(Path, Path)   # (ti3_path, icc_path)
    check_requested  = pyqtSignal()             # user clicked "Check Quality" in the result dialog
    profile_active   = pyqtSignal(bool)         # True while colprof is running, False when done
    ti2_found           = pyqtSignal(Path)  # emitted when a matching .ti2 exists next to the loaded .ti3
    ti3_manually_loaded = pyqtSignal()      # emitted when the user manually loads a .ti3 file
    cal_file_created    = pyqtSignal(Path)  # printcal done; fill -K/-I silently, stay on tab
    cal_chart_requested = pyqtSignal(Path)  # user chose "Go to Create Chart" in result dialog

    def __init__(
        self,
        runner: "ArgyllRunner",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner        = runner
        self._settings      = settings
        self._builder       = ProfileBuilder(runner)
        self._printcal_runner = PrintcalRunner(runner, settings)
        self._applycal_runner = ApplycalRunner(runner, settings)
        self._ti3_path: Path | None = None
        self._icc_path: Path | None = None
        self._cal_ti3_path: Path | None = None

        self._build_ui()
        self._restore_defaults()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _switch_mode(self, mode: str) -> None:
        if mode == "guided":
            self._stack.setCurrentIndex(0)
            self._guided_btn.setChecked(True)
            self._manual_btn.setChecked(False)
        else:
            self._stack.setCurrentIndex(1)
            self._guided_btn.setChecked(False)
            self._manual_btn.setChecked(True)
        self._build_state_box.setVisible(mode == "guided")

    def _current_mode(self) -> str:
        return "guided" if self._stack.currentIndex() == 0 else "manual"

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        self._header = TabHeader(
            "STEP 04 · CREATE ICC PROFILE", "Build ICC profile", "#37bcd6", self
        )
        root.addWidget(self._header)

        _mode_font = QFont("Menlo", 11, QFont.Weight.Medium)

        # --- Calibration mode row: 3 named buttons (hidden in normal mode) ---
        self._cal_mode_row_widget = QWidget(self)
        cal_mode_row = QHBoxLayout(self._cal_mode_row_widget)
        cal_mode_row.setContentsMargins(0, 0, 0, 0)
        self._cal_create_btn  = QPushButton("CREATE CALIBRATION FILE", self._cal_mode_row_widget)
        self._cal_profile_btn = QPushButton("BUILD PROFILE",           self._cal_mode_row_widget)
        self._cal_apply_btn   = QPushButton("APPLY CALIBRATION",       self._cal_mode_row_widget)
        for _btn in (self._cal_create_btn, self._cal_profile_btn, self._cal_apply_btn):
            _btn.setCheckable(True)
            _btn.setObjectName("mode_btn")
            _btn.setFont(_mode_font)
        self._cal_profile_btn.setChecked(True)
        # page 0 = colprof, page 1 = printcal, page 2 = applycal
        self._cal_create_btn.clicked.connect(lambda: self._switch_cal_mode(1))
        self._cal_profile_btn.clicked.connect(lambda: self._switch_cal_mode(0))
        self._cal_apply_btn.clicked.connect(lambda: self._switch_cal_mode(2))
        cal_mode_row.addWidget(self._cal_create_btn)
        cal_mode_row.addWidget(self._cal_profile_btn)
        cal_mode_row.addWidget(self._cal_apply_btn)
        cal_mode_row.addStretch()
        self._cal_mode_row_widget.setVisible(False)
        root.addWidget(self._cal_mode_row_widget)

        # --- Normal mode row: GUIDED / MANUAL (hidden in calibration mode) ---
        self._mode_row_widget = QWidget(self)
        mode_row = QHBoxLayout(self._mode_row_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        self._guided_btn = QPushButton("GUIDED", self._mode_row_widget)
        self._guided_btn.setCheckable(True)
        self._guided_btn.setChecked(True)
        self._guided_btn.setObjectName("mode_btn")
        self._guided_btn.setFont(_mode_font)
        self._manual_btn = QPushButton("MANUAL", self._mode_row_widget)
        self._manual_btn.setCheckable(True)
        self._manual_btn.setObjectName("mode_btn")
        self._manual_btn.setFont(_mode_font)
        self._guided_btn.clicked.connect(lambda: self._switch_mode("guided"))
        self._manual_btn.clicked.connect(lambda: self._switch_mode("manual"))
        mode_row.addWidget(self._guided_btn)
        mode_row.addWidget(self._manual_btn)
        mode_row.addStretch()
        root.addWidget(self._mode_row_widget)

        # --- Outer stack: page 0 = colprof, page 1 = printcal, page 2 = applycal ---
        self._outer_stack = QStackedWidget(self)

        # Page 0 — colprof container (shown in both normal and cal "Build Profile" modes)
        colprof_container = QWidget()
        cc = QVBoxLayout(colprof_container)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(8)

        self._file_grp = file_grp = QGroupBox("Measurement Data (.ti3)", colprof_container)
        fg = QHBoxLayout(file_grp)
        self._load_btn = QPushButton("Load .ti3 file…", file_grp)
        self._load_btn.setIcon(load_folder_icon("folder_build"))
        self._load_btn.clicked.connect(self._on_load_ti3)
        self._file_lbl = QLabel("No file selected", file_grp)
        self._file_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        self._file_lbl.setWordWrap(True)
        fg.addWidget(self._load_btn)
        fg.addWidget(self._file_lbl, stretch=1)
        cc.addWidget(file_grp)

        self._stack = QStackedWidget(colprof_container)
        self._guided_panel = self._make_guided_panel()
        self._manual_panel = self._make_manual_panel()
        self._stack.addWidget(self._guided_panel)
        self._stack.addWidget(self._manual_panel)
        cc.addWidget(self._stack, stretch=1)

        build_box = QGroupBox(colprof_container)
        build_box.setStyleSheet(
            "QGroupBox { margin-top: 0px; padding: 14px 8px 12px 8px;"
            " border: 1px solid #333333; border-radius: 4px; }"
        )
        build_layout = QVBoxLayout(build_box)
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.setSpacing(4)
        self._build_headline = QLabel(
            f'Ready to build<span style="color: {SPEC_CYAN}; font-style: italic;">?</span>',
            build_box,
        )
        self._build_headline.setTextFormat(Qt.TextFormat.RichText)
        self._build_headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._build_headline.setStyleSheet(
            "color: #ffffff; background: transparent;"
            " font-family: Georgia; font-size: 28pt;"
        )
        build_layout.addWidget(self._build_headline)
        self._build_subtext = QLabel("Awaiting your command.", build_box)
        self._build_subtext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._build_subtext.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 9pt; font-weight: 300;"
        )
        build_layout.addWidget(self._build_subtext)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 6, 0, 0)
        bar_row.setSpacing(0)
        bar_row.addStretch()
        for _color in TAB_COLORS:
            _seg = QFrame(build_box)
            _seg.setFixedSize(22, 2)
            _seg.setStyleSheet(f"background-color: {_color}; border: none;")
            bar_row.addWidget(_seg)
        bar_row.addStretch()
        build_layout.addLayout(bar_row)
        self._build_state_box = build_box
        cc.addWidget(build_box)

        btn_row = QHBoxLayout()
        self._build_btn = QPushButton("Build Profile", colprof_container)
        self._build_btn.setObjectName("primary")
        self._build_btn.setFixedHeight(36)
        self._build_btn.setEnabled(False)
        self._build_btn.clicked.connect(self._on_build)
        self._install_btn = QPushButton("Install Profile", colprof_container)
        self._install_btn.setFixedHeight(36)
        self._install_btn.setEnabled(False)
        self._install_btn.clicked.connect(self._on_install)
        self._save_defaults_btn = QPushButton("Save as Defaults", colprof_container)
        self._save_defaults_btn.setFixedHeight(36)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)
        btn_row.addWidget(self._build_btn)
        btn_row.addWidget(self._install_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        cc.addLayout(btn_row)

        self._progress_bar = SpectrumSegmentsBar(colprof_container)
        self._progress_bar.set_label("Build Profile", "")
        self._progress_bar.set_value(0)
        cc.addWidget(self._progress_bar)

        self._log = QPlainTextEdit(colprof_container)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(67)
        self._log.setPlaceholderText("colprof output will appear here…")
        cc.addWidget(self._log)

        self._outer_stack.addWidget(colprof_container)       # page 0
        self._outer_stack.addWidget(self._make_printcal_section())  # page 1
        self._outer_stack.addWidget(self._make_applycal_section())  # page 2

        root.addWidget(self._outer_stack, stretch=1)

    # ------------------------------------------------------------------
    # Calibration mode
    # ------------------------------------------------------------------

    def set_calibration_mode(self, enabled: bool) -> None:
        """Switch between normal (GUIDED/MANUAL) and calibration (3-module) mode."""
        self._cal_mode_row_widget.setVisible(enabled)
        self._mode_row_widget.setVisible(not enabled)
        if enabled:
            self._header.set_texts("STEP 04 · CALIBRATE & PROFILE", "Calibration & Profiling")
            self._switch_mode("manual")
            self._switch_cal_mode(0)  # default to Build Profile
        else:
            self._header.set_texts("STEP 04 · CREATE ICC PROFILE", "Build ICC profile")
            self._outer_stack.setCurrentIndex(0)

    def _switch_cal_mode(self, page: int) -> None:
        """Switch the outer stack page and update the 3 calibration mode buttons.
        page 0 = colprof (Build Profile), 1 = printcal, 2 = applycal."""
        self._outer_stack.setCurrentIndex(page)
        self._cal_create_btn.setChecked(page == 1)
        self._cal_profile_btn.setChecked(page == 0)
        self._cal_apply_btn.setChecked(page == 2)
        if page == 2:
            self._ac_try_autofill()

    def _ac_try_autofill(self) -> None:
        """Scan the working folder and pre-fill applycal fields if matching files exist."""
        work_dir: Path | None = None
        for candidate in (self._cal_ti3_path, self._ti3_path):
            if candidate and candidate.exists():
                work_dir = candidate.parent
                break
        if work_dir is None:
            raw = self._settings.get("custom_output_path", "")
            if raw:
                work_dir = Path(raw)
        if work_dir is None or not work_dir.is_dir():
            return

        folder_name = work_dir.name

        if not self._ac_cal_edit.text().strip():
            cal_candidate = work_dir / f"cal_{folder_name}.cal"
            if cal_candidate.exists():
                self._ac_cal_edit.setText(str(cal_candidate))

        if not self._ac_in_edit.text().strip():
            for ext in (".icc", ".icm"):
                icc_candidate = work_dir / f"{folder_name}{ext}"
                if icc_candidate.exists():
                    self._ac_in_edit.setText(str(icc_candidate))
                    break

    @property
    def ti3_path(self) -> Path | None:
        return self._ti3_path

    @property
    def icc_path(self) -> Path | None:
        return self._icc_path

    @property
    def cal_ti3_path(self) -> Path | None:
        return self._cal_ti3_path

    def set_icc_path(self, path: Path) -> None:
        self._icc_path = path

    def set_cal_ti3_path(self, ti3: Path) -> None:
        """Receive a cal_*.ti3 from the measure tab, pre-fill printcal, and switch to it."""
        self._cal_ti3_path = ti3
        self._pc_ti3_lbl.setText(str(ti3))
        self._pc_ti3_lbl.setStyleSheet("color: #e6e6e6; font-size: 11px;")
        self._pc_run_btn.setEnabled(True)
        self._switch_cal_mode(1)  # jump straight to Create Calibration File
        log.info("Printcal input set to %s", ti3)

    # ------------------------------------------------------------------
    # Printcal section
    # ------------------------------------------------------------------

    # ---- Channel target row helper -------------------------------------------

    class _ChannelRow:
        """One row in the channel target overrides grid."""
        def __init__(self, ch: int, label: str, parent: QWidget) -> None:
            self.ch = ch
            self.enabled_cb = QCheckBox(label, parent)
            self.enabled_cb.setFixedWidth(60)

            def _spin(lo: float, hi: float, decimals: int, step: float) -> NoScrollDoubleSpinBox:
                s = NoScrollDoubleSpinBox(parent)
                s.setRange(lo, hi)
                s.setDecimals(decimals)
                s.setSingleStep(step)
                s.setFixedWidth(70)
                s.setEnabled(False)
                s.setSpecialValueText("—")
                s.setMinimum(lo - step)   # one step below range = "not set"
                s.setValue(lo - step)
                return s

            self.max_spin  = _spin(0.0, 100.0, 1, 1.0)
            self.dev_spin  = _spin(0.0, 100.0, 1, 1.0)
            self.white_spin = _spin(0.0,  20.0, 2, 0.1)
            self.t50_spin  = _spin(0.0, 100.0, 1, 1.0)

            self.enabled_cb.toggled.connect(self._on_toggle)

        def _on_toggle(self, checked: bool) -> None:
            for sp in (self.max_spin, self.dev_spin, self.white_spin, self.t50_spin):
                sp.setEnabled(checked)

        def channel_target(self) -> "ChannelTarget | None":
            if not self.enabled_cb.isChecked():
                return None
            def _val(sp: NoScrollDoubleSpinBox) -> float | None:
                # value below minimum = "not set"
                return sp.value() if sp.value() >= sp.minimum() + sp.singleStep() * 0.5 else None
            return ChannelTarget(
                ch=self.ch,
                max_pct=_val(self.max_spin),
                dev_pct=_val(self.dev_spin),
                white_de=_val(self.white_spin),
                t50_pct=_val(self.t50_spin),
            )

        def restore(self, data: dict) -> None:
            self.enabled_cb.setChecked(data.get("enabled", False))
            for attr, key in (
                ("max_spin",   "max_pct"),
                ("dev_spin",   "dev_pct"),
                ("white_spin", "white_de"),
                ("t50_spin",   "t50_pct"),
            ):
                v = data.get(key)
                if v is not None:
                    getattr(self, attr).setValue(v)

        def save(self) -> dict:
            def _v(sp: NoScrollDoubleSpinBox) -> float | None:
                return sp.value() if sp.value() >= sp.minimum() + sp.singleStep() * 0.5 else None
            return {
                "enabled":  self.enabled_cb.isChecked(),
                "max_pct":  _v(self.max_spin),
                "dev_pct":  _v(self.dev_spin),
                "white_de": _v(self.white_spin),
                "t50_pct":  _v(self.t50_spin),
            }

    # --------------------------------------------------------------------------

    def _make_printcal_section(self) -> QWidget:
        container = QWidget(self)
        cc = QVBoxLayout(container)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(8)

        # ---- Scrollable groupbox ----
        scroll = QScrollArea(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        grp_wrapper = QWidget()
        gw_layout = QVBoxLayout(grp_wrapper)
        gw_layout.setContentsMargins(0, 0, 0, 0)
        gw_layout.setSpacing(8)

        # ---- Measurement Data section ----
        grp_ti3 = QGroupBox("Measurement Data (.ti3)", grp_wrapper)
        ti3_g = QVBoxLayout(grp_ti3)
        ti3_g.setSpacing(8)
        in_row = QHBoxLayout()
        self._pc_load_btn = QPushButton("Load cal_*.ti3…", grp_ti3)
        self._pc_load_btn.setIcon(load_folder_icon("folder_build"))
        self._pc_load_btn.clicked.connect(self._pc_browse_ti3)
        self._pc_ti3_lbl = QLabel("No file selected — measure a calibration target first.", grp_ti3)
        self._pc_ti3_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        self._pc_ti3_lbl.setWordWrap(True)
        in_row.addWidget(self._pc_load_btn)
        in_row.addWidget(self._pc_ti3_lbl, stretch=1)
        ti3_g.addLayout(in_row)
        gw_layout.addWidget(grp_ti3)

        # ---- Calibration Metadata section ----
        self._build_pc_metadata_group(gw_layout, grp_wrapper)

        grp = QGroupBox("Create Calibration File  (printcal)", grp_wrapper)
        g = QVBoxLayout(grp)
        g.setSpacing(8)

        # ---- Mode ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:", grp))
        self._pc_mode_combo = NoScrollComboBox(grp)
        self._pc_mode_combo.addItem("Initial calibration  (creates fresh .cal)", "initial")
        self._pc_mode_combo.addItem("Re-calibrate  (refine existing .cal)", "recalibrate")
        self._pc_mode_combo.addItem("Verify  (check against existing .cal)", "verify")
        self._pc_mode_combo.addItem("Imitation target  (null cal from .ti3)", "imitation")
        self._pc_mode_combo.currentIndexChanged.connect(self._pc_update_mode_vis)
        mode_row.addWidget(self._pc_mode_combo, stretch=1)
        mode_row.addWidget(TooltipButton(
            "Calibration Mode",
            "Initial calibration (-i): creates a brand-new .cal file from your\n"
            "calibration target measurement. Use this the first time.\n\n"
            "Re-calibrate (-r): refines an existing .cal by comparing new\n"
            "measurements to the previous target. Useful for keeping a\n"
            "printer consistent over time.\n\n"
            "Verify (-e): checks how well a printer still matches a prior .cal\n"
            "without writing any new files.\n\n"
            "Imitation target (-I): creates a calibration target from an existing\n"
            ".ti3 using a null (identity) calibration. Useful for deriving a\n"
            "calibration target when no previous .cal exists.",
            grp,
            min_width=480,
        ))
        g.addLayout(mode_row)

        # ---- Previous .cal (shown for recal / verify) ----
        self._pc_prev_widget = QWidget(grp)
        prev_row = QHBoxLayout(self._pc_prev_widget)
        prev_row.setContentsMargins(0, 0, 0, 0)
        prev_row.addWidget(QLabel("Previous .cal file:", self._pc_prev_widget))
        self._pc_prev_edit = QLineEdit(self._pc_prev_widget)
        self._pc_prev_edit.setPlaceholderText("Path to previous calibration file…")
        prev_browse = make_browse_button(self._pc_prev_widget, "Select previous .cal file", icon="folder_build")
        prev_browse.clicked.connect(self._pc_browse_prev)
        prev_row.addWidget(self._pc_prev_edit, stretch=1)
        prev_row.addWidget(prev_browse)
        self._pc_prev_widget.setVisible(False)
        g.addWidget(self._pc_prev_widget)

        # ---- Dry run ----
        dry_row = QHBoxLayout()
        self._pc_dry_run_cb = QCheckBox("Dry run (-d)  —  simulate without writing any files", grp)
        dry_row.addWidget(self._pc_dry_run_cb)
        dry_row.addStretch()
        dry_row.addWidget(TooltipButton(
            "Dry Run (-d)",
            "Runs printcal through all its calculations without writing the .cal\n"
            "file to disk. Use this to check that your input file and settings are\n"
            "correct and to preview what targets would be computed — before\n"
            "committing to a real calibration run.",
            grp,
        ))
        g.addLayout(dry_row)

        # ---- Smoothing ----
        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("Smoothing:", grp))
        self._pc_smooth_spin = NoScrollDoubleSpinBox(grp)
        self._pc_smooth_spin.setRange(0.1, 10.0)
        self._pc_smooth_spin.setSingleStep(0.1)
        self._pc_smooth_spin.setDecimals(1)
        self._pc_smooth_spin.setValue(1.0)
        self._pc_smooth_spin.setMaximumWidth(80)
        smooth_row.addWidget(self._pc_smooth_spin)
        smooth_row.addStretch()
        smooth_row.addWidget(TooltipButton(
            "Curve Smoothing (-s)",
            "Extra smoothing applied to the calibration curves.\n"
            "Default 1.0 suits most printers.\n"
            "Raise (e.g. 2–5) if the printer has noisy tone response.",
            grp,
        ))
        g.addLayout(smooth_row)

        # ---- Verbosity ----
        verb_row = QHBoxLayout()
        verb_row.addWidget(QLabel("Verbosity:", grp))
        self._pc_verb_spin = NoScrollSpinBox(grp)
        self._pc_verb_spin.setRange(0, 3)
        self._pc_verb_spin.setValue(1)
        self._pc_verb_spin.setMaximumWidth(60)
        verb_row.addWidget(self._pc_verb_spin)
        verb_row.addStretch()
        verb_row.addWidget(TooltipButton(
            "Verbosity (-v)",
            "Controls how much detail printcal writes to the log.\n"
            "0 = silent, 1 = normal, 2–3 = verbose/debug.",
            grp,
        ))
        g.addLayout(verb_row)

        gw_layout.addWidget(grp)

        # ---- Initial target overrides (hidden for recalibrate/verify) ----
        _targets_inner = self._make_channel_targets_widget(grp_wrapper)
        self._pc_targets_widget = QGroupBox("Initial Target Overrides", grp_wrapper)
        _tg = QVBoxLayout(self._pc_targets_widget)
        _tg.addWidget(_targets_inner)
        gw_layout.addWidget(self._pc_targets_widget)
        gw_layout.addStretch()
        scroll.setWidget(grp_wrapper)
        cc.addWidget(scroll, stretch=1)

        # ---- Button row (outside scroll area) ----
        btn_row = QHBoxLayout()
        self._pc_run_btn = QPushButton("Create Calibration File", container)
        self._pc_run_btn.setObjectName("primary")
        self._pc_run_btn.setFixedHeight(36)
        self._pc_run_btn.setEnabled(False)
        self._pc_run_btn.clicked.connect(self._on_printcal_run)
        self._pc_save_defaults_btn = QPushButton("Save as Defaults", container)
        self._pc_save_defaults_btn.setFixedHeight(36)
        self._pc_save_defaults_btn.clicked.connect(self._on_pc_save_defaults)
        btn_row.addWidget(self._pc_run_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._pc_save_defaults_btn)
        cc.addLayout(btn_row)

        # ---- Progress bar (outside scroll area) ----
        self._pc_progress = SpectrumSegmentsBar(container)
        self._pc_progress.set_label("Create Calibration File", "")
        self._pc_progress.set_value(0)
        cc.addWidget(self._pc_progress)

        # ---- Log (outside scroll area) ----
        self._pc_log = QPlainTextEdit(container)
        self._pc_log.setObjectName("log")
        self._pc_log.setReadOnly(True)
        self._pc_log.setMaximumHeight(67)
        self._pc_log.setPlaceholderText("printcal output will appear here…")
        cc.addWidget(self._pc_log)

        s = self._settings
        self._pc_smooth_spin.setValue(float(s.get("printcal_smoothing", 1.0)))
        self._pc_verb_spin.setValue(int(s.get("printcal_verbosity", 1)))
        self._pc_dry_run_cb.setChecked(bool(s.get("printcal_dry_run", False)))
        saved_mode = s.get("printcal_mode", "initial")
        idx = self._pc_mode_combo.findData(saved_mode)
        if idx >= 0:
            self._pc_mode_combo.setCurrentIndex(idx)
        saved_targets = s.get("printcal_channel_targets", "[]")
        try:
            targets_data = json.loads(saved_targets) if isinstance(saved_targets, str) else saved_targets
        except (json.JSONDecodeError, TypeError):
            targets_data = []
        for row in self._pc_channel_rows:
            for td in targets_data:
                if isinstance(td, dict) and td.get("ch") == row.ch:
                    row.restore(td)
                    break
        # Auto-show extended channels if any ch4-7 rows were restored as enabled
        if any(row.enabled_cb.isChecked() for row in self._pc_channel_rows if row.ch >= 4):
            self._pc_extended_cb.setChecked(True)

        # Sync visibility for the current mode (in case default index didn't change)
        self._pc_update_mode_vis()

        return container

    def _make_channel_targets_widget(self, parent: QWidget) -> QWidget:
        """Build the 'Initial Target Overrides' section with per-channel spinboxes.

        Layout (all rows in outer VBoxLayout):
          ① header label + stretch + section TooltipButton  (QHBoxLayout)
          ② standard grid  — column headers + C/M/Y/K rows  (QGridLayout)
          ③ extended disclosure + stretch + ext TooltipButton (QHBoxLayout)
          ④ extended grid   — Ch4–Ch7 rows, hidden by default (QGridLayout)

        Both grids use identical column stretch so their columns stay aligned.
        The two TooltipButtons are in addStretch() rows, matching the
        smoothing / verbosity / dry-run pattern above.
        """
        w = QWidget(parent)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # ① Tooltip row -------------------------------------------------------
        tip_row = QHBoxLayout()
        tip_row.addStretch()
        tip_row.addWidget(TooltipButton(
            "Initial Target Overrides",
            "These optional settings let you override the targets printcal computes\n"
            "automatically when building calibration curves.\n\n"
            "Enable a channel row with its checkbox, then fill in only the values\n"
            "you want to change — leave a spinbox at '—' to let printcal decide\n"
            "that value automatically from your measurement data.\n\n"
            "These overrides apply only in Initial calibration and Imitation target\n"
            "modes. They have no effect in Re-calibrate or Verify mode.",
            w,
            min_width=440,
        ))
        outer.addLayout(tip_row)

        # Shared helper: apply identical column stretch to both grids
        def _set_col_stretch(g: QGridLayout) -> None:
            g.setColumnStretch(0, 0)
            for c in range(1, 5):
                g.setColumnStretch(c, 1)

        # Shared helper: add one channel row to a given grid/parent
        def _make_row(label: str, ch: int, grid: QGridLayout,
                      grid_parent: QWidget, grid_row: int) -> "TabProfile._ChannelRow":
            row = TabProfile._ChannelRow(ch, label, grid_parent)
            self._pc_channel_rows.append(row)
            grid.addWidget(row.enabled_cb, grid_row, 0)
            for col, sp in enumerate(
                (row.max_spin, row.dev_spin, row.white_spin, row.t50_spin), start=1
            ):
                grid.addWidget(sp, grid_row, col)
            return row

        # ② Standard grid (header row + C/M/Y/K) -----------------------------
        _col_headers = [
            ("Channel",       ""),
            ("Max % (-x)",
             "Maximum device value for this channel (0–100 %).\n"
             "printcal determines this automatically from your measurements.\n"
             "Override it only if you need to enforce a specific ink limit.\n"
             "Example: 85 caps the darkest patch at 85 % ink."),
            ("Dev % (-m)",
             "Initial target as a percentage of the automatic maximum (0–100 %).\n"
             "Lets you back off from the auto ink limit without specifying an\n"
             "absolute value. Example: 90 means 'target 90 % of the auto max'."),
            ("White ΔE (-n)",
             "Minimum deltaE the white point must deviate before a correction\n"
             "is applied to the lightest end of this channel.\n"
             "Lower values (e.g. 1.0) correct even small white-point shifts;\n"
             "higher values leave the white end untouched."),
            ("50% (-t)",
             "Target device percentage for the 50 % tone step (0–100 %).\n"
             "For a perfectly linear printer this would be 50.\n"
             "Adjust it to shift the mid-tone balance of the calibration curve\n"
             "if your printer's 50 % response is consistently too dark or too light."),
        ]

        std_widget = QWidget(w)
        std_grid = QGridLayout(std_widget)
        std_grid.setContentsMargins(0, 0, 0, 0)
        std_grid.setHorizontalSpacing(4)
        std_grid.setVerticalSpacing(4)
        _set_col_stretch(std_grid)

        for col, (text, tip) in enumerate(_col_headers):
            lbl = QLabel(text, std_widget)
            lbl.setStyleSheet("color: #707070; font-size: 10px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if tip:
                lbl.setToolTip(tip)
            std_grid.addWidget(lbl, 0, col)

        self._pc_channel_rows: list[TabProfile._ChannelRow] = []
        for row_idx, (label, ch) in enumerate([("C", 0), ("M", 1), ("Y", 2), ("K", 3)], start=1):
            _make_row(label, ch, std_grid, std_widget, row_idx)

        outer.addWidget(std_widget)

        # ③ Extended-disclosure row -------------------------------------------
        ext_disc_row = QHBoxLayout()
        self._pc_extended_cb = QCheckBox("Extended inkset channels (Ch4–Ch7)", w)
        self._pc_extended_cb.setStyleSheet("color: #909090; font-size: 11px;")
        ext_disc_row.addWidget(self._pc_extended_cb)
        ext_disc_row.addStretch()
        ext_disc_row.addWidget(TooltipButton(
            "Extended Inkset Channels",
            "Show per-channel controls for printers with more than 4 ink channels\n"
            "(e.g. light cyan, light magenta, or other specialty inks).\n"
            "Channels are numbered from 0; Ch4–Ch7 cover the fifth ink and beyond.",
            w,
        ))
        outer.addLayout(ext_disc_row)

        # ④ Extended grid (Ch4–Ch7), hidden by default -----------------------
        self._pc_ext_grid_widget = QWidget(w)
        ext_grid = QGridLayout(self._pc_ext_grid_widget)
        ext_grid.setContentsMargins(0, 0, 0, 0)
        ext_grid.setHorizontalSpacing(4)
        ext_grid.setVerticalSpacing(4)
        _set_col_stretch(ext_grid)

        for row_idx, (label, ch) in enumerate([("Ch4", 4), ("Ch5", 5), ("Ch6", 6), ("Ch7", 7)]):
            _make_row(label, ch, ext_grid, self._pc_ext_grid_widget, row_idx)

        self._pc_ext_grid_widget.setVisible(False)
        outer.addWidget(self._pc_ext_grid_widget)

        self._pc_extended_cb.toggled.connect(self._pc_ext_grid_widget.setVisible)

        # Keep the old attribute name so restore/save code still works
        self._pc_extended_widgets: list[QWidget] = []  # unused sentinel — toggled via widget

        return w

    def _build_pc_metadata_group(self, layout: QVBoxLayout, parent: QWidget) -> None:
        grp = QGroupBox("Calibration Metadata", parent)
        g = QVBoxLayout(grp)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Description (-D):", grp))
        self._pc_desc_edit = QLineEdit(grp)
        self._pc_desc_edit.setPlaceholderText("e.g. EpsonP900_Cal_2026-04")
        desc_row.addWidget(self._pc_desc_edit, stretch=1)
        desc_row.addWidget(TooltipButton(
            "Description (-D)",
            "Optional description string embedded in the .cal file header.",
            grp,
        ))
        g.addLayout(desc_row)

        for attr, flag, label, placeholder, tip in [
            ("_pc_mfr",   "A", "Manufacturer", "e.g. Epson",    "Manufacturer string embedded in the .cal file header."),
            ("_pc_model", "M", "Model",        "e.g. SC-P900",  "Model string embedded in the .cal file header."),
            ("_pc_copy",  "C", "Copyright",    "e.g. © 2026 …", "Copyright string embedded in the .cal file header."),
        ]:
            check = QCheckBox(f"{label} (-{flag}):", grp)
            edit  = QLineEdit(grp)
            edit.setPlaceholderText(placeholder)
            edit.setEnabled(False)
            check.toggled.connect(edit.setEnabled)
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(edit, stretch=1)
            row.addWidget(TooltipButton(f"-{flag}", tip, grp))
            g.addLayout(row)
            setattr(self, attr + "_check", check)
            setattr(self, attr + "_edit",  edit)

        layout.addWidget(grp)

    def _pc_update_mode_vis(self) -> None:
        mode = self._pc_mode_combo.currentData()
        self._pc_prev_widget.setVisible(mode in ("recalibrate", "verify"))
        # Target overrides only apply to initial / imitation modes
        self._pc_targets_widget.setVisible(mode in ("initial", "imitation"))

    def _pc_browse_ti3(self) -> None:
        p = open_file_dialog(self, "Load calibration measurement", "TI3 files (*.ti3)",
                             extra_path=self._settings.get("custom_output_path", ""))
        if p:
            self._cal_ti3_path = Path(p)
            self._pc_ti3_lbl.setText(p)
            self._pc_ti3_lbl.setStyleSheet("color: #e6e6e6; font-size: 11px;")
            self._pc_run_btn.setEnabled(True)
            if not self._pc_desc_edit.text():
                stem = Path(p).stem
                self._pc_desc_edit.setText(stem[4:] if stem.startswith("cal_") else stem)

    def _pc_browse_prev(self) -> None:
        p = open_file_dialog(self, "Load previous .cal file", "CAL files (*.cal)",
                             extra_path=self._settings.get("custom_output_path", ""))
        if p:
            self._pc_prev_edit.setText(p)

    def _on_pc_save_defaults(self) -> None:
        s = self._settings
        s.set("printcal_smoothing",        self._pc_smooth_spin.value())
        s.set("printcal_verbosity",        self._pc_verb_spin.value())
        s.set("printcal_mode",             self._pc_mode_combo.currentData() or "initial")
        s.set("printcal_dry_run",          self._pc_dry_run_cb.isChecked())
        targets = [{"ch": row.ch, **row.save()} for row in self._pc_channel_rows]
        s.set("printcal_channel_targets",  json.dumps(targets))

    def _on_printcal_run(self) -> None:
        if self._runner.is_running:
            return
        if self._cal_ti3_path is None or not self._cal_ti3_path.exists():
            self._pc_log.setPlainText("[ERROR] No input .ti3 file selected.")
            return

        self._pc_log.clear()
        self._pc_run_btn.setEnabled(False)
        self._pc_progress.set_label("Creating calibration…", "printcal")
        self._pc_progress.set_value(None)
        self._pc_progress.start()

        channel_targets = [
            ct for row in self._pc_channel_rows
            if (ct := row.channel_target()) is not None
        ]

        params = PrintcalParams(
            ti3_path        = self._cal_ti3_path,
            mode            = self._pc_mode_combo.currentData() or "initial",
            prev_cal        = self._pc_prev_edit.text().strip(),
            verbosity       = self._pc_verb_spin.value(),
            smoothing       = self._pc_smooth_spin.value(),
            dry_run         = self._pc_dry_run_cb.isChecked(),
            channel_targets = channel_targets,
            description     = self._pc_desc_edit.text().strip(),
            manufacturer    = self._pc_mfr_edit.text().strip() if self._pc_mfr_check.isChecked() else "",
            model           = self._pc_model_edit.text().strip() if self._pc_model_check.isChecked() else "",
            copyright       = self._pc_copy_edit.text().strip() if self._pc_copy_check.isChecked() else "",
        )

        def _on_line(line: str) -> None:
            self._pc_log.appendPlainText(line)
            self._pc_log.ensureCursorVisible()

        def _on_finish(cal_path: Path | None) -> None:
            self._pc_progress.stop()
            self._pc_progress.set_label("Create Calibration File", "")
            self._pc_progress.set_value(0)
            self._pc_run_btn.setEnabled(True)
            if cal_path is None:
                self._pc_log.appendPlainText("\n[ERROR] printcal failed — see output above.")
                self._pc_log.ensureCursorVisible()
            else:
                self._pc_log.appendPlainText(f"\n[OK] Calibration file created: {cal_path}")
                self._pc_log.ensureCursorVisible()
                self._ac_cal_edit.setText(str(cal_path))
                self._show_printcal_result_dialog(cal_path)

        self._printcal_runner.run(params, on_line=_on_line, on_finish=_on_finish)

    # ------------------------------------------------------------------
    # Applycal section
    # ------------------------------------------------------------------

    def _make_applycal_section(self) -> QWidget:
        container = QWidget(self)
        cc = QVBoxLayout(container)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(8)

        grp = QGroupBox("Apply Calibration  (applycal)", container)
        g = QVBoxLayout(grp)
        g.setSpacing(8)

        # ---- Mode ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:", grp))
        self._ac_mode_combo = NoScrollComboBox(grp)
        self._ac_mode_combo.addItem("Apply calibration to profile  (-a)", "apply")
        self._ac_mode_combo.addItem("Remove calibration from profile  (-u)", "remove")
        self._ac_mode_combo.addItem("Check calibration (no file written)  (-c)", "check")
        mode_row.addWidget(self._ac_mode_combo, stretch=1)
        mode_row.addWidget(TooltipButton(
            "applycal Mode",
            "Apply: bakes the calibration curves into the ICC profile so\n"
            "that any app using the profile automatically gets calibration.\n\n"
            "Remove: strips previously applied calibration curves out of\n"
            "the profile, reverting it to its uncalibrated state.\n\n"
            "Check: reports whether the profile has calibration curves\n"
            "applied, without modifying anything.",
            grp,
            min_width=480,
        ))
        g.addLayout(mode_row)

        # ---- Cal file ----
        cal_row = QHBoxLayout()
        cal_row.addWidget(QLabel("Calibration file (.cal):", grp))
        self._ac_cal_edit = QLineEdit(grp)
        self._ac_cal_edit.setPlaceholderText("Path to .cal file…")
        ac_cal_browse = make_browse_button(grp, "Select .cal file", icon="folder_build")
        ac_cal_browse.clicked.connect(self._ac_browse_cal)
        cal_row.addWidget(self._ac_cal_edit, stretch=1)
        cal_row.addWidget(ac_cal_browse)
        g.addLayout(cal_row)

        # ---- Input ICC ----
        in_row = QHBoxLayout()
        in_row.addWidget(QLabel("Input ICC profile:", grp))
        self._ac_in_edit = QLineEdit(grp)
        self._ac_in_edit.setPlaceholderText("Path to input .icc / .icm…")
        self._ac_in_edit.textChanged.connect(self._ac_update_out_placeholder)
        ac_in_browse = make_browse_button(grp, "Select input ICC profile", icon="folder_build")
        ac_in_browse.clicked.connect(self._ac_browse_in)
        in_row.addWidget(self._ac_in_edit, stretch=1)
        in_row.addWidget(ac_in_browse)
        g.addLayout(in_row)

        # ---- Output ICC ----
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output ICC profile:", grp))
        self._ac_out_edit = QLineEdit(grp)
        self._ac_out_edit.setPlaceholderText("Leave blank to save as cal_<name>.icc")
        ac_out_browse = make_browse_button(grp, "Select output ICC path", icon="folder_build")
        ac_out_browse.clicked.connect(self._ac_browse_out)
        out_row.addWidget(self._ac_out_edit, stretch=1)
        out_row.addWidget(ac_out_browse)
        g.addLayout(out_row)

        # ---- Verbose ----
        opt_row = QHBoxLayout()
        self._ac_verbose_cb = QCheckBox("Verbose", grp)
        opt_row.addWidget(self._ac_verbose_cb)
        opt_row.addStretch()
        g.addLayout(opt_row)

        cc.addWidget(grp)
        cc.addStretch()

        # ---- Button row (outside groupbox) ----
        btn_row = QHBoxLayout()
        self._ac_run_btn = QPushButton("Apply Calibration", container)
        self._ac_run_btn.setObjectName("primary")
        self._ac_run_btn.setFixedHeight(36)
        self._ac_run_btn.clicked.connect(self._on_applycal_run)
        self._ac_save_defaults_btn = QPushButton("Save as Defaults", container)
        self._ac_save_defaults_btn.setFixedHeight(36)
        self._ac_save_defaults_btn.clicked.connect(self._on_ac_save_defaults)
        btn_row.addWidget(self._ac_run_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._ac_save_defaults_btn)
        cc.addLayout(btn_row)

        # ---- Progress bar (outside groupbox) ----
        self._ac_progress = SpectrumSegmentsBar(container)
        self._ac_progress.set_label("Apply Calibration", "")
        self._ac_progress.set_value(0)
        cc.addWidget(self._ac_progress)

        # ---- Log (outside groupbox) ----
        self._ac_log = QPlainTextEdit(container)
        self._ac_log.setObjectName("log")
        self._ac_log.setReadOnly(True)
        self._ac_log.setMaximumHeight(67)
        self._ac_log.setPlaceholderText("applycal output will appear here…")
        cc.addWidget(self._ac_log)

        s = self._settings
        saved_mode = s.get("applycal_mode", "apply")
        idx = self._ac_mode_combo.findData(saved_mode)
        if idx >= 0:
            self._ac_mode_combo.setCurrentIndex(idx)
        self._ac_verbose_cb.setChecked(bool(s.get("applycal_verbose", False)))

        return container

    def _ac_browse_cal(self) -> None:
        p = open_file_dialog(self, "Select calibration file", "CAL files (*.cal)",
                             extra_path=self._settings.get("custom_output_path", ""))
        if p:
            self._ac_cal_edit.setText(p)

    def _ac_browse_in(self) -> None:
        p = open_file_dialog(self, "Select input ICC profile", "ICC profiles (*.icc *.icm)",
                             extra_path=self._settings.get("custom_output_path", ""))
        if p:
            self._ac_in_edit.setText(p)

    def _ac_browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save output ICC profile", self._ac_in_edit.text() or "",
            "ICC profiles (*.icc *.icm)",
        )
        if path:
            self._ac_out_edit.setText(path)

    def _ac_update_out_placeholder(self, in_text: str) -> None:
        """Keep the output placeholder in sync with the input ICC field."""
        if in_text.strip():
            stem = Path(in_text.strip()).stem
            self._ac_out_edit.setPlaceholderText(f"Leave blank to save as cal_{stem}.icc")
        else:
            self._ac_out_edit.setPlaceholderText("Leave blank to save as cal_<name>.icc")

    def _on_ac_save_defaults(self) -> None:
        s = self._settings
        s.set("applycal_mode", self._ac_mode_combo.currentData() or "apply")
        s.set("applycal_verbose", self._ac_verbose_cb.isChecked())

    def _on_applycal_run(self) -> None:
        if self._runner.is_running:
            return
        cal = self._ac_cal_edit.text().strip()
        in_icc = self._ac_in_edit.text().strip()
        if not cal or not in_icc:
            self._ac_log.setPlainText("[ERROR] Please select a .cal file and an input ICC profile.")
            return
        out_raw = self._ac_out_edit.text().strip()
        if out_raw:
            out_icc = out_raw
        else:
            in_path = Path(in_icc)
            out_icc = str(in_path.parent / f"cal_{in_path.name}")

        self._ac_log.clear()
        self._ac_run_btn.setEnabled(False)
        mode = self._ac_mode_combo.currentData() or "apply"
        self._ac_progress.set_label("Applying calibration…", "applycal")
        self._ac_progress.set_value(None)
        self._ac_progress.start()

        def _on_line(line: str) -> None:
            self._ac_log.appendPlainText(line)
            self._ac_log.ensureCursorVisible()

        def _on_finish(result: Path | None) -> None:
            self._ac_progress.stop()
            self._ac_progress.set_label("Apply Calibration", "")
            self._ac_progress.set_value(0)
            self._ac_run_btn.setEnabled(True)
            if result is None:
                self._ac_log.appendPlainText("\n[ERROR] applycal failed — see output above.")
                self._ac_log.ensureCursorVisible()
            else:
                self._ac_log.appendPlainText(f"\n[OK] Done. Output: {result}")
                self._ac_log.ensureCursorVisible()
                if mode == "apply":
                    self._show_applycal_result_dialog(result)

        self._applycal_runner.run(
            cal_path=Path(cal),
            in_icc=Path(in_icc),
            out_icc=Path(out_icc),
            mode=mode,
            verbose=self._ac_verbose_cb.isChecked(),
            on_line=_on_line,
            on_finish=_on_finish,
        )

    def _show_applycal_result_dialog(self, icc_path: Path) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Calibration Applied")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        headline = QLabel("<b>Calibration applied successfully.</b>", dlg)
        headline.setStyleSheet("font-size: 14px;")
        layout.addWidget(headline)

        path_lbl = QLabel(
            f"Saved to:<br><code style='font-size:11px'>{icc_path}</code>",
            dlg,
        )
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        next_lbl = QLabel("What would you like to do next?", dlg)
        layout.addWidget(next_lbl)

        install_desc = QLabel(
            "<b>Install on this Mac</b> — adds the calibrated profile to your Mac's "
            "colour management system so it is immediately available in Photoshop, "
            "Lightroom, and other colour-managed apps.",
            dlg,
        )
        install_desc.setWordWrap(True)
        install_desc.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        layout.addWidget(install_desc)

        btn_box = QDialogButtonBox(dlg)
        install_btn = btn_box.addButton("Install on this Mac", QDialogButtonBox.ButtonRole.ActionRole)
        done_btn    = btn_box.addButton("Done",                QDialogButtonBox.ButtonRole.AcceptRole)
        install_btn.setObjectName("primary")
        layout.addWidget(btn_box)

        def _on_install() -> None:
            dlg.accept()
            try:
                self._builder.install_profile(icc_path)
                self._ac_log.appendPlainText("[OK] Profile installed to ~/Library/ColorSync/Profiles/")
            except Exception as exc:
                self._ac_log.appendPlainText(f"[ERROR] Install failed: {exc}")
            self._ac_log.ensureCursorVisible()

        install_btn.clicked.connect(_on_install)
        done_btn.clicked.connect(dlg.accept)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()

    def _show_printcal_result_dialog(self, cal_path: Path) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Calibration File Created")
        dlg.setMinimumWidth(560)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        headline = QLabel("<b>Your calibration file is ready.</b>", dlg)
        headline.setStyleSheet("font-size: 14px;")
        layout.addWidget(headline)

        path_lbl = QLabel(
            f"Saved to:<br><code style='font-size:11px'>{cal_path}</code>",
            dlg,
        )
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        next_lbl = QLabel(
            "Next step: go to the <b>Create Chart</b> tab, make sure "
            "<i>Create target for calibration</i> is unchecked, and generate a "
            "profiling chart. The .cal path has been pre-filled in both the "
            "<b>-K</b> and <b>-I</b> fields — use whichever applies to your workflow:",
            dlg,
        )
        next_lbl.setWordWrap(True)
        layout.addWidget(next_lbl)

        k_lbl = QLabel(
            "<b>-K &nbsp; Apply calibration to patches</b><br>"
            "<span style='color:#b0b0b0; font-size:11px'>"
            "printtarg remaps every patch value through the .cal curves before printing. "
            "Use this when your printer has no built-in linearisation — the chart will "
            "already reflect calibrated device behaviour. Recommended for most desktop "
            "inkjet printers driven directly from a TIFF."
            "</span>",
            dlg,
        )
        k_lbl.setWordWrap(True)
        k_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(k_lbl)

        i_lbl = QLabel(
            "<b>-I &nbsp; Embed calibration without applying</b><br>"
            "<span style='color:#b0b0b0; font-size:11px'>"
            "The .cal is embedded in the .ti2 as metadata only; patch values are left "
            "untouched. Use this when your printer or RIP already applies linearisation "
            "natively (e.g. EFI Fiery, Wasatch, or any RIP with its own LUT). "
            "colprof will reference the .cal when building the profile."
            "</span>",
            dlg,
        )
        i_lbl.setWordWrap(True)
        i_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(i_lbl)

        note_lbl = QLabel(
            "<span style='color:#606060; font-size:11px'>"
            "-K and -I are mutually exclusive — enable only one at a time."
            "</span>",
            dlg,
        )
        note_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note_lbl)

        btn_box = QDialogButtonBox(dlg)
        chart_btn = btn_box.addButton("Go to Create Chart →", QDialogButtonBox.ButtonRole.ActionRole)
        done_btn  = btn_box.addButton("Done",                  QDialogButtonBox.ButtonRole.AcceptRole)
        chart_btn.setObjectName("primary")
        layout.addWidget(btn_box)

        def _on_chart() -> None:
            dlg.accept()
            self.cal_chart_requested.emit(cal_path)

        def _on_done() -> None:
            dlg.accept()
            self.cal_file_created.emit(cal_path)

        chart_btn.clicked.connect(_on_chart)
        done_btn.clicked.connect(_on_done)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()

    # ------------------------------------------------------------------
    # Guided panel
    # ------------------------------------------------------------------

    def _make_guided_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 4, 0)
        inner_layout.setSpacing(10)

        self._build_core_group(inner_layout)
        self._build_measurement_group(inner_layout)
        self._build_color_science_group(inner_layout)
        self._build_gamut_group(inner_layout)
        self._build_metadata_group(inner_layout)
        self._build_advanced_group(inner_layout)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ------------------------------------------------------------------
    # Manual panel
    # ------------------------------------------------------------------

    def _make_manual_panel(self) -> QWidget:
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # Presets group
        presets_grp = QGroupBox("Presets", container)
        presets_row = QHBoxLayout(presets_grp)
        presets_row.setContentsMargins(8, 4, 8, 8)
        presets_row.addWidget(QLabel("Select preset:", container))
        self._m_preset_combo = NoScrollComboBox(container)
        self._m_preset_combo.addItem("Default", userData=None)
        presets_row.addWidget(self._m_preset_combo, stretch=1)
        self._m_preset_add_btn = QPushButton(container)
        self._m_preset_add_btn.setObjectName("icon_btn")
        self._m_preset_add_btn.setFixedSize(28, 28)
        self._m_preset_add_btn.setIcon(QIcon(str(resource_path("assets/plus.svg"))))
        self._m_preset_add_btn.setToolTip("Save current settings as a new preset")
        self._m_preset_del_btn = QPushButton(container)
        self._m_preset_del_btn.setObjectName("icon_btn")
        self._m_preset_del_btn.setFixedSize(28, 28)
        self._m_preset_del_btn.setIcon(QIcon(str(resource_path("assets/minus.svg"))))
        self._m_preset_del_btn.setToolTip("Delete selected preset")
        self._m_preset_del_btn.setEnabled(False)
        presets_row.addWidget(self._m_preset_add_btn)
        presets_row.addWidget(self._m_preset_del_btn)
        presets_row.addWidget(TooltipButton(
            "Manual Presets",
            "Save and recall named snapshots of all Manual mode settings.\n\n"
            "  +  Save current parameter values as a new named preset.\n"
            "  −  Delete the currently selected preset.\n\n"
            "Select a preset from the dropdown to instantly restore all\n"
            "values. The Default entry always resets to built-in defaults.\n\n"
            "Profile Description is not saved — it is filled from the\n"
            ".ti3 filename. Presets persist between sessions.",
            container,
            min_width=520,
        ))
        self._m_preset_combo.currentIndexChanged.connect(self._on_m_preset_selected)
        self._m_preset_add_btn.clicked.connect(self._on_m_preset_save)
        self._m_preset_del_btn.clicked.connect(self._on_m_preset_delete)
        cl.addWidget(presets_grp)
        cl.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        self._build_m_core_group(layout)
        self._build_m_measurement_group(layout)
        self._build_m_color_science_group(layout)
        self._build_m_gamut_group(layout)
        self._build_m_metadata_group(layout)
        self._build_m_advanced_group(layout)

        layout.addStretch()
        scroll.setWidget(inner)
        cl.addWidget(scroll, stretch=1)
        return container

    # ------------------------------------------------------------------
    # Manual preset helpers (Profile tab)
    # ------------------------------------------------------------------

    def _m_load_presets(self) -> dict:
        raw = self._settings.get("manual2_profile_presets", "")
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _m_save_presets(self, presets: dict) -> None:
        self._settings.set("manual2_profile_presets", json.dumps(presets))

    def _m_populate_preset_combo(self, presets: dict, select_name: str | None = None) -> None:
        self._m_preset_combo.blockSignals(True)
        self._m_preset_combo.clear()
        self._m_preset_combo.addItem("Default", userData=None)
        for name in presets:
            self._m_preset_combo.addItem(name, userData=name)
        if select_name is not None:
            idx = self._m_preset_combo.findText(select_name)
            if idx >= 0:
                self._m_preset_combo.setCurrentIndex(idx)
        self._m_preset_combo.blockSignals(False)
        self._m_preset_del_btn.setEnabled(self._m_preset_combo.currentIndex() > 0)

    def _m_collect_preset_data(self) -> dict:
        gam_mode = self._m_gam_mode_combo.currentData() or ""
        return {
            "algorithm":           self._m_algo_combo.currentData() or "l",
            "quality":             self._m_qual_combo.currentData() or "m",
            "b2a_enabled":         self._m_b2a_check.isChecked(),
            "b2a_quality":         self._m_b2a_combo.currentData() or "m",
            "smoothing":           self._m_smooth_spin.value(),
            "dark_emphasis":       self._m_dark_spin.value(),
            "illuminant":          self._m_illum_combo.currentData() or "",
            "observer":            self._m_obs_combo.currentData() or "",
            "fwa_enabled":         self._m_fwa_check.isChecked(),
            "fwa_illum":           self._m_fwa_illum_combo.currentData() or "",
            "gamut_mode":          gam_mode,
            "gamut_src":           self._m_gam_path_edit.text().strip(),
            "perc_intent_enabled": self._m_perc_intent_check.isChecked(),
            "perc_intent":         self._m_perc_intent_combo.currentData() or "",
            "sat_intent_enabled":  self._m_sat_intent_check.isChecked(),
            "sat_intent":          self._m_sat_intent_combo.currentData() or "",
            "no_perc_gamut":       self._m_no_perc_gamut_cb.isChecked(),
            "no_sat_gamut":        self._m_no_sat_gamut_cb.isChecked(),
            "inv_gamut":           self._m_inv_gamut_cb.isChecked(),
            "mfr_enabled":         self._m_mfr_check.isChecked(),
            "mfr":                 self._m_mfr_edit.text().strip(),
            "model_enabled":       self._m_model_check.isChecked(),
            "model":               self._m_model_edit.text().strip(),
            "copy_enabled":        self._m_copy_check.isChecked(),
            "copy":                self._m_copy_edit.text().strip(),
            "no_input_shaper":     self._m_no_input_cb.isChecked(),
            "no_output_shaper":    self._m_no_output_cb.isChecked(),
            "no_grid_pos":         self._m_no_grid_pos_cb.isChecked(),
            "no_embedded":         self._m_no_embedded_cb.isChecked(),
        }

    def _m_apply_preset_data(self, data: dict) -> None:
        def _set_combo(combo: QComboBox, key: str, default: str) -> None:
            idx = combo.findData(data.get(key, default))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        _set_combo(self._m_algo_combo,          "algorithm", "l")
        _set_combo(self._m_qual_combo,          "quality",   "m")
        self._m_b2a_check.setChecked(bool(data.get("b2a_enabled", False)))
        _set_combo(self._m_b2a_combo,           "b2a_quality", "m")
        self._m_smooth_spin.setValue(float(data.get("smoothing", 0.5)))
        self._m_dark_spin.setValue(float(data.get("dark_emphasis", 1.0)))
        _set_combo(self._m_illum_combo,         "illuminant", "")
        _set_combo(self._m_obs_combo,           "observer",   "")
        self._m_fwa_check.setChecked(bool(data.get("fwa_enabled", False)))
        _set_combo(self._m_fwa_illum_combo,     "fwa_illum",  "")
        gam_mode = data.get("gamut_mode", "S")
        idx = self._m_gam_mode_combo.findData(gam_mode)
        if idx >= 0:
            self._m_gam_mode_combo.setCurrentIndex(idx)
        self._m_gam_path_edit.setText(data.get("gamut_src", ""))
        self._m_perc_intent_check.setChecked(bool(data.get("perc_intent_enabled", False)))
        _set_combo(self._m_perc_intent_combo,   "perc_intent", "")
        self._m_sat_intent_check.setChecked(bool(data.get("sat_intent_enabled", False)))
        _set_combo(self._m_sat_intent_combo,    "sat_intent",  "")
        self._m_no_perc_gamut_cb.setChecked(bool(data.get("no_perc_gamut", False)))
        self._m_no_sat_gamut_cb.setChecked(bool(data.get("no_sat_gamut",   False)))
        self._m_inv_gamut_cb.setChecked(bool(data.get("inv_gamut",         False)))
        self._m_mfr_check.setChecked(bool(data.get("mfr_enabled",   False)))
        self._m_mfr_edit.setText(data.get("mfr", ""))
        self._m_model_check.setChecked(bool(data.get("model_enabled", False)))
        self._m_model_edit.setText(data.get("model", ""))
        self._m_copy_check.setChecked(bool(data.get("copy_enabled",  False)))
        self._m_copy_edit.setText(data.get("copy", ""))
        self._m_no_input_cb.setChecked(bool(data.get("no_input_shaper",  False)))
        self._m_no_output_cb.setChecked(bool(data.get("no_output_shaper", False)))
        self._m_no_grid_pos_cb.setChecked(bool(data.get("no_grid_pos",   False)))
        self._m_no_embedded_cb.setChecked(bool(data.get("no_embedded",   False)))

    def _on_m_preset_selected(self, index: int) -> None:
        self._m_preset_del_btn.setEnabled(index > 0)
        s = self._settings
        if index == 0:
            def _set_combo(combo: QComboBox, key: str, default: str) -> None:
                idx = combo.findData(s.get(key, default))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            _set_combo(self._m_algo_combo,          "manual2_colprof_algorithm", "l")
            _set_combo(self._m_qual_combo,          "manual2_colprof_quality",   "m")
            self._m_b2a_check.setChecked(bool(s.get("manual2_colprof_b2a_enabled", False)))
            _set_combo(self._m_b2a_combo,           "manual2_colprof_b2a_quality", "m")
            self._m_smooth_spin.setValue(float(s.get("manual2_colprof_smoothing", 0.5)))
            self._m_dark_spin.setValue(float(s.get("manual2_colprof_dark_emphasis", 1.0)))
            _set_combo(self._m_illum_combo,         "manual2_colprof_illuminant", "")
            _set_combo(self._m_obs_combo,           "manual2_colprof_observer",   "")
            self._m_fwa_check.setChecked(bool(s.get("manual2_colprof_fwa_enabled", False)))
            _set_combo(self._m_fwa_illum_combo,     "manual2_colprof_fwa_illum",  "")
            gam_mode = s.get("manual2_colprof_gamut_mode", "S")
            idx = self._m_gam_mode_combo.findData(gam_mode)
            if idx >= 0:
                self._m_gam_mode_combo.setCurrentIndex(idx)
            self._m_gam_path_edit.setText(s.get("manual2_colprof_gamut_src", ""))
            self._m_perc_intent_check.setChecked(bool(s.get("manual2_colprof_perc_intent_enabled", False)))
            _set_combo(self._m_perc_intent_combo,   "manual2_colprof_perc_intent", "")
            self._m_sat_intent_check.setChecked(bool(s.get("manual2_colprof_sat_intent_enabled", False)))
            _set_combo(self._m_sat_intent_combo,    "manual2_colprof_sat_intent",  "")
            self._m_no_perc_gamut_cb.setChecked(bool(s.get("manual2_colprof_no_perc_gamut", False)))
            self._m_no_sat_gamut_cb.setChecked(bool(s.get("manual2_colprof_no_sat_gamut",   False)))
            self._m_inv_gamut_cb.setChecked(bool(s.get("manual2_colprof_inv_gamut",         False)))
            self._m_mfr_check.setChecked(bool(s.get("manual2_colprof_mfr_enabled",   False)))
            self._m_mfr_edit.setText(s.get("manual2_colprof_mfr", ""))
            self._m_model_check.setChecked(bool(s.get("manual2_colprof_model_enabled", False)))
            self._m_model_edit.setText(s.get("manual2_colprof_model", ""))
            self._m_copy_check.setChecked(bool(s.get("manual2_colprof_copy_enabled",  False)))
            self._m_copy_edit.setText(s.get("manual2_colprof_copy", ""))
            self._m_no_input_cb.setChecked(bool(s.get("manual2_colprof_no_input_shaper",  False)))
            self._m_no_output_cb.setChecked(bool(s.get("manual2_colprof_no_output_shaper", False)))
            self._m_no_grid_pos_cb.setChecked(bool(s.get("manual2_colprof_no_grid_pos",   False)))
            self._m_no_embedded_cb.setChecked(bool(s.get("manual2_colprof_no_embedded",   False)))
        else:
            name = self._m_preset_combo.currentData()
            presets = self._m_load_presets()
            self._m_apply_preset_data(presets.get(name, {}))

    def _on_m_preset_save(self) -> None:
        data = self._m_collect_preset_data()
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Save Preset")
        dlg.setLabelText(
            "Give this preset a name.\n"
            "All current Manual mode settings will be saved under that name\n"
            "and can be recalled at any time from the preset list.\n"
            "(Profile Description is not included — it is filled from the .ti3 filename.)"
        )
        dlg.setMinimumWidth(460)
        if not dlg.exec():
            return
        name = dlg.textValue().strip()
        if not name:
            return
        presets = self._m_load_presets()
        presets[name] = data
        self._m_save_presets(presets)
        self._m_populate_preset_combo(presets, select_name=name)

    def _on_m_preset_delete(self) -> None:
        name = self._m_preset_combo.currentText()
        dlg = QDialog(self)
        dlg.setWindowTitle("Delete Preset")
        dlg.setMinimumWidth(460)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setSpacing(10)
        dlg_layout.setContentsMargins(20, 20, 20, 16)
        heading = QLabel(f'Delete the preset "{name}"?', dlg)
        heading.setStyleSheet("font-weight: bold;")
        heading.setWordWrap(True)
        dlg_layout.addWidget(heading)
        info = QLabel(
            "All parameter values saved in this preset will be permanently removed. "
            "This cannot be undone.",
            dlg,
        )
        info.setWordWrap(True)
        dlg_layout.addWidget(info)
        bb = QDialogButtonBox(dlg)
        bb.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        del_btn = bb.addButton("Delete", QDialogButtonBox.ButtonRole.AcceptRole)
        del_btn.setObjectName("primary")
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        dlg_layout.addWidget(bb)
        tint_dialog_primary(dlg, _TAB_COLOR)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        presets = self._m_load_presets()
        presets.pop(name, None)
        self._m_save_presets(presets)
        self._m_populate_preset_combo(presets)

    # ------------------------------------------------------------------
    # Manual GroupBox builders
    # ------------------------------------------------------------------

    def _build_m_core_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Profile Core", layout.parentWidget())
        g = QVBoxLayout(grp)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Profile Description (-D):", grp))
        self._m_desc_edit = QLineEdit(grp)
        self._m_desc_edit.setPlaceholderText("e.g. EpsonP900_CansonBaryta_2026-04")
        desc_row.addWidget(self._m_desc_edit, stretch=1)
        desc_row.addWidget(TooltipButton(
            "Profile Description (-D)",
            "The name embedded in the ICC profile — shown in colour management\n"
            "menus in apps like Photoshop, Lightroom, and Preview.\n\n"
            "Use a consistent format: Printer · Paper · Ink type · Date\n"
            "e.g. \"Epson P900 · Canson Baryta · Chromatic · 2026-04\"\n\n"
            "The output file is named after your .ti3 file — keep that name\n"
            "consistent using underscores: EpsonP900_CansonBaryta_2026-04.icc",
            grp,
        ))
        g.addLayout(desc_row)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm (-a):", grp))
        self._m_algo_combo = NoScrollComboBox(grp)
        for code, label in [
            ("l", "Lab cLUT (recommended for inkjet)"),
            ("x", "XYZ cLUT"),
            ("X", "XYZ cLUT + matrix"),
            ("g", "Gamma + matrix"),
            ("G", "Gamma + matrix (forced)"),
            ("s", "Single gamma + matrix"),
            ("S", "Single gamma + matrix (forced)"),
            ("m", "Matrix only"),
            ("M", "Matrix only (forced)"),
        ]:
            self._m_algo_combo.addItem(label, code)
        algo_row.addWidget(self._m_algo_combo, stretch=1)
        algo_row.addWidget(TooltipButton(
            "Profile Algorithm (-a)",
            "Lab cLUT uses a full 3D lookup table — most accurate for inkjet printers.\n"
            "Matrix/gamma profiles are smaller but far less accurate for inkjets.\n"
            "Use matrix only if the destination app requires it.",
            grp,
        ))
        g.addLayout(algo_row)

        qual_row = QHBoxLayout()
        qual_row.addWidget(QLabel("Quality (-q):", grp))
        self._m_qual_combo = NoScrollComboBox(grp)
        for code, label in [
            ("l", "Low — fast test (~30 s)"),
            ("m", "Medium — recommended (~2 min)"),
            ("h", "High — production (~10 min)"),
            ("u", "Ultra — maximum (~30+ min)"),
        ]:
            self._m_qual_combo.addItem(label, code)
        self._m_qual_combo.setCurrentIndex(1)
        qual_row.addWidget(self._m_qual_combo, stretch=1)
        qual_row.addWidget(TooltipButton(
            "Profile Quality (-q)",
            "Controls cLUT grid resolution:\n"
            "• Low: 17³ grid — quick test\n"
            "• Medium: 33³ grid — good balance\n"
            "• High: 45³ grid — production quality\n"
            "• Ultra: 65³ grid — maximum accuracy, very slow",
            grp,
        ))
        g.addLayout(qual_row)

        self._m_b2a_check = QCheckBox("B2A Table Quality (-b):", grp)
        self._m_b2a_combo = NoScrollComboBox(grp)
        for code, lbl in [("l", "Low"), ("m", "Medium"), ("h", "High"),
                           ("u", "Ultra"), ("n", "None (skip B2A)")]:
            self._m_b2a_combo.addItem(lbl, code)
        self._m_b2a_combo.setCurrentIndex(1)
        self._m_b2a_combo.setEnabled(False)
        self._m_b2a_check.toggled.connect(self._m_b2a_combo.setEnabled)
        b2a_row = QHBoxLayout()
        b2a_row.addWidget(self._m_b2a_check)
        b2a_row.addWidget(self._m_b2a_combo, stretch=1)
        b2a_row.addWidget(TooltipButton(
            "B2A Table Quality (-b)",
            "Quality of the B→A (output→input) tables for perceptual/saturation intents.\n"
            "Leave unchecked to inherit the same quality as -q.\n"
            "Setting lower than -q reduces build time.",
            grp,
        ))
        g.addLayout(b2a_row)

        layout.addWidget(grp)

    def _build_m_measurement_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Measurement && Smoothing", layout.parentWidget())
        g = QVBoxLayout(grp)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("Smoothing / Noise (-r):", grp))
        self._m_smooth_spin = NoScrollDoubleSpinBox(grp)
        self._m_smooth_spin.setRange(0.0, 5.0)
        self._m_smooth_spin.setSingleStep(0.1)
        self._m_smooth_spin.setDecimals(2)
        self._m_smooth_spin.setValue(0.5)
        smooth_row.addWidget(self._m_smooth_spin)
        smooth_row.addStretch()
        smooth_row.addWidget(TooltipButton(
            "Measurement Noise (-r)",
            "Estimated average measurement noise as % ΔE.\n"
            "Higher values = more smoothing.\n"
            "• 0.5%: clean measurements (default)\n"
            "• 1.0–2.0%: textured/matte papers\n"
            "• 3.0–5.0%: very noisy conditions",
            grp,
        ))
        g.addLayout(smooth_row)

        dark_row = QHBoxLayout()
        dark_row.addWidget(QLabel("Dark Region Emphasis (-V):", grp))
        self._m_dark_spin = NoScrollDoubleSpinBox(grp)
        self._m_dark_spin.setRange(1.0, 4.0)
        self._m_dark_spin.setSingleStep(0.1)
        self._m_dark_spin.setDecimals(1)
        self._m_dark_spin.setValue(1.0)
        dark_row.addWidget(self._m_dark_spin)
        dark_row.addStretch()
        dark_row.addWidget(TooltipButton(
            "Dark Region Grid Emphasis (-V)",
            "Adds extra cLUT grid points in shadow areas for better shadow gradation.\n"
            "1.0 = uniform grid (default).\n"
            "Try 1.5–2.0 for printers with complex shadow behaviour.",
            grp,
        ))
        g.addLayout(dark_row)

        layout.addWidget(grp)

    def _build_m_color_science_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Color Science", layout.parentWidget())
        g = QVBoxLayout(grp)

        illum_row = QHBoxLayout()
        illum_row.addWidget(QLabel("Illuminant (-i):", grp))
        self._m_illum_combo = NoScrollComboBox(grp)
        for label, val in _ILLUMINANTS:
            self._m_illum_combo.addItem(label, val)
        illum_row.addWidget(self._m_illum_combo, stretch=1)
        illum_row.addWidget(TooltipButton(
            "Illuminant for XYZ Computation (-i)",
            "Illuminant used when converting spectral measurements to XYZ.\n"
            "D50 is the ICC standard default and correct for most workflows.\n"
            "Change only if you need a non-D50 PCS encoding (unusual).",
            grp,
        ))
        g.addLayout(illum_row)

        obs_row = QHBoxLayout()
        obs_row.addWidget(QLabel("CIE Observer (-o):", grp))
        self._m_obs_combo = NoScrollComboBox(grp)
        for label, val in [
            ("Default (1931 2° standard)", ""),
            ("1964 10° large-field observer", "1964_10"),
            ("2015 2° (Stockman)", "2015_2"),
            ("2015 10° (Stockman)", "2015_10"),
        ]:
            self._m_obs_combo.addItem(label, val)
        obs_row.addWidget(self._m_obs_combo, stretch=1)
        obs_row.addWidget(TooltipButton(
            "CIE Observer (-o)",
            "Standard observer for colorimetric computations.\n"
            "The 1931 2° observer is the ICC standard default.\n"
            "The 1964 10° observer is better for large-area viewing.\n"
            "2015 observers (Stockman) are more physiologically accurate.",
            grp,
        ))
        g.addLayout(obs_row)

        fwa_row = QHBoxLayout()
        self._m_fwa_check = QCheckBox("FWA Compensation (-f):", grp)
        self._m_fwa_illum_combo = NoScrollComboBox(grp)
        self._m_fwa_illum_combo.addItem("Same as illuminant (-i)", "")
        for label, val in _ILLUMINANTS[1:]:
            self._m_fwa_illum_combo.addItem(label, val)
        self._m_fwa_illum_combo.setEnabled(False)
        self._m_fwa_check.toggled.connect(self._m_fwa_illum_combo.setEnabled)
        fwa_row.addWidget(self._m_fwa_check)
        fwa_row.addWidget(self._m_fwa_illum_combo, stretch=1)
        fwa_row.addWidget(TooltipButton(
            "FWA Compensation (-f)",
            "Compensates for Fluorescent Whitening Agents (optical brighteners) in paper.\n"
            "Requires spectral measurements (not colorimetric-only).\n"
            "The illuminant selects the lighting to compute the FWA effect under.\n"
            "Use when printing on papers with optical brighteners (e.g. bright white coated papers).",
            grp,
        ))
        g.addLayout(fwa_row)

        layout.addWidget(grp)

    def _build_m_gamut_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Gamut Mapping", layout.parentWidget())
        g = QVBoxLayout(grp)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Gamut Source:", grp))
        self._m_gam_mode_combo = NoScrollComboBox(grp)
        self._m_gam_mode_combo.addItem("None (colprof default)", "")
        self._m_gam_mode_combo.addItem("Perceptual only (-s)", "s")
        self._m_gam_mode_combo.addItem("Perceptual + Saturation (-S)  ← recommended", "S")
        mode_row.addWidget(self._m_gam_mode_combo, stretch=1)
        mode_row.addWidget(TooltipButton(
            "Gamut Source (-s / -S)",
            "When printing, colours that fall outside your printer's range must be\n"
            "compressed to fit. This setting tells ChromIQ which colour space your\n"
            "images live in, so the compression is tuned to that space and looks\n"
            "natural in prints.\n\n"
            "None — colprof uses a large internal default. Works, but the perceptual\n"
            "intent is not optimised for any real working space.\n\n"
            "Perceptual only (-s) — applies the source gamut to the perceptual\n"
            "rendering intent only.\n\n"
            "Perceptual + Saturation (-S, recommended) — applies it to both intents.\n"
            "Use this unless you have a specific reason to treat them differently.\n\n"
            "Leave set to 'Perc+Sat' and sRGB unless you edit your images in\n"
            "AdobeRGB, in which case browse to AdobeRGB.icm in the Argyll ref folder.",
            grp,
        ))
        g.addLayout(mode_row)

        path_row = QHBoxLayout()
        self._m_gam_path_edit = QLineEdit(grp)
        self._m_gam_path_edit.setPlaceholderText(
            "Path to source RGB profile (e.g. sRGB.icm or AdobeRGB.icm from Argyll/ref/)"
        )
        self._m_gam_path_browse = make_browse_button(grp, "Select gamut source profile", icon="folder_build")
        self._m_gam_path_browse.clicked.connect(self._browse_m_gam)
        path_row.addWidget(self._m_gam_path_edit, stretch=1)
        path_row.addWidget(self._m_gam_path_browse)
        g.addLayout(path_row)

        def _on_m_mode_changed() -> None:
            active = bool(self._m_gam_mode_combo.currentData())
            self._m_gam_path_edit.setEnabled(active)
            self._m_gam_path_browse.setEnabled(active)

        self._m_gam_mode_combo.currentIndexChanged.connect(_on_m_mode_changed)
        _on_m_mode_changed()

        perc_intent_row = QHBoxLayout()
        self._m_perc_intent_check = QCheckBox("Perceptual Intent Override (-t):", grp)
        self._m_perc_intent_combo = NoScrollComboBox(grp)
        for label, val in _INTENTS:
            self._m_perc_intent_combo.addItem(label, val)
        self._m_perc_intent_combo.setEnabled(False)
        self._m_perc_intent_check.toggled.connect(self._m_perc_intent_combo.setEnabled)
        perc_intent_row.addWidget(self._m_perc_intent_check)
        perc_intent_row.addWidget(self._m_perc_intent_combo, stretch=1)
        perc_intent_row.addWidget(TooltipButton(
            "Perceptual Rendering Intent Override (-t)",
            "Changes the mathematical algorithm used to compress out-of-gamut colours\n"
            "for the perceptual rendering intent.\n\n"
            "The default (unchecked) works well for most inkjet workflows.\n"
            "Only change this if you are experimenting with different gamut-mapping\n"
            "behaviours.",
            grp,
        ))
        g.addLayout(perc_intent_row)

        sat_intent_row = QHBoxLayout()
        self._m_sat_intent_check = QCheckBox("Saturation Intent Override (-T):", grp)
        self._m_sat_intent_combo = NoScrollComboBox(grp)
        for label, val in _INTENTS:
            self._m_sat_intent_combo.addItem(label, val)
        self._m_sat_intent_combo.setEnabled(False)
        self._m_sat_intent_check.toggled.connect(self._m_sat_intent_combo.setEnabled)
        sat_intent_row.addWidget(self._m_sat_intent_check)
        sat_intent_row.addWidget(self._m_sat_intent_combo, stretch=1)
        sat_intent_row.addWidget(TooltipButton(
            "Saturation Rendering Intent Override (-T)",
            "Same as the Perceptual override above, but for the saturation rendering\n"
            "intent. The saturation intent is rarely used in photo printing — leave\n"
            "unchecked unless you specifically need it.",
            grp,
        ))
        g.addLayout(sat_intent_row)

        flags_row = QHBoxLayout()
        self._m_no_perc_gamut_cb = QCheckBox("Use colorimetric gamut — perceptual (-nP)", grp)
        self._m_no_sat_gamut_cb  = QCheckBox("Use colorimetric gamut — saturation (-nS)", grp)
        self._m_inv_gamut_cb     = QCheckBox("Inverse gamut mapping (-nI)", grp)
        flags_row.addWidget(self._m_no_perc_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Perceptual Gamut (-nP)",
            "Forces the perceptual intent to use the same gamut boundaries as the\n"
            "colorimetric intent, ignoring any gamut source set above.\n"
            "Advanced — leave unchecked.",
            grp,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._m_no_sat_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Saturation Gamut (-nS)",
            "Same as above, but for the saturation intent.\n"
            "Advanced — leave unchecked.",
            grp,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._m_inv_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "Inverse Gamut Mapping (-nI)",
            "Applies gamut mapping in reverse on the A→B tables.\n"
            "Highly experimental — only use if you know exactly what this does.",
            grp,
        ))
        flags_row.addStretch()
        g.addLayout(flags_row)

        layout.addWidget(grp)

    def _build_m_metadata_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Profile Metadata", layout.parentWidget())
        g = QVBoxLayout(grp)

        for attr, flag, placeholder, tip in [
            ("_m_mfr",   "A", "e.g. Epson",   "Manufacturer string in the ICC profile header."),
            ("_m_model", "M", "e.g. SC-P900",  "Model string in the ICC profile header."),
            ("_m_copy",  "C", "e.g. © 2026 …", "Copyright string in the ICC profile header."),
        ]:
            label_text = "Manufacturer" if flag == "A" else "Model" if flag == "M" else "Copyright"
            check = QCheckBox(f"{label_text} (-{flag}):", grp)
            edit  = QLineEdit(grp)
            edit.setPlaceholderText(placeholder)
            edit.setEnabled(False)
            check.toggled.connect(edit.setEnabled)
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(edit, stretch=1)
            row.addWidget(TooltipButton(f"-{flag}", tip, grp))
            g.addLayout(row)
            setattr(self, attr + "_check", check)
            setattr(self, attr + "_edit",  edit)

        layout.addWidget(grp)

    def _build_m_advanced_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Advanced", layout.parentWidget())
        g = QVBoxLayout(grp)

        row1 = QHBoxLayout()
        self._m_no_input_cb  = QCheckBox("No input shaper curves (-ni)", grp)
        self._m_no_output_cb = QCheckBox("No output shaper curves (-no)", grp)
        row1.addWidget(self._m_no_input_cb)
        row1.addWidget(TooltipButton(
            "No Input Shaper Curves (-ni)",
            "Disables 1D tone curves that pre-condition device values before the 3D cLUT.\n"
            "Disable only for diagnostic purposes — normally leave unchecked.",
            grp,
        ))
        row1.addSpacing(16)
        row1.addWidget(self._m_no_output_cb)
        row1.addWidget(TooltipButton(
            "No Output Shaper Curves (-no)",
            "Disables 1D output curves applied after the 3D cLUT.\n"
            "Disable only for diagnostic purposes.",
            grp,
        ))
        row1.addStretch()
        g.addLayout(row1)

        row2 = QHBoxLayout()
        self._m_no_grid_pos_cb = QCheckBox("No input grid position curves (-np)", grp)
        self._m_no_embedded_cb = QCheckBox("Don't embed measurement data (-nc)", grp)
        row2.addWidget(self._m_no_grid_pos_cb)
        row2.addWidget(TooltipButton(
            "No Grid Position Curves (-np)",
            "Disables the 1D curves that position device values onto cLUT grid nodes.\n"
            "Advanced diagnostic option.",
            grp,
        ))
        row2.addSpacing(16)
        row2.addWidget(self._m_no_embedded_cb)
        row2.addWidget(TooltipButton(
            "Don't Embed .ti3 Data (-nc)",
            "By default colprof embeds the .ti3 measurement data inside the ICC profile.\n"
            "Check this to omit it, resulting in a smaller profile file.",
            grp,
        ))
        row2.addStretch()
        g.addLayout(row2)

        layout.addWidget(grp)

    # ------------------------------------------------------------------
    # GroupBox builders (guided panel)
    # ------------------------------------------------------------------

    def _build_core_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Profile Core", layout.parentWidget())
        g = QVBoxLayout(grp)

        # Description
        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Profile Description (-D):", grp))
        self._desc_edit = QLineEdit(grp)
        self._desc_edit.setPlaceholderText("e.g. EpsonP900_CansonBaryta_2026-04")
        desc_row.addWidget(self._desc_edit, stretch=1)
        desc_row.addWidget(TooltipButton(
            "Profile Description (-D)",
            "The name embedded in the ICC profile — shown in colour management\n"
            "menus in apps like Photoshop, Lightroom, and Preview.\n\n"
            "Use a consistent format: Printer · Paper · Ink type · Date\n"
            "e.g. \"Epson P900 · Canson Baryta · Chromatic · 2026-04\"\n\n"
            "The output file is named after your .ti3 file — keep that name\n"
            "consistent using underscores: EpsonP900_CansonBaryta_2026-04.icc",
            grp,
        ))
        g.addLayout(desc_row)

        # Algorithm (hidden)
        _algo_w = QWidget(grp)
        algo_row = QHBoxLayout(_algo_w)
        algo_row.setContentsMargins(0, 0, 0, 0)
        algo_row.addWidget(QLabel("Algorithm (-a):", _algo_w))
        self._algo_combo = NoScrollComboBox(_algo_w)
        for code, label in [
            ("l", "Lab cLUT (recommended for inkjet)"),
            ("x", "XYZ cLUT"),
            ("X", "XYZ cLUT + matrix"),
            ("g", "Gamma + matrix"),
            ("G", "Gamma + matrix (forced)"),
            ("s", "Single gamma + matrix"),
            ("S", "Single gamma + matrix (forced)"),
            ("m", "Matrix only"),
            ("M", "Matrix only (forced)"),
        ]:
            self._algo_combo.addItem(label, code)
        algo_row.addWidget(self._algo_combo, stretch=1)
        algo_row.addWidget(TooltipButton(
            "Profile Algorithm (-a)",
            "Lab cLUT uses a full 3D lookup table — most accurate for inkjet printers.\n"
            "Matrix/gamma profiles are smaller but far less accurate for inkjets.\n"
            "Use matrix only if the destination app requires it.",
            _algo_w,
        ))
        g.addWidget(_algo_w)
        _algo_w.setVisible(False)

        # Quality (hidden)
        _qual_w = QWidget(grp)
        qual_row = QHBoxLayout(_qual_w)
        qual_row.setContentsMargins(0, 0, 0, 0)
        qual_row.addWidget(QLabel("Quality (-q):", _qual_w))
        self._qual_combo = NoScrollComboBox(_qual_w)
        for code, label in [
            ("l", "Low — fast test (~30 s)"),
            ("m", "Medium — recommended (~2 min)"),
            ("h", "High — production (~10 min)"),
            ("u", "Ultra — maximum (~30+ min)"),
        ]:
            self._qual_combo.addItem(label, code)
        self._qual_combo.setCurrentIndex(1)
        qual_row.addWidget(self._qual_combo, stretch=1)
        qual_row.addWidget(TooltipButton(
            "Profile Quality (-q)",
            "Controls cLUT grid resolution:\n"
            "• Low: 17³ grid — quick test\n"
            "• Medium: 33³ grid — good balance\n"
            "• High: 45³ grid — production quality\n"
            "• Ultra: 65³ grid — maximum accuracy, very slow",
            _qual_w,
        ))
        g.addWidget(_qual_w)
        _qual_w.setVisible(False)

        # B2A quality (hidden)
        _b2a_w = QWidget(grp)
        b2a_row = QHBoxLayout(_b2a_w)
        b2a_row.setContentsMargins(0, 0, 0, 0)
        self._b2a_check = QCheckBox("B2A Table Quality (-b):", _b2a_w)
        self._b2a_combo = NoScrollComboBox(_b2a_w)
        for code, lbl in [("l", "Low"), ("m", "Medium"), ("h", "High"),
                           ("u", "Ultra"), ("n", "None (skip B2A)")]:
            self._b2a_combo.addItem(lbl, code)
        self._b2a_combo.setCurrentIndex(1)
        self._b2a_combo.setEnabled(False)
        self._b2a_check.toggled.connect(self._b2a_combo.setEnabled)
        b2a_row.addWidget(self._b2a_check)
        b2a_row.addWidget(self._b2a_combo, stretch=1)
        b2a_row.addWidget(TooltipButton(
            "B2A Table Quality (-b)",
            "Quality of the B→A (output→input) tables for perceptual/saturation intents.\n"
            "Leave unchecked to inherit the same quality as -q.\n"
            "Setting lower than -q reduces build time.",
            _b2a_w,
        ))
        g.addWidget(_b2a_w)
        _b2a_w.setVisible(False)

        layout.addWidget(grp)

    def _build_measurement_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Measurement && Smoothing", layout.parentWidget())
        g = QVBoxLayout(grp)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("Smoothing / Noise (-r):", grp))
        self._smooth_spin = NoScrollDoubleSpinBox(grp)
        self._smooth_spin.setRange(0.0, 5.0)
        self._smooth_spin.setSingleStep(0.1)
        self._smooth_spin.setDecimals(2)
        self._smooth_spin.setValue(0.5)
        smooth_row.addWidget(self._smooth_spin)
        smooth_row.addWidget(TooltipButton(
            "Measurement Noise (-r)",
            "Estimated average measurement noise as % ΔE.\n"
            "Higher values = more smoothing.\n"
            "• 0.5%: clean measurements (default)\n"
            "• 1.0–2.0%: textured/matte papers\n"
            "• 3.0–5.0%: very noisy conditions",
            grp,
        ))
        smooth_row.addStretch()
        g.addLayout(smooth_row)

        dark_row = QHBoxLayout()
        dark_row.addWidget(QLabel("Dark Region Emphasis (-V):", grp))
        self._dark_spin = NoScrollDoubleSpinBox(grp)
        self._dark_spin.setRange(1.0, 4.0)
        self._dark_spin.setSingleStep(0.1)
        self._dark_spin.setDecimals(1)
        self._dark_spin.setValue(1.0)
        dark_row.addWidget(self._dark_spin)
        dark_row.addWidget(TooltipButton(
            "Dark Region Grid Emphasis (-V)",
            "Adds extra cLUT grid points in shadow areas for better shadow gradation.\n"
            "1.0 = uniform grid (default).\n"
            "Try 1.5–2.0 for printers with complex shadow behaviour.",
            grp,
        ))
        dark_row.addStretch()
        g.addLayout(dark_row)

        layout.addWidget(grp)
        grp.setVisible(False)

    def _build_color_science_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Color Science", layout.parentWidget())
        g = QVBoxLayout(grp)

        # Illuminant
        illum_row = QHBoxLayout()
        illum_row.addWidget(QLabel("Illuminant (-i):", grp))
        self._illum_combo = NoScrollComboBox(grp)
        for label, val in _ILLUMINANTS:
            self._illum_combo.addItem(label, val)
        illum_row.addWidget(self._illum_combo, stretch=1)
        illum_row.addWidget(TooltipButton(
            "Illuminant for XYZ Computation (-i)",
            "Illuminant used when converting spectral measurements to XYZ.\n"
            "D50 is the ICC standard default and correct for most workflows.\n"
            "Change only if you need a non-D50 PCS encoding (unusual).",
            grp,
        ))
        g.addLayout(illum_row)

        # Observer
        obs_row = QHBoxLayout()
        obs_row.addWidget(QLabel("CIE Observer (-o):", grp))
        self._obs_combo = NoScrollComboBox(grp)
        for label, val in [
            ("Default (1931 2° standard)", ""),
            ("1964 10° large-field observer", "1964_10"),
            ("2015 2° (Stockman)", "2015_2"),
            ("2015 10° (Stockman)", "2015_10"),
        ]:
            self._obs_combo.addItem(label, val)
        obs_row.addWidget(self._obs_combo, stretch=1)
        obs_row.addWidget(TooltipButton(
            "CIE Observer (-o)",
            "Standard observer for colorimetric computations.\n"
            "The 1931 2° observer is the ICC standard default.\n"
            "The 1964 10° observer is better for large-area viewing.\n"
            "2015 observers (Stockman) are more physiologically accurate.",
            grp,
        ))
        g.addLayout(obs_row)

        # FWA compensation
        fwa_row = QHBoxLayout()
        self._fwa_check = QCheckBox("FWA Compensation (-f):", grp)
        self._fwa_illum_combo = NoScrollComboBox(grp)
        self._fwa_illum_combo.addItem("Same as illuminant (-i)", "")
        for label, val in _ILLUMINANTS[1:]:  # skip the "Default (D50)" entry
            self._fwa_illum_combo.addItem(label, val)
        self._fwa_illum_combo.setEnabled(False)
        self._fwa_check.toggled.connect(self._fwa_illum_combo.setEnabled)
        fwa_row.addWidget(self._fwa_check)
        fwa_row.addWidget(self._fwa_illum_combo, stretch=1)
        fwa_row.addWidget(TooltipButton(
            "FWA Compensation (-f)",
            "Compensates for Fluorescent Whitening Agents (optical brighteners) in paper.\n"
            "Requires spectral measurements (not colorimetric-only).\n"
            "The illuminant selects the lighting to compute the FWA effect under.\n"
            "Use when printing on papers with optical brighteners (e.g. bright white coated papers).",
            grp,
        ))
        g.addLayout(fwa_row)

        layout.addWidget(grp)
        grp.setVisible(False)

    def _build_gamut_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Gamut Mapping", layout.parentWidget())
        g = QVBoxLayout(grp)

        # ── Unified gamut source selector ───────────────────────────────
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Gamut Source:", grp))
        self._gam_mode_combo = NoScrollComboBox(grp)
        self._gam_mode_combo.addItem("None (colprof default)", "")
        self._gam_mode_combo.addItem("Perceptual only (-s)", "s")
        self._gam_mode_combo.addItem("Perceptual + Saturation (-S)  ← recommended", "S")
        mode_row.addWidget(self._gam_mode_combo, stretch=1)
        mode_row.addWidget(TooltipButton(
            "Gamut Source (-s / -S)",
            "When printing, colours that fall outside your printer's range must be\n"
            "compressed to fit. This setting tells ChromIQ which colour space your\n"
            "images live in, so the compression is tuned to that space and looks\n"
            "natural in prints.\n\n"
            "None — colprof uses a large internal default. Works, but the perceptual\n"
            "intent is not optimised for any real working space.\n\n"
            "Perceptual only (-s) — applies the source gamut to the perceptual\n"
            "rendering intent only.\n\n"
            "Perceptual + Saturation (-S, recommended) — applies it to both intents.\n"
            "Use this unless you have a specific reason to treat them differently.\n\n"
            "Leave set to 'Perc+Sat' and sRGB unless you edit your images in\n"
            "AdobeRGB, in which case browse to AdobeRGB.icm in the Argyll ref folder.",
            grp,
        ))
        g.addLayout(mode_row)

        path_row = QHBoxLayout()
        self._gam_path_edit = QLineEdit(grp)
        self._gam_path_edit.setPlaceholderText(
            "Path to source RGB profile (e.g. sRGB.icm or AdobeRGB.icm from Argyll/ref/)"
        )
        self._gam_path_browse = make_browse_button(grp, "Select gamut source profile", icon="folder_build")
        self._gam_path_browse.clicked.connect(self._browse_gam)
        path_row.addWidget(self._gam_path_edit, stretch=1)
        path_row.addWidget(self._gam_path_browse)
        g.addLayout(path_row)

        def _on_mode_changed() -> None:
            active = bool(self._gam_mode_combo.currentData())
            self._gam_path_edit.setEnabled(active)
            self._gam_path_browse.setEnabled(active)

        self._gam_mode_combo.currentIndexChanged.connect(_on_mode_changed)
        _on_mode_changed()

        # ── Perceptual intent override (hidden) ─────────────────────────
        _perc_w = QWidget(grp)
        perc_intent_row = QHBoxLayout(_perc_w)
        perc_intent_row.setContentsMargins(0, 0, 0, 0)
        self._perc_intent_check = QCheckBox("Perceptual Intent Override (-t):", _perc_w)
        self._perc_intent_combo = NoScrollComboBox(_perc_w)
        for label, val in _INTENTS:
            self._perc_intent_combo.addItem(label, val)
        self._perc_intent_combo.setEnabled(False)
        self._perc_intent_check.toggled.connect(self._perc_intent_combo.setEnabled)
        perc_intent_row.addWidget(self._perc_intent_check)
        perc_intent_row.addWidget(self._perc_intent_combo, stretch=1)
        perc_intent_row.addWidget(TooltipButton(
            "Perceptual Rendering Intent Override (-t)",
            "Changes the mathematical algorithm used to compress out-of-gamut colours\n"
            "for the perceptual rendering intent.\n\n"
            "The default (unchecked) works well for most inkjet workflows.\n"
            "Only change this if you are experimenting with different gamut-mapping\n"
            "behaviours.",
            _perc_w,
        ))
        g.addWidget(_perc_w)
        _perc_w.setVisible(False)

        # ── Saturation intent override (hidden) ─────────────────────────
        _sat_w = QWidget(grp)
        sat_intent_row = QHBoxLayout(_sat_w)
        sat_intent_row.setContentsMargins(0, 0, 0, 0)
        self._sat_intent_check = QCheckBox("Saturation Intent Override (-T):", _sat_w)
        self._sat_intent_combo = NoScrollComboBox(_sat_w)
        for label, val in _INTENTS:
            self._sat_intent_combo.addItem(label, val)
        self._sat_intent_combo.setEnabled(False)
        self._sat_intent_check.toggled.connect(self._sat_intent_combo.setEnabled)
        sat_intent_row.addWidget(self._sat_intent_check)
        sat_intent_row.addWidget(self._sat_intent_combo, stretch=1)
        sat_intent_row.addWidget(TooltipButton(
            "Saturation Rendering Intent Override (-T)",
            "Same as the Perceptual override above, but for the saturation rendering\n"
            "intent. The saturation intent is rarely used in photo printing — leave\n"
            "unchecked unless you specifically need it.",
            _sat_w,
        ))
        g.addWidget(_sat_w)
        _sat_w.setVisible(False)

        # ── nP / nS / nI flags (hidden) ────────────────────────────────
        _flags_w = QWidget(grp)
        flags_row = QHBoxLayout(_flags_w)
        flags_row.setContentsMargins(0, 0, 0, 0)
        self._no_perc_gamut_cb = QCheckBox("Use colorimetric gamut — perceptual (-nP)", _flags_w)
        self._no_sat_gamut_cb  = QCheckBox("Use colorimetric gamut — saturation (-nS)", _flags_w)
        self._inv_gamut_cb     = QCheckBox("Inverse gamut mapping (-nI)", _flags_w)
        flags_row.addWidget(self._no_perc_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Perceptual Gamut (-nP)",
            "Forces the perceptual intent to use the same gamut boundaries as the\n"
            "colorimetric intent, ignoring any gamut source set above.\n"
            "Advanced — leave unchecked.",
            _flags_w,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._no_sat_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Saturation Gamut (-nS)",
            "Same as above, but for the saturation intent.\n"
            "Advanced — leave unchecked.",
            _flags_w,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._inv_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "Inverse Gamut Mapping (-nI)",
            "Applies gamut mapping in reverse on the A→B tables.\n"
            "Highly experimental — only use if you know exactly what this does.",
            _flags_w,
        ))
        flags_row.addStretch()
        g.addWidget(_flags_w)
        _flags_w.setVisible(False)

        layout.addWidget(grp)

    def _build_metadata_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Profile Metadata", layout.parentWidget())
        g = QVBoxLayout(grp)

        for attr, flag, placeholder, tip in [
            ("_mfr",   "A", "e.g. Epson",         "Manufacturer string in the ICC profile header."),
            ("_model", "M", "e.g. SC-P900",        "Model string in the ICC profile header."),
            ("_copy",  "C", "e.g. © 2026 …",       "Copyright string in the ICC profile header."),
        ]:
            check = QCheckBox(f"{'Manufacturer' if flag=='A' else 'Model' if flag=='M' else 'Copyright'} (-{flag}):", grp)
            edit  = QLineEdit(grp)
            edit.setPlaceholderText(placeholder)
            edit.setEnabled(False)
            check.toggled.connect(edit.setEnabled)
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(edit, stretch=1)
            row.addWidget(TooltipButton(f"-{flag}", tip, grp))
            g.addLayout(row)
            setattr(self, attr + "_check", check)
            setattr(self, attr + "_edit",  edit)

        layout.addWidget(grp)

    def _build_advanced_group(self, layout: QVBoxLayout) -> None:
        grp = QGroupBox("Advanced", layout.parentWidget())
        g = QVBoxLayout(grp)

        row1 = QHBoxLayout()
        self._no_input_cb  = QCheckBox("No input shaper curves (-ni)", grp)
        self._no_output_cb = QCheckBox("No output shaper curves (-no)", grp)
        row1.addWidget(self._no_input_cb)
        row1.addWidget(TooltipButton(
            "No Input Shaper Curves (-ni)",
            "Disables 1D tone curves that pre-condition device values before the 3D cLUT.\n"
            "Disable only for diagnostic purposes — normally leave unchecked.",
            grp,
        ))
        row1.addSpacing(16)
        row1.addWidget(self._no_output_cb)
        row1.addWidget(TooltipButton(
            "No Output Shaper Curves (-no)",
            "Disables 1D output curves applied after the 3D cLUT.\n"
            "Disable only for diagnostic purposes.",
            grp,
        ))
        row1.addStretch()
        g.addLayout(row1)

        row2 = QHBoxLayout()
        self._no_grid_pos_cb = QCheckBox("No input grid position curves (-np)", grp)
        self._no_embedded_cb = QCheckBox("Don't embed measurement data (-nc)", grp)
        row2.addWidget(self._no_grid_pos_cb)
        row2.addWidget(TooltipButton(
            "No Grid Position Curves (-np)",
            "Disables the 1D curves that position device values onto cLUT grid nodes.\n"
            "Advanced diagnostic option.",
            grp,
        ))
        row2.addSpacing(16)
        row2.addWidget(self._no_embedded_cb)
        row2.addWidget(TooltipButton(
            "Don't Embed .ti3 Data (-nc)",
            "By default colprof embeds the .ti3 measurement data inside the ICC profile.\n"
            "Check this to omit it, resulting in a smaller profile file.",
            grp,
        ))
        row2.addStretch()
        g.addLayout(row2)

        layout.addWidget(grp)
        grp.setVisible(False)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_ti3_path(self, path: Path, propagate: bool = True) -> None:
        self._ti3_path = path
        self._file_lbl.setText(str(path))
        self._build_btn.setEnabled(True)
        self._desc_edit.setText(path.stem)
        self._m_desc_edit.setText(path.stem)
        if propagate:
            ti2 = path.with_suffix(".ti2")
            if ti2.exists():
                self.ti2_found.emit(ti2)

    def clear_files(self) -> None:
        self._ti3_path = None
        self._icc_path = None
        self._file_lbl.setText("")
        self._build_btn.setEnabled(False)
        for field in (
            self._desc_edit, self._mfr_edit, self._model_edit, self._copy_edit,
            self._m_desc_edit, self._m_mfr_edit, self._m_model_edit, self._m_copy_edit,
        ):
            field.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _default_gamut_src(self) -> str:
        bin_path = self._settings.get("argyll_bin_path", "/Applications/Argyll/bin")
        candidate = Path(bin_path).parent / "ref" / "sRGB.icm"
        return str(candidate) if candidate.exists() else ""

    def _on_load_ti3(self) -> None:
        path = open_file_dialog(
            self, "Load .ti3 file", "TI3 files (*.ti3)",
            extra_path=self._settings.get("custom_output_path", ""),
        )
        if path:
            self.set_ti3_path(Path(path))
            self.ti3_manually_loaded.emit()

    def _browse_gam(self) -> None:
        bin_path = self._settings.get("argyll_bin_path", "/Applications/Argyll/bin")
        ref_dir = Path(bin_path).parent / "ref"
        path = open_file_dialog(
            self, "Select gamut source profile", "ICC profiles (*.icc *.icm)",
            start_dir=str(ref_dir) if ref_dir.is_dir() else "",
        )
        if path:
            self._gam_path_edit.setText(path)

    def _browse_m_gam(self) -> None:
        bin_path = self._settings.get("argyll_bin_path", "/Applications/Argyll/bin")
        ref_dir = Path(bin_path).parent / "ref"
        path = open_file_dialog(
            self, "Select gamut source profile", "ICC profiles (*.icc *.icm)",
            start_dir=str(ref_dir) if ref_dir.is_dir() else "",
        )
        if path:
            self._m_gam_path_edit.setText(path)

    def _on_build(self) -> None:
        if not self._ti3_path or not self._ti3_path.exists():
            self._log.appendPlainText("[ERROR] No valid .ti3 file selected.")
            self._log.ensureCursorVisible()
            return
        if self._runner.is_running:
            return

        params = self._collect_params()
        self._log.clear()
        self._build_headline.setText(
            f'Working hard<span style="color: {SPEC_CYAN}; font-style: italic;">…</span>'
        )
        self._build_subtext.setText("Good things take time.")
        self._build_btn.setText("Building Profile…")
        self._build_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._save_defaults_btn.setEnabled(False)
        self._file_grp.setEnabled(False)
        self._stack.setEnabled(False)
        self._progress_bar.set_label("Building", "colprof")
        self._progress_bar.set_value(None)
        self._progress_bar.start()
        self.profile_active.emit(True)

        self._builder.build(
            params,
            on_line=self._on_log_line,
            on_finish=self._on_build_done,
        )

    def _on_log_line(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.ensureCursorVisible()

    def _on_build_done(self, code: int) -> None:
        self.profile_active.emit(False)
        self._build_headline.setText(
            f'Ready to build<span style="color: {SPEC_CYAN}; font-style: italic;">?</span>'
        )
        self._build_subtext.setText("Awaiting your command.")
        self._build_btn.setText("Build Profile")
        self._build_btn.setEnabled(True)
        self._save_defaults_btn.setEnabled(True)
        self._file_grp.setEnabled(True)
        self._stack.setEnabled(True)
        self._progress_bar.stop()
        self._progress_bar.set_label("Build Profile", "")
        self._progress_bar.set_value(0)

        if code != 0:
            self._log.appendPlainText(f"\n[ERROR] colprof exited with code {code}.")
            self._log.ensureCursorVisible()
            return

        params = self._collect_params()
        self._icc_path = self._builder.expected_icc_path(params)
        issues = self._builder.sanity_check(self._icc_path)

        if not (self._icc_path and self._icc_path.exists()):
            self._log.appendPlainText("\n[ERROR] Profile file was not created.")
            self._log.ensureCursorVisible()
            return

        self._install_btn.setEnabled(True)
        self._log.appendPlainText(f"\n[OK] Profile saved: {self._icc_path}")
        self._log.ensureCursorVisible()
        self._ac_in_edit.setText(str(self._icc_path))
        if self._ti3_path:
            self.profile_built.emit(self._ti3_path, self._icc_path)

        self._show_build_result_dialog(self._icc_path, issues)

    def _show_build_result_dialog(self, icc_path: Path, issues: list[str]) -> None:
        cal_mode = bool(self._settings.get("calibration_mode", False))

        dlg = QDialog(self)
        dlg.setWindowTitle("Profile Built")
        dlg.setMinimumWidth(620 if cal_mode else 560)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        if issues:
            headline = QLabel("<b>Your ICC profile has been built — with warnings.</b>", dlg)
        else:
            headline = QLabel("<b>Your ICC profile has been built successfully.</b>", dlg)
        headline.setStyleSheet("font-size: 14px;")
        layout.addWidget(headline)

        path_lbl = QLabel(
            f"Saved to:<br><code style='font-size:11px'>{icc_path}</code>",
            dlg,
        )
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        if issues:
            warn_lbl = QLabel(
                "<b>Warnings detected:</b><br>" +
                "<br>".join(f"&nbsp;&nbsp;• {i}" for i in issues),
                dlg,
            )
            warn_lbl.setWordWrap(True)
            warn_lbl.setStyleSheet("color: #d4a017;")
            layout.addWidget(warn_lbl)

        next_lbl = QLabel("What would you like to do next?", dlg)
        layout.addWidget(next_lbl)

        install_desc = QLabel(
            "<b>Install on this Mac</b> — adds the profile to your Mac's colour management "
            "system so it is immediately available in Photoshop, Lightroom, and other "
            "colour-managed apps.",
            dlg,
        )
        install_desc.setWordWrap(True)
        install_desc.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        layout.addWidget(install_desc)

        check_desc = QLabel(
            "<b>Check Profile Quality</b> — runs a quality check to see how accurately "
            "the profile represents your printer's colours. Recommended before using "
            "the profile for critical prints.",
            dlg,
        )
        check_desc.setWordWrap(True)
        check_desc.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        layout.addWidget(check_desc)

        if cal_mode:
            apply_desc = QLabel(
                "<b>Apply Calibration</b> — bakes your calibration curves (.cal file) "
                "directly into the ICC profile. This means every colour-managed app will "
                "automatically apply the calibration without any extra steps. Use this "
                "after you have created a calibration file in the "
                "<i>Create Calibration File</i> module. The profile path is already "
                "pre-filled — just select your .cal file and run.",
                dlg,
            )
            apply_desc.setWordWrap(True)
            apply_desc.setStyleSheet("color: #b0b0b0; font-size: 11px;")
            layout.addWidget(apply_desc)

        btn_box = QDialogButtonBox(dlg)
        install_btn = btn_box.addButton("Install on this Mac",     QDialogButtonBox.ButtonRole.ActionRole)
        check_btn   = btn_box.addButton("Check Profile Quality →", QDialogButtonBox.ButtonRole.ActionRole)
        if cal_mode:
            apply_btn = btn_box.addButton("Apply Calibration →",   QDialogButtonBox.ButtonRole.ActionRole)
            apply_btn.setObjectName("primary")
        done_btn    = btn_box.addButton("Done",                    QDialogButtonBox.ButtonRole.AcceptRole)
        install_btn.setObjectName("primary")
        check_btn.setObjectName("primary")
        layout.addWidget(btn_box)

        def _on_install() -> None:
            dlg.accept()
            self._on_install()

        def _on_check() -> None:
            dlg.accept()
            self.check_requested.emit()

        def _on_apply_cal() -> None:
            dlg.accept()
            if icc_path and not self._ac_in_edit.text().strip():
                self._ac_in_edit.setText(str(icc_path))
            self._switch_cal_mode(2)

        install_btn.clicked.connect(_on_install)
        check_btn.clicked.connect(_on_check)
        if cal_mode:
            apply_btn.clicked.connect(_on_apply_cal)
        done_btn.clicked.connect(dlg.accept)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()

    def _on_install(self) -> None:
        if not self._icc_path:
            return
        try:
            self._builder.install_profile(self._icc_path)
            self._log.appendPlainText("[OK] Profile installed to ~/Library/ColorSync/Profiles/")
            self._log.ensureCursorVisible()
        except Exception as exc:
            self._log.appendPlainText(f"[ERROR] Install failed: {exc}")
            self._log.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Param collection
    # ------------------------------------------------------------------

    def _collect_params(self) -> ProfileParams:
        if self._current_mode() == "guided":
            return self._collect_guided_profile()
        return self._collect_manual_profile()

    def _collect_guided_profile(self) -> ProfileParams:
        return ProfileParams(
            ti3_path         = self._ti3_path,
            description      = self._desc_edit.text().strip(),
            algorithm        = self._algo_combo.currentData() or "l",
            quality          = self._qual_combo.currentData() or "m",
            b2a_quality      = self._b2a_combo.currentData() if self._b2a_check.isChecked() else "",
            smoothing        = self._smooth_spin.value(),
            dark_emphasis    = self._dark_spin.value(),
            gamut_src        = self._gam_path_edit.text().strip() if self._gam_mode_combo.currentData() == "s" else "",
            manufacturer     = self._mfr_edit.text().strip() if self._mfr_check.isChecked() else "",
            model            = self._model_edit.text().strip() if self._model_check.isChecked() else "",
            copyright        = self._copy_edit.text().strip() if self._copy_check.isChecked() else "",
            no_input_shaper  = self._no_input_cb.isChecked(),
            no_output_shaper = self._no_output_cb.isChecked(),
            extra_args       = "",
            illuminant       = self._illum_combo.currentData() or "",
            observer         = self._obs_combo.currentData() or "",
            fwa_enabled      = self._fwa_check.isChecked(),
            fwa_illum        = (self._fwa_illum_combo.currentData() or "") if self._fwa_check.isChecked() else "",
            gamut_sat_src    = self._gam_path_edit.text().strip() if self._gam_mode_combo.currentData() == "S" else "",
            no_perc_gamut    = self._no_perc_gamut_cb.isChecked(),
            no_sat_gamut     = self._no_sat_gamut_cb.isChecked(),
            inv_gamut_map    = self._inv_gamut_cb.isChecked(),
            perc_intent      = (self._perc_intent_combo.currentData() or "") if self._perc_intent_check.isChecked() else "",
            sat_intent       = (self._sat_intent_combo.currentData() or "") if self._sat_intent_check.isChecked() else "",
            no_grid_pos      = self._no_grid_pos_cb.isChecked(),
            no_embedded_data = self._no_embedded_cb.isChecked(),
        )

    def _collect_manual_profile(self) -> ProfileParams:
        gam_mode = self._m_gam_mode_combo.currentData()
        gam_path = self._m_gam_path_edit.text().strip()
        return ProfileParams(
            ti3_path         = self._ti3_path,
            description      = self._m_desc_edit.text().strip(),
            algorithm        = self._m_algo_combo.currentData() or "l",
            quality          = self._m_qual_combo.currentData() or "m",
            b2a_quality      = self._m_b2a_combo.currentData() if self._m_b2a_check.isChecked() else "",
            smoothing        = self._m_smooth_spin.value(),
            dark_emphasis    = self._m_dark_spin.value(),
            gamut_src        = gam_path if gam_mode == "s" else "",
            gamut_sat_src    = gam_path if gam_mode == "S" else "",
            manufacturer     = self._m_mfr_edit.text().strip() if self._m_mfr_check.isChecked() else "",
            model            = self._m_model_edit.text().strip() if self._m_model_check.isChecked() else "",
            copyright        = self._m_copy_edit.text().strip() if self._m_copy_check.isChecked() else "",
            no_input_shaper  = self._m_no_input_cb.isChecked(),
            no_output_shaper = self._m_no_output_cb.isChecked(),
            extra_args       = "",
            illuminant       = self._m_illum_combo.currentData() or "",
            observer         = self._m_obs_combo.currentData() or "",
            fwa_enabled      = self._m_fwa_check.isChecked(),
            fwa_illum        = (self._m_fwa_illum_combo.currentData() or "") if self._m_fwa_check.isChecked() else "",
            no_perc_gamut    = self._m_no_perc_gamut_cb.isChecked(),
            no_sat_gamut     = self._m_no_sat_gamut_cb.isChecked(),
            inv_gamut_map    = self._m_inv_gamut_cb.isChecked(),
            perc_intent      = (self._m_perc_intent_combo.currentData() or "") if self._m_perc_intent_check.isChecked() else "",
            sat_intent       = (self._m_sat_intent_combo.currentData() or "") if self._m_sat_intent_check.isChecked() else "",
            no_grid_pos      = self._m_no_grid_pos_cb.isChecked(),
            no_embedded_data = self._m_no_embedded_cb.isChecked(),
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_save_defaults(self) -> None:
        s = self._settings
        if self._current_mode() == "guided":
            s.set("colprof_algorithm",          self._algo_combo.currentData() or "l")
            s.set("colprof_quality",            self._qual_combo.currentData() or "m")
            s.set("colprof_b2a_enabled",        self._b2a_check.isChecked())
            s.set("colprof_b2a_quality",        self._b2a_combo.currentData() or "m")
            s.set("colprof_smoothing",          self._smooth_spin.value())
            s.set("colprof_dark_emphasis",      self._dark_spin.value())
            s.set("colprof_illuminant",         self._illum_combo.currentData() or "")
            s.set("colprof_observer",           self._obs_combo.currentData() or "")
            s.set("colprof_fwa_enabled",        self._fwa_check.isChecked())
            s.set("colprof_fwa_illum",          self._fwa_illum_combo.currentData() or "")
            s.set("colprof_gamut_mode",          self._gam_mode_combo.currentData() or "")
            s.set("colprof_gamut_src",           self._gam_path_edit.text().strip())
            s.set("colprof_perc_intent_enabled",self._perc_intent_check.isChecked())
            s.set("colprof_perc_intent",        self._perc_intent_combo.currentData() or "")
            s.set("colprof_sat_intent_enabled", self._sat_intent_check.isChecked())
            s.set("colprof_sat_intent",         self._sat_intent_combo.currentData() or "")
            s.set("colprof_no_perc_gamut",      self._no_perc_gamut_cb.isChecked())
            s.set("colprof_no_sat_gamut",       self._no_sat_gamut_cb.isChecked())
            s.set("colprof_inv_gamut",          self._inv_gamut_cb.isChecked())
            s.set("colprof_mfr_enabled",        self._mfr_check.isChecked())
            s.set("colprof_mfr",                self._mfr_edit.text().strip())
            s.set("colprof_model_enabled",      self._model_check.isChecked())
            s.set("colprof_model",              self._model_edit.text().strip())
            s.set("colprof_copy_enabled",       self._copy_check.isChecked())
            s.set("colprof_copy",               self._copy_edit.text().strip())
            s.set("colprof_no_input_shaper",    self._no_input_cb.isChecked())
            s.set("colprof_no_output_shaper",   self._no_output_cb.isChecked())
            s.set("colprof_no_grid_pos",        self._no_grid_pos_cb.isChecked())
            s.set("colprof_no_embedded",        self._no_embedded_cb.isChecked())
        else:
            s.set("manual2_colprof_algorithm",          self._m_algo_combo.currentData() or "l")
            s.set("manual2_colprof_quality",            self._m_qual_combo.currentData() or "m")
            s.set("manual2_colprof_b2a_enabled",        self._m_b2a_check.isChecked())
            s.set("manual2_colprof_b2a_quality",        self._m_b2a_combo.currentData() or "m")
            s.set("manual2_colprof_smoothing",          self._m_smooth_spin.value())
            s.set("manual2_colprof_dark_emphasis",      self._m_dark_spin.value())
            s.set("manual2_colprof_illuminant",         self._m_illum_combo.currentData() or "")
            s.set("manual2_colprof_observer",           self._m_obs_combo.currentData() or "")
            s.set("manual2_colprof_fwa_enabled",        self._m_fwa_check.isChecked())
            s.set("manual2_colprof_fwa_illum",          self._m_fwa_illum_combo.currentData() or "")
            s.set("manual2_colprof_gamut_mode",         self._m_gam_mode_combo.currentData() or "")
            s.set("manual2_colprof_gamut_src",          self._m_gam_path_edit.text().strip())
            s.set("manual2_colprof_perc_intent_enabled",self._m_perc_intent_check.isChecked())
            s.set("manual2_colprof_perc_intent",        self._m_perc_intent_combo.currentData() or "")
            s.set("manual2_colprof_sat_intent_enabled", self._m_sat_intent_check.isChecked())
            s.set("manual2_colprof_sat_intent",         self._m_sat_intent_combo.currentData() or "")
            s.set("manual2_colprof_no_perc_gamut",      self._m_no_perc_gamut_cb.isChecked())
            s.set("manual2_colprof_no_sat_gamut",       self._m_no_sat_gamut_cb.isChecked())
            s.set("manual2_colprof_inv_gamut",          self._m_inv_gamut_cb.isChecked())
            s.set("manual2_colprof_mfr_enabled",        self._m_mfr_check.isChecked())
            s.set("manual2_colprof_mfr",                self._m_mfr_edit.text().strip())
            s.set("manual2_colprof_model_enabled",      self._m_model_check.isChecked())
            s.set("manual2_colprof_model",              self._m_model_edit.text().strip())
            s.set("manual2_colprof_copy_enabled",       self._m_copy_check.isChecked())
            s.set("manual2_colprof_copy",               self._m_copy_edit.text().strip())
            s.set("manual2_colprof_no_input_shaper",    self._m_no_input_cb.isChecked())
            s.set("manual2_colprof_no_output_shaper",   self._m_no_output_cb.isChecked())
            s.set("manual2_colprof_no_grid_pos",        self._m_no_grid_pos_cb.isChecked())
            s.set("manual2_colprof_no_embedded",        self._m_no_embedded_cb.isChecked())
        self._log.appendPlainText("Profile settings saved as defaults.")
        self._log.ensureCursorVisible()

    def _restore_defaults(self) -> None:
        s = self._settings

        def _set_combo(combo: QComboBox, key: str, default: str) -> None:
            idx = combo.findData(s.get(key, default))
            if idx >= 0:
                combo.setCurrentIndex(idx)

        # Guided defaults
        _set_combo(self._algo_combo,         "colprof_algorithm", "l")
        _set_combo(self._qual_combo,         "colprof_quality",   "m")
        self._b2a_check.setChecked(bool(s.get("colprof_b2a_enabled", False)))
        _set_combo(self._b2a_combo,          "colprof_b2a_quality", "m")

        self._smooth_spin.setValue(float(s.get("colprof_smoothing", 0.5)))
        self._dark_spin.setValue(float(s.get("colprof_dark_emphasis", 1.0)))

        _set_combo(self._illum_combo,        "colprof_illuminant", "")
        _set_combo(self._obs_combo,          "colprof_observer",   "")
        self._fwa_check.setChecked(bool(s.get("colprof_fwa_enabled", False)))
        _set_combo(self._fwa_illum_combo,    "colprof_fwa_illum",  "")

        gamut_mode = s.get("colprof_gamut_mode", "S")
        idx = self._gam_mode_combo.findData(gamut_mode)
        self._gam_mode_combo.setCurrentIndex(idx if idx >= 0 else self._gam_mode_combo.findData("S"))
        saved_src = s.get("colprof_gamut_src", "")
        if not saved_src:
            saved_src = self._default_gamut_src()
        self._gam_path_edit.setText(saved_src)
        self._perc_intent_check.setChecked(bool(s.get("colprof_perc_intent_enabled", False)))
        _set_combo(self._perc_intent_combo,  "colprof_perc_intent", "")
        self._sat_intent_check.setChecked(bool(s.get("colprof_sat_intent_enabled", False)))
        _set_combo(self._sat_intent_combo,   "colprof_sat_intent",  "")
        self._no_perc_gamut_cb.setChecked(bool(s.get("colprof_no_perc_gamut", False)))
        self._no_sat_gamut_cb.setChecked(bool(s.get("colprof_no_sat_gamut",   False)))
        self._inv_gamut_cb.setChecked(bool(s.get("colprof_inv_gamut",         False)))

        self._mfr_check.setChecked(bool(s.get("colprof_mfr_enabled",   False)))
        self._mfr_edit.setText(s.get("colprof_mfr", ""))
        self._model_check.setChecked(bool(s.get("colprof_model_enabled", False)))
        self._model_edit.setText(s.get("colprof_model", ""))
        self._copy_check.setChecked(bool(s.get("colprof_copy_enabled",  False)))
        self._copy_edit.setText(s.get("colprof_copy", ""))

        self._no_input_cb.setChecked(bool(s.get("colprof_no_input_shaper",  False)))
        self._no_output_cb.setChecked(bool(s.get("colprof_no_output_shaper", False)))
        self._no_grid_pos_cb.setChecked(bool(s.get("colprof_no_grid_pos",   False)))
        self._no_embedded_cb.setChecked(bool(s.get("colprof_no_embedded",   False)))

        # Manual defaults
        def _set_m_combo(combo: QComboBox, key: str, default: str) -> None:
            idx = combo.findData(s.get(key, default))
            if idx >= 0:
                combo.setCurrentIndex(idx)

        _set_m_combo(self._m_algo_combo,          "manual2_colprof_algorithm", "l")
        _set_m_combo(self._m_qual_combo,          "manual2_colprof_quality",   "m")
        self._m_b2a_check.setChecked(bool(s.get("manual2_colprof_b2a_enabled", False)))
        _set_m_combo(self._m_b2a_combo,           "manual2_colprof_b2a_quality", "m")
        self._m_smooth_spin.setValue(float(s.get("manual2_colprof_smoothing", 0.5)))
        self._m_dark_spin.setValue(float(s.get("manual2_colprof_dark_emphasis", 1.0)))
        _set_m_combo(self._m_illum_combo,         "manual2_colprof_illuminant", "")
        _set_m_combo(self._m_obs_combo,           "manual2_colprof_observer",   "")
        self._m_fwa_check.setChecked(bool(s.get("manual2_colprof_fwa_enabled", False)))
        _set_m_combo(self._m_fwa_illum_combo,     "manual2_colprof_fwa_illum",  "")
        m_gam_mode = s.get("manual2_colprof_gamut_mode", "S")
        idx = self._m_gam_mode_combo.findData(m_gam_mode)
        self._m_gam_mode_combo.setCurrentIndex(idx if idx >= 0 else self._m_gam_mode_combo.findData("S"))
        m_gam_src = s.get("manual2_colprof_gamut_src", "")
        if not m_gam_src:
            m_gam_src = self._default_gamut_src()
        self._m_gam_path_edit.setText(m_gam_src)
        self._m_perc_intent_check.setChecked(bool(s.get("manual2_colprof_perc_intent_enabled", False)))
        _set_m_combo(self._m_perc_intent_combo,  "manual2_colprof_perc_intent", "")
        self._m_sat_intent_check.setChecked(bool(s.get("manual2_colprof_sat_intent_enabled", False)))
        _set_m_combo(self._m_sat_intent_combo,   "manual2_colprof_sat_intent",  "")
        self._m_no_perc_gamut_cb.setChecked(bool(s.get("manual2_colprof_no_perc_gamut", False)))
        self._m_no_sat_gamut_cb.setChecked(bool(s.get("manual2_colprof_no_sat_gamut",   False)))
        self._m_inv_gamut_cb.setChecked(bool(s.get("manual2_colprof_inv_gamut",         False)))
        self._m_mfr_check.setChecked(bool(s.get("manual2_colprof_mfr_enabled",   False)))
        self._m_mfr_edit.setText(s.get("manual2_colprof_mfr", ""))
        self._m_model_check.setChecked(bool(s.get("manual2_colprof_model_enabled", False)))
        self._m_model_edit.setText(s.get("manual2_colprof_model", ""))
        self._m_copy_check.setChecked(bool(s.get("manual2_colprof_copy_enabled",  False)))
        self._m_copy_edit.setText(s.get("manual2_colprof_copy", ""))
        self._m_no_input_cb.setChecked(bool(s.get("manual2_colprof_no_input_shaper",  False)))
        self._m_no_output_cb.setChecked(bool(s.get("manual2_colprof_no_output_shaper", False)))
        self._m_no_grid_pos_cb.setChecked(bool(s.get("manual2_colprof_no_grid_pos",   False)))
        self._m_no_embedded_cb.setChecked(bool(s.get("manual2_colprof_no_embedded",   False)))
        presets = self._m_load_presets()
        self._m_populate_preset_combo(presets)
