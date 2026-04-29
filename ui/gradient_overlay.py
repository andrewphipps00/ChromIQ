"""Gradient wash overlay — paints a colour-to-transparent strip at the top of a tab pane."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QWidget

_HEIGHT = 50  # px
_ALPHA  = 15  # ≈ 6 % opacity at the top


class GradientOverlay(QWidget):
    """Transparent overlay that draws a vertical gradient over the top 50 px.

    Passes all mouse/keyboard events through to siblings beneath it.
    Install one on each tab widget after the tab widget is fully built.
    """

    def __init__(self, color: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        parent.installEventFilter(self)
        self._fit()
        self.raise_()

    # ------------------------------------------------------------------

    def _fit(self) -> None:
        p = self.parent()
        if p:
            self.setGeometry(0, 0, p.width(), _HEIGHT)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parent():
            t = event.type()
            if t == QEvent.Type.Resize:
                self._fit()
                self.raise_()
            elif t == QEvent.Type.Show:
                self.raise_()
        return False

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, 0, _HEIGHT)
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        # Both stops use the same hue — avoids the black fringe from
        # pre-multiplied alpha interpolation toward QColor(0,0,0,0).
        n = 8
        for i in range(n + 1):
            t = i / n
            a = round(_ALPHA * (1 - t) ** 2)
            grad.setColorAt(t, QColor(r, g, b, a))
        painter.fillRect(self.rect(), grad)
        painter.end()

    def showEvent(self, event) -> None:  # type: ignore[override]
        self.raise_()
