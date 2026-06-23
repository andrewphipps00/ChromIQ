"""The "Measured from Preview" margin inspector panel (Create Chart tab).

Shows the realised page margins (Left/Right/Top/Bottom) and estimated reading-
direction patch size of the generated chart preview, in mm and inches, plus a
large pass/fail status line and the dotted-guide-line toggle. Pure display +
one signal; all measurement/threshold logic lives in
:mod:`workflow.margin_inspector` and the owning tab.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from workflow.margin_inspector import MarginReport, Violation

_MM_PER_INCH = 25.4
_EDGES = (("L", "Left"), ("R", "Right"), ("T", "Top"), ("B", "Bottom"))


class MarginInspectorPanel(QGroupBox):
    """Read-only margin readout + violation status + guide-line checkbox."""

    guides_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("Measured from Preview"), parent)
        self._mode = "dark"
        self._value_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._build_ui()
        self.show_placeholder()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(6)
        v.setContentsMargins(12, 8, 12, 10)

        self._placeholder = QLabel(
            tr("Generate a preview to measure its margins."), self)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #909090; font-size: 11px;")
        v.addWidget(self._placeholder)

        self._table = QWidget(self)
        grid = QGridLayout(self._table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        hdr_mm = QLabel(tr("mm"), self)
        hdr_in = QLabel(tr("inch"), self)
        hdr_thr = QLabel(tr("min"), self)
        for w in (hdr_mm, hdr_in, hdr_thr):
            w.setAlignment(Qt.AlignmentFlag.AlignRight)
            w.setStyleSheet("color: #909090; font-size: 10px;")
        grid.addWidget(hdr_mm, 0, 1)
        grid.addWidget(hdr_in, 0, 2)
        grid.addWidget(hdr_thr, 0, 3)
        self._thr_labels: dict[str, QLabel] = {}
        row = 1
        for key, label in _EDGES:
            name = QLabel(tr(label), self)
            mm = QLabel("—", self)
            inch = QLabel("—", self)
            thr = QLabel("—", self)
            for lbl in (mm, inch, thr):
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            mm.setStyleSheet("font-family: Menlo; font-size: 11px;")
            inch.setStyleSheet("font-family: Menlo; font-size: 11px;")
            thr.setStyleSheet("font-family: Menlo; font-size: 11px; color: #909090;")
            grid.addWidget(name, row, 0)
            grid.addWidget(mm, row, 1)
            grid.addWidget(inch, row, 2)
            grid.addWidget(thr, row, 3)
            self._value_labels[key] = (mm, inch)
            self._thr_labels[key] = thr
            row += 1

        strip_name = QLabel(tr("Patch width (in strip reading direction)"), self)
        self._strip_mm = QLabel("—", self)
        self._strip_in = QLabel("—", self)
        self._strip_mm.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._strip_in.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._strip_mm.setStyleSheet("font-family: Menlo; font-size: 11px;")
        self._strip_in.setStyleSheet("font-family: Menlo; font-size: 11px;")
        grid.addWidget(strip_name, row, 0)
        grid.addWidget(self._strip_mm, row, 1)
        grid.addWidget(self._strip_in, row, 2)
        grid.setColumnStretch(0, 1)
        v.addWidget(self._table)

        # Large pass/fail status, one or more lines.
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self._status)

        self._guide_check = QCheckBox(
            tr("Show margin threshold guide lines on preview (dotted lines)"), self)
        self._guide_check.toggled.connect(self.guides_toggled.emit)
        v.addWidget(self._guide_check)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"

    def guides_enabled(self) -> bool:
        return self._guide_check.isChecked()

    def set_guides_checked(self, on: bool) -> None:
        self._guide_check.setChecked(bool(on))

    def show_placeholder(self) -> None:
        """No preview yet (or measurement failed) — hide the numbers."""
        self._placeholder.setVisible(True)
        self._table.setVisible(False)
        self._status.setVisible(False)

    def update_report(
        self,
        report: Optional[MarginReport],
        violations: list[Violation],
        *,
        thresholds_defined: bool,
        notify: bool,
        thresholds: dict | None = None,
    ) -> None:
        """Show ``report``'s margins and the pass/fail status.

        ``thresholds_defined`` is False when no thresholds exist for the chart's
        combo (status is then a neutral note, not green/red). ``notify`` mirrors
        the Settings flag — when False the status line is suppressed entirely
        (margins still shown).
        """
        if report is None:
            self.show_placeholder()
            return
        self._placeholder.setVisible(False)
        self._table.setVisible(True)

        vals = {"L": report.left_mm, "R": report.right_mm,
                "T": report.top_mm, "B": report.bottom_mm}
        violated_edges = {v.edge for v in violations}
        edge_name = {"L": "Left", "R": "Right", "T": "Top", "B": "Bottom"}
        for key, (mm_lbl, in_lbl) in self._value_labels.items():
            mm = vals[key]
            mm_lbl.setText(f"{mm:.1f}")
            in_lbl.setText(f"{mm / _MM_PER_INCH:.3f}")
            bad = edge_name[key] in violated_edges
            colour = "#e0564b" if bad else ("#1c1b18" if self._mode == "light" else "#d8d8d8")
            weight = "600" if bad else "400"
            for lbl in (mm_lbl, in_lbl):
                lbl.setStyleSheet(
                    f"font-family: Menlo; font-size: 11px; color: {colour}; font-weight: {weight};")
            # Threshold (minimum) for this edge — the "Margin Thresholds Set"
            # readout, shown beside the measured value for easy comparison (#86).
            raw = (thresholds or {}).get(key)
            try:
                self._thr_labels[key].setText("—" if raw in (None, "") else f"{float(raw):.1f}")
            except (TypeError, ValueError):
                self._thr_labels[key].setText("—")

        if report.strip_width_mm is not None:
            self._strip_mm.setText(f"{report.strip_width_mm:.1f}")
            self._strip_in.setText(f"{report.strip_width_mm / _MM_PER_INCH:.3f}")
        else:
            self._strip_mm.setText("—")
            self._strip_in.setText("—")

        self._update_status(violations, thresholds_defined=thresholds_defined,
                            notify=notify)

    # ------------------------------------------------------------------
    def _update_status(
        self, violations: list[Violation], *,
        thresholds_defined: bool, notify: bool,
    ) -> None:
        if not notify:
            self._status.setVisible(False)
            return
        self._status.setVisible(True)
        if not thresholds_defined:
            self._status.setText(tr(
                "No margin thresholds set for this instrument and paper size."))
            self._status.setStyleSheet("color: #909090; font-size: 11px;")
            return
        if not violations:
            self._status.setText(tr("Margins: OK"))
            self._status.setStyleSheet(
                "color: #4fc27a; font-size: 15px; font-weight: 700;")
            return
        lines = [
            tr("⚠ {edge} margin {measured:.1f} mm is below the {threshold:.0f} mm minimum")
            .format(edge=tr(v.edge), measured=v.measured_mm, threshold=v.threshold_mm)
            for v in violations
        ]
        self._status.setText("\n".join(lines))
        self._status.setStyleSheet(
            "color: #e0564b; font-size: 14px; font-weight: 700;")
