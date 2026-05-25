"""Tab 3: Measure Chart."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.preset_store import (
    load_presets as _load_tab_presets,
    reveal_in_file_manager,
    save_presets as _save_tab_presets,
    tab_dir,
)
from core.resource_path import resource_path
from core.strip_utils import letter_to_idx, parse_passes_per_page
from ui.fade_scroll import FadeScrollArea
from ui.tab_header import TabHeader
from ui.tooltip_button import TooltipButton
from ui.widgets import ElidingLabel, NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox, make_browse_button, open_file_dialog, set_folder_icon, set_preset_icon, tint_dialog_primary

_TAB_COLOR = "#56d6a5"  # Measure tab accent
from ui.styles import SPEC_GREEN, TAB_COLORS
from workflow.measure_manager import MeasureManager, MeasureParams
from ui.tiff_preview import TiffPreview

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)



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
        try:
            img = Image.open(tiff_path).convert("L")
        except Exception:
            from ui.tiff_preview import load_tiff_as_rgb, _find_sidecar_channels
            img = load_tiff_as_rgb(
                tiff_path, ink_channels=_find_sidecar_channels(tiff_path)
            ).convert("L")
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
        # Merge within-label character gaps. Must be large enough to bridge the
        # faint crossbar gap in letters like "H" (~5 px at aw=1000) but well
        # below the inter-letter gap (~22+ px on standard printtarg charts).
        MERGE_GAP       = max(8, aw // 100)

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

        if not merged:
            log.debug("Strip detection: no label clusters found")
            return []

        raw_centers = [(s + e) / 2 for s, e in merged]

        # Robustify against split letters ("H" crossbar, narrow "I" cluttering
        # the neighbour) and merged adjacent two-character labels (page 3 has
        # "AW AX AY …"): derive the true column pitch from the MEDIAN gap
        # between adjacent cluster centres — both kinds of artefact only
        # distort a minority of gaps, so the median stays correct.  Then
        # generate a uniform grid between the leftmost and rightmost real
        # cluster (trimming clusters whose nearest neighbour is implausibly
        # close, since those are likely spurious edge clusters).
        if len(raw_centers) >= 3:
            gaps_sorted = sorted(raw_centers[i + 1] - raw_centers[i]
                                 for i in range(len(raw_centers) - 1))
            median_pitch = gaps_sorted[len(gaps_sorted) // 2]
            # Drop a leading cluster whose gap to neighbour is < 60% of median
            # (almost certainly a spurious mark, not a strip label).
            left_centers = list(raw_centers)
            while len(left_centers) >= 2 and \
                    (left_centers[1] - left_centers[0]) < 0.6 * median_pitch:
                left_centers.pop(0)
            while len(left_centers) >= 2 and \
                    (left_centers[-1] - left_centers[-2]) < 0.6 * median_pitch:
                left_centers.pop()
            if len(left_centers) >= 2 and median_pitch > 0:
                left, right = left_centers[0], left_centers[-1]
                n_strips = round((right - left) / median_pitch) + 1
                # Hard sanity bound — never invent or drop more than 25% of
                # what was raw-detected.
                n_strips = max(int(len(raw_centers) * 0.75),
                               min(int(len(raw_centers) * 1.25) + 2, n_strips))
                centers = [left + i * (right - left) / max(1, n_strips - 1)
                           for i in range(n_strips)]
            else:
                centers = raw_centers
        else:
            centers = raw_centers
        n_strips = len(centers)

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


def _detect_uniform_stripe_rects(tiff_path: Path, n_strips: int) -> list[QRect]:
    """Locate strip columns when the page's strip count is already known.

    Used when the chart's .ti2 tells us exactly how many strips a page holds
    (see ``parse_passes_per_page``). Counting strip *labels* from the image is
    fragile — two-character labels (AA, AB, …) cluster unpredictably and the
    rotated title string printtarg prints down the right margin looks like an
    extra strip. Here we sidestep all of that:

    1. Find the label band at the top (vertical anchor for the arrow).
    2. Isolate the patch block as the *widest contiguous run* of "has content"
       columns below the labels — one solid, edge-to-edge run of equal-width
       strips. The white margin before the right-edge title text splits that
       text into its own narrow run, so it is excluded.
    3. Divide the block into exactly ``n_strips`` equal columns.

    Returns [] if the page can't be analysed, so the caller can fall back to
    the label-based detector.
    """
    from PIL import Image
    if n_strips < 1:
        return []
    try:
        try:
            img = Image.open(tiff_path).convert("L")
        except Exception:
            from ui.tiff_preview import load_tiff_as_rgb, _find_sidecar_channels
            img = load_tiff_as_rgb(
                tiff_path, ink_channels=_find_sidecar_channels(tiff_path)
            ).convert("L")
        orig_w, orig_h = img.size

        ANALYSIS_W = 1000
        scale = ANALYSIS_W / orig_w if orig_w > ANALYSIS_W else 1.0
        aw    = max(1, int(orig_w * scale))
        ah    = max(1, int(orig_h * scale))
        small = img.resize((aw, ah), Image.BOX)
        pix   = small.load()

        DARK            = 80
        WHITE           = 240
        MIN_LABEL_DARK  = max(5, aw // 200)
        MAX_LABEL_FRAC  = 0.30
        EMPTY_STOP      = 8

        # ── 1. Label band → vertical anchor (same as the legacy detector) ────
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
        if y_lab_end is None:
            return []

        # ── 2. Patch block = widest contiguous run of content columns ────────
        y0 = y_lab_end + 1
        y1 = int(ah * 0.97)
        if y1 <= y0:
            return []
        col_content = [
            sum(1 for y in range(y0, y1) if pix[x, y] < WHITE) for x in range(aw)
        ]
        thr = (y1 - y0) * 0.10
        gap = max(2, aw // 250)   # bridge anti-alias dropouts between strips
        best: tuple[int, int] | None = None
        run_start: int | None = None
        last = 0
        for x in range(aw):
            if col_content[x] > thr:
                if run_start is None:
                    run_start = x
                last = x
            elif run_start is not None and x - last > gap:
                if best is None or (last - run_start) > (best[1] - best[0]):
                    best = (run_start, last)
                run_start = None
        if run_start is not None and (
            best is None or (last - run_start) > (best[1] - best[0])
        ):
            best = (run_start, last)
        if best is None:
            return []
        block_l, block_r = best
        block_w = block_r - block_l + 1

        # ── 3. Vertical extent for the rect height ───────────────────────────
        y_top_a    = next((y for y in range(ah)
                           if any(pix[x, y] < WHITE for x in range(0, aw, 4))), 0)
        y_bottom_a = next((y for y in range(ah - 1, -1, -1)
                           if any(pix[x, y] < WHITE for x in range(0, aw, 4))), ah - 1)
        inv         = 1.0 / scale
        y_top       = max(0,      int(y_top_a * inv))
        y_bottom    = min(orig_h, int((y_bottom_a + 1) * inv))
        y_label_bot = min(orig_h, int((y_lab_end + 1) * inv))
        strip_h     = max(1, y_bottom - y_top)

        # ── 4. Divide the block into n_strips equal columns ──────────────────
        col_w = block_w / n_strips
        rects: list[QRect] = []
        for i in range(n_strips):
            x0 = int((block_l + i * col_w) * inv)
            x1 = int((block_l + (i + 1) * col_w) * inv)
            rects.append(QRect(x0, y_label_bot, max(1, x1 - x0), strip_h))

        log.info("Uniform strip detection: %d strips, block x=%d–%d (scaled)",
                 n_strips, block_l, block_r)
        return rects

    except Exception as exc:
        log.warning("Uniform strip detection failed: %s", exc)
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
    row_widget: QWidget | None = None

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
    measurement_active = pyqtSignal(bool)  # True when chartread is running, False when done
    ti2_replaced       = pyqtSignal()      # emitted when the user manually loads a different .ti2 file
    ti2_loaded         = pyqtSignal(Path)  # emitted when the user loads a .ti2 file (for cross-tab sync)

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
        # Per-page strip highlight rects and the authoritative per-page strip
        # counts (from the .ti2 PASSES_IN_STRIPS2). Together these let the
        # highlighter map an absolute strip letter to the right page + column,
        # even on multi-page charts whose last page is partly empty.
        self._page_stripe_rects: list[list[QRect]] = []
        self._strips_per_page: list[int] = []
        self._chartread_opts: list[_ChartreadOption] = []
        # Auto bidir-detection: resolved -B value for the loaded .ti2 (False =
        # bidirectional allowed; the no-file / unknown-instrument fallback).
        self._detected_disable_bidir: bool = False
        self._detected_instrument: str | None = None
        # Text of the last "Chart instrument:" line logged, so a new chart can
        # replace it instead of letting the messages accumulate.
        self._instr_log_text: str | None = None
        self._measure_failed: bool = False
        self._strip_list: list[str] = []
        self._refine_strips_path: Path | None = None
        self._guided_refinement_active: bool = False
        self._resume_active: bool = False
        self._auto_proceed: bool = False
        self._all_done_shown: bool = False
        self._instrument_disconnected: bool = False
        self._device_busy: bool = False
        self._no_instrument: bool = False
        self._usb_claimed_by_vm: bool = False
        # Pending terminal dialogs for group-B startup failures (shown by _on_measure_done).
        self._coms_init_failed_msg: str | None = None
        self._inst_init_failed_msg: str | None = None
        self._instrument_wrong_type: str | None = None
        self._ccmx_load_failed_msg: str | None = None
        self._mode_set_failed_msg: str | None = None
        self._ti3_mtime_before: float | None = None
        self._mode: str = "dark"

        self._manager.stripe_changed.connect(self._on_stripe_changed)
        self._manager.all_stripes_done.connect(self._on_all_stripes_done)
        self._manager.calibration_prompt.connect(self._on_calibration_prompt)
        self._manager.calibration_done.connect(self._on_calibration_done)
        self._manager.strip_error.connect(self._on_strip_error)
        self._manager.instrument_disconnected.connect(self._on_instrument_disconnected)
        self._manager.device_busy.connect(self._on_device_busy)
        self._manager.no_instrument.connect(self._on_no_instrument)
        self._manager.wrong_strip.connect(self._on_wrong_strip)
        self._manager.unexpected_response.connect(self._on_unexpected_response)
        self._manager.sensor_wrong_position.connect(self._on_sensor_wrong_position)
        self._manager.usb_claimed_by_vm.connect(self._on_usb_claimed_by_vm)
        # A. Mid-measurement recovery dialogs
        self._manager.strip_interrupted.connect(self._on_strip_interrupted)
        self._manager.unread_confirm.connect(self._on_unread_confirm)
        self._manager.generic_instrument_error.connect(self._on_generic_instrument_error)
        # B. Startup / config error capture (dialogs shown in _on_measure_done)
        self._manager.coms_init_failed.connect(self._on_coms_init_failed)
        self._manager.inst_init_failed.connect(self._on_inst_init_failed)
        self._manager.instrument_wrong_type.connect(self._on_instrument_wrong_type)
        self._manager.ccmx_load_failed.connect(self._on_ccmx_load_failed)
        self._manager.mode_set_failed.connect(self._on_mode_set_failed)
        # B-status. Non-blocking informational messages
        self._manager.info_message.connect(self._on_info_message)
        # D. Spot / XY mode defensive handlers
        self._manager.xy_place_sheet.connect(self._on_xy_place_sheet)
        self._manager.spot_ready.connect(self._on_spot_ready)
        self._manager.abort_confirm.connect(self._on_abort_confirm)
        self._runner.keypress_failed.connect(self._on_keypress_failed)

        # Watchdog: if a dialog sends a key but chartread emits no new output
        # within KEY_WATCHDOG_MS, surface a recoverable warning so the user is
        # not left staring at a frozen dialog when a keystroke vanishes
        # (e.g. Windows AttachConsole failure — issue #20).
        self._last_chartread_output_ts: float = 0.0
        self._key_watchdog = QTimer(self)
        self._key_watchdog.setSingleShot(True)
        self._key_watchdog.setInterval(12000)
        self._key_watchdog.timeout.connect(self._on_key_watchdog_timeout)
        self._build_ui()
        self._restore_defaults()
        self._start_btn.setEnabled(False)

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
        self._calm_outer.setVisible(mode == "guided")
        # The two modes have separate resume checkboxes; reflect the active
        # one's state on the shared Start button. Guarded because _switch_mode
        # is also reachable during UI build before _start_btn exists.
        if hasattr(self, "_start_btn"):
            self._refresh_start_button_label()

    def _current_mode(self) -> str:
        return "guided" if self._stack.currentIndex() == 0 else "manual"

    def set_calibration_mode(self, enabled: bool) -> None:
        """Hide guided mode toggle and lock to manual when calibration mode is active."""
        self._mode_row_widget.setVisible(not enabled)
        if enabled:
            self._switch_mode("manual")

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        """Re-tint the Stop button's disabled background for the active theme."""
        new_mode = "light" if mode == "light" else "dark"
        if new_mode == self._mode:
            return
        self._mode = new_mode
        if hasattr(self, "_stop_btn"):
            self._apply_stop_btn_style()

    def _apply_stop_btn_style(self) -> None:
        # The button keeps its light-grey "always-stand-out" base in both
        # themes; only the disabled state changes so it doesn't paint a
        # dark slab over the light tab background.
        if self._mode == "light":
            disabled_bg     = "#eeeae5"
            disabled_fg     = "#a8a4a0"
            disabled_border = "#ccc9c3"
        else:
            disabled_bg     = "#2a2a2a"
            disabled_fg     = "#555555"
            disabled_border = "#333333"
        self._stop_btn.setStyleSheet(
            "QPushButton { background: #f4f4f4; color: #121212; border: 1px solid #cccccc; font-weight: 600; }"
            "QPushButton:hover { background: #e0e0e0; border-color: #bbbbbb; }"
            f"QPushButton:disabled {{ background: {disabled_bg}; color: {disabled_fg}; border-color: {disabled_border}; }}"
        )

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ---- Left ----
        left_container = QWidget(self)
        self._left_panel = left_container
        left_container.setFixedWidth(580)
        lc_layout = QVBoxLayout(left_container)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.setSpacing(0)

        # Header + mode buttons (outside scroll/stack)
        top_widget = QWidget(left_container)
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(16, 12, 16, 6)
        top_layout.setSpacing(8)
        top_layout.addWidget(TabHeader(
            "STEP 03 · MEASURE TARGET", "Measure printed chart", "#56d6a5", top_widget,
            tooltip_title="Step 3 — Measure the print",
            tooltip_body=(
                "On this screen, your spectrophotometer reads every colour patch "
                "on the printed chart and records what colour your printer actually "
                "produced. ChromIQ pairs each measurement with the RGB value that "
                "was requested in step 1, and saves the result as a .ti3 file.\n\n"
                "Before you start:\n"
                "• Your measurement device (e.g. i1Pro, ColorMunki, ColorMeter) "
                "MUST be plugged in via USB before you open this tab. If ChromIQ "
                "doesn't see it, unplug and replug, then restart the app.\n"
                "• The print must be fully dry — wet ink gives wrong readings.\n"
                "• Have the printed chart in front of you, well-lit, on a flat "
                "surface. Avoid direct sunlight.\n\n"
                "How to use this screen:\n"
                "• Guided mode walks you through reading the chart one strip (row) "
                "at a time. Recommended for first-timers.\n"
                "• Manual mode exposes every chartread option for advanced users.\n"
                "• Follow the on-screen prompts: place the device on the indicated "
                "patch or strip, press the button on the device, and wait for the "
                "beep before moving to the next.\n\n"
                "If you misread a patch, you can usually re-do that strip from the "
                "prompt. Don't rush — accurate reads now mean an accurate profile.\n\n"
                "Next step: build the ICC profile on tab 4."
            ),
        ))
        _mode_font = QFont()
        _mode_font.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
        _mode_font.setPointSize(11)
        _mode_font.setWeight(QFont.Weight.Bold)
        self._mode_row_widget = QWidget(top_widget)
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
        top_layout.addWidget(self._mode_row_widget)
        lc_layout.addWidget(top_widget)

        # File selection — shared between modes
        file_outer = QWidget(left_container)
        fo_layout = QVBoxLayout(file_outer)
        fo_layout.setContentsMargins(16, 4, 16, 0)
        fo_layout.setSpacing(0)
        self._file_grp = file_grp = QGroupBox("Target File (.ti2)", file_outer)
        file_grp.setFlat(True)
        fg = QVBoxLayout(file_grp)
        fg.setContentsMargins(8, 6, 8, 8)
        file_row = QHBoxLayout()
        self._load_ti1_btn = QPushButton("Load .ti2 file…", file_outer)
        set_folder_icon(self._load_ti1_btn, "folder_measure")
        self._load_ti1_btn.clicked.connect(self._on_load_ti2)
        self._ti1_lbl = ElidingLabel("No file selected", file_outer)
        self._ti1_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        file_row.addWidget(self._load_ti1_btn)
        file_row.addWidget(self._ti1_lbl, stretch=1)
        fg.addLayout(file_row)
        fo_layout.addWidget(file_grp)
        lc_layout.addWidget(file_outer)

        # Stacked panels
        self._stack = QStackedWidget(left_container)
        self._guided_panel = self._make_guided_panel()
        self._manual_panel = self._make_manual_panel()
        self._stack.addWidget(self._guided_panel)
        self._stack.addWidget(self._manual_panel)
        lc_layout.addWidget(self._stack, stretch=1)

        # Keep-calm block — guided mode only, sits directly above buttons
        calm_outer = QWidget(left_container)
        co_layout = QVBoxLayout(calm_outer)
        co_layout.setContentsMargins(16, 8, 16, 0)
        calm_box = QGroupBox(calm_outer)
        # Only override layout; let border + radius come from the global theme.
        calm_box.setStyleSheet(
            "QGroupBox { margin-top: 0px; padding: 14px 8px 12px 8px; }"
        )
        calm_layout = QVBoxLayout(calm_box)
        calm_layout.setContentsMargins(0, 0, 0, 0)
        calm_layout.setSpacing(4)
        headline = QLabel(f'Keep calm<span style="color: {SPEC_GREEN}; font-style: italic;">!</span>', calm_box)
        headline.setTextFormat(Qt.TextFormat.RichText)
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        headline.setStyleSheet(
            "background: transparent;"
            " font-family: Georgia; font-size: 28px;"
        )
        calm_layout.addWidget(headline)
        subtext = QLabel("Scan each strip with a slow, steady motion.", calm_box)
        subtext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtext.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 9px; font-weight: 300;"
        )
        calm_layout.addWidget(subtext)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 6, 0, 0)
        bar_row.setSpacing(0)
        bar_row.addStretch()
        for _color in TAB_COLORS:
            _seg = QFrame(calm_outer)
            _seg.setFixedSize(22, 2)
            _seg.setStyleSheet(f"background-color: {_color}; border: none;")
            bar_row.addWidget(_seg)
        bar_row.addStretch()
        calm_layout.addLayout(bar_row)
        co_layout.addWidget(calm_box)
        self._calm_outer = calm_outer
        lc_layout.addWidget(calm_outer)

        # Buttons — shared
        btn_outer = QWidget(left_container)
        bo_layout = QVBoxLayout(btn_outer)
        bo_layout.setContentsMargins(16, 6, 16, 8)
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Measurement", btn_outer)
        self._start_btn.setObjectName("primary")
        self._start_btn.setFixedHeight(36)
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn = QPushButton("Stop", btn_outer)
        self._stop_btn.setFixedHeight(36)
        self._apply_stop_btn_style()
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        self._save_defaults_btn = QPushButton("Save as Defaults", btn_outer)
        self._save_defaults_btn.setFixedHeight(36)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        bo_layout.addLayout(btn_row)
        lc_layout.addWidget(btn_outer)

        # Log — shared
        log_outer = QWidget(left_container)
        lo_layout = QVBoxLayout(log_outer)
        lo_layout.setContentsMargins(16, 0, 16, 12)
        self._log = QPlainTextEdit(log_outer)
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(100)
        self._log.setMaximumHeight(100)
        self._log.setPlaceholderText("chartread output will appear here…")
        lo_layout.addWidget(self._log)
        lc_layout.addWidget(log_outer)

        # Status bar (replaces main-window status bar)
        self._status_bar_lbl = QLabel("", left_container)
        self._status_bar_lbl.setWordWrap(True)
        self._status_bar_lbl.setVisible(False)
        lc_layout.addWidget(self._status_bar_lbl)

        splitter.addWidget(left_container)

        # ---- Right preview ----
        right = QWidget(self)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 12)
        rl.setSpacing(0)
        self._preview = TiffPreview(right)
        self._preview.set_caption("CHART PREVIEW")
        rl.addWidget(self._preview, stretch=1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Guided panel
    # ------------------------------------------------------------------

    def _make_guided_panel(self) -> QWidget:
        scroll = FadeScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 8, 16, 8)
        ll.setSpacing(10)

        # Instrument
        self._instr_grp = instr_grp = QGroupBox("Measurement Instrument", left)
        instr_grp.setFlat(True)
        ig = QVBoxLayout(instr_grp)
        ig.setContentsMargins(8, 6, 8, 8)
        instr_row = QHBoxLayout()
        instr_row.addWidget(QLabel("Instrument port number:", left))
        self._instr_spin = NoScrollSpinBox(left)
        self._instr_spin.setRange(1, 9)
        self._instr_spin.setValue(1)
        instr_row.addWidget(self._instr_spin)
        instr_row.addStretch()
        instr_row.addWidget(TooltipButton(
            "Instrument Port",
            "Port index passed to chartread via -c.\n\n"
            "Most setups use 1. When chartread starts it prints a numbered\n"
            "list of detected instruments — set this to the number shown\n"
            "next to your spectrophotometer in that list.\n\n"
            "Only change it if you have more than one instrument connected\n"
            "at the same time.",
            left,
        ))
        ig.addLayout(instr_row)
        ll.addWidget(instr_grp)
        instr_grp.setVisible(False)

        # Core measurement options (always shown)
        self._core_grp = core_grp = QGroupBox("Measurement Options", left)
        cg = QVBoxLayout(core_grp)
        cg.setContentsMargins(8, 14, 8, 8)
        cg.setSpacing(8)

        def _bool_row(label, default, tt_title, tt_body):
            row = QHBoxLayout()
            cb = QCheckBox(label, left)
            cb.setChecked(default)
            row.addWidget(cb)
            row.addStretch()
            tip = TooltipButton(tt_title, tt_body, left)
            row.addWidget(tip)
            cg.addLayout(row)
            return cb, tip

        # Bidirectional row: the -B checkbox with its own tooltip on the left,
        # then an "Auto" toggle with its own tooltip on the right (Auto derives
        # -B from the loaded chart's instrument via _refresh_bidir_autodetect
        # and greys out the checkbox while on). A stretch between the two
        # option/tooltip groups spreads them across the column width.
        bidir_row = QHBoxLayout()
        self._bidir_cb = QCheckBox("Disable bidirectional strip recognition (-B)", left)
        self._bidir_cb.setChecked(True)
        bidir_row.addWidget(self._bidir_cb)
        bidir_row.addSpacing(18)
        bidir_row.addWidget(TooltipButton(
            "Bidirectional reading (-B)",
            "Sets whether a strip can be read in both directions or only one.\n\n"
            "Tick the box to force one-direction reading (-B); untick it to\n"
            "allow scanning a strip either way. The i1 Pro (including i1 Pro 3)\n"
            "can read both directions, while the ColorMunki reads one only.\n\n"
            "While Auto is on this is decided for you and the box is locked —\n"
            "turn Auto off to choose it yourself.",
            left,
        ))
        bidir_row.addStretch()
        self._bidir_auto_cb = QCheckBox("Auto", left)
        self._bidir_auto_cb.setChecked(True)
        self._bidir_auto_cb.toggled.connect(
            lambda _checked: self._apply_bidir_auto_state("guided")
        )
        bidir_row.addWidget(self._bidir_auto_cb)
        bidir_row.addSpacing(18)
        bidir_row.addWidget(TooltipButton(
            "Auto (recommended)",
            "Sets the bidirectional option for you from the instrument saved\n"
            "in your loaded chart: the i1 Pro (including i1 Pro 3) reads both\n"
            "directions, the ColorMunki reads one direction only.\n\n"
            "While Auto is on, the checkbox to the left is locked and shows\n"
            "the chosen setting. Turn Auto off to set it yourself.",
            left,
        ))
        cg.addLayout(bidir_row)
        self._suppress_cb, _ = _bool_row(
            "Suppress warning messages (-S)", True,
            "Suppress Warnings (-S)",
            "Suppresses non-fatal instrument warnings from chartread.\n\n"
            "Suppressed messages include: calibration drift notices,\n"
            "reflectance range warnings on very dark patches, and strip\n"
            "timing cautions. These rarely affect measurement quality.\n\n"
            "Fatal errors that would prevent a .ti3 from being written are\n"
            "always shown regardless of this setting.",
        )
        self._nocal_cb, _nocal_tip = _bool_row(
            "Skip initial calibration (-N)", False,
            "Skip Initial Calibration (-N)",
            "Skips the automatic white-tile calibration at chartread startup.\n\n"
            "Normally chartread prompts you to place the instrument on its\n"
            "white calibration tile before measuring begins. This ensures\n"
            "accurate absolute reflectance values and takes only a few seconds.\n\n"
            "Enable this only if you have already calibrated the instrument\n"
            "earlier in the same session and do not want to repeat the step.",
        )
        self._nocal_cb.setVisible(False)
        _nocal_tip.setVisible(False)
        self._pbp_cb, _pbp_tip = _bool_row(
            "Patch-by-patch mode (-p)", False,
            "Patch-by-Patch Mode (-p)",
            "Switches from strip reading to single-patch measurement mode.\n\n"
            "Instead of scanning entire strips, chartread guides you patch\n"
            "by patch across the chart. This is significantly slower — one\n"
            "reading per patch — but more reliable on heavily textured\n"
            "surfaces or when strip reading consistently fails on a\n"
            "particular chart layout.",
        )
        self._pbp_cb.setVisible(False)
        _pbp_tip.setVisible(False)

        resume_row = QHBoxLayout()
        self._resume_cb = QCheckBox("Refine / resume existing measurement (-r)", left)
        self._resume_cb.setChecked(False)
        self._resume_cb.setVisible(False)
        resume_row.addWidget(self._resume_cb)
        resume_row.addStretch()
        self._resume_tip = TooltipButton(
            "Refine / Resume Existing Measurement (-r)",
            "Reuses the existing .ti3 file in the same folder as the\n"
            ".ti2 file. Previously measured strips are kept — you only need\n"
            "to scan the strips you want to update or add.\n\n"
            "Use this after a quality check to re-measure problem strips,\n"
            "or to continue a measurement that was interrupted.\n\n"
            "This option appears only when a matching .ti3 file is found.",
            left,
        )
        self._resume_tip.setVisible(False)
        resume_row.addWidget(self._resume_tip)
        cg.addLayout(resume_row)

        # Refinement file row — shown only when resume is checked
        self._refine_row = QWidget(left)
        refine_rl = QHBoxLayout(self._refine_row)
        refine_rl.setContentsMargins(20, 0, 0, 0)
        refine_rl.setSpacing(6)
        self._refine_cb = QCheckBox(
            "Use refinement strips file for guided re-measurement",
            self._refine_row,
        )
        self._refine_cb.setEnabled(False)
        refine_rl.addWidget(self._refine_cb, stretch=1)
        refine_rl.addWidget(TooltipButton(
            "Refinement Strips File",
            "Available when a Refine_Strips_<name>.txt file exists next\n"
            "to your .ti2 file.\n\n"
            "That file is created automatically by the Check && Refine\n"
            "tab after a quality check. It lists the strips with the\n"
            "highest colour errors, sorted worst-first.\n\n"
            "When active, the app navigates chartread to each of those\n"
            "strips automatically — you only need to scan them.",
            self._refine_row,
        ))
        self._refine_row.setVisible(False)
        cg.addWidget(self._refine_row)

        self._resume_cb.stateChanged.connect(
            lambda state: self._refine_row.setVisible(
                state == Qt.CheckState.Checked.value
            )
        )
        self._resume_cb.toggled.connect(lambda _checked: self._refresh_start_button_label())

        ll.addWidget(core_grp)

        # Additional chartread arguments — structured
        self._adv_grp = adv_grp = QGroupBox("Additional Options", left)
        ag = QVBoxLayout(adv_grp)
        ag.setContentsMargins(8, 14, 8, 8)
        ag.setSpacing(6)

        self._chartread_opts = self._make_chartread_options(left)
        for opt in self._chartread_opts:
            row_w = QWidget(left)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            opt.row_widget = row_w

            cb = QCheckBox(opt.label, left)
            cb.setChecked(False)
            opt.checkbox = cb

            if opt.widget is not None:
                opt.widget.setEnabled(False)
                cb.toggled.connect(opt.widget.setEnabled)
                row.addWidget(cb, stretch=1)
                row.addWidget(opt.widget)
            else:
                row.addWidget(cb, stretch=1)

            row.addWidget(TooltipButton(opt.tooltip_title, opt.tooltip_body, left))
            ag.addWidget(row_w)

        for opt in self._chartread_opts:
            if opt.key == "tolerance":
                opt.checkbox.setChecked(True)
                if opt.widget is not None:
                    opt.widget.setValue(0.7)
                    opt.widget.setEnabled(True)
            else:
                if opt.row_widget is not None:
                    opt.row_widget.setVisible(False)

        ll.addWidget(adv_grp)
        ll.addStretch(1)

        scroll.setWidget(left)
        return scroll

    # ------------------------------------------------------------------
    # Manual panel
    # ------------------------------------------------------------------

    def _make_manual_panel(self) -> QWidget:
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(16, 8, 16, 0)
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
        set_preset_icon(self._m_preset_add_btn, "plus")
        self._m_preset_add_btn.setToolTip("Save current settings as a new preset")
        self._m_preset_del_btn = QPushButton(container)
        self._m_preset_del_btn.setObjectName("icon_btn")
        self._m_preset_del_btn.setFixedSize(28, 28)
        set_preset_icon(self._m_preset_del_btn, "minus")
        self._m_preset_del_btn.setToolTip("Delete selected preset")
        self._m_preset_del_btn.setEnabled(False)
        self._m_preset_reveal_btn = QPushButton(container)
        self._m_preset_reveal_btn.setObjectName("icon_btn")
        self._m_preset_reveal_btn.setFixedSize(28, 28)
        set_folder_icon(self._m_preset_reveal_btn, "folder")
        self._m_preset_reveal_btn.setToolTip(
            "Open this tab's presets folder in Finder/Explorer.\n"
            "Each preset is a plain .json file — copy one to a colleague\n"
            "and they can drop it into their own folder to share."
        )
        self._m_preset_reveal_btn.clicked.connect(
            lambda: reveal_in_file_manager(tab_dir("measure"))
        )
        presets_row.addWidget(self._m_preset_add_btn)
        presets_row.addWidget(self._m_preset_del_btn)
        presets_row.addWidget(self._m_preset_reveal_btn)
        presets_row.addWidget(TooltipButton(
            "Manual Presets",
            "Save and recall named snapshots of all Manual mode settings.\n\n"
            "  +  Save current parameter values as a new named preset.\n"
            "  −  Delete the currently selected preset.\n"
            "  ▢  Open this tab's presets folder in Finder/Explorer.\n\n"
            "Select a preset from the dropdown to instantly restore all\n"
            "values. The Default entry always resets to built-in defaults.\n\n"
            "Presets are stored as plain .json files — one per preset —\n"
            "in a ChromIQ folder under your system's Preferences / AppData\n"
            "/ config location. Use the folder button (▢) on the right of\n"
            "the preset row to open it. To share a preset, copy the .json\n"
            "out of that folder and send it to a colleague; to install a\n"
            "shared preset, drop the .json into the matching folder on the\n"
            "target machine and ChromIQ will pick it up on the next launch.\n\n"
            "Presets persist between sessions.",
            container,
            min_width=600,
        ))
        self._m_preset_combo.currentIndexChanged.connect(self._on_m_preset_selected)
        self._m_preset_add_btn.clicked.connect(self._on_m_preset_save)
        self._m_preset_del_btn.clicked.connect(self._on_m_preset_delete)
        cl.addWidget(presets_grp)
        cl.addSpacing(8)

        scroll = FadeScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 8, 0, 8)
        ll.setSpacing(10)

        # Instrument — mirrors guided "Measurement Instrument" group
        m_instr_grp = QGroupBox("Measurement Instrument", left)
        m_instr_grp.setFlat(True)
        mig = QVBoxLayout(m_instr_grp)
        mig.setContentsMargins(8, 6, 8, 8)
        m_instr_row = QHBoxLayout()
        m_instr_row.addWidget(QLabel("Instrument port number:", left))
        self._m_instr_spin = NoScrollSpinBox(left)
        self._m_instr_spin.setRange(1, 9)
        self._m_instr_spin.setValue(1)
        self._m_instr_spin.setFixedWidth(61)
        self._m_instr_spin.setObjectName("compact_input")
        m_instr_row.addWidget(self._m_instr_spin)
        m_instr_row.addStretch()
        m_instr_row.addWidget(TooltipButton(
            "Instrument Port",
            "Port index passed to chartread via -c.\n\n"
            "Most setups use 1. When chartread starts it prints a numbered\n"
            "list of detected instruments — set this to the number shown\n"
            "next to your spectrophotometer in that list.\n\n"
            "Only change it if you have more than one instrument connected\n"
            "at the same time.",
            left,
        ))
        mig.addLayout(m_instr_row)
        ll.addWidget(m_instr_grp)

        # Measurement Options — mirrors guided "Measurement Options" group
        m_core_grp = QGroupBox("Measurement Options", left)
        mcg = QVBoxLayout(m_core_grp)
        mcg.setContentsMargins(8, 14, 8, 8)
        mcg.setSpacing(8)

        def _bool_row_m(label, default, tt_title, tt_body):
            row = QHBoxLayout()
            cb = QCheckBox(label, left)
            cb.setChecked(default)
            row.addWidget(cb)
            row.addStretch()
            row.addWidget(TooltipButton(tt_title, tt_body, left))
            mcg.addLayout(row)
            return cb

        # Bidirectional row (mirrors guided): the -B checkbox with its own
        # tooltip on the left, then an "Auto" toggle with its own tooltip on
        # the right, a stretch between the two groups spreads them across the
        # column width.
        m_bidir_row = QHBoxLayout()
        self._m_bidir_cb = QCheckBox("Disable bidirectional strip recognition (-B)", left)
        self._m_bidir_cb.setChecked(False)
        m_bidir_row.addWidget(self._m_bidir_cb)
        m_bidir_row.addSpacing(18)
        m_bidir_row.addWidget(TooltipButton(
            "Bidirectional reading (-B)",
            "Sets whether a strip can be read in both directions or only one.\n\n"
            "Tick the box to force one-direction reading (-B); untick it to\n"
            "allow scanning a strip either way. The i1 Pro (including i1 Pro 3)\n"
            "can read both directions, while the ColorMunki reads one only.\n\n"
            "While Auto is on this is decided for you and the box is locked —\n"
            "turn Auto off to choose it yourself.",
            left,
        ))
        m_bidir_row.addStretch()
        self._m_bidir_auto_cb = QCheckBox("Auto", left)
        self._m_bidir_auto_cb.setChecked(True)
        self._m_bidir_auto_cb.toggled.connect(
            lambda _checked: self._apply_bidir_auto_state("manual")
        )
        m_bidir_row.addWidget(self._m_bidir_auto_cb)
        m_bidir_row.addSpacing(18)
        m_bidir_row.addWidget(TooltipButton(
            "Auto (recommended)",
            "Sets the bidirectional option for you from the instrument saved\n"
            "in your loaded chart: the i1 Pro (including i1 Pro 3) reads both\n"
            "directions, the ColorMunki reads one direction only.\n\n"
            "While Auto is on, the checkbox to the left is locked and shows\n"
            "the chosen setting. Turn Auto off to set it yourself.",
            left,
        ))
        mcg.addLayout(m_bidir_row)
        self._m_suppress_cb = _bool_row_m(
            "Suppress warning messages (-S)", True,
            "Suppress Warnings (-S)",
            "Suppresses non-fatal instrument warnings from chartread.\n\n"
            "Suppressed messages include: calibration drift notices,\n"
            "reflectance range warnings on very dark patches, and strip\n"
            "timing cautions. These rarely affect measurement quality.\n\n"
            "Fatal errors that would prevent a .ti3 from being written are\n"
            "always shown regardless of this setting.",
        )
        self._m_nocal_cb = _bool_row_m(
            "Skip initial calibration (-N)", False,
            "Skip Initial Calibration (-N)",
            "Skips the automatic white-tile calibration at chartread startup.\n\n"
            "Normally chartread prompts you to place the instrument on its\n"
            "white calibration tile before measuring begins. This ensures\n"
            "accurate absolute reflectance values and takes only a few seconds.\n\n"
            "Enable this only if you have already calibrated the instrument\n"
            "earlier in the same session and do not want to repeat the step.",
        )
        self._m_pbp_cb = _bool_row_m(
            "Patch-by-patch mode (-p)", False,
            "Patch-by-Patch Mode (-p)",
            "Switches from strip reading to single-patch measurement mode.\n\n"
            "Instead of scanning entire strips, chartread guides you patch\n"
            "by patch across the chart. This is significantly slower — one\n"
            "reading per patch — but more reliable on heavily textured\n"
            "surfaces or when strip reading consistently fails on a\n"
            "particular chart layout.",
        )

        m_resume_row = QHBoxLayout()
        self._m_resume_cb = QCheckBox("Refine / resume existing measurement (-r)", left)
        self._m_resume_cb.setChecked(False)
        self._m_resume_cb.setVisible(False)
        m_resume_row.addWidget(self._m_resume_cb)
        m_resume_row.addStretch()
        self._m_resume_tip = TooltipButton(
            "Refine / Resume Existing Measurement (-r)",
            "Reuses the existing .ti3 file in the same folder as the\n"
            ".ti2 file. Previously measured strips are kept — you only need\n"
            "to scan the strips you want to update or add.\n\n"
            "Use this after a quality check to re-measure problem strips,\n"
            "or to continue a measurement that was interrupted.\n\n"
            "This option appears only when a matching .ti3 file is found.",
            left,
        )
        self._m_resume_tip.setVisible(False)
        m_resume_row.addWidget(self._m_resume_tip)
        mcg.addLayout(m_resume_row)

        self._m_refine_row = QWidget(left)
        m_refine_rl = QHBoxLayout(self._m_refine_row)
        m_refine_rl.setContentsMargins(20, 0, 0, 0)
        m_refine_rl.setSpacing(6)
        self._m_refine_cb = QCheckBox(
            "Use refinement strips file for guided re-measurement",
            self._m_refine_row,
        )
        self._m_refine_cb.setEnabled(False)
        m_refine_rl.addWidget(self._m_refine_cb, stretch=1)
        m_refine_rl.addWidget(TooltipButton(
            "Refinement Strips File",
            "Available when a Refine_Strips_<name>.txt file exists next\n"
            "to your .ti2 file.\n\n"
            "That file is created automatically by the Check && Refine\n"
            "tab after a quality check. It lists the strips with the\n"
            "highest colour errors, sorted worst-first.\n\n"
            "When active, the app navigates chartread to each of those\n"
            "strips automatically — you only need to scan them.",
            self._m_refine_row,
        ))
        self._m_refine_row.setVisible(False)
        mcg.addWidget(self._m_refine_row)

        self._m_resume_cb.stateChanged.connect(
            lambda state: self._m_refine_row.setVisible(
                state == Qt.CheckState.Checked.value
            )
        )
        self._m_resume_cb.toggled.connect(lambda _checked: self._refresh_start_button_label())

        ll.addWidget(m_core_grp)

        # Additional Options — mirrors guided "Additional Options" group
        m_adv_grp = QGroupBox("Additional Options", left)
        mag = QVBoxLayout(m_adv_grp)
        mag.setContentsMargins(8, 14, 8, 8)
        mag.setSpacing(6)

        self._m_chartread_opts = self._make_manual_chartread_options(left)
        for opt in self._m_chartread_opts:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            cb = QCheckBox(opt.label, left)
            cb.setChecked(False)
            opt.checkbox = cb
            if opt.widget is not None:
                opt.widget.setEnabled(False)
                cb.toggled.connect(opt.widget.setEnabled)
                row.addWidget(cb, stretch=1)
                row.addWidget(opt.widget)
            else:
                row.addWidget(cb, stretch=1)
            row.addWidget(TooltipButton(opt.tooltip_title, opt.tooltip_body, left))
            mag.addLayout(row)

        ll.addWidget(m_adv_grp)
        ll.addStretch(1)

        scroll.setWidget(left)
        cl.addWidget(scroll, stretch=1)
        return container

    # ------------------------------------------------------------------
    # Manual preset helpers (Measure tab)
    # ------------------------------------------------------------------

    def _m_load_presets(self) -> dict:
        return _load_tab_presets("measure", self._settings)

    def _m_save_presets(self, presets: dict) -> None:
        _save_tab_presets("measure", presets)

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
        data: dict = {
            "instr":      self._m_instr_spin.value(),
            "bidir":      self._m_bidir_cb.isChecked(),
            "bidir_auto": self._m_bidir_auto_cb.isChecked(),
            "suppress":   self._m_suppress_cb.isChecked(),
            "nocal":      self._m_nocal_cb.isChecked(),
            "pbp":        self._m_pbp_cb.isChecked(),
        }
        for opt in self._m_chartread_opts:
            if opt.checkbox:
                data[f"{opt.key}_enabled"] = opt.checkbox.isChecked()
            if opt.widget is not None:
                if isinstance(opt.widget, (QSpinBox, QDoubleSpinBox)):
                    data[f"{opt.key}_value"] = opt.widget.value()
                elif isinstance(opt.widget, QComboBox):
                    data[f"{opt.key}_value"] = opt.widget.currentData()
        return data

    def _m_apply_preset_data(self, data: dict) -> None:
        try:
            self._m_instr_spin.setValue(int(data.get("instr", 1)))
        except (ValueError, TypeError):
            pass
        self._m_bidir_cb.setChecked(bool(data.get("bidir", False)))
        self._m_bidir_auto_cb.setChecked(bool(data.get("bidir_auto", True)))
        self._m_suppress_cb.setChecked(bool(data.get("suppress", True)))
        self._m_nocal_cb.setChecked(bool(data.get("nocal", False)))
        self._m_pbp_cb.setChecked(bool(data.get("pbp", False)))
        for opt in self._m_chartread_opts:
            if opt.checkbox:
                opt.checkbox.setChecked(bool(data.get(f"{opt.key}_enabled", False)))
            if opt.widget is not None:
                val = data.get(f"{opt.key}_value")
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
        self._apply_bidir_auto_state("manual")

    def _on_m_preset_selected(self, index: int) -> None:
        self._m_preset_del_btn.setEnabled(index > 0)
        s = self._settings
        if index == 0:
            # Restore from individual manual2_chartread_* settings
            try:
                self._m_instr_spin.setValue(int(s.get("manual2_chartread_instr", 1)))
            except (ValueError, TypeError):
                pass
            self._m_bidir_cb.setChecked(bool(s.get("manual2_chartread_bidir", False)))
            self._m_bidir_auto_cb.setChecked(bool(s.get("manual2_chartread_bidir_auto", True)))
            self._m_suppress_cb.setChecked(bool(s.get("manual2_chartread_suppress", True)))
            self._m_nocal_cb.setChecked(bool(s.get("manual2_chartread_nocal", False)))
            self._m_pbp_cb.setChecked(bool(s.get("manual2_chartread_pbp", False)))
            for opt in self._m_chartread_opts:
                if opt.checkbox:
                    opt.checkbox.setChecked(bool(s.get(f"manual2_chartread_{opt.key}_enabled", False)))
                if opt.widget is not None:
                    val = s.get(f"manual2_chartread_{opt.key}_value")
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
            self._apply_bidir_auto_state("manual")
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
            "and can be recalled at any time from the preset list."
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
    # Chartread option rows (guided panel)
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
            sb.setObjectName("compact_input")
            return sb

        opts.append(_ChartreadOption(
            key="highres", flag="-H",
            label="High resolution spectral mode (-H)",
            tooltip_title="High Resolution Spectral Mode (-H)",
            tooltip_body=(
                "Enables high-resolution spectral sampling on instruments that\n"
                "support it (i1Pro 2 and i1Pro 3).\n\n"
                "Standard mode samples the spectrum at 10 nm intervals.\n"
                "High-resolution mode uses 5 nm intervals, capturing finer\n"
                "spectral detail and improving colour accuracy for profiling,\n"
                "particularly on saturated or fluorescent colours.\n\n"
                "The measurement time increase is small (roughly 10–20% per\n"
                "strip). Leave this off unless you specifically need the\n"
                "extra spectral resolution."
            ),
        ))

        filter_combo = NoScrollComboBox(parent)
        filter_combo.setFixedWidth(130)
        filter_combo.setObjectName("compact_input")
        for code, lbl in [("n", "None (M0)"), ("5", "D50 (M1)"), ("6", "D65"), ("u", "UV Cut (M2)"), ("p", "Polarizing (M3)")]:
            filter_combo.addItem(lbl, code)
        filter_combo.setCurrentIndex(1)  # default to D50 (M1)
        opts.append(_ChartreadOption(
            key="filter", flag="-F",
            label="Spectral filter type (-F)",
            tooltip_title="Spectral Filter (-F)",
            tooltip_body=(
                "Overrides the illuminant/filter condition used for measurement.\n\n"
                "Select the filter physically in use on your spectrophotometer:\n\n"
                "  n = None  (M0 — no filter, uncontrolled UV)\n"
                "  5 = D50   (M1 — controlled UV, ISO 13655 standard)\n"
                "  6 = D65   illuminant\n"
                "  u = UV Cut (M2 — UV excluded)\n"
                "  p = Polarizing filter (M3)\n\n"
                "The app defaults to D50 (M1), which matches the most common\n"
                "workflow for ICC print profiling with the i1Pro family.\n"
                "Change this only if your instrument has a different filter\n"
                "physically fitted. Wrong selection silently skews measured values."
            ),
            widget=filter_combo,
        ))

        _tol_spin = _spinbox(0.1, 10.0, 0.1, 0.5, decimals=1)
        _tol_spin.setObjectName("")
        opts.append(_ChartreadOption(
            key="tolerance", flag="-T",
            label="Patch consistency tolerance (-T)",
            tooltip_title="Patch Tolerance Multiplier (-T)",
            tooltip_body=(
                "A multiplier on chartread's built-in patch consistency\n"
                "threshold — not a delta-E value. chartread re-reads each patch\n"
                "and rejects strips where the readings disagree by more than\n"
                "the threshold × this number.\n\n"
                "Lower = stricter. A strict setting catches real problems early:\n"
                "clogged inkjet nozzles, low ink, dirty drum rollers, drifting\n"
                "laser toner. On a healthy printer + spectrophotometer combo\n"
                "the default of 0.7 leaves comfortable headroom; experienced\n"
                "users on printerknowledge.com run 0.4 with i1 Pro 2 / 3.\n\n"
                "Raise to 0.8–1.5 if you get false \"inconsistent patch\" errors\n"
                "on textured, matte, or fine-art papers — the surface itself\n"
                "contributes real variance there. Values above 2 mostly mask\n"
                "genuine issues; if you need them, fix the printer first."
            ),
            widget=_tol_spin,
        ))

        opts.append(_ChartreadOption(
            key="save_lab", flag="-l",
            label="Save L*a*b* instead of XYZ (-l)",
            tooltip_title="Save L*a*b* Values (-l)",
            tooltip_body=(
                "Saves measurement data as D50 L*a*b* instead of XYZ in the\n"
                "output .ti3 file.\n\n"
                "Standard ArgyllCMS tools (including colprof) work with XYZ.\n"
                "This option is almost never needed — enable it only if a\n"
                "downstream tool explicitly requires D50 L*a*b* input."
            ),
        ))

        opts.append(_ChartreadOption(
            key="save_lab_and_xyz", flag="-L",
            label="Save L*a*b* AND XYZ (-L)",
            tooltip_title="Save L*a*b* AND XYZ (-L)",
            tooltip_body=(
                "Saves both D50 L*a*b* values and XYZ values in the output\n"
                ".ti3 file.\n\n"
                "Use this when you need the .ti3 to be compatible with tools\n"
                "that require L*a*b* while keeping the XYZ data that colprof\n"
                "and other ArgyllCMS tools expect."
            ),
        ))

        # XRGA conversion combo
        xrga_combo = NoScrollComboBox(parent)
        xrga_combo.setFixedWidth(110)
        xrga_combo.setObjectName("compact_input")
        for code, lbl in [("N", "None"), ("A", "XRGA"), ("X", "XRDI"), ("G", "GMDI")]:
            xrga_combo.addItem(lbl, code)
        opts.append(_ChartreadOption(
            key="xrga", flag="-A",
            label="XRGA instrument correction (-A)",
            tooltip_title="XRGA Correction (-A)",
            tooltip_body=(
                "Applies a colorimetric correction to convert between\n"
                "spectrophotometer calibration standards.\n\n"
                "Different instrument generations use slightly different white\n"
                "references. XRGA standardisation corrects for these offsets:\n\n"
                "  N = No correction   (default — use for modern instruments)\n"
                "  A = XRGA   (X-Rite Global Reference Architecture)\n"
                "  X = XRDI   (older X-Rite reference)\n"
                "  G = GMDI   (GretagMacbeth reference)\n\n"
                "Only change this if you are combining measurements from\n"
                "instruments of different generations or manufacturers."
            ),
            widget=xrga_combo,
        ))

        return opts

    def _make_manual_chartread_options(self, parent: QWidget) -> list[_ChartreadOption]:
        """Mirror of _make_chartread_options for the manual panel."""
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
            sb.setObjectName("compact_input")
            return sb

        opts.append(_ChartreadOption(
            key="highres", flag="-H",
            label="High resolution spectral mode (-H)",
            tooltip_title="High Resolution Spectral Mode (-H)",
            tooltip_body=(
                "Enables high-resolution spectral sampling on instruments that\n"
                "support it (i1Pro 2 and i1Pro 3).\n\n"
                "Standard mode samples the spectrum at 10 nm intervals.\n"
                "High-resolution mode uses 5 nm intervals, capturing finer\n"
                "spectral detail and improving colour accuracy for profiling,\n"
                "particularly on saturated or fluorescent colours.\n\n"
                "The measurement time increase is small (roughly 10–20% per\n"
                "strip). Leave this off unless you specifically need the\n"
                "extra spectral resolution."
            ),
        ))

        filter_combo = NoScrollComboBox(parent)
        filter_combo.setFixedWidth(130)
        filter_combo.setObjectName("compact_input")
        for code, lbl in [("n", "None (M0)"), ("5", "D50 (M1)"), ("6", "D65"), ("u", "UV Cut (M2)"), ("p", "Polarizing (M3)")]:
            filter_combo.addItem(lbl, code)
        filter_combo.setCurrentIndex(1)
        opts.append(_ChartreadOption(
            key="filter", flag="-F",
            label="Spectral filter type (-F)",
            tooltip_title="Spectral Filter (-F)",
            tooltip_body=(
                "Overrides the illuminant/filter condition used for measurement.\n\n"
                "Select the filter physically in use on your spectrophotometer:\n\n"
                "  n = None  (M0 — no filter, uncontrolled UV)\n"
                "  5 = D50   (M1 — controlled UV, ISO 13655 standard)\n"
                "  6 = D65   illuminant\n"
                "  u = UV Cut (M2 — UV excluded)\n"
                "  p = Polarizing filter (M3)\n\n"
                "The app defaults to D50 (M1), which matches the most common\n"
                "workflow for ICC print profiling with the i1Pro family.\n"
                "Change this only if your instrument has a different filter\n"
                "physically fitted. Wrong selection silently skews measured values."
            ),
            widget=filter_combo,
        ))

        opts.append(_ChartreadOption(
            key="tolerance", flag="-T",
            label="Patch consistency tolerance (-T)",
            tooltip_title="Patch Tolerance Multiplier (-T)",
            tooltip_body=(
                "A multiplier on chartread's built-in patch consistency\n"
                "threshold — not a delta-E value. chartread re-reads each patch\n"
                "and rejects strips where the readings disagree by more than\n"
                "the threshold × this number.\n\n"
                "Lower = stricter. A strict setting catches real problems early:\n"
                "clogged inkjet nozzles, low ink, dirty drum rollers, drifting\n"
                "laser toner. On a healthy printer + spectrophotometer combo\n"
                "the default of 0.7 leaves comfortable headroom; experienced\n"
                "users on printerknowledge.com run 0.4 with i1 Pro 2 / 3.\n\n"
                "Raise to 0.8–1.5 if you get false \"inconsistent patch\" errors\n"
                "on textured, matte, or fine-art papers — the surface itself\n"
                "contributes real variance there. Values above 2 mostly mask\n"
                "genuine issues; if you need them, fix the printer first."
            ),
            widget=_spinbox(0.1, 10.0, 0.1, 0.5, decimals=1),
        ))

        opts.append(_ChartreadOption(
            key="save_lab", flag="-l",
            label="Save L*a*b* instead of XYZ (-l)",
            tooltip_title="Save L*a*b* Values (-l)",
            tooltip_body=(
                "Saves measurement data as D50 L*a*b* instead of XYZ in the\n"
                "output .ti3 file.\n\n"
                "Standard ArgyllCMS tools (including colprof) work with XYZ.\n"
                "This option is almost never needed — enable it only if a\n"
                "downstream tool explicitly requires D50 L*a*b* input."
            ),
        ))

        opts.append(_ChartreadOption(
            key="save_lab_and_xyz", flag="-L",
            label="Save L*a*b* AND XYZ (-L)",
            tooltip_title="Save L*a*b* AND XYZ (-L)",
            tooltip_body=(
                "Saves both D50 L*a*b* values and XYZ values in the output\n"
                ".ti3 file.\n\n"
                "Use this when you need the .ti3 to be compatible with tools\n"
                "that require L*a*b* while keeping the XYZ data that colprof\n"
                "and other ArgyllCMS tools expect."
            ),
        ))

        xrga_combo = NoScrollComboBox(parent)
        xrga_combo.setFixedWidth(110)
        xrga_combo.setObjectName("compact_input")
        for code, lbl in [("N", "None"), ("A", "XRGA"), ("X", "XRDI"), ("G", "GMDI")]:
            xrga_combo.addItem(lbl, code)
        opts.append(_ChartreadOption(
            key="xrga", flag="-A",
            label="XRGA instrument correction (-A)",
            tooltip_title="XRGA Correction (-A)",
            tooltip_body=(
                "Applies a colorimetric correction to convert between\n"
                "spectrophotometer calibration standards.\n\n"
                "Different instrument generations use slightly different white\n"
                "references. XRGA standardisation corrects for these offsets:\n\n"
                "  N = No correction   (default — use for modern instruments)\n"
                "  A = XRGA   (X-Rite Global Reference Architecture)\n"
                "  X = XRDI   (older X-Rite reference)\n"
                "  G = GMDI   (GretagMacbeth reference)\n\n"
                "Only change this if you are combining measurements from\n"
                "instruments of different generations or manufacturers."
            ),
            widget=xrga_combo,
        ))

        return opts

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def ti1_path(self) -> Path | None:
        return self._ti1_path

    def set_ti1_path(self, path: Path) -> None:
        self._ti1_path = path
        self._ti1_lbl.setText(str(path))
        self._start_btn.setEnabled(True)
        self._try_load_tiffs(path)
        self._update_resume_availability()
        self._refresh_bidir_autodetect()

    def clear_chart_file(self) -> None:
        self._ti1_path = None
        self._ti1_lbl.setText("No file selected")
        self._ti1_lbl.setStyleSheet("color: #909090; font-size: 11px;")
        self._start_btn.setEnabled(False)
        self._tiff_pages = []
        self._page_stripe_rects = []
        self._strips_per_page = []
        self._preview.clear()
        self._update_resume_availability()
        self._settings.set("session_ti1_path", "")
        self._refresh_bidir_autodetect()

    # ------------------------------------------------------------------
    # Auto bidirectional (-B) detection
    # ------------------------------------------------------------------

    def _refresh_bidir_autodetect(self) -> None:
        """Re-read the loaded chart's TARGET_INSTRUMENT and refresh -B state.

        Called whenever the chart file changes. Resolves the -B value the
        Auto toggle will apply, logs the decision, and updates both modes'
        (greyed) checkboxes so they show what will happen.
        """
        from ui.ti2_loader import (
            disable_bidir_for_instrument, instrument_label, is_spectroscan, read_target_instrument,
        )

        instr = None
        if self._ti1_path is not None and self._ti1_path.exists():
            instr = read_target_instrument(self._ti1_path)
        self._detected_instrument    = instr
        self._detected_disable_bidir = disable_bidir_for_instrument(instr)

        if hasattr(self, "_log"):
            # Drop the previous instrument line so only the most recent
            # detection stays visible across repeated chart generation.
            self._clear_previous_instrument_log()
            if instr:
                label = instrument_label(instr)
                if is_spectroscan(instr):
                    # XY table — reads patches individually, so the
                    # bidirectional "reading direction" note does not apply.
                    msg = f"Chart instrument: {label}."
                else:
                    direction = ("one direction only (-B)" if self._detected_disable_bidir
                                 else "both directions")
                    msg = f"Chart instrument: {label} → reading {direction}."
                self._log.appendPlainText(msg)
                self._instr_log_text = msg

        self._apply_bidir_auto_state("guided")
        self._apply_bidir_auto_state("manual")

    def _clear_previous_instrument_log(self) -> None:
        """Remove the last logged "Chart instrument:" line, if still present.

        Lets repeated chart generation replace the instrument/-B notice in
        place rather than stacking up identical lines in the output field.
        """
        if not self._instr_log_text or not hasattr(self, "_log"):
            return
        from PyQt6.QtGui import QTextCursor

        doc = self._log.document()
        found = doc.find(self._instr_log_text)
        if not found.isNull():
            # Remove the whole line plus exactly one adjacent block separator
            # (the trailing one if anything follows, else the leading one) so
            # no blank line is left behind wherever the line sits.
            block = found.block()
            keep = QTextCursor.MoveMode.KeepAnchor
            cursor = QTextCursor(doc)
            if block.next().isValid():
                cursor.setPosition(block.position())
                cursor.setPosition(block.next().position(), keep)
            elif block.previous().isValid():
                cursor.setPosition(block.position() - 1)
                cursor.setPosition(block.position() + len(block.text()), keep)
            else:
                cursor.setPosition(0)
                cursor.setPosition(len(block.text()), keep)
            cursor.removeSelectedText()
        self._instr_log_text = None

    def _apply_bidir_auto_state(self, mode: str) -> None:
        """Grey out and sync a mode's -B checkbox according to its Auto toggle.

        While Auto is on the checkbox is disabled and mirrors the detected
        value (so the locked box shows the effective setting); its own state
        is ignored when the command is built (see _collect_*).
        """
        if mode == "guided":
            auto_cb, bidir_cb = self._bidir_auto_cb, self._bidir_cb
        else:
            auto_cb, bidir_cb = self._m_bidir_auto_cb, self._m_bidir_cb
        auto_on = auto_cb.isChecked()
        bidir_cb.setEnabled(not auto_on)
        if auto_on:
            bidir_cb.blockSignals(True)
            bidir_cb.setChecked(self._detected_disable_bidir)
            bidir_cb.blockSignals(False)

    def _resolve_disable_bidir(self, mode: str) -> bool:
        """The -B value to pass to chartread: auto-detected when Auto is on,
        else the user's checkbox (its saved preset/default)."""
        if mode == "guided":
            auto_cb, bidir_cb = self._bidir_auto_cb, self._bidir_cb
        else:
            auto_cb, bidir_cb = self._m_bidir_auto_cb, self._m_bidir_cb
        if auto_cb.isChecked():
            return self._detected_disable_bidir
        return bidir_cb.isChecked()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_load_ti2(self) -> None:
        from ui.ti2_loader import resolve_ti2
        path = open_file_dialog(
            self, "Load .ti2 file", "TI2 files (*.ti2)",
            extra_path=self._settings.get("custom_output_path", ""),
        )
        if not path:
            return
        result = resolve_ti2(self, Path(path), self._settings)
        if result is None:
            return
        ti2_path, _ = result   # TIFFs re-discovered by set_ti1_path → _try_load_tiffs
        if ti2_path != self._ti1_path:
            self.ti2_replaced.emit()
        self.set_ti1_path(ti2_path)
        self.ti2_loaded.emit(ti2_path)

    def _update_resume_availability(self) -> None:
        if self._ti1_path is None:
            for cb, tip, rcb in [
                (self._resume_cb,   self._resume_tip,   self._refine_cb),
                (self._m_resume_cb, self._m_resume_tip, self._m_refine_cb),
            ]:
                cb.setVisible(False)
                tip.setVisible(False)
                cb.setChecked(False)
                rcb.setEnabled(False)
                rcb.setChecked(False)
            self._refine_strips_path = None
            self._strip_list = []
            return
        ti3 = self._ti1_path.with_suffix(".ti3")
        has_ti3 = ti3.exists()
        for cb, tip in [
            (self._resume_cb,   self._resume_tip),
            (self._m_resume_cb, self._m_resume_tip),
        ]:
            cb.setVisible(has_ti3)
            tip.setVisible(has_ti3)
            if not has_ti3:
                cb.setChecked(False)
        # Auto-detect Refine_Strips file
        refine_file = self._ti1_path.parent / f"Refine_Strips_{self._ti1_path.stem}.txt"
        if refine_file.exists():
            self._refine_strips_path = refine_file
            self._load_refine_strips(refine_file)
            for rcb in (self._refine_cb, self._m_refine_cb):
                rcb.setEnabled(True)
                rcb.setChecked(True)
        else:
            self._refine_strips_path = None
            self._strip_list = []
            for rcb in (self._refine_cb, self._m_refine_cb):
                rcb.setEnabled(False)
                rcb.setChecked(False)
        self._refresh_start_button_label()

    def _refresh_start_button_label(self) -> None:
        """Show 'Continue Measurement' on the Start button when the resume
        checkbox for the active mode is ticked (i.e. the next run will pass
        chartread's -r flag)."""
        cb = self._resume_cb if self._current_mode() == "guided" else self._m_resume_cb
        if cb.isVisible() and cb.isChecked():
            self._start_btn.setText("Continue Measurement")
        else:
            self._start_btn.setText("Start Measurement")

    def _load_refine_strips(self, path: Path) -> None:
        from workflow.profcheck_runner import parse_refine_strips
        try:
            self._strip_list = parse_refine_strips(path)
        except Exception:
            self._strip_list = []

    def start_guided_refinement(self, ti3: Path, strips_file: Path) -> None:
        """Called by main window when user launches guided refinement from Check & Refine tab."""
        ti2 = ti3.with_suffix(".ti2")
        if ti2.exists():
            self.set_ti1_path(ti2)
        self._resume_cb.setChecked(True)
        self._refine_strips_path = strips_file
        self._load_refine_strips(strips_file)
        self._refine_cb.setEnabled(True)
        self._refine_cb.setChecked(True)

    def _try_load_tiffs(self, base_path: Path) -> None:
        stem   = base_path.with_suffix("").stem
        folder = base_path.parent
        tiffs  = sorted(folder.glob(f"{stem}*.tif"))
        if tiffs:
            self._tiff_pages = tiffs
            self._preview.load_tiff(tiffs)
            self._setup_stripe_rects()
        else:
            self._tiff_pages = []
            self._page_stripe_rects = []
            self._strips_per_page = []
            self._preview.clear()
            self._log.appendPlainText(
                "[WARNING] No matching TIFF preview found. "
                "Ensure you scan the correct target."
            )
            self._log.ensureCursorVisible()

    def _setup_stripe_rects(self) -> None:
        """Detect per-page strip positions and resolve per-page strip counts.

        Strip counts come from the chart's .ti2 (``PASSES_IN_STRIPS2``) — the
        authoritative source — so the highlighter maps the right strip to the
        right page even when the last page is partly empty (e.g. a 24,23 chart).
        Rects are detected per page so the arrow lands correctly on every page,
        not just page 1.

        Falls back to the legacy single-page label detector when the .ti2 is
        unavailable or its page count doesn't line up with the loaded TIFFs.
        """
        self._page_stripe_rects = []
        self._strips_per_page = []
        if not self._tiff_pages:
            return

        counts = parse_passes_per_page(self._ti1_path) if self._ti1_path else []
        if counts and len(counts) == len(self._tiff_pages):
            per_page: list[list[QRect]] = []
            for page_path, n in zip(self._tiff_pages, counts):
                rects = _detect_uniform_stripe_rects(page_path, n)
                if not rects:
                    per_page = []
                    break
                per_page.append(rects)
            if per_page:
                self._page_stripe_rects = per_page
                self._strips_per_page = counts
                self._preview.set_stripe_rects(per_page[0])
                return

        # Fallback: legacy label-based detection on page 1 only. Page mapping
        # in _on_stripe_changed then assumes uniform pages (len(rects)/page).
        rects = _detect_stripe_rects(self._tiff_pages[0])
        if rects:
            self._page_stripe_rects = [rects]
            self._preview.set_stripe_rects(rects)

    def _set_settings_enabled(self, enabled: bool) -> None:
        self._stack.setEnabled(enabled)
        self._file_grp.setEnabled(enabled)
        self._save_defaults_btn.setEnabled(enabled)

    def _on_start(self) -> None:
        if not self._ti1_path:
            self._log.appendPlainText("[ERROR] No .ti2 file selected.")
            self._log.ensureCursorVisible()
            return
        if self._runner.is_running:
            return

        params = self._collect_params()
        self._preview.set_bidirectional(not params.disable_bidir)
        self._log.clear()
        self._auto_proceed = False
        self._all_done_shown = False
        self._instrument_disconnected = False
        self._device_busy = False
        self._no_instrument = False
        _ti3_pre = self._ti1_path.with_suffix(".ti3") if self._ti1_path else None
        self._ti3_mtime_before = (
            _ti3_pre.stat().st_mtime if (_ti3_pre and _ti3_pre.exists()) else None
        )
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/IM", "chartread.exe"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.run(["killall", "-q", "chartread"], capture_output=True)
        self._set_settings_enabled(False)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        QApplication.instance().installEventFilter(self)

        if self._current_mode() == "guided":
            resume_cb  = self._resume_cb
            refine_cb  = self._refine_cb
        else:
            resume_cb  = self._m_resume_cb
            refine_cb  = self._m_refine_cb
        guided = (
            resume_cb.isChecked()
            and refine_cb.isChecked()
            and bool(self._strip_list)
        )
        self._guided_refinement_active = guided
        self._resume_active = resume_cb.isChecked()

        self._manager.set_guided_strips(self._strip_list if guided else [])

        self._manager.start(
            params,
            on_line=self._on_log_line,
            on_finish=self._on_measure_done,
        )
        self.measurement_active.emit(True)

    def _on_stop(self) -> None:
        self._key_watchdog.stop()
        self._manager.abort()

    def _arm_key_watchdog(self) -> None:
        """Start the no-response watchdog after sending a keystroke from a dialog.

        If chartread does not emit any output within the timer interval, the
        watchdog assumes the keystroke did not reach the instrument and warns
        the user (without auto-aborting — the Stop button stays in their hands).
        """
        self._last_chartread_output_ts = time.monotonic()
        self._key_watchdog.start()

    def _on_key_watchdog_timeout(self) -> None:
        # Only warn if chartread is still expected to be running and no output
        # arrived between arming and now.
        if not self._stop_btn.isEnabled():
            return
        idle = time.monotonic() - self._last_chartread_output_ts
        if idle < self._key_watchdog.interval() / 1000.0 - 0.5:
            return
        self._log.appendPlainText(
            "[WARN] No response from chartread after sending a key. "
            "The keystroke may not have reached the instrument. "
            "Try pressing the key again, or click Stop and restart the measurement."
        )
        self._log.ensureCursorVisible()
        self._flash_status(
            "chartread is not responding — the last keystroke may have been lost.",
            duration_ms=8000,
        )

    def _on_keypress_failed(self, key_label: str, reason: str) -> None:
        self._log.appendPlainText(
            f"[WARN] Could not send '{key_label}' to chartread: {reason} "
            "Click Stop and restart the measurement; if the problem persists, "
            "please report it with the log file."
        )
        self._log.ensureCursorVisible()
        self._flash_status(
            f"Keypress '{key_label}' could not be delivered to chartread.",
            duration_ms=8000,
        )

    def _flash_status(self, text: str, duration_ms: int = 8000) -> None:
        self._status_bar_lbl.setText(text)
        self._status_bar_lbl.setVisible(True)
        QTimer.singleShot(duration_ms, lambda: self._status_bar_lbl.setVisible(False))

    def _on_log_line(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.ensureCursorVisible()
        # chartread produced output → it is alive and processed (or never needed)
        # the last keystroke. Cancel the watchdog so it cannot misfire mid-scan.
        self._last_chartread_output_ts = time.monotonic()
        if self._key_watchdog.isActive():
            self._key_watchdog.stop()
        # Only flag fatal errors — strip read failures are recoverable and handled
        # separately via the strip_error signal / dialog.
        if "communications failure" in line.lower():
            self._measure_failed = True

    def _on_wrong_strip(self, read: str, expected: str) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Wrong Strip Read")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            f"<b>Strip {read} was read, but strip {expected} was expected.</b><br><br>"
            "This happens when the instrument is placed on the wrong stripe. "
            "You have three options:<br><br>"
            "&nbsp;&nbsp;<b>Use Anyway</b> — accept the reading for strip "
            f"{read} and continue. Use this if you intentionally read "
            f"{read} out of order.<br><br>"
            "&nbsp;&nbsp;<b>Retry</b> — discard this reading and try again. "
            f"Place your instrument at the correct position for strip {expected}.<br><br>"
            "&nbsp;&nbsp;<b>Give Up</b> — stop the measurement without saving.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["\r"]   # default: use anyway

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        use_btn   = QPushButton("Use Anyway", dlg)
        retry_btn = QPushButton("Retry",      dlg)
        give_btn  = QPushButton("Give Up",    dlg)
        use_btn.setObjectName("primary")
        use_btn.setFixedHeight(32)
        retry_btn.setFixedHeight(32)
        give_btn.setFixedHeight(32)

        def _use():
            chosen[0] = "\r"
            dlg.accept()

        def _retry():
            chosen[0] = " "
            dlg.accept()

        def _give_up():
            chosen[0] = "\x1b"
            dlg.accept()

        use_btn.clicked.connect(_use)
        retry_btn.clicked.connect(_retry)
        give_btn.clicked.connect(_give_up)

        btn_row.addWidget(use_btn)
        btn_row.addWidget(retry_btn)
        btn_row.addStretch()
        btn_row.addWidget(give_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()

        if chosen[0] != "\x1b":
            QApplication.instance().installEventFilter(self)
        # If giving up, chartread will exit and _on_measure_done re-enables UI.

    def _on_unexpected_response(self, delta_e: str) -> None:
        from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Unexpected Color Response")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            f"<b>An unexpected color response was detected (ΔE {delta_e}).</b><br><br>"
            "This usually means the instrument was not aligned correctly with "
            "the stripe, was moved during the scan, or the wrong stripe was read. "
            "A ΔE this high indicates the measured colors are very far from what "
            "is expected.<br><br>"
            "&nbsp;&nbsp;<b>Use Anyway</b> — accept the reading and continue. "
            "Only use this if you are sure the scan was correct.<br><br>"
            "&nbsp;&nbsp;<b>Retry</b> — discard this reading, re-position your "
            "instrument carefully on the correct stripe, and try again.<br><br>"
            "&nbsp;&nbsp;<b>Give Up</b> — stop the measurement without saving.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["\r"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        use_btn   = QPushButton("Use Anyway", dlg)
        retry_btn = QPushButton("Retry",      dlg)
        give_btn  = QPushButton("Give Up",    dlg)
        use_btn.setObjectName("primary")
        use_btn.setFixedHeight(32)
        retry_btn.setFixedHeight(32)
        give_btn.setFixedHeight(32)

        def _use():
            chosen[0] = "\r"
            dlg.accept()

        def _retry():
            chosen[0] = " "
            dlg.accept()

        def _give_up():
            chosen[0] = "\x1b"
            dlg.accept()

        use_btn.clicked.connect(_use)
        retry_btn.clicked.connect(_retry)
        give_btn.clicked.connect(_give_up)

        btn_row.addWidget(use_btn)
        btn_row.addWidget(retry_btn)
        btn_row.addStretch()
        btn_row.addWidget(give_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()

        if chosen[0] != "\x1b":
            QApplication.instance().installEventFilter(self)

    def _on_sensor_wrong_position(self) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Instrument in Wrong Position")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>The measurement device is in the wrong position.</b><br><br>"
            "It looks like the instrument is still in its <b>calibration position</b> "
            "(sensor facing up or to the side). "
            "To scan a strip, it needs to be switched to <b>measuring position</b> "
            "(sensor facing down, resting on the paper).<br><br>"
            "How to fix it:<br>"
            "&nbsp;&nbsp;1. Flip or slide the sensor head so it faces <b>downward</b>.<br>"
            "&nbsp;&nbsp;2. Place the instrument at the beginning of the strip.<br>"
            "&nbsp;&nbsp;3. Press <b>OK</b> — chartread is still waiting and you can scan straight away.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        QApplication.instance().installEventFilter(self)

    def _on_strip_interrupted(self) -> None:
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Strip Read Interrupted")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>The strip read was stopped before it finished.</b><br><br>"
            "This usually happens if the instrument switch is pressed mid-scan "
            "or if scanning is interrupted by another process.<br><br>"
            "&nbsp;&nbsp;<b>Resume</b> — chartread is still waiting; "
            "re-position the instrument at the start of the current strip and continue.<br><br>"
            "&nbsp;&nbsp;<b>Give Up</b> — stop the measurement without saving.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["\r"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        resume_btn = QPushButton("Resume", dlg)
        give_btn   = QPushButton("Give Up", dlg)
        resume_btn.setObjectName("primary")
        resume_btn.setFixedHeight(32)
        give_btn.setFixedHeight(32)

        def _resume():
            chosen[0] = "\r"
            dlg.accept()

        def _give_up():
            chosen[0] = "\x1b"
            dlg.accept()

        resume_btn.clicked.connect(_resume)
        give_btn.clicked.connect(_give_up)

        btn_row.addWidget(resume_btn)
        btn_row.addStretch()
        btn_row.addWidget(give_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()

        if chosen[0] != "\x1b":
            QApplication.instance().installEventFilter(self)

    def _on_unread_confirm(self, patch_info: str) -> None:
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Patches Still Unread")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>The chart is not fully measured yet.</b><br><br>"
            f"At least one patch is still unread: <b>{patch_info}</b>.<br><br>"
            "&nbsp;&nbsp;<b>Save Partial</b> — save what's been measured so far. "
            "You can resume later by ticking <i>Refine / resume existing measurement (-r)</i>.<br><br>"
            "&nbsp;&nbsp;<b>Keep Measuring</b> — return to the strip menu and continue.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["n"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        save_btn = QPushButton("Save Partial", dlg)
        keep_btn = QPushButton("Keep Measuring", dlg)
        save_btn.setObjectName("primary")
        save_btn.setFixedHeight(32)
        keep_btn.setFixedHeight(32)

        def _save():
            chosen[0] = "y"
            dlg.accept()

        def _keep():
            chosen[0] = "n"
            dlg.accept()

        save_btn.clicked.connect(_save)
        keep_btn.clicked.connect(_keep)

        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        btn_row.addWidget(keep_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()

        # 'y' makes chartread write the partial .ti3 and exit; 'n' returns
        # to the strip menu where the event filter is needed again.
        if chosen[0] == "n":
            QApplication.instance().installEventFilter(self)

    def _on_generic_instrument_error(self, friendly: str, technical: str) -> None:
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Instrument Error")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        # Show the friendly message first, with the technical detail as a smaller line.
        msg = QLabel(
            f"<b>{friendly}</b><br>"
            f"<span style='color:#888;'>({technical})</span><br><br>"
            "&nbsp;&nbsp;<b>Retry</b> — try the operation again.<br><br>"
            "&nbsp;&nbsp;<b>Give Up</b> — stop the measurement without saving.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["\r"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        retry_btn = QPushButton("Retry", dlg)
        give_btn  = QPushButton("Give Up", dlg)
        retry_btn.setObjectName("primary")
        retry_btn.setFixedHeight(32)
        give_btn.setFixedHeight(32)

        def _retry():
            chosen[0] = "\r"
            dlg.accept()

        def _give_up():
            chosen[0] = "\x1b"
            dlg.accept()

        retry_btn.clicked.connect(_retry)
        give_btn.clicked.connect(_give_up)

        btn_row.addWidget(retry_btn)
        btn_row.addStretch()
        btn_row.addWidget(give_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()

        if chosen[0] != "\x1b":
            QApplication.instance().installEventFilter(self)

    def _on_device_busy(self) -> None:
        if self._device_busy:
            return
        self._device_busy = True

    def _on_no_instrument(self) -> None:
        self._no_instrument = True

    def _on_usb_claimed_by_vm(self) -> None:
        self._usb_claimed_by_vm = True

    # Group B: capture startup-failure messages so _on_measure_done can show
    # a friendly terminal dialog instead of the generic "measurement failed".
    def _on_coms_init_failed(self, msg: str) -> None:
        self._coms_init_failed_msg = msg

    def _on_inst_init_failed(self, msg: str) -> None:
        self._inst_init_failed_msg = msg

    def _on_instrument_wrong_type(self, capability: str) -> None:
        self._instrument_wrong_type = capability

    def _on_ccmx_load_failed(self, msg: str) -> None:
        self._ccmx_load_failed_msg = msg

    def _on_mode_set_failed(self, msg: str) -> None:
        self._mode_set_failed_msg = msg

    def _on_info_message(self, category: str, text: str) -> None:
        # Log it and flash a status bar message (non-blocking).
        self._log.appendPlainText(f"[INFO] {text}")
        self._log.ensureCursorVisible()
        self._flash_status(text, duration_ms=6000)

    # Group D: spot/XY mode defensive dialogs. They only fire if someone
    # invokes chartread in a non-strip mode (e.g. through extra-args). In
    # strip mode these signals are never emitted.
    def _on_xy_place_sheet(self, sheet_n: int, total: int) -> None:
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Place Sheet on XY Table")
        dlg.setMinimumWidth(460)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)
        msg = QLabel(
            f"<b>Place sheet {sheet_n} of {total} on the XY table.</b><br><br>"
            "Press <b>Continue</b> when the sheet is positioned, or <b>Give Up</b> "
            "to stop without saving.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["\r"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cont_btn = QPushButton("Continue", dlg)
        give_btn = QPushButton("Give Up", dlg)
        cont_btn.setObjectName("primary")
        cont_btn.setFixedHeight(32)
        give_btn.setFixedHeight(32)

        def _cont():
            chosen[0] = "\r"
            dlg.accept()

        def _give_up():
            chosen[0] = "\x1b"
            dlg.accept()

        cont_btn.clicked.connect(_cont)
        give_btn.clicked.connect(_give_up)

        btn_row.addWidget(cont_btn)
        btn_row.addStretch()
        btn_row.addWidget(give_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()
        if chosen[0] != "\x1b":
            QApplication.instance().installEventFilter(self)

    def _on_spot_ready(self, patch_id: str) -> None:
        # Spot mode isn't ChromIQ's default workflow; a status-bar hint is
        # enough — the keyboard event filter still passes f/b/n/d/Enter/Esc
        # through to chartread so the user can drive it manually.
        self._flash_status(
            f"Spot mode: ready to read patch '{patch_id}'. "
            "Press Enter to read, f/b to navigate, d when done.",
            duration_ms=10000,
        )

    def _on_abort_confirm(self) -> None:
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Confirm Abort")
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)
        msg = QLabel(
            "<b>Stop measuring without saving?</b><br><br>"
            "Choose <b>Yes</b> to abort, or <b>No</b> to keep measuring.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        chosen = ["n"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        yes_btn = QPushButton("Yes — Abort", dlg)
        no_btn  = QPushButton("No — Keep Measuring", dlg)
        no_btn.setObjectName("primary")
        yes_btn.setFixedHeight(32)
        no_btn.setFixedHeight(32)

        def _yes():
            chosen[0] = "y"
            dlg.accept()

        def _no():
            chosen[0] = "n"
            dlg.accept()

        yes_btn.clicked.connect(_yes)
        no_btn.clicked.connect(_no)

        btn_row.addWidget(yes_btn)
        btn_row.addStretch()
        btn_row.addWidget(no_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        self._manager.send_key(chosen[0])
        self._arm_key_watchdog()
        if chosen[0] == "n":
            QApplication.instance().installEventFilter(self)

    def _on_instrument_disconnected(self) -> None:
        if self._instrument_disconnected:
            return
        self._instrument_disconnected = True
        self._log.appendPlainText(
            "\n[ERROR] Instrument disconnected — stopping measurement."
        )
        self._log.ensureCursorVisible()
        self._manager.abort()

    def _on_strip_error(self, reason: str) -> None:
        from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Strip Read Failed")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            f"<b>The stripe could not be read:</b> {reason}<br><br>"
            "Re-position your instrument at the beginning of the stripe and try again. "
            "If the error keeps occurring, try scanning more slowly and steadily, or "
            "raise the <i>Patch consistency tolerance</i> setting before the next run.<br><br>"
            "&nbsp;&nbsp;<b>Retry</b> — read this same stripe again.<br>"
            "&nbsp;&nbsp;<b>Skip Stripe</b> — leave this stripe unread for now and "
            "jump to the next unread one. You can come back to it later in this session.<br>"
            "&nbsp;&nbsp;<b>Save Partial &amp; Quit</b> — stop here and save what you "
            "have read so far. Next time you load this chart, "
            "<i>Continue Measurement</i> will pick up where you left off.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # "retry" → send "\r"                                        (any key = retry)
        # "skip"  → send "\r" then "n" via manager                   (retry → strip menu → next unread)
        # "save"  → send "\r" then "d" then auto-"y" on Are-you-sure (retry → strip menu → done → confirm)
        chosen = ["retry"]

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        retry_btn = QPushButton("Retry",              dlg)
        skip_btn  = QPushButton("Skip Stripe",        dlg)
        save_btn  = QPushButton("Save Partial && Quit", dlg)
        retry_btn.setObjectName("primary")
        retry_btn.setFixedHeight(32)
        skip_btn.setFixedHeight(32)
        save_btn.setFixedHeight(32)

        def _retry():
            chosen[0] = "retry"
            dlg.accept()

        def _skip():
            chosen[0] = "skip"
            dlg.accept()

        def _save():
            chosen[0] = "save"
            dlg.accept()

        retry_btn.clicked.connect(_retry)
        skip_btn.clicked.connect(_skip)
        save_btn.clicked.connect(_save)

        btn_row.addWidget(retry_btn)
        btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()

        if chosen[0] == "retry":
            self._manager.send_key("\r")
        elif chosen[0] == "skip":
            # Two-step: retry returns chartread to the strip menu, then 'n'
            # jumps to the next unread stripe.
            self._manager.send_post_retry_key("n")
        else:  # save partial and quit
            # Three-step chain inside the manager: \r → strip menu → 'd' →
            # ("Are you sure" → 'y') → chartread writes the .ti3 and exits.
            self._manager.send_save_partial_and_quit()

        self._arm_key_watchdog()
        QApplication.instance().installEventFilter(self)
        # On the save path chartread will exit on its own once 'y' is sent,
        # and _on_measure_done will then re-enable the UI and auto-arm resume.

    def _on_calibration_prompt(self) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setWindowTitle("Calibration Required")
        dlg.setMinimumWidth(500)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            "<b>Your instrument needs to be calibrated before measuring.</b><br><br>"
            "Place the instrument in the <b>calibration position</b> as described "
            "in its manual, then click <b>Start Calibration</b>.<br><br>"
            "The calibration takes only a few seconds. Once it is complete, another "
            "message will appear with instructions on how to start measuring the "
            "stripes.",
            dlg,
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_box = QDialogButtonBox()
        ok_btn = btn_box.addButton("Start Calibration", QDialogButtonBox.ButtonRole.AcceptRole)
        ok_btn.setObjectName("primary")
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)

        tint_dialog_primary(dlg, _TAB_COLOR)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # "Start Calibration" — any key tells chartread to proceed.
            self._manager.send_key("\r")
            self._arm_key_watchdog()
            QApplication.instance().installEventFilter(self)
        else:
            # The user dismissed the prompt with the window's close button (or
            # Esc) instead of starting calibration. Esc at chartread's
            # calibration prompt cancels the run cleanly; chartread then exits
            # and _on_measure_done re-enables the UI (same path as "Give Up").
            self._manager.send_key("\x1b")
            self._arm_key_watchdog()
            # Don't re-install the event filter: chartread is shutting down.

    def _on_calibration_done(self) -> None:
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFrame, QGridLayout, QLabel, QVBoxLayout,
        )

        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        from ui.theme import resolve_mode
        _mode = resolve_mode(self._settings.get("appearance", "auto"))
        if _mode == "light":
            _frame_bg, _frame_border, _dim_text = "#f7f4ef", "#d0ccc6", "#7a7570"
        else:
            _frame_bg, _frame_border, _dim_text = "#181818", "#2a2a2a", "#909090"
        _frame_style = (
            f"QFrame {{ background: {_frame_bg}; border: 1px solid {_frame_border};"
            " border-radius: 6px; }}"
        )
        _key_style = (
            f"font-family: Menlo, monospace; font-weight: 700; color: {_TAB_COLOR};"
            " background: transparent; border: none;"
        )
        _dim_style = f"color: {_dim_text}; background: transparent; border: none;"
        _plain_style = "background: transparent; border: none;"

        if self._guided_refinement_active and self._strip_list:
            first = self._strip_list[0]
            n = len(self._strip_list)
            dlg.setWindowTitle("Calibration Complete — Guided Refinement Ready")

            msg = QLabel(
                "<b>Calibration complete. The app will guide you to each strip.</b><br><br>"
                f"There are <b>{n} strip(s)</b> to re-measure. "
                "The app will automatically navigate chartread to each one — "
                "<b>you do not need to press f or b yourself.</b>",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)

            hint_frame = QFrame(dlg)
            hint_frame.setStyleSheet(_frame_style)
            hfl = QVBoxLayout(hint_frame)
            hfl.setContentsMargins(16, 12, 16, 12)
            hfl.setSpacing(6)
            hdr = QLabel("To identify which strip to scan:", dlg)
            hdr.setStyleSheet("font-weight: 600; " + _plain_style)
            hfl.addWidget(hdr)
            for bullet_text in (
                "Watch the <b>highlighted strip</b> in the preview panel on the right.",
                "Or follow the <b>output field</b> below — it will name the strip.",
            ):
                b = QLabel(f"  •  {bullet_text}", dlg)
                b.setWordWrap(True)
                b.setStyleSheet(_plain_style)
                hfl.addWidget(b)
            layout.addWidget(hint_frame)

            first_lbl = QLabel(
                f"<b>First strip: {first}</b> — place your instrument there and scan when ready.",
                dlg,
            )
            first_lbl.setWordWrap(True)
            layout.addWidget(first_lbl)

            footnote = QLabel(
                "When all strips are done, the output field will tell you to press ‘d’ to finish and save.",
                dlg,
            )
            footnote.setWordWrap(True)
            footnote.setStyleSheet(_dim_style)
            layout.addWidget(footnote)

        elif self._resume_active:
            dlg.setWindowTitle("Calibration Complete — Manual Re-measurement")

            msg = QLabel(
                "<b>Calibration complete. You are ready to re-measure strips manually.</b><br><br>"
                "chartread is resuming from your existing measurement. Re-scan any strip "
                "to overwrite it, or scan unread strips to fill them in — follow the steps "
                "below to pick which one.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)

            step_frame = QFrame(dlg)
            step_frame.setStyleSheet(_frame_style)
            sfl = QGridLayout(step_frame)
            sfl.setContentsMargins(16, 12, 16, 12)
            sfl.setHorizontalSpacing(14)
            sfl.setVerticalSpacing(7)
            sfl.setColumnStretch(1, 1)
            steps = [
                ("1.", "Press <b>f</b> (forward) or <b>b</b> (back) until chartread shows the strip you want."),
                ("2.", "Place your instrument on that strip and scan it."),
                ("3.", "Repeat for each strip you want to update, then press <b>d</b> to finish and save."),
            ]
            for row, (num, text) in enumerate(steps):
                n_lbl = QLabel(num)
                n_lbl.setStyleSheet(_key_style)
                n_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
                t_lbl = QLabel(text)
                t_lbl.setWordWrap(True)
                t_lbl.setStyleSheet(_plain_style)
                sfl.addWidget(n_lbl, row, 0)
                sfl.addWidget(t_lbl, row, 1)
            layout.addWidget(step_frame)

            footnote = QLabel(
                "<b>n</b> jumps to the next unread strip  —  <b>Esc / q</b> quits without saving.",
                dlg,
            )
            footnote.setWordWrap(True)
            footnote.setStyleSheet(_dim_style)
            layout.addWidget(footnote)

        else:
            dlg.setWindowTitle("Calibration Complete — How to Measure")

            msg = QLabel(
                "<b>Calibration complete. You are ready to start measuring.</b><br><br>"
                "Place your instrument at the beginning of the first stripe and trigger it to scan. "
                "Then proceed stripe by stripe until all are done.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)

            key_frame = QFrame(dlg)
            key_frame.setStyleSheet(_frame_style)
            kfl = QGridLayout(key_frame)
            kfl.setContentsMargins(16, 12, 16, 12)
            kfl.setHorizontalSpacing(20)
            kfl.setVerticalSpacing(6)
            kfl.setColumnStretch(1, 1)
            key_rows = [
                ("f", "Move to the next stripe"),
                ("b", "Move back to the previous stripe"),
                ("n", "Jump to the next unread stripe"),
                ("d", "Finish and save when all stripes are done"),
                ("Esc / q", "Quit without saving"),
            ]
            for row, (key, desc) in enumerate(key_rows):
                k = QLabel(key)
                k.setStyleSheet(_key_style)
                d = QLabel(desc)
                d.setStyleSheet(_plain_style)
                kfl.addWidget(k, row, 0, Qt.AlignmentFlag.AlignLeft)
                kfl.addWidget(d, row, 1, Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(key_frame)

            footnote = QLabel("These instructions are always visible in the output log below.", dlg)
            footnote.setWordWrap(True)
            footnote.setStyleSheet(_dim_style)
            layout.addWidget(footnote)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setObjectName("primary")
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)

        tint_dialog_primary(dlg, _TAB_COLOR)
        dlg.exec()
        QApplication.instance().installEventFilter(self)

    def _on_all_stripes_done(self) -> None:
        if self._all_done_shown:
            return
        self._all_done_shown = True

        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        _ti3_path = self._ti1_path.with_suffix(".ti3") if self._ti1_path else None
        is_cal = (
            _ti3_path is not None
            and _ti3_path.stem.startswith("cal_")
            and bool(self._settings.get("calibration_mode", False))
        )

        # Suspend the event filter while the dialog is open so that keyboard
        # interactions with the dialog (Enter, Space, Esc) are not forwarded
        # to chartread as spurious keystrokes.
        QApplication.instance().removeEventFilter(self)

        dlg = QDialog(self)
        dlg.setMinimumWidth(560)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        if self._guided_refinement_active:
            n = len(self._strip_list)
            dlg.setWindowTitle("Re-measurement Complete")
            msg = QLabel(
                f"<b>All {n} target strip(s) have been re-measured successfully.</b><br><br>"
                "What would you like to do next?<br><br>"
                "&nbsp;&nbsp;•&nbsp; <b>Build Profile</b> — saves the measurement "
                "and takes you straight to the Build Profile tab to create your updated "
                "ICC profile.<br><br>"
                "&nbsp;&nbsp;•&nbsp; <b>Continue Measuring Manually</b> — keeps "
                "chartread running so you can scan additional strips yourself. "
                "You will have <b>full manual control</b>: use <b>f</b>&nbsp;/&nbsp;<b>b</b> "
                "to move between strips, <b>n</b> to jump to the next unread one, and "
                "<b>d</b> when you are done. "
                "The automatic strip navigation is switched off for the rest of this session.",
                dlg,
            )
        elif is_cal:
            dlg.setWindowTitle("Calibration Measurement Complete")
            msg = QLabel(
                "<b>All stripes of your calibration target have been read successfully.</b><br><br>"
                "The measurement data has been saved. The next step is to turn it into a "
                "<b>calibration file (.cal)</b> — click <b>Create Calibration File</b> to go "
                "directly to the <b>4. Calibration &amp; Profiling</b> tab, where the file "
                "path is already filled in and ready to go.<br><br>"
                "If you would like to re-read any stripe first, click <b>Re-read Stripes</b>. "
                "Use <b>f</b>&nbsp;/&nbsp;<b>b</b> to move forward and back between stripes, "
                "<b>n</b> to jump to the next unread stripe, and press <b>d</b> when you "
                "are done.<br><br>"
                "<span style='color:#909090;'>These instructions are always visible in "
                "the output log below.</span>",
                dlg,
            )
        else:
            dlg.setWindowTitle("All Stripes Read")
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
        if is_cal and not self._guided_refinement_active:
            accept_label = "Create Calibration File →"
        else:
            accept_label = "Build Profile →"
        build_btn = btn_box.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
        build_btn.setObjectName("primary")
        cont_label = "Continue Measuring Manually" if self._guided_refinement_active else "Re-read Stripes"
        btn_box.addButton(cont_label, QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        tint_dialog_primary(dlg, _TAB_COLOR)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._auto_proceed = True
            self._manager.send_key("d")
            self._arm_key_watchdog()
            # Event filter stays off — chartread will finish momentarily.
        else:
            if self._guided_refinement_active:
                # Hand back full keyboard control; disable auto-navigation.
                self._guided_refinement_active = False
                self._manager.set_guided_strips([])
            QApplication.instance().installEventFilter(self)

    def _on_measure_done(self, code: int) -> None:
        self._preview.highlight_stripe(-1)
        self._preview.set_bidirectional(False)
        self._key_watchdog.stop()
        self.measurement_active.emit(False)
        QApplication.instance().removeEventFilter(self)
        self._set_settings_enabled(True)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

        if self._usb_claimed_by_vm:
            self._usb_claimed_by_vm = False
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Not Accessible")
            dlg.setMinimumWidth(500)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>Your measurement device could not be opened — it appears to be "
                "connected to a virtual machine.</b><br><br>"
                "When a device is assigned to a VM (Parallels, VMware, VirtualBox, etc.), "
                "the host operating system cannot access it at the same time.<br><br>"
                "To fix this:<br>"
                "&nbsp;&nbsp;1. In your VM software, disconnect the device from the "
                "virtual machine<br>"
                "&nbsp;&nbsp;2. Reconnect the USB cable if needed<br>"
                "&nbsp;&nbsp;3. Press <b>Start Measurement</b> again",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._no_instrument:
            self._no_instrument = False
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("No Instrument Found")
            dlg.setMinimumWidth(460)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            _conn_bullet = (
                "&nbsp;&nbsp;• connected to your Windows PC via USB<br>"
                if sys.platform == "win32" else
                "&nbsp;&nbsp;• connected to your Mac via USB<br>"
            )
            _driver_hint = (
                "<br>If the instrument is connected but still not found, make sure the "
                "Argyll WinUSB driver is installed for your device (use Argyll's "
                "ArgyllInstallers tool or Zadig). See the Argyll documentation for details."
                if sys.platform == "win32" else ""
            )
            msg = QLabel(
                "<b>No measurement instrument was detected.</b><br><br>"
                "Please make sure your instrument is:<br>"
                + _conn_bullet +
                "&nbsp;&nbsp;• switched on<br>"
                "&nbsp;&nbsp;• not in use by another application<br><br>"
                "Once the instrument is ready, press <b>Start Measurement</b> again."
                + _driver_hint,
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._device_busy:
            self._device_busy = False
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Not Available")
            dlg.setMinimumWidth(480)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>The instrument could not be opened — it is already in use by "
                "another process.</b><br><br>"
                "This usually happens when a previous measurement session was not "
                "stopped properly before closing the app. ChromIQ automatically "
                "tries to free the device when starting a new measurement.<br><br>"
                "Please click OK and then press <b>Start Measurement</b> again.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._instrument_disconnected:
            self._instrument_disconnected = False
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Disconnected")
            dlg.setMinimumWidth(460)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>The measurement instrument was disconnected.</b><br><br>"
                "The measurement has been stopped automatically. Please check "
                "the USB connection, reconnect your instrument, and start a "
                "new measurement.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        # Group B: friendly terminal dialogs for chartread startup failures.
        # The communications/init failures share a dialog body — the only
        # difference is which Argyll error string is shown.
        _b_init_msg = self._coms_init_failed_msg or self._inst_init_failed_msg
        if _b_init_msg:
            self._coms_init_failed_msg = None
            self._inst_init_failed_msg = None
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Failed to Initialize")
            dlg.setMinimumWidth(480)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>The instrument could not be initialised.</b><br><br>"
                f"Argyll reported: <i>{_b_init_msg}</i><br><br>"
                "Try the following:<br>"
                "&nbsp;&nbsp;• Unplug and replug the USB cable<br>"
                "&nbsp;&nbsp;• Make sure the instrument is switched on<br>"
                "&nbsp;&nbsp;• Close any other application that might be using it<br><br>"
                "Then press <b>Start Measurement</b> again.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._instrument_wrong_type:
            cap = self._instrument_wrong_type
            self._instrument_wrong_type = None
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Type Mismatch")
            dlg.setMinimumWidth(480)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                f"<b>This instrument cannot measure in {cap} mode.</b><br><br>"
                "ChromIQ measures printed test charts, which need a "
                "<b>reflection-capable</b> instrument (e.g. i1Pro, i1Pro 2, "
                "i1Pro 3, ColorMunki, SpectroScan).<br><br>"
                "Display-only colorimeters (e.g. i1Display) cannot read paper. "
                "Connect a reflection-capable instrument and try again.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._ccmx_load_failed_msg:
            err = self._ccmx_load_failed_msg
            self._ccmx_load_failed_msg = None
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Correction File Failed to Load")
            dlg.setMinimumWidth(500)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>The colorimeter correction file could not be applied.</b><br><br>"
                f"Argyll reported: <i>{err}</i><br><br>"
                "Check the path in <b>Settings → Argyll Options</b>, or remove the "
                "CCMX / CCSS reference from the extra-args field and try again.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        if self._mode_set_failed_msg:
            err = self._mode_set_failed_msg
            self._mode_set_failed_msg = None
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle("Instrument Mode Rejected")
            dlg.setMinimumWidth(480)
            layout = QVBoxLayout(dlg)
            layout.setSpacing(16)
            layout.setContentsMargins(24, 20, 24, 20)
            msg = QLabel(
                "<b>The instrument refused the requested measurement mode.</b><br><br>"
                f"Argyll reported: <i>{err}</i><br><br>"
                "Check the instrument-specific flags in your settings (high-res, UV mode, "
                "scan tolerance, etc.) and try again.",
                dlg,
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            tint_dialog_primary(dlg, _TAB_COLOR)
            dlg.exec()
            return

        # chartread exits non-zero even on a clean 'd' (done) completion.
        # Only count the .ti3 as valid if it was actually written during this run —
        # a stale file from a previous session must not mask a fresh failure.
        ti3 = self._ti1_path.with_suffix(".ti3") if self._ti1_path else None
        if ti3 is not None and ti3.exists():
            ti3_exists = (
                self._ti3_mtime_before is None          # file didn't exist before → fresh
                or ti3.stat().st_mtime > self._ti3_mtime_before
            )
        else:
            ti3_exists = False
        failed = self._measure_failed or (code != 0 and not ti3_exists)
        self._measure_failed = False

        is_cal = (
            ti3 is not None
            and ti3.stem.startswith("cal_")
            and bool(self._settings.get("calibration_mode", False))
        )
        if failed:
            self._log.appendPlainText("\n[ERROR] Measurement failed — see output above.")
        elif ti3_exists and not self._all_done_shown:
            # chartread wrote a .ti3 but never emitted "ALL ROWS READ" —
            # the user pressed 'd' (Save Partial & Quit, or manually in the
            # log) with some patches still unread. Refresh the resume
            # checkbox visibility and auto-tick it so the next click on the
            # Start button (now relabelled "Continue Measurement") resumes
            # chartread with -r against this partial file.
            self._update_resume_availability()
            cb = self._resume_cb if self._current_mode() == "guided" else self._m_resume_cb
            if cb.isVisible():
                cb.setChecked(True)
            self._log.appendPlainText(
                "\n[INFO] Measurement was interrupted — partial readings saved.\n"
                f"Saved: {ti3}\n\n"
                "→ Press Continue Measurement to resume where you left off, "
                "or untick 'Refine / resume existing measurement (-r)' to start over."
            )
            self.measure_finished.emit(ti3)
        else:
            if is_cal:
                next_step = "→ Next step: go to the '4. Calibration & Profiling' tab to create your calibration file."
            else:
                next_step = "→ Next step: go to the '4. Build Profile' tab to create your ICC profile."
            self._log.appendPlainText(
                "\n[OK] Measurement complete.\n"
                f"Saved: {ti3}\n\n"
                + next_step
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
            sent = True
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
                else:
                    sent = False
            if sent:
                self._arm_key_watchdog()
            return True   # consume — don't let widgets act on it
        return False

    def _on_stripe_changed(self, strip_id: str) -> None:
        self._log.appendPlainText(f"[→ strip {strip_id}]")
        self._log.ensureCursorVisible()
        letter = "".join(c for c in strip_id if c.isalpha()).upper()
        if not letter:
            return
        if not self._page_stripe_rects:
            return
        global_idx = letter_to_idx(letter)
        n_pages    = max(1, len(self._tiff_pages))

        # Map the absolute strip index → (page, local index). Prefer the
        # authoritative per-page counts from the .ti2: walking them handles a
        # non-uniform last page (e.g. 24,23) correctly, where a flat
        # global_idx // strips_per_page would keep the first strip of page 2 on
        # page 1. Fall back to a uniform split only when those counts are
        # absent (legacy label-detection path).
        if self._strips_per_page:
            page = 0
            local_idx = global_idx
            for count in self._strips_per_page:
                if local_idx < count:
                    break
                local_idx -= count
                page += 1
            strips_per_page_dbg = ",".join(str(c) for c in self._strips_per_page)
        else:
            strips_per_page = max(1, len(self._page_stripe_rects[0]))
            page            = global_idx // strips_per_page
            local_idx       = global_idx % strips_per_page
            strips_per_page_dbg = str(strips_per_page)

        page = max(0, min(page, n_pages - 1))
        # Use this page's own rects when we detected them per page; otherwise
        # (legacy fallback) reuse the only page we have.
        rects_idx = min(page, len(self._page_stripe_rects) - 1)
        rects = self._page_stripe_rects[rects_idx]

        if bool(self._settings.get("debug_highlighter", False)):
            msg = (
                f"[highlighter] id={strip_id} letter={letter} "
                f"global_idx={global_idx} strips_per_page={strips_per_page_dbg} "
                f"page={page + 1}/{n_pages} local_idx={local_idx}"
            )
            self._log.appendPlainText(msg)
            log.warning(msg)  # also goes to chromiq.log file

        self._preview.set_stripe_rects(rects)
        if 0 <= page < n_pages:
            self._preview.show_page(page)
        self._preview.highlight_stripe(local_idx)

    # ------------------------------------------------------------------
    # Param collection
    # ------------------------------------------------------------------

    def _collect_guided(self) -> MeasureParams:
        extra_args: list[str] = []
        for opt in self._chartread_opts:
            extra_args += opt.build_args()

        return MeasureParams(
            ti1_path            = self._ti1_path,
            instrument          = str(self._instr_spin.value()),
            disable_bidir       = self._resolve_disable_bidir("guided"),
            suppress_warnings   = self._suppress_cb.isChecked(),
            disable_initial_cal = self._nocal_cb.isChecked(),
            patch_by_patch      = self._pbp_cb.isChecked(),
            resume              = self._resume_cb.isChecked(),
            extra_args          = " ".join(extra_args),
        )

    def _collect_manual(self) -> MeasureParams:
        extra_args: list[str] = []
        for opt in self._m_chartread_opts:
            extra_args += opt.build_args()

        return MeasureParams(
            ti1_path            = self._ti1_path,
            instrument          = str(self._m_instr_spin.value()),
            disable_bidir       = self._resolve_disable_bidir("manual"),
            suppress_warnings   = self._m_suppress_cb.isChecked(),
            disable_initial_cal = self._m_nocal_cb.isChecked(),
            patch_by_patch      = self._m_pbp_cb.isChecked(),
            resume              = self._m_resume_cb.isChecked(),
            extra_args          = " ".join(extra_args),
        )

    def _collect_params(self) -> MeasureParams:
        if self._current_mode() == "guided":
            return self._collect_guided()
        return self._collect_manual()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_save_defaults(self) -> None:
        s = self._settings
        if self._current_mode() == "guided":
            s.set("measure_disable_bidir",     self._bidir_cb.isChecked())
            s.set("measure_bidir_auto",        self._bidir_auto_cb.isChecked())
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
        else:
            s.set("manual2_chartread_instr",    self._m_instr_spin.value())
            s.set("manual2_chartread_bidir",    self._m_bidir_cb.isChecked())
            s.set("manual2_chartread_bidir_auto", self._m_bidir_auto_cb.isChecked())
            s.set("manual2_chartread_suppress", self._m_suppress_cb.isChecked())
            s.set("manual2_chartread_nocal",    self._m_nocal_cb.isChecked())
            s.set("manual2_chartread_pbp",      self._m_pbp_cb.isChecked())
            for opt in self._m_chartread_opts:
                if opt.checkbox:
                    s.set(f"manual2_chartread_{opt.key}_enabled", opt.checkbox.isChecked())
                if opt.widget is not None:
                    if isinstance(opt.widget, (QSpinBox, QDoubleSpinBox)):
                        s.set(f"manual2_chartread_{opt.key}_value", opt.widget.value())
                    elif isinstance(opt.widget, QComboBox):
                        s.set(f"manual2_chartread_{opt.key}_value", opt.widget.currentData())
        self._log.appendPlainText("Measurement settings saved as defaults.")
        self._log.ensureCursorVisible()

    def _restore_defaults(self) -> None:
        s = self._settings
        # Guided defaults
        self._bidir_cb.setChecked(bool(s.get("measure_disable_bidir", True)))
        self._bidir_auto_cb.setChecked(bool(s.get("measure_bidir_auto", True)))
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
        # Manual defaults
        m_instr = s.get("manual2_chartread_instr")
        if m_instr is not None:
            try:
                self._m_instr_spin.setValue(int(m_instr))
            except (ValueError, TypeError):
                pass
        self._m_bidir_cb.setChecked(bool(s.get("manual2_chartread_bidir", False)))
        self._m_bidir_auto_cb.setChecked(bool(s.get("manual2_chartread_bidir_auto", True)))
        self._m_suppress_cb.setChecked(bool(s.get("manual2_chartread_suppress", True)))
        self._m_nocal_cb.setChecked(bool(s.get("manual2_chartread_nocal", False)))
        self._m_pbp_cb.setChecked(bool(s.get("manual2_chartread_pbp", False)))
        for opt in self._m_chartread_opts:
            if opt.checkbox:
                enabled = bool(s.get(f"manual2_chartread_{opt.key}_enabled", False))
                opt.checkbox.setChecked(enabled)
            if opt.widget is not None:
                val = s.get(f"manual2_chartread_{opt.key}_value")
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
        presets = self._m_load_presets()
        self._m_populate_preset_combo(presets)
        # Reflect the restored Auto toggles (grey out / sync the -B checkboxes).
        self._apply_bidir_auto_state("guided")
        self._apply_bidir_auto_state("manual")
