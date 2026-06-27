"""The "Chart layout information" panel (Create Chart tab).

A small read-only readout of the chart currently in the preview — total patch
count, the strip grid (patches per strip × strips), and page count — shown next
to the "Measured from Preview" margin inspector. Knut asked for this: the only
place these numbers appeared was the log text in the corner (#93).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from core.i18n import tr

class ChartLayoutInfoPanel(QGroupBox):
    """Read-only patch-count / grid / page readout for the previewed chart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("Chart layout information"), parent)
        self._value_labels: dict[str, QLabel] = {}
        self._build_ui()
        self.show_placeholder()

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
        rows = (
            ("total", tr("Total patches")),
            ("rows", tr("Patches per strip")),
            ("cols", tr("Strips (this page)")),
            ("pages", tr("Pages")),
        )
        for row, (key, label) in enumerate(rows):
            name = QLabel(label, self)
            val = QLabel("—", self)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setStyleSheet("font-family: Menlo; font-size: 11px;")
            grid.addWidget(name, row, 0)
            grid.addWidget(val, row, 1)
            self._value_labels[key] = val
        grid.setColumnStretch(0, 1)
        v.addWidget(self._table)
        v.addStretch(1)   # keep rows at natural height, table pinned to the top

    # ------------------------------------------------------------------
    def show_placeholder(self) -> None:
        self._placeholder.setVisible(True)
        self._table.setVisible(False)

    def update_info(self, *, total: int, rows: int, cols: int, pages: int) -> None:
        """Fill the readout. ``rows`` = patches per strip, ``cols`` = strips on
        the page currently shown, ``pages`` = total pages."""
        self._placeholder.setVisible(False)
        self._table.setVisible(True)
        self._value_labels["total"].setText(str(total))
        self._value_labels["rows"].setText(str(rows))
        self._value_labels["cols"].setText(str(cols))
        self._value_labels["pages"].setText(str(pages))
