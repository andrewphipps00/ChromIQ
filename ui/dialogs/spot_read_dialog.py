"""Single-patch (spot) reading tool — Tools ▸ "Read single patches".

Drives ArgyllCMS ``spotread`` via ``SpotReadManager`` to measure individual
colour patches off any material (or a display / light source), shows each
reading's L*a*b* with an on-screen sRGB swatch, and saves the set to a CSV plus
an Argyll ``.ti3``.

This is a standalone ``QDialog`` (its interactive Start/Take-reading/table shape
doesn't fit the Run/Close+log ``_ToolDialogBase``), but it reuses that module's
styling helpers and the calibration-popup pattern from the Measure tab so it
looks and behaves like the rest of the app.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from ui.dialogs.tools_dialogs import _indicator_color, neutral_controls_qss
from ui.styles import SPEC_GREEN
from ui.tab_header import dialog_masthead
from ui.widgets import NoScrollComboBox, tint_dialog_primary
from workflow.spot_read_io import SpotReading, write_csv, write_ti3
from workflow.spot_read_manager import SpotReadManager, SpotReadParams

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

_ACCENT = "#56d6a5"   # share the Measure tab's accent — this is measurement work

_HELP = tr(
    "Read individual colour patches with your measuring instrument, off any "
    "material — printed sheets, fabric, paint chips, or even a display.\n\n"
    "Click Start session, calibrate the instrument if prompted, then place it "
    "on a colour and click Take reading. Each reading is added to the table with "
    "its L*a*b* value and an approximate on-screen colour. Save writes a CSV (for "
    "a spreadsheet) and an Argyll .ti3 (for other tools)."
)


class SpotReadDialog(QDialog):
    _MODE_KEYS = ("reflective", "emissive", "ambient")

    def __init__(
        self,
        runner: "ArgyllRunner",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._manager = SpotReadManager(runner, self)
        self._readings: list[SpotReading] = []

        self.setWindowTitle(tr("Read single patches"))
        self.setMinimumWidth(960)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        # Tab-style masthead (eyebrow + serif title + ⓘ) over a full-width
        # spectrum stripe, matching the chart-design windows. The outer layout
        # spans full width so the stripe bleeds to the edges; the content below
        # re-adds the side inset.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head, _header, stripe = dialog_masthead(
            self, tr("INSTRUMENT · SPOT READ"), tr("Read single patches"),
            tooltip_title=tr("Read single patches"), tooltip_body=_HELP,
            accent=SPEC_GREEN)
        root.addLayout(head)
        root.addWidget(stripe)

        outer = QVBoxLayout()
        outer.setContentsMargins(22, 14, 22, 16)
        outer.setSpacing(12)
        root.addLayout(outer)

        body = QLabel(
            tr("Measure single colours off any material and save their L*a*b* values."),
            self,
        )
        body.setWordWrap(True)
        outer.addWidget(body)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        # --- Session controls (disabled once a session runs) ---------------
        # The instrument number (spotread -c) is left at 1: almost everyone has a
        # single measuring device connected, so we don't ask. Multi-instrument
        # users can still use the strip-based Measure tab.
        controls = QHBoxLayout()
        controls.setSpacing(10)

        controls.addWidget(QLabel(tr("Mode"), self))
        self._mode = NoScrollComboBox(self)
        self._mode.addItem(tr("Reflective (material)"))
        self._mode.addItem(tr("Emissive (display)"))
        self._mode.addItem(tr("Ambient (light)"))
        controls.addWidget(self._mode)

        self._skip_cal = QCheckBox(tr("Skip initial calibration"), self)
        controls.addWidget(self._skip_cal)
        controls.addStretch(1)
        outer.addLayout(controls)

        # --- Start / Take reading row -------------------------------------
        action = QHBoxLayout()
        action.setSpacing(10)
        self._start_btn = QPushButton(tr("Start session"), self)
        self._start_btn.setObjectName("primary")
        self._start_btn.clicked.connect(self._on_start_stop)
        action.addWidget(self._start_btn)

        self._read_btn = QPushButton(tr("Take reading"), self)
        self._read_btn.setEnabled(False)
        self._read_btn.clicked.connect(self._manager.take_reading)
        action.addWidget(self._read_btn)
        action.addStretch(1)
        outer.addLayout(action)

        self._status = QLabel(tr("Idle — click Start session to begin."), self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #909090;")
        outer.addWidget(self._status)

        # --- Results table -------------------------------------------------
        self._table = QTableWidget(0, 8, self)
        self._table.setHorizontalHeaderLabels([
            tr("Name"), tr("L*"), tr("a*"), tr("b*"),
            tr("X"), tr("Y"), tr("Z"), tr("Colour"),
        ])
        self._table.verticalHeader().setVisible(False)
        # Taller rows so each cell — especially the Colour swatch — has more
        # breathing room.
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hdr = self._table.horizontalHeader()
        # Name takes a modest fixed-ish width (interactive, so it can be dragged);
        # the remaining columns stretch to share the rest evenly instead of Name
        # hogging all the free space.
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 150)
        for c in range(1, 8):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self._table, 1)

        # --- Bottom buttons ------------------------------------------------
        bb = QDialogButtonBox(self)
        self._save_btn  = bb.addButton(tr("Save…"),  QDialogButtonBox.ButtonRole.ActionRole)
        self._clear_btn = bb.addButton(tr("Clear"),  QDialogButtonBox.ButtonRole.ResetRole)
        close_btn       = bb.addButton(tr("Close"),  QDialogButtonBox.ButtonRole.RejectRole)
        self._save_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        self._clear_btn.clicked.connect(self._on_clear)
        close_btn.clicked.connect(self.reject)
        outer.addWidget(bb)

        self.setStyleSheet(neutral_controls_qss(_indicator_color(settings)))
        # Tint the Start button with the Measure tab's green accent — this tool
        # is measurement work and reads as part of that family.
        tint_dialog_primary(self, _ACCENT)

        # --- Manager signals ----------------------------------------------
        m = self._manager
        m.reading_ready.connect(self._on_reading)
        m.ready_to_read.connect(self._on_ready)
        m.calibration_prompt.connect(self._on_calibration_prompt)
        m.misread.connect(lambda: self._set_status(tr("Misread — reposition and take the reading again.")))
        m.sensor_wrong_position.connect(lambda: self._set_status(tr("Instrument is in the wrong position.")))
        m.no_instrument.connect(self._on_no_instrument)
        m.device_busy.connect(self._on_device_busy)
        m.instrument_disconnected.connect(self._on_disconnected)
        m.coms_init_failed.connect(lambda s: self._on_init_failed(s))
        m.inst_init_failed.connect(lambda s: self._on_init_failed(s))
        m.session_ended.connect(self._on_session_ended)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def _on_start_stop(self) -> None:
        if self._manager.is_running:
            self._manager.quit()
            self._manager.abort()
            return
        params = SpotReadParams(
            mode=self._MODE_KEYS[self._mode.currentIndex()],
            disable_initial_cal=self._skip_cal.isChecked(),
        )
        self._set_session_running(True)
        self._set_status(tr("Starting instrument…"))
        self._manager.start(params, lambda _line: None)

    def _set_session_running(self, running: bool) -> None:
        self._mode.setEnabled(not running)
        self._skip_cal.setEnabled(not running)
        self._start_btn.setText(tr("Stop session") if running else tr("Start session"))
        if not running:
            self._read_btn.setEnabled(False)

    def _on_session_ended(self, code: int) -> None:
        self._set_session_running(False)
        self._set_status(tr("Session ended."))

    def _on_ready(self) -> None:
        self._read_btn.setEnabled(True)
        self._set_status(tr("Ready — place the instrument on a colour and click Take reading."))

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------
    def _on_reading(self, xyz: tuple, lab: tuple) -> None:
        name = tr("Patch {n}").format(n=len(self._readings) + 1)
        reading = SpotReading(name=name, xyz=tuple(xyz), lab=tuple(lab))
        self._readings.append(reading)
        self._append_row(reading)
        self._save_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._set_status(
            tr("Read {name}: L* {l:.1f}  a* {a:.1f}  b* {b:.1f}").format(
                name=name, l=lab[0], a=lab[1], b=lab[2])
        )

    def _append_row(self, r: SpotReading) -> None:
        self._table.blockSignals(True)
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_item = QTableWidgetItem(r.name)
        name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 0, name_item)

        vals = (*r.lab, *r.xyz)
        for col, v in enumerate(vals, start=1):
            item = QTableWidgetItem(f"{v:.2f}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, col, item)

        swatch = QTableWidgetItem(r.hex)
        swatch.setFlags(swatch.flags() & ~Qt.ItemFlag.ItemIsEditable)
        swatch.setBackground(QColor(r.hex))
        swatch.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 7, swatch)

        self._table.blockSignals(False)
        self._table.scrollToBottom()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self._readings):
            self._readings[row].name = item.text()

    def _on_clear(self) -> None:
        self._readings.clear()
        self._table.setRowCount(0)
        self._save_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

    def _on_save(self) -> None:
        if not self._readings:
            return
        dlg = QFileDialog(self, tr("Save spot readings"), str(Path.home() / "spot-readings"))
        dlg.setOptions(QFileDialog.Option.DontUseNativeDialog)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setNameFilter(tr("Spot readings (*.csv)"))
        dlg.selectFile("spot-readings.csv")
        if dlg.exec() != QFileDialog.DialogCode.Accepted:
            return
        chosen = dlg.selectedFiles()
        if not chosen:
            return
        base = Path(chosen[0]).with_suffix("")
        try:
            csv_path = write_csv(base.with_suffix(".csv"), self._readings)
            ti3_path = write_ti3(base.with_suffix(".ti3"), self._readings)
        except OSError as exc:
            QMessageBox.warning(self, tr("Save failed"), str(exc))
            return
        QMessageBox.information(
            self, tr("Saved"),
            tr("Readings saved to:\n{csv}\n{ti3}").format(
                csv=csv_path.name, ti3=ti3_path.name),
        )

    # ------------------------------------------------------------------
    # Calibration + error pop-ups
    # ------------------------------------------------------------------
    def _on_calibration_prompt(self) -> None:
        # Same wording as the Measure tab's calibration pop-up — generic but
        # clear — with the strip-specific tail swapped for a spot-read one.
        self._read_btn.setEnabled(False)
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Calibration Required"))
        dlg.setMinimumWidth(500)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(16)
        lay.setContentsMargins(24, 20, 24, 20)

        msg = QLabel(
            tr("<b>Your instrument needs to be calibrated before measuring.</b><br><br>"
               "Place the instrument in the <b>calibration position</b> as described "
               "in its manual, then click <b>Start Calibration</b>.<br><br>"
               "The calibration takes only a few seconds. Once it is complete, you "
               "can start taking readings."),
            dlg,
        )
        msg.setWordWrap(True)
        msg.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(msg)

        box = QDialogButtonBox(dlg)
        ok = box.addButton(tr("Start Calibration"), QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        box.accepted.connect(dlg.accept)
        lay.addWidget(box)

        tint_dialog_primary(dlg, _ACCENT)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._manager.send_key("\r")
            self._set_status(tr("Calibrating…"))
        else:
            # Dismissed — exit spotread cleanly.
            self._manager.send_key("\x1b")

    def _on_no_instrument(self) -> None:
        self._set_status(tr("No instrument detected."))
        QMessageBox.warning(
            self, tr("No instrument detected"),
            tr("No measuring instrument was found. Connect it and try again."),
        )

    def _on_device_busy(self) -> None:
        self._set_status(tr("Instrument is in use by another program."))
        QMessageBox.warning(
            self, tr("Instrument busy"),
            tr("The instrument is being used by another program. Close it and try again."),
        )

    def _on_disconnected(self) -> None:
        self._set_status(tr("Instrument disconnected."))

    def _on_init_failed(self, detail: str) -> None:
        self._set_status(tr("Could not start the instrument: {detail}").format(detail=detail))

    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def reject(self) -> None:  # noqa: D102
        if self._manager.is_running:
            self._manager.quit()
            self._manager.abort()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._manager.is_running:
            self._manager.quit()
            self._manager.abort()
        super().closeEvent(event)
