"""Measurement report viewer (Knut): accuracy statistics for a measured chart
and drift comparison over time.

Pick a measurement (.ti3); the dialog shows how the reading compares to the
chart's expected colours — mean / median / worst / spread ΔE00, the worst
patches with their colours, and the paper white and darkest black. "Save this
report" keeps a timestamped copy next to the chart so later measurements of
the same chart can be compared, revealing ink / printer / instrument drift.
"""
from __future__ import annotations

import html
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from core.i18n import tr
from core.logger import get_logger
from ui.fade_scroll import attach_edge_fades
from ui.styles import BG_INPUT, BORDER, SPEC_GREEN, TAB_COLORS, TEXT_MAIN
from ui.tab_header import dialog_masthead
from ui.theme import resolve_mode
from ui.tooltip_button import TooltipButton
from ui.widgets import open_file_dialog

log = get_logger(__name__)


# Cube-corner codes → human labels (lazy so tr() runs under the active language).
_CORNER_LABELS = {
    "W": lambda: tr("White"),
    "K": lambda: tr("Black"),
    "R": lambda: tr("Red"),
    "G": lambda: tr("Green"),
    "B": lambda: tr("Blue"),
    "C": lambda: tr("Cyan"),
    "M": lambda: tr("Magenta"),
    "Y": lambda: tr("Yellow"),
}

# Distinct, theme-legible line colours for each cube corner's trend line.
_CORNER_LINE = {
    "W": "#9a9a9a", "K": "#555555", "R": "#e23b3b", "G": "#33a94a",
    "B": "#3b6fe2", "C": "#1fb0b0", "M": "#c93bc9", "Y": "#c2a41f",
}

# The colour-accuracy metrics (Knut's revised set), keyed by report ``de00``
# field. Labels are lazy so tr() runs under the active language. ``_METRIC_LABELS``
# covers all six (Spread included); ``_ACCURACY_ROW_KEYS`` are the five that carry
# a Pass/Fail verdict, in display order; the trend chart plots those five.
_METRIC_LABELS = {
    "avg_all":   lambda: tr("Average ΔE, all patches"),
    "avg_low95": lambda: tr("Average ΔE, lowest 95%"),
    "avg_high5": lambda: tr("Average ΔE, highest 5%"),
    "max_all":   lambda: tr("Maximum ΔE, all patches"),
    "max_low95": lambda: tr("Maximum ΔE, lowest 95%"),
    "std":       lambda: tr("Spread (std. dev.)"),
}
_ACCURACY_ROW_KEYS = ("avg_all", "avg_low95", "avg_high5", "max_all", "max_low95")
# Line colour per accuracy metric for the colour-accuracy trend chart.
_METRIC_LINE = {
    "avg_all":   "#56d6a5", "avg_low95": "#37bcd6", "avg_high5": "#e0864b",
    "max_all":   "#e0574b", "max_low95": "#9f82ff",
}

# Section heading colour / a thin heading rule, shared by window + PDF.
_HEAD = "#2a2a2a"
# Max dated columns (runs) per table before it continues below — a portrait page
# fits six run columns plus the Metric column without the dates wrapping (Knut).
_MAX_RUN_COLS = 6


def _swatch(hexc: str) -> str:
    """A solid colour block for rich text. Qt ignores width/height on an empty
    span but honours background-color on a span WITH content, so we fill it with
    spaces hidden by matching the text colour to the fill."""
    c = html.escape(hexc or "#ffffff")
    return (f"<span style='background-color:{c};color:{c};"
            f"border:1px solid #999'>&nbsp;&nbsp;&nbsp;</span>")


def _colour_line_html(height: int = 5) -> str:
    """The ChromIQ five-part spectrum line as a full-width rich-text table row."""
    cells = "".join(
        f"<td width='20%' style='background:{c};font-size:1px;line-height:1px'>"
        f"&nbsp;</td>" for c in TAB_COLORS)
    return (f"<table width='100%' cellpadding='0' cellspacing='0' "
            f"style='height:{height}px;margin:0'><tr>{cells}</tr></table>")


def _h2(text: str, *, page_break: bool = False) -> str:
    """A main section heading, matching 'Trend over time (this printer)' etc."""
    brk = "page-break-before:always;" if page_break else ""
    return (f"<h2 style='color:{_HEAD};{brk}margin:14px 0 4px'>"
            f"{html.escape(text)}</h2>")


def _h3(text: str) -> str:
    return f"<h3 style='color:{_HEAD};margin:12px 0 3px'>{html.escape(text)}</h3>"


def _fmt(v, dec: int = 2) -> str:
    return f"{v:.{dec}f}" if isinstance(v, (int, float)) else "—"


class _TrendChart(QWidget):
    """A compact multi-line chart of a printer's measurement history over time
    (#40, Knut). Generic: each instance plots one GROUP of related metrics
    (ΔE00 accuracy, paper white/black, or the eight cube corners) so unlike
    scales never share an axis. A metric is ``(label, QColor, accessor)`` where
    ``accessor(point)`` returns the value or ``None``. Hidden until ≥2 points.
    ``unit_dec`` sets the y-label decimals; ``y_max`` optionally pins the top
    (e.g. 100 for L*)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: list[dict] = []
        self._metrics: list = []
        self._dark = True
        self._y_max: "float | None" = None
        self._dec = 1
        self._auto = False
        self.setMinimumHeight(210)

    def set_data(self, series, metrics, dark=True, y_max=None, dec=1,
                 auto=False) -> None:
        def has_any(pt) -> bool:
            return any(acc(pt) is not None for _, _, acc in metrics)
        self._series = [p for p in (series or []) if has_any(p)]
        self._metrics = metrics
        self._dark = dark
        self._y_max = y_max
        self._dec = dec
        # auto: range the axis tightly around the data (rounded to 0.1) instead of
        # anchoring at 0, so a small paper-white/black drift is actually visible
        # (Knut). ΔE charts keep their 0-anchored axis.
        self._auto = auto
        # NB: visibility is owned by the container (the tab widget), NOT the
        # chart — a per-widget setVisible here fought the tab stack and made all
        # three pages paint on top of each other before layout settled.
        self.update()

    def has_trend(self) -> bool:
        return len(self._series) >= 2

    def paintEvent(self, _ev) -> None:  # noqa: N802
        pts = self._series
        if len(pts) < 2:
            return
        fg = QColor(210, 210, 210) if self._dark else QColor(60, 60, 60)
        grid = QColor(255, 255, 255, 28) if self._dark else QColor(0, 0, 0, 22)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(); font.setPixelSize(10); p.setFont(font)

        import math
        L, R, T, B = 40.0, 12.0, 24.0, 26.0
        w = max(1.0, self.width() - L - R)
        h = max(1.0, self.height() - T - B)
        vals = [v for pt in pts for _, _, acc in self._metrics
                if (v := acc(pt)) is not None]
        if self._auto and vals:
            dmin, dmax = min(vals), max(vals)
            pad = 0.3 if (dmax - dmin) < 1e-9 else (dmax - dmin) * 0.15
            vmin = math.floor((dmin - pad) * 10.0) / 10.0
            vmax = math.ceil((dmax + pad) * 10.0) / 10.0
        else:
            vmin = 0.0
            vmax = self._y_max if self._y_max else max(vals + [1.0]) * 1.12
        span = max(1e-6, vmax - vmin)
        n = len(pts)

        def xy(i: int, val: float):
            return QPointF(L + (w * i / (n - 1)),
                           T + h * (1.0 - (val - vmin) / span))

        # Y grid + labels (bottom, mid, top of the actual range).
        p.setPen(QPen(grid, 1.0))
        for frac in (0.0, 0.5, 1.0):
            yy = T + h * (1.0 - frac)
            p.drawLine(QPointF(L, yy), QPointF(L + w, yy))
            p.setPen(QPen(fg, 1.0))
            p.drawText(QRectF(0, yy - 7, L - 4, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{vmin + span * frac:.{self._dec}f}")
            p.setPen(QPen(grid, 1.0))

        # One polyline per metric.
        for _lbl, col, acc in self._metrics:
            poly = [xy(i, v) for i, pt in enumerate(pts)
                    if (v := acc(pt)) is not None]
            if len(poly) < 2:
                continue
            p.setPen(QPen(col, 2.0))
            for a, b in zip(poly, poly[1:]):
                p.drawLine(a, b)
            p.setBrush(col); p.setPen(Qt.PenStyle.NoPen)
            for q in poly:
                p.drawEllipse(q, 2.4, 2.4)

        # X axis: a tick under EVERY measurement point plus as many dated labels
        # (YYYY-MM-DD) as fit without overlapping — always the first and last —
        # so you can read at WHICH date each change happened, not just the range
        # (Knut). Ticks mark every point even where the date label is skipped.
        axis_y = self.height() - B
        p.setPen(QPen(grid, 1.0))
        for i in range(n):
            x = L + (w * i / (n - 1))
            p.drawLine(QPointF(x, axis_y), QPointF(x, axis_y + 3))
        p.setPen(QPen(fg, 1.0))
        fm = p.fontMetrics()

        def _lab(i: int) -> str:
            return str(pts[i].get("created") or "")[:10]

        def _draw_date(left: float, text: str) -> None:
            p.drawText(QRectF(left, axis_y + 4, fm.horizontalAdvance(text) + 6, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, text)

        # Reserve the first (flush-left) and last (flush-right) dates, then fill
        # in as many intermediate dates as fit without overlapping either those
        # or each other — so the ends never collide (Knut).
        d0, dn = _lab(0), _lab(n - 1)
        w0, wn = fm.horizontalAdvance(d0), fm.horizontalAdvance(dn)
        last_left = L + w - wn
        _draw_date(L, d0)
        _draw_date(last_left, dn)
        occupied = [(L, L + w0), (last_left, last_left + wn)]
        for i in range(1, n - 1):
            d = _lab(i)
            tw = fm.horizontalAdvance(d)
            left = L + (w * i / (n - 1)) - tw / 2.0
            right = left + tw
            if all(right < a - 8 or left > b + 8 for a, b in occupied):
                _draw_date(left, d)
                occupied.append((left, right))

        # Legend (wraps across as many rows as needed for 8 corners).
        lx, ly = L + 4, T - 12
        for lbl, col, _acc in self._metrics:
            adv = 26 + fm.horizontalAdvance(lbl)
            if lx + adv > L + w:
                lx = L + 4; ly += 13
            p.setBrush(col); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(lx + 4, ly), 3.0, 3.0)
            p.setPen(QPen(fg, 1.0))
            p.drawText(QPointF(lx + 12, ly + 4), lbl)
            lx += adv
        p.end()


class MeasurementReportDialog(QDialog):
    def __init__(self, settings, parent=None, initial_ti3=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._report: dict | None = None
        self._ti3: Path | None = None
        self._trend_series: list = []
        self._history: list = []
        self._project_dirs: set = set()
        self.setWindowTitle(tr("Measurement Report"))
        self.setMinimumSize(760, 640)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        _help = tr(
            "What this tool does\n"
            "This report compares what your instrument measured on a printed "
            "chart against the colours the chart was designed to have, and turns "
            "it into a clear Pass/Fail verdict you can track over time. The real "
            "power is comparison: because the design reference never changes, the "
            "way the numbers move between dated reports of the same printer is a "
            "clean signal of drift — ageing inks, a printer slowly wandering, or "
            "an instrument going off.\n\n"
            "Two ways to use it\n"
            "  • Profiling runs — after building a profile, check how faithfully "
            "the chart reproduced.\n"
            "  • Verification runs — the most valuable habit: print a small chart "
            "THROUGH your finished profile (a colour-managed print, the "
            "“Verification measurement” option on the Measure tab), measure it "
            "every so often, and save a report each time. When the Pass/Fail "
            "results start slipping, that's your sign the printer has drifted far "
            "enough to re-profile. A tiny verification chart is enough — you're "
            "watching the trend, not building a profile.\n\n"
            "The sections\n"
            "  • Report Scope — which profiles and instruments are in the report, "
            "the run count and date range. IMPORTANT: the report cannot tell which "
            "printer a measurement came from. It is up to YOU to only include runs "
            "from the same printer. A good habit is a clear Printer Profile Name "
            "(set on the Create Chart tab) — e.g. include the printer and paper — "
            "so profiles from one printer are easy to pick out. As a safety net "
            "the report still warns you if the runs you loaded use different "
            "instruments, or if a chart is missing any of the eight cube corners "
            "(which would make its cube-corner figures unreliable).\n"
            "  • Report Results — a Pass/Fail grid: each colour-accuracy metric "
            "against each run. Green passes, red fails.\n"
            "  • Colour accuracy — the ΔE00 (colour difference) figures, split so "
            "the bulk of the chart (all patches, and the best 95 %) is separated "
            "from the few hardest patches (the worst 5 %). Each is judged against "
            "your Pass thresholds. 0 is perfect, 1–2 is barely visible, 10+ is "
            "clearly wrong.\n"
            "  • Trend over time — the same metrics plotted across every saved "
            "measurement, so a slow rise or a sudden jump stands out at a glance.\n"
            "  • Side-by-side comparison — every metric for every run in one table.\n"
            "  • Detailed data per run (optional) — the full breakdown for each "
            "run: the accuracy table, paper white & black, the cube corners and "
            "the sixteen worst patches.\n\n"
            "Pass thresholds\n"
            "You set two limits. The Average threshold (default 2.0 ΔE) judges the "
            "three average metrics; the Maximum threshold (default 3.0 ΔE) judges "
            "the two maximum metrics. A metric passes when it is at or below its "
            "limit. Tighten them for critical work, loosen them for a quick check.\n\n"
            "Options\n"
            "  • Show all measurement runs — the whole printer's history, not just "
            "the loaded one.\n"
            "  • Show detailed data for each run — add the per-run breakdown.\n"
            "  • Save report as PDF — a ChromIQ-styled PDF you can keep or share; "
            "it opens automatically. Reveal folder opens where it was saved.\n\n"
            "Using i1Profiler measurements\n"
            "You can feed this report measurements made in i1Profiler: export the "
            "measurement as a text file and convert it with the Tools menu's "
            "“Convert i1Profiler → TI3”. For the colour-accuracy figures the report "
            "also needs "
            "the chart's design reference — put the matching .ti2 next to the .ti3 "
            "(same file name). Without a .ti2 you still get paper white and black, "
            "but not the ΔE comparison.\n\n"
            "Screen and print colours here are approximate; the numbers come from "
            "your measurement file and are exact.")

        # Tool-style chrome: uppercase eyebrow + serif title + ⓘ over a
        # full-width spectrum stripe, green accent — the same look as the other
        # Tools windows. Zero side margins so the stripe runs edge to edge.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head, self._header, stripe = dialog_masthead(
            self, tr("MEASUREMENT · REPORT"), tr("Measurement Report"),
            tooltip_title=tr("Measurement report"), tooltip_body=_help,
            accent=SPEC_GREEN)
        outer.addLayout(head)
        outer.addWidget(stripe)

        v = QVBoxLayout()
        v.setContentsMargins(22, 14, 22, 16)
        v.setSpacing(12)
        outer.addLayout(v)

        intro = QLabel(tr(
            "See how accurately your printed chart was reproduced, and keep a "
            "dated report so you can compare measurements of the same chart "
            "over time."), self)
        intro.setWordWrap(True)
        v.addWidget(intro)

        # Two rows so the (now five) controls never clip: sourcing on top,
        # output below (Knut — beta.21 buttons were cut on both sides).
        btn_row = QHBoxLayout()
        self._open_btn = QPushButton(tr("Open another measurement (.ti3)…"), self)
        self._open_btn.clicked.connect(self._on_open)
        btn_row.addWidget(self._open_btn)
        self._add_btn = QPushButton(tr("Add another project's runs…"), self)
        self._add_btn.setToolTip(tr(
            "Fold another profile folder's saved measurements into the trend and "
            "PDF — for the SAME printer kept in a different project. Pick any "
            ".ti3 from that project."))
        self._add_btn.clicked.connect(self._on_add_project)
        self._add_btn.setEnabled(False)
        btn_row.addWidget(self._add_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        out_row = QHBoxLayout()
        self._pdf_btn = QPushButton(tr("Save report as PDF…"), self)
        self._pdf_btn.clicked.connect(self._export_pdf)
        self._pdf_btn.setEnabled(False)
        out_row.addWidget(self._pdf_btn)
        self._reveal_btn = QPushButton(tr("Reveal folder"), self)
        self._reveal_btn.clicked.connect(self._on_reveal)
        self._reveal_btn.setEnabled(False)
        out_row.addWidget(self._reveal_btn)
        out_row.addWidget(TooltipButton(
            tr("Reveal folder"),
            tr("Opens the profile's folder in your file manager, so you can "
               "browse to the reports folder and open any PDF you saved. When "
               "“Show all measurement runs” is on, reports are written to a "
               "reports folder next to the profile's runs; when it is off, they "
               "go in the loaded run's own reports folder."),
            self, color=SPEC_GREEN))
        out_row.addStretch(1)
        v.addLayout(out_row)

        # Report options — the window and the PDF always show the same thing (Knut).
        opt_row = QHBoxLayout()
        self._all_runs_check = QCheckBox(tr("Show all measurement runs"), self)
        self._all_runs_check.setChecked(True)
        self._all_runs_check.setToolTip(tr(
            "When on, the report covers every saved measurement of this printer "
            "(Report Scope, Report Results and the side-by-side comparison span "
            "them all). When off, only the loaded measurement is shown."))
        self._all_runs_check.toggled.connect(lambda _=None: self._render())
        opt_row.addWidget(self._all_runs_check)
        self._detail_check = QCheckBox(tr("Show detailed data for each run"), self)
        self._detail_check.setChecked(False)
        self._detail_check.setToolTip(tr(
            "When on, the full per-run breakdown — colour-accuracy Pass/Fail "
            "table, paper white & black, cube corners and the worst patches — is "
            "added for every run, in the window and in the PDF."))
        self._detail_check.toggled.connect(lambda _=None: self._render())
        opt_row.addWidget(self._detail_check)
        opt_row.addStretch(1)
        v.addLayout(opt_row)

        # Pass/Fail thresholds — the average threshold judges the three average
        # metrics, the maximum threshold the two maximum metrics (Knut).
        from ui.widgets import NoScrollDoubleSpinBox
        from workflow.measurement_report import DEFAULT_PASS_AVG, DEFAULT_PASS_MAX
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel(tr("Pass threshold — Average:"), self))
        self._avg_thr_spin = NoScrollDoubleSpinBox(self)
        self._avg_thr_spin.setDecimals(1); self._avg_thr_spin.setRange(0.1, 100.0)
        self._avg_thr_spin.setSingleStep(0.5); self._avg_thr_spin.setSuffix(" ΔE")
        self._avg_thr_spin.setValue(DEFAULT_PASS_AVG)
        self._avg_thr_spin.valueChanged.connect(lambda _=None: self._render())
        thr_row.addWidget(self._avg_thr_spin)
        thr_row.addSpacing(14)
        thr_row.addWidget(QLabel(tr("Maximum:"), self))
        self._max_thr_spin = NoScrollDoubleSpinBox(self)
        self._max_thr_spin.setDecimals(1); self._max_thr_spin.setRange(0.1, 100.0)
        self._max_thr_spin.setSingleStep(0.5); self._max_thr_spin.setSuffix(" ΔE")
        self._max_thr_spin.setValue(DEFAULT_PASS_MAX)
        self._max_thr_spin.valueChanged.connect(lambda _=None: self._render())
        thr_row.addWidget(self._max_thr_spin)
        thr_row.addWidget(TooltipButton(
            tr("Pass thresholds"),
            tr("The colour-accuracy verdict. A metric passes when its measured "
               "ΔE00 is at or below its threshold. The Average threshold is "
               "compared against the three average metrics (all patches, the best "
               "95%, and the worst 5%); the Maximum threshold against the two "
               "maximum metrics (all patches, and the best 95%). Typical starting "
               "points are 2.0 for the average and 3.0 for the maximum — tighten "
               "them for critical work, loosen them for a quick health check."),
            self, color=SPEC_GREEN))
        thr_row.addStretch(1)
        v.addLayout(thr_row)

        self._trend_label = QLabel(tr("Trend over time (this printer)"), self)
        self._trend_label.setStyleSheet("font-weight:bold;margin-top:2px")
        self._trend_label.setVisible(False)
        v.addWidget(self._trend_label)
        # Unlike-scaled metrics can't share one axis (Knut), so group them into
        # separate tabbed charts. Paper white (~L*100) and black (~L*10) are too
        # far apart to read a trend on one axis, so they get a chart each.
        self._trend_tabs = QTabWidget(self)
        self._trend_de = _TrendChart(self)
        self._trend_white = _TrendChart(self)
        self._trend_black = _TrendChart(self)
        self._trend_corners = _TrendChart(self)
        self._trend_tabs.addTab(self._trend_de, tr("Colour accuracy (ΔE00)"))
        self._trend_tabs.addTab(self._trend_white, tr("Paper white (L*)"))
        self._trend_tabs.addTab(self._trend_black, tr("Darkest black (L*)"))
        self._trend_tabs.addTab(self._trend_corners, tr("Cube corners"))
        self._trend_tabs.setVisible(False)
        v.addWidget(self._trend_tabs)

        self._view = QTextBrowser(self)
        self._view.setOpenExternalLinks(False)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setHtml(self._empty_html())
        v.addWidget(self._view, 1)
        # The report view scrolls internally — give it the same fade-to-surface
        # gradient the Tools dialogs use on their scroll areas.
        self._view_fades = attach_edge_fades(self._view, surface="dialog")
        self._view_fades.set_appearance(
            resolve_mode(self._settings.get("appearance", "auto")))

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton(tr("Close"), self)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        v.addLayout(close_row)

        # Controls take the window's own green accent (checked checkbox + focus
        # rings), like the Ti1→i1Profiler tool uses its masthead accent, instead
        # of the global tab cyan; in dark mode match the report view to the input
        # background so it isn't darker than the chrome.
        mode = resolve_mode(self._settings.get("appearance", "auto"))
        from ui.dialogs.tools_dialogs import neutral_controls_qss
        qss = neutral_controls_qss(SPEC_GREEN)
        if mode == "dark":
            qss += (f"QTextBrowser {{ background: {BG_INPUT}; color: {TEXT_MAIN};"
                    f" border: 1px solid {BORDER}; border-radius: 3px; }}")
        self.setStyleSheet(qss)

        if initial_ti3 is not None and Path(initial_ti3).exists():
            self._load(Path(initial_ti3))

    # ------------------------------------------------------------------
    def _on_open(self) -> None:
        path = open_file_dialog(
            self, tr("Open a measurement"), tr("Measurement files (*.ti3)"),
            extra_path=self._settings.get("custom_output_path", ""))
        if not path:
            return
        self._load(Path(path))

    def _load(self, path: Path) -> None:
        from workflow.measurement_report import (
            build_report, list_project_reports,
        )
        import json
        try:
            self._report = build_report(path)
        except Exception as exc:  # noqa: BLE001
            self._view.setHtml(self._error_html(str(exc)))
            return
        self._ti3 = Path(path)
        # The printer's full history: every saved report across all runs of this
        # project, oldest first (#40) — the runs the report and trend cover.
        history: list[dict] = []
        for p in list_project_reports(self._ti3.parent):
            try:
                history.append(json.loads(p.read_text()))
            except Exception:  # noqa: BLE001
                continue
        self._history = history                 # every saved run, for the report
        self._project_dirs = {self._ti3.parent}  # folders folded into the trend

        self._refresh_trend()
        self._render()
        self._pdf_btn.setEnabled(True)
        self._add_btn.setEnabled(True)
        self._reveal_btn.setEnabled(True)

    def _render(self) -> None:
        """(Re)build the on-screen report — the SAME body sequence as the PDF,
        minus the trend charts (they're the tabs above). Called on load and
        whenever a control that changes the report changes (Knut)."""
        if not self._report:
            self._view.setHtml(self._empty_html())
            return
        self._view.setHtml(
            self._report_body_html(self._runs_for_report(), for_pdf=False))

    def _refresh_trend(self) -> None:
        """Recompute the trend series from the current history (which may span
        several project folders once the user has added some) and repaint."""
        from ui.theme import resolve_mode
        from workflow.measurement_report import report_trend
        dark = resolve_mode(self._settings.get("appearance", "auto")) != "light"
        # Oldest-first by the report's own date, across whatever folders are in.
        self._history.sort(key=lambda r: str(r.get("created") or ""))
        self._trend_series = report_trend(self._history)
        self._update_trends(self._trend_series, dark)

    def _on_add_project(self) -> None:
        """Manual multi-printer: fold another profile folder's saved reports into
        the trend + PDF (for the same printer kept in a different project)."""
        import json
        from workflow.measurement_report import list_project_reports
        path = open_file_dialog(
            self, tr("Add a project (pick any of its .ti3 files)"),
            tr("Measurement files (*.ti3)"),
            extra_path=self._settings.get("custom_output_path", ""))
        if not path:
            return
        run_dir = Path(path).parent
        if run_dir in self._project_dirs:
            return                                # already included
        self._project_dirs.add(run_dir)
        seen = {(r.get("chart"), r.get("created")) for r in self._history}
        added = 0
        for p in list_project_reports(run_dir):
            try:
                r = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            if (r.get("chart"), r.get("created")) not in seen:
                self._history.append(r)
                seen.add((r.get("chart"), r.get("created")))
                added += 1
        self._refresh_trend()
        self._render()
        self._add_btn.setText(tr("Added — {n} projects in trend").format(
            n=len(self._project_dirs)))

    def _trend_configs(self) -> list:
        """The four grouped charts as ``(chart, title, metrics, y_max, dec, auto)``
        — shared by the live tabs and the PDF export so they always match. ``auto``
        ranges the axis tightly around the data instead of anchoring at 0."""
        corner_metrics = [
            (_CORNER_LABELS[code](), QColor(_CORNER_LINE[code]),
             (lambda pt, c=code: (pt.get("corners") or {}).get(c)))
            for code in ("W", "K", "R", "G", "B", "C", "M", "Y")
        ]
        return [
            (self._trend_de, tr("Colour accuracy (ΔE00)"), [
                (_METRIC_LABELS[k](), QColor(_METRIC_LINE[k]),
                 (lambda pt, kk=k: pt.get(kk)))
                for k in _ACCURACY_ROW_KEYS
            ], None, 1, False),
            # White (~L*100) and black (~L*10) are too far apart to share an axis
            # (Knut), so each is its own auto-scaled chart — and the axis ranges
            # tightly around the values (not from 0) so a small drift is visible.
            (self._trend_white, tr("Paper white (L*)"), [
                (tr("Paper white L*"), QColor("#8a8a8a"), lambda pt: pt.get("white_L")),
            ], None, 1, True),
            (self._trend_black, tr("Darkest black (L*)"), [
                (tr("Black L*"), QColor("#505050"), lambda pt: pt.get("black_L")),
            ], None, 1, True),
            (self._trend_corners, tr("Cube corners (ΔE00 per ink)"),
             corner_metrics, None, 1, False),
        ]

    def _profile_root(self) -> Path:
        """The profile's project folder (``<project>/runs/<id>`` → ``<project>``),
        or the run folder itself for a browsed external ``.ti3`` that isn't in a
        ChromIQ project layout."""
        run_dir = self._ti3.parent if self._ti3 else Path.cwd()
        if run_dir.parent.name == "runs":
            return run_dir.parents[1]
        return run_dir

    def _report_dir(self) -> Path:
        """Where a PDF is saved (Knut): an all-runs report belongs to the whole
        profile, so it goes in a ``reports`` folder next to ``runs/``; a single-run
        report goes in that run's own ``reports`` folder."""
        from core.file_manager import reports_subdir
        run_dir = self._ti3.parent
        if self._all_runs_check.isChecked() and run_dir.parent.name == "runs":
            return reports_subdir(self._profile_root())
        return reports_subdir(run_dir)

    def _on_reveal(self) -> None:
        """Open the profile's folder in the file manager so the user can browse to
        the reports folder and open saved PDFs (Knut)."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        if self._ti3:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._profile_root())))

    def _export_pdf(self) -> None:
        """Write the full report — all data, the trend charts and a plain-language
        guide to reading them — as a PDF, then open it for viewing (Knut)."""
        if not self._report or not self._ti3:
            return
        from datetime import datetime
        from PyQt6.QtCore import QMarginsF, QRectF, QSizeF, QUrl
        from PyQt6.QtGui import (
            QAbstractTextDocumentLayout, QColor, QDesktopServices, QFont,
            QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument,
        )

        reports = self._report_dir()
        reports.mkdir(parents=True, exist_ok=True)
        default = reports / (
            f"measurement_report_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save report as PDF"), str(default), "PDF (*.pdf)")
        if not path:
            return

        doc = QTextDocument()
        charts_html = ""
        if self._trend_de.has_trend():
            # Render each grouped chart off-screen (the live tabs only lay out the
            # current one) and embed it as a resource. Kept compact so all four
            # trend charts fit on the one trend page (Knut).
            for i, (_c, title, metrics, y_max, dec, auto) in enumerate(self._trend_configs()):
                tmp = _TrendChart()
                tmp.resize(640, 176)
                tmp.set_data(self._trend_series, metrics, dark=False,
                             y_max=y_max, dec=dec, auto=auto)
                img = tmp.grab().toImage()
                url = QUrl(f"chart://{i}")
                doc.addResource(QTextDocument.ResourceType.ImageResource, url, img)
                charts_html += (f"<h3 style='margin:4px 0 0'>{html.escape(title)}</h3>"
                                f"<img src='chart://{i}' width='600'>")
        # The exact same run set the window shows, so the PDF matches it (Knut).
        runs = self._runs_for_report()
        doc.setHtml(self._pdf_html(runs, charts_html))

        from PyQt6.QtGui import QFontMetricsF

        writer = QPdfWriter(str(path))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        # 15 mm all round keeps the wordmark ≥ 1.5 cm from the paper edge (Knut).
        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
        # QPdfWriter defaults to a very high resolution, but the document is laid
        # out in ~96-dpi pixels (font px, img widths), so match the writer to the
        # document's 96-dpi coordinate space; text stays vector-crisp regardless.
        writer.setResolution(96)
        page_w, page_h = float(writer.width()), float(writer.height())
        # Header band (wordmark + per-page scope + colour line) at the top of the
        # printable area, footer band (page number) at the bottom. 34 px ≈ 9 mm,
        # so with the 15 mm margin the top margin stays under 2.5 cm (Knut).
        header_h, footer_h = 34.0, 22.0
        body_h = page_h - header_h - footer_h
        doc.setPageSize(QSizeF(page_w, body_h))

        units = self._scope_header_units(runs)   # profile names + measurements/date
        head_font = QFont(); head_font.setPixelSize(8)
        foot_font = QFont(); foot_font.setPixelSize(10)
        # The ChromIQ wordmark, exactly as the app masthead draws it: "Chrom" in
        # Instrument Serif near-black, "IQ" bold-italic in the magenta accent.
        wm_r = QFont(); wm_r.setPixelSize(22)
        wm_r.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
        wm_i = QFont(wm_r); wm_i.setBold(True); wm_i.setItalic(True)
        wm_fr, wm_fi = QFontMetricsF(wm_r), QFontMetricsF(wm_i)
        wm_chrom_w = wm_fr.horizontalAdvance("Chrom")
        wm_iq_w = wm_fi.horizontalAdvance("IQ")

        def draw_wordmark() -> None:
            x = page_w - (wm_chrom_w + wm_iq_w)
            base = 1.0 + wm_fr.ascent()
            painter.save()
            painter.setFont(wm_r); painter.setPen(QColor("#1c1b18"))
            painter.drawText(QPointF(x, base), "Chrom")
            painter.setFont(wm_i); painter.setPen(QColor("#ff4573"))
            painter.drawText(QPointF(x + wm_chrom_w - 1.0, base), "IQ")
            painter.restore()

        painter = QPainter(writer)
        layout = doc.documentLayout()
        total = max(1, doc.pageCount())

        def draw_header(pg: int) -> None:
            draw_wordmark()
            if pg == 0:
                return                            # page 1: wordmark only (line is in body)
            # Scope text, left, wrapped by whole units within the width left of
            # the wordmark; and the five-part colour line along the band's bottom.
            painter.save()
            painter.setPen(QColor(90, 90, 90)); painter.setFont(head_font)
            fm = painter.fontMetrics()
            max_w = page_w - (wm_chrom_w + wm_iq_w) - 14.0
            x, y, line_h = 0.0, 8.0, fm.height() + 1.0
            for u in units:
                w = fm.horizontalAdvance(u)
                if x > 0 and x + w > max_w:
                    x = 0.0; y += line_h
                    if y > header_h - 8.0:        # keep it inside the band
                        break
                painter.drawText(QPointF(x, y + fm.ascent()), u)
                x += w
            painter.restore()
            seg = page_w / 5.0
            for i, col in enumerate(TAB_COLORS):
                painter.fillRect(QRectF(i * seg, header_h - 4.0, seg, 3.0), QColor(col))

        for pg in range(total):
            if pg > 0:
                writer.newPage()
            draw_header(pg)
            painter.save()
            painter.translate(0.0, header_h - pg * body_h)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.clip = QRectF(0, pg * body_h, page_w, body_h)
            layout.draw(painter, ctx)
            painter.restore()
            painter.save()
            painter.setPen(QColor(120, 120, 120)); painter.setFont(foot_font)
            painter.drawText(
                QRectF(0, page_h - footer_h + 2, page_w, footer_h - 2),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                tr("Page {n} of {total}").format(n=pg + 1, total=total))
            painter.restore()
        painter.end()

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _scope_header_units(self, runs: list) -> list:
        """The per-page header units (Knut): each profile name in quotes, then the
        total measurement count + date range as one unit — wrapped by whole units
        in the painter so a long list flows onto a second header line."""
        from workflow.measurement_report import report_scope
        sc = report_scope(runs)
        profs = sc["profiles"]
        units = [f'“{p["name"]}”' + ("," if i < len(profs) - 1 else "")
                 for i, p in enumerate(profs)]
        d0, d1 = sc["date_range"]
        units.append("  " + tr("{n} measurements").format(n=sc["total"])
                     + f" ({d0} – {d1})")
        # Add a trailing space to each name unit so they don't run together.
        return [(u + " ") if u.endswith(",") else u for u in units]

    # ---- report composition (shared by the window and the PDF) --------------
    _ZEBRA_BG = "#f2f2f2"

    def _thresholds(self) -> "tuple[float, float]":
        """The (average, maximum) ΔE00 Pass thresholds from the input fields, or
        the module defaults before those fields are built."""
        from workflow.measurement_report import DEFAULT_PASS_AVG, DEFAULT_PASS_MAX
        avg = getattr(self, "_avg_thr_spin", None)
        mx = getattr(self, "_max_thr_spin", None)
        return (float(avg.value()) if avg is not None else DEFAULT_PASS_AVG,
                float(mx.value()) if mx is not None else DEFAULT_PASS_MAX)

    def _runs_for_report(self) -> list:
        """Every saved run of the loaded printer(s) when 'Show all measurement
        runs' is on, else just the loaded one. The same list drives the window
        and the PDF, so they always match (worst-patch count included, Knut)."""
        if (getattr(self, "_all_runs_check", None) is not None
                and self._all_runs_check.isChecked() and self._history):
            return list(self._history)
        return [self._report] if self._report else []

    def _metric_table(self, dates: list, data_rows: list) -> str:
        """One metric×run table: a wide, no-wrap Metric column, dated run columns,
        a rule under the header row and a light-grey background on every other
        data row (Knut)."""
        thb = "border-bottom:1.5px solid #bbb;white-space:nowrap"
        th = ("<tr><th align='left' style='" + thb + ";padding:2px 14px 3px 0'>"
              + html.escape(tr("Metric")) + "</th>"
              + "".join("<th align='right' style='" + thb + ";padding:2px 8px 3px'>"
                        + html.escape(d) + "</th>" for d in dates) + "</tr>")
        body = [th]
        for i, (label, cells) in enumerate(data_rows):
            bg = f" style='background:{self._ZEBRA_BG}'" if i % 2 == 1 else ""
            body.append(f"<tr{bg}><td style='white-space:nowrap;padding-right:14px'>"
                        + html.escape(label) + "</td>" + "".join(cells) + "</tr>")
        return ("<table cellpadding='4' cellspacing='0' style='border-collapse:"
                "collapse;font-size:11px;margin-bottom:10px'>"
                + "".join(body) + "</table>")

    def _chunked_metric_tables(self, runs: list, row_getters: list) -> str:
        """Stacked metric×run tables, at most :data:`_MAX_RUN_COLS` dated columns
        each, continuing below with the Metric column repeated; oldest run first."""
        out = []
        for i in range(0, len(runs), _MAX_RUN_COLS):
            chunk = runs[i:i + _MAX_RUN_COLS]
            dates = [str(r.get("created") or "")[:10] for r in chunk]
            rows = [(label, [get(r) for r in chunk]) for label, get in row_getters]
            out.append(self._metric_table(dates, rows))
        return "".join(out)

    def _scope_html(self, runs: list) -> str:
        """Report Scope (Knut): which profiles + instruments are included, the run
        count and date range, and red warnings for mixed instruments or missing
        cube colours."""
        from workflow.measurement_report import report_scope
        sc = report_scope(runs)
        items = "".join(
            "<li>“" + html.escape(p["name"]) + "”, "
            + html.escape(tr("Instrument: {inst}").format(inst=p["instrument"]))
            + f" <span style='color:#888'>· {p['n']} " + html.escape(tr("runs"))
            + "</span></li>"
            for p in sc["profiles"])
        d0, d1 = sc["date_range"]
        ind = "margin:0 0 0 1.6em"
        out = (_h2(tr("Report Scope"))
               + "<div>" + html.escape(
                   tr("The following profile(s) measurement runs are included:"))
               + "</div><ul style='margin:2px 0 6px'>" + items + "</ul>"
               + "<div><b>" + html.escape(tr("No. of Measurements:")) + "</b></div>"
               + f"<div style='{ind}'>{sc['total']}</div>"
               + "<div><b>" + html.escape(tr("Date range:")) + "</b></div>"
               + f"<div style='{ind}'>{html.escape(d0)} – {html.escape(d1)}</div>")
        return out + self._scope_warnings_html(sc["warnings"])

    def _scope_warnings_html(self, warnings: list) -> str:
        """Red warning block for the Report Scope checks (Knut). Empty when clean."""
        if not warnings:
            return ""
        blocks = []
        for w in warnings:
            if w["kind"] == "instrument":
                lis = "".join(
                    "<li>" + html.escape(o["run"]) + " — "
                    + html.escape(tr("uses {inst}").format(inst=o["instrument"]))
                    + "</li>" for o in w["runs"])
                blocks.append(
                    "<div><b>" + html.escape(tr("Warning — mixed instruments.")) + "</b> "
                    + html.escape(tr(
                        "Every run in a report should come from the same instrument "
                        "and the same printer; the report cannot tell printers apart. "
                        "These runs use a different instrument from the majority "
                        "({dom}):").format(dom=w["dominant"]))
                    + "</div><ul>" + lis + "</ul>")
            elif w["kind"] == "corners":
                lis = "".join(
                    "<li>" + html.escape(o["run"]) + " — "
                    + html.escape(tr("missing {names}").format(
                        names=", ".join(_CORNER_LABELS.get(n, (lambda n=n: n))()
                                        for n in o["missing"])))
                    + "</li>" for o in w["runs"])
                blocks.append(
                    "<div><b>" + html.escape(tr("Warning — missing cube colours.")) + "</b> "
                    + html.escape(tr(
                        "These runs are missing one or more of the eight cube "
                        "corners, so their cube-corner figures are less meaningful:"))
                    + "</div><ul>" + lis + "</ul>")
        return ("<div style='color:#c0392b;margin-top:10px'>"
                + "".join(blocks) + "</div>")

    def _how_to_read_html(self) -> str:
        """The plain-language guide, boxed like before (Knut keeps it up front)."""
        guide = (
            _h2(tr("How to read this report"))
            + "<p>" + html.escape(tr(
                "This report compares what your instrument measured against the "
                "colours the chart was designed to have. Every number is a colour "
                "difference (ΔE00): 0 is a perfect match, 1–2 is barely visible, "
                "and 10+ is clearly wrong.")) + "</p>"
            "<ul>"
            "<li>" + html.escape(tr(
                "Colour accuracy — the ΔE00 across the patches, split so you can "
                "see the bulk of the chart (all patches and the best 95%) apart "
                "from the few hardest patches (the worst 5%). Each metric is judged "
                "against your Pass thresholds.")) + "</li>"
            "<li>" + html.escape(tr(
                "Paper white & darkest black — the brightest and deepest patches "
                "(L*), a quick health check of your paper and maximum ink.")) + "</li>"
            "<li>" + html.escape(tr(
                "Cube corners — paper white, composite black and the six primary "
                "and secondary inks. These say as much about your inks as about "
                "the instrument.")) + "</li>"
            "</ul>"
            "<p>" + html.escape(tr(
                "On a printer a single ΔE against the design isn't meaningful on "
                "its own — a printer doesn't reproduce sRGB. But the design "
                "reference never changes, so comparing dated reports of the same "
                "chart is a clean drift signal. Save a report after each "
                "measurement to build that history. Screen and print colours here "
                "are approximate; the numbers come from your measurement file and "
                "are exact.")) + "</p>")
        return ("<table width='100%' cellpadding='12' cellspacing='0'>"
                "<tr><td style='background:#f4f7f6'>" + guide + "</td></tr></table>")

    def _report_results_html(self, runs: list) -> str:
        """Report Results: a Pass/Fail grid, rows = the five threshold metrics,
        columns = dated runs (≤6 per table, continuing below). Pass green, Fail
        red (Knut)."""
        from workflow.measurement_report import accuracy_verdict
        avg_thr, max_thr = self._thresholds()
        verd = {id(r): {x["key"]: x["pass"]
                        for x in accuracy_verdict(r.get("de00") or {}, avg_thr, max_thr)[0]}
                for r in runs}

        def pf(r, key):
            p = verd[id(r)].get(key)
            if p is None:
                return "<td align='center'>—</td>"
            col = "#1e8e3e" if p else "#c0392b"
            txt = tr("Pass") if p else tr("Fail")
            return (f"<td align='center' style='color:{col};font-weight:bold'>"
                    f"{html.escape(txt)}</td>")

        row_getters = [(_METRIC_LABELS[k](), (lambda r, k=k: pf(r, k)))
                       for k in _ACCURACY_ROW_KEYS]
        return (_h2(tr("Report Results"))
                + "<div style='color:#555;margin-bottom:4px'>" + html.escape(tr(
                    "The following results are extracted from section “Colour "
                    "accuracy” for each measurement run included for this report."))
                + "</div>" + self._chunked_metric_tables(runs, row_getters))

    def _comparison_table_html(self, runs: list) -> str:
        """Side-by-side: the full metric set across every run (columns = dated
        runs, ≤6 per table). Zebra rows, header rule, wide Metric column (Knut)."""
        de = lambda r: (r.get("de00") or {})

        def num(getter, dec):
            return lambda r: f"<td align='right'>{_fmt(getter(r), dec)}</td>"

        def corner_de(r, code):
            for cc in (r.get("corners") or []):
                if cc.get("name") == code:
                    return cc.get("de")
            return None

        row_getters = [(_METRIC_LABELS[k](), num((lambda r, k=k: de(r).get(k)), 2))
                       for k in ("avg_all", "avg_low95", "avg_high5",
                                 "max_all", "max_low95", "std")]
        row_getters += [
            (tr("Paper white L*"),
             num(lambda r: (r.get("paper_white") or {}).get("lab", [None])[0], 1)),
            (tr("Black L*"),
             num(lambda r: (r.get("max_black") or {}).get("lab", [None])[0], 1)),
        ]
        for code in ("W", "K", "R", "G", "B", "C", "M", "Y"):
            lbl = tr("{corner} ΔE00").format(corner=_CORNER_LABELS[code]())
            row_getters.append((lbl, num((lambda r, c=code: corner_de(r, c)), 2)))
        return (_h2(tr("Side-by-side comparison"), page_break=True)
                + self._chunked_metric_tables(runs, row_getters))

    def _report_body_html(self, runs: list, *, for_pdf: bool,
                          charts_html: str = "") -> str:
        """The full report body, shared by the window and the PDF in ONE sequence
        (Knut): title/heading → Report Scope → How to read → Report Results →
        trend charts (PDF) → Side-by-side (>1 run) → Detailed (opt-in)."""
        if not runs:
            return self._empty_html()
        total = len(runs)
        d0 = str(runs[0].get("created") or "")[:10]
        d1 = str(runs[-1].get("created") or "")[:10]
        span = ""
        if total > 1:
            span = (" &nbsp;·&nbsp; " + tr("{n} measurements").format(n=total)
                    + f" ({html.escape(d0)} – {html.escape(d1)})")
        # ── heading ──
        if for_pdf:
            head = (f"<div style='font-size:22px;font-weight:bold;color:{_HEAD}'>"
                    + html.escape(tr("Measurement Report")) + "</div>"
                    f"<div style='font-size:12px;color:#777;margin:2px 0 4px'>"
                    + html.escape(runs[0].get("chart") or "") + span + "</div>"
                    + _colour_line_html() + "<br>")
        else:
            head = (f"<h2 style='margin:0 0 2px'>"
                    + html.escape(runs[0].get("chart") or "") + "</h2>"
                    f"<div style='color:#888;font-size:12px'>"
                    + tr("{n} measurements").format(n=total)
                    + f" ({html.escape(d0)} – {html.escape(d1)})</div>")
        parts = [head, self._scope_html(runs), self._how_to_read_html(),
                 self._report_results_html(runs)]
        if for_pdf and charts_html:
            parts.append(
                _h2(tr("Trend over time (this printer)"), page_break=True)
                + "<div style='color:#555;margin-bottom:6px'>" + html.escape(tr(
                    "A rising average or shifting white/black/colour over time "
                    "points to ageing inks, printer drift, or instrument drift."))
                + "</div>" + charts_html)
        if total > 1:
            parts.append(self._comparison_table_html(runs))
        if getattr(self, "_detail_check", None) is not None \
                and self._detail_check.isChecked():
            parts.append(self._detailed_section_html(runs))
        return ("<div style='font-family:sans-serif;color:#333;font-size:12px'>"
                + "".join(parts) + "</div>")

    def _pdf_html(self, runs: list, charts_html: str) -> str:
        return self._report_body_html(runs, for_pdf=True, charts_html=charts_html)

    def _update_trends(self, series: list, dark: bool) -> None:
        """Feed the three grouped trend charts their own metric sets (#40, Knut)."""
        for chart, _title, metrics, y_max, dec, auto in self._trend_configs():
            chart.set_data(series, metrics, dark=dark, y_max=y_max, dec=dec,
                           auto=auto)
        has = self._trend_de.has_trend()
        self._trend_label.setVisible(has)
        self._trend_tabs.setVisible(has)

    # ------------------------------------------------------------------
    def _empty_html(self) -> str:
        return ("<div style='color:#888;padding:24px'>"
                + html.escape(tr("Open a measurement file to see its report."))
                + "</div>")

    def _error_html(self, msg: str) -> str:
        return ("<div style='color:#d9534f;padding:24px'>"
                + html.escape(tr("Could not read this measurement: {msg}")
                              .format(msg=msg)) + "</div>")

    def _run_detail_html(self, r: dict) -> str:
        """One run's full breakdown: the colour-accuracy Pass/Fail table
        (Metric / Measured ΔE00 / Threshold / Result), paper white & black, the
        cube corners, and the 16 worst patches (Knut)."""
        de = r.get("de00") or {}
        parts = []
        if de.get("avg_all") is not None:
            from workflow.measurement_report import accuracy_verdict
            avg_thr, max_thr = self._thresholds()
            rows, _ = accuracy_verdict(de, avg_thr, max_thr)
            head = ("<tr style='color:#888'>"
                    "<th align='left' style='border-bottom:1.5px solid #bbb'>"
                    + html.escape(tr("Metric")) + "</th>"
                    "<th align='right' style='border-bottom:1.5px solid #bbb'>"
                    + html.escape(tr("Measured ΔE00")) + "</th>"
                    "<th align='right' style='border-bottom:1.5px solid #bbb'>"
                    + html.escape(tr("Threshold")) + "</th>"
                    "<th align='center' style='border-bottom:1.5px solid #bbb'>"
                    + html.escape(tr("Result")) + "</th></tr>")
            trs = [head]

            def row_html(i, label, measured, threshold, verdict):
                bg = f" style='background:{self._ZEBRA_BG}'" if i % 2 == 1 else ""
                if verdict is None:
                    res = "—"
                else:
                    col = "#1e8e3e" if verdict else "#c0392b"
                    res = (f"<span style='color:{col};font-weight:bold'>"
                           + html.escape(tr("Pass") if verdict else tr("Fail"))
                           + "</span>")
                return (f"<tr{bg}><td style='padding-right:14px'>{html.escape(label)}</td>"
                        f"<td align='right'><b>{_fmt(measured)}</b></td>"
                        f"<td align='right'>{_fmt(threshold) if threshold is not None else '—'}</td>"
                        f"<td align='center'>{res}</td></tr>")

            for i, row in enumerate(rows):
                trs.append(row_html(i, _METRIC_LABELS[row["key"]](), row["value"],
                                    row["threshold"], row["pass"]))
            # Spread is reported for completeness but carries no threshold (Knut).
            trs.append(row_html(len(rows), _METRIC_LABELS["std"](),
                                de.get("std"), None, None))
            parts.append(_h3(tr("Colour accuracy (ΔE00 vs the chart's design)")))
            parts.append("<table cellpadding='5' cellspacing='0' "
                         "style='border-collapse:collapse;font-size:11px'>"
                         + "".join(trs) + "</table>")
        else:
            parts.append("<p style='color:#888'>" + html.escape(tr(
                "No design reference (.ti2) was found next to this measurement, so "
                "colour-accuracy statistics aren't available — only the paper white "
                "and black below.")) + "</p>")

        w, b = r.get("paper_white"), r.get("max_black")
        if w and b:
            parts.append(_h3(tr("Paper white & darkest black")))
            parts.append(
                f"<div>{_swatch(w['hex'])} " + html.escape(tr("White"))
                + f" ({html.escape(str(w['loc']))}) — L* {w['lab'][0]:.1f}</div>"
                f"<div>{_swatch(b['hex'])} " + html.escape(tr("Black"))
                + f" ({html.escape(str(b['loc']))}) — L* {b['lab'][0]:.1f}</div>")

        corners = r.get("corners") or []
        if corners:
            parts.append(_h3(tr("Cube corners (the eight ink extremes)")))
            head = ("<tr style='color:#888'><th align='left'>" + html.escape(tr("Corner"))
                    + "</th><th>" + html.escape(tr("Expected")) + "</th><th>"
                    + html.escape(tr("Measured")) + "</th><th align='right'>ΔE00</th></tr>")
            crows = [head]
            for i, c in enumerate(corners):
                lbl = _CORNER_LABELS.get(c["name"], (lambda: c["name"]))()
                exp = _swatch(c["expected_hex"]) if c.get("expected_hex") else "—"
                de_c = f"<b>{_fmt(c.get('de'))}</b>" if c.get("de") is not None else "—"
                miss = "" if c.get("present", True) else (
                    " <span style='color:#c0392b'>(" + html.escape(tr("missing"))
                    + ")</span>")
                bg = f" style='background:{self._ZEBRA_BG}'" if i % 2 == 1 else ""
                crows.append(
                    f"<tr{bg}><td>{html.escape(lbl)}{miss} "
                    f"<span style='color:#888'>({html.escape(str(c['loc']))})</span></td>"
                    f"<td align='center'>{exp}</td>"
                    f"<td align='center'>{_swatch(c['hex'])}</td>"
                    f"<td align='right'>{de_c}</td></tr>")
            parts.append("<table cellpadding='5' cellspacing='0' "
                         "style='border-collapse:collapse;font-size:11px'>"
                         + "".join(crows) + "</table>")

        worst = r.get("worst_patches") or []
        if worst:
            # Two 8-row halves side by side in one 9-column table (empty middle
            # column) — same columns, half the height (Knut).
            parts.append(_h3(tr("Worst patches")))

            def wcells(p) -> str:
                if p is None:
                    return "<td></td><td></td><td></td><td></td>"
                return (f"<td>{html.escape(str(p['loc']))}</td>"
                        f"<td align='right'><b>{_fmt(p['de'])}</b></td>"
                        f"<td align='center'>{_swatch(p['expected_hex'])}</td>"
                        f"<td align='center'>{_swatch(p['measured_hex'])}</td>")

            hdr = ("<th align='left'>" + html.escape(tr("Patch")) + "</th>"
                   "<th align='right'>ΔE00</th><th>" + html.escape(tr("Expected"))
                   + "</th><th>" + html.escape(tr("Measured")) + "</th>")
            half = (len(worst) + 1) // 2
            left, right = worst[:half], worst[half:]
            rows = ["<tr style='color:#888'>" + hdr
                    + "<th style='width:16px'></th>" + hdr + "</tr>"]
            for i in range(half):
                lp = left[i] if i < len(left) else None
                rp = right[i] if i < len(right) else None
                rows.append("<tr>" + wcells(lp) + "<td></td>" + wcells(rp) + "</tr>")
            parts.append("<table cellpadding='5' cellspacing='0' "
                         "style='border-collapse:collapse;font-size:11px'>"
                         + "".join(rows) + "</table>")

        return "<div>" + "".join(parts) + "</div>"

    def _detailed_section_html(self, runs: list) -> str:
        """The opt-in 'Detailed data per measurement run' section: each run on its
        own page, led by a 'Measurement run — date — N patches' heading and the
        profile name (Knut)."""
        out = [_h2(tr("Detailed data per measurement run"), page_break=True)]
        for idx, run in enumerate(runs):
            brk = "page-break-before:always;" if idx > 0 else ""
            out.append(
                f"<h3 style='color:{_HEAD};{brk}border-bottom:1px solid #ddd;"
                f"margin:12px 0 2px'>"
                + html.escape(tr("Measurement run — {date} — {n} patches").format(
                    date=str(run.get("created") or ""), n=run.get("patches", 0)))
                + "</h3>"
                "<div style='color:#555;margin-bottom:4px'>"
                + html.escape(tr("Profile name: {name}").format(
                    name=run.get("chart") or "")) + "</div>"
                + self._run_detail_html(run))
        return "".join(out)
