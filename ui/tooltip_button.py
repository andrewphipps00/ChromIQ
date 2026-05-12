"""Clickable ⓘ icon button that opens a detailed info dialog.

The icon is drawn in code using the active tab's accent colour (``TooltipButton.ACCENT``),
set by MainWindow whenever the active tab changes.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor, QFont, QGuiApplication, QIcon, QPainter, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger

log = get_logger(__name__)

_ICON_SIZE = 18  # logical px


class TooltipButton(QToolButton):
    """Small ⓘ icon button that opens a modal info dialog on click."""

    # Set by MainWindow._on_tab_changed() each time the tab switches.
    ACCENT: str = "#1FB7C7"

    def __init__(
        self,
        title: str,
        body: str,
        parent: QWidget | None = None,
        min_width: int = 420,
    ) -> None:
        super().__init__(parent)
        self._title     = title
        self._body      = body.strip()
        self._min_width = min_width

        self.setObjectName("tooltip_btn")
        self.setToolTip(f"{title}\n\nClick for details")
        self.setFixedSize(QSize(_ICON_SIZE + 4, _ICON_SIZE + 4))
        self._set_icon()
        self.clicked.connect(self._show_dialog)
        log.debug("TooltipButton created: %s", title)

    # ------------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.setEnabled(True)

    def _set_icon(self) -> None:
        color = getattr(self, "_color_override", None) or self.__class__.ACCENT
        self.setIcon(self._draw_icon(QColor(color)))
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))

    def _draw_icon(self, color: QColor) -> QIcon:
        dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(_ICON_SIZE * dpr)
        px   = QPixmap(phys, phys)
        px.fill(Qt.GlobalColor.transparent)

        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(color, max(1.0, phys * 0.10))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        margin = int(phys * 0.07)
        p.drawEllipse(margin, margin, phys - 2 * margin, phys - 2 * margin)

        # Italic "i" glyph
        font = QFont()
        font.setFamilies(["Georgia", "Times New Roman", "serif"])
        font.setItalic(True)
        font.setBold(True)
        font.setPixelSize(max(8, int(phys * 0.54)))
        p.setFont(font)
        p.setPen(color)
        p.drawText(
            QRect(0, 0, phys, int(phys * 1.05)),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            "i",
        )
        p.end()
        px.setDevicePixelRatio(dpr)
        return QIcon(px)

    # ------------------------------------------------------------------
    def _show_dialog(self) -> None:
        log.debug("Tooltip dialog opened: %s", self._title)
        dlg = _InfoDialog(self._title, self._body, self.window(), self._min_width)
        dlg.exec()


class _InfoDialog(QDialog):
    def __init__(
        self,
        title: str,
        body:  str,
        parent: QWidget | None,
        min_width: int = 420,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(min_width)
        self.setMaximumWidth(max(min_width + 120, 540))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 16)

        heading = QLabel(title, self)
        heading.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        text = QLabel(body, self)
        text.setWordWrap(True)
        text.setStyleSheet("color: #c8c8c8; line-height: 1.5;")
        text.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(text)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        bb.rejected.connect(self.accept)
        layout.addWidget(bb)

        self.adjustSize()


InfoDialog = _InfoDialog  # public alias for use outside this module
