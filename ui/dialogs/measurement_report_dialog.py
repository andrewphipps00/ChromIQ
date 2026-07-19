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
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from core.i18n import tr
from core.logger import get_logger
from ui.tooltip_button import TooltipButton
from ui.widgets import open_file_dialog, tint_dialog_primary

log = get_logger(__name__)

_TAB_COLOR = "#56d6a5"


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
        self.setMinimumHeight(210)

    def set_data(self, series, metrics, dark=True, y_max=None, dec=1) -> None:
        def has_any(pt) -> bool:
            return any(acc(pt) is not None for _, _, acc in metrics)
        self._series = [p for p in (series or []) if has_any(p)]
        self._metrics = metrics
        self._dark = dark
        self._y_max = y_max
        self._dec = dec
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

        L, R, T, B = 40.0, 12.0, 24.0, 26.0
        w = max(1.0, self.width() - L - R)
        h = max(1.0, self.height() - T - B)
        vals = [v for pt in pts for _, _, acc in self._metrics
                if (v := acc(pt)) is not None]
        vmax = self._y_max if self._y_max else max(vals + [1.0]) * 1.12
        n = len(pts)

        def xy(i: int, val: float):
            return QPointF(L + (w * i / (n - 1)), T + h * (1.0 - val / vmax))

        # Y grid + labels (0, mid, top).
        p.setPen(QPen(grid, 1.0))
        for frac in (0.0, 0.5, 1.0):
            yy = T + h * (1.0 - frac)
            p.drawLine(QPointF(L, yy), QPointF(L + w, yy))
            p.setPen(QPen(fg, 1.0))
            p.drawText(QRectF(0, yy - 7, L - 4, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{vmax * frac:.{self._dec}f}")
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
        self.setMinimumSize(720, 640)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        intro_row = QHBoxLayout()
        intro = QLabel(tr(
            "See how accurately your printed chart was reproduced, and keep a "
            "dated report so you can compare measurements of the same chart "
            "over time."), self)
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        intro_row.addWidget(TooltipButton(
            tr("Measurement report"),
            tr("This compares what your instrument measured against the "
            "colours the chart was designed to have, patch by patch, and "
            "sums it up:\n\n"
            "  • ΔE00 (colour difference) — the average, middle (median), "
            "worst and spread across all patches. Lower is closer to the "
            "design; the numbers matter most when you compare two "
            "measurements of the SAME chart.\n"
            "  • Worst patches — the individual patches that differ most, with "
            "the expected and measured colour side by side, so you can spot a "
            "misread or a genuinely hard-to-reproduce colour.\n"
            "  • Paper white and darkest black — the brightest and deepest "
            "patches, a quick health check of your paper and maximum ink.\n"
            "  • Cube corners — paper white, composite black and the six "
            "primary and secondary inks (red, green, blue, cyan, magenta, "
            "yellow): the corners of the colour cube. Each is compared to its "
            "design colour, so they tell you about the inks themselves, not "
            "only the instrument or the paper.\n\n"
            "Why compare over time? On a printer, a single ΔE against the "
            "design isn't meaningful on its own (a printer doesn't reproduce "
            "sRGB). But because the design reference never changes, the "
            "CHANGE between two dated reports of the same chart is a clean "
            "signal of drift — ageing inks, a drifting printer, or a "
            "drifting instrument. Press “Save this report” after each "
            "measurement to build that history.\n\n"
            "Trend over time: once you've saved two or more reports for a "
            "printer, a small chart appears at the top plotting the average "
            "and worst ΔE00 of every saved measurement across all of this "
            "printer's builds — so a slow rise (drift) or a sudden jump (a "
            "bad print or a misread) stands out at a glance. This is the "
            "per-printer view: it gathers the whole history for this project, "
            "which is one printer and paper.\n\n"
            "Screen colours are approximate; the numbers come from your "
            "measurement file and are exact."),
            self))
        v.addLayout(intro_row)

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
        self._pdf_btn = QPushButton(tr("Save report as PDF…"), self)
        self._pdf_btn.clicked.connect(self._export_pdf)
        self._pdf_btn.setEnabled(False)
        btn_row.addWidget(self._pdf_btn)
        self._all_runs_check = QCheckBox(
            tr("Include all measurement runs in the PDF"), self)
        self._all_runs_check.setChecked(True)
        self._all_runs_check.setToolTip(tr(
            "When on, the PDF lists the data tables for every saved measurement "
            "of this printer plus a side-by-side comparison — not only the run "
            "shown here."))
        btn_row.addWidget(self._all_runs_check)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

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
        self._view.setHtml(self._empty_html())
        v.addWidget(self._view, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton(tr("Close"), self)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        v.addLayout(close_row)

        tint_dialog_primary(self, _TAB_COLOR)

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
            build_report, compare_reports, list_project_reports, report_trend,
        )
        import json
        try:
            self._report = build_report(path)
        except Exception as exc:  # noqa: BLE001
            self._view.setHtml(self._error_html(str(exc)))
            return
        self._ti3 = Path(path)
        # The printer's full history: every saved report across all runs of this
        # project, oldest first (#40). The current measurement is usually the
        # newest auto-saved one; compare against the report before it, and plot
        # the whole series as a trend.
        history: list[dict] = []
        for p in list_project_reports(self._ti3.parent):
            try:
                history.append(json.loads(p.read_text()))
            except Exception:  # noqa: BLE001
                continue
        self._history = history                 # every saved run, for the PDF
        self._project_dirs = {self._ti3.parent}  # folders folded into the trend
        comparison = None
        for older in reversed(history):
            if older.get("created") != self._report.get("created"):
                comparison = compare_reports(older, self._report)
                break

        self._refresh_trend()
        self._view.setHtml(self._report_html(self._report, comparison))
        self._pdf_btn.setEnabled(True)
        self._add_btn.setEnabled(True)

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
        self._add_btn.setText(tr("Added — {n} projects in trend").format(
            n=len(self._project_dirs)))

    def _trend_configs(self) -> list:
        """The three grouped charts as ``(chart, title, metrics, y_max, dec)`` —
        shared by the live tabs and the PDF export so they always match."""
        corner_metrics = [
            (_CORNER_LABELS[code](), QColor(_CORNER_LINE[code]),
             (lambda pt, c=code: (pt.get("corners") or {}).get(c)))
            for code in ("W", "K", "R", "G", "B", "C", "M", "Y")
        ]
        return [
            (self._trend_de, tr("Colour accuracy (ΔE00)"), [
                (tr("Average"), QColor("#56d6a5"), lambda pt: pt.get("mean")),
                (tr("Worst"),   QColor("#e0864b"), lambda pt: pt.get("max")),
            ], None, 1),
            # White (~L*100) and black (~L*10) are too far apart to share an axis
            # (Knut), so each is its own auto-scaled chart — a small drift in
            # either is then actually visible.
            (self._trend_white, tr("Paper white (L*)"), [
                (tr("Paper white L*"), QColor("#8a8a8a"), lambda pt: pt.get("white_L")),
            ], None, 1),
            (self._trend_black, tr("Darkest black (L*)"), [
                (tr("Black L*"), QColor("#505050"), lambda pt: pt.get("black_L")),
            ], None, 1),
            (self._trend_corners, tr("Cube corners (ΔE00 per ink)"),
             corner_metrics, None, 1),
        ]

    def _export_pdf(self) -> None:
        """Write the full report — all data, the trend charts and a plain-language
        guide to reading them — as a PDF into the reports folder (Knut)."""
        if not self._report or not self._ti3:
            return
        from datetime import datetime
        from PyQt6.QtCore import QMarginsF, QSizeF, QUrl
        from PyQt6.QtGui import (
            QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument,
        )
        from core.file_manager import reports_subdir

        reports = reports_subdir(self._ti3.parent)
        reports.mkdir(parents=True, exist_ok=True)
        default = reports / (
            f"report_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save report as PDF"), str(default), "PDF (*.pdf)")
        if not path:
            return

        doc = QTextDocument()
        charts_html = ""
        if self._trend_de.has_trend():
            # Render each grouped chart off-screen at a fixed export size (the
            # live tabs only lay out the current one) and embed it as a resource.
            for i, (_c, title, metrics, y_max, dec) in enumerate(self._trend_configs()):
                tmp = _TrendChart()
                tmp.resize(720, 240)
                tmp.set_data(self._trend_series, metrics, dark=False,
                             y_max=y_max, dec=dec)
                img = tmp.grab().toImage()
                url = QUrl(f"chart://{i}")
                doc.addResource(QTextDocument.ResourceType.ImageResource, url, img)
                charts_html += (f"<h3>{html.escape(title)}</h3>"
                                f"<img src='chart://{i}' width='680'>")
        # All saved runs of this printer, or just the loaded one (Knut's checkbox).
        # The loaded run is normally already among the saved history; we use the
        # history as-is (each with its own saved date) rather than the freshly
        # built current report, so a run is never listed twice.
        if self._all_runs_check.isChecked() and self._history:
            runs = list(self._history)
        else:
            runs = [self._report]
        doc.setHtml(self._pdf_html(runs, charts_html))

        writer = QPdfWriter(str(path))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(14, 14, 14, 14), QPageLayout.Unit.Millimeter)
        # QPdfWriter defaults to a very high resolution, but the document is laid
        # out in ~96-dpi pixels (font px, the img width=680), so at the default
        # the content filled only a fraction of the page. Match the writer to the
        # document's 96-dpi coordinate space so it fills the page; text stays
        # vector-crisp regardless of this number.
        writer.setResolution(96)
        doc.setPageSize(QSizeF(writer.width(), writer.height()))
        doc.print(writer)
        self._pdf_btn.setText(tr("Saved: {name}").format(name=Path(path).name))

    def _comparison_table_html(self, runs: list) -> str:
        """A side-by-side table of each metric across every run — the at-a-glance
        drift view Knut asked for (columns = dated runs, rows = metrics)."""
        def cell(v, dec=2):
            return f"{v:.{dec}f}" if isinstance(v, (int, float)) else "—"
        dates = [str(r.get("created") or "")[:10] for r in runs]
        head = ("<tr><th align='left'>" + tr("Metric") + "</th>"
                + "".join(f"<th>{html.escape(d)}</th>" for d in dates) + "</tr>")
        rows = [head]

        def metric_row(label, fn, dec=2):
            cells = "".join(f"<td align='right'>{cell(fn(r), dec)}</td>" for r in runs)
            rows.append(f"<tr><td style='color:#555'>{html.escape(label)}</td>{cells}</tr>")

        de = lambda r: (r.get("de00") or {})
        metric_row(tr("Average ΔE00"), lambda r: de(r).get("mean"))
        metric_row(tr("Worst ΔE00"), lambda r: de(r).get("max"))
        metric_row(tr("Paper white L*"),
                   lambda r: (r.get("paper_white") or {}).get("lab", [None])[0], 1)
        metric_row(tr("Black L*"),
                   lambda r: (r.get("max_black") or {}).get("lab", [None])[0], 1)
        for code in ("W", "K", "R", "G", "B", "C", "M", "Y"):
            lbl = tr("{corner} ΔE00").format(corner=_CORNER_LABELS[code]())

            def corner_de(r, c=code):
                for cc in (r.get("corners") or []):
                    if cc.get("name") == c:
                        return cc.get("de")
                return None
            metric_row(lbl, corner_de)
        return ("<h2 style='color:#2a2a2a'>" + tr("Side-by-side comparison") + "</h2>"
                "<table cellpadding='4' cellspacing='0' "
                "style='border-collapse:collapse;font-size:11px'>"
                + "".join(rows) + "</table>")

    def _pdf_html(self, runs: list, charts_html: str) -> str:
        """The printable report (Knut's order): a printer-level header, the
        how-to-read guide, the trend charts, a side-by-side comparison, then the
        full detailed data for every run below."""
        first = runs[0] if runs else self._report
        span = ""
        if len(runs) > 1:
            span = (" &nbsp;·&nbsp; " + tr("{n} measurements").format(n=len(runs))
                    + f" ({str(runs[0].get('created') or '')[:10]} – "
                    f"{str(runs[-1].get('created') or '')[:10]})")
        header = (
            "<table width='100%' cellpadding='0' cellspacing='0'>"
            "<tr><td style='border-bottom:2px solid #56d6a5;padding-bottom:6px'>"
            f"<span style='font-size:22px;font-weight:bold;color:#2a2a2a'>"
            f"{html.escape(tr('Measurement Report'))}</span><br>"
            f"<span style='font-size:12px;color:#777'>{html.escape(first['chart'])}"
            + span + "</span></td></tr></table><br>")
        guide = (
            "<h2>" + tr("How to read this report") + "</h2>"
            "<p>" + tr(
                "This report compares what your instrument measured against the "
                "colours the chart was designed to have. Every number is a colour "
                "difference (ΔE00): 0 is a perfect match, 1–2 is barely visible, "
                "and 10+ is clearly wrong.") + "</p>"
            "<ul>"
            "<li>" + tr("<b>Colour accuracy</b> — the average, median, worst, best "
                        "and spread of ΔE00 across every patch. On a printer the "
                        "absolute value is less telling than how it CHANGES between "
                        "dated reports of the same chart.") + "</li>"
            "<li>" + tr("<b>Paper white &amp; darkest black</b> — the brightest and "
                        "deepest patches (L*), a quick health check of your paper "
                        "and maximum ink.") + "</li>"
            "<li>" + tr("<b>Cube corners</b> — paper white, composite black and the "
                        "six primary and secondary inks. These say as much about "
                        "your inks as about the instrument.") + "</li>"
            "</ul>"
            "<p>" + tr(
                "The trend charts plot every saved report of this printer over "
                "time, so a slow rise (drift — ageing inks, a drifting printer or "
                "instrument) or a sudden jump (a bad print or a misread) stands "
                "out at a glance. Save a report after each measurement to build "
                "that history. Screen and print colours here are approximate; the "
                "numbers come from your measurement file and are exact.") + "</p>")
        charts = (("<h2 style='color:#2a2a2a'>" + tr("Trend over time (this printer)")
                   + "</h2>" + charts_html) if charts_html else "")
        guide_box = ("<table width='100%' cellpadding='12' cellspacing='0'>"
                     "<tr><td style='background:#f4f7f6'>" + guide + "</td></tr></table>")
        comparison = self._comparison_table_html(runs) if len(runs) > 1 else ""
        # Full detailed data for every run, below the overview (Knut).
        details = "<h2 style='color:#2a2a2a'>" + tr("Detailed data per measurement") + "</h2>"
        for run in runs:
            details += (
                "<h3 style='color:#2a2a2a;border-bottom:1px solid #ddd'>"
                f"{html.escape(str(run.get('created') or ''))} &nbsp;·&nbsp; "
                f"{run.get('patches', 0)} " + html.escape(tr('patches')) + "</h3>"
                + self._report_html(run, None, title=False))
        # Knut's order: how-to-read first, then charts, then comparison, then data.
        return ("<div style='font-family:sans-serif;color:#333;font-size:12px'>"
                + header + guide_box + "<br>" + charts + "<br>" + comparison
                + "<br>" + details + "</div>")

    def _update_trends(self, series: list, dark: bool) -> None:
        """Feed the three grouped trend charts their own metric sets (#40, Knut)."""
        for chart, _title, metrics, y_max, dec in self._trend_configs():
            chart.set_data(series, metrics, dark=dark, y_max=y_max, dec=dec)
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

    def _report_html(self, r: dict, comparison: "dict | None", title: bool = True) -> str:
        def sw(hexc: str) -> str:
            # Qt's rich-text engine ignores display:inline-block width/height on
            # an empty span (the swatches came out invisible), but it DOES honour
            # background-color on a span with content. Fill it with spaces hidden
            # by matching the text colour to the fill, so a solid colour block
            # shows on every theme.
            c = html.escape(hexc)
            return (f"<span style='background-color:{c};color:{c};"
                    f"border:1px solid #999'>&nbsp;&nbsp;&nbsp;</span>")

        de = r.get("de00")
        parts = []
        if title:
            parts += [f"<h2 style='margin:0 0 2px'>{html.escape(r['chart'])}</h2>",
                      f"<div style='color:#888;font-size:12px'>"
                      f"{html.escape(r['created'])} · {r['patches']} "
                      + tr("patches") + "</div>"]

        if de:
            parts.append("<h3>" + tr("Colour accuracy (ΔE00 vs the chart's design)") + "</h3>")
            parts.append(
                "<table cellpadding='5' style='border-collapse:collapse'>"
                + "".join(
                    f"<tr><td style='color:#888'>{html.escape(lbl)}</td>"
                    f"<td style='text-align:right'><b>{de[k]:.2f}</b></td></tr>"
                    for lbl, k in ((tr("Average"), "mean"), (tr("Median"), "median"),
                                   (tr("Worst"), "max"), (tr("Best"), "min"),
                                   (tr("Spread (std. dev.)"), "std"),
                                   (tr("95% within"), "p95")))
                + "</table>")
        else:
            parts.append("<p style='color:#888'>"
                         + tr("No design reference (.ti2) was found next to this "
                              "measurement, so colour-accuracy statistics aren't "
                              "available — only the paper white and black below.")
                         + "</p>")

        w, b = r["paper_white"], r["max_black"]
        parts.append("<h3>" + tr("Paper white &amp; darkest black") + "</h3>")
        parts.append(
            f"<div>{sw(w['hex'])} " + tr("White") + f" ({html.escape(str(w['loc']))}) "
            f"— L* {w['lab'][0]:.1f}</div>"
            f"<div>{sw(b['hex'])} " + tr("Black") + f" ({html.escape(str(b['loc']))}) "
            f"— L* {b['lab'][0]:.1f}</div>")

        corners = r.get("corners") or []
        if corners:
            parts.append("<h3>" + tr("Cube corners (the eight ink extremes)") + "</h3>")
            parts.append("<div style='color:#888;font-size:12px;margin-bottom:4px'>"
                         + tr("Paper white, composite black and the six primary and "
                              "secondary inks — the corners of the colour cube. These "
                              "say as much about your inks as about the measurement.")
                         + "</div>")
            head = ("<tr style='color:#888'><th align='left'>" + tr("Corner")
                    + "</th><th>" + tr("Expected") + "</th><th>" + tr("Measured")
                    + "</th><th align='right'>ΔE00</th></tr>")
            crows = [head]
            for c in corners:
                lbl = _CORNER_LABELS.get(c["name"], lambda: c["name"])()
                exp = (sw(c["expected_hex"]) if c.get("expected_hex") else "—")
                de_c = (f"<b>{c['de']:.2f}</b>" if c.get("de") is not None else "—")
                crows.append(
                    f"<tr><td>{html.escape(lbl)} "
                    f"<span style='color:#888'>({html.escape(str(c['loc']))})</span></td>"
                    f"<td align='center'>{exp}</td>"
                    f"<td align='center'>{sw(c['hex'])}</td>"
                    f"<td align='right'>{de_c}</td></tr>")
            parts.append("<table cellpadding='5' style='border-collapse:collapse'>"
                         + "".join(crows) + "</table>")

        worst = r.get("worst_patches") or []
        if worst:
            parts.append("<h3>" + tr("Worst patches") + "</h3>")
            rows = ["<tr style='color:#888'><th align='left'>" + tr("Patch")
                    + "</th><th align='right'>ΔE00</th><th>" + tr("Expected")
                    + "</th><th>" + tr("Measured") + "</th></tr>"]
            for p in worst:
                rows.append(
                    f"<tr><td>{html.escape(str(p['loc']))}</td>"
                    f"<td align='right'><b>{p['de']:.2f}</b></td>"
                    f"<td align='center'>{sw(p['expected_hex'])}</td>"
                    f"<td align='center'>{sw(p['measured_hex'])}</td></tr>")
            parts.append("<table cellpadding='5' style='border-collapse:collapse'>"
                         + "".join(rows) + "</table>")

        if comparison:
            parts.append("<h3>" + tr("Change since the last saved report") + "</h3>")
            parts.append("<div style='color:#888;font-size:12px'>"
                         + tr("compared with {when}").format(
                             when=html.escape(str(comparison.get("older", "?"))))
                         + "</div>")
            crows = []
            for lbl, k in ((tr("Average ΔE00"), "de00_mean_delta"),
                           (tr("Worst ΔE00"), "de00_max_delta"),
                           (tr("Paper-white shift"), "paper_white_de"),
                           (tr("Black shift"), "max_black_de")):
                if k in comparison:
                    val = comparison[k]
                    arrow = "▲" if val > 0.05 else ("▼" if val < -0.05 else "—")
                    crows.append(f"<tr><td style='color:#888'>{html.escape(lbl)}</td>"
                                 f"<td align='right'>{arrow} {val:+.2f}</td></tr>")
            if crows:
                parts.append("<table cellpadding='5'>" + "".join(crows) + "</table>")
            parts.append("<div style='color:#888;font-size:12px;margin-top:6px'>"
                         + tr("A rising average or shifting white/black over time "
                              "points to ageing inks, printer drift, or instrument "
                              "drift.") + "</div>")

        return "<div style='font-family:sans-serif'>" + "".join(parts) + "</div>"
