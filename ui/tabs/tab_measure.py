"""Tab 3: Measure Chart."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.resource_path import resource_path
from ui.tooltip_button import TooltipButton
from ui.widgets import make_browse_button
from workflow.measure_manager import MeasureManager, MeasureParams
from ui.tiff_preview import TiffPreview

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)


def _detect_instruments(argyll_bin: str) -> list[tuple[str, str]]:
    """Return list of (index, description) from chartread -?."""
    try:
        r = subprocess.run(
            [str(Path(argyll_bin) / "chartread"), "-?"],
            capture_output=True, text=True, timeout=5,
        )
        lines = (r.stdout + r.stderr).splitlines()
        instruments: list[tuple[str, str]] = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped[0].isdigit() and " " in stripped:
                parts = stripped.split(None, 1)
                if len(parts) == 2:
                    instruments.append((parts[0], parts[1]))
        return instruments if instruments else [("1", "Default instrument")]
    except Exception:
        return [("1", "Default instrument")]


# ---------------------------------------------------------------------------
# Per-option chartread row helper
# ---------------------------------------------------------------------------

@dataclass
class _ChartreadOption:
    """One chartread option row with enable-checkbox and optional value widget."""
    key: str           # settings key suffix
    flag: str          # CLI flag
    label: str
    tooltip_title: str
    tooltip_body: str
    widget: QWidget | None = None   # value widget (spinbox, combo…)
    checkbox: QCheckBox | None = None

    def build_args(self) -> list[str]:
        """Return CLI tokens for this option if enabled."""
        if self.checkbox is None or not self.checkbox.isChecked():
            return []
        if self.widget is None:
            return [self.flag]
        val = self._read_widget()
        if val is None:
            return [self.flag]
        return [self.flag, str(val)]

    def _read_widget(self):
        if isinstance(self.widget, (QSpinBox, QDoubleSpinBox)):
            return self.widget.value()
        if isinstance(self.widget, QComboBox):
            return self.widget.currentData()
        return None


class TabMeasure(QWidget):
    """Step 3: interactive chart measurement with chartread."""

    def __init__(
        self,
        runner: "ArgyllRunner",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner   = runner
        self._settings = settings
        self._manager  = MeasureManager(runner, self)
        self._ti1_path: Path | None = None
        self._tiff_pages: list[Path] = []
        self._chartread_opts: list[_ChartreadOption] = []

        self._manager.stripe_changed.connect(self._on_stripe_changed)
        self._build_ui()
        self._restore_defaults()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ---- Left ----
        left_scroll = QScrollArea(self)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(left_scroll.Shape.NoFrame)
        left_scroll.setMaximumWidth(580)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 12, 16, 12)
        ll.setSpacing(10)

        # Instrument
        instr_grp = QGroupBox("Measurement Instrument", left)
        ig = QVBoxLayout(instr_grp)
        ig.setContentsMargins(8, 14, 8, 8)
        instr_row = QHBoxLayout()
        instr_row.addWidget(QLabel("Instrument:", left))
        self._instr_combo = QComboBox(left)
        self._instr_combo.addItem("Detecting…", "1")
        instr_row.addWidget(self._instr_combo, stretch=1)
        refresh_btn = QPushButton(left)
        refresh_btn.setIcon(
            QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        refresh_btn.setFixedSize(34, 34)
        refresh_btn.setStyleSheet("QPushButton { padding: 0; min-height: 0; }")
        refresh_btn.setToolTip("Refresh instrument list")
        refresh_btn.clicked.connect(self._refresh_instruments)
        instr_row.addWidget(refresh_btn)
        instr_row.addWidget(TooltipButton(
            "Instrument Port",
            "Select the port / connection index for your spectrophotometer.\n"
            "chartread lists connected instruments at startup.\n"
            "Click ⟳ to refresh after connecting your device.",
            left,
        ))
        ig.addLayout(instr_row)
        ll.addWidget(instr_grp)

        # Core measurement options (always shown)
        core_grp = QGroupBox("Measurement Options", left)
        cg = QVBoxLayout(core_grp)
        cg.setContentsMargins(8, 14, 8, 8)
        cg.setSpacing(8)

        def _bool_row(label, default, tt_title, tt_body):
            row = QHBoxLayout()
            cb = QCheckBox(label, left)
            cb.setChecked(default)
            row.addWidget(cb)
            row.addStretch()
            row.addWidget(TooltipButton(tt_title, tt_body, left))
            cg.addLayout(row)
            return cb

        self._bidir_cb = _bool_row(
            "Disable bidirectional strip recognition (-B)", True,
            "Disable Bidirectional Reading (-B)",
            "Strongly recommended ON.  Prevents mis-reads from scanning\n"
            "strips in the wrong direction.",
        )
        self._suppress_cb = _bool_row(
            "Suppress warning messages (-S)", True,
            "Suppress Warnings (-S)",
            "Suppresses non-fatal instrument warnings during measurement.",
        )
        self._nocal_cb = _bool_row(
            "Skip initial calibration (-N)", False,
            "Skip Initial Calibration (-N)",
            "Skips the white-tile calibration at startup.  Only use if you\n"
            "have already calibrated in this session.",
        )
        self._pbp_cb = _bool_row(
            "Patch-by-patch mode (-p)", False,
            "Patch-by-Patch Mode (-p)",
            "Measure each patch individually instead of reading strips.\n"
            "Much slower but useful if strip reading fails.",
        )
        ll.addWidget(core_grp)

        # Additional chartread arguments — structured
        adv_grp = QGroupBox("Additional Options", left)
        ag = QVBoxLayout(adv_grp)
        ag.setContentsMargins(8, 14, 8, 8)
        ag.setSpacing(6)

        self._chartread_opts = self._make_chartread_options(left)
        for opt in self._chartread_opts:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            # Enable checkbox
            cb = QCheckBox(opt.label, left)
            cb.setChecked(False)
            opt.checkbox = cb

            # Value widget setup
            if opt.widget is not None:
                opt.widget.setEnabled(False)
                cb.toggled.connect(opt.widget.setEnabled)
                row.addWidget(cb, stretch=1)
                row.addWidget(opt.widget)
            else:
                row.addWidget(cb, stretch=1)

            row.addWidget(TooltipButton(opt.tooltip_title, opt.tooltip_body, left))
            ag.addLayout(row)

        ll.addWidget(adv_grp)

        # File selection
        file_grp = QGroupBox("Target File (.ti2)", left)
        fg = QVBoxLayout(file_grp)
        fg.setContentsMargins(8, 14, 8, 8)
        file_row = QHBoxLayout()
        self._load_ti1_btn = QPushButton("Load .ti2 file…", left)
        self._load_ti1_btn.clicked.connect(self._on_load_ti2)
        self._ti1_lbl = QLabel("No file selected", left)
        self._ti1_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        self._ti1_lbl.setWordWrap(True)
        file_row.addWidget(self._load_ti1_btn)
        file_row.addWidget(self._ti1_lbl, stretch=1)
        fg.addLayout(file_row)
        ll.addWidget(file_grp)

        # Buttons
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Measurement", left)
        self._start_btn.setObjectName("primary")
        self._start_btn.setFixedHeight(36)
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn = QPushButton("Stop", left)
        self._stop_btn.setObjectName("danger")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        self._save_defaults_btn = QPushButton("Save as Defaults", left)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        ll.addLayout(btn_row)

        # Key shortcut info
        info = QLabel(
            "During measurement:  "
            "Enter/Space = confirm strip  ·  "
            "← / → = prev/next strip  ·  "
            "ESC = abort",
            left,
        )
        info.setObjectName("info")
        info.setWordWrap(True)
        ll.addWidget(info)

        # Log
        self._log = QPlainTextEdit(left)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(120)
        self._log.setPlaceholderText("chartread output will appear here…")
        ll.addWidget(self._log, stretch=1)

        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        # ---- Right preview ----
        right = QWidget(self)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Chart Preview", right)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #909090; font-size: 11px; padding: 4px;")
        rl.addWidget(lbl)
        self._preview = TiffPreview(right)
        rl.addWidget(self._preview, stretch=1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        self._refresh_instruments()

    # ------------------------------------------------------------------
    # Chartread option rows
    # ------------------------------------------------------------------

    def _make_chartread_options(self, parent: QWidget) -> list[_ChartreadOption]:
        opts = []

        def _spinbox(lo, hi, step, default, decimals=0):
            if decimals > 0:
                sb = QDoubleSpinBox(parent)
                sb.setRange(lo, hi)
                sb.setSingleStep(step)
                sb.setDecimals(decimals)
                sb.setValue(default)
                sb.setFixedWidth(90)
            else:
                sb = QSpinBox(parent)
                sb.setRange(int(lo), int(hi))
                sb.setSingleStep(int(step))
                sb.setValue(int(default))
                sb.setFixedWidth(90)
            return sb

        opts.append(_ChartreadOption(
            key="highres", flag="-H",
            label="High resolution spectral mode (-H)",
            tooltip_title="High Resolution Spectral Mode (-H)",
            tooltip_body="Enables high-resolution spectral sampling on instruments that\n"
                         "support it (i1Pro 2/3).  Slightly slower but more accurate Lab values.",
        ))

        opts.append(_ChartreadOption(
            key="tolerance", flag="-T",
            label="Patch consistency tolerance (-T)",
            tooltip_title="Patch Tolerance Multiplier (-T)",
            tooltip_body="Multiplies the default patch consistency tolerance.\n"
                         "Increase to 2.0–3.0 on textured or matte papers.\n"
                         "Default: 1.0",
            widget=_spinbox(0.1, 10.0, 0.1, 1.0, decimals=1),
        ))

        opts.append(_ChartreadOption(
            key="save_lab", flag="-l",
            label="Save L*a*b* instead of XYZ (-l)",
            tooltip_title="Save L*a*b* Values (-l)",
            tooltip_body="Saves measurement data as D50 L*a*b* instead of XYZ.\n"
                         "Most workflows use XYZ (default).  Enable only if downstream\n"
                         "tools require L*a*b* input.",
        ))

        opts.append(_ChartreadOption(
            key="save_lab_and_xyz", flag="-L",
            label="Save L*a*b* AND XYZ (-L)",
            tooltip_title="Save L*a*b* AND XYZ (-L)",
            tooltip_body="Saves both D50 L*a*b* and XYZ values in the .ti3 file.",
        ))

        # XRGA conversion combo
        xrga_combo = QComboBox(parent)
        xrga_combo.setFixedWidth(110)
        for code, lbl in [("N", "None"), ("A", "XRGA"), ("X", "XRDI"), ("G", "GMDI")]:
            xrga_combo.addItem(lbl, code)
        opts.append(_ChartreadOption(
            key="xrga", flag="-A",
            label="XRGA instrument correction (-A)",
            tooltip_title="XRGA Correction (-A)",
            tooltip_body="Apply an XRGA colorimetric correction to convert between\n"
                         "different spectrophotometer calibration standards.\n"
                         "N = none (default), A = XRGA, X = XRDI, G = GMDI.",
            widget=xrga_combo,
        ))

        return opts

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_ti1_path(self, path: Path) -> None:
        self._ti1_path = path
        self._ti1_lbl.setText(str(path))
        self._try_load_tiffs(path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_instruments(self) -> None:
        bin_dir = self._settings.get("argyll_bin_path", "/Applications/Argyll/bin")
        instruments = _detect_instruments(bin_dir)
        self._instr_combo.blockSignals(True)
        self._instr_combo.clear()
        for idx, desc in instruments:
            self._instr_combo.addItem(f"{idx}: {desc}", idx)
        self._instr_combo.blockSignals(False)

    def _on_load_ti2(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load .ti2 file", str(Path.home()),
            "TI2 files (*.ti2)",
        )
        if not path:
            return
        self._ti1_path = Path(path)
        self._ti1_lbl.setText(str(self._ti1_path))
        self._try_load_tiffs(self._ti1_path)

    def _try_load_tiffs(self, base_path: Path) -> None:
        stem   = base_path.with_suffix("").stem
        folder = base_path.parent
        tiffs  = sorted(folder.glob(f"{stem}*.tif"))
        if tiffs:
            self._tiff_pages = tiffs
            self._preview.load_tiff(tiffs)
        else:
            self._preview.clear()
            self._log.appendPlainText(
                "[WARNING] No matching TIFF preview found. "
                "Ensure you scan the correct target."
            )

    def _on_start(self) -> None:
        if not self._ti1_path:
            self._log.appendPlainText("[ERROR] No .ti2 file selected.")
            return
        if self._runner.is_running:
            return

        params = self._collect_params()
        self._log.clear()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._manager.start(
            params,
            on_line=self._on_log_line,
            on_finish=self._on_measure_done,
        )

    def _on_stop(self) -> None:
        self._manager.abort()

    def _on_log_line(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.ensureCursorVisible()

    def _on_measure_done(self, code: int) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if code == 0:
            self._log.appendPlainText("\n[OK] Measurement complete.")
        else:
            self._log.appendPlainText(f"\n[ERROR] chartread exited with code {code}.")

    def _on_stripe_changed(self, strip_id: str) -> None:
        self._log.appendPlainText(f"[STRIP] Now scanning strip: {strip_id}")

    def _collect_params(self) -> MeasureParams:
        extra_args: list[str] = []
        for opt in self._chartread_opts:
            extra_args += opt.build_args()

        return MeasureParams(
            ti1_path           = self._ti1_path,
            disable_bidir      = self._bidir_cb.isChecked(),
            suppress_warnings  = self._suppress_cb.isChecked(),
            disable_initial_cal = self._nocal_cb.isChecked(),
            patch_by_patch     = self._pbp_cb.isChecked(),
            extra_args         = " ".join(extra_args),
        )

    def _on_save_defaults(self) -> None:
        s = self._settings
        s.set("measure_disable_bidir",     self._bidir_cb.isChecked())
        s.set("measure_suppress_warnings", self._suppress_cb.isChecked())
        s.set("measure_no_cal",            self._nocal_cb.isChecked())
        s.set("measure_patch_by_patch",    self._pbp_cb.isChecked())
        for opt in self._chartread_opts:
            if opt.checkbox:
                s.set(f"measure_{opt.key}_enabled", opt.checkbox.isChecked())
            if opt.widget is not None:
                if isinstance(opt.widget, (QSpinBox, QDoubleSpinBox)):
                    s.set(f"measure_{opt.key}_value", opt.widget.value())
                elif isinstance(opt.widget, QComboBox):
                    s.set(f"measure_{opt.key}_value", opt.widget.currentData())
        self._log.appendPlainText("Measurement settings saved as defaults.")

    def _restore_defaults(self) -> None:
        s = self._settings
        self._bidir_cb.setChecked(bool(s.get("measure_disable_bidir", True)))
        self._suppress_cb.setChecked(bool(s.get("measure_suppress_warnings", True)))
        self._nocal_cb.setChecked(bool(s.get("measure_no_cal", False)))
        self._pbp_cb.setChecked(bool(s.get("measure_patch_by_patch", False)))
        for opt in self._chartread_opts:
            if opt.checkbox:
                enabled = bool(s.get(f"measure_{opt.key}_enabled", False))
                opt.checkbox.setChecked(enabled)
            if opt.widget is not None:
                val = s.get(f"measure_{opt.key}_value")
                if val is not None:
                    if isinstance(opt.widget, (QSpinBox, QDoubleSpinBox)):
                        try:
                            opt.widget.setValue(float(val))
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(opt.widget, QComboBox):
                        idx = opt.widget.findData(str(val))
                        if idx >= 0:
                            opt.widget.setCurrentIndex(idx)
