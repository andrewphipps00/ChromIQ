"""Reusable step-header widget shown at the top of each workflow tab."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class TabHeader(QWidget):
    """Inline accent stroke before step label, large title below."""

    def __init__(
        self,
        step_text: str,
        title_text: str,
        accent_color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(4)

        # First row: colored stroke + step text side by side
        step_row = QHBoxLayout()
        step_row.setContentsMargins(0, 0, 0, 0)
        step_row.setSpacing(8)

        bar = QFrame(self)
        bar.setFixedSize(22, 2)
        bar.setStyleSheet(f"background-color: {accent_color}; border: none;")
        step_row.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)

        step_lbl = QLabel(step_text, self)
        step_lbl.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 12pt; font-weight: 300;"
        )
        step_row.addWidget(step_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        step_row.addStretch()

        root.addLayout(step_row)

        # Second row: large title
        title_lbl = QLabel(title_text, self)
        title_lbl.setStyleSheet(
            "color: #ffffff; background: transparent;"
            " font-family: Georgia; font-size: 30pt;"
        )
        title_font = QFont()
        title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 85)
        title_lbl.setFont(title_font)
        root.addWidget(title_lbl)
