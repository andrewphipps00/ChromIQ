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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

from core.i18n import tr
from core.logger import get_logger
from ui.tooltip_button import TooltipButton
from ui.widgets import open_file_dialog, tint_dialog_primary

log = get_logger(__name__)

_TAB_COLOR = "#56d6a5"


class MeasurementReportDialog(QDialog):
    def __init__(self, settings, parent=None, initial_ti3=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._report: dict | None = None
        self._ti3: Path | None = None
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
            "patches, a quick health check of your paper and maximum ink.\n\n"
            "Why compare over time? On a printer, a single ΔE against the "
            "design isn't meaningful on its own (a printer doesn't reproduce "
            "sRGB). But because the design reference never changes, the "
            "CHANGE between two dated reports of the same chart is a clean "
            "signal of drift — ageing inks, a drifting printer, or a "
            "drifting instrument. Press “Save this report” after each "
            "measurement to build that history.\n\n"
            "Screen colours are approximate; the numbers come from your "
            "measurement file and are exact."),
            self))
        v.addLayout(intro_row)

        btn_row = QHBoxLayout()
        self._open_btn = QPushButton(tr("Open another measurement (.ti3)…"), self)
        self._open_btn.clicked.connect(self._on_open)
        btn_row.addWidget(self._open_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

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
            build_report, compare_reports, list_reports,
        )
        import json
        try:
            self._report = build_report(path)
        except Exception as exc:  # noqa: BLE001
            self._view.setHtml(self._error_html(str(exc)))
            return
        self._ti3 = Path(path)
        # Compare against the most recent prior saved report of this chart. The
        # newest saved report is usually this very measurement (auto-saved), so
        # compare against the one before it when present.
        prior = [p for p in list_reports(self._ti3.parent)]
        comparison = None
        for p in reversed(prior):
            try:
                older = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            if older.get("created") != self._report.get("created"):
                comparison = compare_reports(older, self._report)
                break
        self._view.setHtml(self._report_html(self._report, comparison))

    # ------------------------------------------------------------------
    def _empty_html(self) -> str:
        return ("<div style='color:#888;padding:24px'>"
                + html.escape(tr("Open a measurement file to see its report."))
                + "</div>")

    def _error_html(self, msg: str) -> str:
        return ("<div style='color:#d9534f;padding:24px'>"
                + html.escape(tr("Could not read this measurement: {msg}")
                              .format(msg=msg)) + "</div>")

    def _report_html(self, r: dict, comparison: "dict | None") -> str:
        def sw(hexc: str) -> str:
            return (f"<span style='display:inline-block;width:14px;height:14px;"
                    f"border:1px solid #999;background:{html.escape(hexc)};"
                    f"vertical-align:middle'></span>")

        de = r.get("de00")
        parts = [f"<h2 style='margin:0 0 2px'>{html.escape(r['chart'])}</h2>",
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
