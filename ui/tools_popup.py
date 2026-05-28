"""Speech-bubble popup listing the standalone utilities.

Opened from the masthead Tools button; auto-closes on outside click (Qt.Popup).
Rounded panel with a small upward-pointing tail aligned under the button. Each
row behaves like a combobox item — hover highlight, click emits ``selected``.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QMouseEvent, QPainter, QPainterPath,
    QPaintEvent, QPen,
)
from PyQt6.QtWidgets import QApplication, QWidget


# Per-mode visual tokens. Hover background uses a translucent accent so the
# row sits naturally on the panel fill without an explicit border.
_PALETTE_DARK = {
    "panel_bg":      "#1f1f1f",
    "panel_border":  "#333333",
    "text":          "#e6e6e6",
    "text_hover":    "#ffffff",
    "hover_bg":      "#2a2a2a",
    "shadow":        QColor(0, 0, 0, 110),
}
_PALETTE_LIGHT = {
    "panel_bg":      "#ffffff",
    "panel_border":  "#d0ccc6",
    "text":          "#22211f",
    "text_hover":    "#22211f",
    "hover_bg":      "#f0ece6",
    "shadow":        QColor(0, 0, 0, 30),
}


@dataclass(frozen=True)
class ToolEntry:
    key:   str
    label: str


_ENTRIES: tuple[ToolEntry, ...] = (
    ToolEntry("ti2_relayout",  "Edit / create chart layout"),
    ToolEntry("average",      "Average measurements"),
    ToolEntry("merge",        "Merge measurements"),
    ToolEntry("ti1_to_i1p",   "Convert TI1 → i1Profiler"),
    ToolEntry("i1p_to_ti3",   "Convert i1Profiler → TI3"),
    ToolEntry("i1p_to_ti1",   "Convert i1Profiler → TI1"),
)


class ToolsPopup(QWidget):
    """Speech-bubble popup. Show via ``show_under(button)``; emits ``selected``."""

    selected = pyqtSignal(str)  # tool key

    TAIL_W      = 16    # base width of the tail triangle
    TAIL_H      = 9     # height of the tail (sticks up above the panel)
    CORNER_R    = 10
    ROW_H       = 36
    H_PAD       = 18    # horizontal padding inside the panel
    V_PAD       = 8     # vertical padding above the first / below the last row
    PANEL_MARGIN = 12   # transparent space around panel for the drop shadow

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # NoDropShadowWindowHint suppresses the platform's own popup shadow.
        # On Windows that native shadow sits on the bottom/right edges of this
        # translucent frameless window and reads as a hard border; we paint our
        # own soft shadow in paintEvent, so the OS one is both redundant and ugly.
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self._mode    = "dark"
        self._palette = _PALETTE_DARK
        self._hover_index: int = -1
        self._tail_offset_x: int = 0  # tail apex X within the widget
        self._anchor: QWidget | None = None  # button we're anchored under, for hover-reset on close

        font = QFont()
        font.setPixelSize(13)
        self.setFont(font)

        self._compute_size()

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self._palette = _PALETTE_LIGHT if self._mode == "light" else _PALETTE_DARK
        self.update()

    # ------------------------------------------------------------------
    def _compute_size(self) -> None:
        fm = QFontMetricsF(self.font())
        text_w = max(fm.horizontalAdvance(e.label) for e in _ENTRIES)
        panel_w = int(text_w) + 2 * self.H_PAD
        panel_w = max(panel_w, 240)
        panel_h = len(_ENTRIES) * self.ROW_H + 2 * self.V_PAD
        w = panel_w + 2 * self.PANEL_MARGIN
        h = panel_h + 2 * self.PANEL_MARGIN + self.TAIL_H
        self.setFixedSize(w, h)

    def _panel_rect(self) -> QRect:
        return QRect(
            self.PANEL_MARGIN,
            self.PANEL_MARGIN + self.TAIL_H,
            self.width()  - 2 * self.PANEL_MARGIN,
            self.height() - 2 * self.PANEL_MARGIN - self.TAIL_H,
        )

    def _row_rect(self, index: int) -> QRect:
        panel = self._panel_rect()
        top = panel.top() + self.V_PAD + index * self.ROW_H
        return QRect(panel.left() + 6, top, panel.width() - 12, self.ROW_H)

    # ------------------------------------------------------------------
    def show_under(self, anchor: QWidget) -> None:
        """Position so the tail apex lands at the horizontal centre of ``anchor``."""
        self._anchor = anchor
        gp = anchor.mapToGlobal(QPoint(0, anchor.height()))
        anchor_center_x = gp.x() + anchor.width() // 2

        # Panel ideal X: centred under the anchor.
        ideal_x = anchor_center_x - self.width() // 2

        # Clamp to the anchor widget's screen so we never cross the screen edge.
        screen = anchor.screen()
        avail = screen.availableGeometry() if screen else None
        if avail:
            min_x = avail.left() + 4
            max_x = avail.right() - self.width() - 4
            x = max(min_x, min(ideal_x, max_x))
        else:
            x = ideal_x

        y = gp.y() + 2  # slight gap below the button

        # Tail apex offset within the widget, so paintEvent draws it pointing
        # straight up at the anchor regardless of clamping.
        self._tail_offset_x = anchor_center_x - x
        self._tail_offset_x = max(
            self.PANEL_MARGIN + self.CORNER_R + self.TAIL_W,
            min(self._tail_offset_x, self.width() - self.PANEL_MARGIN - self.CORNER_R - self.TAIL_W),
        )

        self.move(x, y)
        self._hover_index = -1
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    def paintEvent(self, _ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        pal = self._palette
        panel = self._panel_rect()

        # Build speech-bubble path: rounded rectangle + triangular tail at top.
        bubble = QPainterPath()
        bubble.addRoundedRect(
            float(panel.left()), float(panel.top()),
            float(panel.width()), float(panel.height()),
            float(self.CORNER_R), float(self.CORNER_R),
        )
        tail = QPainterPath()
        apex_x = self._tail_offset_x
        apex_y = panel.top() - self.TAIL_H + 1   # +1 to overlap and avoid hairline gap
        base_y = panel.top() + 1
        tail.moveTo(float(apex_x),                       float(apex_y))
        tail.lineTo(float(apex_x - self.TAIL_W / 2),     float(base_y))
        tail.lineTo(float(apex_x + self.TAIL_W / 2),     float(base_y))
        tail.closeSubpath()
        bubble = bubble.united(tail)

        # Soft shadow — translate the bubble path down a few pixels and fill faint.
        shadow = QPainterPath(bubble)
        shadow.translate(0, 3)
        p.fillPath(shadow, pal["shadow"])

        # Panel fill + border (single combined path so the tail joins seamlessly).
        p.fillPath(bubble, QColor(pal["panel_bg"]))
        p.setPen(QPen(QColor(pal["panel_border"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(bubble)

        # Rows
        for i, entry in enumerate(_ENTRIES):
            row = self._row_rect(i)
            if i == self._hover_index:
                p.setBrush(QColor(pal["hover_bg"]))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(row, 6, 6)
                text_color = pal["text_hover"]
            else:
                text_color = pal["text"]

            p.setPen(QColor(text_color))
            p.setFont(self.font())
            p.drawText(
                QRect(row.left() + 12, row.top(), row.width() - 24, row.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                entry.label,
            )

        p.end()

    # ------------------------------------------------------------------
    def _index_at(self, y: int) -> int:
        for i in range(len(_ENTRIES)):
            if self._row_rect(i).contains(self._row_rect(i).left() + 4, y):
                return i
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint().y())
        if idx != self._hover_index:
            self._hover_index = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_index != -1:
            self._hover_index = -1
            self.update()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        # Qt.Popup grabs the mouse the moment the anchor button is pressed, so
        # the underlying QToolButton never receives the Leave event that would
        # clear its :hover background. After dismissing the popup the button
        # would otherwise read as still-highlighted until the cursor next
        # enters and leaves it. Send a synthetic Leave so Qt re-evaluates.
        super().hideEvent(event)
        anchor = self._anchor
        if anchor is not None:
            QApplication.sendEvent(anchor, QEvent(QEvent.Type.Leave))
            anchor.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        # Clicks outside the panel close the popup (Qt.Popup also handles this
        # by closing the window — but only for clicks fully outside our
        # geometry; here we explicitly close when the panel area is missed).
        pt = event.position().toPoint()
        if not self._panel_rect().contains(pt):
            self.close()
            return
        idx = self._index_at(pt.y())
        if idx < 0:
            return
        key = _ENTRIES[idx].key
        self.close()
        self.selected.emit(key)
