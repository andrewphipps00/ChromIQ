"""Shared ti2 file loading workflow: working-folder detection, copy/rename dialogs."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget
    from core.settings import AppSettings


# Matches:  TARGET_INSTRUMENT "GretagMacbeth i1 Pro"
_TARGET_INSTRUMENT_RE = re.compile(r'TARGET_INSTRUMENT\s+"([^"]*)"')
# Matches:  SPECTRAL_BANDS "36"   (number of spectral bands recorded per patch)
_SPECTRAL_BANDS_RE = re.compile(r'SPECTRAL_BANDS\s+"?(\d+)"?')

# The exact TARGET_INSTRUMENT strings ChromIQ lays out charts for. ArgyllCMS
# writes the same value into the resulting .ti3, so detection works on either.
KNOWN_INSTRUMENTS: tuple[str, ...] = (
    "X-Rite ColorMunki",          # ColorMunki / i1Studio / ColorChecker Studio
    "GretagMacbeth i1 Pro",       # i1 Pro family (i1 Pro / Pro 2 / Pro 3 / Pro 3+)
    "GretagMacbeth SpectroScan",  # motorized XY table (patch-by-patch, not strips)
)


def read_target_instrument(cgats_path: Path) -> str | None:
    """Return the TARGET_INSTRUMENT value from a CGATS file (.ti1/.ti2/.ti3), or None.

    ArgyllCMS records the instrument a chart was laid out for in this keyword and
    carries it through into the measured .ti3, e.g.
    ``TARGET_INSTRUMENT "GretagMacbeth i1 Pro"`` (i1 Pro family, incl. i1Pro3+),
    ``TARGET_INSTRUMENT "X-Rite ColorMunki"`` (ColorMunki/i1Studio) or
    ``TARGET_INSTRUMENT "GretagMacbeth SpectroScan"`` (XY table). See
    ``KNOWN_INSTRUMENTS`` for the values ChromIQ produces.
    """
    try:
        text = cgats_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = _TARGET_INSTRUMENT_RE.search(text)
    return m.group(1).strip() if m else None


def is_colormunki(name: str | None) -> bool:
    """Whether the instrument is a ColorMunki (incl. its i1Studio rebrand).

    Single source of truth for the ColorMunki check used both by the chartread
    -B decision and by the option-gating in the Build-Profile / Check-Refine tabs.
    """
    return bool(name) and "colormunki" in name.lower()


def is_spectroscan(name: str | None) -> bool:
    """Whether the instrument is a GretagMacbeth SpectroScan.

    The SpectroScan is a motorized XY table that reads each patch individually
    rather than scanning strips, so the bidirectional (-B) concept does not apply.
    """
    return bool(name) and "spectroscan" in name.lower()


def instrument_label(name: str | None) -> str | None:
    """Friendly display name for a TARGET_INSTRUMENT value (UI output only).

    ArgyllCMS tags whole instrument families under one string; this expands them
    to the model names users recognise. Detection/gating still use the raw value
    (see ``is_colormunki`` / ``is_spectroscan``) — this is purely for display.
    Unrecognised instruments (incl. the SpectroScan) are shown unchanged.
    """
    if not name:
        return None
    if is_colormunki(name):
        return "ColorMunki / i1Studio / CCStudio"
    low = name.lower()
    if "i1 pro" in low or "i1pro" in low:
        return "i1Pro / i1Pro2 / i1Pro3(+)"
    return name


def disable_bidir_for_instrument(name: str | None) -> bool:
    """Whether bidirectional strip recognition should be disabled (chartread -B).

    The ColorMunki (and its i1Studio rebrand) can only read strips reliably in
    one direction, so it needs ``-B``. The i1 Pro family — i1 Pro / Pro 2 /
    Pro 3 / Pro 3+, all tagged ``"GretagMacbeth i1 Pro"`` — reads in either
    direction, and the SpectroScan is an XY table that reads patches individually,
    so neither needs ``-B``. Unknown / missing instruments fall back to
    bidirectional allowed (no ``-B``).
    """
    return is_colormunki(name)


def has_spectral_data(cgats_path: Path) -> bool:
    """Whether a CGATS file (.ti3) contains spectral measurements.

    Spectral-dependent options (FWA compensation, illuminant, observer) only work
    when the .ti3 carries per-patch spectral readings, flagged by a positive
    ``SPECTRAL_BANDS`` keyword. Returns False on a missing/unreadable file.
    """
    try:
        text = cgats_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    m = _SPECTRAL_BANDS_RE.search(text)
    return bool(m) and int(m.group(1)) > 0


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
    # Set-comprehension dedupes Windows' case-insensitive glob matches
    # (chart.tif matches both *.tif and *.TIF), which otherwise made the
    # preview show "Page 1/2 and 2/2" for a single-file chart when *loading an
    # existing target* (forum #148275 — same root cause as the generation-path
    # fix in chart_creator._printtarg_done for #148124).
    tiffs  = sorted({
        *folder.glob(f"{stem}*.tif"),
        *folder.glob(f"{stem}*.TIF"),
        *folder.glob(f"{stem}*.tiff"),
    })
    return (ti1 if ti1.exists() else None), tiffs


def _handle_outside(
    parent: "QWidget",
    ti2_path: Path,
    working_dir: Path,
) -> tuple[Path, list[Path]] | None:
    ti1, tiffs = _related_files(ti2_path)
    result = _ask_profile_name(parent, ti2_path, ti1, tiffs, working_dir)
    if result is None:
        return None
    new_name, overwrite = result
    return _copy_files(ti2_path, ti1, tiffs, working_dir, new_name, overwrite=overwrite)


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
        result = _ask_profile_name(parent, ti2_path, ti1, tiffs, working_dir)
        if result is None:
            return None
        new_name, overwrite = result
        return _copy_files(ti2_path, ti1, tiffs, working_dir, new_name, overwrite=overwrite)
    return None


def _ask_profile_name(
    parent: "QWidget",
    ti2_path: Path,
    ti1: Path | None,
    tiffs: list[Path],
    working_dir: Path,
) -> tuple[str, bool] | None:
    """Ask the user for a profile name.

    Returns (name, overwrite) — `overwrite=True` means the user explicitly
    confirmed wiping an existing folder of the same name. Returns None if
    the user cancelled.
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
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
    dlg.setMinimumWidth(580)
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
    error_lbl.setWordWrap(True)
    layout.addWidget(error_lbl)

    btn_row = QHBoxLayout()

    ok_btn = QPushButton("OK", dlg)
    ok_btn.setDefault(True)
    btn_row.addWidget(ok_btn)

    overwrite_btn = QPushButton("Overwrite existing folder", dlg)
    overwrite_btn.setAutoDefault(False)
    overwrite_btn.setVisible(False)
    btn_row.addWidget(overwrite_btn)

    btn_row.addStretch(1)

    cancel_btn = QPushButton("Cancel", dlg)
    cancel_btn.setAutoDefault(False)
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel_btn)

    layout.addLayout(btn_row)

    result: dict = {"name": None, "overwrite": False}

    def _is_self_collision(name: str) -> bool:
        # Guard against rmtree'ing the .ti2's own parent folder
        # (only possible when loading a chart that already lives inside
        # the working folder).
        try:
            return (working_dir / name).resolve() == ti2_path.parent.resolve()
        except OSError:
            return False

    def _validate(name: str) -> str | None:
        if not name:
            return "Please enter a name."
        if any(c in name for c in r'/\:*?"<>|'):
            return "Name contains invalid characters."
        return None

    def _on_name_changed(_text: str = "") -> None:
        name = name_edit.text().strip()
        collision = bool(name) and (working_dir / name).exists() and not _is_self_collision(name)
        if collision:
            error_lbl.setText(
                f"“{name}” already exists. Click “Overwrite existing folder” to replace it."
            )
            ok_btn.setVisible(False)
            overwrite_btn.setVisible(True)
        else:
            error_lbl.setText("")
            ok_btn.setVisible(True)
            overwrite_btn.setVisible(False)

    name_edit.textChanged.connect(_on_name_changed)

    def _on_accept() -> None:
        name = name_edit.text().strip()
        err = _validate(name)
        if err:
            error_lbl.setText(err)
            return
        if (working_dir / name).exists() and not _is_self_collision(name):
            _on_name_changed()
            return
        if _is_self_collision(name):
            error_lbl.setText(
                "That name points to the chart's own folder. Pick a different name."
            )
            return
        result["name"] = name
        result["overwrite"] = False
        dlg.accept()

    def _on_overwrite() -> None:
        name = name_edit.text().strip()
        err = _validate(name)
        if err:
            error_lbl.setText(err)
            return
        if _is_self_collision(name):
            error_lbl.setText(
                "You're trying to overwrite the chart's own folder. "
                "Pick a different name."
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
            "Overwrite existing folder?",
            f"This will permanently delete:\n\n    {dest}\n\n"
            "and replace it with the imported chart files. Continue?",
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


def _copy_files(
    ti2_path: Path,
    ti1: Path | None,
    tiffs: list[Path],
    working_dir: Path,
    new_name: str,
    overwrite: bool = False,
) -> tuple[Path, list[Path]]:
    old_stem = ti2_path.stem
    dest     = working_dir / new_name
    if overwrite and dest.exists():
        shutil.rmtree(dest)
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
