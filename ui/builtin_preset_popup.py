"""Speech-bubble popup listing the built-in Create Chart presets.

Opened from the Create Chart tab's star button (next to GUIDED / MANUAL);
auto-closes on outside click (Qt.Popup). Same rounded-panel-with-tail look as
``ui.tools_popup.ToolsPopup``, but the rows are grouped under a small instrument
header (e.g. "i1Pro", "ColorMunki"). Header rows are inert; preset rows behave
like combobox items — hover highlight, click emits ``selected`` with the preset
key. Picking one is wired by the tab to the very same flow as choosing the
preset in the Manual presets dropdown (name prompt + generate).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QMouseEvent, QPainter, QPainterPath,
    QPaintEvent, QPen, QPolygonF,
)
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from ui.styles import SPEC_MAGENTA


# Per-mode visual tokens — kept in step with ui.tools_popup so the two popups
# read as siblings. ``header_text`` is the muted instrument-label colour.
_PALETTE_DARK = {
    "panel_bg":      "#1f1f1f",
    "panel_border":  "#333333",
    "text":          "#e6e6e6",
    "text_hover":    "#ffffff",
    "header_text":   "#8a8a8a",
    "hover_bg":      "#2a2a2a",
    "shadow":        QColor(0, 0, 0, 110),
}
_PALETTE_LIGHT = {
    "panel_bg":      "#ffffff",
    "panel_border":  "#d0ccc6",
    "text":          "#22211f",
    "text_hover":    "#22211f",
    "header_text":   "#9b958c",
    "hover_bg":      "#f0ece6",
    "shadow":        QColor(0, 0, 0, 30),
}


class BuiltinPresetButton(QToolButton):
    """Star button that opens the Built-in presets overlay.

    Sits next to the GUIDED / MANUAL switch. The five-point star is the app's
    spectrum magenta in both themes (the masthead motif), painted directly in
    ``paintEvent`` like the welcome "?" button — building a QIcon from a
    DPR-scaled pixmap clips on Retina, painting in logical coords doesn't.
    The QSS ``#tooltip_btn`` rule still paints the hover background (we chain to
    ``super().paintEvent``). ``set_appearance`` is a no-op (magenta is
    theme-independent), kept only so the apply_theme broadcast can call it.
    """

    # Star diameter as a fraction of the button — leaves a comfortable margin so
    # the glyph reads as a small icon inside a larger hit target.
    STAR_FRAC = 0.58

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tooltip_btn")
        self.setToolTip("Built-in presets")
        self.setFixedSize(QSize(40, 40))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_appearance(self, mode: str) -> None:
        pass  # magenta is theme-independent — nothing to repaint

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(ev)  # QSS background (incl. :hover) under the glyph
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        r_out = min(self.width(), self.height()) * self.STAR_FRAC / 2.0
        r_in  = r_out * 0.42
        pts = QPolygonF()
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5    # start at the top point
            r = r_out if i % 2 == 0 else r_in
            pts.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        color = QColor(SPEC_MAGENTA)
        if not self._hover:
            color.setAlpha(230)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(pts)
        p.end()


@dataclass(frozen=True)
class _VisualRow:
    """One painted line: an instrument header, or a selectable preset."""
    kind:   str          # "header" | "item"
    text:   str
    key:    str | None   # preset key for "item" rows, else None
    top:    int          # y of the row within the widget
    height: int


class BuiltinPresetPopup(QWidget):
    """Grouped speech-bubble popup. Show via ``show_under(button)``.

    ``groups`` is ``[(instrument, [(overlay_label, key), …]), …]`` — exactly
    what the tab derives from BUILTIN_PRESET_GROUPS. Emits ``selected(key)``.
    """

    selected = pyqtSignal(str)  # preset key

    TAIL_W       = 16   # base width of the tail triangle
    TAIL_H       = 9    # height of the tail (sticks up above the panel)
    CORNER_R     = 10
    HEADER_H     = 26   # instrument header row
    ROW_H        = 34   # preset row
    H_PAD        = 18   # horizontal padding inside the panel
    V_PAD        = 8    # vertical padding above the first / below the last row
    ITEM_INDENT  = 12   # extra left inset for preset labels under a header
    PANEL_MARGIN = 12   # transparent space around panel for the drop shadow

    def __init__(
        self,
        groups: list[tuple[str, list[tuple[str, str]]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # NoDropShadowWindowHint suppresses the platform's own popup shadow (see
        # ui.tools_popup for the rationale); we paint our own soft shadow.
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self._groups  = groups
        self._mode    = "dark"
        self._palette = _PALETTE_DARK
        self._hover_index: int = -1
        self._tail_offset_x: int = 0
        self._anchor: QWidget | None = None

        self._item_font = QFont()
        self._item_font.setPixelSize(13)
        self._header_font = QFont()
        self._header_font.setPixelSize(11)
        self._header_font.setBold(True)
        self.setFont(self._item_font)

        self._rows: list[_VisualRow] = []
        self._build_rows()
        self._compute_size()

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self._palette = _PALETTE_LIGHT if self._mode == "light" else _PALETTE_DARK
        self.update()

    # ------------------------------------------------------------------
    def _build_rows(self) -> None:
        """Flatten the grouped presets into headers + item rows with y offsets."""
        self._rows = []
        y = self.PANEL_MARGIN + self.TAIL_H + self.V_PAD
        for instr, entries in self._groups:
            self._rows.append(_VisualRow("header", instr, None, y, self.HEADER_H))
            y += self.HEADER_H
            for label, key in entries:
                self._rows.append(_VisualRow("item", label, key, y, self.ROW_H))
                y += self.ROW_H

    def _compute_size(self) -> None:
        item_fm   = QFontMetricsF(self._item_font)
        header_fm = QFontMetricsF(self._header_font)
        text_w = 0.0
        for row in self._rows:
            if row.kind == "item":
                # Items inset by ROW(6)+TEXT(12)+ITEM_INDENT on the left.
                w = item_fm.horizontalAdvance(row.text) + self.ITEM_INDENT
            else:
                w = header_fm.horizontalAdvance(row.text)
            text_w = max(text_w, w)
        inner = 2 * (6 + 12)
        panel_w = math.ceil(text_w) + inner + self.H_PAD
        panel_w = max(panel_w, 260)
        content_h = sum(r.height for r in self._rows)
        panel_h = content_h + 2 * self.V_PAD
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

    def _row_rect(self, row: _VisualRow) -> QRect:
        panel = self._panel_rect()
        return QRect(panel.left() + 6, row.top, panel.width() - 12, row.height)

    # ------------------------------------------------------------------
    def show_under(self, anchor: QWidget) -> None:
        """Position so the tail apex lands at the horizontal centre of ``anchor``."""
        self._anchor = anchor
        gp = anchor.mapToGlobal(QPoint(0, anchor.height()))
        anchor_center_x = gp.x() + anchor.width() // 2

        ideal_x = anchor_center_x - self.width() // 2
        screen = anchor.screen()
        avail = screen.availableGeometry() if screen else None
        if avail:
            min_x = avail.left() + 4
            max_x = avail.right() - self.width() - 4
            x = max(min_x, min(ideal_x, max_x))
        else:
            x = ideal_x

        y = gp.y() + 2

        self._tail_offset_x = anchor_center_x - x
        self._tail_offset_x = max(
            self.PANEL_MARGIN + self.CORNER_R + self.TAIL_W,
            min(self._tail_offset_x,
                self.width() - self.PANEL_MARGIN - self.CORNER_R - self.TAIL_W),
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

        bubble = QPainterPath()
        bubble.addRoundedRect(
            float(panel.left()), float(panel.top()),
            float(panel.width()), float(panel.height()),
            float(self.CORNER_R), float(self.CORNER_R),
        )
        tail = QPainterPath()
        apex_x = self._tail_offset_x
        apex_y = panel.top() - self.TAIL_H + 1
        base_y = panel.top() + 1
        tail.moveTo(float(apex_x),                   float(apex_y))
        tail.lineTo(float(apex_x - self.TAIL_W / 2), float(base_y))
        tail.lineTo(float(apex_x + self.TAIL_W / 2), float(base_y))
        tail.closeSubpath()
        bubble = bubble.united(tail)

        shadow = QPainterPath(bubble)
        shadow.translate(0, 3)
        p.fillPath(shadow, pal["shadow"])

        p.fillPath(bubble, QColor(pal["panel_bg"]))
        p.setPen(QPen(QColor(pal["panel_border"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(bubble)

        for i, row in enumerate(self._rows):
            rect = self._row_rect(row)
            if row.kind == "header":
                p.setPen(QColor(pal["header_text"]))
                p.setFont(self._header_font)
                p.drawText(
                    QRect(rect.left() + 12, rect.top(), rect.width() - 24, rect.height()),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                    row.text,
                )
                continue
            if i == self._hover_index:
                p.setBrush(QColor(pal["hover_bg"]))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect, 6, 6)
                text_color = pal["text_hover"]
            else:
                text_color = pal["text"]
            p.setPen(QColor(text_color))
            p.setFont(self._item_font)
            p.drawText(
                QRect(rect.left() + 12 + self.ITEM_INDENT, rect.top(),
                      rect.width() - 24 - self.ITEM_INDENT, rect.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                row.text,
            )

        p.end()

    # ------------------------------------------------------------------
    def _index_at(self, pt: QPoint) -> int:
        """Index of the selectable (item) row under ``pt``; -1 over headers,
        gaps, or the transparent margin to either side of the panel."""
        for i, row in enumerate(self._rows):
            if row.kind != "item":
                continue
            if self._row_rect(row).contains(pt):
                return i
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint())
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
        # Same synthetic-Leave fix as ToolsPopup: the Qt.Popup mouse grab robs
        # the anchor button of the Leave that would clear its :hover state.
        super().hideEvent(event)
        anchor = self._anchor
        if anchor is not None:
            QApplication.sendEvent(anchor, QEvent(QEvent.Type.Leave))
            anchor.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pt = event.position().toPoint()
        if not self._panel_rect().contains(pt):
            self.close()
            return
        idx = self._index_at(pt)
        if idx < 0:
            return
        key = self._rows[idx].key
        self.close()
        if key is not None:
            self.selected.emit(key)
