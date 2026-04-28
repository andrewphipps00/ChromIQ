"""Tab 4: Create ICC Profile."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, load_folder_icon, make_browse_button, open_file_dialog
from workflow.profile_builder import ProfileBuilder, ProfileParams

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)

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

    def __init__(
        self,
        runner: "ArgyllRunner",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner   = runner
        self._settings = settings
        self._builder  = ProfileBuilder(runner)
        self._ti3_path: Path | None = None
        self._icc_path: Path | None = None

        self._build_ui()
        self._restore_defaults()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # --- File selection (outside scroll) ---
        self._file_grp = file_grp = QGroupBox("Measurement Data (.ti3)", self)
        fg = QHBoxLayout(file_grp)
        self._load_btn = QPushButton("Load .ti3 file…", self)
        self._load_btn.setIcon(load_folder_icon("folder_build"))
        self._load_btn.clicked.connect(self._on_load_ti3)
        self._file_lbl = QLabel("No file selected", self)
        self._file_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        self._file_lbl.setWordWrap(True)
        fg.addWidget(self._load_btn)
        fg.addWidget(self._file_lbl, stretch=1)
        root.addWidget(file_grp)

        # --- Scrollable options area ---
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._options_widget = inner = QWidget()
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
        root.addWidget(scroll, stretch=1)

        # --- Buttons (outside scroll) ---
        btn_row = QHBoxLayout()
        self._build_btn = QPushButton("Build Profile", self)
        self._build_btn.setObjectName("primary")
        self._build_btn.setFixedHeight(36)
        self._build_btn.setEnabled(False)
        self._build_btn.clicked.connect(self._on_build)

        self._install_btn = QPushButton("Install Profile", self)
        self._install_btn.setFixedHeight(36)
        self._install_btn.setEnabled(False)
        self._install_btn.clicked.connect(self._on_install)

        self._save_defaults_btn = QPushButton("Save as Defaults", self)
        self._save_defaults_btn.setFixedHeight(36)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)

        btn_row.addWidget(self._build_btn)
        btn_row.addWidget(self._install_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        root.addLayout(btn_row)

        # --- Build progress bar (hidden until a build is running) ---
        from ui.spectrum_progress import SpectrumSegmentsBar
        self._progress_bar = SpectrumSegmentsBar(self)
        self._progress_bar.set_label("Building", "colprof")
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        # --- Log ---
        self._log = QPlainTextEdit(self)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(67)
        self._log.setPlaceholderText("colprof output will appear here…")
        root.addWidget(self._log)

    # ------------------------------------------------------------------
    # GroupBox builders
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
            "Name embedded in the ICC profile shown in colour-picker menus.\n"
            "Suggested format: Printer_Paper_Type_Date",
            grp,
        ))
        g.addLayout(desc_row)

        # Algorithm
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm (-a):", grp))
        self._algo_combo = NoScrollComboBox(grp)
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
            grp,
        ))
        g.addLayout(algo_row)

        # Quality
        qual_row = QHBoxLayout()
        qual_row.addWidget(QLabel("Quality (-q):", grp))
        self._qual_combo = NoScrollComboBox(grp)
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
            grp,
        ))
        g.addLayout(qual_row)

        # B2A quality
        self._b2a_check = QCheckBox("B2A Table Quality (-b):", grp)
        self._b2a_combo = NoScrollComboBox(grp)
        for code, lbl in [("l", "Low"), ("m", "Medium"), ("h", "High"),
                           ("u", "Ultra"), ("n", "None (skip B2A)")]:
            self._b2a_combo.addItem(lbl, code)
        self._b2a_combo.setCurrentIndex(1)
        self._b2a_combo.setEnabled(False)
        self._b2a_check.toggled.connect(self._b2a_combo.setEnabled)
        b2a_row = QHBoxLayout()
        b2a_row.addWidget(self._b2a_check)
        b2a_row.addWidget(self._b2a_combo, stretch=1)
        b2a_row.addWidget(TooltipButton(
            "B2A Table Quality (-b)",
            "Quality of the B→A (output→input) tables for perceptual/saturation intents.\n"
            "Leave unchecked to inherit the same quality as -q.\n"
            "Setting lower than -q reduces build time.",
            grp,
        ))
        g.addLayout(b2a_row)

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

        # ── Perceptual intent override ──────────────────────────────────
        perc_intent_row = QHBoxLayout()
        self._perc_intent_check = QCheckBox("Perceptual Intent Override (-t):", grp)
        self._perc_intent_combo = NoScrollComboBox(grp)
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
            grp,
        ))
        g.addLayout(perc_intent_row)

        # ── Saturation intent override ──────────────────────────────────
        sat_intent_row = QHBoxLayout()
        self._sat_intent_check = QCheckBox("Saturation Intent Override (-T):", grp)
        self._sat_intent_combo = NoScrollComboBox(grp)
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
            grp,
        ))
        g.addLayout(sat_intent_row)

        # ── nP / nS / nI flags ─────────────────────────────────────────
        flags_row = QHBoxLayout()
        self._no_perc_gamut_cb = QCheckBox("Use colorimetric gamut — perceptual (-nP)", grp)
        self._no_sat_gamut_cb  = QCheckBox("Use colorimetric gamut — saturation (-nS)", grp)
        self._inv_gamut_cb     = QCheckBox("Inverse gamut mapping (-nI)", grp)
        flags_row.addWidget(self._no_perc_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Perceptual Gamut (-nP)",
            "Forces the perceptual intent to use the same gamut boundaries as the\n"
            "colorimetric intent, ignoring any gamut source set above.\n"
            "Advanced — leave unchecked.",
            grp,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._no_sat_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "No Saturation Gamut (-nS)",
            "Same as above, but for the saturation intent.\n"
            "Advanced — leave unchecked.",
            grp,
        ))
        flags_row.addSpacing(12)
        flags_row.addWidget(self._inv_gamut_cb)
        flags_row.addWidget(TooltipButton(
            "Inverse Gamut Mapping (-nI)",
            "Applies gamut mapping in reverse on the A→B tables.\n"
            "Highly experimental — only use if you know exactly what this does.",
            grp,
        ))
        flags_row.addStretch()
        g.addLayout(flags_row)

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

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_ti3_path(self, path: Path) -> None:
        self._ti3_path = path
        self._file_lbl.setText(str(path))
        self._build_btn.setEnabled(True)
        if not self._desc_edit.text():
            self._desc_edit.setText(path.stem)

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

    def _browse_gam(self) -> None:
        bin_path = self._settings.get("argyll_bin_path", "/Applications/Argyll/bin")
        ref_dir = Path(bin_path).parent / "ref"
        path = open_file_dialog(
            self, "Select gamut source profile", "ICC profiles (*.icc *.icm)",
            start_dir=str(ref_dir) if ref_dir.is_dir() else "",
        )
        if path:
            self._gam_path_edit.setText(path)

    def _on_build(self) -> None:
        if not self._ti3_path or not self._ti3_path.exists():
            self._log.appendPlainText("[ERROR] No valid .ti3 file selected.")
            self._log.ensureCursorVisible()
            return
        if self._runner.is_running:
            return

        params = self._collect_params()
        self._log.clear()
        self._build_btn.setText("Building Profile…")
        self._build_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._save_defaults_btn.setEnabled(False)
        self._file_grp.setEnabled(False)
        self._options_widget.setEnabled(False)
        self._progress_bar.start()
        self._progress_bar.setVisible(True)

        self._builder.build(
            params,
            on_line=self._on_log_line,
            on_finish=self._on_build_done,
        )

    def _on_log_line(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.ensureCursorVisible()

    def _on_build_done(self, code: int) -> None:
        self._build_btn.setText("Build Profile")
        self._build_btn.setEnabled(True)
        self._save_defaults_btn.setEnabled(True)
        self._file_grp.setEnabled(True)
        self._options_widget.setEnabled(True)
        self._progress_bar.stop()
        self._progress_bar.setVisible(False)

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
        if self._ti3_path:
            self.profile_built.emit(self._ti3_path, self._icc_path)

        self._show_build_result_dialog(self._icc_path, issues)

    def _show_build_result_dialog(self, icc_path: Path, issues: list[str]) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Profile Built")
        dlg.setMinimumWidth(520)

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

        btn_box = QDialogButtonBox(dlg)
        install_btn = btn_box.addButton("Install on this Mac",    QDialogButtonBox.ButtonRole.ActionRole)
        check_btn   = btn_box.addButton("Check Profile Quality →", QDialogButtonBox.ButtonRole.ActionRole)
        done_btn    = btn_box.addButton("Done",                    QDialogButtonBox.ButtonRole.AcceptRole)
        install_btn.setObjectName("primary")
        layout.addWidget(btn_box)

        def _on_install() -> None:
            dlg.accept()
            self._on_install()

        def _on_check() -> None:
            dlg.accept()
            self.check_requested.emit()

        install_btn.clicked.connect(_on_install)
        check_btn.clicked.connect(_on_check)
        done_btn.clicked.connect(dlg.accept)

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

    def _collect_params(self) -> ProfileParams:
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

    def _on_save_defaults(self) -> None:
        s = self._settings
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
        self._log.appendPlainText("Profile settings saved as defaults.")
        self._log.ensureCursorVisible()

    def _restore_defaults(self) -> None:
        s = self._settings

        def _set_combo(combo: QComboBox, key: str, default: str) -> None:
            idx = combo.findData(s.get(key, default))
            if idx >= 0:
                combo.setCurrentIndex(idx)

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
