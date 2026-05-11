"""Shared ti2 file loading workflow: working-folder detection, copy/rename dialogs."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget
    from core.settings import AppSettings


def resolve_ti2(
    parent: "QWidget",
    ti2_path: Path,
    settings: "AppSettings",
) -> tuple[Path, list[Path]] | None:
    """Determine how to load a .ti2 file relative to the working folder.

    Returns (ti2_path_to_use, tiff_list) — either the original paths or newly
    copied/renamed ones — or None if the user cancelled.
    """
    working_dir = _resolve_working_dir(settings)
    if _is_under(ti2_path, working_dir):
        return _handle_inside(parent, ti2_path, working_dir)
    return _handle_outside(parent, ti2_path, working_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_working_dir(settings: "AppSettings") -> Path:
    custom = settings.get("custom_output_path", "")
    return Path(custom) if custom else Path.home() / "ChromIQ"


def _is_under(path: Path, root: Path) -> bool:
    # Only consider the file "inside" the working folder if it sits directly
    # in a first-level project subfolder: <working_dir>/<project>/<file>.
    # This avoids false positives when working_dir is a broad path (e.g. ~/).
    try:
        rel = path.resolve().relative_to(root.resolve())
        return len(rel.parts) == 2
    except ValueError:
        return False


def _related_files(ti2_path: Path) -> tuple[Path | None, list[Path]]:
    """Return (ti1_or_None, sorted_tiff_list) for a given .ti2."""
    folder = ti2_path.parent
    stem   = ti2_path.stem
    ti1    = folder / f"{stem}.ti1"
    tiffs  = sorted([
        *folder.glob(f"{stem}*.tif"),
        *folder.glob(f"{stem}*.TIF"),
        *folder.glob(f"{stem}*.tiff"),
    ])
    return (ti1 if ti1.exists() else None), tiffs


def _handle_outside(
    parent: "QWidget",
    ti2_path: Path,
    working_dir: Path,
) -> tuple[Path, list[Path]] | None:
    ti1, tiffs = _related_files(ti2_path)
    new_name = _ask_profile_name(parent, ti2_path, ti1, tiffs, working_dir)
    if not new_name:
        return None
    return _copy_files(ti2_path, ti1, tiffs, working_dir, new_name)


def _handle_inside(
    parent: "QWidget",
    ti2_path: Path,
    working_dir: Path,
) -> tuple[Path, list[Path]] | None:
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle("Load Chart")
    dlg.setMinimumWidth(460)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(12)

    lbl = QLabel(
        f"<b>{ti2_path.name}</b> is already in your working folder.<br><br>"
        "What would you like to do?",
        dlg,
    )
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    cont_desc = QLabel(
        "<i>Continue</i> — use the chart files in this folder as-is — "
        "nothing will be copied or moved.",
        dlg,
    )
    cont_desc.setWordWrap(True)
    layout.addWidget(cont_desc)

    new_desc = QLabel(
        "<i>Use as base for a new profile</i> — copy the chart files to a new "
        "subfolder so you can build a separate ICC profile without overwriting "
        "the original.",
        dlg,
    )
    new_desc.setWordWrap(True)
    layout.addWidget(new_desc)

    btn_box    = QDialogButtonBox(dlg)
    cont_btn   = btn_box.addButton("Continue",                     QDialogButtonBox.ButtonRole.AcceptRole)
    new_btn    = btn_box.addButton("Use as base for a new profile", QDialogButtonBox.ButtonRole.ActionRole)
    cancel_btn = btn_box.addButton("Cancel",                        QDialogButtonBox.ButtonRole.RejectRole)
    layout.addWidget(btn_box)

    choice: list[str | None] = [None]

    def _on_continue() -> None:
        choice[0] = "continue"
        dlg.accept()

    def _on_new() -> None:
        choice[0] = "new"
        dlg.accept()

    cont_btn.clicked.connect(_on_continue)
    new_btn.clicked.connect(_on_new)
    cancel_btn.clicked.connect(dlg.reject)
    dlg.exec()

    if choice[0] == "continue":
        _, tiffs = _related_files(ti2_path)
        return ti2_path, tiffs
    if choice[0] == "new":
        ti1, tiffs = _related_files(ti2_path)
        new_name = _ask_profile_name(parent, ti2_path, ti1, tiffs, working_dir)
        if not new_name:
            return None
        return _copy_files(ti2_path, ti1, tiffs, working_dir, new_name)
    return None


def _ask_profile_name(
    parent: "QWidget",
    ti2_path: Path,
    ti1: Path | None,
    tiffs: list[Path],
    working_dir: Path,
) -> str | None:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (
        QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout,
    )

    file_lines = [f"  • {ti2_path.name}"]
    if ti1:
        file_lines.append(f"  • {ti1.name}")
    for t in tiffs:
        file_lines.append(f"  • {t.name}")
    ti3 = ti2_path.with_suffix(".ti3")
    if ti3.exists():
        file_lines.append(f"  • {ti3.name}")
    for ext in (".icc", ".icm"):
        icc = ti2_path.with_suffix(ext)
        if icc.exists():
            file_lines.append(f"  • {icc.name}")
            break

    dlg = QDialog(parent)
    dlg.setWindowTitle("Copy Chart Files")
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(10)

    info = QLabel(
        f"The following files from <b>{ti2_path.parent.name}/</b> will be "
        f"copied into your working folder as a new profile set:<br><br>"
        f"<pre>{'<br>'.join(file_lines)}</pre>"
        f"They will be placed in:<br>"
        f"<code>{working_dir}/&lt;name&gt;/</code><br><br>"
        "Enter a name for the new profile:",
        dlg,
    )
    info.setWordWrap(True)
    layout.addWidget(info)

    name_edit = QLineEdit(dlg)
    name_edit.setPlaceholderText("e.g. Canon_ProGraf_Glossy_240g")
    layout.addWidget(name_edit)

    error_lbl = QLabel("", dlg)
    error_lbl.setStyleSheet("color: #e05555;")
    layout.addWidget(error_lbl)

    btn_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        dlg,
    )
    layout.addWidget(btn_box)

    def _on_accept() -> None:
        name = name_edit.text().strip()
        if not name:
            error_lbl.setText("Please enter a name.")
            return
        if any(c in name for c in r'/\:*?"<>|'):
            error_lbl.setText("Name contains invalid characters.")
            return
        if (working_dir / name).exists():
            error_lbl.setText(f"“{name}” already exists in the working folder.")
            return
        dlg.accept()

    btn_box.accepted.connect(_on_accept)
    btn_box.rejected.connect(dlg.reject)

    QTimer.singleShot(0, name_edit.setFocus)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return name_edit.text().strip()
    return None


def _copy_files(
    ti2_path: Path,
    ti1: Path | None,
    tiffs: list[Path],
    working_dir: Path,
    new_name: str,
) -> tuple[Path, list[Path]]:
    old_stem = ti2_path.stem
    dest     = working_dir / new_name
    dest.mkdir(parents=True, exist_ok=True)

    new_ti2 = dest / f"{new_name}.ti2"
    shutil.copy2(ti2_path, new_ti2)

    if ti1:
        shutil.copy2(ti1, dest / f"{new_name}.ti1")

    new_tiffs: list[Path] = []
    for tiff in tiffs:
        suffix   = tiff.name[len(old_stem):]    # e.g. ".tif" or "_2.tif"
        new_tiff = dest / f"{new_name}{suffix}"
        shutil.copy2(tiff, new_tiff)
        new_tiffs.append(new_tiff)

    ti3 = ti2_path.with_suffix(".ti3")
    if ti3.exists():
        shutil.copy2(ti3, dest / f"{new_name}.ti3")

    for ext in (".icc", ".icm"):
        icc = ti2_path.with_suffix(ext)
        if icc.exists():
            shutil.copy2(icc, dest / f"{new_name}{ext}")
            break

    return new_ti2, new_tiffs
