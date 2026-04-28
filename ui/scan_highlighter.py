"""Scan-strip highlighter — corner brackets + tint over the active row.

Drop-in replacement for whatever currently marks the active strip in
the Measure tab. Renders cleanly at any size: corner brackets stay at
fixed pixel thickness, label/chevron auto-hide when there's no room.

Usage:

    from ui.scan_highlighter import ScanHighlighter

    hl = ScanHighlighter(self.tiff_preview_widget)
    hl.set_strip(row_index=3, total=14, direction="right")
    hl.setGeometry(x, y, w, h)   # over the active strip in the preview
    hl.show()
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

# The Measure tab's spectrum color is green
DEFAULT_COLOR = "#34B158"


class ScanHighlighter(QWidget):
    """Transparent overlay that highlights the active strip with brackets."""

    def __init__(
        self,
        parent: QWidget | None = None,
        color: str = DEFAULT_COLOR,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._color = QColor(color)
        self._row = 0
        self._total = 0
        self._direction = "right"

    def set_strip(self, row_index: int, total: int, direction: str = "right") -> None:
        self._row = row_index
        self._total = total
        self._direction = direction
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self.width() < 6 or self.height() < 6:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)

        # Faint inside tint
        tint = QColor(self._color)
        tint.setAlpha(38)
        p.fillRect(rect, tint)

        # Inner outline at half opacity
        outline = QColor(self._color)
        outline.setAlpha(128)
        p.setPen(QPen(outline, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

        # Corner brackets — fixed pixel thickness
        bracket = 14
        stroke = 2
        p.setPen(QPen(self._color, stroke, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.SquareCap))

        x0, y0 = rect.left(), rect.top()
        x1, y1 = rect.right(), rect.bottom()

        # Top-left
        p.drawLine(x0, y0, x0 + bracket, y0)
        p.drawLine(x0, y0, x0, y0 + bracket)
        # Top-right
        p.drawLine(x1 - bracket, y0, x1, y0)
        p.drawLine(x1, y0, x1, y0 + bracket)
        # Bottom-right
        p.drawLine(x1 - bracket, y1, x1, y1)
        p.drawLine(x1, y1 - bracket, x1, y1)
        # Bottom-left
        p.drawLine(x0, y1, x0 + bracket, y1)
        p.drawLine(x0, y1 - bracket, x0, y1)

        # Direction chevron — only when h is generous enough
        if self.height() >= 28:
            p.setPen(QPen(self._color, 2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            cy = rect.center().y()
            if self._direction == "right":
                cx = x1 - 14
                p.drawLine(cx - 5, cy - 5, cx, cy)
                p.drawLine(cx, cy, cx - 5, cy + 5)
            else:
                cx = x0 + 14
                p.drawLine(cx + 5, cy - 5, cx, cy)
                p.drawLine(cx, cy, cx + 5, cy + 5)

        # Strip label — only when w + h give it room
        if self.width() >= 200 and self.height() >= 36 and self._total > 0:
            label = f"ROW {self._row + 1:02d} / {self._total:02d}"
            p.setBrush(self._color)
            p.setPen(Qt.PenStyle.NoPen)
            font = self.font()
            font.setPointSize(9)
            font.setBold(True)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
            p.setFont(font)
            metrics = p.fontMetrics()
            text_w = metrics.horizontalAdvance(label)
            pad = 6
            badge = QRect(x0 + 6, y0 + 6,
                          text_w + pad * 2, metrics.height() + 4)
            p.drawRect(badge)
            p.setPen(QColor("#0f0f0f"))
            p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), label)

        p.end()
