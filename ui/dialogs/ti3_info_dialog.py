"""Measurement info — Tools ▸ "Inspect a measurement".

Opens an ArgyllCMS ``.ti3`` (the raw per-patch measurement behind a profile)
and explains, in plain language, what the paper-and-ink actually did: the true
measured contrast, how neutral the greys really are (the cast the profile then
corrects), how far the gamut reaches, whether the read looks trustworthy, and —
from the spectral data — how the paper behaves under other lighting.

Companion to :mod:`ui.dialogs.profile_info_dialog`. Where that inspects the
fitted ``.icc`` model, this inspects the ground-truth data it was built from.
All maths is in :mod:`workflow.ti3_analysis`; no ArgyllCMS call, so nothing here
can hang or crash on quit.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.logger import get_logger
from ui.dialog_sizing import pin_min_height
from ui.dialogs.tools_dialogs import _indicator_color, neutral_controls_qss
from ui.fade_scroll import FadeScrollArea
from ui.styles import SPEC_CYAN, TEXT_DIM, TEXT_MAIN
from ui.tab_header import dialog_masthead
from ui.widgets import make_browse_button, open_file_dialog
from workflow.ti3_analysis import (
    Ti3Analysis,
    Ti3ParseError,
    analyse_ti3,
    parse_ti3,
)

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_ACCENT = SPEC_CYAN

_HELP = tr(
    "Open a measurement file (.ti3) — the raw readings of every patch on a "
    "printed chart, taken with your instrument before any profile is built. "
    "Because it's the real data a profile is fitted to, it can tell you things "
    "the finished profile only smooths over:\n\n"
    "  •  the true contrast between paper white and the deepest black,\n"
    "  •  how neutral your greys actually measured (the colour cast the profile "
    "then has to correct),\n"
    "  •  how saturated a colour your paper-and-ink reached,\n"
    "  •  whether the measurement looks clean or a strip was misread, and\n"
    "  •  how the paper behaves under different lighting (from its spectral data).\n\n"
    "Hover any value for a friendly explanation of what it means and what you "
    "can learn from it."
)


class Ti3InfoDialog(QDialog):
    def __init__(
        self,
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._analysis: Ti3Analysis | None = None

        self.setWindowTitle(tr("Measurement info"))
        self.setMinimumWidth(680)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head, _header, stripe = dialog_masthead(
            self, tr("MEASUREMENT · INSPECT"), tr("Measurement info"),
            tooltip_title=tr("Measurement info"), tooltip_body=_HELP,
            accent=_ACCENT)
        root.addLayout(head)
        root.addWidget(stripe)

        self._inner = QVBoxLayout()
        self._inner.setContentsMargins(22, 14, 22, 16)
        self._inner.setSpacing(12)
        root.addLayout(self._inner)

        self._body = QLabel(
            tr("Open a measurement file to inspect what your printer and paper "
               "actually did."), self)
        self._body.setWordWrap(True)
        self._inner.addWidget(self._body)

        # --- File picker row ----------------------------------------------
        pick = QHBoxLayout()
        pick.setSpacing(8)
        pick.addWidget(QLabel(tr("Measurement:"), self))
        self._path_edit = QLineEdit(self)
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(tr("Browse for a .ti3 measurement…"))
        pick.addWidget(self._path_edit, 1)
        # Reuse the cyan "folder_build" glyph (as on the Build Profile tab) so
        # the browse button matches this dialog's cyan masthead, not the violet
        # "folder_check" used by the violet Profile-info dialog.
        browse = make_browse_button(
            self, tr("Browse for a .ti3 measurement"), "folder_build")
        browse.clicked.connect(self._on_browse)
        pick.addWidget(browse)
        self._inner.addLayout(pick)

        # --- error banner (hidden until needed) ---------------------------
        self._banner = QLabel("", self)
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        self._inner.addWidget(self._banner)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._inner.addWidget(sep)

        # --- Scrollable details -------------------------------------------
        self._scroll = FadeScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Tall floor so the full six-section report is comfortably visible in
        # one glance on a normal display; pin_min_height still clamps the open
        # height to 90 % of the screen, and the scroll covers smaller screens.
        self._scroll.setMinimumHeight(720)

        self._details = QWidget()
        self._grid = QGridLayout(self._details)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(7)
        self._grid.setColumnStretch(1, 1)
        self._scroll.setWidget(self._details)
        self._inner.addWidget(self._scroll, 1)

        self._placeholder = QLabel(tr("No measurement loaded yet."), self._details)
        self._placeholder.setStyleSheet(f"color: {TEXT_DIM};")
        self._grid.addWidget(self._placeholder, 0, 0, 1, 2)

        # --- Bottom buttons -----------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton(tr("Close"), self)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        self._inner.addLayout(btn_row)

        # Use the cyan masthead accent (not the neutral indicator) for the
        # focus ring, so a highlighted field matches the rest of the dialog.
        self.setStyleSheet(neutral_controls_qss(_ACCENT))

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        pin_min_height(
            self, min_width=680, wrap_labels=(self._body, self._banner),
            inner_margins=self._inner.contentsMargins(), resize_width=True)

    # ------------------------------------------------------------------
    def _on_browse(self) -> None:
        start = str(Path.home() / "ChromIQ")
        if not Path(start).exists():
            start = str(Path.home())
        path = open_file_dialog(
            self, tr("Select a .ti3 measurement"),
            tr("Measurements (*.ti3);;All files (*)"), start_dir=start)
        if path:
            self.load_measurement(Path(path))

    def load_measurement(self, path: Path) -> None:
        self._path_edit.setText(str(path))
        try:
            data = parse_ti3(path)
            self._analysis = analyse_ti3(data)
        except (OSError, Ti3ParseError) as exc:
            self._analysis = None
            self._show_error(str(exc))
            return
        self._banner.setVisible(False)
        self._populate(self._analysis)
        self._body.setText(
            tr("Showing the measurement “{name}”.").format(name=path.name))

    def _show_error(self, msg: str) -> None:
        self._clear_grid()
        self._banner.setText(tr("Could not read this measurement: {msg}").format(msg=msg))
        self._banner.setStyleSheet(
            "QLabel { background: rgba(255,69,115,0.12); color: #ff4573;"
            " border: 1px solid #ff4573; border-radius: 4px; padding: 8px 10px; }")
        self._banner.setVisible(True)

    # ------------------------------------------------------------------
    # Details grid
    # ------------------------------------------------------------------
    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._row = 0

    def _section(self, title: str, tooltip: str = "") -> None:
        lbl = QLabel(title, self._details)
        lbl.setStyleSheet(
            f"color: {_ACCENT}; font-weight: 600; font-size: 12px;"
            f" padding-top: {'10px' if self._row else '0px'}; padding-bottom: 2px;")
        if tooltip:
            lbl.setToolTip(tooltip)
        self._grid.addWidget(lbl, self._row, 0, 1, 2)
        self._row += 1

    def _row_kv(self, key: str, value: str, tooltip: str = "") -> None:
        k = QLabel(key, self._details)
        k.setStyleSheet(
            f"color: {TEXT_DIM}; font-family: Menlo, Consolas, monospace; font-size: 11px;")
        k.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v = QLabel(value or "—", self._details)
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.setStyleSheet(
            f"color: {TEXT_MAIN}; font-family: Menlo, Consolas, monospace; font-size: 12px;")
        if tooltip:
            k.setToolTip(tooltip)
            v.setToolTip(tooltip)
        self._grid.addWidget(k, self._row, 0)
        self._grid.addWidget(v, self._row, 1)
        self._row += 1

    def _populate(self, a: Ti3Analysis) -> None:
        self._clear_grid()
        kw = a.data.keywords

        # --- Measurement -------------------------------------------------
        self._section(tr("Measurement"), tr(
            "The basic facts about this chart and how it was read."))
        self._row_kv(
            tr("Patches"), tr("{n:,}").format(n=a.data.n_patches),
            tr("How many colour patches were printed and measured. More patches "
               "generally let the profile describe your printer more accurately."))
        self._row_kv(
            tr("Instrument"), kw.get("TARGET_INSTRUMENT", "—"),
            tr("The measuring device that read the chart. Different instruments "
               "can disagree slightly, so it's good to know which one produced "
               "these numbers."))
        self._row_kv(
            tr("Device"), kw.get("DEVICE_CLASS", "—") + "  ·  "
            + kw.get("COLOR_REP", ""),
            tr("What kind of device was measured (here an OUTPUT printer) and the "
               "colour encoding — for example iRGB_XYZ means device RGB in, "
               "measured XYZ out."))
        spec = (tr("yes — {b} bands, {lo:.0f}–{hi:.0f} nm").format(
                    b=len(a.data.wavelengths), lo=a.data.wavelengths[0],
                    hi=a.data.wavelengths[-1])
                if a.data.has_spectral else tr("no (XYZ/Lab only)"))
        self._row_kv(
            tr("Spectral data"), spec,
            tr("Whether the file stores the full colour spectrum of each patch, "
               "not just a single XYZ value. Spectral data lets ChromIQ "
               "recompute the colours under different lighting (see below)."))
        if kw.get("CREATED"):
            self._row_kv(tr("Measured"), kw["CREATED"], tr(
                "When the chart was read. Inks and paper can drift over time, so "
                "an old measurement may no longer match today's prints."))

        # --- Tone & contrast ---------------------------------------------
        self._section(tr("Tone & contrast"), tr(
            "How light the paper is, how dark the black is, and the range "
            "between them — taken straight from the lightest and darkest "
            "patches actually measured."))
        self._row_kv(
            tr("Paper white L*"), tr("{l:.1f}").format(l=a.white_lab[0]),
            tr("Lightness of the blank paper on the 0–100 L* scale — the "
               "brightest your prints can be. Higher is a whiter paper."))
        self._row_kv(
            tr("Max black L*"), tr("{l:.1f}").format(l=a.black_lab[0]),
            tr("Lightness of the deepest black this paper-and-ink reached. "
               "Lower is a richer black and usually means more contrast and "
               "better shadow detail."))
        self._row_kv(
            tr("Contrast ratio"), tr("{r:,.0f} : 1").format(r=a.contrast_ratio),
            tr("How many times brighter the paper white is than the deepest "
               "black. Bigger means punchier prints. This is the *measured* "
               "contrast — the honest figure for this paper-and-ink, which is "
               "what you started out asking about."))
        self._row_kv(
            tr("Dynamic range"), tr("{d:.2f} D").format(d=a.dynamic_range),
            tr("The same contrast written as optical density, the way print and "
               "photo people measure it — the span from lightest to darkest "
               "tone. Each whole step is a ten-fold change in reflected light; "
               "around 2.2 D and up is excellent for inkjet."))
        self._row_kv(
            tr("Lightness range ΔL*"), tr("{d:.1f}").format(d=a.delta_lstar),
            tr("The plain lightness gap between paper white and max black on the "
               "L* scale — a quick, perceptual feel for the contrast."))

        # --- Grey balance -------------------------------------------------
        self._section(tr("Grey balance"), tr(
            "How neutral your greys measured before profiling. A printer's raw "
            "greys almost always have a colour tint — this is exactly what the "
            "profile then corrects, so a bigger cast here means the profile is "
            "working harder, not that your prints will look wrong."))
        self._row_kv(
            tr("Neutral patches"), tr("{n}").format(n=a.n_neutral),
            tr("How many patches had equal amounts of R, G and B (the grey "
               "ramp). These are what we measure the cast from."))
        self._row_kv(
            tr("Average cast"), tr("{c:.1f} C*").format(c=a.mean_cast),
            tr("On average, how far the greys drifted away from truly neutral "
               "(their chroma). 0 would be perfectly neutral; a few units is "
               "normal for an unprofiled printer."))
        self._row_kv(
            tr("Largest cast"), tr("{c:.1f} C*  at L* {l:.0f}").format(
                c=a.max_cast, l=a.max_cast_lstar),
            tr("The strongest tint found in the grey ramp, and the lightness it "
               "happened at. Casts often peak in the shadows or highlights."))
        self._row_kv(
            tr("Tendency"), _cast_text(a.cast_token),
            tr("Which way the greys lean overall — for example slightly warm/"
               "yellow or cool/blue. Tells you the character of your raw output "
               "that the profile neutralises."))

        # --- Gamut --------------------------------------------------------
        self._section(tr("Gamut reach"), tr(
            "The most saturated colours your paper-and-ink managed to print. "
            "A wider reach means more vivid colours are possible."))
        self._row_kv(
            tr("Most saturated"), tr("C* {c:.0f}  ({hue})").format(
                c=a.max_chroma, hue=_hue_text(a.max_chroma_hue)),
            tr("The single most vivid patch measured and roughly its hue. On "
               "most inkjet papers this is a yellow or orange, which the eye "
               "sees as the punchiest colour."))
        prim = a.primary_chroma
        # Universal channel letters (R Y G C B M) — not translated.
        letters = {"red": "R", "yellow": "Y", "green": "G",
                   "cyan": "C", "blue": "B", "magenta": "M"}
        prim_txt = "  ".join(
            f"{letters[p]}{int(round(prim[p]))}" for p in letters)
        self._row_kv(
            tr("Per-hue peak C*"), prim_txt,
            tr("The strongest chroma reached in each colour direction (Red, "
               "Yellow, Green, Cyan, Blue, Magenta). Lets you see if one "
               "direction — say, a weak cyan — is limiting your gamut."))
        if a.has_rolloff:
            self._row_kv(
                tr("Note"), tr("⚠ see tooltip"),
                tr("Full-ink primaries measured notably less saturated than "
                   "mid-tone ones — sometimes a sign the printer driver applied "
                   "colour management despite a “No Correction” setting. Worth "
                   "checking if the print looked dull."))

        # --- Quality ------------------------------------------------------
        self._section(tr("Measurement quality"), tr(
            "Sanity checks that catch a bad read before you build a profile "
            "from it."))
        mono = (tr("clean — greys get lighter step by step")
                if a.neutral_non_monotonic == 0
                else tr("{n} reversal(s) — possible misread")
                .format(n=a.neutral_non_monotonic))
        self._row_kv(
            tr("Neutral ramp"), mono,
            tr("Going up the grey ramp, each step should measure lighter than "
               "the last. A step that goes darker usually means a strip was "
               "read in the wrong order or place — a classic cause of broken "
               "profiles. Clean is what you want here."))
        if a.duplicate_max_de is not None:
            self._row_kv(
                tr("Repeat scatter"),
                tr("{de:.2f} ΔE  over {n} repeats").format(
                    de=a.duplicate_max_de, n=a.duplicate_count),
                tr("Some charts print the same colour more than once. This is "
                   "the biggest colour difference between copies of the same "
                   "patch — a direct measure of how repeatable the read was. "
                   "Under ~1 ΔE is excellent; large values hint at instrument "
                   "noise or a smudged chart."))

        # --- Under other light -------------------------------------------
        if a.illuminant_points:
            self._section(tr("Under other light"), tr(
                "Using the spectral data, the paper white recomputed as if lit "
                "by different standard light sources. Shows how your paper's "
                "tint and contrast change with the lighting it's viewed in — "
                "useful if prints are shown under shop or gallery lights rather "
                "than daylight."))
            for p in a.illuminant_points:
                self._row_kv(
                    p.name,
                    tr("L* {l:.1f}   a* {a:+.1f}   b* {b:+.1f}   ·   {r:,.0f}:1").format(
                        l=p.lab[0], a=p.lab[1], b=p.lab[2], r=p.contrast_ratio),
                    tr("The paper white's lightness (L*) and tint (a* green↔red, "
                       "b* blue↔yellow) under {name}, plus the contrast ratio. "
                       "Compare the rows: if a*/b* change a lot between lights, "
                       "your paper is metameric or brightened and will look "
                       "different depending on where it's displayed.").format(
                           name=p.name))
            if a.paper_shift_d50_d65 is not None:
                self._row_kv(
                    tr("Daylight shift"),
                    tr("{de:.1f} ΔE  (D50 → D65)").format(de=a.paper_shift_d50_d65),
                    tr("How much the paper white's colour shifts between warm "
                       "daylight (D50, the printing standard) and cool daylight "
                       "(D65, a typical screen white). A small number means the "
                       "paper looks consistent across lighting; a large one "
                       "means it's strongly brightened (lots of optical "
                       "brightener) and will change noticeably."))


def _cast_text(token: str) -> str:
    return {
        "neutral": tr("neutral (no significant cast)"),
        "warm": tr("slightly warm / yellow"),
        "cool": tr("slightly cool / blue"),
        "green": tr("slightly green"),
        "magenta": tr("slightly magenta"),
        "none": tr("no neutral patches found"),
    }.get(token, token)


def _hue_text(deg: float) -> str:
    # Lab hue angles: red ≈ 40°, yellow ≈ 95°, green ≈ 135°, cyan ≈ 195°,
    # blue ≈ 270°, magenta ≈ 330°.
    table = [(40, tr("red")), (70, tr("orange")), (105, tr("yellow")),
             (160, tr("green")), (210, tr("cyan")), (280, tr("blue")),
             (340, tr("magenta")), (360, tr("red"))]
    for edge, name in table:
        if deg < edge:
            return name
    return tr("red")
