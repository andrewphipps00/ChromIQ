"""Import an i1Profiler measurement .txt into the working folder.

i1Profiler can read charts ChromIQ exports (see ``workflow/i1profiler_export``)
and write back a measurement .txt. That file isn't an Argyll .ti3, so before the
Build-Profile tab can use it we place it in a per-profile subfolder and let the
caller run Argyll's ``txt2ti3`` to produce the .ti3.

This mirrors ``ui/ti2_loader`` — same working-folder detection and copy/rename
dialogs — but for the single-file case (just the .txt), so the ti2 chart-loading
logic stays untouched. The generic working-folder helpers are reused from there.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ui.ti2_loader import _project_root_for, _resolve_working_dir
from core.i18n import tr

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget
    from core.settings import AppSettings


def resolve_txt(
    parent: "QWidget",
    txt_path: Path,
    settings: "AppSettings",
) -> tuple[Path, str] | None:
    """Decide where an i1Profiler measurement .txt should live before conversion.

    Returns ``(txt_to_convert, base_name)`` — the .txt path to feed ``txt2ti3``
    and the base name to use for the resulting .ti3 — or ``None`` if the user
    cancelled. The .txt is either used in place (when already inside a working
    sub-folder and the user chose "Continue") or copied, renamed, into a fresh
    ``<working_dir>/<name>/`` sub-folder.
    """
    working_dir = _resolve_working_dir(settings)
    if _project_root_for(txt_path, working_dir) is not None:
        return _handle_inside(parent, txt_path, working_dir)
    return _handle_outside(parent, txt_path, working_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _handle_outside(
    parent: "QWidget",
    txt_path: Path,
    working_dir: Path,
) -> tuple[Path, str] | None:
    result = _ask_profile_name(parent, txt_path, working_dir)
    if result is None:
        return None
    name, overwrite = result
    return _copy_txt(txt_path, working_dir, name, overwrite=overwrite)


def _handle_inside(
    parent: "QWidget",
    txt_path: Path,
    working_dir: Path,
) -> tuple[Path, str] | None:
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("Load Measurement"))
    dlg.setMinimumWidth(460)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(12)

    lbl = QLabel(
        tr("<b>{name}</b> is already in your working folder.<br><br>"
           "What would you like to do?").format(name=txt_path.name),
        dlg,
    )
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    cont_desc = QLabel(
        tr("<i>Continue</i> — convert and use the measurement in this folder as-is — "
        "nothing will be copied or moved."),
        dlg,
    )
    cont_desc.setWordWrap(True)
    layout.addWidget(cont_desc)

    new_desc = QLabel(
        tr("<i>Use as base for a new profile</i> — copy the measurement to a new "
        "subfolder so you can build a separate ICC profile without overwriting "
        "the original."),
        dlg,
    )
    new_desc.setWordWrap(True)
    layout.addWidget(new_desc)

    btn_box    = QDialogButtonBox(dlg)
    cont_btn   = btn_box.addButton(tr("Continue"),                      QDialogButtonBox.ButtonRole.AcceptRole)
    new_btn    = btn_box.addButton(tr("Use as base for a new profile"), QDialogButtonBox.ButtonRole.ActionRole)
    cancel_btn = btn_box.addButton(tr("Cancel"),                        QDialogButtonBox.ButtonRole.RejectRole)
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
        return txt_path, txt_path.stem
    if choice[0] == "new":
        result = _ask_profile_name(parent, txt_path, working_dir)
        if result is None:
            return None
        name, overwrite = result
        return _copy_txt(txt_path, working_dir, name, overwrite=overwrite)
    return None


def _ask_profile_name(
    parent: "QWidget",
    txt_path: Path,
    working_dir: Path,
) -> tuple[str, bool] | None:
    """Ask the user for a profile name for an imported measurement .txt.

    Returns ``(name, overwrite)`` — ``overwrite=True`` means the user explicitly
    confirmed wiping an existing folder of the same name. Returns ``None`` if the
    user cancelled.
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
    )

    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("Choose a name for the profile"))
    dlg.setMinimumWidth(580)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(10)

    info = QLabel(
        tr("The i1Profiler measurement <b>{name}</b> will be copied into "
           "your working folder as a new profile set:<br><br>"
           "<pre>  • {name}</pre>"
           "It will be placed in:<br>"
           "<code>{target}/&lt;name&gt;/</code><br><br>"
           "Enter a name for the profile you want to create:").format(
            name=txt_path.name, target=working_dir),
        dlg,
    )
    info.setWordWrap(True)
    layout.addWidget(info)

    name_edit = QLineEdit(dlg)
    name_edit.setPlaceholderText(tr("e.g. Canon_ProGraf_Glossy_240g"))
    layout.addWidget(name_edit)

    error_lbl = QLabel("", dlg)
    error_lbl.setStyleSheet("color: #e05555;")
    error_lbl.setWordWrap(True)
    layout.addWidget(error_lbl)

    btn_row = QHBoxLayout()

    ok_btn = QPushButton(tr("OK"), dlg)
    ok_btn.setDefault(True)
    btn_row.addWidget(ok_btn)

    overwrite_btn = QPushButton(tr("Overwrite existing folder"), dlg)
    overwrite_btn.setAutoDefault(False)
    overwrite_btn.setVisible(False)
    btn_row.addWidget(overwrite_btn)

    btn_row.addStretch(1)

    cancel_btn = QPushButton(tr("Cancel"), dlg)
    cancel_btn.setAutoDefault(False)
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel_btn)

    layout.addLayout(btn_row)

    result: dict = {"name": None, "overwrite": False}

    def _is_self_collision(name: str) -> bool:
        # Guard against rmtree'ing the .txt's own parent folder (only possible
        # when the loaded .txt already lives inside the working folder).
        try:
            return (working_dir / name).resolve() == txt_path.parent.resolve()
        except OSError:
            return False

    def _normalise(text: str) -> str:
        """Sanitise the typed name the same way set_target_name does (spaces→-,
        illegal chars→_), so the on-disk folder = the user-facing name."""
        from core.file_manager import FileManager
        cleaned = FileManager.strip_workfile_ext(text)
        return FileManager._sanitise(cleaned) if cleaned.strip() else ""

    def _validate(name: str) -> str | None:
        if not name:
            return "Please enter a name."
        if any(c in name for c in r'/\:*?"<>|'):
            return "Name contains invalid characters."
        return None

    def _on_name_changed(_text: str = "") -> None:
        name = _normalise(name_edit.text())
        collision = bool(name) and (working_dir / name).exists() and not _is_self_collision(name)
        if collision:
            error_lbl.setText(
                tr("“{name}” already exists. Click “Overwrite existing folder” to replace it.").format(name=name)
            )
            ok_btn.setVisible(False)
            overwrite_btn.setVisible(True)
        else:
            error_lbl.setText("")
            ok_btn.setVisible(True)
            overwrite_btn.setVisible(False)

    name_edit.textChanged.connect(_on_name_changed)

    def _on_accept() -> None:
        name = _normalise(name_edit.text())
        err = _validate(name)
        if err:
            error_lbl.setText(err)
            return
        if (working_dir / name).exists() and not _is_self_collision(name):
            _on_name_changed()
            return
        if _is_self_collision(name):
            error_lbl.setText(
                tr("That name points to the measurement's own folder. Pick a different name.")
            )
            return
        result["name"] = name
        result["overwrite"] = False
        dlg.accept()

    def _on_overwrite() -> None:
        name = _normalise(name_edit.text())
        err = _validate(name)
        if err:
            error_lbl.setText(err)
            return
        if _is_self_collision(name):
            error_lbl.setText(
                tr("You're trying to overwrite the measurement's own folder. "
                "Pick a different name.")
            )
            return
        dest = working_dir / name
        if not dest.exists():
            result["name"] = name
            result["overwrite"] = False
            dlg.accept()
            return
        confirm = QMessageBox.warning(
            dlg,
            tr("Overwrite existing folder?"),
            tr("This will permanently delete:\n\n    {dest}\n\n"
               "and replace it with the imported measurement. Continue?"
               ).format(dest=dest),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            result["name"] = name
            result["overwrite"] = True
            dlg.accept()

    ok_btn.clicked.connect(_on_accept)
    overwrite_btn.clicked.connect(_on_overwrite)

    QTimer.singleShot(0, name_edit.setFocus)
    if dlg.exec() == QDialog.DialogCode.Accepted and result["name"]:
        return result["name"], result["overwrite"]
    return None


def _copy_txt(
    txt_path: Path,
    working_dir: Path,
    new_name: str,
    overwrite: bool = False,
) -> tuple[Path, str]:
    """Import an i1Profiler .txt into a fresh project as run1's chart.

    Builds the per-run layout (see docs/dev_folder_layout.md): a project at
    ``working_dir/<new_name>/`` with the .txt placed at
    ``runs/run1/<new_name>.txt``. txt2ti3 then writes
    ``runs/run1/<new_name>.ti3`` — the canonical measurement under the
    project-name chart stem.

    Returns (txt path, base name to hand txt2ti3).
    """
    from core.file_manager import FileManager, Project

    # Defensive: dialog already sanitises, but enforce here for any caller.
    new_name = FileManager._sanitise(FileManager.strip_workfile_ext(new_name))

    dest = working_dir / new_name
    if overwrite and dest.exists():
        shutil.rmtree(dest)

    proj = Project.create(dest, new_name)
    run  = proj.current_run()
    run.ensure_dir()

    # Place the .txt under the chart stem so txt2ti3's output stays paired
    # (Run.measurement_ti3 = <stem>.ti3 = <new_name>.ti3).
    new_txt = run.dir / f"{run.stem}.txt"
    shutil.copy2(txt_path, new_txt)
    return new_txt, run.stem
