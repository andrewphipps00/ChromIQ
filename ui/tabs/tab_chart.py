"""Tab 1: Chart Creation — Guided and Manual modes."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.resource_path import resource_path
from data.patch_db import (
    EXCLUDED_PAPERS,
    I1PRO_DEFAULT_PRESET_KEY,
    INSTRUMENT_DEFAULT_MARGIN,
    INSTRUMENT_LABELS,
    PAPER_FALLBACK,
    PAPER_LABELS,
    PAPER_SIZES,
    i1_defaults_from_preset,
    query_patches,
)
from ui.fade_scroll import FadeScrollArea
from ui.parameter_widget import ParameterWidget
from ui.styles import SPEC_AMBER, SPEC_CYAN, SPEC_GREEN, SPEC_MAGENTA, SPEC_VIOLET
from ui.tab_header import TabHeader
from ui.tiff_preview import TiffPreview
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollSpinBox, icc_profile_paths, make_browse_button, open_file_dialog, set_folder_icon, set_preset_icon
from workflow.chart_creator import ChartCreator, ChartParams
from workflow.tiff_metadata import ALLOWED_LEFT_CLIP_PAPERS

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings

log = get_logger(__name__)


def _value_compatible_with_pw(v: Any, pw: "ParameterWidget") -> bool:
    """True if v can be set on the widget without raising / warning.

    Used to suppress the noisy "set_value(-g, 'true')" warning that would
    otherwise fire while migrating a Windows-registry-corrupted legacy key.
    """
    t = getattr(pw, "_param", {}).get("type", "string")
    try:
        if t == "boolean":
            return v is not None
        if t in ("int",):
            int(v)
            return True
        if t in ("float",):
            float(v)
            return True
        return True
    except (TypeError, ValueError):
        return False


def _pw_settings_key(tool: str, flag: str) -> str:
    """Storage key for a tool parameter, case-disambiguated for Windows.

    QSettings on Windows uses HKCU which is case-insensitive, so the bare
    keys for -g (Grey Axis Steps, int) and -G (Good Mode, bool) collide and
    last-write-wins corrupts whichever was written first. Appending a one-char
    case marker after single-letter alpha flags eliminates the collision while
    leaving multi-character / non-alpha flags unchanged.
    """
    if len(flag) == 2 and flag.startswith("-") and flag[1].isalpha():
        return f"manual_{tool}_{flag}_{'u' if flag[1].isupper() else 'l'}"
    return f"manual_{tool}_{flag}"


def _extra_args_have_patch_source(extra: str) -> bool:
    """True if extra targen args contain a flag that produces patches on its own.

    targen needs at least one of -f, -g, -s, -c (preconditioning profile) or
    -m to produce a valid output. The first three live on dedicated widgets;
    this guard handles -c / -m / -V / -D buried in extra_targen_args.
    """
    if not extra:
        return False
    try:
        toks = shlex.split(extra)
    except ValueError:
        return False
    for tok in toks:
        if tok.startswith(("-c", "-V", "-D", "-m")):
            return True
    return False


class TabChart(QWidget):
    """Step 1: create targen/printtarg test chart."""

    chart_finished  = pyqtSignal(object, object)  # (list[Path] tiffs, Path ti2)
    target_started  = pyqtSignal()

    def __init__(
        self,
        runner: "ArgyllRunner",
        file_mgr: "FileManager",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner  = runner
        self._file_mgr = file_mgr
        self._settings = settings
        self._creator  = ChartCreator(runner, file_mgr, settings)
        self._params   = self._load_yaml_params()
        self._preconditioning_from_dialog = False

        self._build_ui()
        self._restore_defaults()

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(4)

        # Left: controls
        left = QWidget(self)
        self._left_panel = left
        left.setFixedWidth(580)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 16, 12)
        left_layout.setSpacing(8)

        left_layout.addWidget(TabHeader(
            "STEP 01 · GENERATE TARGET", "Create test chart", "#ff4573", left,
            tooltip_title="Step 1 — Make a test chart",
            tooltip_body=(
                "This is where you design the sheet of colour patches your printer "
                "will print. The patches are how ChromIQ later \"learns\" how your "
                "printer reproduces colour.\n\n"
                "Before you start:\n"
                "• Pick the printer and paper you actually want to profile — the "
                "profile will only be accurate for that exact combination.\n"
                "• Have a rough idea of how careful you want to be. More patches = "
                "more accuracy, but also more ink and paper.\n\n"
                "How to use this screen:\n"
                "• Guided mode picks sensible patch counts for you based on your "
                "paper size and instrument. Recommended if you're new.\n"
                "• Manual mode exposes every option. Use it once you know what each "
                "flag does.\n"
                "• Click \"Generate\" to create the test chart. You'll get a TIFF "
                "image (the printable chart) and a .ti2 file (the recipe ChromIQ "
                "uses later to read it back).\n\n"
                "Next step: print the TIFF on tab 2."
            ),
        ))

        # Mode switcher (wrapped in a widget so it can be hidden in calibration mode)
        self._mode_row_widget = QWidget(left)
        mode_row = QHBoxLayout(self._mode_row_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        _mode_font = QFont()
        _mode_font.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
        _mode_font.setPointSize(11)
        _mode_font.setWeight(QFont.Weight.Bold)
        self._guided_btn = QPushButton("GUIDED", self)
        self._guided_btn.setCheckable(True)
        self._guided_btn.setChecked(True)
        self._guided_btn.setObjectName("mode_btn")
        self._guided_btn.setFont(_mode_font)
        self._manual_btn = QPushButton("MANUAL", self)
        self._manual_btn.setCheckable(True)
        self._manual_btn.setObjectName("mode_btn")
        self._manual_btn.setFont(_mode_font)
        self._guided_btn.clicked.connect(lambda: self._switch_mode("guided"))
        self._manual_btn.clicked.connect(lambda: self._switch_mode("manual"))
        mode_row.addWidget(self._guided_btn)
        mode_row.addWidget(self._manual_btn)
        mode_row.addStretch()
        left_layout.addWidget(self._mode_row_widget)

        # Stacked panel
        self._stack = QStackedWidget(self)
        self._guided_panel = self._make_guided_panel()
        self._manual_panel = self._make_manual_panel()
        self._stack.addWidget(self._guided_panel)
        self._stack.addWidget(self._manual_panel)
        left_layout.addWidget(self._stack, stretch=1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self._generate_btn = QPushButton("Generate Chart", self)
        self._generate_btn.setObjectName("primary")
        self._generate_btn.setFixedHeight(36)
        self._generate_btn.clicked.connect(self._on_generate)

        self._load_ti1_btn = QPushButton("Load existing .ti1…", self)
        self._load_ti1_btn.setFixedHeight(36)
        set_folder_icon(self._load_ti1_btn, "folder_create")
        self._load_ti1_btn.clicked.connect(self._on_load_ti1)

        self._save_defaults_btn = QPushButton("Save as Defaults", self)
        self._save_defaults_btn.setFixedHeight(36)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)

        btn_row.addWidget(self._generate_btn)
        btn_row.addWidget(self._load_ti1_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        left_layout.addLayout(btn_row)

        # Log output
        from PyQt6.QtWidgets import QPlainTextEdit
        self._log = QPlainTextEdit(self)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(67)
        self._log.setPlaceholderText("Output will appear here…")
        left_layout.addWidget(self._log)

        # Status bar (replaces main-window status bar)
        self._status_bar_lbl = QLabel("", left)
        self._status_bar_lbl.setWordWrap(True)
        self._status_bar_lbl.setVisible(False)
        left_layout.addWidget(self._status_bar_lbl)

        splitter.addWidget(left)

        # Right: TIFF preview
        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 12)
        right_layout.setSpacing(0)
        self._preview = TiffPreview(right)
        self._preview.set_caption("CHART PREVIEW")
        right_layout.addWidget(self._preview, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Guided panel
    # ------------------------------------------------------------------

    def _make_guided_panel(self) -> QWidget:
        outer = QWidget(self)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = FadeScrollArea(outer)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)

        # Working folder / target name
        folder_grp = QGroupBox("Output", inner)
        folder_layout = QVBoxLayout(folder_grp)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Target name:", inner))
        self._target_name_edit = self._make_lineedit("", inner)
        # Live-update the guided command preview as the user types.
        self._target_name_edit.textChanged.connect(self._update_patch_count)
        name_row.addWidget(self._target_name_edit, stretch=1)
        name_row.addWidget(TooltipButton(
            "Target Name",
            "A short, descriptive name for this profiling session.\n\n"
            "This name is used for the output folder and all generated files "
            "(chart, TIFF, measurements, ICC profile) throughout the entire workflow. "
            "Choose a name that lets you identify the correct files for your printer "
            "and paper combination at a glance.\n\n"
            "Tip: combine your printer model, paper type, and instrument — "
            "e.g. Canon_Pro1000_Baryta_i1Pro3. Use underscores or dashes instead of spaces.",
            inner,
            min_width=540,
        ))
        folder_layout.addLayout(name_row)
        layout.addWidget(folder_grp)

        # Instrument
        instr_grp = QGroupBox("Measurement Instrument", inner)
        instr_layout = QVBoxLayout(instr_grp)
        instr_layout.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Instrument:", inner))
        self._instr_combo = NoScrollComboBox(inner)
        for code, label in INSTRUMENT_LABELS.items():
            self._instr_combo.addItem(label, code)
        self._instr_combo.currentIndexChanged.connect(self._update_patch_count)
        self._instr_combo.currentIndexChanged.connect(self._update_dd_visibility)
        self._instr_combo.currentIndexChanged.connect(self._rebuild_paper_combo)
        row.addWidget(self._instr_combo, stretch=1)
        row.addWidget(TooltipButton(
            "Measurement Instrument",
            "Tells the chart generator which spectrophotometer you will use to read "
            "the printed chart. The patch grid is built around that instrument's "
            "strip width, patch size and spacing — so getting this right is "
            "essential.\n\n"
            "  •  i1Pro / i1Pro 2 / i1Pro 3 — handheld strip reader, the most "
            "common choice. Reads a column of patches in one sweep.\n\n"
            "  •  i1Pro 3 Plus — larger-aperture version of the i1Pro 3. Reads "
            "bigger patches, so far fewer fit per sheet (~5× less than the "
            "regular i1Pro).\n\n"
            "  •  ColorMunki / i1Studio / ColorChecker Studio — entry-level "
            "device. Reads one patch at a time on its own; with the optional "
            "measuring rig it pairs them up (see the Double Density option).\n\n"
            "  •  SpectroScan — flatbed XY scanner. A motorised arm reads each "
            "patch individually, so it packs far more colours per sheet than any "
            "strip reader.\n\n"
            "Picking the wrong instrument produces a chart your device cannot "
            "align to or read reliably — you'll see \"patches not found\" or "
            "alignment errors when measuring.\n\n"
            "In Guided mode the layout adapts to this choice automatically.",
            inner,
            min_width=600,
        ))
        instr_layout.addLayout(row)

        # Double density (CM only)
        dd_row = QHBoxLayout()
        self._dd_check = QCheckBox("Double density (requires measuring rig)", inner)
        self._dd_check.toggled.connect(self._update_patch_count)
        self._dd_tooltip = TooltipButton(
            "Double Density (-h)",
            "Doubles the number of patches that fit in each measurement strip when "
            "using a ColorMunki / i1Studio / ColorChecker Studio.\n\n"
            "REQUIRES the physical measuring rig accessory — a clear plastic guide "
            "that mounts the instrument over the chart. Without the rig the device "
            "cannot align to the tighter patch spacing and will misread.\n\n"
            "With the rig you get roughly twice as many patches per page, which "
            "means either a more detailed profile from the same number of sheets, "
            "or the same profile quality on fewer sheets. Recommended for anyone "
            "with the rig — it's a strict upgrade on patch density.\n\n"
            "Has no effect on i1Pro, i1Pro 3 Plus or SpectroScan — the option is "
            "hidden when those are selected.",
            inner,
            min_width=600,
        )
        dd_row.addWidget(self._dd_check)
        dd_row.addStretch()
        dd_row.addWidget(self._dd_tooltip)
        instr_layout.addLayout(dd_row)
        layout.addWidget(instr_grp)

        # Paper
        paper_grp = QGroupBox("Paper", inner)
        paper_layout = QVBoxLayout(paper_grp)
        paper_row = QHBoxLayout()
        paper_row.addWidget(QLabel("Paper size:", inner))
        self._paper_combo = NoScrollComboBox(inner)
        self._paper_combo.currentIndexChanged.connect(self._update_patch_count)
        # Paper changes also affect ChromIQ-style gating, which decides whether
        # the guided -L checkbox is visible.
        self._paper_combo.currentIndexChanged.connect(self._update_dd_visibility)
        paper_row.addWidget(self._paper_combo, stretch=1)
        paper_row.addWidget(TooltipButton(
            "Paper Size",
            "Sets the dimensions of each sheet in the printed chart. The chart "
            "always fills the page edge to edge — bigger paper fits more "
            "patches, which means a more detailed profile from fewer sheets.\n\n"
            "Pick the same size you will actually print on, including its "
            "orientation. Strip readers (i1Pro family) read top-to-bottom, so:\n\n"
            "  •  Portrait — longer strips, fewer of them. Standard choice.\n\n"
            "  •  Landscape — shorter strips, more of them. Use this when your "
            "printer feeds landscape more reliably, or when a portrait sheet "
            "would leave the last strip too close to the paper edge.\n\n"
            "Some paper sizes are hidden depending on the selected instrument:\n\n"
            "  •  A3 Portrait is hidden for i1Pro — the landscape variant fits "
            "~43% more patches.\n\n"
            "  •  Small photo formats (5×7\", 4×6\") are hidden for i1Pro 3 "
            "Plus — its large patches don't leave a usable profile on those.\n\n"
            "If you change paper size mid-workflow, the recommended patch count "
            "and page count update automatically.",
            inner,
            min_width=600,
        ))
        paper_layout.addLayout(paper_row)
        layout.addWidget(paper_grp)

        # Pages + left border
        pages_grp = QGroupBox("Chart Size", inner)
        pages_layout = QVBoxLayout(pages_grp)
        pages_layout.setSpacing(6)

        pages_row = QHBoxLayout()
        pages_row.addWidget(QLabel("Number of pages:", inner))
        self._pages_spin = NoScrollSpinBox(inner)
        self._pages_spin.setRange(1, 20)
        self._pages_spin.setValue(1)
        self._pages_spin.valueChanged.connect(self._update_patch_count)
        pages_row.addWidget(self._pages_spin)
        pages_row.addStretch()
        pages_row.addWidget(TooltipButton(
            "Number of Pages",
            "How many physical sheets the chart spans. Each sheet is filled with "
            "as many patches as fit for the selected paper, instrument and layout "
            "— so total patches = patches-per-page × pages.\n\n"
            "More pages means more colour samples, which produces a more accurate "
            "profile. The trade-off is more ink, more paper and a longer reading "
            "session. Rough guide:\n\n"
            "  •  1 page — quick check or single-sheet workflows (~500 patches on "
            "A4 with an i1Pro). Fine for casual profiling.\n\n"
            "  •  2-3 pages — recommended for everyday photo printing. Good "
            "balance of accuracy versus effort.\n\n"
            "  •  4-5+ pages — professional or fine-art workflows where the "
            "profile needs to nail tricky tonal transitions and out-of-gamut "
            "colours.\n\n"
            "How many patches you actually need depends on your printer's colour "
            "gamut, ink set and how non-linear it behaves. When in doubt, more is "
            "better.",
            inner,
            min_width=600,
        ))
        pages_layout.addLayout(pages_row)

        lb_row = QHBoxLayout()
        self._lb_check = QCheckBox("Suppress left clip border (-L)", inner)
        self._lb_check.setChecked(True)
        self._lb_check.toggled.connect(self._update_patch_count)
        lb_row.addWidget(self._lb_check)
        lb_row.addStretch()
        self._lb_tooltip = TooltipButton(
            "Suppress Left Clip Border (-L)",
            "Removes the left-edge paper-clip border, gaining ~15 mm for extra patches.\n"
            "Enable unless you use a physical page-clamp jig.  Recommended: ON.",
            inner,
        )
        lb_row.addWidget(self._lb_tooltip)
        pages_layout.addLayout(lb_row)
        layout.addWidget(pages_grp)

        # Refinement / pre-conditioning (optional second-pass profile)
        precond_grp = QGroupBox("Refinement (Optional)", inner)
        precond_row = QHBoxLayout(precond_grp)
        precond_row.setSpacing(6)

        self._guided_precond_check = QCheckBox("Refinement profile", inner)
        self._guided_precond_check.toggled.connect(self._on_guided_precond_toggled)
        precond_row.addWidget(self._guided_precond_check)

        self._guided_precond_path = QLineEdit(inner)
        self._guided_precond_path.setReadOnly(True)
        self._guided_precond_path.setPlaceholderText("No profile selected")
        self._guided_precond_path.setEnabled(False)
        precond_row.addWidget(self._guided_precond_path, stretch=1)

        self._guided_precond_browse = make_browse_button(
            inner, "Select pre-conditioning profile", icon="folder_create",
        )
        self._guided_precond_browse.setEnabled(False)
        self._guided_precond_browse.clicked.connect(self._on_guided_precond_browse)
        precond_row.addWidget(self._guided_precond_browse)

        precond_row.addWidget(TooltipButton(
            "Refinement Profile (Pre-conditioning)",
            "Use this to make a second, noticeably better profile after you have "
            "already built and confirmed a working one for the same printer + paper.\n\n"
            "How it helps:\n"
            "Your first profile tells ChromIQ which colours your printer gets right "
            "and which it struggles with. When you turn this option on, ChromIQ uses "
            "that knowledge to place the new test patches more cleverly — sampling "
            "more in the regions your printer reproduces least accurately, and fewer "
            "in the regions it already nails. The end result is a profile that is "
            "more accurate where it matters, without needing more patches overall.\n\n"
            "When to use it:\n"
            "• You already have a first ICC profile (.icc or .icm) built from this "
            "same printer + paper combination.\n"
            "• You want to invest one more round of printing and measuring to get a "
            "noticeably better profile, especially for tricky papers (matte, baryta, "
            "fine-art).\n\n"
            "When NOT to use it:\n"
            "• On a first-ever profile for this paper — leave this off and just "
            "build the normal way.\n"
            "• If you don't have a working profile yet for this exact paper/printer.\n\n"
            "Tip: the more pages you print on the refinement pass, the more benefit "
            "the cleverer patch placement gives you.",
            inner,
            min_width=580,
        ))

        layout.addWidget(precond_grp)

        # Patch count display
        count_grp = QGroupBox("Calculated Patches", inner)
        # Only override what differs from the global QGroupBox QSS (zero top-padding
        # so the big number sits tight under the title). Border + title color come
        # from the active theme.
        count_grp.setStyleSheet("QGroupBox { padding-top: 0px; }")
        count_layout = QVBoxLayout(count_grp)
        count_layout.setContentsMargins(8, 0, 8, 12)
        count_layout.setSpacing(4)

        self._patch_count_lbl = QLabel("—", inner)
        self._patch_count_lbl.setObjectName("patch_count")
        self._patch_count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._patch_count_lbl.setStyleSheet(
            "background: transparent;"
            " font-family: Georgia; font-size: 56px;"
        )
        count_font = QFont()
        count_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 85)
        self._patch_count_lbl.setFont(count_font)
        count_layout.addWidget(self._patch_count_lbl)

        self._patch_detail_lbl = QLabel("", inner)
        self._patch_detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._patch_detail_lbl.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 9px; font-weight: 300;"
        )
        count_layout.addWidget(self._patch_detail_lbl)

        # 5-segment spectrum bar, centered
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 6, 0, 0)
        bar_row.setSpacing(0)
        bar_row.addStretch()
        for _color in (SPEC_MAGENTA, SPEC_AMBER, SPEC_GREEN, SPEC_CYAN, SPEC_VIOLET):
            _seg = QFrame(inner)
            _seg.setFixedSize(22, 2)
            _seg.setStyleSheet(f"background-color: {_color}; border: none;")
            bar_row.addWidget(_seg)
        bar_row.addStretch()
        count_layout.addLayout(bar_row)

        layout.addWidget(count_grp)

        # Hidden-defaults info box
        self._guided_info_lbl = QLabel("", inner)
        self._guided_info_lbl.setObjectName("info")
        self._guided_info_lbl.setWordWrap(True)
        layout.addWidget(self._guided_info_lbl)

        layout.addStretch()
        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    # ------------------------------------------------------------------
    # Manual panel
    # ------------------------------------------------------------------

    def _make_manual_panel(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Calibration target option (hidden until calibration mode is enabled)
        self._cal_target_grp = QGroupBox("Calibration Target", w)
        cal_tgt_layout = QVBoxLayout(self._cal_target_grp)
        cal_tgt_row = QHBoxLayout()
        self._cal_target_check = QCheckBox("Create target for calibration", w)
        cal_tgt_row.addWidget(self._cal_target_check)
        cal_tgt_row.addStretch()
        cal_tgt_row.addWidget(TooltipButton(
            "Create Target for Calibration",
            "Use this before running printcal to create a printer linearisation curve.\n\n"
            "When enabled:\n"
            "  • Output files are prefixed with 'cal_' (e.g. cal_MyChart.ti1)\n"
            "  • Patch count is set to 0 (auto), white and black patches set to 0\n"
            "  • Single channel steps set to 20, randomisation disabled\n"
            "  • Good distribution (-G) is disabled\n\n"
            "Generate the chart, print it, and measure it. The resulting cal_*.ti3\n"
            "file is automatically routed to the Create Calibration File module\n"
            "in the Calibration & Profiling tab.\n\n"
            "Existing cal_* files in your working folder are preserved when this\n"
            "option is OFF, so your .cal file survives the next chart generation.",
            w,
            min_width=560,
        ))
        cal_tgt_layout.addLayout(cal_tgt_row)

        self._cal_status_lbl = QLabel("", w)
        self._cal_status_lbl.setWordWrap(True)
        self._cal_status_lbl.setStyleSheet("color: #56d6a5; font-size: 11px;")
        self._cal_status_lbl.setVisible(False)
        cal_tgt_layout.addWidget(self._cal_status_lbl)

        self._cal_target_grp.setVisible(False)
        layout.addWidget(self._cal_target_grp)

        # Output (target name)
        output_grp = QGroupBox("Output", w)
        output_layout = QVBoxLayout(output_grp)
        # Shared label width keeps the "Target name:" and "Chart notes:"
        # input fields aligned vertically.
        _OUTPUT_LBL_W = 96
        name_row = QHBoxLayout()
        _name_lbl = QLabel("Target name:", w)
        _name_lbl.setFixedWidth(_OUTPUT_LBL_W)
        name_row.addWidget(_name_lbl)
        self._manual_target_name_edit = self._make_lineedit("", w)
        # Live-update the manual command preview as the user types.
        self._manual_target_name_edit.textChanged.connect(
            self._refresh_manual_command_preview
        )
        name_row.addWidget(self._manual_target_name_edit, stretch=1)
        name_row.addWidget(TooltipButton(
            "Target Name",
            "A short, descriptive name for this profiling session.\n\n"
            "This name is used for the output folder and all generated files "
            "(chart, TIFF, measurements, ICC profile) throughout the entire workflow. "
            "Choose a name that lets you identify the correct files for your printer "
            "and paper combination at a glance.\n\n"
            "Tip: combine your printer model, paper type, and instrument — "
            "e.g. Canon_Pro1000_Baryta_i1Pro3. Use underscores or dashes instead of spaces.",
            w,
            min_width=540,
        ))
        output_layout.addLayout(name_row)

        # Chart notes row — wrapped in a QWidget so it can be hidden when
        # ChromIQ-style clipping border is on (the right margin it targets
        # gets pushed off-page by the patch shift).
        self._manual_chart_notes_row = QWidget(w)
        m_notes_row = QHBoxLayout(self._manual_chart_notes_row)
        m_notes_row.setContentsMargins(0, 0, 0, 0)
        _notes_lbl = QLabel("Chart notes:", self._manual_chart_notes_row)
        _notes_lbl.setFixedWidth(_OUTPUT_LBL_W)
        m_notes_row.addWidget(_notes_lbl)
        self._manual_chart_notes_edit = self._make_lineedit("", self._manual_chart_notes_row)
        self._manual_chart_notes_edit.setPlaceholderText("e.g. Canon Pro-1000 / Hahnemühle Photo Rag 308")
        m_notes_row.addWidget(self._manual_chart_notes_edit, stretch=1)
        m_notes_row.addWidget(TooltipButton(
            "Chart Notes",
            "Optional free-text label stamped onto the right edge of the chart "
            "TIFFs alongside the targen and printtarg commands that produced them. "
            "Useful for recording the exact printer/paper combination this chart "
            "was made for, so you can match it to the right ICC profile months "
            "later. Patch pixels are not modified — only the white margin to the "
            "right of the patches is stamped.",
            self._manual_chart_notes_row,
            min_width=540,
        ))
        output_layout.addWidget(self._manual_chart_notes_row)

        # Stamp-commands row — also wrapped for ChromIQ-style hiding.
        self._manual_stamp_cmd_row = QWidget(w)
        stamp_row = QHBoxLayout(self._manual_stamp_cmd_row)
        stamp_row.setContentsMargins(0, 0, 0, 0)
        _stamp_lbl_spacer = QLabel("", self._manual_stamp_cmd_row)
        _stamp_lbl_spacer.setFixedWidth(_OUTPUT_LBL_W)
        stamp_row.addWidget(_stamp_lbl_spacer)
        self._manual_stamp_cmd_check = QCheckBox(
            "Stamp targen and printtarg commands on the chart", self._manual_stamp_cmd_row
        )
        self._manual_stamp_cmd_check.setChecked(True)
        stamp_row.addWidget(self._manual_stamp_cmd_check)
        stamp_row.addStretch()
        stamp_row.addWidget(TooltipButton(
            "Stamp Commands",
            "When enabled, the exact targen and printtarg commands used to "
            "produce the chart — plus the ChromIQ version — are stamped onto "
            "the right edge of the generated TIFF (alongside Argyll's own "
            "vertical ID line). This makes the chart self-documenting: months "
            "later you can read the printed sheet and recreate the same chart "
            "exactly. Disable if you'd rather keep the right margin clean and "
            "only stamp your own notes (or leave the chart fully unstamped if "
            "you also clear the notes field).",
            self._manual_stamp_cmd_row,
            min_width=540,
        ))
        output_layout.addWidget(self._manual_stamp_cmd_row)

        # Left-clip info row: only meaningful when -L is off on an i1Pro chart
        # with a large-enough paper. Wrap in a QWidget so setVisible(False)
        # collapses the empty space when the gating conditions aren't met.
        self._manual_left_clip_row = QWidget(w)
        left_clip_row = QHBoxLayout(self._manual_left_clip_row)
        left_clip_row.setContentsMargins(0, 0, 0, 0)
        _left_clip_lbl_spacer = QLabel("", self._manual_left_clip_row)
        _left_clip_lbl_spacer.setFixedWidth(_OUTPUT_LBL_W)
        left_clip_row.addWidget(_left_clip_lbl_spacer)
        self._manual_left_clip_check = QCheckBox(
            "Print info in left clip area", self._manual_left_clip_row
        )
        left_clip_row.addWidget(self._manual_left_clip_check)
        left_clip_row.addStretch()
        left_clip_row.addWidget(TooltipButton(
            "Left Clip Info",
            "Fills the wide blank strip on the LEFT side of the chart — the "
            "space printtarg reserves for the i1Pro 2 / i1Pro 3 Plus scanning-"
            "table clip — with two rotated text columns:\n\n"
            "• Outer column: a one-line chart summary (patch count + paper "
            "size), a print-driver reminder (borderless, no expansion, retain "
            "size, color management off), and a fill-in-the-blank form line "
            "for date, printer, ink set, profile name, paper and driver "
            "settings.\n"
            "• Inner column: orientation instructions for the i1Pro scanning "
            "table — which edge faces up and how to seat the sheet in the "
            "clip.\n\n"
            "This option is only available when:\n"
            "  • The instrument is i1Pro / i1Pro 2 or i1Pro 3 Plus.\n"
            "  • 'Suppress left clip border' is OFF (so the clip strip is "
            "actually reserved).\n"
            "  • The paper size is A4 / Letter or larger — smaller sheets "
            "have no room for legible rotated text.\n\n"
            "The row hides automatically when these conditions aren't met. "
            "Patch pixels are never modified — only the otherwise-empty left "
            "clip strip is stamped.",
            self._manual_left_clip_row,
            min_width=560,
        ))
        output_layout.addWidget(self._manual_left_clip_row)
        self._manual_left_clip_row.setVisible(False)

        layout.addWidget(output_grp)

        # Presets
        presets_grp = QGroupBox("Presets", w)
        presets_row = QHBoxLayout(presets_grp)
        presets_row.setContentsMargins(8, 4, 8, 8)
        presets_row.addWidget(QLabel("Select preset:", w))
        self._preset_combo = NoScrollComboBox(w)
        self._preset_combo.addItem("Default", userData=None)
        presets_row.addWidget(self._preset_combo, stretch=1)
        self._preset_add_btn = QPushButton(w)
        self._preset_add_btn.setObjectName("icon_btn")
        self._preset_add_btn.setFixedSize(28, 28)
        set_preset_icon(self._preset_add_btn, "plus")
        self._preset_add_btn.setIconSize(QSize(14, 14))
        self._preset_add_btn.setToolTip("Save current settings as a new preset")
        self._preset_del_btn = QPushButton(w)
        self._preset_del_btn.setObjectName("icon_btn")
        self._preset_del_btn.setFixedSize(28, 28)
        set_preset_icon(self._preset_del_btn, "minus")
        self._preset_del_btn.setIconSize(QSize(14, 14))
        self._preset_del_btn.setToolTip("Delete selected preset")
        self._preset_del_btn.setEnabled(False)
        presets_row.addWidget(self._preset_add_btn)
        presets_row.addWidget(self._preset_del_btn)
        presets_row.addWidget(TooltipButton(
            "Manual Presets",
            "Save and recall named snapshots of all Manual mode settings.\n\n"
            "  +  Save current parameter values as a new named preset.\n"
            "  −  Delete the currently selected preset.\n\n"
            "Select a preset from the dropdown to instantly restore all\n"
            "values. The Default entry always resets to built-in defaults.\n\n"
            "The target name field is not saved with presets.\n"
            "Presets persist between sessions.",
            w,
            min_width=520,
        ))
        layout.addWidget(presets_grp)

        scroll = FadeScrollArea(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(4)
        inner_layout.setContentsMargins(4, 4, 4, 4)

        self._manual_widgets: dict[str, list[ParameterWidget]] = {
            "targen": [], "printtarg": [],
        }
        self._manual_lb_pw: ParameterWidget | None = None
        self._manual_dd_pw: ParameterWidget | None = None
        self._manual_instr_pw: ParameterWidget | None = None
        self._manual_paper_pw: ParameterWidget | None = None
        self._manual_f_pw: ParameterWidget | None = None
        self._manual_a_pw: ParameterWidget | None = None
        self._manual_m_pw: ParameterWidget | None = None
        self._manual_cal_k_pw: ParameterWidget | None = None
        self._manual_cal_i_pw: ParameterWidget | None = None
        self._manual_auto_patches_check: QCheckBox | None = None
        self._manual_pages_spin: NoScrollSpinBox | None = None
        self._manual_pages_row: QWidget | None = None
        self._bit8_radio: QRadioButton | None = None
        self._bit16_radio: QRadioButton | None = None
        self._pre_cal_snapshot: dict | None = None
        self._d_cascade_widgets: list[ParameterWidget] = []

        for tool, params in [
            ("targen",    self._params.get("targen", [])),
            ("printtarg", self._params.get("printtarg", [])),
        ]:
            grp = QGroupBox(f"{tool} parameters", inner)
            grp_layout = QVBoxLayout(grp)

            basic_grp = QGroupBox("Basic", grp)
            basic_layout = QVBoxLayout(basic_grp)
            expert_grp = QGroupBox("Expert Options", grp)
            expert_layout = QVBoxLayout(expert_grp)

            for p in params:
                pw = ParameterWidget(p, inner, browse_icon="folder_create")
                pw.make_compact()
                flag = p.get("flag", "")

                if tool == "printtarg" and flag == "-t":
                    # Shrink the DPI spinbox and add 8-bit/16-bit radio buttons
                    pw._control.setMaximumWidth(90)
                    bg = QButtonGroup(pw)
                    self._bit8_radio = QRadioButton("8-bit", pw)
                    self._bit16_radio = QRadioButton("16-bit", pw)
                    self._bit8_radio.setChecked(True)
                    bg.addButton(self._bit8_radio)
                    bg.addButton(self._bit16_radio)
                    # Insert before the last item (tooltip button)
                    insert_at = pw.layout().count() - 1
                    pw.layout().insertWidget(insert_at,     self._bit8_radio)
                    pw.layout().insertWidget(insert_at + 1, self._bit16_radio)

                if tool == "targen" and flag == "-f":
                    # Shrink the patch-count spinbox and add an "Auto" checkbox
                    # that drives live estimation from current paper/layout settings.
                    self._manual_f_pw = pw
                    pw._control.setMaximumWidth(90)
                    self._manual_auto_patches_check = QCheckBox("Auto", pw)
                    self._manual_auto_patches_check.setToolTip(
                        "Auto-compute the patch count to fill exactly the number of\n"
                        "pages set under printtarg → Pages, using the current paper,\n"
                        "instrument, double-density, left-border, patch scale and margin."
                    )
                    insert_at = pw.layout().count() - 1
                    pw.layout().insertWidget(insert_at, self._manual_auto_patches_check)
                    self._manual_auto_patches_check.toggled.connect(
                        self._on_auto_patches_toggled
                    )

                if tool == "printtarg" and flag == "-L":
                    self._manual_lb_pw = pw
                    pw.value_changed.connect(self._update_manual_lb_visibility)
                if tool == "printtarg" and flag == "-h":
                    self._manual_dd_pw = pw
                if tool == "printtarg" and flag == "-i":
                    self._manual_instr_pw = pw
                    pw.value_changed.connect(self._update_manual_lb_visibility)
                    pw.value_changed.connect(self._apply_instrument_default_margin)
                if tool == "printtarg" and flag == "-p":
                    self._manual_paper_pw = pw
                    pw.value_changed.connect(self._update_manual_lb_visibility)
                if tool == "printtarg" and flag == "-a":
                    self._manual_a_pw = pw
                if tool == "printtarg" and flag == "-m":
                    self._manual_m_pw = pw
                if tool == "printtarg" and flag == "-K":
                    self._manual_cal_k_pw = pw
                if tool == "printtarg" and flag == "-I":
                    self._manual_cal_i_pw = pw

                if tool == "targen" and flag == "-D":
                    self._d_cascade_widgets.append(pw)
                    expert_layout.addWidget(pw)
                    self._manual_widgets[tool].append(pw)
                    for _ in range(10):
                        extra_pw = ParameterWidget(p, inner)
                        extra_pw.make_compact()
                        extra_pw.setVisible(False)
                        self._d_cascade_widgets.append(extra_pw)
                        expert_layout.addWidget(extra_pw)
                        self._manual_widgets[tool].append(extra_pw)
                elif p.get("expert_only", False):
                    expert_layout.addWidget(pw)
                    self._manual_widgets[tool].append(pw)
                else:
                    basic_layout.addWidget(pw)
                    self._manual_widgets[tool].append(pw)

            # Insert the Pages row right under printtarg -p (paper size).
            # Drives the Auto patch-count estimate; greyed out unless Auto is on.
            if tool == "printtarg" and self._manual_paper_pw is not None:
                pages_row_w = QWidget(basic_grp)
                pages_row_l = QHBoxLayout(pages_row_w)
                pages_row_l.setContentsMargins(0, 2, 0, 2)
                pages_row_l.setSpacing(8)
                pages_lbl = QLabel("Pages:", pages_row_w)
                pages_lbl.setFixedWidth(190)
                from ui.theme import resolve_mode
                _pages_mode = resolve_mode(self._settings.get("appearance", "auto"))
                pages_lbl.setStyleSheet(
                    f"color: {'#22211F' if _pages_mode == 'light' else '#c8c8c8'};"
                )
                pages_row_l.addWidget(pages_lbl)
                self._manual_pages_spin = NoScrollSpinBox(pages_row_w)
                self._manual_pages_spin.setObjectName("compact_input")
                self._manual_pages_spin.setRange(1, 20)
                self._manual_pages_spin.setValue(1)
                self._manual_pages_spin.setMaximumWidth(90)
                self._manual_pages_spin.setEnabled(False)
                pages_row_l.addWidget(self._manual_pages_spin)
                pages_row_l.addStretch()
                pages_row_l.addWidget(TooltipButton(
                    "Pages (Auto patch count)",
                    "How many physical sheets the chart should span. This control "
                    "drives the Auto checkbox next to targen → Total Patch Count "
                    "above: when Auto is on, ChromIQ picks the patch count that "
                    "fills exactly this many sheets — using the current paper, "
                    "instrument, double-density / hexagon, left-border, patch "
                    "scale and margin settings. Total patches = patches-per-page "
                    "× pages.\n\n"
                    "More pages means more colour samples, which produces a more "
                    "accurate profile. The trade-off is more ink, more paper and "
                    "a longer reading session. Rough guide:\n\n"
                    "  •  1 page — quick check or single-sheet workflows "
                    "(~500 patches on A4 with an i1Pro). Fine for casual "
                    "profiling.\n\n"
                    "  •  2-3 pages — recommended for everyday photo printing. "
                    "Good balance of accuracy versus effort.\n\n"
                    "  •  4-5+ pages — professional or fine-art workflows where "
                    "the profile needs to nail tricky tonal transitions and "
                    "out-of-gamut colours.\n\n"
                    "This control is greyed out when Auto is off — without Auto, "
                    "printtarg just uses as many sheets as the explicit Total "
                    "Patch Count requires.",
                    pages_row_w,
                    min_width=600,
                ))
                idx = basic_layout.indexOf(self._manual_paper_pw)
                basic_layout.insertWidget(idx + 1 if idx >= 0 else basic_layout.count(),
                                          pages_row_w)
                self._manual_pages_row = pages_row_w

            grp_layout.addWidget(basic_grp)
            grp_layout.addWidget(expert_grp)
            inner_layout.addWidget(grp)

        self._update_manual_lb_visibility()
        self._apply_instrument_default_margin()
        self._connect_cal_mutex()
        self._connect_d_cascade()
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self._preset_add_btn.clicked.connect(self._on_preset_save)
        self._preset_del_btn.clicked.connect(self._on_preset_delete)
        self._manual_target_name_edit.textChanged.connect(self._check_for_cal_file)
        self._cal_target_check.toggled.connect(self._on_cal_target_toggled)
        # Cal-target prefix changes the displayed name — refresh the preview too.
        self._cal_target_check.toggled.connect(self._refresh_manual_command_preview)

        # Live command preview — mirrors the guided info box but reflects the
        # actual targen / printtarg args the workflow will build from the
        # current ParameterWidget state.  Sits at the bottom of the scrollable
        # area so it follows the last parameter group.
        self._manual_info_lbl = QLabel("", inner)
        self._manual_info_lbl.setObjectName("info")
        self._manual_info_lbl.setWordWrap(True)
        inner_layout.addWidget(self._manual_info_lbl)

        # Wire every parameter widget to refresh the live command preview.
        for tool in ("targen", "printtarg"):
            for pw in self._manual_widgets.get(tool, []):
                pw.value_changed.connect(self._refresh_manual_command_preview)
        if self._manual_pages_spin is not None:
            self._manual_pages_spin.valueChanged.connect(
                self._refresh_manual_command_preview
            )
        if self._manual_auto_patches_check is not None:
            self._manual_auto_patches_check.toggled.connect(
                self._refresh_manual_command_preview
            )
        if self._bit16_radio is not None:
            self._bit16_radio.toggled.connect(self._refresh_manual_command_preview)
        self._refresh_manual_command_preview()

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return w

    def _refresh_manual_command_preview(self) -> None:
        """Rebuild the manual info label from the current ParameterWidget state.

        Mirrors workflow/chart_creator.py:_build_targen_args /
        _build_printtarg_args so the preview matches exactly what runs."""
        if getattr(self, "_manual_info_lbl", None) is None:
            return
        try:
            p = self._collect_manual()
        except Exception:
            self._manual_info_lbl.setText("Manual mode — preview unavailable.")
            return

        # targen
        targen_args: list[str] = [f"-d{p.device_type}"]
        patches = int(p.patches)
        if self._manual_auto_patches_check is not None \
                and self._manual_auto_patches_check.isChecked() \
                and self._manual_f_pw is not None:
            try:
                patches = int(self._manual_f_pw.get_raw_value() or 0)
            except (TypeError, ValueError):
                pass
        targen_args += [f"-f{patches}"]
        targen_args += [f"-e{p.white_patches}", f"-B{p.black_patches}"]
        if p.good_mode:
            targen_args.append("-G")
        if p.grey_steps > 0:
            targen_args += [f"-g{p.grey_steps}"]
        if p.single_channel_steps > 0:
            targen_args += [f"-s{p.single_channel_steps}"]
        if p.extra_targen_args:
            targen_args += shlex.split(p.extra_targen_args)
        targen_args.append(self._preview_target_name("manual"))

        # printtarg
        pt_args: list[str] = []
        pt_instr = "3p" if p.instrument == "p3" else p.instrument
        pt_args.append(f"-i{pt_instr}")
        pt_args.append(f"-p{p.paper}")
        dpi_flag = "-T" if p.tiff_16bit else "-t"
        pt_args.append(f"{dpi_flag}{p.tiff_dpi}")
        if p.double_density and p.instrument in {"CM", "SS"}:
            pt_args.append("-h")
        # Mirror chart_creator._build_printtarg_args: ChromIQ-style clipping
        # border forces -L regardless of the per-chart toggle, so the preview
        # has to reflect that too.
        from workflow.chart_creator import _chromiq_clip_active
        force_l = _chromiq_clip_active(p)
        if (p.disable_left_border or force_l) and p.instrument in {"i1", "p3"}:
            pt_args.append("-L")
        if abs(p.patch_scale - 1.0) > 0.01:
            pt_args.append(f"-a{p.patch_scale:.2f}")
        if p.margin_mm != 6:
            pt_args.append(f"-m{p.margin_mm}")
        pt_args.append(f"-M{p.margin_mm}")
        if p.no_randomise:
            pt_args.append("-r")
        if p.bw_spacers:
            pt_args.append("-b")
        if p.no_strip_limit:
            pt_args.append("-P")
        if p.extra_printtarg_args:
            pt_args += shlex.split(p.extra_printtarg_args)
        pt_args.append(self._preview_target_name("manual"))

        pages = (
            self._manual_pages_spin.value()
            if self._manual_pages_spin is not None else 1
        )
        notes = [f"{pages} page{'s' if pages != 1 else ''}"]
        if self._manual_auto_patches_check is not None \
                and self._manual_auto_patches_check.isChecked():
            notes.append("Auto patch count")
        if p.tiff_16bit:
            notes.append("16-bit TIFF")

        info = (
            f"Manual mode — your current configuration ({' · '.join(notes)}):\n"
            f"targen {' '.join(targen_args)}\n"
            f"printtarg {' '.join(pt_args)}"
        )
        self._manual_info_lbl.setText(info)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def set_calibration_mode(self, enabled: bool) -> None:
        """Show/hide calibration-specific UI and lock to manual mode when enabled."""
        self._mode_row_widget.setVisible(not enabled)
        self._cal_target_grp.setVisible(enabled)
        if enabled:
            self._switch_mode("manual")
            if not self._cal_target_check.isChecked():
                self._check_for_cal_file(self._manual_target_name_edit.text())
        else:
            self._cal_target_check.setChecked(False)

    def set_cal_file_paths(self, cal_path: "Path") -> None:
        """Pre-fill the -I and -K parameter widgets with the given .cal path."""
        from pathlib import Path
        cal_str = str(cal_path)
        if self._manual_cal_k_pw is not None:
            self._manual_cal_k_pw.set_value(cal_str)
        if self._manual_cal_i_pw is not None:
            self._manual_cal_i_pw.set_value(cal_str)
        self._cal_target_check.setChecked(False)

    def _check_for_cal_file(self, name: str) -> None:
        """Live check: if cal_<name>.cal exists in working folder, prefill -I and -K."""
        name = name.strip()
        if not name:
            self._cal_status_lbl.setVisible(False)
            return
        cal_file = self._file_mgr.working_dir() / f"cal_{name}.cal"
        if cal_file.exists():
            cal_str = str(cal_file)
            if self._manual_cal_k_pw is not None:
                self._manual_cal_k_pw.set_value(cal_str)
            if self._manual_cal_i_pw is not None:
                self._manual_cal_i_pw.set_value(cal_str)
            self._cal_status_lbl.setText(
                f"Calibration file found: {cal_file.name} — auto-filled into -I and -K fields below."
            )
            self._cal_status_lbl.setVisible(True)
        else:
            self._cal_status_lbl.setVisible(False)

    _PREVIEW_NAME_MAX_LEN = 32

    def _preview_target_name(self, mode: str) -> str:
        """Return the target name as it will appear in the command preview.

        Falls back to "chart" when the name field is empty, matching the
        default that ChartCreator uses at generate time. Prefixes "cal_"
        when the Calibration Target checkbox is active (manual mode only).
        Truncates with an ellipsis when longer than _PREVIEW_NAME_MAX_LEN
        characters so an unbroken name can't force the info-box wider than
        its container — the *actual* target name used at Generate-click is
        read directly from the line edit, not from this helper.
        """
        if mode == "guided":
            edit = getattr(self, "_target_name_edit", None)
        else:
            edit = getattr(self, "_manual_target_name_edit", None)
        name = (edit.text().strip() if edit is not None else "") or "chart"

        if mode == "manual" and getattr(self, "_cal_target_check", None) is not None:
            grp = getattr(self, "_cal_target_grp", None)
            if (self._cal_target_check.isChecked()
                    and grp is not None and grp.isVisible()):
                name = f"cal_{name}"

        if len(name) > self._PREVIEW_NAME_MAX_LEN:
            name = name[: self._PREVIEW_NAME_MAX_LEN - 1] + "…"
        return name

    def _on_cal_target_toggled(self, checked: bool) -> None:
        _CAL_VALUES: list[tuple[str, str, Any]] = [
            ("targen",    "-f",  0),
            ("targen",    "-e",  0),
            ("targen",    "-B",  0),
            ("targen",    "-s",  20),
            ("targen",    "-G",  False),
            ("printtarg", "-r",  True),
        ]
        if checked:
            self._pre_cal_snapshot = {}
            for tool, flag, val in _CAL_VALUES:
                for pw in self._manual_widgets.get(tool, []):
                    if pw.flag == flag:
                        self._pre_cal_snapshot[(tool, flag)] = pw.get_raw_value()
                        pw.set_value(val)
        else:
            if self._pre_cal_snapshot:
                for tool, flag, _ in _CAL_VALUES:
                    saved = self._pre_cal_snapshot.get((tool, flag))
                    if saved is not None:
                        for pw in self._manual_widgets.get(tool, []):
                            if pw.flag == flag:
                                pw.set_value(saved)
            self._pre_cal_snapshot = None

    def _make_lineedit(self, text: str, parent: QWidget) -> Any:
        from PyQt6.QtWidgets import QLineEdit
        le = QLineEdit(parent)
        le.setText(text)
        return le

    def _load_yaml_params(self) -> dict:
        path = resource_path("data/parameters.yaml")
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("parameters", {})
        except Exception as exc:
            log.error("Cannot load parameters.yaml: %s", exc)
            return {}

    def _switch_mode(self, mode: str) -> None:
        if mode == "guided":
            self._stack.setCurrentIndex(0)
            self._guided_btn.setChecked(True)
            self._manual_btn.setChecked(False)
        else:
            self._stack.setCurrentIndex(1)
            self._guided_btn.setChecked(False)
            self._manual_btn.setChecked(True)

    def _on_guided_precond_toggled(self, checked: bool) -> None:
        self._guided_precond_path.setEnabled(checked)
        self._guided_precond_browse.setEnabled(checked)
        if not checked:
            # Forget the "came from a result dialog" hint — only honor it while the
            # checkbox is actively ticked so that toggling off and back on doesn't
            # silently re-arm the rename-instead-of-wipe behavior.
            self._preconditioning_from_dialog = False
        self._update_patch_count()

    def _on_guided_precond_browse(self) -> None:
        start = self._guided_precond_path.text().strip()
        if start:
            start = str(Path(start).parent)
        path = open_file_dialog(
            self, "Select pre-conditioning profile",
            "ICC / MPP profiles (*.icc *.icm *.mpp)",
            start_dir=start,
            extra_path=self._settings.get("custom_output_path", ""),
            extra_paths=icc_profile_paths(),
        )
        if path:
            self._guided_precond_path.setText(path)
            self._update_patch_count()

    def apply_preconditioning(self, profile_path: Path | str) -> None:
        """Programmatically pre-fill pre-conditioning from a result dialog.

        Called by the main window when the user clicks "Use as pre-conditioning
        profile" in the Build Profile or Check/Refine result dialog. Switches to
        guided mode, ticks the checkbox, fills the path picker, and arms the
        rename-instead-of-wipe behavior for the next Generate Chart click.
        """
        self._switch_mode("guided")
        self._guided_precond_path.setText(str(profile_path))
        self._guided_precond_check.setChecked(True)
        self._preconditioning_from_dialog = True

    def _current_mode(self) -> str:
        return "guided" if self._stack.currentIndex() == 0 else "manual"

    def refresh_chromiq_clip_visibility(self) -> None:
        """Re-evaluate ChromIQ-style-driven UI visibility.

        Called by MainWindow after the Settings dialog closes so toggling the
        'Use ChromIQ-style clipping border' preference takes effect on the
        Create Chart tab without needing the user to bump instrument or paper.
        """
        if hasattr(self, "_update_dd_visibility"):
            self._update_dd_visibility()
        self._update_manual_lb_visibility()

    def _chromiq_force_l(self, instr: str, paper: str) -> bool:
        """True iff ChromIQ-style clipping border forces -L for this instr+paper.

        Mirrors workflow.chart_creator._chromiq_clip_active gating so patch-
        count lookups and command previews agree with what actually runs.
        """
        return (
            bool(self._settings.get("i1pro_chromiq_clip_style", False))
            and instr in {"i1", "p3"}
            and paper in ALLOWED_LEFT_CLIP_PAPERS
        )

    def _chromiq_clip_active_in_ui(self) -> bool:
        """True iff the ChromIQ-style clipping border WILL be applied.

        Mirrors `workflow.chart_creator._chromiq_clip_active`: setting on AND
        instrument is i1Pro family AND paper >= A4 AND the user did NOT
        suppress the left clip border. Checking the suppress toggle disables
        the ChromIQ branded strip even when the setting is on, so the per-
        chart toggle remains the user's escape hatch.
        """
        if self._current_mode() == "guided":
            instr = self._instr_combo.currentData() or "i1"
            paper = self._paper_combo.currentData() or "A4"
            suppress = self._lb_check.isChecked()
        else:
            instr = (self._manual_instr_pw.get_raw_value()
                     if self._manual_instr_pw is not None else "i1") or "i1"
            paper = (self._manual_paper_pw.get_raw_value()
                     if self._manual_paper_pw is not None else "A4") or "A4"
            suppress = (bool(self._manual_lb_pw.get_raw_value())
                        if self._manual_lb_pw is not None else False)
        return self._chromiq_force_l(instr, paper) and not suppress

    def _update_manual_lb_visibility(self) -> None:
        if self._manual_instr_pw is None:
            return
        instr = self._manual_instr_pw.get_raw_value() or "i1"
        chromiq_clip = self._chromiq_clip_active_in_ui()

        # -L only matters for strip instruments. Even with ChromIQ-style on,
        # the row stays visible: unchecked = branded strip, checked = no
        # border (commands/notes route to the right margin as usual).
        if self._manual_lb_pw is not None:
            self._manual_lb_pw.setVisible(instr in {"i1", "p3"})

        # Chart notes + stamp-commands rows stay available in all modes. Under
        # ChromIQ-style their content is routed into a clip-border column
        # instead of the right margin (handled in chart_creator).
        if getattr(self, "_manual_chart_notes_row", None) is not None:
            self._manual_chart_notes_row.setVisible(True)
        if getattr(self, "_manual_stamp_cmd_row", None) is not None:
            self._manual_stamp_cmd_row.setVisible(True)

        # Left clip info row: hidden under ChromIQ-style (the stamp always
        # runs there, no opt-in needed). Otherwise visible only when -L is
        # OFF on a suitable i1Pro chart.
        if getattr(self, "_manual_left_clip_row", None) is not None:
            paper = (self._manual_paper_pw.get_raw_value()
                     if self._manual_paper_pw is not None else "A4") or "A4"
            lb_on = (bool(self._manual_lb_pw.get_raw_value())
                     if self._manual_lb_pw is not None else False)
            show_left_clip = (
                not chromiq_clip
                and instr in {"i1", "p3"}
                and not lb_on
                and paper in ALLOWED_LEFT_CLIP_PAPERS
            )
            self._manual_left_clip_row.setVisible(show_left_clip)
        # -h is offered on CM (double density) and SS (hexagon patches);
        # relabel per instrument so the meaning is clear.
        if self._manual_dd_pw is not None:
            if instr == "CM":
                self._manual_dd_pw.setVisible(True)
                self._manual_dd_pw.set_display_text(
                    "Double density (for measuring rig)",
                    "Double Density (-h)",
                    "Doubles the number of patches that fit in each measurement "
                    "strip when using a ColorMunki / i1Studio / ColorChecker "
                    "Studio.\n\n"
                    "REQUIRES the physical measuring rig accessory — a clear "
                    "plastic guide that mounts the instrument over the chart. "
                    "Without the rig the device cannot align to the tighter "
                    "patch spacing and will misread.\n\n"
                    "With the rig you get roughly twice as many patches per "
                    "page, which means either a more detailed profile from the "
                    "same number of sheets, or the same profile quality on "
                    "fewer sheets. Recommended for anyone with the rig — it's "
                    "a strict upgrade on patch density.\n\n"
                    "Has no effect on i1Pro, i1Pro 3 Plus or SpectroScan — the "
                    "option is hidden when those are selected.",
                    tooltip_min_width=600,
                )
            elif instr == "SS":
                self._manual_dd_pw.setVisible(True)
                self._manual_dd_pw.set_display_text(
                    "Hexagon patches",
                    "Hexagon Patches (-h)",
                    "Switches the SpectroScan chart layout from rectangular to "
                    "hexagonal patches. Hexagons tessellate more tightly than "
                    "rectangles, so roughly 14% more patches fit on the same "
                    "sheet — useful for squeezing extra colour samples out of "
                    "large papers.\n\n"
                    "No extra hardware is required. The SpectroScan's XY scanner "
                    "reads each patch individually under a motorised arm, so it "
                    "doesn't care whether the patch is square or hexagonal.\n\n"
                    "Has no effect on i1Pro, i1Pro 3 Plus or ColorMunki — the "
                    "option is hidden when those are selected.",
                    tooltip_min_width=600,
                )
            else:
                self._manual_dd_pw.setVisible(False)
                # Clear hidden -h so it can't leak into printtarg the next time
                # the user switches back to CM/SS. Mirror of guided mode.
                if self._manual_dd_pw.get_raw_value():
                    self._manual_dd_pw.set_value(False)

    def _apply_instrument_default_margin(self) -> None:
        """Auto-update -m (and -a, for i1) widgets to the per-instrument default
        on instrument change.

        Only overwrites known preset values so a user who deliberately set a
        custom margin (e.g. 12) or scale (e.g. 0.85) keeps their value when
        flipping instruments.

        For instrument == "i1" the (margin, scale) pair comes from the
        Preferences → i1Pro Chart Defaults setting. For other instruments only
        the margin is touched (legacy behaviour).
        """
        if self._manual_instr_pw is None or self._manual_m_pw is None:
            return
        instr = self._manual_instr_pw.get_raw_value() or "i1"

        if instr == "i1":
            preset_key = str(self._settings.get(
                "i1pro_default_preset", I1PRO_DEFAULT_PRESET_KEY
            ))
            target_margin, target_scale = i1_defaults_from_preset(preset_key)
        else:
            target_margin = INSTRUMENT_DEFAULT_MARGIN.get(instr, 6)
            # Non-i1 instruments use the printtarg native default (-a 1.0).
            # Switching away from i1 must undo any 0.95 the i1pro preset set.
            target_scale = 1.0

        try:
            current_m = int(self._manual_m_pw.get_raw_value() or 6)
        except (TypeError, ValueError):
            current_m = None
        if current_m in (6, 10) and current_m != target_margin:
            self._manual_m_pw.set_value(target_margin)

        if self._manual_a_pw is not None:
            try:
                current_a = float(self._manual_a_pw.get_raw_value() or 1.0)
            except (TypeError, ValueError):
                current_a = None
            # Only override if the current scale is one of the known preset
            # values — leave custom scales (e.g. 0.85, 1.1) intact.
            if current_a is not None and any(
                abs(current_a - known) <= 0.01 for known in (1.0, 0.95)
            ) and abs(current_a - target_scale) > 0.01:
                self._manual_a_pw.set_value(target_scale)

    # ------------------------------------------------------------------
    # Auto patch-count (Manual mode)
    # ------------------------------------------------------------------

    def _on_auto_patches_toggled(self, checked: bool) -> None:
        """Enable/disable -f and Pages spinboxes; show 'Auto' placeholder in -f.

        The actual patch-count estimate runs at Generate-click — see
        _on_generate — so this handler is purely UI state.
        """
        if self._manual_pages_spin is not None:
            self._manual_pages_spin.setEnabled(checked)
        if self._manual_f_pw is None or self._manual_f_pw._control is None:
            return
        spin = self._manual_f_pw._control
        self._manual_f_pw.set_control_enabled(not checked)
        spin.blockSignals(True)
        if checked:
            # QSpinBox shows specialValueText whenever value == minimum.
            # -f's min is 0 (see data/parameters.yaml), so set 0 here.
            spin.setSpecialValueText("Auto")
            spin.setValue(0)
        else:
            spin.setSpecialValueText("")
        spin.blockSignals(False)

    def _connect_cal_mutex(self) -> None:
        k, i = self._manual_cal_k_pw, self._manual_cal_i_pw
        if k is None or i is None:
            return
        k.value_changed.connect(lambda: i.set_user_enabled(False) if k.is_enabled_by_user else None)
        i.value_changed.connect(lambda: k.set_user_enabled(False) if i.is_enabled_by_user else None)

    def _connect_d_cascade(self) -> None:
        for i, pw in enumerate(self._d_cascade_widgets):
            pw.value_changed.connect(lambda _=None, idx=i: self._on_d_cascade(idx))

    def _rebuild_d_cascade_visibility(self) -> None:
        for i, pw in enumerate(self._d_cascade_widgets):
            if i == 0:
                pw.setVisible(True)
            else:
                pw.setVisible(self._d_cascade_widgets[i - 1].is_enabled_by_user)

    def _on_d_cascade(self, index: int) -> None:
        pw = self._d_cascade_widgets[index]
        nxt = index + 1
        if pw.is_enabled_by_user:
            if nxt < len(self._d_cascade_widgets):
                self._d_cascade_widgets[nxt].setVisible(True)
        else:
            for i in range(nxt, len(self._d_cascade_widgets)):
                w = self._d_cascade_widgets[i]
                w.set_user_enabled(False)
                w.setVisible(False)

    # ------------------------------------------------------------------
    # Preset helpers
    # ------------------------------------------------------------------

    def _load_presets_from_settings(self) -> dict:
        raw = self._settings.get("manual_presets", "")
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _save_presets_to_settings(self, presets: dict) -> None:
        self._settings.set("manual_presets", json.dumps(presets))

    def _populate_preset_combo(self, presets: dict, select_name: str | None = None) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("Default", userData=None)
        for name in presets:
            self._preset_combo.addItem(name, userData=name)
        if select_name is not None:
            idx = self._preset_combo.findText(select_name)
            if idx >= 0:
                self._preset_combo.setCurrentIndex(idx)
        self._preset_combo.blockSignals(False)
        self._preset_del_btn.setEnabled(self._preset_combo.currentIndex() > 0)

    def _on_preset_selected(self, index: int) -> None:
        self._preset_del_btn.setEnabled(index > 0)
        s = self._settings
        if index == 0:
            for tool, widgets in self._manual_widgets.items():
                for pw in widgets:
                    if pw in self._d_cascade_widgets:
                        continue
                    v = s.get(f"manual_{tool}_{pw.flag}")
                    if v is not None:
                        pw.set_value(v)
            for idx, pw in enumerate(self._d_cascade_widgets):
                v = s.get(f"manual_targen_-D_{idx}")
                if v is not None:
                    pw.set_value(v)
                pw.set_user_enabled(bool(s.get(f"manual_targen_-D_{idx}_enabled", False)))
            self._rebuild_d_cascade_visibility()
            if self._bit8_radio is not None and self._bit16_radio is not None:
                is_16bit = bool(s.get("manual_printtarg_tiff_16bit", False))
                self._bit16_radio.setChecked(is_16bit)
                self._bit8_radio.setChecked(not is_16bit)
            if self._manual_pages_spin is not None:
                self._manual_pages_spin.setValue(int(s.get("manual_pages", 1)))
            if self._manual_auto_patches_check is not None:
                auto_on = bool(s.get("manual_auto_patches", False))
                self._manual_auto_patches_check.setChecked(auto_on)
                self._on_auto_patches_toggled(auto_on)
            self._manual_left_clip_check.setChecked(
                bool(s.get("chart_left_clip_info", False))
            )
        else:
            name = self._preset_combo.currentData()
            presets = self._load_presets_from_settings()
            data = presets.get(name, {})
            for tool, widgets in self._manual_widgets.items():
                for pw in widgets:
                    if pw in self._d_cascade_widgets:
                        continue
                    v = data.get(f"{tool}_{pw.flag}")
                    if v is not None:
                        pw.set_value(v)
            for idx, pw in enumerate(self._d_cascade_widgets):
                v = data.get(f"targen_-D_{idx}")
                if v is not None:
                    pw.set_value(v)
                pw.set_user_enabled(bool(data.get(f"targen_-D_{idx}_enabled", False)))
            self._rebuild_d_cascade_visibility()
            if self._bit8_radio is not None and self._bit16_radio is not None:
                is_16bit = bool(data.get("tiff_16bit", False))
                self._bit16_radio.setChecked(is_16bit)
                self._bit8_radio.setChecked(not is_16bit)
            if self._manual_pages_spin is not None:
                self._manual_pages_spin.setValue(int(data.get("pages", 1)))
            if self._manual_auto_patches_check is not None:
                auto_on = bool(data.get("auto_patches", False))
                self._manual_auto_patches_check.setChecked(auto_on)
                self._on_auto_patches_toggled(auto_on)
            self._manual_left_clip_check.setChecked(
                bool(data.get("left_clip_info", False))
            )
        self._update_manual_lb_visibility()

    def _on_preset_save(self) -> None:
        capture: dict = {}
        for tool, widgets in self._manual_widgets.items():
            for pw in widgets:
                if pw in self._d_cascade_widgets:
                    continue
                v = pw.get_raw_value()
                if v is not None:
                    capture[f"{tool}_{pw.flag}"] = v
        for idx, pw in enumerate(self._d_cascade_widgets):
            capture[f"targen_-D_{idx}"] = pw.get_raw_value()
            capture[f"targen_-D_{idx}_enabled"] = pw.is_enabled_by_user
        capture["tiff_16bit"] = (
            self._bit16_radio.isChecked() if self._bit16_radio is not None else False
        )
        capture["auto_patches"] = (
            self._manual_auto_patches_check.isChecked()
            if self._manual_auto_patches_check is not None else False
        )
        capture["pages"] = (
            int(self._manual_pages_spin.value())
            if self._manual_pages_spin is not None else 1
        )
        capture["left_clip_info"] = bool(self._manual_left_clip_check.isChecked())
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Save Preset")
        dlg.setLabelText(
            "Give this preset a name.\n"
            "All current Manual mode parameter values will be saved under that name\n"
            "and can be recalled at any time from the preset list."
        )
        dlg.setMinimumWidth(460)
        if not dlg.exec():
            return
        name = dlg.textValue().strip()
        if not name:
            return
        presets = self._load_presets_from_settings()
        presets[name] = capture
        self._save_presets_to_settings(presets)
        self._populate_preset_combo(presets, select_name=name)

    def _on_preset_delete(self) -> None:
        name = self._preset_combo.currentText()
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
        bb.addButton("Delete", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        dlg_layout.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        presets = self._load_presets_from_settings()
        presets.pop(name, None)
        self._save_presets_to_settings(presets)
        self._populate_preset_combo(presets)

    # ------------------------------------------------------------------
    # Patch count display
    # ------------------------------------------------------------------

    def _update_patch_count(self) -> None:
        instr  = self._instr_combo.currentData() or "i1"
        paper  = self._paper_combo.currentData() or "A4"
        dd     = self._dd_check.isChecked()
        pages  = self._pages_spin.value()
        has_lb = self._lb_check.isChecked()  # True = -L active (left border suppressed)
        # ChromIQ-style forces -L, so capacity must be computed at -L-enabled
        # values even when the user left the checkbox unchecked.
        chromiq_force_l = self._chromiq_force_l(instr, paper)
        eff_lb = has_lb or chromiq_force_l
        dpi    = int(self._settings.get("printtarg_dpi", 300))
        if instr == "i1":
            preset_key = str(self._settings.get(
                "i1pro_default_preset", I1PRO_DEFAULT_PRESET_KEY
            ))
            eff_margin, eff_scale = i1_defaults_from_preset(preset_key)
        else:
            eff_margin = INSTRUMENT_DEFAULT_MARGIN.get(instr, 6)
            eff_scale = 1.0

        per_sheet = query_patches(instr, paper, dd, suppress_lb=eff_lb,
                                  margin_mm=eff_margin, patch_scale=eff_scale)
        if per_sheet is not None:
            total = per_sheet * pages
            self._patch_count_lbl.setText(str(total))
            self._patch_detail_lbl.setText(
                f"PATCHES · {pages} PAGES · {paper.upper()}"
            )
        else:
            self._patch_count_lbl.setText("?")
            self._patch_detail_lbl.setText("CUSTOM LAYOUT")

        # Hidden-defaults info label (values mirror _collect_guided logic)
        base_white = int(self._settings.get("targen_white_patches", 4))
        base_black = int(self._settings.get("targen_black_patches", 4))
        ps = per_sheet or 504
        grey_steps = max(8, min((ps * pages) // 30, 64))
        wp = base_white + (pages - 1) * 2
        bp = base_black + (pages - 1) * 2
        # -L only matters for strip instruments; -h only for CM/SS. Hide
        # both from the command preview when not applicable so the user
        # sees exactly what printtarg will run. ChromIQ-style clipping
        # border forces -L regardless of the per-chart toggle (eff_lb).
        lb_flag = "-L " if eff_lb and instr in {"i1", "p3"} else ""
        dd_flag = "-h " if dd and instr in {"CM", "SS"} else ""
        margin_flag = f"-m{eff_margin} -M{eff_margin} " if eff_margin != 6 else ""
        scale_flag = f"-a{eff_scale:.2f} " if abs(eff_scale - 1.0) > 0.01 else ""
        precond_path = (
            self._guided_precond_path.text().strip()
            if hasattr(self, "_guided_precond_path") else ""
        )
        precond_active = (
            hasattr(self, "_guided_precond_check")
            and self._guided_precond_check.isChecked()
        )
        precond_line = ""
        recommendation = ""
        if precond_active:
            if precond_path:
                precond_line = f" -c pre_{Path(precond_path).name}"
                recommendation = (
                    "\nTip: use at least as many pages as the original profile."
                )
            else:
                recommendation = (
                    "\nPick a profile to refine from (Browse… above)."
                )

        target_name = self._preview_target_name("guided")
        info = (
            f"Guided mode applies these fixed settings:\n"
            f"targen -d2 -G -e{wp} -B{bp} -g{grey_steps}{precond_line} {target_name}\n"
            f"printtarg -i{instr} -p{paper} -t{dpi} {scale_flag}{lb_flag}{dd_flag}{margin_flag}{target_name}"
            f"{recommendation}"
        )
        if hasattr(self, "_guided_info_lbl"):
            self._guided_info_lbl.setText(info)

    def _rebuild_paper_combo(self) -> None:
        instr    = self._instr_combo.currentData() or "i1"
        excluded = EXCLUDED_PAPERS.get(instr, set())
        current  = self._paper_combo.currentData()

        self._paper_combo.blockSignals(True)
        self._paper_combo.clear()
        for size in PAPER_SIZES:
            if size not in excluded:
                self._paper_combo.addItem(PAPER_LABELS.get(size, size), size)
        self._paper_combo.blockSignals(False)

        target = current if current not in excluded else PAPER_FALLBACK.get(current, "A4")
        idx = self._paper_combo.findData(target)
        self._paper_combo.setCurrentIndex(max(idx, 0))
        self._update_patch_count()

    def _update_dd_visibility(self) -> None:
        instr = self._instr_combo.currentData() or "i1"
        # -h is meaningful on CM (double density via rig) and SS (hexagon
        # patches), but has different semantics → relabel and retitle.
        if instr == "CM":
            self._dd_check.setVisible(True)
            self._dd_tooltip.setVisible(True)
            self._dd_check.setText("Double density (requires measuring rig)")
            self._dd_tooltip._title = "Double Density (-h)"
            self._dd_tooltip._body = (
                "Doubles the number of patches that fit in each measurement strip "
                "when using a ColorMunki / i1Studio / ColorChecker Studio.\n\n"
                "REQUIRES the physical measuring rig accessory — a clear plastic "
                "guide that mounts the instrument over the chart. Without the rig "
                "the device cannot align to the tighter patch spacing and will "
                "misread.\n\n"
                "With the rig you get roughly twice as many patches per page, "
                "which means either a more detailed profile from the same number "
                "of sheets, or the same profile quality on fewer sheets. "
                "Recommended for anyone with the rig — it's a strict upgrade on "
                "patch density.\n\n"
                "Has no effect on i1Pro, i1Pro 3 Plus or SpectroScan — the option "
                "is hidden when those are selected."
            )
            self._dd_tooltip._min_width = 600
            self._dd_tooltip.setToolTip("Double Density (-h)\n\nClick for details")
        elif instr == "SS":
            self._dd_check.setVisible(True)
            self._dd_tooltip.setVisible(True)
            self._dd_check.setText("Hexagon patches (packs ~15% more per sheet)")
            self._dd_tooltip._title = "Hexagon Patches (-h)"
            self._dd_tooltip._body = (
                "Switches the SpectroScan chart layout from rectangular to "
                "hexagonal patches. Hexagons tessellate more tightly than "
                "rectangles, so roughly 14% more patches fit on the same sheet — "
                "useful for squeezing extra colour samples out of large papers.\n\n"
                "No extra hardware is required. The SpectroScan's XY scanner "
                "reads each patch individually under a motorised arm, so it "
                "doesn't care whether the patch is square or hexagonal.\n\n"
                "Has no effect on i1Pro, i1Pro 3 Plus or ColorMunki — the option "
                "is hidden when those are selected."
            )
            self._dd_tooltip._min_width = 600
            self._dd_tooltip.setToolTip("Hexagon Patches (-h)\n\nClick for details")
        else:
            self._dd_check.setVisible(False)
            self._dd_tooltip.setVisible(False)
            # Force-uncheck when hidden so the state can't leak into printtarg
            # the next time the user goes back to CM/SS without re-touching it.
            if self._dd_check.isChecked():
                self._dd_check.setChecked(False)
        # -L only affects strip instruments (i1, p3). CM reads patches
        # individually and SS is an XY flatbed — both ignore -L. Even with
        # the ChromIQ-style clipping border on, the toggle stays visible:
        # leaving it unchecked yields the branded strip; checking it
        # suppresses the border entirely and routes commands/notes to the
        # right margin as usual.
        lb_visible = instr in {"i1", "p3"}
        self._lb_check.setVisible(lb_visible)
        self._lb_tooltip.setVisible(lb_visible)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        if self._runner.is_running:
            log.warning("A process is already running")
            return
        self.target_started.emit()

        params = self._collect_params()
        name = (
            self._target_name_edit.text().strip()
            if self._current_mode() == "guided"
            else self._manual_target_name_edit.text().strip()
        )
        if name:
            self._file_mgr.set_target_name(name)
        base_name = self._file_mgr.get_target_name()

        # Apply calibration target overrides (working folder stays as base_name)
        cal_target_active = (
            hasattr(self, "_cal_target_check")
            and self._cal_target_check.isChecked()
            and self._cal_target_grp.isVisible()
        )
        if cal_target_active:
            params.cal_target = True
            params.target_name = f"cal_{base_name}"
        else:
            params.target_name = base_name

        self._last_target_name = params.target_name

        self._log.clear()
        self._preview.clear()
        self._generate_btn.setEnabled(False)

        # Auto patch count (manual mode only): estimate now, then proceed.
        # Live re-estimation on every settings change blocks the UI for
        # custom layouts (binary search shells out to targen/printtarg
        # via subprocess.run), so we defer to the click.
        if (self._current_mode() == "manual"
                and self._manual_auto_patches_check is not None
                and self._manual_auto_patches_check.isChecked()):
            self._log.appendPlainText("Calculating patch count…")
            self._log.ensureCursorVisible()
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                params.patches = self._creator.estimate_patches(
                    params, progress_cb=self._on_log_line
                )
            except Exception as exc:
                log.error("Auto patch estimation failed: %s", exc)
                self._log.appendPlainText(f"Auto patch estimation failed: {exc}")
                self._generate_btn.setEnabled(True)
                return
            self._log.appendPlainText(f"Auto patch count: {params.patches}")

        # Pre-flight: targen exits with code 1 ("Must have some single or multi
        # dimensional RGB or CMY steps") if -f is 0 and no -g / -s / -c steps
        # provide patches either. Catch this before launching the subprocess so
        # the user sees an actionable message instead of a cryptic exit code.
        if (self._current_mode() == "manual"
                and params.patches <= 0
                and params.grey_steps <= 0
                and params.single_channel_steps <= 0
                and not _extra_args_have_patch_source(params.extra_targen_args)):
            self._log.appendPlainText(
                "[ERROR] Nothing for targen to generate.\n"
                "        Set a non-zero Total Patch Count (-f), enable the Auto checkbox,\n"
                "        or set Grey Axis Steps (-g) / Single Channel Steps (-s) to a positive value."
            )
            self._log.ensureCursorVisible()
            self._generate_btn.setEnabled(True)
            return

        self._creator.generate(
            params,
            on_line=self._on_log_line,
            on_finish=self._on_generate_finished,
        )

    def _on_load_ti1(self) -> None:
        path = open_file_dialog(
            self, "Load .ti1 file", "TI1 files (*.ti1)",
            extra_path=self._settings.get("custom_output_path", ""),
        )
        if not path:
            return
        ti1 = Path(path)
        self._file_mgr.set_target_name(ti1.stem)
        params = self._collect_params()
        self._log.clear()
        self._preview.clear()
        self._generate_btn.setEnabled(False)
        self._creator.load_ti1_and_generate_preview(
            ti1, params,
            on_line=self._on_log_line,
            on_finish=self._on_generate_finished,
        )

    def _on_log_line(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.ensureCursorVisible()

    def _on_generate_finished(self, tiffs: list[Path]) -> None:
        self._generate_btn.setEnabled(True)
        # One-shot flag: consumed by this run, don't carry over to the next.
        was_from_dialog = self._preconditioning_from_dialog
        self._preconditioning_from_dialog = False
        # If a v1 promotion just happened, the path in the picker now points to
        # a file that has been renamed away. Clear it so the user isn't left
        # with a stale path next time they look at the panel.
        if was_from_dialog and hasattr(self, "_guided_precond_path"):
            picked = self._guided_precond_path.text().strip()
            if picked and not Path(picked).is_file():
                self._guided_precond_path.clear()
                self._guided_precond_check.setChecked(False)
        if tiffs:
            self._preview.load_tiff(tiffs)
            log.info("Preview loaded: %d TIFF(s)", len(tiffs))
            stem = getattr(self, "_last_target_name", None) or "chart"
            ti2 = tiffs[0].parent / f"{stem}.ti2"
            self.chart_finished.emit(tiffs, ti2)
        else:
            self._log.appendPlainText("[ERROR] Chart generation failed.")
            self._log.ensureCursorVisible()

    def _on_save_defaults(self) -> None:
        params = self._collect_params()
        s = self._settings
        name = (
            self._target_name_edit.text().strip()
            if self._current_mode() == "guided"
            else self._manual_target_name_edit.text().strip()
        )
        s.set("chart_target_name",         name or "ChromIQ Test Chart")
        s.set("chart_stamp_commands",      bool(params.stamp_commands))
        s.set("chart_left_clip_info",      bool(params.left_clip_info))
        s.set("chart_instrument",          params.instrument)
        s.set("chart_paper",               params.paper)
        s.set("chart_pages",               params.pages)
        s.set("chart_double_density",      params.double_density)
        s.set("chart_disable_left_border", params.disable_left_border)
        s.set("targen_device_type",        params.device_type)
        s.set("targen_good_mode",          params.good_mode)
        s.set("targen_white_patches",      params.white_patches)
        s.set("targen_black_patches",      params.black_patches)
        s.set("printtarg_dpi",             params.tiff_dpi)
        # Save all manual widget values individually
        for tool, widgets in self._manual_widgets.items():
            for pw in widgets:
                if pw in self._d_cascade_widgets:
                    continue
                v = pw.get_raw_value()
                if v is not None:
                    s.set(_pw_settings_key(tool, pw.flag), v)
        for idx, pw in enumerate(self._d_cascade_widgets):
            s.set(f"manual_targen_-D_{idx}", pw.get_raw_value())
            s.set(f"manual_targen_-D_{idx}_enabled", pw.is_enabled_by_user)
        if self._bit16_radio is not None:
            s.set("manual_printtarg_tiff_16bit", self._bit16_radio.isChecked())
        if self._manual_auto_patches_check is not None:
            s.set("manual_auto_patches", self._manual_auto_patches_check.isChecked())
        if self._manual_pages_spin is not None:
            s.set("manual_pages", int(self._manual_pages_spin.value()))
        log.info("Chart defaults saved")
        self._log.appendPlainText("Current settings saved as defaults.")
        self._log.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Param collection
    # ------------------------------------------------------------------

    def _collect_params(self) -> ChartParams:
        if self._current_mode() == "guided":
            return self._collect_guided()
        return self._collect_manual()

    def _collect_guided(self) -> ChartParams:
        pages   = self._pages_spin.value()
        instr   = self._instr_combo.currentData() or "i1"
        paper   = self._paper_combo.currentData() or "A4"
        dd      = self._dd_check.isChecked()
        has_lb  = self._lb_check.isChecked()
        if instr == "i1":
            preset_key = str(self._settings.get(
                "i1pro_default_preset", I1PRO_DEFAULT_PRESET_KEY
            ))
            margin, patch_scale = i1_defaults_from_preset(preset_key)
        else:
            margin = INSTRUMENT_DEFAULT_MARGIN.get(instr, 6)
            patch_scale = 1.0
        base_white = int(self._settings.get("targen_white_patches", 4))
        base_black = int(self._settings.get("targen_black_patches", 4))
        # ChromIQ-style forces -L → size grey ramp from -L-enabled capacity.
        eff_lb = has_lb or self._chromiq_force_l(instr, paper)
        per_sheet  = query_patches(instr, paper, dd, suppress_lb=eff_lb,
                                   margin_mm=margin, patch_scale=patch_scale) or 504
        grey_steps = max(8, min((per_sheet * pages) // 30, 64))

        precond_path = self._guided_precond_path.text().strip()
        precond_active = self._guided_precond_check.isChecked() and bool(precond_path)
        extra_targen = shlex.join(["-c", precond_path]) if precond_active else ""

        return ChartParams(
            instrument           = instr,
            paper                = paper,
            pages                = pages,
            double_density       = dd,
            disable_left_border  = has_lb,
            device_type          = self._settings.get("targen_device_type", "2"),
            patches              = 0,
            white_patches        = base_white + (pages - 1) * 2,
            black_patches        = base_black + (pages - 1) * 2,
            good_mode            = bool(self._settings.get("targen_good_mode", True)),
            grey_steps           = grey_steps,
            extra_targen_args    = extra_targen,
            tiff_dpi             = int(self._settings.get("printtarg_dpi", 300)),
            patch_scale          = patch_scale,
            margin_mm            = margin,
            left_clip_info       = bool(self._settings.get("chart_left_clip_info", False)),
            chromiq_clip_style   = bool(self._settings.get("i1pro_chromiq_clip_style", False)),
            preserve_as_preconditioning = (
                precond_active and self._preconditioning_from_dialog
            ),
        )

    def _collect_manual(self) -> ChartParams:
        p = ChartParams()

        if self._manual_pages_spin is not None:
            p.pages = int(self._manual_pages_spin.value())

        def _get(tool: str, flag: str, default: Any) -> Any:
            for pw in self._manual_widgets.get(tool, []):
                if pw.flag == flag:
                    v = pw.get_raw_value()
                    return v if v is not None else default
            return default

        p.device_type          = str(_get("targen",    "-d",  "2"))
        p.patches              = int(_get("targen",    "-f",  0))
        p.white_patches        = int(_get("targen",    "-e",  4))
        p.black_patches        = int(_get("targen",    "-B",  4))
        p.good_mode            = bool(_get("targen",   "-G",  True))
        p.grey_steps           = int(_get("targen",    "-g",  0))
        p.single_channel_steps = int(_get("targen",    "-s",  0))

        extra = []
        for pw in self._manual_widgets.get("targen", []):
            if pw.flag in {"-D", "-c", "-C", "-N", "-V"}:
                extra.extend(pw.build_args())
        if extra:
            p.extra_targen_args = shlex.join(extra)

        p.instrument           = str(_get("printtarg", "-i",  "i1"))
        p.paper                = str(_get("printtarg", "-p",  "A4"))
        p.tiff_dpi             = int(_get("printtarg", "-t",  300))
        p.tiff_16bit           = self._bit16_radio is not None and self._bit16_radio.isChecked()
        p.double_density       = bool(_get("printtarg", "-h", False))
        p.disable_left_border  = bool(_get("printtarg", "-L", True))
        p.patch_scale          = float(_get("printtarg", "-a", 1.0))
        p.margin_mm            = int(_get("printtarg",  "-m",  6))
        p.no_randomise         = bool(_get("printtarg", "-r",  False))
        p.bw_spacers           = bool(_get("printtarg", "-b",  False))
        p.no_strip_limit       = bool(_get("printtarg", "-P",  False))

        # All remaining printtarg params (e.g. -N, -K, -I, -C, -D, -U, -R, -Q, -A, -n, -c)
        # are collected here and passed through extra_printtarg_args, which
        # _build_printtarg_args() already appends verbatim before the target name.
        _pt_mapped = {"-i", "-p", "-t", "-h", "-L", "-a", "-m", "-r", "-b", "-P"}
        extra_pt: list[str] = []
        for pw in self._manual_widgets.get("printtarg", []):
            if pw.flag not in _pt_mapped:
                extra_pt.extend(pw.build_args())
        if extra_pt:
            p.extra_printtarg_args = shlex.join(extra_pt)

        p.chart_notes          = self._manual_chart_notes_edit.text().strip()
        p.stamp_commands       = self._manual_stamp_cmd_check.isChecked()
        p.left_clip_info       = self._manual_left_clip_check.isChecked()
        p.chromiq_clip_style   = bool(self._settings.get("i1pro_chromiq_clip_style", False))
        p.is_manual            = True
        return p

    # ------------------------------------------------------------------
    # Restore saved defaults
    # ------------------------------------------------------------------

    def _restore_defaults(self) -> None:
        s = self._settings

        default_name = s.get("chart_target_name", "ChromIQ Test Chart")
        self._target_name_edit.setText(default_name)
        self._manual_target_name_edit.setText(default_name)

        # Chart notes are per-chart, not a session default — always start empty.
        # Also evict any stale value that an older session may have persisted
        # under the now-unused "chart_notes" key.
        try:
            if s._qs.contains("chart_notes"):
                s._qs.remove("chart_notes")
        except AttributeError:
            pass
        if hasattr(self, "_manual_chart_notes_edit"):
            self._manual_chart_notes_edit.setText("")
        if hasattr(self, "_manual_stamp_cmd_check"):
            self._manual_stamp_cmd_check.setChecked(bool(s.get("chart_stamp_commands", True)))
        if hasattr(self, "_manual_left_clip_check"):
            self._manual_left_clip_check.setChecked(bool(s.get("chart_left_clip_info", False)))

        instr = s.get("chart_instrument", "i1")
        idx = self._instr_combo.findData(instr)
        if idx >= 0:
            self._instr_combo.setCurrentIndex(idx)
        self._rebuild_paper_combo()  # populate/filter even if instrument index didn't change

        paper = s.get("chart_paper", "A4")
        idx = self._paper_combo.findData(paper)
        if idx >= 0:
            self._paper_combo.setCurrentIndex(idx)

        self._pages_spin.setValue(int(s.get("chart_pages", 1)))
        self._dd_check.setChecked(bool(s.get("chart_double_density", False)))
        self._lb_check.setChecked(bool(s.get("chart_disable_left_border", True)))
        self._update_dd_visibility()
        self._update_patch_count()

        # Restore manual widget values. Prefer the case-disambiguated key;
        # fall through to the legacy bare key for backward compatibility, then
        # evict the bare key so it can't keep colliding with its case-twin in
        # the Windows registry (HKCU is case-insensitive). Legacy values that
        # don't type-coerce to the widget's expected type are discarded
        # silently — they are leftover bytes from a clobbering case-twin.
        for tool, widgets in self._manual_widgets.items():
            for pw in widgets:
                if pw in self._d_cascade_widgets:
                    continue
                new_key = _pw_settings_key(tool, pw.flag)
                v = s.get(new_key)
                if v is None:
                    legacy_key = f"manual_{tool}_{pw.flag}"
                    if legacy_key != new_key:
                        v = s.get(legacy_key)
                        try:
                            if s._qs.contains(legacy_key):
                                s._qs.remove(legacy_key)
                        except AttributeError:
                            pass
                        if v is not None and not _value_compatible_with_pw(v, pw):
                            v = None
                if v is not None:
                    pw.set_value(v)
        for idx, pw in enumerate(self._d_cascade_widgets):
            v = s.get(f"manual_targen_-D_{idx}")
            if v is not None:
                pw.set_value(v)
            pw.set_user_enabled(bool(s.get(f"manual_targen_-D_{idx}_enabled", False)))
        self._rebuild_d_cascade_visibility()
        if self._bit8_radio is not None and self._bit16_radio is not None:
            is_16bit = bool(s.get("manual_printtarg_tiff_16bit", False))
            self._bit16_radio.setChecked(is_16bit)
            self._bit8_radio.setChecked(not is_16bit)
        if self._manual_pages_spin is not None:
            self._manual_pages_spin.setValue(int(s.get("manual_pages", 1)))
        if self._manual_auto_patches_check is not None:
            auto_on = bool(s.get("manual_auto_patches", False))
            self._manual_auto_patches_check.setChecked(auto_on)
            self._on_auto_patches_toggled(auto_on)
        self._update_manual_lb_visibility()
        self._apply_instrument_default_margin()

        presets = self._load_presets_from_settings()
        self._populate_preset_combo(presets)

        mode = s.get("chart_mode", "guided")
        self._switch_mode(mode)
