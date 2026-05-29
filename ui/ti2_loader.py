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
# printtarg writes RANDOM_START on a randomised chart and CHART_ID on a
# fixed-order one (printtarg.c:3718). chartread keys its auto strip-ID and
# bidirectional recognition off the same keyword (chartread.c:2980).
_RANDOM_START_RE = re.compile(r'\bRANDOM_START\b')

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


def is_i1pro(name: str | None) -> bool:
    """Whether the instrument is an i1 Pro family device.

    The i1 Pro / Pro 2 / Pro 3 / Pro 3+ are all tagged ``"GretagMacbeth i1 Pro"``
    by ArgyllCMS (some builds report ``"X-Rite i1 Pro …"``). They can read a
    strip in either direction (``inst2_bidi_scan``), so they support chartread's
    force-bidirectional mode (``-b``).
    """
    if not name:
        return False
    low = name.lower()
    return "i1 pro" in low or "i1pro" in low


def disable_bidir_for_instrument(name: str | None) -> bool:
    """Whether the Auto toggle should disable bidirectional strip recognition
    (chartread ``-B``).

    No instrument auto-selects ``-B`` any more. The ColorMunki (and its
    i1Studio rebrand) reads strips in one direction, but ArgyllCMS's default
    behaviour already handles that correctly, so Auto leaves the ColorMunki on
    the Argyll default (no ``-B``) rather than forcing ``-B``. The i1 Pro family
    auto-forces ``-b`` instead (see ``force_bidir_for_instrument``); the
    SpectroScan reads patches individually. Users can still pick "Bidirectional
    disabled" by hand.

    Always returns ``False`` — kept as the single Auto ``-B`` decision point so
    the behaviour stays documented in one place.
    """
    return False


def force_bidir_for_instrument(name: str | None) -> bool:
    """Whether to force-enable bidirectional strip recognition (chartread -b).

    chartread only auto-enables bidirectional reading when the chart is
    randomised; on a fixed-order chart (e.g. printtarg ``-r``) it otherwise
    reads one direction only and rejects strips scanned backwards. The i1 Pro
    family reads either direction, so ``-b`` forces the auto-detection on for
    those charts. The ColorMunki reads one direction only and the SpectroScan
    reads patches individually, so neither should force it. ``-b`` and ``-B``
    are mutually exclusive; this only returns True for the i1 Pro family.
    """
    return is_i1pro(name)


def is_randomized(cgats_path: Path) -> bool:
    """Whether a chart was laid out in randomised patch order.

    printtarg randomises by default and writes ``RANDOM_START``; its ``-r`` flag
    keeps the source order and writes ``CHART_ID`` instead. chartread reads the
    same keyword to decide whether it may auto-recognise strips and read them in
    either direction. Randomisation gives every strip a unique colour signature,
    which is what makes that recognition reliable — a fixed-order chart (no
    ``RANDOM_START``) can confuse it, especially with forced bidirectional
    reading (``-b``).

    Returns ``True`` only when ``RANDOM_START`` is present; a missing/unreadable
    file is treated as randomised (``True``) so callers don't warn spuriously.
    """
    try:
        text = cgats_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True
    return bool(_RANDOM_START_RE.search(text))


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
    if _project_root_for(ti2_path, working_dir) is not None:
        return _handle_inside(parent, ti2_path, working_dir)
    return _handle_outside(parent, ti2_path, working_dir)


def resolve_ti3(
    parent: "QWidget",
    ti3_path: Path,
    settings: "AppSettings",
) -> Path | None:
    """Determine how to load a .ti3 relative to the working folder.

    Mirrors ``resolve_ti2``: returns the path to use — either the original
    (when the file is already inside a structured project) or a newly copied
    path inside a freshly-created project. Returns ``None`` if the user
    cancelled.

    For an external .ti3 with a sibling .ti2, the full ti2 import flow is
    reused (the .ti2 is the chart; the .ti3 is its measurement). For a bare
    .ti3 (no chart files beside it), a measurement-only project is created.
    Either way, ``Project.create`` writes the "Where are my files.txt" README
    at the new project root so the user gets the new layout on the spot.
    """
    working_dir = _resolve_working_dir(settings)
    if _project_root_for(ti3_path, working_dir) is not None:
        return ti3_path                              # already in a project
    sibling_ti2 = ti3_path.with_suffix(".ti2")
    if sibling_ti2.is_file():
        result = _handle_outside(parent, sibling_ti2, working_dir)
        if result is None:
            return None
        new_ti2, _tiffs = result
        new_ti3 = new_ti2.with_suffix(".ti3")
        return new_ti3 if new_ti3.exists() else None
    # Bare .ti3 — import the measurement (and sibling .icc, if any) alone.
    return _handle_outside_ti3_only(parent, ti3_path, working_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_working_dir(settings: "AppSettings") -> Path:
    custom = settings.get("custom_output_path", "")
    return Path(custom) if custom else Path.home() / "ChromIQ"


def _project_root_for(path: Path, working_dir: Path) -> Path | None:
    """Return the ChromIQ project root that contains ``path``, or None.

    A project root is a first-level subfolder of ``working_dir`` that holds a
    ``project.json``. ``path`` counts as "inside" when that manifest exists —
    so a chart already structured as ``<project>/runs/<id>/chart.ti2`` (or a
    calibration in ``<project>/cal/``) is recognised and not re-imported.
    """
    try:
        rel = path.resolve().relative_to(working_dir.resolve())
    except ValueError:
        return None
    if not rel.parts:
        return None
    candidate = working_dir / rel.parts[0]
    return candidate if (candidate / "project.json").exists() else None


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
    dlg.setWindowTitle("Load Test Session")
    dlg.setMinimumWidth(460)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(12)

    lbl = QLabel(
        f"The session <b>{ti2_path.stem}</b> is already set up in your working "
        "folder.<br><br>"
        "What would you like to do?",
        dlg,
    )
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    cont_desc = QLabel(
        "<i>Continue</i> — use the files in this folder as-is — "
        "nothing will be copied or moved.",
        dlg,
    )
    cont_desc.setWordWrap(True)
    layout.addWidget(cont_desc)

    new_desc = QLabel(
        "<i>Use as base for a new profile</i> — copy the files to a new "
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

    # Build the file list, deduping so the bare-.ti3 import path (where
    # ti2_path is actually a .ti3) doesn't list the same file twice.
    seen: set[Path] = set()
    file_lines: list[str] = []
    def _add(p: Path) -> None:
        try:
            r = p.resolve()
        except OSError:
            r = p
        if r not in seen:
            seen.add(r)
            file_lines.append(f"  • {p.name}")
    _add(ti2_path)
    if ti1:
        _add(ti1)
    for t in tiffs:
        _add(t)
    ti3 = ti2_path.with_suffix(".ti3")
    if ti3.exists():
        _add(ti3)
    for ext in (".icc", ".icm"):
        icc = ti2_path.with_suffix(ext)
        if icc.exists():
            _add(icc)
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

    def _normalise(text: str) -> str:
        """Sanitise the typed name the same way set_target_name does (spaces→-,
        illegal chars→_), so the on-disk folder = the user-facing name. Empty
        in → empty out (so the dialog can still report "please enter a name")."""
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
                "That name points to the chart's own folder. Pick a different name."
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
    """Import an external chart into a fresh project as run1.

    Builds the per-run layout (see docs/dev_folder_layout.md): a project at
    ``working_dir/<new_name>/`` with ``project.json`` and the imported chart
    placed under ``runs/run1/`` with the canonical ``chart`` stem
    (``chart.ti2`` / ``chart.ti1`` / ``chart_NN.tif`` / ``chart.ti3`` /
    ``chart.icc``). Returns (run1 chart.ti2, copied page tiffs).
    """
    from core.file_manager import FileManager, Project

    # Defensive: the dialog already sanitises, but make the contract explicit
    # so any programmatic caller also gets a clean folder name.
    new_name = FileManager._sanitise(FileManager.strip_workfile_ext(new_name))

    old_stem = ti2_path.stem
    dest      = working_dir / new_name
    if overwrite and dest.exists():
        shutil.rmtree(dest)

    proj = Project.create(dest, new_name)
    run  = proj.current_run()
    run.ensure_dir()

    shutil.copy2(ti2_path, run.chart_ti2)
    if ti1:
        shutil.copy2(ti1, run.chart_ti1)

    # Chart recognition + channels sidecar travel with the chart when present.
    cht = ti2_path.with_suffix(".cht")
    if cht.exists():
        shutil.copy2(cht, run.chart_cht)
    channels = ti2_path.with_name(f"{old_stem}.channels.json")
    if channels.exists():
        shutil.copy2(channels, run.chart_channels_json)

    # Pages are renumbered <stem>_01.tif, <stem>_02.tif, … in sorted order.
    new_tiffs: list[Path] = []
    for i, tiff in enumerate(sorted(tiffs), start=1):
        new_tiff = run.dir / f"{run.stem}_{i:02d}.tif"
        shutil.copy2(tiff, new_tiff)
        new_tiffs.append(new_tiff)

    ti3 = ti2_path.with_suffix(".ti3")
    if ti3.exists():
        shutil.copy2(ti3, run.measurement_ti3)   # chart.ti3

    for ext in (".icc", ".icm"):
        icc = ti2_path.with_suffix(ext)
        if icc.exists():
            shutil.copy2(icc, run.profile_icc)    # chart.icc
            break

    return run.chart_ti2, new_tiffs


def _handle_outside_ti3_only(
    parent: "QWidget",
    ti3_path: Path,
    working_dir: Path,
) -> Path | None:
    """Import a bare .ti3 (no chart files) into a new project as the
    canonical measurement. The matching .icc/.icm is carried over too.
    Reuses _ask_profile_name to gather the project name + overwrite intent."""
    result = _ask_profile_name(parent, ti3_path, ti1=None, tiffs=[],
                                working_dir=working_dir)
    if result is None:
        return None
    name, overwrite = result
    return _copy_ti3_only(ti3_path, working_dir, name, overwrite=overwrite)


def _copy_ti3_only(
    ti3_path: Path,
    working_dir: Path,
    new_name: str,
    overwrite: bool = False,
) -> Path:
    """Create a project shell around an external .ti3.

    Places the .ti3 at ``runs/run1/<new_name>.ti3`` (the canonical
    measurement) and a sibling .icc/.icm (if present) at
    ``runs/run1/<new_name>.icc``. Returns the new .ti3 path.
    """
    from core.file_manager import FileManager, Project

    new_name = FileManager._sanitise(FileManager.strip_workfile_ext(new_name))
    dest = working_dir / new_name
    if overwrite and dest.exists():
        shutil.rmtree(dest)

    proj = Project.create(dest, new_name)
    run  = proj.current_run()
    run.ensure_dir()

    shutil.copy2(ti3_path, run.measurement_ti3)
    for ext in (".icc", ".icm"):
        icc = ti3_path.with_suffix(ext)
        if icc.exists():
            shutil.copy2(icc, run.profile_icc)
            break
    return run.measurement_ti3
