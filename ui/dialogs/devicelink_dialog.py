"""Tools → "Create device-link profile" (collink wrapper).

Builds an ICC **device-link** from a source profile + a destination (printer)
profile, with gamut-mapping control collink offers beyond colprof's stock
intents. The result is applied later in Photoshop ("Convert to Profile") or a
RIP — it is an export artifact, not part of ChromIQ's measure→profile loop.

Follows the shared Tools-dialog chrome (:class:`_ToolDialogBase`): cyan masthead,
a ⓘ help button on every option, and ChromIQ's own (non-native) file pickers.
The input rows live in a fade-edged scroll area so the optional **Expert**
section (per-image source gamut, abstract profile, calibration, 3DLUT export,
inverse gamut mode, forced white point) can't push the window off-screen. v4
source profiles are transcoded to v2 first (Argyll is v2-only); temp files are
deleted afterwards.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.logger import get_logger
from ui.dialogs.tools_dialogs import (
    _OutputRow,
    _ToolDialogBase,
    _initial_dir,
    _remember_dir,
    neutral_controls_qss,
)
from ui.fade_scroll import FadeScrollArea
from ui.styles import SPEC_CYAN
from ui.theme import resolve_mode
from ui.tooltip_button import TooltipButton
from ui.widgets import (
    CollapsibleGroupBox,
    NoScrollComboBox,
    confirm,
    tint_dialog_primary,
)
from workflow.collink_runner import CollinkParams, CollinkRunner
from workflow.icc_convert import NotConvertible, to_v2
from workflow.icc_info import IccParseError, read_icc

log = get_logger(__name__)

_ICC_FILTER = "ICC profiles (*.icc *.icm);;All files (*)"
_CAL_FILTER = "Calibration files (*.cal);;All files (*)"
_IMG_FILTER = "Images (*.tif *.tiff *.jpg *.jpeg *.png);;All files (*)"


class DeviceLinkDialog(_ToolDialogBase):
    TOOL_KEY  = "device_link"
    TITLE     = tr("Create device-link profile")
    EYEBROW   = tr("PROFILES · DEVICE-LINK")
    ACCENT    = SPEC_CYAN
    RUN_LABEL = tr("Create device-link")
    MIN_WIDTH = 700

    HELP = (
        tr("A device-link profile bakes a fixed 'source → your printer' colour "
        "conversion into one file, with the gamut mapping decided up front.\n\n"
        "Normally a colour-managed app converts a photo through a neutral middle "
        "step (Lab) every time you print, and the result can vary between apps and "
        "software versions. A device-link skips that live round-trip: you apply "
        "one pre-tested transform, so a stable printer/ink/paper setup gives the "
        "exact same colour across a whole series of prints — handy for photo books, "
        "exhibitions and art reproduction.\n\n"
        "How to use it:\n\n"
        "1. Pick the source profile — the colour space your images are in "
        "(sRGB, AdobeRGB, ProPhoto…).\n"
        "2. Pick the destination — the printer profile you built in ChromIQ.\n"
        "3. Choose how colours outside the printer's range are mapped (the "
        "rendering style) and the viewing conditions.\n"
        "4. Save the device-link, then in Photoshop use Edit → Convert to "
        "Profile and choose it, or load it in your RIP.\n\n"
        "Tip: it pays off most when you reuse the same printer/ink/paper for many "
        "images. For a one-off print the normal workflow is simpler."))
    DESCRIPTION = (
        tr("Create a fixed source→printer transform (an ICC device-link) with "
        "explicit gamut-mapping control, to apply in Photoshop's Convert to "
        "Profile or a RIP. Source profiles in ICC v4 are converted to v2 "
        "automatically (Argyll only reads v2)."))

    # (label, collink -i code)
    _INTENTS = (
        (tr("Photographic (perceptual) — recommended"), "p"),
        (tr("Accurate colours (relative colorimetric)"), "r"),
        (tr("Punchy (saturation)"), "s"),
        (tr("Proof another device (absolute colorimetric)"), "a"),
    )
    # (label, (src viewcond, dst viewcond))
    _VIEWCONDS = (
        (tr("Screen → print (typical room)"), ("mt", "pp")),
        (tr("Screen → print (D50 viewing booth / critical)"), ("mt", "pc")),
        (tr("Bright screen → print"), ("mb", "pp")),
    )
    # (label, collink -q code)
    _QUALITIES = (
        (tr("High (recommended)"), "h"),
        (tr("Ultra (slowest, finest)"), "u"),
        (tr("Medium (faster)"), "m"),
    )
    # (label, collink -3 code)  "" = off
    _LUT3D = (
        (tr("Off"), ""),
        (tr("IRIDAS / Resolve (.cube)"), "c"),
        (tr("eeColor (.txt)"), "e"),
        (tr("MadVR (.3dlut)"), "m"),
    )

    def __init__(self, runner, settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._collink = CollinkRunner(runner)
        self._src_path: Path | None = None
        self._dst_path: Path | None = None
        self._abstract_path: Path | None = None
        self._cal_path: Path | None = None
        self._image_path: Path | None = None
        self._temp_files: list[Path] = []
        self._build_inputs()
        self._autofill_destination()
        # The base styles interactive controls with the neutral indicator; this
        # window is cyan-themed throughout, so re-tint checkboxes, focus rings,
        # combos and the primary button to the masthead accent (appended so the
        # cyan rules win over the base's neutral ones, keeping its dark-mode
        # status-field fix intact).
        self._run_btn.setObjectName("primary")
        self.setStyleSheet(self.styleSheet() + neutral_controls_qss(SPEC_CYAN))
        tint_dialog_primary(self, SPEC_CYAN)
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _file_row(self, layout: QVBoxLayout, placeholder: str, on_pick):
        """A read-only path field + Browse button appended to ``layout``."""
        row = QHBoxLayout()
        field = QLineEdit(self)
        field.setReadOnly(True)
        field.setPlaceholderText(placeholder)
        row.addWidget(field, 1)
        browse = QPushButton(tr("Browse…"), self)
        browse.clicked.connect(on_pick)
        row.addWidget(browse)
        layout.addLayout(row)
        return field

    def _label_row(self, layout: QVBoxLayout, text: str,
                   tip_title: str, tip_body: str) -> None:
        head = QHBoxLayout()
        head.addWidget(QLabel(text, self))
        head.addStretch(1)
        head.addWidget(self._tip(tip_title, tip_body), 0,
                       Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

    def _tip(self, title: str, body: str, min_width: int = 520) -> TooltipButton:
        return TooltipButton(title, body, self, min_width=min_width, color=SPEC_CYAN)

    def _combo_row(self, layout: QVBoxLayout, label: str, tip_title: str,
                   tip_body: str, entries) -> NoScrollComboBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label, self))
        combo = NoScrollComboBox(self)
        for text, data in entries:
            combo.addItem(text, data)
        row.addWidget(combo, 1)
        row.addWidget(self._tip(tip_title, tip_body, 500), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        return combo

    def _check_row(self, layout: QVBoxLayout, label: str,
                   tip_title: str, tip_body: str, checked: bool = False) -> QCheckBox:
        row = QHBoxLayout()
        cb = QCheckBox(label, self)
        cb.setChecked(checked)
        row.addWidget(cb)
        row.addStretch(1)
        row.addWidget(self._tip(tip_title, tip_body, 480), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        return cb

    def _build_inputs(self) -> None:
        # All input rows live inside a fade-edged scroll area so the Expert
        # section can't push the dialog off a short screen.
        host = QWidget(self)
        form = QVBoxLayout(host)
        # Right inset so the scrollbar leaves a gap to the section frames /
        # inputs instead of butting against them.
        form.setContentsMargins(0, 0, 10, 0)
        form.setSpacing(10)
        self._form = form
        self._build_basic(form)
        self._build_expert(form)
        self._build_output(form)

        scroll = FadeScrollArea(self, surface="panel")
        # No frame: the QScrollArea's default border insets the viewport, which
        # offsets the fade overlay from the content edge and lets a sliver of
        # text show through. Without it the fade aligns flush with the rows.
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.FADE_H = 34          # taller, smoother fade than the 24px default
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        scroll.set_appearance(resolve_mode(self._settings.get("appearance", "auto")))
        scroll.setMinimumHeight(240)
        avail = QGuiApplication.primaryScreen().availableGeometry().height()
        scroll.setMaximumHeight(max(320, int(avail * 0.9) - 300))
        self._scroll = scroll
        self._content.addWidget(scroll)

    def _build_basic(self, form: QVBoxLayout) -> None:
        self._label_row(
            form, tr("Source profile — the colour space your images are in:"),
            tr("Source profile"),
            tr("The colour space your photos are saved in — most often sRGB, "
            "AdobeRGB (1998) or ProPhoto/ROMM. This tells the device-link where "
            "the colours are coming from. If the file is an ICC version-4 profile, "
            "ChromIQ converts a copy to version 2 automatically, because the "
            "ArgyllCMS engine only reads version 2."))
        self._src_field = self._file_row(
            form, tr("Pick an ICC profile (e.g. sRGB, AdobeRGB)…"),
            self._pick_source)

        self._label_row(
            form, tr("Destination profile — your printer profile:"),
            tr("Destination (printer) profile"),
            tr("The printer profile you built in ChromIQ for this printer, ink and "
            "paper. The device-link maps your source colours straight onto what "
            "this printer can reproduce. If you have a current project open, "
            "ChromIQ fills this in for you."))
        self._dst_field = self._file_row(
            form, tr("Pick your printer .icc (auto-filled from the current project)…"),
            self._pick_destination)

        self._intent_combo = self._combo_row(
            form, tr("Rendering style:"), tr("Rendering style"),
            tr("How colours that fall outside the printer's range are handled.\n\n"
            "• Photographic (perceptual) gently squeezes the whole picture so "
            "relationships between colours stay natural — the best default for "
            "photos.\n"
            "• Accurate keeps in-range colours exact and clips the rest — good for "
            "logos and spot colours.\n"
            "• Punchy favours vivid saturation.\n"
            "• Proof reproduces another device's colours as-is, for proofing.\n\n"
            "The choice is baked into the link — when you apply it in Photoshop the "
            "intent dropdown no longer matters."),
            self._INTENTS)

        self._view_combo = self._combo_row(
            form, tr("Viewing conditions:"), tr("Viewing conditions"),
            tr("Where the print will be looked at, so the colours are adapted to "
            "that light. 'Typical room' suits normal indoor light; the 'D50 "
            "viewing booth' option is for a colour-critical proofing booth. When in "
            "doubt, leave it on the typical room."),
            self._VIEWCONDS)

        self._quality_combo = self._combo_row(
            form, tr("Quality:"), tr("Quality"),
            tr("How finely the conversion table is computed. High is the right "
            "choice for a saved link you'll reuse. Ultra is a touch finer but much "
            "slower; Medium is faster if you're just experimenting."),
            self._QUALITIES)

        self._black_cb = self._check_row(
            form, tr("Map source black to printer black"), tr("Map black to black"),
            tr("Lines up the darkest source colour with the darkest the printer "
            "can make, so shadows use the paper's full depth instead of looking "
            "washed out or plugged. Recommended on for RGB photo printing."),
            checked=True)

        self._diag_cb = self._check_row(
            form, tr("Also save a gamut-mapping diagnostic (3D)"),
            tr("Gamut-mapping diagnostic"),
            tr("Writes an extra interactive 3D web page next to the link showing "
            "how colours were moved to fit the printer. Useful if you want to see "
            "what the mapping did; leave it off otherwise."))

    def _build_expert(self, form: QVBoxLayout) -> None:
        group = CollapsibleGroupBox(tr("Expert options"), self, collapsed=True)
        body = QVBoxLayout(group.body)
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(10)

        # 1 — per-image source gamut (runs tiffgamut before collink).
        self._image_cb = self._check_row(
            body, tr("Optimise the mapping for one specific image"),
            tr("Optimise for a specific image"),
            tr("Normally the link is built to fit the whole source colour space. "
            "Tick this and choose an image, and ChromIQ measures the colours that "
            "are actually in that picture and tunes the gamut mapping to them — so "
            "the colours you care about get the most faithful treatment. Best when "
            "you'll print one image (or a set with similar colours) many times."))
        self._image_cb.toggled.connect(self._on_image_toggled)
        self._image_field = self._file_row(
            body, tr("Pick the image to optimise for…"), self._pick_image)
        self._image_field.setEnabled(False)

        # 2 — abstract "tweak" profile.
        self._label_row(
            body, tr("Abstract 'tweak' profile (optional):"),
            tr("Abstract profile"),
            tr("An optional creative adjustment baked into the link — for example a "
            "profile that warms the whole image slightly or lifts contrast. Leave "
            "empty unless you've made one on purpose; it changes every colour the "
            "link touches."))
        self._abstract_field = self._file_row(
            body, tr("Pick an abstract profile…"), self._pick_abstract)

        # 3 — bake-in calibration.
        self._label_row(
            body, tr("Bake in calibration curves (optional):"),
            tr("Bake-in calibration"),
            tr("If your printer was calibrated to a known state (a .cal file), "
            "folding those curves into the link keeps the printer on that target "
            "without a separate calibration step. Only use the .cal that belongs to "
            "this exact printer/paper — the wrong one will skew every colour."))
        self._cal_field = self._file_row(
            body, tr("Pick a calibration (.cal) file…"), self._pick_cal)

        # 4 — 3DLUT export.
        self._lut3d_combo = self._combo_row(
            body, tr("Also export a 3DLUT:"), tr("3DLUT export"),
            tr("As well as the ICC device-link, write a 3D look-up table in a "
            "format that hardware boxes and some RIPs use. Leave it Off unless your "
            "workflow specifically asks for a .cube, eeColor or MadVR file."),
            self._LUT3D)

        # 5 — inverse-A2B gamut mode.
        self._inverse_cb = self._check_row(
            body, tr("Use inverse-table gamut mapping (advanced)"),
            tr("Inverse-table gamut mapping"),
            tr("Two ways of working out the mapping. The normal method is fine for "
            "almost everyone. The inverse-table method can occasionally place "
            "out-of-range colours a little more precisely on some printer profiles, "
            "at the cost of slower building. Try it only if you're comparing "
            "results."))

        # 6 — forced white point.
        self._white_cb = self._check_row(
            body, tr("Force source white to map exactly to paper white"),
            tr("Forced white point"),
            tr("Pins the brightest source colour to the paper's own white so a "
            "neutral white stays neutral, even if the paper is a little warm or "
            "cool. Helpful for clean whites on tinted art papers; usually not "
            "needed otherwise."))

        # Refit the dialog when the section is opened/closed so it grows if there
        # is room (and otherwise the scroll area takes over). The group toggles
        # itself on a title click; wrap that bound method to also refit.
        _orig_toggle = group.toggle
        def _toggle_and_refit():  # noqa: ANN202
            _orig_toggle()
            self._refit_height()
        group.toggle = _toggle_and_refit  # type: ignore[method-assign]
        form.addWidget(group)

    def _build_output(self, form: QVBoxLayout) -> None:
        form.addWidget(QLabel(tr("Save the device-link as:"), self))
        self._output = _OutputRow(
            self, ext_hint=".icc", on_change=self._refresh,
            initial_dir=_initial_dir(self._settings, self.TOOL_KEY),
            initial_name="")
        form.addWidget(self._output)

    def _on_image_toggled(self, on: bool) -> None:
        self._image_field.setEnabled(on)
        if not on:
            self._image_path = None
            self._image_field.clear()
        self._refresh()

    # --------------------------------------------------------------- pickers
    def _pick_source(self) -> None:
        p = self._pick_input_file(tr("Choose source profile"), _ICC_FILTER)
        if p:
            self._src_path = p
            self._src_field.setText(str(p))
            self._maybe_default_output_name()
            self._refresh()

    def _pick_destination(self) -> None:
        p = self._pick_input_file(tr("Choose printer profile"), _ICC_FILTER)
        if p:
            self._set_destination(p)

    def _pick_abstract(self) -> None:
        p = self._pick_input_file(tr("Choose abstract profile"), _ICC_FILTER)
        if p:
            self._abstract_path = p
            self._abstract_field.setText(str(p))

    def _pick_cal(self) -> None:
        p = self._pick_input_file(tr("Choose calibration file"), _CAL_FILTER)
        if p:
            self._cal_path = p
            self._cal_field.setText(str(p))

    def _pick_image(self) -> None:
        p = self._pick_input_file(tr("Choose image to optimise for"), _IMG_FILTER)
        if p:
            self._image_path = p
            self._image_field.setText(str(p))
            self._refresh()

    def _set_destination(self, p: Path) -> None:
        self._dst_path = p
        self._dst_field.setText(str(p))
        self._output._dir_edit.setText(str(p.parent))
        self._maybe_default_output_name()
        self._refresh()

    def _autofill_destination(self) -> None:
        try:
            from core.file_manager import FileManager
            icc = FileManager(self._settings).project().current_run().icc
        except Exception:  # noqa: BLE001 — best-effort convenience only
            return
        if icc and icc.exists():
            self._set_destination(icc)

    def _maybe_default_output_name(self) -> None:
        if self._output.name:
            return
        if self._dst_path and self._src_path:
            self._output._name_edit.setText(
                f"{self._dst_path.stem}-from-{self._src_path.stem}-devicelink")
        elif self._dst_path:
            self._output._name_edit.setText(f"{self._dst_path.stem}-devicelink")

    # --------------------------------------------------------------- run
    def _can_run(self) -> bool:
        if self._image_cb.isChecked() and self._image_path is None:
            return False
        return (self._src_path is not None and self._dst_path is not None
                and self._output.is_complete())

    def _execute(self) -> None:
        if self._runner.is_running:
            self._log.appendPlainText(tr("[BUSY] Another operation is running — please wait."))
            self._finish(False)
            return

        assert self._src_path and self._dst_path
        out_dir = self._output.directory
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{self._output.name}.icc"

        if out.exists():
            choice = confirm(
                self, tr("Overwrite existing file?"),
                tr("'{name}' already exists in:\n  {folder}\n\nOverwrite it?"
                   ).format(name=out.name, folder=out.parent),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if choice != QMessageBox.StandardButton.Yes:
                self._finish(False)
                return

        self._log.clear()
        try:
            self._src_v2 = self._ensure_v2(self._src_path, tr("source"))
            self._dst_v2 = self._ensure_v2(self._dst_path, tr("destination"))
        except _ConversionError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return

        # If optimising for an image, build its gamut first (tiffgamut), then
        # link; otherwise go straight to collink.
        if self._image_cb.isChecked() and self._image_path is not None:
            self._build_image_gamut_then_link(out)
        else:
            self._run_collink(out, src_gamut=None)

    def _build_image_gamut_then_link(self, out: Path) -> None:
        from workflow.tiffgamut_runner import TiffgamutParams, TiffgamutRunner
        self._log.appendPlainText(
            tr("Measuring the image's colours (this can take a moment)…"))
        tg = TiffgamutRunner(self._runner)
        self._gam_path: Path | None = None

        def _on_gamut_ready(_vol: float, _html: str, gam: str) -> None:
            self._gam_path = Path(gam) if gam else None

        tg.finished.connect(_on_gamut_ready)
        tg.error.connect(lambda msg: self._log.appendPlainText(f"[ERROR] {msg}"))

        def _on_done(code: int) -> None:
            if code != 0 or not self._gam_path or not self._gam_path.exists():
                self._log.appendPlainText(
                    tr("[ERROR] Could not analyse the image gamut — see messages above."))
                self._cleanup_temps()
                self._finish(False)
                return
            self._temp_files.append(self._gam_path)
            self._run_collink(out, src_gamut=self._gam_path)

        tg.run(
            TiffgamutParams(image_path=self._image_path, profile_path=self._src_v2),
            on_line=lambda ln: self._log_line(ln), on_finish=_on_done)

    def _run_collink(self, out: Path, src_gamut: Path | None) -> None:
        intent = self._intent_combo.currentData()
        src_vc, dst_vc = self._view_combo.currentData()
        params = CollinkParams(
            src_path=self._src_v2, dst_path=self._dst_v2, out_path=out,
            intent=intent, src_viewcond=src_vc, dst_viewcond=dst_vc,
            quality=self._quality_combo.currentData(),
            black_point_hack=self._black_cb.isChecked(),
            diagnostic=self._diag_cb.isChecked(),
            src_gamut=src_gamut,
            abstract=self._abstract_path,
            calibration=self._cal_path,
            lut3d=self._lut3d_combo.currentData(),
            inverse_gamut=self._inverse_cb.isChecked(),
            forced_white=self._white_cb.isChecked(),
            description=f"{out.stem} (ChromIQ device-link)",
            manufacturer="ChromIQ")
        self._log.appendPlainText(
            tr("Building device-link → {name}").format(name=out.name))

        def _on_finish(code: int) -> None:
            self._cleanup_temps()
            if code == 0 and out.exists():
                self._log.appendPlainText(tr("[OK] Wrote {path}").format(path=out))
                _remember_dir(self._settings, self.TOOL_KEY, out.parent)
                self._finish(True)
            else:
                fail = self._collink.primary_failure()
                msg = fail[1] if fail else tr("collink failed — see messages above.")
                self._log.appendPlainText(f"[ERROR] {msg}")
                self._finish(False)

        self._collink.run(params, lambda ln: self._log_line(ln), _on_finish)

    def _log_line(self, line: str) -> None:
        text = line.rstrip()
        if text and not text.endswith("%"):     # swallow the % progress spam
            self._log.appendPlainText(text)
            self._log.ensureCursorVisible()

    def _ensure_v2(self, path: Path, role: str) -> Path:
        try:
            info = read_icc(path)
        except IccParseError as exc:
            raise _ConversionError(
                tr("The {role} profile isn't a readable ICC file: {err}"
                   ).format(role=role, err=exc))
        if not info.is_v4:
            return path
        self._log.appendPlainText(
            tr("Converting {role} profile from ICC v4 to v2…").format(role=role))
        try:
            v2 = to_v2(path)
        except NotConvertible:
            raise _ConversionError(
                tr("The {role} profile is an ICC v4 profile that ChromIQ can't "
                   "convert automatically (it isn't a standard matrix RGB profile). "
                   "Please supply a version-2 profile.").format(role=role))
        self._temp_files.append(v2)
        return v2

    def _cleanup_temps(self) -> None:
        for p in self._temp_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_files.clear()

    def reject(self) -> None:  # noqa: D102
        self._cleanup_temps()
        super().reject()


class _ConversionError(Exception):
    """Raised internally when a profile can't be made v2-usable."""
