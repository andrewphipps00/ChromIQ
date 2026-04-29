"""Full-width tab bar where each tab gets one of the 5 spectrum colors.

Drop-in replacement for the standard QTabBar inside ChromIQ's QTabWidget.
Each tab paints a 3px accent strip at the top in its own color, plus
a faint color tint when active. Tabs expand to fill the full width.

Usage in main_window.py:

    from ui.spectrum_tab_bar import SpectrumTabBar
    self._tabs = QTabWidget(central)
    self._tabs.setTabBar(SpectrumTabBar(self._tabs))
    self._tabs.setDocumentMode(True)
    # ... addTab() calls remain unchanged
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyleOptionTab, QTabBar, QWidget

# Five spectrum colors — same order as tabs:
# 1 Create · 2 Print · 3 Measure · 4 Profile · 5 Check
SPECTRUM = [
    "#ff4573",  # 1 Create Chart
    "#ffb42d",  # 2 Print Chart
    "#56d6a5",  # 3 Measure
    "#37bcd6",  # 4 Build Profile
    "#9f82ff",  # 5 Check & Refine
]

BG_BAR     = "#0f0f0f"
BG_INACTIVE = "transparent"
BG_ACTIVE   = "#1e1e1e"
TEXT_INACTIVE = "#909090"
TEXT_ACTIVE   = "#ffffff"
SEP           = "#2a2a2a"


class SpectrumTabBar(QTabBar):
    """Custom tab bar — full width, per-tab spectrum accent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setExpanding(True)        # spread tabs to fill the bar
        self.setDrawBase(False)        # we paint our own bottom line
        self.setUsesScrollButtons(False)
        self.setDocumentMode(True)
        self.setFixedHeight(48)

    # ------------------------------------------------------------------
    # Sizing — each tab gets equal share of total width
    # ------------------------------------------------------------------
    def tabSizeHint(self, index: int):  # type: ignore[override]
        sh = super().tabSizeHint(index)
        n = self.count()
        if n > 0 and self.parent() is not None:
            # Use the parent (QTabWidget) width so tabs stretch full bar
            parent = self.parent()
            total = parent.width() if parent else self.width()
            sh.setWidth(max(120, total // n))
        sh.setHeight(48)
        return sh

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Fill the entire bar background — covers any gap to the right
        p.fillRect(self.rect(), QColor(BG_BAR))

        # Bottom border line
        p.setPen(QPen(QColor(SEP), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        for i in range(self.count()):
            rect: QRect = self.tabRect(i)
            if not rect.isValid():
                continue

            color_hex = SPECTRUM[i] if i < len(SPECTRUM) else SPECTRUM[-1]
            color = QColor(color_hex)
            is_active = (i == self.currentIndex())

            # Active tab: subtle color-tint background
            if is_active:
                p.fillRect(rect, QColor(BG_ACTIVE))
                tint = QColor(color)
                tint.setAlpha(15)
                p.fillRect(rect, tint)

            # Top accent strip (3px)
            strip_h = 3
            if is_active:
                p.fillRect(rect.x(), rect.y(),
                           rect.width(), strip_h, color)
            else:
                # Inactive: very faint colored hint
                hint = QColor(color)
                hint.setAlpha(60)
                p.fillRect(rect.x(), rect.y() + 1,
                           rect.width(), 2, hint)

            # Vertical separator on the right edge of every tab except last
            if i < self.count() - 1:
                p.setPen(QPen(QColor(SEP), 1))
                p.drawLine(rect.x() + rect.width() - 1, rect.y() + 8,
                           rect.x() + rect.width() - 1,
                           rect.y() + rect.height() - 8)

            # Active underline glow
            if is_active:
                under = QColor(color)
                under.setAlpha(120)
                p.fillRect(rect.x() + 14, rect.y() + rect.height() - 4,
                           rect.width() - 28, 1, under)

            # Label
            text_color = TEXT_ACTIVE if is_active else TEXT_INACTIVE
            p.setPen(QColor(text_color))
            font: QFont = self.font()
            font.setPointSize(13)
            font.setWeight(QFont.Weight.DemiBold if is_active
                           else QFont.Weight.Medium)
            p.setFont(font)
            label_rect = rect.adjusted(8, strip_h, -8, -2)
            p.drawText(label_rect,
                       int(Qt.AlignmentFlag.AlignCenter),
                       self.tabText(i))

        p.end()

    # ------------------------------------------------------------------
    # Helpers — used by Workspace tinting in the body of each tab
    # ------------------------------------------------------------------
    @staticmethod
    def color_for(index: int) -> str:
        if 0 <= index < len(SPECTRUM):
            return SPECTRUM[index]
        return SPECTRUM[-1]
