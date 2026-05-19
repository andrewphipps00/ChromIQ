"""Welcome dialog — opens on first launch and via the masthead "?" button.

Two-page QStackedWidget:
  • Page 0 — six clickable WorkflowCard tiles arranged 3x2
  • Page 1 — numbered step instructions for the selected workflow

Theme-aware via set_appearance(mode); persists the "show on startup" choice
through AppSettings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.fade_scroll import FadeScrollArea
from ui.styles import SPEC_MAGENTA, TAB_COLORS

if TYPE_CHECKING:
    from core.settings import AppSettings


# ---------------------------------------------------------------------------
# Workflow content
# ---------------------------------------------------------------------------

# Each step is (tab_index_1based, text) or (tab_index_1based, text, optional_bool).
# tab_index drives the coloured badge. The displayed number inside the badge is
# the step count, not the tab number — the colour already tells you which tab.
# optional=True renders the badge outlined (rather than filled) and dims the
# text slightly, marking steps that improve quality but aren't required.
WORKFLOWS: list[dict] = [
    {
        "key": "first_profile",
        "title": "Build my first ICC profile",
        "subtitle": "The full walk-through from blank chart to finished profile.",
        "steps": [
            (1, "On the Create Chart tab, pick which instrument you'll measure "
                "with (e.g. i1Pro) and choose your paper size. Give the chart "
                "a descriptive name — it carries through to every file "
                "downstream (.ti2, .ti3 and the final .icc). A good convention "
                "is printer + paper + date, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_2026-05”; avoid spaces and "
                "special characters. Click “Create Chart”. ChromIQ writes a "
                "chart TIFF plus a .ti2 file that records exactly where every "
                "patch sits on the page."),
            (2, "Move to the Print Chart tab and pick your printer and media. "
                "Important: switch OFF any colour management in your driver "
                "dialog — if the driver re-maps colours the patches won't "
                "match their definition and the profile will be wrong. "
                "Click “Print”."),
            (3, "On the Measure tab, connect and switch on your "
                "spectrophotometer, then place the printed chart on a white "
                "surface (a plain sheet of paper underneath works perfectly) — "
                "a coloured or dark backing can bleed through thin stock and "
                "skew the reading. Before you scan, check the “Disable "
                "bidirectional reading” option: it's ON by default, the safest "
                "setting for any spectro. If you use an i1Pro and you're used "
                "to scanning each strip in one continuous left-and-right "
                "motion, turn this option OFF first — leaving it on while you "
                "sweep bidirectionally is the classic cause of mis-recognised "
                "strips and bad data. Click “Measure Chart” and follow the "
                "strip-by-strip prompts."),
            (4, "On the Build Profile tab the new .ti3 measurement is "
                "already loaded. Pick the profile quality (medium is a fine "
                "first try) and algorithm (Lab CLUT is the safe default for "
                "printers), then click “Build Profile”. When the build "
                "finishes a result popup appears — install the .icc system-"
                "wide from there, or jump to Check & Refine to verify its "
                "accuracy and start guided refinement (the steps below) for "
                "a noticeably more accurate profile."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "two_pass",
        "title": "Build a high-quality profile (2-pass)",
        "subtitle": "A pre-conditioning pass produces a sharper second profile.",
        "steps": [
            (1, "Start a fresh chart on the Create Chart tab. Pick the "
                "instrument and paper size as normal, then give the chart a "
                "descriptive name — it carries through to every file "
                "downstream (.ti2, .ti3 and the final .icc). A good "
                "convention is printer + paper + a “_pre” suffix for this "
                "pre-conditioning pass, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_pre_2026-05”; avoid spaces "
                "and special characters. Click “Create Chart”. This first "
                "chart will produce the pre-conditioning profile — not yet "
                "the final one."),
            (2, "On the Print Chart tab print this chart with driver "
                "colour management OFF, exactly as in workflow 1."),
            (3, "On the Measure tab check the “Disable bidirectional "
                "reading” option (see note in workflow 1) and read the "
                "chart with your spectrophotometer."),
            (4, "On the Build Profile tab click “Build Profile” to "
                "produce the first .icc. Treat this profile as a colour-"
                "space map rather than a finished result."),
            (4, "In the result popup (or in Check & Refine) click “Use as "
                "Pre-conditioning Profile”. ChromIQ jumps back to the "
                "Create Chart tab with the new .icc loaded as the "
                "pre-conditioning profile."),
            (1, "Optionally raise the patch count — a second-pass chart "
                "benefits from more patches because they're placed where "
                "the printer is most non-linear. Click “Create Chart” to "
                "generate the high-quality chart."),
            (2, "Print and measure the new chart exactly as before — "
                "driver colour management OFF, bidirectional reading "
                "decided correctly for your spectro."),
            (4, "Click “Build Profile” one more time. The result is "
                "noticeably more accurate than the first-pass profile "
                "because targen could place patches where they actually "
                "mattered. This is the profile to install."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold. After a clean 2-pass build "
                "the result is often already good enough; the steps below "
                "are for squeezing out the last few outliers.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "improve_existing_profile",
        "title": "Improve an existing ICC profile",
        "subtitle": "Seed ChromIQ with a current profile to build a sharper one.",
        "steps": [
            (1, "On the Create Chart tab, find the “Refinement (Optional)” "
                "section, tick “Refinement profile”, then click “Select "
                "pre-conditioning profile” and pick the existing .icc for "
                "this printer + paper combination. Choose the instrument "
                "and paper size as usual, and give the chart a descriptive "
                "name with a “_v2” (or similar) suffix, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_v2_2026-05”. Because the "
                "seed profile tells ChromIQ exactly where your printer is "
                "most non-linear, raise the patch count so those tricky "
                "regions get more samples. Click “Create Chart”."),
            (2, "On the Print Chart tab, print the new chart with driver "
                "colour management OFF, exactly as for any profiling print."),
            (3, "On the Measure tab, check the “Disable bidirectional "
                "reading” option (see note in workflow 1), place the chart "
                "on a white surface, and click “Measure Chart”."),
            (4, "On the Build Profile tab the new .ti3 is already loaded. "
                "Pick the profile quality — raising it from the seed "
                "profile's setting is worth the build time, because the "
                "smarter patch placement deserves a smarter build. Click "
                "“Build Profile”. When the build finishes a result popup "
                "appears — the new .icc is more accurate than the seed "
                "profile because ChromIQ placed patches where they mattered. "
                "Install it from the popup, or jump to Check & Refine to "
                "verify it before installing."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "print_chart",
        "title": "Print an existing test chart",
        "subtitle": "You already have a chart on disk and just want to print it.",
        "steps": [
            (2, "On the Print Chart tab, click “Load .ti2” and pick the "
                "chart definition file. ChromIQ finds the matching TIFF "
                "pages automatically — you don't pick them by hand."),
            (2, "Choose your printer, paper type and any quality settings "
                "the print dialog exposes. Make sure driver colour "
                "management is OFF, just like a fresh print."),
            (2, "Click “Print”. The TIFF is sent as raw PostScript so no "
                "driver filter alters the patches on the way to the "
                "printer."),
            (3, "Once the print is dry, head to the Measure tab and connect "
                "your spectrophotometer, then place the chart on a white "
                "surface (a plain sheet of paper underneath works). Before "
                "scanning, check the “Disable bidirectional reading” option "
                "— it's ON by default which is safest, but if you use an "
                "i1Pro and you're used to scanning each strip in one "
                "continuous left-and-right motion, turn it OFF first. "
                "Click “Measure Chart” and follow the strip-by-strip "
                "prompts."),
            (4, "On the Build Profile tab the new .ti3 is already loaded. "
                "Pick the profile quality (medium is a fine first try) and "
                "algorithm (Lab CLUT is the safe default for printers), "
                "then click “Build Profile”. When the build finishes a "
                "result popup appears — install the .icc system-wide from "
                "there, or jump to Check & Refine to verify its accuracy "
                "and start guided refinement (the steps below) for a "
                "noticeably more accurate profile."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "measure_existing",
        "title": "Measure a chart I already printed",
        "subtitle": "Jump straight to reading patches with your spectrophotometer.",
        "steps": [
            (3, "On the Measure tab, click “Load Chart File” and pick the "
                ".ti1 or .ti2 that matches your printed chart. The .ti2 is "
                "preferred where available — it contains the exact patch "
                "positions printtarg used."),
            (3, "Connect and switch on the spectrophotometer. ChromIQ "
                "detects it automatically; a green status pill appears in "
                "the toolbar when it's ready."),
            (3, "Check the “Disable bidirectional reading” option before "
                "you start. It's ON by default — safe for any spectro but "
                "slower. If you use an i1Pro and you're used to scanning "
                "each strip in one continuous left-and-right sweep, turn "
                "it OFF first; leaving it on while you scan bidirectionally "
                "causes mis-recognised strips and bad measurements."),
            (3, "Click “Measure Chart” and follow the strip-by-strip "
                "prompts. Results save as a .ti3 next to the chart, "
                "ready for the Build Profile tab."),
            (4, "On the Build Profile tab the new .ti3 is already loaded. "
                "Pick the profile quality (medium is a fine first try) and "
                "algorithm (Lab CLUT is the safe default for printers), "
                "then click “Build Profile”. When the build finishes a "
                "result popup appears — install the .icc system-wide from "
                "there, or jump to Check & Refine to verify its accuracy "
                "and start guided refinement (the steps below) for a "
                "noticeably more accurate profile."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "build_from_measurement",
        "title": "Build a profile from an existing measurement",
        "subtitle": "You have a .ti3 file — turn it into an ICC profile.",
        "steps": [
            (4, "On the Build Profile tab, click “Load .ti3” and pick your "
                "existing measurement file. The matching .ti1/.ti2 is "
                "found and loaded automatically."),
            (4, "Pick the profile quality and algorithm. Lab CLUT is the "
                "safe default for any printer profile; raise the quality "
                "setting if you want longer build times in return for "
                "smoother gradations."),
            (4, "Click “Build Profile”. The .icc lands next to the .ti3 "
                "in the same folder."),
            (4, "A result popup appears with three actions: install the "
                "profile to your system colour folder, jump to Check & "
                "Refine to inspect its accuracy, or feed it back as a "
                "pre-conditioning profile (workflow 2). You can dismiss "
                "the popup and come back to any of these later."),
            (5, "Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips.",
                True),
            (3, "Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are.",
                True),
            (4, "Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile.",
                True),
            (5, "Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further.",
                True),
        ],
    },
    {
        "key": "refine",
        "title": "Refine an existing profile",
        "subtitle": "Re-measure only the strips where ΔE is worst.",
        "steps": [
            (5, "On the Check & Refine tab, click “Load .ti3” and open the "
                "measurement of the profile you want to improve. The "
                "matching .icc loads automatically."),
            (5, "Click “Analyse”. ChromIQ runs profcheck and looks for "
                "patches whose ΔE is above your refinement threshold "
                "(configurable in the panel — 2.0 is a sensible "
                "starting point)."),
            (5, "If outlier patches are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected "
                "strips — much faster than reprinting and re-measuring "
                "the whole chart."),
            (3, "Re-measure the strips ChromIQ marks. The new readings "
                "are merged into the .ti3 — old patches are kept where "
                "they were already good."),
            (4, "Click “Build Profile” again. The refined .ti3 produces "
                "a more accurate profile, and you can repeat this cycle "
                "until the worst outliers are below threshold."),
        ],
    },
    {
        "key": "check_visualise",
        "title": "Visualise a profile's gamut",
        "subtitle": "See in 3D what colours a printer can and can't reproduce.",
        "steps": [
            (5, "On the Check & Refine tab, load the .icc profile you "
                "want to inspect. A matching .ti3 is helpful but not "
                "required for the gamut viewer."),
            (5, "Open the Gamut Viewer pane. ChromIQ runs iccgamut on the "
                "profile and renders the printer's colour volume as a 3D "
                "mesh that you can rotate, zoom and pan freely."),
            (5, "Optionally overlay a reference gamut (e.g. sRGB or "
                "AdobeRGB) to see at a glance which colours of the "
                "reference space the printer can hit and which it has "
                "to clip."),
            (5, "This workflow is read-only — no files are written, so "
                "you can poke around freely without changing anything."),
        ],
    },
]


# ---------------------------------------------------------------------------
# Painted card icon — geometric placeholder per workflow
# ---------------------------------------------------------------------------

class WorkflowIcon(QWidget):
    """96x96 painted icon. Magenta accent + monochrome lines that flip with theme."""

    SIZE = 96

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._mode = "dark"
        self.setFixedSize(QSize(self.SIZE, self.SIZE))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self.update()

    def _fg(self) -> QColor:
        return QColor("#22211f" if self._mode == "light" else "#e6e6e6")

    def paintEvent(self, _ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fg = self._fg()
        accent = QColor(SPEC_MAGENTA)
        stroke = 2.4

        s = self.SIZE
        if self._key == "first_profile":
            # 5 nodes connected in a row, last one filled magenta
            cy = s / 2
            n = 5
            pad = 12
            step = (s - 2 * pad) / (n - 1)
            p.setPen(QPen(fg, stroke))
            for i in range(n - 1):
                x0 = pad + i * step
                x1 = pad + (i + 1) * step
                p.drawLine(int(x0), int(cy), int(x1), int(cy))
            for i in range(n):
                cx = pad + i * step
                r = 7 if i == n - 1 else 5
                if i == n - 1:
                    p.setBrush(accent)
                    p.setPen(Qt.PenStyle.NoPen)
                else:
                    p.setBrush(QColor(0, 0, 0, 0))
                    p.setPen(QPen(fg, stroke))
                p.drawEllipse(int(cx - r), int(cy - r), 2 * r, 2 * r)

        elif self._key == "print_chart":
            # Sheet (rectangle) with a 4x6 patch grid; one accent patch
            margin = 14
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(margin, margin, s - 2 * margin, s - 2 * margin, 4, 4)
            cols, rows = 6, 4
            cell_w = (s - 2 * margin - 8) / cols
            cell_h = (s - 2 * margin - 8) / rows
            ox = margin + 4
            oy = margin + 4
            p.setPen(Qt.PenStyle.NoPen)
            accent_cell = (2, 1)
            for r in range(rows):
                for c in range(cols):
                    x = ox + c * cell_w
                    y = oy + r * cell_h
                    if (r, c) == accent_cell:
                        p.setBrush(accent)
                    else:
                        col = QColor(fg)
                        col.setAlpha(110)
                        p.setBrush(col)
                    p.drawRect(int(x + 1), int(y + 1), int(cell_w - 2), int(cell_h - 2))

        elif self._key == "measure_existing":
            # Spectro head (rounded rectangle with notch) above a strip of patches.
            # Sized to leave a generous gap to the card title below.
            head_w, head_h = 50, 24
            head_x = (s - head_w) / 2
            head_y = 19
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(int(head_x), int(head_y), head_w, head_h, 7, 7)
            # Aperture
            p.setBrush(accent)
            p.setPen(Qt.PenStyle.NoPen)
            ap = 7
            p.drawEllipse(int(s / 2 - ap / 2), int(head_y + head_h - 4), ap, ap)
            # Patches strip — integer dimensions keep every cell and gap uniform.
            n = 6
            cell = 12          # 12 * 6 = 72
            strip_w = n * cell  # 72
            strip_h = 18
            strip_y = 53
            pad = (s - strip_w) // 2  # 12
            patch_w = cell - 2  # 10 — leaves a 2 px gap to the next patch
            for i in range(n):
                if i == 2:
                    p.setBrush(accent)
                else:
                    col = QColor(fg)
                    col.setAlpha(110)
                    p.setBrush(col)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(pad + i * cell + 1, strip_y, patch_w, strip_h)
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(pad, strip_y, strip_w, strip_h)

        elif self._key == "build_from_measurement":
            # Document glyph (folded corner) → arrow → cube. Tightened so the
            # whole composition fits within the 96 canvas with a clear bottom
            # margin (previous version overflowed past the right edge).
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            # Document
            dx, dy, dw, dh = 10, 13, 28, 52
            p.drawLine(dx, dy, dx + dw - 9, dy)
            p.drawLine(dx + dw - 9, dy, dx + dw, dy + 9)
            p.drawLine(dx + dw, dy + 9, dx + dw, dy + dh)
            p.drawLine(dx + dw, dy + dh, dx, dy + dh)
            p.drawLine(dx, dy + dh, dx, dy)
            p.drawLine(dx + dw - 9, dy, dx + dw - 9, dy + 9)
            p.drawLine(dx + dw - 9, dy + 9, dx + dw, dy + 9)
            # Arrow
            ax0 = dx + dw + 4
            ax1 = ax0 + 12
            ay = dy + dh / 2
            p.setPen(QPen(accent, stroke))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube
            p.setPen(QPen(fg, stroke))
            csz = 20
            cx0 = ax1 + 4
            cy0 = int(ay - csz / 2)
            iso = 6
            p.drawRect(cx0, cy0, csz, csz)
            p.drawLine(cx0 + iso, cy0 - iso, cx0 + csz + iso, cy0 - iso)
            p.drawLine(cx0 + csz, cy0, cx0 + csz + iso, cy0 - iso)
            p.drawLine(cx0 + csz + iso, cy0 - iso,
                       cx0 + csz + iso, cy0 + csz - iso)
            p.drawLine(cx0 + csz, cy0 + csz, cx0 + csz + iso, cy0 + csz - iso)

        elif self._key == "refine":
            # Magnifying glass — refinement = inspecting + re-measuring outliers.
            # (Previous circular-arrow attempt had arrowhead-angle issues — this
            # geometry is foolproof and matches the analyse-then-re-measure flow.)
            import math
            lens_r = 22
            cx = s / 2 - 8
            cy = s / 2 - 8
            p.setPen(QPen(fg, stroke + 0.4))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawEllipse(int(cx - lens_r), int(cy - lens_r), 2 * lens_r, 2 * lens_r)
            # Handle — 45° line off the lower-right of the lens
            ang = math.radians(-45)
            hx0 = cx + lens_r * math.cos(ang)
            hy0 = cy - lens_r * math.sin(ang)
            hx1 = hx0 + 18
            hy1 = hy0 + 18
            p.setPen(QPen(fg, stroke + 1.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(hx0), int(hy0), int(hx1), int(hy1))
            # Magenta accent dot inside the lens — the outlier being inspected
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(int(cx - 5), int(cy - 5), 10, 10)

        elif self._key == "two_pass":
            # Two cubes side by side, second one filled magenta — first profile
            # becomes the pre-conditioning base for a higher-quality second one.
            csz = 24
            gap = 10
            x0 = (s - (2 * csz + gap + 10)) / 2
            y0 = (s - csz) / 2 + 3
            # Cube 1 — outline
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(int(x0), int(y0), csz, csz)
            p.drawLine(int(x0 + 5), int(y0 - 5), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz), int(y0), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz + 5), int(y0 - 5),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x0 + csz), int(y0 + csz),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            # Arrow between
            ay = y0 + csz / 2
            ax0 = x0 + csz + 7
            ax1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube 2 — filled magenta
            x1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke))
            p.setBrush(accent)
            p.drawRect(int(x1), int(y0), csz, csz)
            # Iso depth lines for cube 2 — slightly faded
            faded = QColor(accent)
            faded.setAlpha(170)
            p.setPen(QPen(faded, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawLine(int(x1 + 5), int(y0 - 5), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz), int(y0), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz + 5), int(y0 - 5),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x1 + csz), int(y0 + csz),
                       int(x1 + csz + 5), int(y0 + csz - 5))

        elif self._key == "improve_existing_profile":
            # Existing profile (filled grey cube) → arrow → improved profile
            # (outlined cube with magenta "+"). Distinct from two_pass which
            # uses two cubes both being internally produced; here the first
            # cube is the *given* input, not built in-app.
            csz = 24
            gap = 10
            x0 = (s - (2 * csz + gap + 10)) / 2
            y0 = (s - csz) / 2 + 3
            # Cube 1 — filled in fg colour (the seed profile you bring in)
            seed = QColor(fg)
            seed.setAlpha(180)
            p.setPen(QPen(fg, stroke))
            p.setBrush(seed)
            p.drawRect(int(x0), int(y0), csz, csz)
            faded = QColor(fg)
            faded.setAlpha(160)
            p.setPen(QPen(faded, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawLine(int(x0 + 5), int(y0 - 5), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz), int(y0), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz + 5), int(y0 - 5),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x0 + csz), int(y0 + csz),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            # Arrow
            ay = y0 + csz / 2
            ax0 = x0 + csz + 7
            ax1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube 2 — outlined in accent, with a magenta "+" inside
            x1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(int(x1), int(y0), csz, csz)
            p.drawLine(int(x1 + 5), int(y0 - 5), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz), int(y0), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz + 5), int(y0 - 5),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x1 + csz), int(y0 + csz),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            # "+" mark — improvement
            cx2 = x1 + csz / 2
            cy2 = y0 + csz / 2
            arm = 5
            p.setPen(QPen(accent, stroke + 0.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(cx2 - arm), int(cy2), int(cx2 + arm), int(cy2))
            p.drawLine(int(cx2), int(cy2 - arm), int(cx2), int(cy2 + arm))

        elif self._key == "check_visualise":
            # Isometric wireframe cube
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            cx, cy = s / 2, s / 2
            r = 30
            import math
            verts = [
                (cx, cy - r),               # top
                (cx + r * math.cos(math.radians(30)), cy - r * math.sin(math.radians(30))),  # right-top
                (cx + r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))),  # right-bot
                (cx, cy + r),               # bottom
                (cx - r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))),  # left-bot
                (cx - r * math.cos(math.radians(30)), cy - r * math.sin(math.radians(30))),  # left-top
            ]
            # Outer hex outline
            for i in range(6):
                a = verts[i]
                b = verts[(i + 1) % 6]
                p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
            # 3 inner spokes from centre to alternating verts
            for idx in (0, 2, 4):
                v = verts[idx]
                p.drawLine(int(cx), int(cy), int(v[0]), int(v[1]))
            # Accent vertex
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            v = verts[1]
            p.drawEllipse(int(v[0] - 5), int(v[1] - 5), 10, 10)

        p.end()


# ---------------------------------------------------------------------------
# Card widget (clickable)
# ---------------------------------------------------------------------------

class WorkflowCard(QFrame):
    """Clickable workflow tile — icon, title, one-line subtitle."""

    clicked = pyqtSignal(str)  # emits workflow key

    def __init__(self, workflow: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = workflow["key"]
        self._mode = "dark"
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(190)
        self.setMinimumWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)

        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        self._icon = WorkflowIcon(self._key, self)
        icon_row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(icon_row)

        self._title = QLabel(workflow["title"], self)
        self._title.setWordWrap(True)
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(f)
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._title)

        self._subtitle = QLabel(workflow["subtitle"], self)
        self._subtitle.setWordWrap(True)
        sf = QFont()
        sf.setPixelSize(11)
        self._subtitle.setFont(sf)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._subtitle)
        layout.addStretch(1)

        self._apply_style()

    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self._icon.set_appearance(self._mode)
        self._apply_style()

    def _apply_style(self) -> None:
        if self._mode == "light":
            bg = "#ffffff"
            border = "#d0ccc6"
            text = "#22211f"
            sub = "#7a7570"
        else:
            bg = "#1a1a1a"
            border = "#333333"
            text = "#e6e6e6"
            sub = "#8a8a8a"
        hover_border = SPEC_MAGENTA if self._hover else border
        self.setStyleSheet(
            f"""
            WorkflowCard {{
                background: {bg};
                border: 1.5px solid {hover_border};
                border-radius: 10px;
            }}
            WorkflowCard QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        self._title.setStyleSheet(f"color: {text};")
        self._subtitle.setStyleSheet(f"color: {sub};")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Tab number badge
# ---------------------------------------------------------------------------

class StepBadge(QLabel):
    """Step-number chip — the number is the step count; colour = tab.

    Optional steps render outlined (transparent fill + coloured ring) so they
    read as suggestions rather than required steps in the sequence.
    """

    def __init__(
        self,
        step_number: int,
        tab_index_1based: int,
        parent: QWidget | None = None,
        *,
        optional: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText(str(step_number))
        color = TAB_COLORS[(tab_index_1based - 1) % len(TAB_COLORS)]
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.Bold)
        self.setFont(f)
        if optional:
            # Outlined: ring in the tab colour, text in the tab colour,
            # transparent fill — visually quieter than a filled chip.
            self.setStyleSheet(
                f"background: transparent; color: {color}; "
                f"border-radius: 15px; border: 2px solid {color};"
            )
        else:
            self.setStyleSheet(
                f"background: {color}; color: #0a0a0a; "
                f"border-radius: 15px; border: none;"
            )


# ---------------------------------------------------------------------------
# Welcome dialog
# ---------------------------------------------------------------------------

class WelcomeDialog(QDialog):
    """Welcome menu + per-workflow instructions."""

    def __init__(
        self,
        settings: "AppSettings",
        parent: QWidget | None = None,
        initial_mode: str = "dark",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._mode = "light" if initial_mode == "light" else "dark"
        self.setWindowTitle("Welcome to ChromIQ")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setMinimumSize(870, 660)
        self._cards: list[WorkflowCard] = []
        self._build_ui()
        self.set_appearance(self._mode)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_menu_page())
        self._stack.addWidget(self._build_detail_page())
        self._stack.currentChanged.connect(self._on_page_changed)
        outer.addWidget(self._stack, stretch=1)

        # Work-in-progress disclaimer. Persistent across both pages; small,
        # italic, dimmed text — visible but not noisy.
        self._wip_note = QLabel(
            "These guides are still being polished — some details may not "
            "be fully accurate yet. When in doubt, trust what you see in "
            "the app over what you read here.",
            self,
        )
        self._wip_note.setObjectName("welcome_wip_note")
        self._wip_note.setWordWrap(True)
        self._wip_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wip_font = QFont()
        wip_font.setPixelSize(11)
        wip_font.setItalic(True)
        self._wip_note.setFont(wip_font)
        outer.addWidget(self._wip_note)

        # Footer — shared across both pages. Back button only shows on detail.
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self._show_cb = QCheckBox("Show this on startup", self)
        self._show_cb.setChecked(bool(self._settings.get("show_welcome_dialog", True)))
        self._show_cb.toggled.connect(
            lambda v: self._settings.set("show_welcome_dialog", bool(v))
        )
        footer.addWidget(self._show_cb)
        footer.addStretch(1)
        self._back_btn = QPushButton("← Back", self)
        self._back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._back_btn.setVisible(False)
        footer.addWidget(self._back_btn)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.accept)
        footer.addWidget(self._close_btn)
        outer.addLayout(footer)

    def _on_page_changed(self, index: int) -> None:
        self._back_btn.setVisible(index == 1)

    # ------------------------------------------------------------------
    def _build_menu_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # Heading
        heading = self._make_heading()
        v.addWidget(heading)

        self._subtitle = QLabel("What would you like to do?", page)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sf = QFont()
        sf.setPixelSize(15)
        self._subtitle.setFont(sf)
        v.addWidget(self._subtitle)

        # Card grid — 3 columns. Layout adapts to the workflow count:
        #   • 6 cards: 3+3
        #   • 7 cards: 3+3+1 (last centred)
        #   • 8 cards: 3+3+2 (last row at cols 0 and 2, col 1 empty for
        #     symmetry — mirrors the centred-bottom feel of the 7-card case)
        # Wrapped in a FadeScrollArea so the dialog can be shorter than the
        # full grid height; users scroll to reach lower workflows and the
        # edges fade to dialog bg instead of being cut by a hard line.
        grid_host = QWidget(page)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 8, 12, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        n_cards = len(WORKFLOWS)
        last_row_count = n_cards - 6 if n_cards > 6 else 0
        for i, wf in enumerate(WORKFLOWS):
            card = WorkflowCard(wf, grid_host)
            card.clicked.connect(self._on_card_clicked)
            self._cards.append(card)
            if i < 6:
                grid.addWidget(card, i // 3, i % 3)
            elif last_row_count == 1:
                # Solo extra card — centre column of the third row.
                grid.addWidget(card, 2, 1)
            else:
                # Two extra cards — sit at cols 0 and 2, leaving col 1 empty.
                grid.addWidget(card, 2, 0 if i == 6 else 2)

        self._menu_scroll = FadeScrollArea(page, surface="dialog")
        self._menu_scroll.setWidget(grid_host)
        self._menu_scroll.setWidgetResizable(True)
        self._menu_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._menu_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        v.addWidget(self._menu_scroll, stretch=1)
        return page

    # ------------------------------------------------------------------
    def _build_detail_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(12)

        self._detail_title = QLabel("", page)
        tf = QFont()
        tf.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
        tf.setPixelSize(32)
        tf.setWeight(QFont.Weight.Normal)
        self._detail_title.setFont(tf)
        self._detail_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self._detail_title)

        self._detail_subtitle = QLabel("", page)
        self._detail_subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._detail_subtitle.setWordWrap(True)
        ssf = QFont()
        ssf.setPixelSize(13)
        self._detail_subtitle.setFont(ssf)
        v.addWidget(self._detail_subtitle)

        # Steps in a scroll area
        self._steps_host = QWidget(page)
        self._steps_layout = QVBoxLayout(self._steps_host)
        self._steps_layout.setContentsMargins(20, 16, 20, 16)
        self._steps_layout.setSpacing(14)

        self._detail_scroll = FadeScrollArea(page, surface="dialog")
        self._detail_scroll.setWidget(self._steps_host)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(self._detail_scroll, stretch=1)

        return page

    # ------------------------------------------------------------------
    def _make_heading(self) -> QWidget:
        """Custom-painted 'Welcome to ChromIQ' wordmark with magenta IQ."""

        class _Heading(QWidget):
            def __init__(self, dialog: "WelcomeDialog") -> None:
                super().__init__(dialog)
                self._dialog = dialog
                self.setFixedHeight(72)

            def paintEvent(self, _ev):
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

                font_r = QFont()
                font_r.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
                font_r.setPixelSize(44)
                font_r.setWeight(QFont.Weight.Normal)

                font_i = QFont()
                font_i.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
                font_i.setPixelSize(44)
                font_i.setWeight(QFont.Weight.Bold)
                font_i.setItalic(True)

                fm_r = QFontMetricsF(font_r)
                fm_i = QFontMetricsF(font_i)
                text_pre = "Welcome to Chrom"
                text_iq  = "IQ"
                wpre = fm_r.horizontalAdvance(text_pre)
                wiq  = fm_i.horizontalAdvance(text_iq)
                total = wpre + wiq - 1
                x_start = (self.width() - total) / 2
                baseline = (self.height() + fm_r.ascent() - fm_r.descent()) / 2

                fg = "#22211f" if self._dialog._mode == "light" else "#ffffff"
                p.setFont(font_r)
                p.setPen(QColor(fg))
                p.drawText(int(x_start), int(baseline), text_pre)
                p.setFont(font_i)
                p.setPen(QColor(SPEC_MAGENTA))
                p.drawText(int(x_start + wpre - 1), int(baseline), text_iq)
                p.end()

        self._heading = _Heading(self)
        return self._heading

    # ------------------------------------------------------------------
    def _on_card_clicked(self, key: str) -> None:
        wf = next((w for w in WORKFLOWS if w["key"] == key), None)
        if wf is None:
            return
        self._detail_title.setText(wf["title"])
        self._detail_subtitle.setText(wf["subtitle"])
        # Clear previous step rows
        while self._steps_layout.count():
            item = self._steps_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Build new rows. Steps are (tab_idx, text) or (tab_idx, text, optional).
        for i, step in enumerate(wf["steps"], start=1):
            tab_idx, text = step[0], step[1]
            optional = bool(step[2]) if len(step) > 2 else False
            row = self._make_step_row(i, tab_idx, text, optional=optional)
            self._steps_layout.addWidget(row)
        self._steps_layout.addStretch(1)
        self._apply_detail_text_colors()
        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    def _make_step_row(
        self,
        number: int,
        tab_index: int,
        text: str,
        *,
        optional: bool = False,
    ) -> QWidget:
        row = QWidget(self._steps_host)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        badge = StepBadge(number, tab_index, row, optional=optional)
        h.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        body = QLabel(text, row)
        body.setWordWrap(True)
        bf = QFont()
        bf.setPixelSize(13)
        if optional:
            bf.setItalic(True)
        body.setFont(bf)
        body.setObjectName("welcome_step_body")
        # Tag the label so theme re-tinting can dim optional steps.
        body.setProperty("welcome_optional", optional)
        h.addWidget(body, stretch=1)
        return row

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        """Re-tint dialog chrome + propagate to children."""
        self._mode = "light" if mode == "light" else "dark"
        if self._mode == "light":
            dialog_bg = "#eeece8"     # match LM_BG_WINDOW
            sub_fg    = "#7a7570"
        else:
            dialog_bg = "#181818"     # match BG_PANEL — dark grey, not pure black
            sub_fg    = "#a8a4a0"
        # Dialog body. Override the global ACCENT (cyan/blue) for the checkbox
        # indicator so it picks up the spectrum-magenta accent of the welcome
        # dialog rather than the app-wide cyan/blue.
        self.setStyleSheet(
            f"""
            QDialog {{ background: {dialog_bg}; }}
            QDialog QLabel {{ background: transparent; }}
            QDialog QCheckBox::indicator:checked {{
                background: {SPEC_MAGENTA};
                border-color: {SPEC_MAGENTA};
            }}
            QDialog QCheckBox::indicator:hover {{
                border-color: {SPEC_MAGENTA};
            }}
            """
        )
        if hasattr(self, "_subtitle"):
            self._subtitle.setStyleSheet(f"color: {sub_fg};")
        if hasattr(self, "_detail_subtitle"):
            self._detail_subtitle.setStyleSheet(f"color: {sub_fg};")
        if hasattr(self, "_wip_note"):
            self._wip_note.setStyleSheet(f"color: {sub_fg};")
        for card in self._cards:
            card.set_appearance(self._mode)
        if hasattr(self, "_heading"):
            self._heading.update()
        if hasattr(self, "_menu_scroll"):
            self._menu_scroll.set_appearance(self._mode)
        if hasattr(self, "_detail_scroll"):
            self._detail_scroll.set_appearance(self._mode)
        self._apply_detail_text_colors()

    # ------------------------------------------------------------------
    def _apply_detail_text_colors(self) -> None:
        if not hasattr(self, "_steps_host"):
            return
        body_fg     = "#22211f" if self._mode == "light" else "#e6e6e6"
        optional_fg = "#7a7570" if self._mode == "light" else "#9a9a9a"
        title_fg = body_fg
        for lbl in self._steps_host.findChildren(QLabel, "welcome_step_body"):
            fg = optional_fg if bool(lbl.property("welcome_optional")) else body_fg
            lbl.setStyleSheet(f"color: {fg};")
        if hasattr(self, "_detail_title"):
            self._detail_title.setStyleSheet(f"color: {title_fg};")
