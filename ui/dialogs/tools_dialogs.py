"""Stand-alone utility dialogs launched from the masthead Tools popup.

Four tools — all already implemented inside the regular workflow — exposed as
project-free file-in/file-out utilities:

  * Average measurements        (workflow.average_runner)
  * Merge measurements          (workflow.ti3_merge)
  * TI1  -> i1Profiler          (workflow.i1profiler_export)
  * i1Profiler .txt -> TI3      (Argyll txt2ti3 via ArgyllRunner)

Each dialog explains the tool in plain language, takes one or more input files,
asks for an output destination + filename, and runs the underlying logic. File
pickers default to the configured ChromIQ working folder on first open, then
remember the last-used directory per tool across sessions.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from workflow.average_runner import AverageParams, AverageRunner
from workflow.i1profiler_export import export_from_ti1
from workflow.ti3_merge import Ti3MergeError, merge_measurements

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _working_dir(settings: "AppSettings") -> Path:
    custom = settings.get("custom_output_path", "")
    return Path(custom) if custom else Path.home() / "ChromIQ"


def _last_dir_key(tool_key: str) -> str:
    return f"tools_last_dir_{tool_key}"


def _initial_dir(settings: "AppSettings", tool_key: str) -> Path:
    stored = str(settings.get(_last_dir_key(tool_key), ""))
    if stored:
        p = Path(stored)
        if p.exists() and p.is_dir():
            return p
    return _working_dir(settings)


def _remember_dir(settings: "AppSettings", tool_key: str, path: Path) -> None:
    settings.set(_last_dir_key(tool_key), str(path))


# ---------------------------------------------------------------------------
# Base dialog
# ---------------------------------------------------------------------------

class _ToolDialogBase(QDialog):
    """Shared chrome: title, descriptive body, content area, log, Run/Close."""

    TOOL_KEY: str   = ""
    TITLE: str      = ""
    DESCRIPTION: str = ""
    RUN_LABEL: str  = "Run"
    MIN_WIDTH: int  = 620

    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._sized_once = False
        self.setWindowTitle(self.TITLE)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 16)
        outer.setSpacing(14)

        self._heading = QLabel(self.TITLE, self)
        self._heading.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        self._body = QLabel(self.DESCRIPTION, self)
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self._body)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        # Subclasses populate this area with their input rows.
        self._content = QVBoxLayout()
        self._content.setSpacing(10)
        outer.addLayout(self._content)

        # Log / status area
        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        self._log.setFixedHeight(120)
        self._log.setPlaceholderText("Status messages will appear here.")
        outer.addWidget(self._log)

        # Buttons
        bb = QDialogButtonBox(self)
        self._run_btn   = bb.addButton(self.RUN_LABEL, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close_btn = bb.addButton("Close",        QDialogButtonBox.ButtonRole.RejectRole)
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._close_btn.clicked.connect(self.reject)
        outer.addWidget(bb)

        # Defer building the input rows + first refresh until the subclass __init__
        # has finished setting up its own attributes.
        # Subclasses must call self._build_inputs() once their fields are wired.

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        """Open at the height the (now fully built) content needs, and lock a
        minimum height so the user can't drag the window short enough to make
        the layout overlap its widgets.

        Subclasses add their input rows after ``__init__``, so the dialog's
        natural sizeHint is only complete by first show. The layout's
        ``minimumSize`` is the floor below which widgets (the file list, the
        fixed-height log) can no longer shrink and start overlapping — pin the
        dialog's minimum height to it. Cap both at 90 % of the screen so a very
        long body can't push the dialog off-screen."""
        super().showEvent(event)
        if self._sized_once:
            return
        self._sized_once = True
        layout = self.layout()

        # Word-wrapped QLabels report a tiny minimumSize height (they can wrap
        # to one word), so the layout's own minimumSize underestimates the
        # space they actually need at the dialog's real width — which is what
        # let the window shrink until widgets overlapped. Pin each wrapping
        # label's height to its true heightForWidth at the content width so the
        # floor we read back below is accurate.
        target_w = max(self.MIN_WIDTH, layout.sizeHint().width())
        margins = layout.contentsMargins()
        avail = target_w - margins.left() - margins.right()
        for lbl in (self._heading, self._body):
            lbl.setMinimumHeight(max(0, lbl.heightForWidth(avail)))

        hint  = layout.sizeHint()
        floor = layout.minimumSize()
        screen = self.screen() or QGuiApplication.primaryScreen()
        cap_h = (int(screen.availableGeometry().height() * 0.9)
                 if screen is not None else hint.height())
        self.setMinimumHeight(min(floor.height(), cap_h))
        self.resize(target_w, min(hint.height(), cap_h))

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    def _build_inputs(self) -> None:
        """Subclasses populate ``self._content`` with their input rows."""
        raise NotImplementedError

    def _can_run(self) -> bool:
        """Whether the Run button should be enabled."""
        return False

    def _execute(self) -> None:
        """Perform the work. Must call ``self._finish(success)`` when done."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        self._run_btn.setEnabled(self._can_run())

    def _on_run_clicked(self) -> None:
        if not self._can_run():
            return
        self._set_busy(True)
        try:
            self._execute()
        except Exception as exc:
            log.exception("Tool '%s' failed", self.TOOL_KEY)
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)

    def _set_busy(self, busy: bool) -> None:
        self._run_btn.setEnabled(not busy and self._can_run())
        self._close_btn.setEnabled(not busy)

    def _finish(self, success: bool) -> None:
        self._set_busy(False)
        if success:
            self._log.appendPlainText("[DONE]")

    # ------------------------------------------------------------------
    # Picker helpers
    # ------------------------------------------------------------------
    def _pick_input_file(self, caption: str, name_filter: str) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self, caption, str(_initial_dir(self._settings, self.TOOL_KEY)), name_filter,
        )
        if not path:
            return None
        p = Path(path)
        _remember_dir(self._settings, self.TOOL_KEY, p.parent)
        return p

    def _pick_input_files(self, caption: str, name_filter: str) -> list[Path]:
        paths, _ = QFileDialog.getOpenFileNames(
            self, caption, str(_initial_dir(self._settings, self.TOOL_KEY)), name_filter,
        )
        if not paths:
            return []
        result = [Path(p) for p in paths]
        _remember_dir(self._settings, self.TOOL_KEY, result[0].parent)
        return result

    def _pick_output_dir(self, caption: str) -> Path | None:
        path = QFileDialog.getExistingDirectory(
            self, caption, str(_initial_dir(self._settings, self.TOOL_KEY)),
        )
        if not path:
            return None
        p = Path(path)
        _remember_dir(self._settings, self.TOOL_KEY, p)
        return p


# ---------------------------------------------------------------------------
# Reusable output-row widget
# ---------------------------------------------------------------------------

class _OutputRow(QWidget):
    """Folder picker + filename field, used as the destination of every tool.

    The filename is shown without its extension; the dialog appends the correct
    extension(s) when it builds the actual output paths.
    """

    def __init__(
        self,
        parent: QWidget,
        ext_hint: str,
        on_change: Callable[[], None],
        initial_dir: Path,
        initial_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._dir_edit = QLineEdit(str(initial_dir), self)
        self._dir_edit.setPlaceholderText("Destination folder")
        self._dir_edit.textChanged.connect(lambda _t: self._on_change())
        row.addWidget(self._dir_edit, 3)

        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)

        self._name_edit = QLineEdit(initial_name, self)
        self._name_edit.setPlaceholderText("Filename")
        self._name_edit.textChanged.connect(lambda _t: self._on_change())
        row.addWidget(self._name_edit, 2)

        ext_lbl = QLabel(ext_hint, self)
        ext_lbl.setStyleSheet("color: #888;")
        row.addWidget(ext_lbl)

    def _browse(self) -> None:
        start = self._dir_edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose destination folder", start)
        if path:
            self._dir_edit.setText(path)

    @property
    def directory(self) -> Path | None:
        text = self._dir_edit.text().strip()
        return Path(text) if text else None

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    def is_complete(self) -> bool:
        d = self.directory
        return bool(self.name) and d is not None


# ---------------------------------------------------------------------------
# Average measurements
# ---------------------------------------------------------------------------

class AverageMeasurementsDialog(_ToolDialogBase):
    TOOL_KEY    = "average"
    TITLE       = "Average measurements"
    RUN_LABEL   = "Average"
    DESCRIPTION = (
        "Combine two or more readings of the same printed chart into a single "
        "averaged measurement. Reading the chart several times and averaging "
        "the results reduces instrument noise — every patch's colour value "
        "becomes the mean across the reads, so a stray bad sweep has less "
        "influence on the final profile.\n\n"
        "Method: 'mean' averages every value; 'median' picks the middle value "
        "and ignores the extremes, which is more robust to a single bad "
        "reading. Median needs at least three input files to behave "
        "differently from mean — with only two reads the two methods are "
        "mathematically identical, so the choice is locked to mean.\n\n"
        "Requirements: every input must be a .ti3 measurement of the SAME chart "
        "(identical patch list, made with the same instrument). The averaged "
        ".ti3 can then be loaded into Build Profile."
    )

    def __init__(self, runner: "ArgyllRunner", settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._avg_runner = AverageRunner(runner)
        self._inputs: list[Path] = []
        self._build_inputs()
        self._refresh()

    def _build_inputs(self) -> None:
        info = QLabel("Measurement files to average (.ti3) — pick at least two:", self)
        self._content.addWidget(info)

        self._list = QListWidget(self)
        self._list.setMinimumHeight(110)
        self._content.addWidget(self._list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add…", self)
        add_btn.clicked.connect(self._add_files)
        btn_row.addWidget(add_btn)
        rem_btn = QPushButton("Remove selected", self)
        rem_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(rem_btn)
        btn_row.addStretch(1)
        self._content.addLayout(btn_row)

        # Method selector — median only meaningful for 3+ inputs.
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:", self))
        self._mean_btn   = QRadioButton("Mean (average)", self)
        self._median_btn = QRadioButton("Median",        self)
        self._mean_btn.setChecked(True)
        self._method_group = QButtonGroup(self)
        self._method_group.addButton(self._mean_btn)
        self._method_group.addButton(self._median_btn)
        method_row.addWidget(self._mean_btn)
        method_row.addWidget(self._median_btn)
        self._median_hint = QLabel("(needs 3+ files)", self)
        self._median_hint.setStyleSheet("color: #888;")
        method_row.addWidget(self._median_hint)
        method_row.addStretch(1)
        self._content.addLayout(method_row)

        self._content.addWidget(QLabel("Save the averaged measurement as:", self))
        self._output = _OutputRow(
            self,
            ext_hint=".ti3",
            on_change=self._refresh,
            initial_dir=_initial_dir(self._settings, self.TOOL_KEY),
            initial_name="averaged",
        )
        self._content.addWidget(self._output)
        self._update_method_state()

    def _add_files(self) -> None:
        files = self._pick_input_files(
            "Add measurement files",
            "Measurement files (*.ti3);;All files (*)",
        )
        for p in files:
            if p not in self._inputs:
                self._inputs.append(p)
                self._list.addItem(str(p))
        if files and not self._output.name:
            # Borrow the first input's stem as a starting point.
            self._output._name_edit.setText(f"{files[0].stem}-averaged")
        self._update_method_state()
        self._refresh()

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            row = self._list.row(item)
            self._list.takeItem(row)
            del self._inputs[row]
        self._update_method_state()
        self._refresh()

    def _update_method_state(self) -> None:
        """Median needs 3+ inputs (with 2, Argyll's `average` falls back to mean
        anyway). Lock the radio to mean below the threshold."""
        can_median = len(self._inputs) >= 3
        self._median_btn.setEnabled(can_median)
        self._median_hint.setVisible(not can_median)
        if not can_median and self._median_btn.isChecked():
            self._mean_btn.setChecked(True)

    def _can_run(self) -> bool:
        return len(self._inputs) >= 2 and self._output.is_complete()

    def _execute(self) -> None:
        if self._runner.is_running:
            self._log.appendPlainText("[BUSY] Another operation is running — please wait.")
            self._finish(False)
            return

        out_dir = self._output.directory
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{self._output.name}.ti3"

        if out.exists():
            confirm = QMessageBox.question(
                self,
                "Overwrite existing file?",
                f"'{out.name}' already exists in:\n  {out.parent}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._finish(False)
                return

        method = "median" if self._median_btn.isChecked() else "mean"
        self._log.clear()
        self._log.appendPlainText(
            f"Averaging {len(self._inputs)} measurement files ({method}) → {out.name}"
        )

        params = AverageParams(inputs=list(self._inputs), output=out, method=method)

        def _on_line(line: str) -> None:
            self._log.appendPlainText(line.rstrip())
            self._log.ensureCursorVisible()

        def _on_finish(result: Path | None) -> None:
            if result is not None:
                self._log.appendPlainText(f"[OK] Wrote {result}")
                _remember_dir(self._settings, self.TOOL_KEY, result.parent)
                self._finish(True)
            else:
                fail = self._avg_runner.primary_failure()
                if fail is not None:
                    self._log.appendPlainText(f"[ERROR] {fail[1]}")
                else:
                    self._log.appendPlainText("[ERROR] Averaging failed — see messages above.")
                self._finish(False)

        self._avg_runner.run(params, _on_line, _on_finish)


# ---------------------------------------------------------------------------
# Merge measurements
# ---------------------------------------------------------------------------

class MergeMeasurementsDialog(_ToolDialogBase):
    TOOL_KEY    = "merge"
    TITLE       = "Merge measurements"
    RUN_LABEL   = "Merge"
    DESCRIPTION = (
        "Combine the patches of several measurement files into one. Unlike "
        "averaging (which mixes repeated reads of the same chart), merging "
        "stacks the patches from DIFFERENT charts so the profiler has more data "
        "points to fit — useful for combining a pre-conditioning chart with a "
        "refinement chart, or for fusing complementary patch sets.\n\n"
        "Requirements: every file must use the same colour space and the same "
        "data layout (e.g. all spectral, all made with comparable instruments). "
        "Header information is taken from the primary file; the additional "
        "files contribute only their patches."
    )

    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._primary: Path | None    = None
        self._additional: list[Path]  = []
        self._build_inputs()
        self._refresh()

    def _build_inputs(self) -> None:
        self._primary_lbl = QLabel("Primary measurement (.ti3) — its header is kept:", self)
        self._content.addWidget(self._primary_lbl)
        row = QHBoxLayout()
        self._primary_field = QLineEdit(self)
        self._primary_field.setReadOnly(True)
        self._primary_field.setPlaceholderText("No file selected")
        row.addWidget(self._primary_field, 1)
        primary_btn = QPushButton("Browse…", self)
        primary_btn.clicked.connect(self._pick_primary)
        row.addWidget(primary_btn)
        self._content.addLayout(row)

        self._additional_lbl = QLabel(
            "Additional measurements (.ti3) — their patches are appended:", self
        )
        self._content.addWidget(self._additional_lbl)

        self._list = QListWidget(self)
        self._list.setMinimumHeight(90)
        self._content.addWidget(self._list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add…", self)
        add_btn.clicked.connect(self._add_additional)
        btn_row.addWidget(add_btn)
        rem_btn = QPushButton("Remove selected", self)
        rem_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(rem_btn)
        btn_row.addStretch(1)
        self._content.addLayout(btn_row)

        self._content.addWidget(QLabel("Save the merged measurement as:", self))
        self._output = _OutputRow(
            self,
            ext_hint=".ti3",
            on_change=self._refresh,
            initial_dir=_initial_dir(self._settings, self.TOOL_KEY),
            initial_name="merged",
        )
        self._content.addWidget(self._output)

    def _pick_primary(self) -> None:
        p = self._pick_input_file("Choose primary measurement", "Measurement files (*.ti3);;All files (*)")
        if p:
            self._primary = p
            self._primary_field.setText(str(p))
            if not self._output.name or self._output.name == "merged":
                self._output._name_edit.setText(f"{p.stem}-merged")
            self._refresh()

    def _add_additional(self) -> None:
        files = self._pick_input_files(
            "Add measurements to merge in", "Measurement files (*.ti3);;All files (*)"
        )
        for p in files:
            if p not in self._additional and p != self._primary:
                self._additional.append(p)
                self._list.addItem(str(p))
        self._refresh()

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            row = self._list.row(item)
            self._list.takeItem(row)
            del self._additional[row]
        self._refresh()

    def _can_run(self) -> bool:
        return (self._primary is not None
                and len(self._additional) >= 1
                and self._primary not in self._additional
                and self._output.is_complete())

    def _execute(self) -> None:
        out_dir = self._output.directory
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{self._output.name}.ti3"

        if out.exists():
            confirm = QMessageBox.question(
                self,
                "Overwrite existing file?",
                f"'{out.name}' already exists in:\n  {out.parent}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._finish(False)
                return

        inputs = [self._primary, *self._additional]
        self._log.clear()
        self._log.appendPlainText(
            f"Merging {len(inputs)} measurement files "
            f"(primary '{self._primary.name}') → {out.name}"
        )

        bin_dir = Path(self._settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        try:
            total = merge_measurements(inputs, out, bin_dir=bin_dir)
        except Ti3MergeError as exc:
            self._log.appendPlainText(f"[ERROR] {exc.message}")
            self._finish(False)
            return

        self._log.appendPlainText(f"[OK] Wrote {out} ({total} patches)")
        _remember_dir(self._settings, self.TOOL_KEY, out.parent)
        self._finish(True)


# ---------------------------------------------------------------------------
# TI1 → i1Profiler
# ---------------------------------------------------------------------------

class Ti1ToI1ProfilerDialog(_ToolDialogBase):
    TOOL_KEY    = "ti1_to_i1p"
    TITLE       = "Convert TI1 → i1Profiler"
    RUN_LABEL   = "Convert"
    DESCRIPTION = (
        "Convert an Argyll TI1 chart definition into the patch-set formats that "
        "X-Rite i1Profiler reads (.txt and .pxf). Use this when you want an "
        "i1iSis (or another i1Profiler-driven scanner) to measure a chart that "
        "Argyll's targen produced.\n\n"
        "Supported colour spaces:\n"
        "  • RGB    — writes both .txt (CGATS) and .pxf (CxF3)\n"
        "  • CMYK   — writes both .txt and .pxf\n"
        "  • CMYK+N — writes .pxf only (i1Profiler accepts extended-gamut sets "
        "only as CxF3)\n\n"
        "The resulting files are loaded in i1Profiler's 'Open patch set' workflow."
    )

    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._ti1: Path | None = None
        self._build_inputs()
        self._refresh()

    def _build_inputs(self) -> None:
        self._content.addWidget(QLabel("Argyll chart definition (.ti1):", self))
        row = QHBoxLayout()
        self._ti1_field = QLineEdit(self)
        self._ti1_field.setReadOnly(True)
        self._ti1_field.setPlaceholderText("No file selected")
        row.addWidget(self._ti1_field, 1)
        btn = QPushButton("Browse…", self)
        btn.clicked.connect(self._pick_ti1)
        row.addWidget(btn)
        self._content.addLayout(row)

        self._content.addWidget(QLabel("Save the i1Profiler patch set as:", self))
        self._output = _OutputRow(
            self,
            ext_hint=".pxf (+ .txt for RGB/CMYK)",
            on_change=self._refresh,
            initial_dir=_initial_dir(self._settings, self.TOOL_KEY),
            initial_name="i1profiler",
        )
        self._content.addWidget(self._output)

    def _pick_ti1(self) -> None:
        p = self._pick_input_file(
            "Choose chart definition", "Argyll chart files (*.ti1);;All files (*)"
        )
        if p:
            self._ti1 = p
            self._ti1_field.setText(str(p))
            if not self._output.name or self._output.name == "i1profiler":
                self._output._name_edit.setText(f"{p.stem}-i1profiler")
            self._refresh()

    def _can_run(self) -> bool:
        return self._ti1 is not None and self._output.is_complete()

    def _execute(self) -> None:
        out_dir = self._output.directory
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        base = self._output.name

        pxf = out_dir / f"{base}.pxf"
        txt = out_dir / f"{base}.txt"
        existing = [p for p in (pxf, txt) if p.exists()]
        if existing:
            names = ", ".join(p.name for p in existing)
            confirm = QMessageBox.question(
                self,
                "Overwrite existing file(s)?",
                f"These file(s) already exist in:\n  {out_dir}\n\n  {names}\n\nOverwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._finish(False)
                return

        self._log.clear()
        self._log.appendPlainText(f"Converting {self._ti1.name} → {base}.pxf / {base}.txt")

        try:
            txt_out, pxf_out = export_from_ti1(self._ti1, out_dir, base_name=base)
        except ValueError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return

        self._log.appendPlainText(f"[OK] Wrote {pxf_out}")
        if txt_out is not None:
            self._log.appendPlainText(f"[OK] Wrote {txt_out}")
        else:
            self._log.appendPlainText(
                "Note: CMYK+N targets are only written as .pxf — i1Profiler does "
                "not accept extended-gamut patch sets in CGATS .txt form."
            )
        _remember_dir(self._settings, self.TOOL_KEY, out_dir)
        self._finish(True)


# ---------------------------------------------------------------------------
# i1Profiler .txt → TI3
# ---------------------------------------------------------------------------

class I1ProfilerToTi3Dialog(_ToolDialogBase):
    TOOL_KEY    = "i1p_to_ti3"
    TITLE       = "Convert i1Profiler → TI3"
    RUN_LABEL   = "Convert"
    DESCRIPTION = (
        "Convert an i1Profiler measurement export (.txt) into an Argyll .ti3 "
        "measurement file. Use this when you measured a chart in i1Profiler "
        "(typically because you used an i1iSis scanner) and want to build the "
        "ICC profile with Argyll instead.\n\n"
        "Requirements: the .txt must contain real measured colour data (Lab/XYZ "
        "or spectral) for every patch — not just a reference patch list. The "
        "resulting .ti3 can be loaded into Build Profile."
    )

    def __init__(self, runner: "ArgyllRunner", settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._txt: Path | None = None
        self._build_inputs()
        self._refresh()

    def _build_inputs(self) -> None:
        self._content.addWidget(QLabel("i1Profiler measurement file (.txt):", self))
        row = QHBoxLayout()
        self._txt_field = QLineEdit(self)
        self._txt_field.setReadOnly(True)
        self._txt_field.setPlaceholderText("No file selected")
        row.addWidget(self._txt_field, 1)
        btn = QPushButton("Browse…", self)
        btn.clicked.connect(self._pick_txt)
        row.addWidget(btn)
        self._content.addLayout(row)

        self._content.addWidget(QLabel("Save the Argyll measurement as:", self))
        self._output = _OutputRow(
            self,
            ext_hint=".ti3",
            on_change=self._refresh,
            initial_dir=_initial_dir(self._settings, self.TOOL_KEY),
            initial_name="",
        )
        self._content.addWidget(self._output)

    def _pick_txt(self) -> None:
        p = self._pick_input_file(
            "Choose i1Profiler measurement", "i1Profiler exports (*.txt);;All files (*)"
        )
        if p:
            self._txt = p
            self._txt_field.setText(str(p))
            if not self._output.name:
                self._output._name_edit.setText(p.stem)
            self._refresh()

    def _can_run(self) -> bool:
        return self._txt is not None and self._output.is_complete()

    def _execute(self) -> None:
        if self._runner.is_running:
            self._log.appendPlainText("[BUSY] Another operation is running — please wait.")
            self._finish(False)
            return

        out_dir = self._output.directory
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        base = self._output.name
        out = out_dir / f"{base}.ti3"

        if out.exists():
            confirm = QMessageBox.question(
                self,
                "Overwrite existing file?",
                f"'{out.name}' already exists in:\n  {out.parent}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._finish(False)
                return

        # txt2ti3 wants both files in the cwd and writes <base>.ti3 next to <base>.txt.
        # Copy the input alongside the desired base name so we don't pollute the
        # source folder and so txt2ti3's output ends up where the user asked.
        import shutil
        staged_txt = out_dir / f"{base}.txt"
        if staged_txt.resolve() != self._txt.resolve():
            shutil.copy2(self._txt, staged_txt)

        self._log.clear()
        self._log.appendPlainText(f"Converting {self._txt.name} → {out.name}")

        collected: list[str] = []

        def _on_line(line: str) -> None:
            collected.append(line)
            self._log.appendPlainText(line.rstrip())
            self._log.ensureCursorVisible()

        def _on_finish(code: int) -> None:
            ok = code == 0 and out.exists()
            if ok:
                self._log.appendPlainText(f"[OK] Wrote {out}")
                _remember_dir(self._settings, self.TOOL_KEY, out.parent)
                self._finish(True)
            else:
                detail = next(
                    (l.strip() for l in collected if "error" in l.lower()),
                    "",
                )
                self._log.appendPlainText("[ERROR] txt2ti3 could not convert the file.")
                if detail:
                    self._log.appendPlainText(detail)
                self._finish(False)

        self._runner.run(
            "txt2ti3",
            ["-v", staged_txt.name, base],
            cwd=out_dir,
            on_line=_on_line,
            on_finish=_on_finish,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def open_tool_dialog(
    key: str,
    runner: "ArgyllRunner",
    settings: "AppSettings",
    parent: QWidget | None = None,
) -> None:
    """Open the dialog for the given tool key (no-op for unknown keys)."""
    if key == "average":
        dlg = AverageMeasurementsDialog(runner, settings, parent)
    elif key == "merge":
        dlg = MergeMeasurementsDialog(settings, parent)
    elif key == "ti1_to_i1p":
        dlg = Ti1ToI1ProfilerDialog(settings, parent)
    elif key == "i1p_to_ti3":
        dlg = I1ProfilerToTi3Dialog(runner, settings, parent)
    else:
        log.warning("Unknown tool key: %s", key)
        return
    dlg.exec()
