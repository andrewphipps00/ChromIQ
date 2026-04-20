"""Tab 3: Measure Chart."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, pyqtSignal
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
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.resource_path import resource_path
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox, make_browse_button
from workflow.measure_manager import MeasureManager, MeasureParams
from ui.tiff_preview import TiffPreview

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)


def _letter_to_idx(letter: str) -> int:
    """Convert a strip letter (A=0, B=1, … Z=25, AA=26, …) to a 0-based index."""
    idx = 0
    for c in letter.upper():
        idx = idx * 26 + (ord(c) - ord("A") + 1)
    return idx - 1


def _detect_stripe_rects(tiff_path: Path) -> list[QRect]:
    """Locate vertical strip columns in a printtarg TIFF.

    Strategy
    --------
    1. Find the label zone: the first contiguous block of rows that have a
       "moderate" number of dark pixels (printed label characters — neither
       blank white rows nor full-width separator lines).
    2. Build a per-column dark-pixel count from those rows; merge adjacent
       non-zero runs to get one cluster per strip label.
    3. Convert label-cluster centres → strip x-boundaries (midpoints between
       centres, extrapolated at the edges).
    4. Derive the vertical extent from the full content bounding box.
    """
    from PIL import Image
    try:
        img = Image.open(tiff_path).convert("L")
        orig_w, orig_h = img.size

        ANALYSIS_W = 1000
        scale  = ANALYSIS_W / orig_w if orig_w > ANALYSIS_W else 1.0
        aw     = max(1, int(orig_w * scale))
        ah     = max(1, int(orig_h * scale))
        small  = img.resize((aw, ah), Image.BOX)
        pix    = small.load()

        DARK            = 80
        WHITE           = 240
        MIN_LABEL_DARK  = max(5, aw // 200)   # at least this many dark px/row
        MAX_LABEL_FRAC  = 0.30                 # exclude separator lines (>30% dark)
        EMPTY_STOP      = 8                    # white rows before stopping label scan
        MERGE_GAP       = max(3, aw // 200)   # merge within-label character gaps

        # ── 1. Locate the label zone ─────────────────────────────────────────
        max_label_dark = int(aw * MAX_LABEL_FRAC)
        y_lab_start: int | None = None
        y_lab_end:   int | None = None
        empty_streak = 0
        for y in range(ah * 30 // 100):
            count = sum(1 for x in range(aw) if pix[x, y] < DARK)
            if MIN_LABEL_DARK <= count <= max_label_dark:
                if y_lab_start is None:
                    y_lab_start = y
                y_lab_end = y
                empty_streak = 0
            else:
                empty_streak += 1
                if y_lab_start is not None and empty_streak >= EMPTY_STOP:
                    break

        if y_lab_start is None or y_lab_end is None:
            log.debug("Strip detection: no label zone found")
            return []

        # ── 2. Column dark-pixel profile → merge into per-strip clusters ─────
        col_dark = [0] * aw
        for y in range(y_lab_start, y_lab_end + 1):
            for x in range(aw):
                if pix[x, y] < DARK:
                    col_dark[x] += 1

        runs: list[tuple[int, int]] = []
        in_run = False
        r_start = 0
        for x in range(aw):
            if col_dark[x] > 0 and not in_run:
                in_run, r_start = True, x
            elif col_dark[x] == 0 and in_run:
                in_run = False
                runs.append((r_start, x - 1))
        if in_run:
            runs.append((r_start, aw - 1))

        merged: list[list[int]] = []
        for s, e in runs:
            if merged and s - merged[-1][1] <= MERGE_GAP:
                merged[-1][1] = e
            else:
                merged.append([s, e])

        n_strips = len(merged)
        if n_strips < 1:
            log.debug("Strip detection: no label clusters found")
            return []

        centers = [(s + e) / 2 for s, e in merged]

        # ── 3. Vertical extent ───────────────────────────────────────────────
        y_top_a    = next((y for y in range(ah)
                           if any(pix[x, y] < WHITE for x in range(0, aw, 4))), 0)
        y_bottom_a = next((y for y in range(ah - 1, -1, -1)
                           if any(pix[x, y] < WHITE for x in range(0, aw, 4))), ah - 1)

        inv           = 1.0 / scale
        y_top         = max(0,      int(y_top_a    * inv))
        y_bottom      = min(orig_h, int((y_bottom_a + 1) * inv))
        strip_h       = max(1, y_bottom - y_top)
        y_label_bot   = min(orig_h, int((y_lab_end + 1) * inv))

        # ── 4. Build vertical column rects ───────────────────────────────────
        rects: list[QRect] = []
        for i, cx in enumerate(centers):
            half_l = (cx - centers[i - 1]) / 2 if i > 0         else (centers[1] - centers[0]) / 2
            half_r = (centers[i + 1] - cx) / 2 if i < n_strips-1 else (centers[-1] - centers[-2]) / 2
            x0 = max(0,      int((cx - half_l) * inv))
            x1 = min(orig_w, int((cx + half_r) * inv))
            rects.append(QRect(x0, y_label_bot, max(1, x1 - x0), strip_h))

        log.info("Strip detection: %d strips, label y=%d–%d (scaled), content y=%d–%d (orig)",
                 n_strips, y_lab_start, y_lab_end, y_top, y_bottom)
        return rects

    except Exception as exc:
        log.warning("Strip detection failed: %s", exc)
        return []


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

    measure_finished   = pyqtSignal(Path)  # emits the .ti3 path on success
    proceed_to_profile = pyqtSignal()      # emitted when user chooses to go straight to tab 4

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
        self._measure_failed: bool = False
        self._auto_proceed: bool = False
        self._all_done_shown: bool = False

        self._manager.stripe_changed.connect(self._on_stripe_changed)
        self._manager.all_stripes_done.connect(self._on_all_stripes_done)
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
        instr_row.addWidget(QLabel("Instrument port number:", left))
        self._instr_spin = NoScrollSpinBox(left)
        self._instr_spin.setRange(1, 9)
        self._instr_spin.setValue(1)
        instr_row.addWidget(self._instr_spin)
        instr_row.addStretch()
        instr_row.addWidget(TooltipButton(
            "Instrument Port",
            "Port index passed to chartread via -c.\n"
            "Most setups use 1 (single instrument connected).\n"
            "If chartread lists multiple devices at startup, set the\n"
            "number shown next to your instrument in that list.",
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

        # Log
        self._log = QPlainTextEdit(left)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(100)
        self._log.setMaximumHeight(100)
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

    # ------------------------------------------------------------------
    # Chartread option rows
    # ------------------------------------------------------------------

    def _make_chartread_options(self, parent: QWidget) -> list[_ChartreadOption]:
        opts = []

        def _spinbox(lo, hi, step, default, decimals=0):
            if decimals > 0:
                sb = NoScrollDoubleSpinBox(parent)
                sb.setRange(lo, hi)
                sb.setSingleStep(step)
                sb.setDecimals(decimals)
                sb.setValue(default)
                sb.setFixedWidth(90)
            else:
                sb = NoScrollSpinBox(parent)
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
        xrga_combo = NoScrollComboBox(parent)
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
            self._setup_stripe_rects()
        else:
            self._preview.clear()
            self._log.appendPlainText(
                "[WARNING] No matching TIFF preview found. "
                "Ensure you scan the correct target."
            )
            self._log.ensureCursorVisible()

    def _setup_stripe_rects(self) -> None:
        """Detect strip positions from the first TIFF page and push to preview."""
        if not self._tiff_pages:
            return
        rects = _detect_stripe_rects(self._tiff_pages[0])
        if rects:
            self._preview.set_stripe_rects(rects)

    def _on_start(self) -> None:
        if not self._ti1_path:
            self._log.appendPlainText("[ERROR] No .ti2 file selected.")
            self._log.ensureCursorVisible()
            return
        if self._runner.is_running:
            return

        params = self._collect_params()
        self._log.clear()
        self._auto_proceed = False
        self._all_done_shown = False
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        QApplication.instance().installEventFilter(self)

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
        if "failed" in line.lower() or "communications failure" in line.lower():
            self._measure_failed = True

    def _on_all_stripes_done(self) -> None:
        if self._all_done_shown:
            return
        self._all_done_shown = True

        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("All Stripes Read")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>All stripes have been read successfully.</b><br><br>"
            "Click <b>Build Profile</b> to finalise the measurement and go directly "
            "to the Build Profile tab — the next and final step.<br><br>"
            "If you would like to re-read any stripe first, click <b>Re-read Stripes</b>. "
            "Use <b>f</b>&nbsp;/&nbsp;<b>b</b> to move forward and back between stripes, "
            "<b>n</b> to jump to the next unread stripe, and press <b>d</b> when you "
            "are done.<br><br>"
            "<span style='color:#909090;'>These instructions are always visible in "
            "the output log below.</span>",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_box = QDialogButtonBox()
        build_btn = btn_box.addButton("Build Profile →", QDialogButtonBox.ButtonRole.AcceptRole)
        build_btn.setObjectName("primary")
        btn_box.addButton("Re-read Stripes", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._auto_proceed = True
            self._manager.send_key("d")

    def _on_measure_done(self, code: int) -> None:
        QApplication.instance().removeEventFilter(self)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

        # chartread exits non-zero even on a clean 'd' (done) completion.
        # Treat as success if the .ti3 file was actually written.
        ti3 = self._ti1_path.with_suffix(".ti3") if self._ti1_path else None
        ti3_exists = ti3 is not None and ti3.exists()
        failed = self._measure_failed or (code != 0 and not ti3_exists)
        self._measure_failed = False

        if failed:
            self._log.appendPlainText("\n[ERROR] Measurement failed — see output above.")
        else:
            self._log.appendPlainText(
                "\n[OK] Measurement complete.\n"
                f"Saved: {ti3}\n\n"
                "→ Next step: go to the '4. Build Profile' tab to create your ICC profile."
            )
            if ti3_exists:
                self.measure_finished.emit(ti3)
                if self._auto_proceed:
                    self.proceed_to_profile.emit()
        self._auto_proceed = False
        self._log.ensureCursorVisible()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._manager.send_key("\x1b")
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._manager.send_key("\r")
            elif key == Qt.Key.Key_Space:
                self._manager.send_key(" ")
            elif key == Qt.Key.Key_Left:
                self._manager.send_key("\x1b[D")
            elif key == Qt.Key.Key_Right:
                self._manager.send_key("\x1b[C")
            else:
                text = event.text()
                if text:
                    self._manager.send_key(text)
            return True   # consume — don't let widgets act on it
        return False

    def _on_stripe_changed(self, strip_id: str) -> None:
        self._log.appendPlainText(f"[→ strip {strip_id}]")
        self._log.ensureCursorVisible()
        letter = "".join(c for c in strip_id if c.isalpha()).upper()
        if not letter:
            return
        rects = self._preview._stripe_rects
        if not rects:
            return
        global_idx     = _letter_to_idx(letter)
        strips_per_page = len(rects)
        page            = global_idx // strips_per_page
        local_idx       = global_idx % strips_per_page
        n_pages         = max(1, len(self._tiff_pages))
        if 0 <= page < n_pages:
            self._preview.show_page(page)
        self._preview.highlight_stripe(local_idx)

    def _collect_params(self) -> MeasureParams:
        extra_args: list[str] = []
        for opt in self._chartread_opts:
            extra_args += opt.build_args()

        return MeasureParams(
            ti1_path           = self._ti1_path,
            instrument         = str(self._instr_spin.value()),
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
        self._log.ensureCursorVisible()

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
