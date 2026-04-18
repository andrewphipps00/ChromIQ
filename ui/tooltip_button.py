"""Clickable info icon that shows a detailed tooltip dialog."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from core.resource_path import resource_path

log = get_logger(__name__)

_ICON_SIZE = 18  # px


class TooltipButton(QToolButton):
    """Small ⓘ icon button that opens a modal info dialog on click."""

    def __init__(
        self,
        title: str,
        body: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._body = body.strip()

        self.setObjectName("tooltip_btn")
        self.setToolTip(f"{title}\n\nClick for details")
        self.setFixedSize(QSize(_ICON_SIZE + 4, _ICON_SIZE + 4))
        self._set_icon()
        self.clicked.connect(self._show_dialog)
        log.debug("TooltipButton created: %s", title)

    # ------------------------------------------------------------------

    def _set_icon(self) -> None:
        path = resource_path("assets/tooltip.png")
        px = QPixmap(str(path))
        if px.isNull():
            log.warning("tooltip.png not found at %s", path)
            self.setText("ⓘ")
        else:
            scaled = px.scaled(
                _ICON_SIZE, _ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setIcon(QIcon(scaled))
            self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))

    def _show_dialog(self) -> None:
        log.debug("Tooltip dialog opened: %s", self._title)
        dlg = _InfoDialog(self._title, self._body, self.window())
        dlg.exec()


class _InfoDialog(QDialog):
    def __init__(self, title: str, body: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setMaximumWidth(540)
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
