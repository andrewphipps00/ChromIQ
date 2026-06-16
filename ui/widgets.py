"""Shared widget factory helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QSize, QSortFilterProxyModel, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap, QTextCursor

from core.i18n import tr
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
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


def _input_bg_qss() -> str:
    """Per-widget QSS rule forcing the body of QComboBox / QSpinBox /
    QDoubleSpinBox to the current theme's input background colour
    (white in light, BG_INPUT #1f1f1f in dark). App-wide QSS for these
    rules is silently ignored by Qt's QStyleSheetStyle for compound
    widgets, but per-widget setStyleSheet bypasses that quirk."""
    bg = QApplication.palette().base().color().name()
    return (
        "QComboBox:enabled, QSpinBox:enabled, QDoubleSpinBox:enabled {"
        f" background-color: {bg};"
        "}"
    )


def confirm(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton,
    default: "QMessageBox.StandardButton | None" = None,
) -> QMessageBox.StandardButton:
    """Yes/No-style confirmation prompt without the question-mark icon.

    A drop-in for ``QMessageBox.question`` (which bakes in the “?” icon the
    user dislikes): same signature shape, returns the StandardButton clicked.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    box.exec()
    return box.standardButton(box.clickedButton())


class NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ElidingLabel(QLabel):
    """Single-line label that middle-elides overflowing text with ``(...)``.

    A long file path used to expand the label to its full natural width and
    squeeze the adjacent "Load" button. This label reports a zero minimum
    width (size policy ``Ignored``) so it never pushes its neighbours, and
    middle-elides whatever no longer fits the available width — keeping the
    start of the path and the filename at the end both visible. The full,
    un-elided text is preserved and exposed as a hover tooltip and via
    ``text()``.
    """

    _SEP = "(...)"

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = text or ""
        self._apply_elision()

    def text(self) -> str:  # type: ignore[override]
        """Return the full, un-elided text (not what is currently painted)."""
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        fm = self.fontMetrics()
        avail = self.width()
        full = self._full_text
        if avail <= 0 or fm.horizontalAdvance(full) <= avail:
            super().setText(full)
            self.setToolTip("")
            return
        budget = avail - fm.horizontalAdvance(self._SEP)
        if budget <= 0:
            super().setText(self._SEP)
            self.setToolTip(full)
            return
        # Grow head and tail one character at a time, alternating, until the
        # next character would overflow the budget either side of the separator.
        head, tail = "", ""
        i, j = 0, len(full) - 1
        take_head = True
        while i <= j:
            ch = full[i] if take_head else full[j]
            if fm.horizontalAdvance(head + ch + tail) > budget:
                break
            if take_head:
                head += ch
                i += 1
            else:
                tail = ch + tail
                j -= 1
            take_head = not take_head
        super().setText(f"{head}{self._SEP}{tail}")
        self.setToolTip(full)


def reapply_input_stylesheet(root: QWidget) -> None:
    """Re-apply the per-widget input-bg QSS on every combo/spin descendant.
    Called from MainWindow.apply_theme on every theme switch so the
    hardcoded colour in the existing per-widget stylesheet is refreshed
    for the new theme."""
    qss = _input_bg_qss()
    for cls in (QComboBox, QSpinBox, QDoubleSpinBox):
        for w in root.findChildren(cls):
            w.setStyleSheet(qss)


def _apply_groupbox_surface(gb: QGroupBox) -> None:
    """Paint the GroupBox surface via QPalette + autoFillBackground instead
    of QSS. The QSS rule `QGroupBox { background: ... }` causes Qt's
    QStyleSheetStyle to propagate the colour into descendants' palette
    roles (including QPalette.Base), which makes QComboBox / QSpinBox
    bodies render the same surface colour as the section. Setting only
    palette.Window via setPalette() does not contaminate descendants'
    Base role, so inputs stay white per their own QSS rule."""
    app_pal = QApplication.palette()
    is_light = app_pal.window().color().lightness() > 150
    if is_light:
        from ui.light_styles import LM_BG_SURFACE
        gb.setAutoFillBackground(True)
        pal = gb.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(LM_BG_SURFACE))
        gb.setPalette(pal)
    else:
        gb.setAutoFillBackground(False)
        gb.setPalette(QPalette())  # revert to inherited


class GroupBoxSurfaceFilter(QObject):
    """Installs on QApplication. Whenever a QGroupBox is polished, applies
    the cream surface colour via setPalette + autoFillBackground so the
    QSS rule for QGroupBox can stay background-less and not contaminate
    descendant input widgets' palette.Base."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(obj, QGroupBox):
            _apply_groupbox_surface(obj)
        return False


def reapply_groupbox_surface(root: QWidget) -> None:
    """Walk every QGroupBox descendant of `root` and re-apply the surface
    colour. Called from MainWindow.apply_theme on every theme switch
    because Polish only fires once per widget."""
    for gb in root.findChildren(QGroupBox):
        _apply_groupbox_surface(gb)


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
    from core.settings import AppSettings
    from ui.theme import APPEARANCE_LIGHT, resolve_mode

    # Light mode's pale toolbar washes out the light arrows that read fine on
    # Dark mode's dark toolbar — use a near-black arrow there instead.
    mode = resolve_mode(AppSettings().get("appearance", "auto"))
    arrow_color = QColor("#1C1B18" if mode == APPEARANCE_LIGHT else "#e0e0e0")
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
    preview: bool = False,
) -> str:
    """Open a Qt file dialog with sidebar shortcuts and proper file-type filtering.

    Non-matching files are hidden when name_filter is set. When ``preview`` is
    True, an image thumbnail of the highlighted file is shown beside the list
    (for picking images).

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
    if preview:
        _attach_image_preview(dlg)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


def _attach_image_preview(dlg: "QFileDialog") -> None:
    """Add a live image-thumbnail pane to a non-native QFileDialog.

    QFileDialog's body is a QGridLayout; we drop a preview label into the column
    to the right of the file list and refresh it on ``currentChanged``. Loading
    is done lazily off the highlighted path (a QPixmap of the whole file) and
    scaled down — fine for the modest sizes a user browses one at a time.
    """
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QGridLayout, QLabel
    layout = dlg.layout()
    if not isinstance(layout, QGridLayout):
        return
    holder = QLabel(dlg)
    holder.setObjectName("imagePreview")
    holder.setMinimumSize(QSize(220, 220))
    holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    holder.setText(tr("No preview"))
    holder.setStyleSheet(
        "QLabel#imagePreview { border: 1px solid palette(mid); color: palette(mid);"
        " background: palette(base); }")
    # Span the file-list rows on the far right.
    layout.addWidget(holder, 1, layout.columnCount(), layout.rowCount() - 1, 1)
    # The preview column needs room — open the dialog much wider than the
    # default (the file list keeps its width, the preview takes the extra).
    dlg.setMinimumWidth(1000)
    dlg.resize(1200, max(720, dlg.height()))

    def _show(path: str) -> None:
        if path and Path(path).is_file():
            pm = QPixmap(path)
            if not pm.isNull():
                holder.setPixmap(pm.scaled(
                    holder.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                return
        holder.setPixmap(QPixmap())
        holder.setText(tr("No preview"))

    dlg.currentChanged.connect(_show)


def open_files_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_dir: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
) -> list[str]:
    """Multi-file variant of :func:`open_file_dialog`.

    Returns the list of selected paths, or an empty list if cancelled.
    """
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    dlg.setOptions(QFileDialog.Option.DontUseNativeDialog)
    _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
    if name_filter:
        dlg.setNameFilter(name_filter)
        exts = _parse_extensions(name_filter)
        if exts:
            dlg.setProxyModel(_ExtensionFilterProxy(exts, dlg))
    dlg.setSidebarUrls(_sidebar_urls(extra_path, extra_paths))
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        return list(dlg.selectedFiles())
    return []


def save_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_path: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
) -> str:
    """Open a Qt **save** file dialog with sidebar shortcuts.

    ``start_path`` may be a directory or a full path with a default
    filename — if it points at an existing directory the dialog opens
    there, otherwise it pre-selects the file inside its parent dir.
    Returns the chosen path, or an empty string if cancelled.
    """
    p = Path(start_path) if start_path else None
    if p is not None and p.is_dir():
        start_dir, default_name = str(p), ""
    elif p is not None:
        start_dir, default_name = str(p.parent), p.name
    else:
        start_dir, default_name = str(Path.home()), ""
    dlg = QFileDialog(parent, title, start_dir)
    dlg.setOptions(QFileDialog.Option.DontUseNativeDialog)
    _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    if name_filter:
        dlg.setNameFilter(name_filter)
    if default_name:
        dlg.selectFile(default_name)
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

    For the plain "folder" icon (used in the Preferences dialog), if the
    active palette is light, take the same PNG and re-tint every
    non-transparent pixel to #22211f so the shape stays identical to the
    coloured variants — just in a dark hue that reads on the warm-white
    Preferences background. The tab-specific coloured variants
    (folder_build, folder_print, …) are kept as-is since their hues
    already read on either background.

    Falls back to the OS system folder icon if no asset is found.
    """
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication

    dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
    phys = round(20 * dpr)

    src = resource_path(f"assets/folder/{name}.png")
    src_px = QPixmap(str(src))
    if not src_px.isNull():
        scaled = src_px.scaled(phys, phys,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        # Light-theme: recolour the bare "folder" icon to #22211f. Compose
        # the new colour using SourceIn so the icon's existing alpha mask
        # (the line work) is preserved exactly — every line that was
        # rendered in the dark PNG is repainted in the new colour.
        if name == "folder" and _is_light_palette():
            from PyQt6.QtGui import QImage, QPainter
            img = scaled.toImage().convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied
            )
            painter = QPainter(img)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceIn
            )
            painter.fillRect(img.rect(), QColor("#22211f"))
            painter.end()
            recoloured = QPixmap.fromImage(img)
            recoloured.setDevicePixelRatio(dpr)
            return QIcon(recoloured)
        scaled.setDevicePixelRatio(dpr)
        return QIcon(scaled)
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)


def _is_light_palette() -> bool:
    """True when the active app palette is a light theme."""
    from PyQt6.QtGui import QGuiApplication
    pal = QGuiApplication.palette()
    return pal.window().color().lightness() > 150


def load_preset_icon(name: str) -> QIcon:
    """Load a preset +/- icon, switching to the *_dark variant in light mode.

    `name` is the bare asset stem ("plus" or "minus"). On a light palette,
    we load the *_dark.svg sibling so the glyph reads on the warm-white
    Presets row.
    """
    from core.resource_path import resource_path
    stem = f"{name}_dark" if _is_light_palette() else name
    return QIcon(str(resource_path(f"assets/{stem}.svg")))


def set_folder_icon(btn: QPushButton, name: str) -> None:
    """Set a folder-glyph icon on `btn` and tag it for live theme refresh."""
    btn.setIcon(load_folder_icon(name))
    btn.setProperty("themed_folder_icon", name)


def set_preset_icon(btn: QPushButton, name: str) -> None:
    """Set a preset +/- icon on `btn` and tag it for live theme refresh."""
    btn.setIcon(load_preset_icon(name))
    btn.setProperty("themed_preset_icon", name)


def apply_themed_icons(root: QWidget) -> None:
    """Reload every theme-aware icon under `root`.

    Walks all QPushButtons and re-runs the appropriate loader for buttons
    tagged by set_folder_icon / set_preset_icon / make_browse_button. Call
    from MainWindow.apply_theme so palette-dependent icons repaint without
    requiring an app restart.
    """
    for btn in root.findChildren(QPushButton):
        folder_name = btn.property("themed_folder_icon")
        if folder_name:
            btn.setIcon(load_folder_icon(str(folder_name)))
            continue
        preset_name = btn.property("themed_preset_icon")
        if preset_name:
            btn.setIcon(load_preset_icon(str(preset_name)))


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
    btn.setProperty("themed_folder_icon", icon)
    btn.setIconSize(QSize(20, 20))
    return btn


def replace_log_line(
    log: QPlainTextEdit,
    prev_text: str | None,
    new_text: str | None,
) -> str | None:
    """Replace a single tracked status line in a QPlainTextEdit log, in place.

    Removes ``prev_text``'s block (if still present) along with exactly one
    adjacent block separator — the trailing one when anything follows, otherwise
    the leading one — so no blank line is left wherever the line sits. Then
    appends ``new_text`` when it is non-empty. Returns the text now being tracked
    (``new_text`` or ``None``), to store for the next call.

    Lets a tab show only the most recent of a recurring notice (e.g. the detected
    instrument) instead of stacking identical lines as files are reloaded.
    """
    if prev_text:
        found = log.document().find(prev_text)
        if not found.isNull():
            block = found.block()
            keep = QTextCursor.MoveMode.KeepAnchor
            cursor = QTextCursor(log.document())
            if block.next().isValid():
                cursor.setPosition(block.position())
                cursor.setPosition(block.next().position(), keep)
            elif block.previous().isValid():
                cursor.setPosition(block.position() - 1)
                cursor.setPosition(block.position() + len(block.text()), keep)
            else:
                cursor.setPosition(0)
                cursor.setPosition(len(block.text()), keep)
            cursor.removeSelectedText()
    if new_text:
        log.appendPlainText(new_text)
        log.ensureCursorVisible()
        return new_text
    return None


@dataclass
class GatedOption:
    """An option disabled when a tab's instrument/data gate is active.

    ``widgets`` are greyed out while the gate is active; ``neutralise`` clears the
    option in the collected params object right before the tool runs, so a flag
    enabled before the gate became active is never passed to colprof/profcheck.
    """
    widgets: list[QWidget] = field(default_factory=list)
    neutralise: Callable[[Any], None] = lambda params: None
