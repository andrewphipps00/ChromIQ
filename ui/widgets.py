"""Shared widget factory helpers."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QPushButton,
    QSpinBox,
    QStyle,
    QWidget,
)


class NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _sidebar_urls(extra_path: str = "") -> list[QUrl]:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "ChromIQ",
    ]
    if extra_path:
        candidates.append(Path(extra_path))
    return [QUrl.fromLocalFile(str(p)) for p in candidates if p.exists()]


def open_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_dir: str = "",
    extra_path: str = "",
) -> str:
    """Open a Qt file dialog with sidebar shortcuts and proper file-type filtering.

    Non-matching files are hidden when name_filter is set.
    Returns the selected file path, or an empty string if cancelled.
    """
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    dlg.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    if name_filter:
        dlg.setNameFilter(name_filter)
    dlg.setSidebarUrls(_sidebar_urls(extra_path))
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


def open_dir_dialog(
    parent: QWidget,
    title: str,
    start_dir: str = "",
    extra_path: str = "",
) -> str:
    """Open a Qt directory dialog with sidebar shortcuts.

    Returns the selected directory path, or an empty string if cancelled.
    """
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    dlg.setOptions(
        QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly
    )
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    urls = _sidebar_urls(extra_path)
    urls.append(QUrl.fromLocalFile("/Applications"))
    dlg.setSidebarUrls(urls)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        dirs = dlg.selectedFiles()
        return dirs[0] if dirs else ""
    return ""


def make_browse_button(parent: QWidget | None = None, tooltip: str = "Browse…") -> QPushButton:
    """Create a standardised file-browse button with folder icon."""
    btn = QPushButton(parent)
    btn.setObjectName("browse")
    btn.setFixedWidth(36)
    btn.setToolTip(tooltip)
    icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
    btn.setIcon(icon)
    return btn
