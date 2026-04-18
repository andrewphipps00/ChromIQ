"""Shared widget factory helpers."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QPushButton, QStyle, QWidget


def make_browse_button(parent: QWidget | None = None, tooltip: str = "Browse…") -> QPushButton:
    """Create a standardised file-browse button with folder icon."""
    btn = QPushButton(parent)
    btn.setObjectName("browse")
    btn.setFixedWidth(36)
    btn.setToolTip(tooltip)
    icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
    btn.setIcon(icon)
    return btn
