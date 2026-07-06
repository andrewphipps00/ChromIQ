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

import re
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

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
# Printer mode reads the chart's device + aim values from its .ti2, so a chart you
# only PRINTED (never measured) is fine — accept either file.
_CHART_FILTER = "Chart you printed (*.ti2 *.ti3);;All files (*)"
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


def _user_profile_dir() -> Path:
    """The colour-profile folder "Install profile" writes to (Nelson):
    the platform's per-user store, or the user's own choice from
    Settings → Paths (Knut #108) — one source of truth in platform_paths."""
    from core.platform_paths import icc_install_dir
    return icc_install_dir()


def _load_scan_qimage(path) -> "QImage":
    """Load a scan for the marquee, robust to real scanner output (#108).

    A plain ``QImage(path)`` silently returns null for images whose decoded
    size exceeds Qt's allocation limit (256 MB — a 16-bit A4 scan at 600 dpi
    is over it), which left the marquee empty so the grid could never be
    aligned. Lift the limit; if Qt still can't decode the format, fall back to
    Pillow and convert to 8-bit RGB (the on-screen preview doesn't need more).
    """
    from PyQt6.QtGui import QImageReader
    reader = QImageReader(str(path))
    reader.setAllocationLimit(0)
    img = reader.read()
    if not img.isNull():
        return img
    try:
        from PIL import Image
        from PIL.ImageQt import ImageQt
        with Image.open(path) as im:
            return QImage(ImageQt(im.convert("RGB"))).copy()
    except Exception:  # noqa: BLE001 — the caller shows the empty-marquee state
        log.warning("could not load scan preview %s (Qt: %s)",
                    path, reader.errorString())
        return QImage()


def _chart_base(ti3: Path) -> Path:
    stem = ti3.stem
    if stem.endswith("-verify"):
        stem = stem[: -len("-verify")]
    return ti3.with_name(stem)


_PROFCHECK_RE = re.compile(
    r"Profile check complete, peak err = ([\d.]+), avg err = ([\d.]+)")


def _plain_id(sid: str) -> str:
    """``H01`` → ``H1``: scanin zero-pads sample IDs on output; the chart's
    ``.ti2`` rows and layout locs don't."""
    m = re.match(r"([A-Za-z]+)0*(\d+)$", sid)
    return (m.group(1) + m.group(2)) if m else sid


def page_ids_from_cht(cht: Path) -> set[str] | None:
    """The (plain) sample IDs of the patches a page's ``.cht`` reads — the
    subset of the chart that one scan can legitimately fill. ``None`` if the
    file can't be parsed."""
    from workflow.cht_parser import ChtParseError, parse_cht
    try:
        geom = parse_cht(cht.read_text(errors="ignore"))
    except (OSError, ChtParseError):
        return None
    return {_plain_id(b.name) for b in geom.patches} or None


def page_reference_agreement(ti3: Path, ti2: Path,
                             ids: set[str] | None = None) -> float | None:
    """Printer mode's misalignment signal (#108): Spearman rank agreement
    between what the scanner measured (through its profile) and the chart's
    aim values, optionally restricted to the *ids* one page fills.

    Replaces the retired ΔE-vs-aims share check, which was structurally wrong
    for real prints: a printer can't REACH the chart's ideal aims (gamut
    compression, paper white), so saturated patches sit ΔE 20–40 away even
    when everything is perfect — Knut's real aligned scans flagged 100 % on
    every page while colprof's own fit was excellent (peak 2.9). Print
    response is monotone, so RANK agreement survives it: his real aligned
    pages measure ≈ 0.95, scrambled reads ≈ 0. One methodology across
    scanner, printer and standard modes, as he asked. ``None`` when the
    files can't be parsed or too few patches match."""
    from workflow.ti3_analysis import parse_ti3
    got = parse_ti3(ti3)
    aim = parse_ti3(ti2)
    loc_of = {_plain_id(s): _plain_id(l.strip('"'))
              for s, l in zip(aim.sample_ids, aim.sample_locs)}
    aim_y = {_plain_id(s): y for s, (_x, y, _z) in zip(aim.sample_ids, aim.xyz)}
    pairs = []
    for sid, (_x, y, _z) in zip(got.sample_ids, got.xyz):
        sid = _plain_id(sid)
        if ids is not None and sid not in ids and loc_of.get(sid) not in ids:
            continue
        a = aim_y.get(sid)
        if a is not None:
            pairs.append((y, a))
    if len(pairs) < 8:
        return None

    def _ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    ra = _ranks([p_[0] for p_ in pairs])
    rb = _ranks([p_[1] for p_ in pairs])
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    den = (sum((a - ma) ** 2 for a in ra) * sum((b - mb) ** 2 for b in rb)) ** 0.5
    return num / den if den else None


def locally_misaligned_groups(read_by_id: dict[str, float],
                              expected_by_id: dict[str, float],
                              z: float = 3.0) -> list[str]:
    """Knut's row/column pattern idea (#108), in the form that survives
    randomised charts: rank both value sets over the whole page (removing
    scanner/printer response to first order), take each patch's rank
    displacement |expected − read|, and flag a whole ROW or COLUMN whose
    mean displacement sits ``z`` standard errors above the page mean — a
    grid edge sitting a full cell off drags its entire line of patches
    onto the neighbours' values while the rest of the page stays put.

    Validated on Knut's own 3-page chart: 0 false alarms in 300 noisy
    aligned trials, 100 % detection of his mid-handle squeeze (top row
    reading the row below). Sub-⅔-of-a-patch blends stay invisible here
    (their values are individually plausible) — the post-build self-check
    covers those. His literal per-row own-pattern matching can't work on a
    randomised chart: 7-patch rows gave a 98.5 % false-alarm rate, because
    randomisation removes the row uniqueness the comparison needs.

    Groups are parsed from the sample IDs (letters = column/strip, digits
    = row); groups need ≥ 4 members and the page ≥ 4 groups to be judged.
    Returns human-readable labels like ``"row 3"`` / ``"column H"``."""
    import statistics
    ids = [i for i in read_by_id if i in expected_by_id]
    if len(ids) < 16:
        return []

    def _ranks(vals: list[float]) -> list[int]:
        order = sorted(range(len(vals)), key=lambda k: vals[k])
        r = [0] * len(vals)
        for pos, k in enumerate(order):
            r[k] = pos
        return r

    er = dict(zip(ids, _ranks([expected_by_id[i] for i in ids])))
    rr = dict(zip(ids, _ranks([read_by_id[i] for i in ids])))
    disp = {i: abs(er[i] - rr[i]) for i in ids}
    mean = statistics.mean(disp.values())
    sd = statistics.pstdev(disp.values()) or 1.0
    rows: dict[str, list[str]] = {}
    cols: dict[str, list[str]] = {}
    for i in ids:
        m = re.match(r"([A-Za-z]+)0*(\d+)$", i)
        if not m:
            continue
        cols.setdefault(m.group(1), []).append(i)
        rows.setdefault(m.group(2), []).append(i)
    flagged: list[str] = []
    for label, groups in ((tr("row {n}"), rows), (tr("column {n}"), cols)):
        if len(groups) < 4:
            continue
        for key, members in groups.items():
            if len(members) < 4:
                continue
            gm = statistics.mean(disp[i] for i in members)
            if gm > mean + z * sd / (len(members) ** 0.5):
                flagged.append(label.format(n=key))
    return flagged


def scan_reference_correlation(ti3: Path) -> float | None:
    """Spearman rank correlation between the scan's RGB luminance and the
    reference Y in a scanner-mode ``.ti3`` (RGB = what the scanner saw, XYZ =
    the chart's known colours). Scanner response is monotone, so an aligned
    read correlates strongly (a real measured ChromIQ chart lands ≈ 0.9; a
    synthetic render ≈ 1.0) even on an unprofiled scanner; a misplaced grid
    scrambles the pairing toward 0 (Knut's flipped pages: 0.00–0.33, #108).
    ``None`` when the file can't be parsed or is too small to judge."""
    from workflow.ti3_analysis import Ti3ParseError, parse_ti3
    try:
        t = parse_ti3(ti3)
    except (OSError, Ti3ParseError, ValueError):
        return None
    if t.rgb is None or len(t.rgb) < 8:
        return None
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in t.rgb]
    y = [v for _x, v, _z in t.xyz]

    def _ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    ra, rb = _ranks(lum), _ranks(y)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    den = (sum((a - ma) ** 2 for a in ra) * sum((b - mb) ** 2 for b in rb)) ** 0.5
    return num / den if den else None


class ScannerProfileDialog(_ToolDialogBase):
    TOOL_KEY    = "scanner_profile"
    TITLE       = tr("Build scanner or camera profile")
    EYEBROW     = tr("MEASURE · SCANNER / CAMERA PROFILE")
    ACCENT      = SPEC_GREEN
    RUN_LABEL   = tr("Build scanner or camera profile")
    BUSY_BAR_IDLE_LABEL = tr("Ready")   # always-visible bar; animates while running
    MIN_WIDTH   = 760
    SCROLLABLE_CONTENT = True    # tall (mode toggle + inputs + marquee + averaging)

    # Prepended OUTSIDE the main tr() key — appending inside would orphan the
    # existing help key and its translations (the WHICH_CHART_HELP lesson).
    HELP = tr(
        "A scanner or camera is never as accurate as a real spectrophotometer "
        "— but it lets you build a genuinely useful printer profile with no "
        "spectro at all, and a fine scanner/camera profile for your device."
    ) + "\n\n" + tr(
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
        "Turn a scan or photo of a target into a colour profile for your scanner "
        "or camera — or, from a scan of a chart you printed, a profile for your "
        "printer (using the scanner as the measuring instrument).")

    def __init__(self, runner, settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._scanin = ScaninRunner(runner)
        self._profiler = ProfileBuilder(runner)
        self._ti3: Path | None = None
        self._layout: dict | None = None
        self._printer_scan_profile: Path | None = None   # scanner ICC for printer mode
        self._chart_measured = False   # loaded chart has a real .ti3 (not just .ti2)
        self._align_warnings: list[str] = []   # per-page misalignment findings
        self._run_diags: list[Path] = []       # diagnostic images this run writes
        self._chart_reject_reason: str | None = None  # why the last pick failed (#101)
        # Bring-your-own-.cht (#105): a printer-mode chart without channels.json
        # waits here for the user to pick printtarg's per-page .cht files.
        self._byo_awaiting = False
        self._byo_base: Path | None = None
        self._byo_ref: Path | None = None
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
        # "Reveal profile" — shown after a successful build so the .icc is easy to
        # find (ChromIQ doesn't auto-install scanner profiles). Hidden until then.
        self._last_profile: Path | None = None
        self._reveal_btn = self._button_box.addButton(
            tr("Reveal profile"), QDialogButtonBox.ButtonRole.ActionRole)
        self._reveal_btn.setToolTip(tr(
            "Open the folder containing the scanner/camera profile just built, so "
            "you can install it as your device's input profile."))
        self._reveal_btn.clicked.connect(self._reveal_profile)
        self._reveal_btn.setVisible(False)
        # "Install profile" — copy the built .icc into the user's colour-profile
        # folder so apps can pick it from their profile lists (Nelson).
        self._install_btn = self._button_box.addButton(
            tr("Install profile"), QDialogButtonBox.ButtonRole.ActionRole)
        self._install_btn.setToolTip(tr(
            "Copy the profile just built into your user colour-profile folder "
            "({dir}), where colour-managed programs look for profiles. Restart "
            "a program to see it in its lists.").format(
                dir=str(_user_profile_dir())))
        self._install_btn.clicked.connect(self._install_profile)
        self._install_btn.setVisible(False)
        self.setStyleSheet(self.styleSheet() + neutral_controls_qss(SPEC_GREEN))
        self._style_primary_button()
        self._refresh()

    def _reveal_profile(self) -> None:
        if self._last_profile is None:
            return
        from core.preset_store import reveal_in_file_manager
        reveal_in_file_manager(self._last_profile.parent)

    def _install_profile(self) -> None:
        if self._last_profile is None:
            return
        import shutil
        dest_dir = _user_profile_dir()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / self._last_profile.name
            shutil.copy2(self._last_profile, dest)
        except OSError as exc:
            self._log.appendPlainText(
                f"[ERROR] {tr('Installing the profile failed: {e}').format(e=exc)}")
            return
        self._log.appendPlainText(
            tr("[OK] Profile installed: {p}").format(p=dest))
        self._log.appendPlainText(tr(
            "Colour-managed programs list it after they restart."))

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
        # Name the choice the radios make — without it the two options read as
        # floating statements (Knut, #108 follow-up).
        row.addWidget(QLabel(tr("Create profile using:"), self))
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
        # The printer-mode switch sits FIRST: it changes the labels and fields
        # below it (".ti3" vs ".ti2", the .cht row, the scanner profile), so it
        # must be seen before them (Knut, #108).
        # --- Printer-profile mode: use the scanner as the measuring instrument ---
        self._printer_cb = QCheckBox(
            tr("Profile my printer from this scan (scanner as the instrument)"), self)
        # Help lives only behind the ⓘ (click to open) — no hover tooltip on the
        # checkbox itself.
        _pr_help = tr(
            "Turn this on to build a profile for your PRINTER from this scan — using "
            "your flat-bed scanner in place of a spectrophotometer — instead of a "
            "profile for the scanner itself.\n\n"
            "How it works: you print one of your own ChromIQ charts, scan the print, "
            "and ChromIQ reads the patches and measures their colour through a "
            "scanner profile you made earlier. That gives colprof what it needs to "
            "build a printer profile — no spectrophotometer required.\n\n"
            "What you need first: a profile for THIS scanner. Build one in the normal "
            "scanner mode from a bought target (an IT8 or LaserSoft sheet). The "
            "printer profile is only as good as that scanner profile, so make a solid "
            "one first — and note the chicken-and-egg: profile the scanner off a "
            "bought target, then use it to profile the printer.\n\n"
            "Honest expectations: a scanner-based printer profile is great for "
            "clearing colour casts and making everyday prints look better, but it "
            "won't match a profile made with a real spectrophotometer. For critical "
            "or proofing work, a spectro is still the way.")
        self._printer_cb.toggled.connect(self._on_printer_toggled)
        # An always-visible ⓘ next to the checkbox opens the help on click.
        _pr_row = QHBoxLayout()
        _pr_row.addWidget(self._printer_cb)
        _pr_row.addStretch(1)
        _pr_row.addWidget(self._tip(tr("Printer profile from a scan"), _pr_help),
                          0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(_pr_row)

        self._printer_box = QWidget(self)
        pv = QVBoxLayout(self._printer_box)
        # Flush with the other rows — the old 22px indent left the label and
        # a shorter field floating right of everything else (Basti, #108).
        pv.setContentsMargins(0, 0, 0, 2)
        # Same labelled-field pattern as the other rows: an always-visible ⓘ that
        # carries the extensive help (a plain hover tooltip left no visible cue).
        pv.addLayout(self._labelled(
            tr("Scanner profile (.icc):"), tr("Scanner profile"),
            tr("The profile for THIS scanner that ChromIQ uses to turn the scanned "
            "colours into real, measured colour — the step that makes the printer "
            "profile trustworthy.\n\n"
            "You built this earlier in the normal scanner mode: scan a bought target "
            "(an IT8 or LaserSoft sheet), press Build, and you get a scanner .icc. "
            "Pick that file here.\n\n"
            "Without it, the scan would be raw scanner colour — carrying the "
            "scanner's own cast — and the printer profile would come out wrong. "
            "That's why it's required for this mode.")))
        prow = QHBoxLayout()
        self._printer_prof_field = QLineEdit(self)
        self._printer_prof_field.setReadOnly(True)
        self._printer_prof_field.setPlaceholderText(
            tr("Pick the scanner profile you built earlier…"))
        prow.addWidget(self._printer_prof_field, 1)
        pb = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        pb.clicked.connect(self._pick_scanner_profile)
        prow.addWidget(pb)
        pv.addLayout(prow)
        self._printer_box.setVisible(False)
        v.addWidget(self._printer_box)

        _chart_row = QHBoxLayout()
        self._chart_label = QLabel(tr("Measured chart (.ti3):"), self)
        _chart_row.addWidget(self._chart_label)
        _chart_row.addStretch(1)
        _chart_row.addWidget(self._tip(
            tr("Which chart to read"),
            tr("Which chart your scan is of.\n\n"
            "• For a scanner or camera profile — pick a chart you have already "
            "MEASURED (its .ti3). ChromIQ compares the chart's known, measured "
            "colours with how your device saw them, and builds the profile from the "
            "difference.\n\n"
            "• For a printer profile (the “Profile my printer from this scan” tick "
            "below) — you can pick a chart you simply PRINTED, even if you never "
            "measured it. Pick its .ti2 — the file ChromIQ wrote when it created the "
            "chart, holding the exact colour values it sent to the printer. This time "
            "the scanner does the measuring, so no spectrophotometer reading is "
            "needed.\n\n"
            "Both files live in the chart's own folder, next to the chart image."),
            ), 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(_chart_row)
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

        # Chart geometry (.cht) — printer mode only (#105). ChromIQ charts carry
        # their geometry in channels.json; a chart made outside ChromIQ (e.g. a
        # manual `printtarg -s` run) instead supplies printtarg's own per-page
        # .cht files here.
        self._byo_row_w = QWidget(self)
        _byo_v = QVBoxLayout(self._byo_row_w)
        _byo_v.setContentsMargins(0, 0, 0, 0)
        _byo_head = QHBoxLayout()
        _byo_head.addWidget(QLabel(tr("Chart geometry (.cht):"), self))
        _byo_head.addStretch(1)
        _byo_head.addWidget(self._tip(
            tr("Chart geometry (.cht)"),
            tr("Where ChromIQ learns the exact position of every patch on the "
               "printed sheet.\n\n"
               "For a chart made in ChromIQ there's nothing to do — the "
               "geometry is stored with the chart (its .channels.json), and "
               "this row just says so.\n\n"
               "For a chart you made outside ChromIQ (for example with "
               "printtarg on the command line), pick the .cht file(s) that "
               "printtarg wrote next to your chart — one per page, e.g. "
               "chart_01.cht … chart_05.cht. Select all pages in one go. "
               "ChromIQ checks that the boxes match the chart's .ti2 exactly, "
               "so a wrong or missing page is caught before anything is "
               "read.")), 0, Qt.AlignmentFlag.AlignVCenter)
        _byo_v.addLayout(_byo_head)
        _byo_row = QHBoxLayout()
        self._byo_field = QLineEdit(self)
        self._byo_field.setReadOnly(True)
        self._byo_field.setPlaceholderText(
            tr("provided by the chart (.channels.json)"))
        _byo_row.addWidget(self._byo_field, 1)
        self._byo_btn = make_browse_button(self, tr("Browse…"),
                                           icon="folder_measure")
        self._byo_btn.clicked.connect(self._pick_byo_cht)
        _byo_row.addWidget(self._byo_btn)
        _byo_v.addLayout(_byo_row)
        self._byo_row_w.setVisible(False)          # printer mode only
        v.addWidget(self._byo_row_w)

        # Page selector (multi-page charts only)
        self._page_row = QHBoxLayout()
        self._page_row.setContentsMargins(0, 0, 0, 0)
        self._page_row.addWidget(QLabel(tr("Page:"), self))
        self._page_combo = NoScrollComboBox(self)
        self._page_combo.currentIndexChanged.connect(self._on_page_changed)
        self._page_row.addWidget(self._page_combo)
        # Every page needs its own capture — say so, and count what's still
        # missing (Knut, #108).
        self._page_hint = self._hint_label("")
        self._page_row.addWidget(self._page_hint)
        self._page_row.addStretch(1)
        self._page_widget = QWidget(self)
        self._page_widget.setLayout(self._page_row)
        self._page_widget.setVisible(False)
        # Added to the shared form in _build_inputs, directly above the scan
        # field — picking a page changes which scan is shown, so the two belong
        # together (Knut, #108).

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
        self._demo_btn = QPushButton(tr("Try with a demo scan"), self)
        self._demo_btn.setStyleSheet(_COMPACT_BTN)
        self._demo_btn.setToolTip(tr(
            "Loads a synthetic practice scan of this target — each patch a flat "
            "colour, drawn from the recognition file — plus its matching reference, "
            "so you can try placing the grid and building a profile with no scanner. "
            "It is NOT a real target: for a real profile, load your own scan and the "
            "reference that came with your physical target instead."))
        self._demo_btn.clicked.connect(self._reveal_target_files)
        trow.addWidget(self._demo_btn)
        v.addLayout(trow)

        # Custom .cht browse (only when "Other…" is selected). Labelled like
        # every other file row, and margin-free so its field lines up with
        # them on both sides (Knut, #108: it sat indented and unlabelled).
        self._cht_row_w = QWidget(self)
        _cht_v = QVBoxLayout(self._cht_row_w)
        _cht_v.setContentsMargins(0, 0, 0, 0)
        _cht_v.addLayout(self._labelled(
            tr("Target layout file (.cht):"), tr("Target layout file"),
            tr("The recognition file that describes where every patch sits on "
               "your target — ArgyllCMS calls it a .cht file. It usually comes "
               "with the target's software, or from ArgyllCMS's ref folder.\n\n"
               "Pick the one made for your exact target type; ChromIQ lays its "
               "reading grid from it.")))
        self._cht_row = QHBoxLayout()
        self._cht_field = QLineEdit(self)
        self._cht_field.setReadOnly(True)
        self._cht_field.setPlaceholderText(tr("Pick a .cht recognition file…"))
        self._cht_row.addWidget(self._cht_field, 1)
        bc = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        bc.clicked.connect(self._pick_cht)
        self._cht_row.addWidget(bc)
        _cht_v.addLayout(self._cht_row)
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
            self._marquee.set_image(_load_scan_qimage(scan))
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
        printer = self._printer_mode()
        # Printer mode reads ONE scan per page (the pages accumulate into a
        # single .ti3) — extra shots were silently ignored, so don't offer to
        # add them there (Knut's question; per-page averaging for printer mode
        # would be its own feature).
        self._add_shot_btn.setVisible(not printer)
        self._shot_combo.setVisible(multi and not printer)
        self._remove_shot_btn.setVisible(multi)
        self._avg_row_w.setVisible(multi and not printer)
        if len(self._pages) > 1:
            done = sum(1 for pg in self._pages
                       if any(sh["path"] for sh in self._page_shots(pg)))
            self._page_hint.setText(
                tr("one scan per page — {k} of {n} picked").format(
                    k=done, n=len(self._pages)))

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

        # Page selector directly above the scan it switches (Knut, #108).
        form.addWidget(self._page_widget)
        form.addLayout(self._labelled(
            tr("Scan or photo of the target (TIFF):"), tr("Scan or photo"),
            tr("Your capture of the target on the device you want to profile: a "
            "scan from a scanner, or a photo from a camera. Save it as a plain "
            "RGB TIFF, with the device's own colour correction turned off — the "
            "exact settings for scanners and cameras are further down in this "
            "note.\n\n"
            "Multi-page ChromIQ charts: switch pages with the Page selector and "
            "load each page's capture. To reduce noise you can also add several "
            "captures of the same sheet and let ChromIQ average them — see “Add "
            "another scan to average” below.")
            + "\n\n───────────────\n" + SCAN_SETUP_HELP
            + "\n\n───────────────\n" + SCANNING_TIPS_HELP
            + "\n\n───────────────\n" + CAMERA_HELP))
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
        self._reset_grid_btn = QPushButton(tr("Reset grid"), self)
        self._reset_grid_btn.setToolTip(tr(
            "Re-centre the reading grid at the size computed from this target — use "
            "it if the grid has drifted off-screen (e.g. after loading an image at a "
            "different resolution)."))
        self._reset_grid_btn.clicked.connect(self._marquee.reset_selection_grid)
        self._popout_btn = QPushButton(tr("⤢ Pop out for a bigger view"), self)
        self._popout_btn.clicked.connect(self._toggle_popout)
        for _b in (self._rotate_btn, self._reset_btn, self._reset_grid_btn, self._popout_btn):
            _b.setStyleSheet(_COMPACT_BTN)
        ctl.addWidget(self._rotate_btn)
        ctl.addWidget(self._reset_btn)
        ctl.addWidget(self._reset_grid_btn)
        ctl.addStretch(1)
        ctl.addWidget(self._popout_btn)
        form.addLayout(ctl)

        form.addWidget(self._hint_label(tr(
            "Drag the four corners onto the target's patch area until the green "
            "grid sits on the real patches. ChromIQ then reads each patch and "
            "builds the profile.")))
        form.addWidget(self._hint_label(tr(
            "Drag inside the grid to move it · drag a corner to reshape it · drag "
            "the background to pan · scroll (or ⌘/Ctrl + scroll) to zoom, also "
            "⌘/Ctrl +/− and ⌘/Ctrl + 0 to reset · double-click resets the view. "
            "Rotate handles a sideways scan; Pop out gives a bigger view.")))

        # Inline label + control, sharing one label column with "Profile
        # type:" / "Profile name:" below (Basti: the control belongs NEXT to
        # its name, not under it, and the three should line up).
        self._sa_label = QLabel(tr("Patch sample area:"), self)
        row_sa = QHBoxLayout()
        row_sa.addWidget(self._sa_label)
        self._sample_area = NoScrollSpinBox(self)
        self._sample_area.setRange(20, 100)
        self._sample_area.setValue(50)
        self._sample_area.setSuffix(" %")
        self._sample_area.setMinimumWidth(110)
        self._sample_area.valueChanged.connect(
            lambda v: self._marquee.set_sample_fraction(v / 100.0))
        row_sa.addWidget(self._sample_area)
        row_sa.addStretch(1)
        row_sa.addWidget(self._tip(
            tr("Patch sample area"),
            tr("How much of each patch ChromIQ reads — shown as the filled green "
            "inner square inside every cell of the grid above.\n\n"
            "It always reads the middle of a patch and leaves the edges out, "
            "because the edges are where ink can bleed, a thin border may show, or "
            "the grid may sit a hair off. Reading only the clean centre keeps the "
            "measured colour honest.\n\n"
            "50% is a safe default. Lower it (a smaller square) if your patches are "
            "small or the grid isn't perfectly aligned, so you stay well clear of "
            "the edges. Raise it (a bigger square) only for large, cleanly-printed "
            "patches with the grid sitting exactly right, to average over more of "
            "each colour for a touch less noise.")), 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(row_sa)

        self._build_shot_bar(form)

        opts = QGridLayout()
        opts.setHorizontalSpacing(24)
        opts.setVerticalSpacing(6)
        self._perspective = QCheckBox(tr("Correct perspective (slightly skewed scan)"), self)
        self._perspective.setChecked(True)
        self._diag = QCheckBox(tr("Save a diagnostic image of what was read"), self)
        opts.addWidget(self._perspective, 0, 0)
        opts.addWidget(self._diag, 0, 1)
        opts.setColumnStretch(2, 1)
        opts.addWidget(self._tip(
            tr("Reading options"),
            tr("How ChromIQ reads the patches from your scan.\n\n"
            "• Correct perspective — leave this on (it's on by default). Almost "
            "every scan or photo is very slightly skewed, and this lets ChromIQ "
            "read the patch area as a gently four-cornered shape instead of "
            "insisting on a perfect rectangle. That way the grid still lands on "
            "the patches even if the sheet wasn't perfectly square to the scanner "
            "or camera. There's no downside to leaving it on — only turn it off if "
            "you're certain the scan is geometrically perfect.\n\n"
            "• Save a diagnostic image — after reading, ChromIQ writes a copy of "
            "your scan with the patches it actually read drawn on top, right next "
            "to the scan file. Open that image to check the grid landed correctly: "
            "each drawn marker should sit squarely on its colour. It's the very "
            "first thing to look at if a profile comes out wrong, and it costs "
            "nothing but a little disk space — so it's worth leaving on while "
            "you're getting your placement right.\n\n"
            "• Use fiducial marks — shown only for standard targets that print "
            "small registration crosses just outside the patch block. Either way "
            "you line the four corners up on the patches themselves — the easy, "
            "always-visible reference. With it off, the reading is placed straight "
            "from those corners; with it on, ChromIQ also draws the crosses and "
            "anchors to them, working out where they are from your corner placement "
            "(so you still just line up the patches). It puts the grid in exactly "
            "the same spot, so turn it on only if you find the marks handy to see. "
            "It hides automatically for ChromIQ-made charts, which print no marks.")),
            0, 3, 2, 1,
            Qt.AlignmentFlag.AlignVCenter)

        self._use_fiducials_cb = QCheckBox(
            tr("Use fiducial marks in the .cht as reference"), self)
        self._use_fiducials_cb.setToolTip(tr(
            "How ChromIQ lines the reading grid up with your scan.\n\n"
            "Either way, you drag the four corners onto the patch area — the block "
            "of colour squares. It's the easiest thing to aim at and it works for "
            "every target, so you never have to hunt for anything smaller.\n\n"
            "Off (default): the grid is placed straight from where you put the four "
            "corners.\n\n"
            "On: ChromIQ also draws the target's fiducial marks — the little "
            "registration crosses printed just outside the patches — and lines the "
            "grid up with those instead. It figures out where the marks are from "
            "the corners you placed, so you still only line up the patches. The grid "
            "ends up in exactly the same place either way, so switch it on only if "
            "you like seeing the marks.\n\n"
            "The box turns itself off (with a quick flash) for targets that don't "
            "have separate fiducial marks — there's nothing extra to show."))
        self._use_fiducials_cb.toggled.connect(self._on_fiducial_toggled)
        opts.addWidget(self._use_fiducials_cb, 1, 0)
        form.addLayout(opts)

        self._pt_label = QLabel(tr("Profile type:"), self)
        row3 = QHBoxLayout()
        row3.addWidget(self._pt_label)
        self._ptype = NoScrollComboBox(self)
        # data = (colprof -a algorithm, -q quality). "s" (shaper+matrix) keeps the
        # previous default output exactly under the friendly "Matrix" label.
        self._ptype.addItem(tr("Matrix (recommended)"), ("s", "m"))
        self._ptype.addItem(tr("LUT — medium quality"), ("x", "m"))
        self._ptype.addItem(tr("LUT — high quality"), ("x", "h"))
        row3.addWidget(self._ptype, 1)
        row3.addWidget(self._tip(
            tr("Profile type"),
            tr("How the scanner profile models colour.\n\n"
            "• Matrix — a small, robust profile (a matrix with per-channel "
            "curves). The most common choice for scanners: forgiving of noise "
            "and few patches, and enough for faithful colour. Recommended.\n\n"
            "• LUT — medium / high quality — a look-up-table profile that can "
            "follow the scanner more closely. Use it when you have a chart with "
            "many patches and clean, repeatable scans; high is finer but needs "
            "the best data or it just fits the noise.")), 0,
            Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(row3)
        # Optional profile name (Nelson): without it the .icc inherits the
        # chart / target scan's name, which reads like a paper profile — let the
        # user call it e.g. "Epson ET-8550 scanner" instead.
        row4 = QHBoxLayout()
        self._pn_label = QLabel(tr("Profile name:"), self)
        row4.addWidget(self._pn_label)
        self._prof_name = QLineEdit(self)
        self._prof_name.setPlaceholderText(
            tr("optional — otherwise named after the chart / target"))
        row4.addWidget(self._prof_name, 1)
        row4.addWidget(self._tip(
            tr("Profile name"),
            tr("The name of the finished profile — used for the .icc file "
               "itself and for the name colour-managed programs (Photoshop, "
               "your scanning software, the printer driver…) show in their "
               "profile lists.\n\n"
               "You can leave this empty: the profile is then named after the "
               "chart or target scan it was built from. That works, but a name "
               "like “Moab_Satin_240gsm” is easy to mistake for a paper or "
               "printer profile later. A name that says what the profile "
               "actually is — “Epson ET-8550 scanner”, or “Brother MFC-9460 "
               "plain paper” for a printer profile — keeps your profile list "
               "understandable years from now.\n\n"
               "The name applies to whichever profile this window builds: the "
               "scanner or camera profile, or, when “Profile my printer from "
               "this scan” is ticked, the printer profile.")))
        form.addLayout(row4)
        # One shared label column → the spinbox, combo and name field all
        # start at the same x (Basti, #108 follow-up).
        _labels = (self._sa_label, self._pt_label, self._pn_label)
        _w = max(l.sizeHint().width() for l in _labels) + 8
        for _l in _labels:
            _l.setFixedWidth(_w)

    def _custom_profile_stem(self) -> str | None:
        """The user-chosen profile name as a filesystem-safe stem, or None."""
        import re as _re
        raw = self._prof_name.text().strip()
        if not raw:
            return None
        return _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(". ") or None

    def _apply_profile_name(self, ti3: Path) -> tuple[Path, str | None]:
        """Honour the optional profile name (Nelson): colprof names the .icc
        after its .ti3, so copy *ti3* to ``<name>.ti3`` and return it together
        with the description to embed. Returns (*ti3*, None) when no name was
        given — the caller keeps its defaults."""
        stem = self._custom_profile_stem()
        if not stem:
            return ti3, None
        named = ti3.with_name(stem + ".ti3")
        if named != ti3:
            import shutil
            try:
                shutil.copy2(ti3, named)
            except OSError as exc:
                self._log.appendPlainText(
                    f"[WARN] {tr('Could not apply the profile name: {e}').format(e=exc)}")
                return ti3, self._prof_name.text().strip()
            ti3 = named
        return ti3, self._prof_name.text().strip()

    # ------------------------------------------------------------------ chart
    def _reject_chart(self, reason: str) -> None:
        """Reject the picked chart with *reason* shown in BOTH the chart note
        and the status log — Knut picked a chart, missed the small note, and
        only hit a generic dead-Browse message much later (#101)."""
        self._layout = None
        self._pages = []
        self._chart_reject_reason = reason
        self._chart_note.setText(reason)
        self._log.appendPlainText(reason)
        self._refresh()

    def _pick_chart(self) -> None:
        if self._printer_mode():
            title, flt = tr("Choose the chart you printed"), _CHART_FILTER
        else:
            title, flt = tr("Choose the measured chart"), _TI3_FILTER
        path = open_file_dialog(self, title, flt,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._set_chart(Path(path))

    def _set_chart(self, picked: Path) -> None:
        import json
        # Honour the file the user actually picked (#101): auto-preferring a
        # sibling .ti3 silently swapped Knut's explicit .ti2 pick for an
        # unrelated scanner .ti3 that happened to share the folder — the field
        # then showed a file he never chose. Only when the picked file itself
        # isn't a chart table (e.g. a "-verify" pick) fall back to the measured
        # .ti3, then the .ti2 (aim values) — a chart you only PRINTED still
        # works for a printer profile; both carry loc + RGB + XYZ.
        base = _chart_base(picked)
        ti3, ti2 = base.with_suffix(".ti3"), base.with_suffix(".ti2")
        if (picked.suffix.lower() in (".ti2", ".ti3") and picked.is_file()
                and picked.stem == base.name):     # not a "-verify" alias pick
            ref = picked
        else:
            ref = ti3 if ti3.is_file() else ti2
        self._chart_measured = ref.suffix.lower() == ".ti3" and ref.is_file()
        self._ti3 = ref
        self._ti3_field.setText(str(ref))
        _remember_dir(self._settings, self.TOOL_KEY, picked.parent)
        self._reset_shots()
        self._reset_byo_cht()               # a fresh chart pick starts over (#105)
        channels = base.with_name(base.name + ".channels.json")
        if not has_scanner_geometry(channels):
            # Recovery (#101): the sidecar may not share the picked file's stem
            # (e.g. only some files were copied out of a run folder, or renamed).
            # If the folder holds exactly one usable .channels.json, take it —
            # a mispairing is still caught later by the loc-alignment check.
            cands = [c for c in sorted(base.parent.glob("*.channels.json"))
                     if has_scanner_geometry(c)]
            if len(cands) == 1:
                channels = cands[0]
            elif self._printer_mode() and ref.is_file():
                # No sidecar at all, but printer mode can take the chart's own
                # printtarg .cht page files instead (#105, Knut's manual charts).
                self._await_byo_cht(base, ref)
                return
            else:
                self._reject_chart(tr(
                    "⚠ No chart layout found: the chart “{name}” has no "
                    ".channels.json with usable geometry next to it. ChromIQ "
                    "writes that sidecar when it creates a chart — pick the "
                    "chart inside its original folder (or copy the "
                    ".channels.json along with it).").format(name=base.name))
                return
        if not ref.is_file():
            self._reject_chart(tr(
                "⚠ This chart has no .ti3 or .ti2 next to it, so ChromIQ can't read "
                "its patch values."))
            return
        self._layout = json.loads(channels.read_text())["layout"]
        # A stored printtarg capture whose page count differs from the printed
        # chart is wrong by construction (printtarg -s re-lays some chart
        # types out, e.g. ColorMunki double density) — reject it honestly
        # instead of showing a grid that can never match the scan (#108).
        if self._layout.get("engine") == "printtarg":
            stored = len(self._layout.get("cht_pages") or [])
            tifs = sorted(base.parent.glob(f"{base.name}_*.tif"))
            printed = len(tifs) or (1 if base.with_suffix(".tif").is_file() else 0)
            if stored and printed and stored != printed:
                self._layout = None
                self._reject_chart(tr(
                    "⚠ This chart's stored scan geometry doesn't match the "
                    "chart: {g} recognition page(s) for {t} printed page(s). "
                    "The chart fills its pages right to the limit, and "
                    "printtarg needs slightly more room in scan mode. Reduce "
                    "the Patch Size Scale a little (e.g. 0.90 instead of "
                    "0.93) and regenerate — or use a ChromIQ layout-engine "
                    "chart.").format(g=stored, t=printed))
                return
        # Build the .cht/.cie from the reference (measured .ti3, or .ti2 aim values).
        try:
            build_scanin_target_from_paths(channels, ref, base)
        except ScaninTargetError as exc:
            self._layout = None
            self._reject_chart(f"⚠ {exc}")
            return
        self._chart_geometry_ready()

    def _chart_geometry_ready(self) -> None:
        """Shared success tail of a chart pick: the layout is set and its
        .cht/.cie were written — announce it, fill the page selector and show
        the grid. Used by the channels.json path and the BYO-.cht path (#105)."""
        if self._layout.get("patches"):                     # engine chart
            self._pages = sorted({int(p.get("page", 0))
                                  for p in self._layout["patches"]})
            n_patches = len(self._layout["patches"])
        else:                                               # printtarg chart
            self._pages = list(range(len(self._layout.get("cht_pages", [1]))))
            n_patches = len(self._layout.get("locs") or [])
        self._chart_reject_reason = None             # pick accepted (#101)
        if not self._chart_measured:
            if self._printer_mode():
                # Printer mode is already on — point at the next step instead
                # of asking to tick the checkbox again (#105).
                self._chart_note.setText((
                    tr("✓ {n} patches on one page — pick the scan of the "
                       "printed chart below.")
                    if len(self._pages) == 1 else
                    tr("✓ {n} patches on {p} pages — pick each page's scan "
                       "below.")).format(n=n_patches, p=len(self._pages)))
            else:
                self._chart_note.setText(tr(
                    "✓ {n} patches. This chart hasn't been measured — tick “Profile my "
                    "printer from this scan” below to build a printer profile from it "
                    "(no spectrophotometer needed).").format(n=n_patches))
        else:
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

    # ---------------------------------------------- bring-your-own .cht (#105)
    def _reset_byo_cht(self) -> None:
        self._byo_awaiting = False
        self._byo_base = None
        self._byo_ref = None
        self._byo_field.clear()
        self._byo_field.setPlaceholderText(
            tr("provided by the chart (.channels.json)"))

    def _await_byo_cht(self, base: Path, ref: Path) -> None:
        """Printer mode, chart without channels.json: hold the pick and ask for
        printtarg's per-page .cht files instead of rejecting (#105)."""
        self._layout = None
        self._pages = []
        self._byo_awaiting = True
        self._byo_base = base
        self._byo_ref = ref
        msg = tr(
            "This chart wasn't made by ChromIQ (no .channels.json) — that's "
            "fine for a printer profile: pick the .cht page file(s) printtarg "
            "wrote for it under “Chart geometry (.cht)” below.")
        self._chart_reject_reason = "⚠ " + msg
        self._chart_note.setText("⚠ " + msg)
        self._log.appendPlainText("⚠ " + msg)
        self._byo_field.setPlaceholderText(
            tr("pick the chart's .cht page file(s)…"))
        self._refresh()

    def _pick_byo_cht(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        if not self._byo_awaiting or self._byo_base is None:
            self._log.appendPlainText(tr(
                "⚠ This chart already carries its geometry (.channels.json) — "
                "there is nothing to pick. The .cht row is only used for "
                "charts made outside ChromIQ."))
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Pick the chart's .cht page file(s)"),
            str(self._byo_base.parent),
            tr("Chart geometry (*.cht);;All files (*)"))
        if not paths:
            return
        cht_paths = sorted(Path(p) for p in paths)   # printtarg numbers _01…_NN
        from workflow.scanin_target import build_scanin_target_from_cht_files
        try:
            layout, res = build_scanin_target_from_cht_files(
                cht_paths, self._byo_ref, self._byo_base)
        except ScaninTargetError as exc:
            self._byo_field.clear()
            self._reject_chart(f"⚠ {exc}")
            # Stay in the awaiting state so another pick can succeed.
            self._byo_awaiting = True
            return
        self._byo_field.setText(", ".join(p.name for p in cht_paths))
        self._byo_awaiting = False
        self._layout = layout
        self._log.appendPlainText(tr(
            "✓ Chart geometry loaded from {n} .cht file(s) — {p} patches "
            "verified against the chart.").format(n=len(cht_paths),
                                                  p=res.n_patches))
        self._chart_geometry_ready()

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

    # -------------------------------------------------- remembered placement
    def _target_key(self) -> str | None:
        """A stable key for the current target, so its last grid placement can be
        restored next session. Standard targets key on the .cht stem; ChromIQ
        charts key on the chart stem."""
        if self._standard_mode():
            return f"std:{self._std_cht.stem}" if self._std_cht else None
        return f"chart:{self._ti3.stem}" if self._ti3 else None

    def _save_placement(self) -> None:
        """Store the current grid as fractions of the image size, keyed by target,
        so it can be reused on a future scan of the same target at any resolution."""
        key = self._target_key()
        w, h = self._marquee.image_size()
        if not key or not w or not h or not self._marquee.has_placement():
            return
        norm = [[x / w, y / h] for x, y in self._marquee.corners_image_px()]
        places = dict(self._settings.get("scanin_grid_placements", {}) or {})
        places[key] = norm
        self._settings.set("scanin_grid_placements", places)

    def _restore_placement(self) -> bool:
        """Apply the remembered placement for this target to the loaded image
        (scaled to its size). Returns True if one was applied."""
        key = self._target_key()
        w, h = self._marquee.image_size()
        if not key or not w or not h:
            return False
        norm = (self._settings.get("scanin_grid_placements", {}) or {}).get(key)
        if not norm or len(norm) != 4:
            return False
        self._marquee.set_corners([(fx * w, fy * h) for fx, fy in norm])
        return True

    # ------------------------------------------------------------- mode/standard
    def _on_mode_changed(self, _checked: bool = False) -> None:
        std = self._standard_mode()
        self._chromiq_box.setVisible(not std)
        self._standard_box.setVisible(std)
        # ChromIQ charts carry no fiducial marks, so hide the option and force it
        # off — the same align-the-patches / derive-the-F process is used either
        # way. (Shows again automatically for standard targets that have marks.)
        self._use_fiducials_cb.setVisible(std)
        if not std:
            self._use_fiducials_cb.setChecked(False)
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
        # Printer mode is only meaningful with a ChromIQ chart (needs its .ti2).
        self._printer_cb.setVisible(not std)
        if std:
            self._printer_cb.setChecked(False)
        self._refresh_shot_bar()
        self._refresh()

    def _printer_mode(self) -> bool:
        """True when building a PRINTER profile from this scan (scanner as the
        instrument) — only offered for a ChromIQ chart, which carries the .ti2."""
        return not self._standard_mode() and self._printer_cb.isChecked()

    def _on_printer_toggled(self, checked: bool) -> None:
        self._printer_box.setVisible(checked)
        # The Chart-geometry (.cht) row only matters in printer mode (#105).
        self._byo_row_w.setVisible(checked)
        self._refresh_shot_bar()   # averaging affordances hide in printer mode
        # In printer mode the chart's .ti2 is enough (no measurement needed), so the
        # picker asks for the chart you printed rather than a measured .ti3.
        self._chart_label.setText(
            tr("Chart you printed (.ti2):") if checked else tr("Measured chart (.ti3):"))
        self._ti3_field.setPlaceholderText(
            tr("Pick the chart you printed (.ti2)…") if checked else
            tr("Pick the measured chart (.ti3)…"))
        # Ticking printer mode AFTER a sidecar-less chart was picked (and thus
        # rejected) re-evaluates it, so the BYO-.cht offer appears without
        # re-picking the chart (#105). Nothing to lose: the layout is unset.
        if (checked and self._layout is None and not self._byo_awaiting
                and self._ti3 is not None and Path(self._ti3).is_file()):
            self._set_chart(Path(self._ti3))
        # The field shows the file the MODE actually consumes: printer mode
        # reads the chart's .ti2, scanner mode its measured .ti3 — a
        # pre-filled .ti3 in printer mode read like the wrong input (Knut).
        elif self._ti3 is not None:
            want = Path(self._ti3).with_suffix(".ti2" if checked else ".ti3")
            if want.is_file() and want != Path(self._ti3):
                self._set_chart(want)
        # Leave the profile-type selector enabled so its tooltip stays readable
        # (Qt hides tooltips on disabled widgets). Its *quality* is honoured for the
        # printer profile; the Matrix/LUT choice isn't (a printer profile is always
        # a LUT) — see _build_printer_profile.
        self._run_btn.setText(
            tr("Build printer profile") if checked else
            tr("Build scanner or camera profile"))
        self._refresh()

    def _pick_scanner_profile(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        start = str(self._printer_scan_profile.parent) if self._printer_scan_profile \
            else self._settings.get("tools_last_dir_scanner_profile", "")
        p, _ = QFileDialog.getOpenFileName(
            self, tr("Pick the scanner profile (.icc) you built earlier"),
            start, tr("ICC profiles (*.icc *.icm)"))
        if p:
            self._printer_scan_profile = Path(p)
            self._printer_prof_field.setText(p)
            self._refresh()

    def _on_target_changed(self, _idx: int = 0) -> None:
        data = self._target_combo.currentData()
        other = not data
        self._cht_row_w.setVisible(other)
        self._demo_btn.setEnabled(not other)
        if other:
            txt = self._cht_field.text()
            self._set_std_target(Path(txt) if txt else None)
        else:
            self._set_std_target(Path(data))

    def _reveal_target_files(self) -> None:
        """Generate a synthetic demo scan + matching reference from the selected
        target's ``.cht`` and load them into the dialog, so the grid (and the whole
        read → build) can be tried with no hardware. It is a practice image (each
        patch a flat colour), NOT a real target scan — the same known-colour pair
        the automated tests use to confirm scanin reads correctly."""
        cht = self._target_combo.currentData()
        if not cht:
            self._log.appendPlainText(tr("Pick a bundled target above first."))
            return
        from workflow.standard_targets import make_test_scan
        out = Path.home() / "ChromIQ" / "scanner-test-targets"
        try:
            tif, ref = make_test_scan(Path(cht), out)
        except Exception as exc:  # noqa: BLE001
            self._log.appendPlainText(
                tr("Couldn't prepare the demo scan: {e}").format(e=exc))
            return
        self._cur_shot()["path"] = tif                 # load the demo scan
        self._scan_field.setText(str(tif))
        self._marquee.set_image(_load_scan_qimage(tif))
        if self._cur_shot()["corners"]:
            self._marquee.set_corners(self._cur_shot()["corners"])
        elif self._restore_placement():
            self._cur_shot()["corners"] = self._marquee.corners_image_px()
        self._std_ref = ref                            # load its matching reference
        self._ref_field.setText(str(ref))
        self._ref_converted_note = ""
        self._update_std_note()
        self._refresh_shot_bar()
        self._refresh()
        self._log.appendPlainText(tr(
            "Loaded a demo scan + reference to practise on. This is a synthetic "
            "image ChromIQ drew from the target's recognition file — each patch a "
            "flat colour — NOT a real target. Place the grid and Build to see the "
            "read work end-to-end.\n"
            "For a real profile, load your own scan (.tif) and the reference "
            "(.cie) that came with your physical target instead. The bundled "
            "recognition file is:\n  {cht}").format(cht=cht))

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
        if not self._fiducials_available() and self._use_fiducials_cb.isChecked():
            self._use_fiducials_cb.setChecked(False)   # new target has no fiducials
        if cht is None or not cht.is_file():
            self._std_grid = None
            self._marquee.set_grid(GridSpec([]))
        else:
            self._rebuild_std_grid()
            # Changing the target only swaps the grid; the loaded scan and its
            # placement stay, so nothing to re-apply here.
        self._update_std_note()
        self._refresh()

    def _rebuild_std_grid(self) -> None:
        """(Re)build the standard-target grid from the current .cht. The marquee
        **always** frames the patch block (the reliable, always-visible reference);
        the "Use fiducial marks" option no longer changes the grid — it only
        changes how scanin's ``-F`` is derived from this one alignment
        (:meth:`_scanin_corners`). Keeps the current scan's placement."""
        if not self._standard_mode() or self._std_cht is None:
            return
        self._capture_current_corners()
        try:
            self._std_grid = GridSpec.from_cht(self._std_cht.read_text(errors="ignore"))
        except OSError:
            self._std_grid = GridSpec([])
        self._marquee.set_grid(self._std_grid)
        self._marquee.set_show_fiducials(self._use_fiducials_cb.isChecked())
        if self._cur_shot()["corners"]:               # set_grid re-seeds — restore
            self._marquee.set_corners(self._cur_shot()["corners"])

    def _fiducials_available(self) -> bool:
        if not self._standard_mode() or self._std_cht is None:
            return False
        from ui.scan_grid_marquee import cht_has_fiducials
        try:
            return cht_has_fiducials(self._std_cht.read_text(errors="ignore"))
        except OSError:
            return False

    def _on_fiducial_toggled(self, checked: bool) -> None:
        if checked and not self._fiducials_available():
            self._blink_widget(self._use_fiducials_cb)   # "not available" feedback
            self._use_fiducials_cb.blockSignals(True)
            self._use_fiducials_cb.setChecked(False)
            self._use_fiducials_cb.blockSignals(False)
            return
        # The marquee stays on the patch grid; toggling only draws the fiducial
        # frame and changes how the scanin -F is derived at build time.
        self._marquee.set_show_fiducials(checked)
        self._update_std_note()

    def _reframe_marquee(self, to_fiducial: bool) -> None:
        """Grow the marquee out to the fiducial marks (or back to the patch area),
        keeping the patches on the same image spot — so ticking the box visibly
        adds the fiducial band around the patch grid."""
        if self._std_cht is None:
            return
        from ui.scan_grid_marquee import fiducial_frame
        from workflow.cht_parser import ChtParseError, parse_cht
        txt = self._std_cht.read_text(errors="ignore")
        fr = fiducial_frame(txt)
        if fr is None:
            return
        try:
            g = parse_cht(txt)
        except ChtParseError:
            return
        xs = [b.x1 for b in g.patches] + [b.x2 for b in g.patches]
        ys = [b.y1 for b in g.patches] + [b.y2 for b in g.patches]
        px0, px1, py0, py1 = min(xs), max(xs), min(ys), max(ys)
        fx0, fx1, fy0, fy1 = fr
        self._marquee.reframe(px0 - fx0, py0 - fy0, fx1 - px1, fy1 - py1,
                              px1 - px0, py1 - py0, to_fiducial)

    def _blink_widget(self, w) -> None:
        """Flash a widget red twice to say "can't enable that" (Knut)."""
        from PyQt6.QtCore import QTimer
        orig = w.styleSheet()
        seq = ["QCheckBox{color:#d9534f;}", orig] * 2
        def step(i: int = 0) -> None:
            if i < len(seq):
                w.setStyleSheet(seq[i])
                QTimer.singleShot(200, lambda: step(i + 1))
        step()

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
        done.clicked.connect(self._popout.close)
        for _b in (rot, rst):
            _b.setStyleSheet(_COMPACT_BTN)
        # The pop-out is its own window, so it doesn't inherit the dialog's green
        # accent — the global "primary" style would make Done blue. Paint it green
        # (the scanner/measure family colour) explicitly.
        done.setStyleSheet(
            "QPushButton {"
            f"  background: {SPEC_GREEN}; color: #08130e; border: none;"
            "   border-radius: 6px; padding: 3px 22px; min-height: 0;"
            "   font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #6fe0b6; }"
            "QPushButton:pressed { background: #45b98d; }")
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
        self._reset_grid_btn.setEnabled(False)
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
        self._reset_grid_btn.setEnabled(True)
        self._marquee._reset_view()              # main view returns fully zoomed-out
        self._popout = None
        pop.deleteLater()

    def _pick_scan(self) -> None:
        ready = (self._std_cht is not None if self._standard_mode()
                 else self._layout is not None)
        if not ready:
            # Don't fail silently — Knut hit a dead Browse button because his .ti3
            # wasn't a ChromIQ engine chart. Say what to do, in the status box —
            # matching the active mode (the old text demanded a .ti3 even in
            # printer mode, where the .ti2 is the right file, #101) and repeating
            # why a picked chart was rejected instead of a generic hint.
            if self._byo_awaiting and self._printer_mode():
                self._log.appendPlainText(tr(
                    "⚠ Pick the chart's .cht page file(s) first — the “Chart "
                    "geometry (.cht)” row above — then choose the scan."))
            elif self._chart_reject_reason and not self._standard_mode():
                self._log.appendPlainText(tr(
                    "⚠ The chart you picked can't be used — fix that first, then "
                    "choose the scan. The problem was:"))
                self._log.appendPlainText(self._chart_reject_reason)
            elif self._standard_mode():
                self._log.appendPlainText(tr(
                    "⚠ Choose your target first, then the scan: load the "
                    "target's .cht reference file above."))
            elif self._printer_mode():
                self._log.appendPlainText(tr(
                    "⚠ Choose your chart first, then the scan: pick the .ti2 of "
                    "the chart you printed (ChromIQ wrote it, with its "
                    ".channels.json, into the chart's folder when you created "
                    "the chart)."))
            else:
                self._log.appendPlainText(tr(
                    "⚠ Choose your target first, then the scan. Under “A chart I "
                    "made in ChromIQ”, pick the .ti3 of a chart you built here (it "
                    "needs its .channels.json alongside). An older .ti3 from a "
                    "plain scanin run won't work — for a bought target, switch to "
                    "“A standard target I own” above and load its .cht."))
            return
        path = open_file_dialog(self, tr("Choose the scan"), _SCAN_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._cur_shot()["path"] = Path(path)
        self._scan_field.setText(path)
        _remember_dir(self._settings, self.TOOL_KEY, Path(path).parent)
        img = _load_scan_qimage(path)
        self._marquee.set_image(img)
        if img.isNull():
            # Never leave the user staring at an empty marquee without a word —
            # without a preview the grid can't be aligned (#108).
            self._log.appendPlainText(tr(
                "⚠ This scan couldn't be decoded for the preview, so the grid "
                "can't be aligned on it. Re-save the scan as an 8-bit TIFF (or "
                "PNG) and pick it again."))
        if self._cur_shot()["corners"]:
            self._marquee.set_corners(self._cur_shot()["corners"])
        elif self._restore_placement():          # reuse last session's placement
            self._cur_shot()["corners"] = self._marquee.corners_image_px()
        self._refresh_shot_bar()
        self._refresh()

    # ------------------------------------------------------------------ run
    def _can_run(self) -> bool:
        if self._standard_mode():
            return (self._std_cht is not None and self._std_ref is not None
                    and self._std_grid is not None and bool(self._std_grid.rects)
                    and self._page_ready(0))
        if self._printer_mode() and self._printer_scan_profile is None:
            return False                         # printer mode needs a scanner ICC
        if not self._printer_mode() and not self._chart_measured:
            return False                         # a scanner profile needs a real .ti3
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

    def _prepare_scanin_cht(self, orig_cht: Path, corners, frac: float,
                            base: Path, tag: str) -> Path:
        """The cht scanin reads for one scan: (1) reposition the boxes onto
        rectarg's integer edges for this scan's patch-area pixel size, so the
        interior lines up with a rounded rectarg image the same way the on-screen
        grid does; (2) fiducial-frame; (3) sample-area. One shared calculation for
        the marquee and scanin. Falls back to the original layout if the boxes
        aren't a uniform grid or the scan is too small to round."""
        cht = orig_cht
        try:
            text = orig_cht.read_text(errors="ignore")
        except OSError:
            text = None
        if text is not None and corners and len(corners) == 4:
            import math
            wpx = math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
            hpx = math.hypot(corners[3][0] - corners[0][0], corners[3][1] - corners[0][1])
            from ui.scan_grid_marquee import rectarg_align_cht
            aligned = rectarg_align_cht(text, wpx, hpx)
            if aligned != text:
                cht = base.parent / f"{orig_cht.stem}-{tag}-aligned.cht"
                cht.write_text(aligned)
        cht = self._apply_fiducial_frame(cht, base)
        return self._apply_sample_area(cht, frac, base)

    def _scanin_corners(self, corners, orig_cht: Path):
        """Turn the patch-grid-aligned marquee quad into scanin's ``-F`` corners.
        With "Use fiducial marks" ON (standard mode) extrapolate the quad out to
        the fiducial frame (matching the on-disk ``F`` line kept by
        :meth:`_apply_fiducial_frame`); OFF returns the quad unchanged (its ``F``
        was rewritten to the patch bbox). One alignment, two consistent frames —
        so ON and OFF land the grid identically. *orig_cht* is read only in ON."""
        if not (corners and self._standard_mode()
                and self._use_fiducials_cb.isChecked()):
            return corners
        from ui.scan_grid_marquee import extrapolate_to_fiducials
        try:
            text = orig_cht.read_text(errors="ignore")
        except OSError:
            return corners
        return extrapolate_to_fiducials(corners, text) or corners

    def _apply_fiducial_frame(self, cht: Path, base: Path) -> Path:
        """The ``.cht``'s ``F`` line is the real fiducial marks. When "Use
        fiducial marks" is ON, hand scanin that file unchanged — the user placed
        the marquee corners on the marks, and ``-F`` maps them to the ``F`` line.
        Otherwise rewrite ``F`` to the patch-area bounding box, so ``-F`` maps
        the corners the user placed on the patch grid instead.

        The rewrite runs for ChromIQ-chart mode too (#108): a user-supplied
        printtarg ``-s`` .cht carries real corner marks OUTSIDE the patch
        area — Knut's charts have them 7 mm out on three sides, so skipping
        the rewrite compressed the whole grid downward. The rewrite keeps the
        original F's corner ORDER (engine charts are y-up, standard charts
        y-down — a fixed order vertically mirrored engine reads, #108)."""
        if self._standard_mode() and self._use_fiducials_cb.isChecked():
            return cht                # ON: corners were placed on the real marks
        from workflow.scanin_runner import cht_with_patchbox_fiducials
        try:
            txt = cht.read_text(errors="ignore")
        except OSError:
            return cht
        new = cht_with_patchbox_fiducials(txt)
        if new == txt:
            return cht
        dst = base.parent / f"{cht.stem}-patchbox.cht"
        dst.write_text(new)
        return dst

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
        self._save_placement()                   # remember this target's grid
        self._log.clear()
        self._align_warnings = []                # per-page misalignment findings
        self._run_diags: list[Path] = []         # diagnostic images this run writes
        method = self._avg_method.currentData() or "mean"
        if self._standard_mode():
            pages = [0]
            first = next(s["path"] for s in self._page_shots(0) if s["path"])
            base = first.parent / first.stem
            # Keep the result folder self-contained: drop the reference .cie (a
            # converted one otherwise lives in a temp dir) next to the scan +
            # outputs, so everything for this profile sits together (Knut).
            if self._std_ref is not None:
                dest = base.parent / self._std_ref.name
                try:
                    if self._std_ref.resolve() != dest.resolve():
                        import shutil
                        shutil.copy2(self._std_ref, dest)
                        self._std_ref = dest
                except OSError:
                    pass
        else:
            pages = self._pages
            base = _chart_base(self._ti3)

        frac = self._sample_area.value() / 100.0
        if self._printer_mode():
            self._execute_printer(base, frac)
            return
        self._jobs = []
        page_ti3s: list[Path] = []
        for pg in pages:
            orig_cht, cie = self._files_for_page(pg, base)   # pre-rewrite (fiducials)
            shots = [s for s in self._page_shots(pg) if s["path"]]
            shot_ti3s: list[Path] = []
            for k, s in enumerate(shots):
                scan = s["path"]
                # Per-scan cht: align the interior to THIS scan's pixel size (so a
                # rounded rectarg image lines up), then fiducial-frame + sample-area.
                cht = self._prepare_scanin_cht(orig_cht, s["corners"], frac, base,
                                               f"p{pg + 1}s{k + 1}")
                # One diag per scan, named after the scan itself — with averaging
                # you want to check EVERY shot's alignment, not just the first
                # (#102). Distinct scan stems keep the files from colliding.
                diag = (scan.with_name(scan.stem + "-diag.tif")
                        if self._diag.isChecked() else None)
                if diag is not None:
                    self._run_diags.append(diag)
                params = ScaninParams(
                    scan, cht, cie,
                    corners=self._scanin_corners(s["corners"], orig_cht),
                    perspective=self._perspective.isChecked(), diag=diag,
                    out_name=f"{base.name}-p{pg + 1}s{k + 1}-scanner.ti3")
                shot_ti3s.append(params.out_ti3)
                self._jobs.append({"kind": "scanin", "params": params,
                                   "page": pg + 1, "shot": k + 1,
                                   "nshots": len(shots),
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
        total = len(self._jobs)
        step = tr("Step {k} of {n}").format(k=i + 1, n=total)
        if job["kind"] == "scanin":
            self._log.appendPlainText(job["label"])
            self._set_busy_note(f"{step} — {job['label']}", fraction=i / total)

            unfilled = []

            def _watch(line: str) -> None:
                # scanin keeps going after this, but the read is partial — buried
                # in the -v noise Knut only noticed via the bad diagnostics (#108).
                if "Not all sample values have been filled" in line:
                    unfilled.append(line)
                self._log_line(line)

            def _done(code: int, i=i, job=job) -> None:
                fail = self._scanin.primary_failure()
                if code != 0 or fail is not None or not job["params"].out_ti3.exists():
                    msg = fail[1] if fail else tr("ScanIn couldn't read this page.")
                    self._log.appendPlainText(f"[ERROR] {msg}")
                    self._finish(False)
                    return
                # In printer mode each page fills only its own share of the
                # accumulated .ti3, so scanin reports "Not all sample values
                # have been filled" on every page but the last even when all
                # is well (#108) — only the final page's report means real gaps.
                if unfilled and not (job["params"].is_printer
                                     and not job.get("final")):
                    self._log.appendPlainText(tr(
                        "⚠ Not every patch on this page could be read — the grid "
                        "placement is probably off. Check the diagnostic image "
                        "(if saved), realign the grid on this page's scan and "
                        "build again; a profile from a partial read will be "
                        "wrong."))
                if not job["params"].is_printer:     # printer .ti3 is accumulated;
                    self._sanitize_scanner_ti3(job["params"].out_ti3)  # sanitize at end
                self._check_page_alignment(job)
                self._run_job(i + 1)

            self._scanin.run(job["params"], on_line=_watch, on_finish=_done)
        elif job["kind"] == "average":
            _avg = tr("Averaging {n} scans of this page…").format(n=len(job["ti3s"]))
            self._log.appendPlainText(_avg)
            self._set_busy_note(f"{step} — {_avg}", fraction=i / total)
            try:
                average_scanner_ti3(job["ti3s"], job["out"], method=job["method"])
            except (Ti3AverageError, OSError) as exc:
                self._log.appendPlainText(f"[ERROR] {exc}")
                self._finish(False)
                return
            self._run_job(i + 1)
        elif job["kind"] == "colprof_printer":
            self._set_busy_note(
                f"{step} — " + tr("Building the printer profile…"),
                fraction=i / total)
            self._build_printer_profile(job["pbase"], job["base"])
        else:
            self._set_busy_note(
                f"{step} — " + tr("Building the scanner profile…"),
                fraction=i / total)
            self._build_profile(job["ti3s"], job["base"])

    def _execute_printer(self, base: Path, frac: float) -> None:
        """Printer profile from a scanned ChromIQ chart: ``scanin -c/-ca`` converts
        each page's patches to real colour through the scanner profile and reads the
        chart's ``<base>.ti2`` (printer device values), accumulating one
        ``<pbase>.ti3``; then colprof builds a printer profile from it. The flat-bed
        scanner is the measuring instrument."""
        import shutil
        chart_ti2 = base.with_suffix(".ti2")
        if not chart_ti2.is_file():
            self._log.appendPlainText(tr(
                "[ERROR] This chart has no .ti2 (the printer values it was printed "
                "with) next to its .ti3, so a printer profile can't be built from it."))
            self._finish(False)
            return
        first = next((s["path"] for pg in self._pages
                      for s in self._page_shots(pg) if s["path"]), None)
        if first is None:
            self._finish(False)
            return
        pbase = first.parent / f"{base.name}-printer"
        try:
            shutil.copy2(chart_ti2, pbase.with_suffix(".ti2"))
        except OSError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return
        self._jobs = []
        if any(sum(1 for sh in self._page_shots(pg) if sh["path"]) > 1
               for pg in self._pages):
            self._log.appendPlainText(tr(
                "Note: averaging isn't used for a printer profile — only the "
                "first scan of each page is read."))
        first_page = True
        for pg in self._pages:
            orig_cht, _ = self._files_for_page(pg, base)
            shots = [s for s in self._page_shots(pg) if s["path"]]
            if not shots:
                continue
            s = shots[0]                             # one scan per page in printer mode
            cht = self._prepare_scanin_cht(orig_cht, s["corners"], frac, base,
                                           f"printer-p{pg + 1}")
            # A diag per page scan (each page is its own image), not just the
            # first — every page's alignment is worth checking (#102).
            diag = (s["path"].with_name(s["path"].stem + "-diag.tif")
                    if self._diag.isChecked() else None)
            if diag is not None:
                self._run_diags.append(diag)
            params = ScaninParams(
                s["path"], cht,
                corners=self._scanin_corners(s["corners"], orig_cht),
                perspective=self._perspective.isChecked(), diag=diag,
                scan_profile=self._printer_scan_profile, pbase=pbase,
                accumulate=not first_page)
            self._jobs.append({"kind": "scanin", "params": params,
                               "page": pg + 1,
                               "label": tr("Reading page {n} for the printer "
                                           "profile…").format(n=pg + 1)})
            first_page = False
        if not self._jobs:
            self._finish(False)
            return
        self._jobs[-1]["final"] = True      # last page: the .ti3 must be complete
        self._jobs.append({"kind": "colprof_printer", "pbase": pbase, "base": base})
        self._run_job(0)

    def _check_page_alignment(self, job: dict) -> None:
        """Knut's misalignment sanity check (#108), per page — so the warning
        names the scan to fix. Printer mode: compare the page's patches (the
        IDs its .cht reads) in the accumulated .ti3 against the chart's aim
        values — ΔE76 > 15 on more than 10% of them means a scrambled patch
        assignment, not an uncalibrated printer. Scanner mode: the reference
        is what the .ti3 itself pairs the read with, so ΔE is trivially small
        there — instead rank-correlate the scan's luminance with the
        reference Y (:func:`scan_reference_correlation`). Findings are logged
        AND collected in ``_align_warnings``; before colprof runs the user
        gets a modal choice — his misaligned build sailed through as one ⚠
        line buried in colprof's -v output."""
        try:
            p = job["params"]
            floor = float(self._settings.get("scanner_align_corr", 0.60))
            if p.is_printer:
                rho = page_reference_agreement(
                    p.out_ti3, p.pbase.with_suffix(".ti2"),
                    ids=page_ids_from_cht(p.cht))
                if rho is not None and rho < floor:
                    msg = tr(
                        "Page {n}: what the scanner measured doesn't line up "
                        "with the colours the chart asked the printer to "
                        "print — the grid is probably misaligned on this "
                        "page's scan.").format(n=job.get("page", 1))
                    self._log.appendPlainText("⚠ " + msg)
                    self._align_warnings.append(msg)
                else:
                    self._check_local_groups(job, p.out_ti3,
                                             p.pbase.with_suffix(".ti2"),
                                             ids=page_ids_from_cht(p.cht))
                return
            rho = scan_reference_correlation(p.out_ti3)
            if rho is not None and rho < floor:
                msg = (tr("Page {n} (scan {k}): what the scanner read doesn't "
                          "line up with the chart's colours — the grid is "
                          "probably misaligned on this scan.")
                       if job.get("nshots", 1) > 1 else
                       tr("Page {n}: what the scanner read doesn't line up "
                          "with the chart's colours — the grid is probably "
                          "misaligned on this page's scan.")).format(
                    n=job.get("page", 1), k=job.get("shot", 1))
                self._log.appendPlainText("⚠ " + msg)
                self._align_warnings.append(msg)
            else:
                self._check_local_groups(job, p.out_ti3)
        except Exception:  # noqa: BLE001 — a sanity check must never block
            log.warning("misalignment check failed", exc_info=True)

    def _check_local_groups(self, job: dict, ti3: Path,
                            ti2: Path | None = None,
                            ids: set[str] | None = None) -> None:
        """The LOCAL layer (Knut's row/column idea, #108): a page whose
        whole-page checks pass can still have one grid edge a cell off —
        rank-displacement clustering names the affected row/column."""
        from workflow.ti3_analysis import parse_ti3
        got = parse_ti3(ti3)
        if ti2 is None:                       # scanner mode: reference is inline
            read = {_plain_id(s): 0.2126 * r + 0.7152 * g + 0.0722 * b
                    for s, (r, g, b) in zip(got.sample_ids, got.rgb)}
            exp = {_plain_id(s): y
                   for s, (_x, y, _z) in zip(got.sample_ids, got.xyz)}
        else:                                 # printer mode: aims from the .ti2
            aim = parse_ti3(ti2)
            loc_of = {_plain_id(s): _plain_id(l.strip('"'))
                      for s, l in zip(aim.sample_ids, aim.sample_locs)}
            exp = {loc_of.get(_plain_id(s), _plain_id(s)): y
                   for s, (_x, y, _z) in zip(aim.sample_ids, aim.xyz)}
            read = {loc_of.get(_plain_id(s), _plain_id(s)): y
                    for s, (_x, y, _z) in zip(got.sample_ids, got.xyz)}
            if ids is not None:
                read = {k: v for k, v in read.items() if k in ids}
        groups = locally_misaligned_groups(read, exp)
        if not groups:
            return
        msg = tr(
            "Page {n}: the patches in {groups} read like their neighbours' "
            "colours — a grid edge probably sits about one cell off there. "
            "Check that edge of the grid on this page's scan.").format(
                n=job.get("page", 1), groups=", ".join(groups))
        self._log.appendPlainText("⚠ " + msg)
        self._align_warnings.append(msg)

    def _confirm_despite_misalignment(self) -> bool:
        """Modal stop before colprof when a page failed the alignment check —
        a profile from a scrambled read is garbage, and a log line alone is
        overlooked (#108). Returns True to build anyway."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Scan doesn't match the chart"))
        box.setText(tr("The alignment check failed:"))
        box.setInformativeText(
            "\n\n".join("• " + w for w in self._align_warnings) + "\n\n" + tr(
                "Check the flagged page's diagnostic image (if saved), realign "
                "the grid on its scan and build again. A profile built from "
                "this read will be wrong."))
        stop = box.addButton(tr("Stop"), QMessageBox.ButtonRole.RejectRole)
        box.addButton(tr("Build anyway"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(stop)
        box.exec()
        if box.clickedButton() is stop:
            self._log.appendPlainText(tr(
                "Stopped — realign the flagged page's grid and build again."))
            # Put the evidence one click away (Knut: the reveal button only
            # appeared after a FINISHED build, so the diagnostic image the
            # message points at was left to hunt for by hand).
            diags = [d for d in getattr(self, "_run_diags", []) if d.exists()]
            scans = [s["path"] for pg in self._pages
                     for s in self._page_shots(pg) if s["path"]]
            target = diags[0] if diags else (scans[0] if scans else None)
            if target is not None:
                # It reveals the FOLDER (Knut: the old label promised the
                # image itself, and appeared even with the diag box unticked).
                self._last_profile = target
                self._reveal_btn.setText(tr("Reveal folder"))
                self._reveal_btn.setVisible(True)
                self._reveal_btn.setEnabled(True)
            if not diags:
                self._log.appendPlainText(tr(
                    "Tip: tick “Save a diagnostic image of what was read” and "
                    "build again — the image shows exactly which patches were "
                    "read from your scan."))
            self._finish(False)
            return False
        return True

    def _watch_profile_check(self):
        """An ``on_line`` wrapper that also captures colprof's own fit check
        ("Profile check complete, peak err = …"). Knut's sub-patch grid shifts
        slip past the per-page pre-checks (a half-patch shift reads plausible
        BLENDS of neighbouring colours) but blow this number up (his tests:
        peak 60–91 vs < 10 aligned) — so the fit check is the arbiter of
        subtle misalignment (#108)."""
        found: list[tuple[float, float]] = []

        def _on_line(line: str) -> None:
            m = _PROFCHECK_RE.search(line)
            if m:
                found.append((float(m.group(1)), float(m.group(2))))
            self._log_line(line)

        return _on_line, found

    def _selfcheck_verdict(self, found: list[tuple[float, float]]) -> None:
        """Warn when colprof's fit check looks like a misread. BOTH numbers
        must be high: a matrix scanner profile legitimately fits a few
        extreme patches poorly (Knut's perfectly aligned build: peak 32.8,
        average 8.5), while a misplaced grid lifts the AVERAGE too (his
        misaligned runs: peak 60–91 with averages around 40)."""
        if not found:
            return
        peak, avg = found[-1]
        peak_lim = float(self._settings.get("scanner_selfcheck_peak", 30.0))
        avg_lim = float(self._settings.get("scanner_selfcheck_avg", 12.0))
        if peak <= peak_lim or avg <= avg_lim:
            return
        self._log.appendPlainText(tr(
            "⚠ Self-check: colprof reports a peak fit error of {p} with an "
            "average of {a} — an aligned read keeps the average well under "
            "{al}. A grid sitting slightly off on one page (even by half a "
            "patch) produces exactly this. Check the diagnostic images, "
            "realign and rebuild before trusting this profile. (Thresholds: "
            "Settings → Scanner Limits.)").format(
                p=round(peak, 1), a=round(avg, 1), al=round(avg_lim)))

    def _build_printer_profile(self, pbase: Path, base: Path) -> None:
        ti3 = pbase.with_suffix(".ti3")
        self._sanitize_scanner_ti3(ti3)              # once, on the accumulated .ti3
        if not self._align_warnings:
            # Per-page checks found nothing (or couldn't run — e.g. an
            # unparseable BYO .cht): one whole-chart pass as the safety net.
            try:
                floor = float(self._settings.get("scanner_align_corr", 0.60))
                rho = page_reference_agreement(ti3, base.with_suffix(".ti2"))
                if rho is not None and rho < floor:
                    self._align_warnings.append(tr(
                        "What the scanner measured doesn't line up with the "
                        "colours the chart asked the printer to print — a "
                        "grid was probably misaligned, or a scan doesn't "
                        "belong to this chart."))
            except Exception:  # noqa: BLE001 — a sanity check must never block
                log.warning("misalignment check failed", exc_info=True)
        if self._align_warnings and not self._confirm_despite_misalignment():
            return
        self._log.appendPlainText(tr("Building the printer profile…"))
        ti3, custom = self._apply_profile_name(ti3)
        params = ProfileParams(
            ti3_path=ti3, algorithm="l", quality="m",
            description=custom or f"{base.name} (scanner-measured)",
            manufacturer="ChromIQ",
            model=custom or base.name, verbose=True)

        def _done(code: int) -> None:
            icc = self._profiler.expected_icc_path(params)
            if not (icc.exists() and icc.stat().st_size > 1000):
                fail = self._profiler.primary_failure()
                if fail:
                    self._log.appendPlainText(f"[ERROR] {fail[1]}")
                else:
                    raw = self._profiler.last_output()
                    self._log.appendPlainText(
                        f"[ERROR] {tr('Building the profile failed. colprof said:')}")
                    self._log.appendPlainText(raw or tr("(colprof produced no output)"))
                self._finish(False)
                return
            self._log.appendPlainText(tr("[OK] Printer profile saved: {p}").format(p=icc))
            self._selfcheck_verdict(_check)
            self._log.appendPlainText(tr(
                "Install it as your printer's profile. The measurement (.ti3) sits "
                "next to it — load that in the Build Profile tab if you want to "
                "fine-tune the printer profile (intents, quality, …)."))
            self._last_profile = icc
            self._reveal_btn.setText(tr("Reveal profile"))
            self._reveal_btn.setVisible(True)
            self._reveal_btn.setEnabled(True)
            self._install_btn.setVisible(True)
            self._install_btn.setEnabled(True)
            self._finish(True)

        on_line, _check = self._watch_profile_check()
        self._profiler.build(params, on_line=on_line, on_finish=_done)

    def _sanitize_scanner_ti3(self, ti3: Path) -> None:
        """Fix nan/inf values scanin can write for degenerate patches, which would
        otherwise make colprof reject the whole .ti3 (a common Windows crash)."""
        from workflow.scanin_runner import sanitize_ti3
        try:
            clean, zeroed, dropped = sanitize_ti3(ti3.read_text(errors="ignore"))
        except OSError:
            return
        if not (zeroed or dropped):
            return
        try:
            ti3.write_text(clean)
        except OSError:
            return
        if dropped:
            msg = (tr("Note: 1 patch that didn't read (no usable pixels) was left "
                      "out so the profile can still build — re-check the grid "
                      "covers every patch inside the image.")
                   if dropped == 1 else tr(
                      "Note: {n} patches that didn't read (no usable pixels) were "
                      "left out so the profile can still build — re-check the grid "
                      "covers every patch inside the image.").format(n=dropped))
            self._log.appendPlainText(msg)
        if zeroed:
            self._log.appendPlainText(tr(
                "Note: some patches had an undefined noise figure; it was set to "
                "zero (no effect on the measured colour)."))

    def _build_profile(self, page_ti3s: list[Path], base: Path) -> None:
        # Combine multi-page reads into one .ti3, then colprof → scanner ICC.
        if self._align_warnings and not self._confirm_despite_misalignment():
            return
        try:
            combined = self._combine_ti3(page_ti3s, base)
        except OSError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return
        self._log.appendPlainText(tr("Building the scanner profile…"))
        alg, quality = self._ptype.currentData() or ("s", "m")
        combined, custom = self._apply_profile_name(combined)
        desc = custom or f"{base.name} scanner"
        params = ProfileParams(
            ti3_path=combined, algorithm=alg, quality=quality,
            description=desc, manufacturer="ChromIQ",
            model=desc, verbose=True)                     # show colprof's output

        def _done(code: int) -> None:
            # Resolve the profile the same robust way the printer builder does:
            # colprof writes .icc OR .icm (Windows) and may append rather than
            # replace the extension. Trust a valid profile on disk over colprof's
            # exit code — on Windows it can exit non-zero *after* "Profile done",
            # which used to make ChromIQ cry failure and hide the profile (Nelson).
            icc = self._profiler.expected_icc_path(params)
            if not (icc.exists() and icc.stat().st_size > 1000):
                fail = self._profiler.primary_failure()
                if fail:
                    self._log.appendPlainText(f"[ERROR] {fail[1]}")
                else:
                    # No recognised pattern — show what colprof actually said, so
                    # the reason is never hidden behind "see messages above".
                    raw = self._profiler.last_output()
                    self._log.appendPlainText(f"[ERROR] {tr('Building the profile failed. colprof said:')}")
                    self._log.appendPlainText(raw or tr("(colprof produced no output)"))
                self._finish(False)
                return
            self._log.appendPlainText(tr("[OK] Scanner profile saved: {p}").format(p=icc))
            self._selfcheck_verdict(_check)
            self._log.appendPlainText(tr(
                "Install it as your scanner's input profile. Use the diagnostic "
                "image (if you saved one) to check the patches were read correctly."))
            self._last_profile = icc
            self._reveal_btn.setText(tr("Reveal profile"))
            self._reveal_btn.setVisible(True)     # let the user find the .icc
            self._reveal_btn.setEnabled(True)
            self._install_btn.setVisible(True)
            self._install_btn.setEnabled(True)
            self._finish(True)

        on_line, _check = self._watch_profile_check()
        self._profiler.build(params, on_line=on_line, on_finish=_done)

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
