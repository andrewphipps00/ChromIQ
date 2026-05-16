"""Shared widget factory helpers."""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QSize, QSortFilterProxyModel, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QPushButton,
    QSizeGrip,
    QSpinBox,
    QStyle,
    QToolBar,
    QToolButton,
    QWidget,
)


class ButtonFontFilter(QObject):
    """Applies Menlo + AllUppercase to every QPushButton as it is polished."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QPushButton) and event.type() == QEvent.Type.Polish:
            font = obj.font()
            font.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
            font.setCapitalization(QFont.Capitalization.AllUppercase)
            obj.setFont(font)
        return False


class _ExtensionFilterProxy(QSortFilterProxyModel):
    """Hides files whose extension is not in the allowed set; directories always shown."""

    def __init__(self, extensions: list[str], parent=None) -> None:
        super().__init__(parent)
        self._exts = {e.lower() for e in extensions}

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._exts:
            return True
        src = self.sourceModel()
        idx = src.index(source_row, 0, source_parent)
        try:
            if src.isDir(idx):
                return True
            name = src.fileName(idx)
        except Exception:
            return True
        dot = name.rfind(".")
        if dot < 0:
            return False
        return ("." + name[dot + 1:].lower()) in self._exts


def _parse_extensions(name_filter: str) -> list[str]:
    """Return ['.ti3', '.icc'] from 'ICC profiles (*.icc *.icm)'."""
    return ["." + e.lower() for e in re.findall(r"\*\.(\w+)", name_filter)]


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


def icc_profile_paths() -> list[str]:
    """Common OS-level ICC/ICM profile directories for file-dialog sidebars."""
    import os
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return [
            "/Library/ColorSync/Profiles",
            "/System/Library/ColorSync/Profiles",
            str(home / "Library/ColorSync/Profiles"),
        ]
    if sys.platform.startswith("win"):
        paths = [r"C:\Windows\System32\spool\drivers\color"]
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            paths.append(str(Path(local) / "Microsoft" / "Windows" / "Color"))
        return paths
    return [
        "/usr/share/color/icc",
        "/usr/local/share/color/icc",
        str(home / ".color/icc"),
    ]


def _sidebar_urls(extra_path: str = "", extra_paths: tuple | list = ()) -> list[QUrl]:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "ChromIQ",
    ]
    if extra_path:
        candidates.append(Path(extra_path))
    for p in extra_paths:
        if p:
            candidates.append(Path(p))
    return [QUrl.fromLocalFile(str(p)) for p in candidates if p.exists()]


_NAV_BUTTONS = {
    "backButton":     QStyle.StandardPixmap.SP_ArrowBack,
    "forwardButton":  QStyle.StandardPixmap.SP_ArrowForward,
    "toParentButton": QStyle.StandardPixmap.SP_FileDialogToParent,
}

# Arrow drawn at _NAV_ARROW_SIZE, centred inside a _NAV_BTN_SIZE canvas.
# Qt places the canvas icon at top-left of the button, so centering is
# baked into the transparent padding of the canvas image.
_NAV_BTN_SIZE   = QSize(28, 28)
_NAV_ARROW_SIZE = QSize(16, 16)


def _nav_icon(icon: QIcon, color: QColor) -> QIcon:
    """Recolor icon and centre it on a transparent canvas matching button size."""
    raw = icon.pixmap(_NAV_ARROW_SIZE)
    # recolor
    colored = QPixmap(raw.size())
    colored.fill(Qt.GlobalColor.transparent)
    p = QPainter(colored)
    p.drawPixmap(0, 0, raw)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(colored.rect(), color)
    p.end()
    # centre on canvas
    canvas = QPixmap(_NAV_BTN_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    x = (_NAV_BTN_SIZE.width()  - _NAV_ARROW_SIZE.width())  // 2
    y = (_NAV_BTN_SIZE.height() - _NAV_ARROW_SIZE.height()) // 2
    p.drawPixmap(x, y, colored)
    p.end()
    return QIcon(canvas)


def _style_file_dialog_toolbar(dlg: QFileDialog) -> None:
    arrow_color = QColor("#e0e0e0")
    style = dlg.style()
    for name, sp in _NAV_BUTTONS.items():
        btn = dlg.findChild(QToolButton, name)
        if btn:
            btn.setIcon(_nav_icon(style.standardIcon(sp), arrow_color))
            btn.setIconSize(_NAV_BTN_SIZE)
            btn.setFixedSize(_NAV_BTN_SIZE)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    grip = dlg.findChild(QSizeGrip)
    if grip:
        grip.hide()


def open_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_dir: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
) -> str:
    """Open a Qt file dialog with sidebar shortcuts and proper file-type filtering.

    Non-matching files are hidden when name_filter is set.
    Returns the selected file path, or an empty string if cancelled.
    """
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    dlg.setOptions(QFileDialog.Option.DontUseNativeDialog)
    _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    if name_filter:
        dlg.setNameFilter(name_filter)
        exts = _parse_extensions(name_filter)
        if exts:
            dlg.setProxyModel(_ExtensionFilterProxy(exts, dlg))
    dlg.setSidebarUrls(_sidebar_urls(extra_path, extra_paths))
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
    _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    import sys as _sys
    urls = _sidebar_urls(extra_path)
    if _sys.platform == "darwin":
        urls.append(QUrl.fromLocalFile("/Applications"))
    dlg.setSidebarUrls(urls)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        dirs = dlg.selectedFiles()
        return dirs[0] if dirs else ""
    return ""


def load_folder_icon(name: str) -> QIcon:
    """Load a colored folder icon from assets/folder/<name>.png.

    Falls back to the OS system folder icon if the file is not found.
    """
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication
    px = QPixmap(str(resource_path(f"assets/folder/{name}.png")))
    if not px.isNull():
        dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(20 * dpr)
        scaled = px.scaled(phys, phys,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return QIcon(scaled)
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)


def tint_dialog_primary(dlg: "QWidget", color: str) -> None:
    """Stamp tab accent color onto every QPushButton#primary inside a dialog (v2 only).

    Safe to call on any dialog — no-op if no primary buttons are present.
    """
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    hover = "#{:02x}{:02x}{:02x}".format(int(r * 0.82), int(g * 0.82), int(b * 0.82))
    for btn in dlg.findChildren(QPushButton):
        if btn.objectName() == "primary":
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 1px solid {color};"
                f" color: #0a0a0a; font-weight: 700; }}"
                f"QPushButton:hover {{ background: {hover}; border-color: {hover}; }}"
            )


def load_refresh_icon(name: str) -> QIcon:
    """Load a colored refresh icon from assets/refresh/<name>.png.

    Falls back to the OS browser-reload icon if the file is not found.
    """
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication
    px = QPixmap(str(resource_path(f"assets/refresh/{name}.png")))
    if not px.isNull():
        dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(20 * dpr)
        scaled = px.scaled(phys, phys,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return QIcon(scaled)
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)


def make_browse_button(
    parent: QWidget | None = None,
    tooltip: str = "Browse…",
    icon: str = "folder",
) -> QPushButton:
    """Create a standardised file-browse button with a folder icon.

    Pass the icon name (without path or extension) to select a colored variant,
    e.g. ``icon="folder_build"``.
    """
    btn = QPushButton(parent)
    btn.setObjectName("browse")
    btn.setFixedWidth(36)
    btn.setToolTip(tooltip)
    btn.setIcon(load_folder_icon(icon))
    btn.setIconSize(QSize(20, 20))
    return btn
