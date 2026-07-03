"""Tools → "Build scanner or camera profile" — profile a scanner or camera (#98).

Workflow: pick a **measured** ChromIQ chart and a **scan** of the printed chart,
drag the four corners over the patch area (a live grid confirms the fit), and
ChromIQ runs ``scanin`` (manual ``-F`` registration + perspective) to read the
scan against the chart's measured colours, then ``colprof`` to build the scanner
ICC. Multi-page charts get a scan (or several) placed per page; several scans of
a page are averaged, then the pages are combined before profiling. It can also
profile from a standard target the user owns (IT8, ColorChecker, …) via its
Argyll ``.cht`` + the target's own reference file.

Needs the chart's ``.cht`` + ``.cie`` (built by "Create scanner target" / the
measure-tab checkbox); this tool builds them on the fly if they're missing but
the chart was measured. Green (measure/scanner family), ⓘ per option,
non-native pickers, readable helper text in both themes.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from core.i18n import tr
from core.logger import get_logger
from ui.dialogs.tools_dialogs import (
    _ToolDialogBase, _initial_dir, _remember_dir, neutral_controls_qss)
from ui.scan_grid_marquee import GridSpec, ScanGridMarquee
from ui.styles import SPEC_GREEN
from ui.dialogs.scanin_target_dialog import WHICH_CHART_HELP, WHICH_CHART_CAMERA_NOTE
from ui.theme import resolve_mode
from ui.tooltip_button import TooltipButton
from ui.widgets import (NoScrollComboBox, NoScrollSpinBox, make_browse_button,
                        open_file_dialog)
from workflow.profile_builder import ProfileBuilder, ProfileParams
from workflow.scanin_runner import ScaninParams, ScaninRunner
from workflow.ti3_average import Ti3AverageError, average_scanner_ti3
from workflow.scanin_target import (
    ScaninTargetError, build_scanin_target_from_paths, has_scanner_geometry)
from workflow.standard_targets import list_standard_targets

log = get_logger(__name__)

_TI3_FILTER = "Measured chart (*.ti3);;All files (*)"
_SCAN_FILTER = "Scans (*.tif *.tiff);;All files (*)"
_CHT_FILTER = "Chart recognition (*.cht);;All files (*)"
_REF_FILTER = "Target reference (*.cie *.txt *.ti3 *.cxf);;All files (*)"
# Compact button — a per-widget rule beats the app-wide 28px min-height.
_COMPACT_BTN = "QPushButton { padding: 2px 12px; min-height: 0; font-size: 11px; }"

# Scanner-side capture settings (folded from Knut's VueScan/ArgyllCMS guide).
# Kept as its own key so the large HELP block above stays stable — only this
# short section needs (re-)translating when the wording changes.
SCAN_SETUP_HELP = tr(
    "How to scan the chart for a good profile\n\n"
    "The profile can only be as faithful as the scan, so capture the chart flat "
    "and unaltered:\n\n"
    "• Colour: turn OFF every automatic correction — no colour balance, no "
    "auto-levels or curves, no sharpening, and no scanner ICC profile applied. "
    "(In VueScan: Colour balance = None, curves left at their defaults, "
    "brightness = 1.)\n"
    "• Depth & format: 48-bit RGB (16 bit per channel), saved as an "
    "uncompressed TIFF.\n"
    "• Resolution: 300–600 ppi is plenty for a patch chart. For best quality, "
    "scan higher (e.g. 2400 ppi) and let the software downsample — averaging "
    "pixels lowers noise.\n"
    "• Multiple samples: if your scanner software can average several passes "
    "per scan, turn it on to reduce noise further.\n"
    "• Placement: clean the glass and the chart, lay it flat and square, and "
    "crop to the patch area.\n\n"
    "Scan the same way every time. The profile describes your scanner at these "
    "settings, so changing them later means it no longer fits.")

# Consolidated workflow tips: averaging, multi-page charts, and standard targets.
SCANNING_TIPS_HELP = tr(
    "Getting the best result\n\n"
    "• Average several scans. Scanning the same sheet two or three times and "
    "averaging the reads cancels out the random noise every scanner adds, for a "
    "cleaner profile. Pick your first scan and place its four corners, then use "
    "“Add another scan to average” for each extra scan — each keeps its own "
    "placement, so it's fine if the sheet shifted a little. Pick how they're "
    "combined under “Combine repeated scans by”.\n\n"
    "• Multi-page ChromIQ charts. When a chart spans several pages, a Page "
    "selector appears. Pick and place each page's scan in turn — and you can add "
    "several scans per page too. ChromIQ averages each page's scans, then builds "
    "one profile from all the pages together.\n\n"
    "• A standard target is a single sheet. A bought IT8, ColorChecker or "
    "similar target has no pages (even a two-area target like the Wolf Faust IT8 "
    "is one sheet, read from one scan) — just scan it once, or a few times to "
    "average.")

# Camera profiling — same engine as scanning, so a photo of a target works too.
CAMERA_HELP = tr(
    "Profiling a camera\n\n"
    "This tool works for a digital camera too, not just a scanner — ArgyllCMS "
    "reads camera and scanner targets the same way. Use the “standard target” "
    "mode with a camera target (an X-Rite ColorChecker, IT8, and so on), and "
    "wherever ChromIQ says “scan”, a photo of the target works just the same.\n\n"
    "For a camera the capture matters more than the software:\n\n"
    "• Even light. A camera profile is only valid for the light you shot under, "
    "so light the target flatly and evenly — no glare or hot-spots — under the "
    "lighting you'll actually use (daylight, studio strobe, and so on).\n"
    "• Shoot flat. Photograph raw and convert with a neutral, linear setting — "
    "no creative white balance, tone curve, contrast or sharpening — then export "
    "a plain TIFF. That's the camera version of turning a scanner's correction "
    "off.\n"
    "• Fill the frame square-on, so the target is flat and undistorted.\n"
    "• Keep the profile type on Matrix for a small target like a 24-patch "
    "ColorChecker; a LUT needs a many-patch target.\n\n"
    "The profile applies to that camera under that light. A camera isn't a "
    "colorimeter, so treat it as a very good approximation — great for consistent "
    "studio or repro work, less so across mixed lighting.")


def _chart_base(ti3: Path) -> Path:
    stem = ti3.stem
    if stem.endswith("-verify"):
        stem = stem[: -len("-verify")]
    return ti3.with_name(stem)


class ScannerProfileDialog(_ToolDialogBase):
    TOOL_KEY    = "scanner_profile"
    TITLE       = tr("Build scanner or camera profile")
    EYEBROW     = tr("MEASURE · SCANNER / CAMERA PROFILE")
    ACCENT      = SPEC_GREEN
    RUN_LABEL   = tr("Build scanner or camera profile")
    MIN_WIDTH   = 760
    SCROLLABLE_CONTENT = True    # tall (mode toggle + inputs + marquee + averaging)

    HELP = tr(
        "Builds an ICC colour profile for a scanner or a digital camera, from a "
        "target whose true colours are known. Once built, the profile tells any "
        "colour-managed program how your device really sees colour, so scans and "
        "photos come out accurate instead of dull or colour-cast.\n\n"
        "There are two ways to provide the target — choose one at the top of the "
        "window:\n\n"
        "• A chart you made in ChromIQ. Print and measure a chart as usual and "
        "keep its scanner files (.cht + .cie) — tick 'Also save "
        "scanner-profiling files' after measuring, or use Tools ▸ Create scanner "
        "or camera target. Nothing extra to buy: ChromIQ already knows every "
        "patch's real colour.\n"
        "• A standard target you own. A bought reflective target such as an IT8 "
        "(for example Wolf Faust), an X-Rite ColorChecker or a LaserSoft target. "
        "Pick its type from the list and load the reference data file that came "
        "with it (.cie / .txt).\n\n"
        "Then capture the target on the device you want to profile — scan it, or "
        "for a camera photograph it — as a plain RGB TIFF, with the device's own "
        "colour correction turned OFF. Load it here, drag the four corners over "
        "the patch area until the green grid sits on the real patches, and click "
        "Build scanner or camera profile. ChromIQ compares how your device saw "
        "each patch against the true colours and writes the ICC profile next to "
        "your capture.\n\n"
        "The sections below cover, in order: the best way to capture the target, "
        "averaging several captures for less noise, profiling a camera, and "
        "which target to use.\n\n"
        "───────────────\n"
        "Using your profile\n\n"
        "The profile makes your scans or photos come out accurate — great for "
        "digitising prints, artwork and photos, or for repeatable studio and "
        "repro work, so the result matches the original.\n\n"
        "Two common ways to use it:\n\n"
        "• In your scanner software (VueScan, SilverFast, Epson Scan, etc.): "
        "set this .icc file as the scanner's input / ICC profile, and choose a "
        "working space such as sRGB or Adobe RGB as the output. New scans are "
        "then corrected automatically.\n\n"
        "• In Photoshop or another editor (this is also the route for camera "
        "photos): open the scan or photo — captured with correction OFF — then "
        "Assign Profile ▸ this profile (so the app knows how your device saw the "
        "colours), and Convert to Profile ▸ your working space (e.g. sRGB or "
        "Adobe RGB). The colours now match the original.\n\n"
        "Good to know:\n"
        "• The profile is specific to this device and the settings you captured "
        "with. Keep the scanner's auto-correction off — or the camera's lighting "
        "and raw settings the same — exactly as when you captured the target, or "
        "the profile won't fit.\n"
        "• A scanner profile is most accurate for media like the paper you "
        "profiled; a camera profile is tied to the light you shot under.\n"
        "• The profile characterises the device — it does not sharpen or "
        "retouch; it just makes the colours faithful."
    ) + "\n\n───────────────\n" + SCANNING_TIPS_HELP \
      + "\n\n───────────────\n" + SCAN_SETUP_HELP \
      + "\n\n───────────────\n" + CAMERA_HELP \
      + "\n\n───────────────\n" + WHICH_CHART_HELP \
      + "\n\n───────────────\n" + WHICH_CHART_CAMERA_NOTE
    DESCRIPTION = tr(
        "Turn a scan or photo of a target into a colour profile for your "
        "scanner or camera.")

    def __init__(self, runner, settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._scanin = ScaninRunner(runner)
        self._profiler = ProfileBuilder(runner)
        self._ti3: Path | None = None
        self._layout: dict | None = None
        self._pages: list[int] = []
        self._page = 0
        # Per page, a list of "shots" — one or more scans of the same page, each
        # with its own corner placement — averaged before profiling (#98 ask 1c).
        self._shots: dict[int, list[dict]] = {}
        self._shot_idx = 0
        self._jobs: list[dict] = []
        # Standard-target (own IT8 / ColorChecker) mode state.
        self._std_cht: Path | None = None
        self._std_ref: Path | None = None
        self._std_grid = None
        self._convert_tmp: Path | None = None   # scratch for converted references
        self._ref_converted_note = ""           # set when a .cxf/.txt was converted
        light = resolve_mode(settings.get("appearance", "auto")) == "light"
        self._hint = "#4a4a4a" if light else "#b8b8b8"
        self._build_inputs()
        self._run_btn.setObjectName("primary")
        self.setStyleSheet(self.styleSheet() + neutral_controls_qss(SPEC_GREEN))
        self._style_primary_button()
        self._refresh()

    def _style_primary_button(self) -> None:
        c = SPEC_GREEN
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        hover = "#{:02x}{:02x}{:02x}".format(int(r * .86), int(g * .86), int(b * .86))
        light = resolve_mode(self._settings.get("appearance", "auto")) == "light"
        dis_bg, dis_fg = ("#e8e6e1", "#a8a4a0") if light else ("#1e1e1e", "#484848")
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background:{c}; border:1px solid {c}; color:#0a0a0a;"
            f" font-weight:700; }}"
            f"QPushButton:hover {{ background:{hover}; border-color:{hover}; }}"
            f"QPushButton:disabled {{ background:{dis_bg}; border:1px solid {c};"
            f" color:{dis_fg}; }}")

    def _tip(self, title: str, body: str) -> TooltipButton:
        return TooltipButton(title, body, self, min_width=500, color=SPEC_GREEN)

    # ------------------------------------------------------------------ UI
    def _labelled(self, text: str, tip_t: str, tip_b: str):
        h = QHBoxLayout()
        h.addWidget(QLabel(text, self))
        h.addStretch(1)
        h.addWidget(self._tip(tip_t, tip_b), 0, Qt.AlignmentFlag.AlignVCenter)
        return h

    def _hint_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{self._hint}; font-size:12px;")
        return lbl

    def _standard_mode(self) -> bool:
        return self._mode_standard.isChecked()

    def _build_mode_selector(self, form) -> None:
        row = QHBoxLayout()
        self._mode_group = QButtonGroup(self)
        self._mode_chromiq = QRadioButton(tr("A chart I made in ChromIQ"), self)
        self._mode_standard = QRadioButton(
            tr("A standard target I own (IT8, ColorChecker…)"), self)
        self._mode_chromiq.setChecked(True)
        self._mode_group.addButton(self._mode_chromiq)
        self._mode_group.addButton(self._mode_standard)
        row.addWidget(self._mode_chromiq)
        row.addWidget(self._mode_standard)
        row.addStretch(1)
        row.addWidget(self._tip(
            tr("Which source?"),
            tr("Two ways to profile a scanner:\n\n"
            "• A chart I made in ChromIQ — print and measure a chart, then scan "
            "the print. ChromIQ already knows its exact patch colours.\n\n"
            "• A standard target I own — a bought reflective target such as a "
            "Wolf Faust IT8, LaserSoft or X-Rite ColorChecker. Pick its type and "
            "the reference data file that came with your target (.cie / .txt), "
            "then scan it. No printing or measuring needed.")),
            0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(row)
        self._mode_chromiq.toggled.connect(self._on_mode_changed)

    def _build_chromiq_inputs(self, form) -> None:
        self._chromiq_box = QWidget(self)
        v = QVBoxLayout(self._chromiq_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._labelled(
            tr("Measured chart (.ti3):"), tr("Measured chart"),
            tr("The chart you printed and measured, whose printed copy you're "
            "scanning. Pick its .ti3. ChromIQ uses the chart's exact layout + "
            "measured colours to read the scan.")))
        row = QHBoxLayout()
        self._ti3_field = QLineEdit(self)
        self._ti3_field.setReadOnly(True)
        self._ti3_field.setPlaceholderText(tr("Pick the measured chart (.ti3)…"))
        row.addWidget(self._ti3_field, 1)
        b = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        b.clicked.connect(self._pick_chart)
        row.addWidget(b)
        v.addLayout(row)
        self._chart_note = self._hint_label("")
        v.addWidget(self._chart_note)

        # Page selector (multi-page charts only)
        self._page_row = QHBoxLayout()
        self._page_row.addWidget(QLabel(tr("Page:"), self))
        self._page_combo = NoScrollComboBox(self)
        self._page_combo.currentIndexChanged.connect(self._on_page_changed)
        self._page_row.addWidget(self._page_combo)
        self._page_row.addStretch(1)
        self._page_widget = QWidget(self)
        self._page_widget.setLayout(self._page_row)
        self._page_widget.setVisible(False)
        v.addWidget(self._page_widget)
        form.addWidget(self._chromiq_box)

    def _build_standard_inputs(self, form) -> None:
        self._standard_box = QWidget(self)
        v = QVBoxLayout(self._standard_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._labelled(
            tr("Target type:"), tr("Target type"),
            tr("Pick the target you're holding. The list is every standard "
            "scanner target ArgyllCMS knows how to read — Wolf Faust and other "
            "IT8 charts, LaserSoft, the X-Rite ColorCheckers, and more. Choosing "
            "the right one lets ChromIQ lay its reading grid exactly over your "
            "target's patches.\n\n"
            "If your target isn't in the list, choose “Other…” and point ChromIQ "
            "at its own layout file (a .cht that came with the target or from "
            "ArgyllCMS).")))
        trow = QHBoxLayout()
        self._target_combo = NoScrollComboBox(self)
        for name, path in list_standard_targets(self._settings):
            self._target_combo.addItem(name, str(path))
        self._target_combo.addItem(tr("Other… (choose a .cht file)"), "")
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        trow.addWidget(self._target_combo, 1)
        v.addLayout(trow)

        # Custom .cht browse (only when "Other…" is selected).
        self._cht_row = QHBoxLayout()
        self._cht_field = QLineEdit(self)
        self._cht_field.setReadOnly(True)
        self._cht_field.setPlaceholderText(tr("Pick a .cht recognition file…"))
        self._cht_row.addWidget(self._cht_field, 1)
        bc = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        bc.clicked.connect(self._pick_cht)
        self._cht_row.addWidget(bc)
        self._cht_row_w = QWidget(self)
        self._cht_row_w.setLayout(self._cht_row)
        self._cht_row_w.setVisible(False)
        v.addWidget(self._cht_row_w)

        v.addLayout(self._labelled(
            tr("Target reference data (.cie / .txt / .cxf):"), tr("Reference data"),
            tr("The colour data file that came with your physical target — it "
            "lists the true colour of every patch. It's specific to your "
            "target's exact batch, so it can't be bundled; point ChromIQ at your "
            "own copy (the file you downloaded from the maker, or that came on "
            "the disc with the target).\n\n"
            "You don't need to prepare it — ChromIQ takes whatever format your "
            "target came in and converts it for you if needed:\n\n"
            "• Ready to use (used as-is): a .cie, .txt or .ti3 that already lists "
            "XYZ or Lab colour — for example Wolf Faust IT8, HutchColor HCT or "
            "LaserSoft DCPro.\n"
            "• An X-Rite .cxf (for example LaserSoft's ISO 12641-2 targets): "
            "ChromIQ converts it automatically.\n"
            "• A raw or spectral .txt (for example the Christophe Métairie CMP "
            "Digital Target measurements): ChromIQ converts it automatically too.\n\n"
            "Any conversion is written to a temporary folder, so your original "
            "download is never changed.")))
        rrow = QHBoxLayout()
        self._ref_field = QLineEdit(self)
        self._ref_field.setReadOnly(True)
        self._ref_field.setPlaceholderText(tr("Pick the target's reference data…"))
        rrow.addWidget(self._ref_field, 1)
        br = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        br.clicked.connect(self._pick_ref)
        rrow.addWidget(br)
        v.addLayout(rrow)
        self._std_note = self._hint_label("")
        v.addWidget(self._std_note)

        form.addWidget(self._standard_box)
        self._standard_box.setVisible(False)

    def _build_shot_bar(self, form) -> None:
        """Add-a-scan controls + averaging method (shown once a page has ≥2
        scans) — averaging repeated scans of a page cuts scanner noise."""
        row = QHBoxLayout()
        self._shot_combo = NoScrollComboBox(self)
        self._shot_combo.currentIndexChanged.connect(self._on_shot_changed)
        self._shot_combo.setVisible(False)
        row.addWidget(self._shot_combo)
        # Compact height — a per-widget rule beats the app-wide 28px min-height.
        _compact_btn = ("QPushButton { padding: 2px 12px; min-height: 0;"
                        " font-size: 11px; }")
        self._add_shot_btn = QPushButton(tr("＋ Add another scan to average"), self)
        self._add_shot_btn.clicked.connect(self._add_shot)
        self._add_shot_btn.setStyleSheet(_compact_btn)
        row.addWidget(self._add_shot_btn)
        self._remove_shot_btn = QPushButton(tr("Remove this scan"), self)
        self._remove_shot_btn.clicked.connect(self._remove_shot)
        self._remove_shot_btn.setStyleSheet(_compact_btn)
        self._remove_shot_btn.setVisible(False)
        row.addWidget(self._remove_shot_btn)
        row.addStretch(1)
        row.addWidget(self._tip(
            tr("Averaging several scans"),
            tr("Scanning the same sheet more than once and averaging the results "
            "smooths out the random noise every scanner adds, giving a cleaner, "
            "more accurate profile. Two or three scans is usually plenty.\n\n"
            "How to do it: pick your first scan above and place its four corners, "
            "then click “Add another scan to average”, pick the next scan, and "
            "place its corners too. Use the “Scan 1 / Scan 2 …” box to switch "
            "between them. Each scan keeps its own placement, so it's fine if the "
            "sheet shifted a little on the glass between scans.\n\n"
            "When you build, ChromIQ reads every scan, averages each patch, and "
            "profiles from the result. (For a multi-page ChromIQ chart, scans are "
            "averaged separately within each page.)")),
            0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(row)

        self._avg_row = QHBoxLayout()
        self._avg_row.addWidget(QLabel(tr("Combine repeated scans by:"), self))
        self._avg_method = NoScrollComboBox(self)
        self._avg_method.addItem(tr("Mean (simple average)"), "mean")
        self._avg_method.addItem(tr("Geometric mean (robust to an odd scan)"), "geomean")
        self._avg_method.addItem(tr("Trimmed mean (drop highest & lowest)"), "trimmed")
        self._avg_row.addWidget(self._avg_method)
        self._avg_row.addStretch(1)
        self._avg_row.addWidget(self._tip(
            tr("Averaging method"),
            tr("How repeated scans of a page are combined into one reading per "
            "patch:\n\n"
            "• Mean — the plain average. A good default.\n\n"
            "• Geometric mean — multiplies the readings and takes the root; a "
            "single unusually bright or dark scan pulls the result less than the "
            "plain mean. A good choice for scans.\n\n"
            "• Trimmed mean — throws away the highest and lowest reading of each "
            "patch, then averages the rest. Needs at least three scans; best when "
            "one scan might be off.")), 0, Qt.AlignmentFlag.AlignVCenter)
        self._avg_row_w = QWidget(self)
        self._avg_row_w.setLayout(self._avg_row)
        self._avg_row_w.setVisible(False)
        form.addWidget(self._avg_row_w)

    # ------------------------------------------------------------------ shots
    def _page_shots(self, pg: int | None = None) -> list[dict]:
        pg = self._page if pg is None else pg
        return self._shots.setdefault(pg, [{"path": None, "corners": None}])

    def _cur_shot(self) -> dict:
        shots = self._page_shots()
        if self._shot_idx >= len(shots):
            self._shot_idx = 0
        return shots[self._shot_idx]

    def _page_ready(self, pg: int) -> bool:
        return any(s["path"] for s in self._page_shots(pg))

    def _reset_shots(self) -> None:
        self._shots.clear()
        self._shot_idx = 0

    def _sync_shot_view(self) -> None:
        """Show the current shot's scan + placement in the marquee."""
        shot = self._cur_shot()
        scan = shot["path"]
        self._scan_field.setText(str(scan) if scan else "")
        if scan and Path(scan).is_file():
            self._marquee.set_image(QImage(str(scan)))
            if shot["corners"]:
                self._marquee.set_corners(shot["corners"])
        else:
            self._marquee.set_image(QImage())
        self._refresh_shot_bar()
        self._refresh()

    def _refresh_shot_bar(self) -> None:
        shots = self._page_shots()
        self._shot_combo.blockSignals(True)
        self._shot_combo.clear()
        for i in range(len(shots)):
            self._shot_combo.addItem(tr("Scan {n}").format(n=i + 1), i)
        self._shot_combo.setCurrentIndex(min(self._shot_idx, len(shots) - 1))
        self._shot_combo.blockSignals(False)
        multi = len(shots) > 1
        self._shot_combo.setVisible(multi)
        self._remove_shot_btn.setVisible(multi)
        self._avg_row_w.setVisible(multi)

    def _add_shot(self) -> None:
        self._capture_current_corners()
        self._page_shots().append({"path": None, "corners": None})
        self._shot_idx = len(self._page_shots()) - 1
        self._sync_shot_view()

    def _remove_shot(self) -> None:
        shots = self._page_shots()
        if len(shots) <= 1:
            return
        del shots[self._shot_idx]
        self._shot_idx = min(self._shot_idx, len(shots) - 1)
        self._sync_shot_view()

    def _on_shot_changed(self, idx: int) -> None:
        if idx < 0:
            return
        self._capture_current_corners()
        self._shot_idx = idx
        self._sync_shot_view()

    def _build_inputs(self) -> None:
        form = self._content
        self._build_mode_selector(form)
        self._build_chromiq_inputs(form)
        self._build_standard_inputs(form)

        form.addLayout(self._labelled(
            tr("Scan or photo of the target (TIFF):"), tr("Scan or photo"),
            tr("Your capture of the target on the device you want to profile: a "
            "scan from a scanner, or a photo from a camera. Save it as a plain "
            "RGB TIFF, with the device's own colour correction turned off (see "
            "the ⓘ at the top for exactly which settings, for both scanners and "
            "cameras).\n\n"
            "Multi-page ChromIQ charts: switch pages with the Page selector and "
            "load each page's capture. To reduce noise you can also add several "
            "captures of the same sheet and let ChromIQ average them — see “Add "
            "another scan to average” below.")))
        row2 = QHBoxLayout()
        self._scan_field = QLineEdit(self)
        self._scan_field.setReadOnly(True)
        self._scan_field.setPlaceholderText(tr("Pick the scan or photo (TIFF)…"))
        row2.addWidget(self._scan_field, 1)
        self._scan_browse = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        self._scan_browse.clicked.connect(self._pick_scan)
        row2.addWidget(self._scan_browse)
        form.addLayout(row2)

        self._marquee = ScanGridMarquee(self)
        self._marquee.setMinimumHeight(460)
        self._marquee_box = QVBoxLayout()
        self._marquee_box.setContentsMargins(0, 0, 0, 0)
        self._marquee_box.addWidget(self._marquee)
        self._marquee_placeholder = QLabel(
            tr("The grid is open in a separate window."), self)
        self._marquee_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._marquee_placeholder.setMinimumHeight(120)
        self._marquee_placeholder.setVisible(False)
        self._marquee_box.addWidget(self._marquee_placeholder)
        form.addLayout(self._marquee_box)

        ctl = QHBoxLayout()
        self._rotate_btn = QPushButton(tr("⟳ Rotate 90°"), self)
        self._rotate_btn.clicked.connect(self._marquee.rotate_90)
        self._reset_btn = QPushButton(tr("Reset view"), self)
        self._reset_btn.clicked.connect(self._marquee._reset_view)
        self._popout_btn = QPushButton(tr("⤢ Pop out for a bigger view"), self)
        self._popout_btn.clicked.connect(self._toggle_popout)
        for _b in (self._rotate_btn, self._reset_btn, self._popout_btn):
            _b.setStyleSheet(_COMPACT_BTN)
        ctl.addWidget(self._rotate_btn)
        ctl.addWidget(self._reset_btn)
        ctl.addStretch(1)
        ctl.addWidget(self._popout_btn)
        form.addLayout(ctl)

        form.addWidget(self._hint_label(tr(
            "Drag the four corners onto the target's patch area until the green "
            "grid sits on the real patches. ChromIQ then reads each patch and "
            "builds the profile.")))
        form.addWidget(self._hint_label(tr(
            "Scroll to zoom · drag the image to pan · double-click to reset the "
            "view. Use Rotate for a sideways scan, or Pop out for a bigger view.")))

        form.addLayout(self._labelled(
            tr("Patch sample area:"), tr("Patch sample area"),
            tr("How much of each patch scanin reads — the filled green inner "
            "square on the grid above.\n\n"
            "It samples the centre of every patch and ignores the edges, where "
            "ink bleeds, a border shows, or placement is slightly off. 60% is a "
            "safe default. Lower it if your patches are small or the grid isn't "
            "perfectly aligned; raise it for large, cleanly-printed patches to "
            "average over more of each colour.")))
        row_sa = QHBoxLayout()
        self._sample_area = NoScrollSpinBox(self)
        self._sample_area.setRange(20, 100)
        self._sample_area.setValue(60)
        self._sample_area.setSuffix(" %")
        self._sample_area.valueChanged.connect(
            lambda v: self._marquee.set_sample_fraction(v / 100.0))
        row_sa.addWidget(self._sample_area, 1)
        row_sa.addStretch(1)
        form.addLayout(row_sa)

        self._build_shot_bar(form)

        opts = QHBoxLayout()
        self._perspective = QCheckBox(tr("Correct perspective (slightly skewed scan)"), self)
        self._perspective.setChecked(True)
        self._diag = QCheckBox(tr("Save a diagnostic image of what was read"), self)
        opts.addWidget(self._perspective)
        opts.addSpacing(24)
        opts.addWidget(self._diag)
        opts.addStretch(1)
        opts.addWidget(self._tip(
            tr("Reading options"),
            tr("Correct perspective compensates for a slightly skewed scan (keep "
            "it on). The diagnostic image saves a copy of the scan with the "
            "patches ChromIQ read drawn on it, so you can check the alignment "
            "if the profile looks off.")), 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(opts)

        form.addLayout(self._labelled(
            tr("Profile type:"), tr("Profile type"),
            tr("How the scanner profile models colour.\n\n"
            "• Matrix — a small, robust profile (a matrix with per-channel "
            "curves). The most common choice for scanners: forgiving of noise "
            "and few patches, and enough for faithful colour. Recommended.\n\n"
            "• LUT — medium / high quality — a look-up-table profile that can "
            "follow the scanner more closely. Use it when you have a chart with "
            "many patches and clean, repeatable scans; high is finer but needs "
            "the best data or it just fits the noise.")))
        row3 = QHBoxLayout()
        self._ptype = NoScrollComboBox(self)
        # data = (colprof -a algorithm, -q quality). "s" (shaper+matrix) keeps the
        # previous default output exactly under the friendly "Matrix" label.
        self._ptype.addItem(tr("Matrix (recommended)"), ("s", "m"))
        self._ptype.addItem(tr("LUT — medium quality"), ("x", "m"))
        self._ptype.addItem(tr("LUT — high quality"), ("x", "h"))
        row3.addWidget(self._ptype, 1)
        row3.addStretch(1)
        form.addLayout(row3)

    # ------------------------------------------------------------------ chart
    def _pick_chart(self) -> None:
        path = open_file_dialog(self, tr("Choose the measured chart"), _TI3_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._set_chart(Path(path))

    def _set_chart(self, ti3: Path) -> None:
        import json
        self._ti3 = ti3
        self._ti3_field.setText(str(ti3))
        _remember_dir(self._settings, self.TOOL_KEY, ti3.parent)
        self._reset_shots()
        base = _chart_base(ti3)
        channels = base.with_name(base.name + ".channels.json")
        if not has_scanner_geometry(channels):
            self._layout = None
            self._pages = []
            self._chart_note.setText(tr(
                "⚠ Not a layout-engine chart — scanner profiling needs a chart "
                "created with ChromIQ's layout engine."))
            self._refresh()
            return
        self._layout = json.loads(channels.read_text())["layout"]
        if self._layout.get("patches"):                     # engine chart
            self._pages = sorted({int(p.get("page", 0))
                                  for p in self._layout["patches"]})
        else:                                               # printtarg chart
            self._pages = list(range(len(self._layout.get("cht_pages", [1]))))
        # Ensure the .cht/.cie exist (build from the measurement if missing).
        try:
            build_scanin_target_from_paths(channels, ti3, base)
        except ScaninTargetError as exc:
            self._chart_note.setText(f"⚠ {exc}")
            self._layout = None
            self._refresh()
            return
        if self._layout.get("patches"):
            n_patches = len(self._layout["patches"])
        else:
            n_patches = len(self._layout.get("locs") or [])
        self._chart_note.setText((
            tr("✓ Ready — {n} patches on one page.")
            if len(self._pages) == 1 else
            tr("✓ Ready — {n} patches on {p} pages.")
        ).format(n=n_patches, p=len(self._pages)))
        self._page_widget.setVisible(len(self._pages) > 1)
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for pg in self._pages:
            self._page_combo.addItem(tr("Page {n}").format(n=pg + 1), pg)
        self._page_combo.blockSignals(False)
        self._page = self._pages[0] if self._pages else 0
        self._shot_idx = 0
        self._load_page_grid()
        self._refresh()

    def _load_page_grid(self) -> None:
        if self._layout is None:
            return
        pg = self._page
        # Engine charts have exact per-patch rects; printtarg charts carry a
        # captured .cht per page — both render a grid overlay (the .cht is parsed
        # into the fiducial frame just like a standard target).
        patches = [p for p in self._layout.get("patches", [])
                   if int(p.get("page", 0)) == pg]
        if patches:
            self._marquee.set_grid(GridSpec.from_patches(patches))
        else:
            cht_pages = self._layout.get("cht_pages") or []
            self._marquee.set_grid(
                GridSpec.from_cht(cht_pages[pg]) if 0 <= pg < len(cht_pages)
                else GridSpec([]))
        self._sync_shot_view()

    def _on_page_changed(self, idx: int) -> None:
        self._capture_current_corners()
        if 0 <= idx < len(self._pages):
            self._page = self._pages[idx]
            self._shot_idx = 0
            self._load_page_grid()

    def _capture_current_corners(self) -> None:
        if self._marquee.has_placement():
            self._cur_shot()["corners"] = self._marquee.corners_image_px()

    # ------------------------------------------------------------- mode/standard
    def _on_mode_changed(self, _checked: bool = False) -> None:
        std = self._standard_mode()
        self._chromiq_box.setVisible(not std)
        self._standard_box.setVisible(std)
        self._reset_shots()
        self._scan_field.setText("")
        self._marquee.set_image(QImage())
        if std:
            self._pages = [0]
            self._page = 0
            self._page_widget.setVisible(False)
            self._on_target_changed()
        else:
            self._std_grid = None
            if self._layout is not None:
                self._load_page_grid()
            else:
                self._marquee.set_grid(GridSpec([]))
        self._refresh_shot_bar()
        self._refresh()

    def _on_target_changed(self, _idx: int = 0) -> None:
        data = self._target_combo.currentData()
        other = not data
        self._cht_row_w.setVisible(other)
        if other:
            txt = self._cht_field.text()
            self._set_std_target(Path(txt) if txt else None)
        else:
            self._set_std_target(Path(data))

    def _pick_cht(self) -> None:
        path = open_file_dialog(self, tr("Choose a .cht recognition file"),
                                _CHT_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._cht_field.setText(path)
        _remember_dir(self._settings, self.TOOL_KEY, Path(path).parent)
        self._set_std_target(Path(path))

    def _convert_dir(self) -> Path:
        if self._convert_tmp is None:
            import tempfile
            self._convert_tmp = Path(tempfile.mkdtemp(prefix="chromiq-ref-"))
        return self._convert_tmp

    def _pick_ref(self) -> None:
        path = open_file_dialog(self, tr("Choose the target reference data"),
                                _REF_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        p = Path(path)
        _remember_dir(self._settings, self.TOOL_KEY, p.parent)
        from workflow.reference_convert import (
            ReferenceConvertError, ReferenceKind, classify_reference,
            convert_reference)
        self._ref_converted_note = ""
        if classify_reference(p) is ReferenceKind.DIRECT:
            self._std_ref = p
        else:
            # A .cxf or raw/spectral .txt — convert it with Argyll for the user.
            self._std_note.setText(tr("Converting {name} to a reference file…")
                                   .format(name=p.name))
            QApplication.processEvents()
            try:
                self._std_ref = convert_reference(
                    p, self._settings.get("argyll_bin_path", ""), self._convert_dir())
            except ReferenceConvertError as exc:
                self._std_ref = None
                self._ref_field.setText("")
                self._std_note.setText(f"⚠ {exc}")
                self._refresh()
                return
            self._ref_converted_note = tr(
                "Converted “{name}” to a reference ChromIQ can read.").format(name=p.name)
        self._ref_field.setText(str(p))
        self._update_std_note()
        self._refresh()

    def _set_std_target(self, cht: Path | None) -> None:
        self._std_cht = cht
        if cht is None or not cht.is_file():
            self._std_grid = None
            self._marquee.set_grid(GridSpec([]))
        else:
            try:
                self._std_grid = GridSpec.from_cht(cht.read_text(errors="ignore"))
            except OSError:
                self._std_grid = GridSpec([])
            self._marquee.set_grid(self._std_grid)
            # Changing the target only swaps the grid; the loaded scan and its
            # placement stay, so nothing to re-apply here.
        self._update_std_note()
        self._refresh()

    def _update_std_note(self) -> None:
        if self._std_cht is None:
            self._std_note.setText("")
            return
        n = len(self._std_grid.rects) if self._std_grid else 0
        if n == 0:
            self._std_note.setText(tr(
                "⚠ Couldn't read this target's patch grid from the .cht."))
        elif self._std_ref is None:
            self._std_note.setText(tr(
                "✓ {n} patches. Now choose the reference data file that came "
                "with your target.").format(n=n))
        else:
            msg = tr("✓ Ready — {n} patches, reference loaded. Scan the target "
                     "and place the corners on its registration marks.").format(n=n)
            if self._ref_converted_note:
                msg += "  " + self._ref_converted_note
            self._std_note.setText(msg)

    # ------------------------------------------------------------------ scan
    def _toggle_popout(self) -> None:
        """Open the grid in a separate, resizable window for a bigger view — or
        dock it back. The same marquee moves between the two windows, so the
        placement, zoom and rotation are preserved. The pop-out carries its own
        Rotate / Reset controls and a Done button; you build the profile back in
        the main window (placement is kept automatically)."""
        if getattr(self, "_popout", None) is not None:
            self._popout.close()
            return
        self._popout = QDialog(self)
        self._popout.setWindowTitle(tr("Place the grid — bigger view"))
        self._popout.setModal(False)
        v = QVBoxLayout(self._popout)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        self._marquee_box.removeWidget(self._marquee)
        self._marquee.set_wheel_zoom(True)       # plain scroll zooms in the pop-out
        v.addWidget(self._marquee, 1)
        bar = QHBoxLayout()
        rot = QPushButton(tr("⟳ Rotate 90°"), self._popout)
        rot.clicked.connect(self._marquee.rotate_90)
        rst = QPushButton(tr("Reset view"), self._popout)
        rst.clicked.connect(self._marquee._reset_view)
        note = QLabel(tr("Placement is saved automatically — click Done, then "
                         "build the profile in the main window."), self._popout)
        note.setStyleSheet("color:#8a8a8a; font-size:11px;")
        done = QPushButton(tr("Done"), self._popout)
        done.setObjectName("primary")
        done.clicked.connect(self._popout.close)
        for _b in (rot, rst, done):
            _b.setStyleSheet(_COMPACT_BTN)
        bar.addWidget(rot)
        bar.addWidget(rst)
        bar.addStretch(1)
        bar.addWidget(note)
        bar.addStretch(1)
        bar.addWidget(done)
        v.addLayout(bar)
        self._marquee_placeholder.setVisible(True)
        self._popout_btn.setText(tr("⤢ Dock back"))
        self._rotate_btn.setEnabled(False)
        self._reset_btn.setEnabled(False)
        self._popout.resize(1200, 940)
        self._popout.finished.connect(lambda _=0: self._dock_marquee())
        self._popout.show()
        self._popout.raise_()
        self._popout.activateWindow()

    def _dock_marquee(self) -> None:
        pop = getattr(self, "_popout", None)
        if pop is None:
            return
        self._marquee.setParent(None)            # detach from the pop-out layout
        self._marquee.set_wheel_zoom(False)
        self._marquee_box.insertWidget(0, self._marquee)
        self._marquee_placeholder.setVisible(False)
        self._popout_btn.setText(tr("⤢ Pop out for a bigger view"))
        self._rotate_btn.setEnabled(True)
        self._reset_btn.setEnabled(True)
        self._popout = None
        pop.deleteLater()

    def _pick_scan(self) -> None:
        ready = (self._std_cht is not None if self._standard_mode()
                 else self._layout is not None)
        if not ready:
            # Don't fail silently — Knut hit a dead Browse button because his .ti3
            # wasn't a ChromIQ engine chart. Say what to do, in the status box.
            self._log.append(tr(
                "⚠ Choose your target first, then the scan. Under “A chart I made "
                "in ChromIQ”, pick the .ti3 of a chart you built here (it needs its "
                ".channels.json alongside). An older .ti3 from a plain scanin run "
                "won't work — for a bought target, switch to “A standard target I "
                "own” above and load its .cht."))
            return
        path = open_file_dialog(self, tr("Choose the scan"), _SCAN_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._cur_shot()["path"] = Path(path)
        self._scan_field.setText(path)
        _remember_dir(self._settings, self.TOOL_KEY, Path(path).parent)
        self._marquee.set_image(QImage(path))
        if self._cur_shot()["corners"]:
            self._marquee.set_corners(self._cur_shot()["corners"])
        self._refresh_shot_bar()
        self._refresh()

    # ------------------------------------------------------------------ run
    def _can_run(self) -> bool:
        if self._standard_mode():
            return (self._std_cht is not None and self._std_ref is not None
                    and self._std_grid is not None and bool(self._std_grid.rects)
                    and self._page_ready(0))
        return self._layout is not None and bool(self._pages) and all(
            self._page_ready(pg) for pg in self._pages)

    def _files_for_page(self, pg: int, base: Path) -> tuple[Path, Path]:
        """The (.cht, reference) pair for page *pg*: the chosen standard target,
        or the chart's own per-page .cht + .cie."""
        if self._standard_mode():
            return self._std_cht, self._std_ref
        single = len(self._pages) == 1
        cht = (base.with_suffix(".cht") if single
               else base.parent / f"{base.name}_{pg + 1:02d}.cht")
        return cht, base.with_suffix(".cie")

    def _apply_sample_area(self, cht: Path, frac: float, base: Path) -> Path:
        """Write a sibling ``.cht`` whose ``BOX_SHRINK`` samples *frac* of each
        patch (Knut's patch-sample-area control), and hand scanin that copy. The
        bundled/original file is never modified. Full-area needs no change."""
        if frac >= 0.999:
            return cht
        from workflow.scanin_runner import cht_with_sample_area
        try:
            new_text = cht_with_sample_area(cht.read_text(errors="ignore"), frac)
        except OSError:
            return cht
        dst = base.parent / f"{cht.stem}-sample.cht"
        dst.write_text(new_text)
        return dst

    def _execute(self) -> None:
        self._capture_current_corners()
        self._log.clear()
        method = self._avg_method.currentData() or "mean"
        if self._standard_mode():
            pages = [0]
            first = next(s["path"] for s in self._page_shots(0) if s["path"])
            base = first.parent / first.stem
        else:
            pages = self._pages
            base = _chart_base(self._ti3)

        frac = self._sample_area.value() / 100.0
        self._jobs = []
        page_ti3s: list[Path] = []
        for pg in pages:
            cht, cie = self._files_for_page(pg, base)
            cht = self._apply_sample_area(cht, frac, base)
            shots = [s for s in self._page_shots(pg) if s["path"]]
            shot_ti3s: list[Path] = []
            for k, s in enumerate(shots):
                scan = s["path"]
                diag = (scan.with_name(scan.stem + "-diag.tif")
                        if self._diag.isChecked() and k == 0 else None)
                params = ScaninParams(
                    scan, cht, cie, corners=s["corners"],
                    perspective=self._perspective.isChecked(), diag=diag,
                    out_name=f"{base.name}-p{pg + 1}s{k + 1}-scanner.ti3")
                shot_ti3s.append(params.out_ti3)
                self._jobs.append({"kind": "scanin", "params": params,
                                   "label": (tr("Reading scan {k} of page {n}…")
                                             if len(shots) > 1 else
                                             tr("Reading page {n} from the scan…"))
                                   .format(k=k + 1, n=pg + 1)})
            if len(shot_ti3s) > 1:
                avg = base.parent / f"{base.name}-p{pg + 1}-avg.ti3"
                self._jobs.append({"kind": "average", "ti3s": shot_ti3s,
                                   "out": avg, "method": method})
                page_ti3s.append(avg)
            else:
                page_ti3s.append(shot_ti3s[0])
        self._jobs.append({"kind": "colprof", "ti3s": page_ti3s, "base": base})
        self._run_job(0)

    def _run_job(self, i: int) -> None:
        if i >= len(self._jobs):
            return
        job = self._jobs[i]
        if job["kind"] == "scanin":
            self._log.appendPlainText(job["label"])

            def _done(code: int, i=i, job=job) -> None:
                fail = self._scanin.primary_failure()
                if code != 0 or fail is not None or not job["params"].out_ti3.exists():
                    msg = fail[1] if fail else tr("ScanIn couldn't read this page.")
                    self._log.appendPlainText(f"[ERROR] {msg}")
                    self._finish(False)
                    return
                self._run_job(i + 1)

            self._scanin.run(job["params"], on_line=self._log_line, on_finish=_done)
        elif job["kind"] == "average":
            self._log.appendPlainText(
                tr("Averaging {n} scans of this page…").format(n=len(job["ti3s"])))
            try:
                average_scanner_ti3(job["ti3s"], job["out"], method=job["method"])
            except (Ti3AverageError, OSError) as exc:
                self._log.appendPlainText(f"[ERROR] {exc}")
                self._finish(False)
                return
            self._run_job(i + 1)
        else:
            self._build_profile(job["ti3s"], job["base"])

    def _build_profile(self, page_ti3s: list[Path], base: Path) -> None:
        # Combine multi-page reads into one .ti3, then colprof → scanner ICC.
        try:
            combined = self._combine_ti3(page_ti3s, base)
        except OSError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return
        self._log.appendPlainText(tr("Building the scanner profile…"))
        alg, quality = self._ptype.currentData() or ("s", "m")
        params = ProfileParams(
            ti3_path=combined, algorithm=alg, quality=quality,
            description=f"{base.name} scanner", manufacturer="ChromIQ",
            model=f"{base.name} scanner")

        def _done(code: int) -> None:
            icc = combined.with_suffix(".icc")
            if code != 0 or not icc.exists():
                fail = self._profiler.primary_failure()
                self._log.appendPlainText(
                    f"[ERROR] {fail[1] if fail else tr('Building the profile failed — see messages above.')}")
                self._finish(False)
                return
            self._log.appendPlainText(tr("[OK] Scanner profile saved: {p}").format(p=icc))
            self._log.appendPlainText(tr(
                "Install it as your scanner's input profile. Use the diagnostic "
                "image (if you saved one) to check the patches were read correctly."))
            self._finish(True)

        self._profiler.build(params, on_line=self._log_line, on_finish=_done)

    def _combine_ti3(self, page_ti3s: list[Path], base: Path) -> Path:
        """Single page → use it directly; multi-page → concatenate the data rows
        into one scanner ``.ti3`` for colprof (same DEVICE_CLASS/format)."""
        if len(page_ti3s) == 1:
            return page_ti3s[0]
        # "-scanner" so the combined read / built profile can never collide with
        # the chart's own <stem>.ti3 / <stem>.icc (the printer profile).
        merged = base.with_name(base.name + "-scanner.ti3")
        header, rows = None, []
        for tp in page_ti3s:
            text = tp.read_text()
            lines = text.splitlines()
            ds = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA")
            de = next(i for i, l in enumerate(lines) if l.strip() == "END_DATA")
            if header is None:
                header = lines[:ds + 1]
            rows += [l for l in lines[ds + 1:de] if l.strip()]
        # renumber SET count
        out = []
        for l in header:
            if l.strip().startswith("NUMBER_OF_SETS"):
                out.append(f"NUMBER_OF_SETS {len(rows)}")
            else:
                out.append(l)
        out += rows + ["END_DATA", ""]
        merged.write_text("\n".join(out))
        return merged

    def _log_line(self, line: str) -> None:
        text = line.rstrip()
        if text and not text.endswith("%"):
            self._log.appendPlainText(text)
            self._log.ensureCursorVisible()
