"""Reusable step-header widget shown at the top of each workflow tab."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui.tooltip_button import TooltipButton


class TabHeader(QWidget):
    """Inline accent stroke before step label, large title below.

    Optionally renders a ⓘ tooltip button next to the title when
    ``tooltip_title`` and ``tooltip_body`` are supplied.
    """

    def __init__(
        self,
        step_text: str,
        title_text: str,
        accent_color: str,
        parent: QWidget | None = None,
        *,
        tooltip_title: str | None = None,
        tooltip_body: str | None = None,
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

        self._step_lbl = QLabel(step_text, self)
        self._step_lbl.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 12px; font-weight: 300;"
        )
        step_row.addWidget(self._step_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        step_row.addStretch()

        root.addLayout(step_row)

        # Second row: large title (+ optional tooltip icon)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)

        self._title_lbl = QLabel(title_text, self)
        self._title_lbl.setStyleSheet(
            "color: #ffffff; background: transparent;"
            " font-family: Georgia; font-size: 30px;"
        )
        title_font = QFont()
        title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 85)
        self._title_lbl.setFont(title_font)
        title_row.addWidget(self._title_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._tooltip_btn: TooltipButton | None = None
        if tooltip_title and tooltip_body:
            self._tooltip_btn = TooltipButton(
                tooltip_title, tooltip_body, self, min_width=560
            )
            btn_wrap = QWidget(self)
            btn_layout = QVBoxLayout(btn_wrap)
            btn_layout.setContentsMargins(0, 4, 0, 0)
            btn_layout.setSpacing(0)
            btn_layout.addWidget(self._tooltip_btn)
            title_row.addWidget(btn_wrap, 0, Qt.AlignmentFlag.AlignVCenter)

        title_row.addStretch()
        root.addLayout(title_row)

    def set_texts(self, step_text: str, title_text: str) -> None:
        self._step_lbl.setText(step_text)
        self._title_lbl.setText(title_text)

    def set_tooltip(self, title: str, body: str) -> None:
        """Update the headline tooltip's title and body."""
        if self._tooltip_btn is None:
            return
        self._tooltip_btn._title = title
        self._tooltip_btn._body = body.strip()
        self._tooltip_btn.setToolTip(f"{title}\n\nClick for details")
