"""The "Chart layout information" panel (Create Chart tab).

A small read-only readout of the chart's patch count, strip grid and page count,
shown next to the "Measured from Preview" margin inspector. Knut asked for this:
the only place these numbers appeared was the log text in the corner (#93).

Two columns differentiate the **chart currently on screen** (measured from the
generated chart) from a live **estimate** of the current settings — so after
loading a chart and changing options you can see both what's printed and what
regenerating would give (#93).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                            QVBoxLayout, QWidget)

from core.i18n import tr
from ui.tooltip_button import TooltipButton

_DASH = "—"
_AMBER = "#c47f17"      # estimate differs from the chart on screen
_MUTED = "#909090"


class ChartLayoutInfoPanel(QGroupBox):
    """Patch-count / grid / page readout with on-screen vs estimate columns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("Chart layout information"), parent)
        self._actual: dict | None = None        # measured from the shown chart
        self._estimate: dict | None = None       # predicted from current settings
        self._actual_labels: dict[str, QLabel] = {}
        self._estimate_labels: dict[str, QLabel] = {}
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(6)
        v.setContentsMargins(12, 8, 12, 10)

        self._placeholder = QLabel(
            tr("Generate a preview to see its layout."), self)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #909090; font-size: 11px;")
        v.addWidget(self._placeholder)

        self._table = QWidget(self)
        grid = QGridLayout(self._table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)

        hdr_screen = QLabel(tr("on screen"), self)
        hdr_est = QLabel(tr("estimate"), self)
        for w in (hdr_screen, hdr_est):
            w.setAlignment(Qt.AlignmentFlag.AlignRight)
            w.setStyleSheet("color: #909090; font-size: 10px;")
        grid.addWidget(hdr_screen, 0, 1)
        grid.addWidget(hdr_est, 0, 2)

        rows = (
            ("total", tr("Total patches")),
            ("rows", tr("Patches per strip")),
            ("cols", tr("Strips (this page)")),
            ("pages", tr("Pages")),
        )
        for r, (key, label) in enumerate(rows, start=1):
            grid.addWidget(QLabel(label, self), r, 0)
            for col, store in ((1, self._actual_labels), (2, self._estimate_labels)):
                val = QLabel(_DASH, self)
                val.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
                val.setStyleSheet("font-family: Menlo; font-size: 11px;")
                grid.addWidget(val, r, col)
                store[key] = val
        grid.setColumnStretch(0, 1)
        v.addWidget(self._table)
        v.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(TooltipButton(
            tr("About chart layout information"),
            tr("This panel shows the SIZE and SHAPE of your chart — how many "
               "colour patches it has, how they're arranged, and how many pages "
               "it needs — so you can judge a chart before (and after) you make "
               "it.\n\n"
               "What the rows mean:\n"
               "• Total patches — how many colour squares the whole chart holds. "
               "More patches usually means a more accurate profile, but a bigger "
               "chart to print and measure.\n"
               "• Patches per strip — how many patches sit in one strip (a strip "
               "is a single column the instrument reads from top to bottom).\n"
               "• Strips (this page) — how many of those strips fit across the "
               "page you're looking at.\n"
               "• Pages — how many sheets the chart spans.\n\n"
               "The two columns:\n"
               "• on screen — the real numbers of the chart currently in the "
               "preview.\n"
               "• estimate — what the settings you have right now would produce "
               "if you generate. This is shown while the ChromIQ layout engine "
               "is switched on, because the engine can work the layout out "
               "exactly in advance.\n\n"
               "Change a setting (patch size, paper, margins, alignment…) and the "
               "estimate updates live. Any number that would come out different "
               "from the chart on screen turns amber — so you can see the effect "
               "of a change before re-generating the chart."),
            self))
        v.addLayout(bottom)

    # ------------------------------------------------------------------
    @staticmethod
    def _as_dict(total, rows, cols, pages) -> dict:
        return {"total": total, "rows": rows, "cols": cols, "pages": pages}

    def set_actual(self, *, total: int, rows: int, cols: int, pages: int) -> None:
        """The measured values of the chart currently in the preview."""
        self._actual = self._as_dict(total, rows, cols, pages)
        self._render()

    def clear_actual(self) -> None:
        self._actual = None
        self._render()

    def set_estimate(self, *, total: int, rows: int, cols: int, pages: int) -> None:
        """The predicted values for the current (engine) settings."""
        self._estimate = self._as_dict(total, rows, cols, pages)
        self._render()

    def clear_estimate(self) -> None:
        self._estimate = None
        self._render()

    def show_placeholder(self) -> None:
        self._actual = self._estimate = None
        self._render()

    # ------------------------------------------------------------------
    def _render(self) -> None:
        if self._actual is None and self._estimate is None:
            self._placeholder.setVisible(True)
            self._table.setVisible(False)
            return
        self._placeholder.setVisible(False)
        self._table.setVisible(True)
        for key in self._actual_labels:
            a = self._actual.get(key) if self._actual else None
            e = self._estimate.get(key) if self._estimate else None
            self._actual_labels[key].setText(_DASH if a is None else str(a))
            est = self._estimate_labels[key]
            est.setText(_DASH if e is None else str(e))
            # Flag the estimate amber when it diverges from the shown chart.
            differs = a is not None and e is not None and a != e
            est.setStyleSheet(
                f"font-family: Menlo; font-size: 11px; "
                f"color: {_AMBER if differs else _MUTED};")
