"""Soft-proof / Check image — Tools ▸ "Soft-proof / check an image".

Loads an image and a printer ICC profile and answers one question two ways:

  * **Preview** — an approximate on-screen soft-proof (how the image will look
    printed), with out-of-gamut colours optionally highlighted.
  * **Gamut fit** — the image's colour gamut overlaid on the printer's gamut in
    the 3D viewer, so you can see *which* colours fall outside.

plus a headline **% out of gamut** metric. The soft-proof + out-of-gamut math
lives in :mod:`workflow.softproof_runner`; the 3D overlay reuses
:class:`workflow.gamut_viewer.GamutViewer`, :class:`workflow.tiffgamut_runner`
and :class:`workflow.viewgam_runner.ViewgamRunner`.

ArgyllCMS only reads ICC **v2**, so the printer profile is checked with
:func:`workflow.icc_info.is_v4` up front and the run is blocked (with a pointer
to the Profile info tool) for v4 profiles. The 3D viewer is torn down via
:func:`core.webengine_shutdown.drain_web_view` on close (issue #38), generated
lazily and only from a downsampled image, so it can neither crash nor hang.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.logger import get_logger
from core.platform_paths import detect_display_profile, icc_system_dirs
from core.webengine_shutdown import drain_web_view
from ui.dialog_sizing import pin_min_height
from ui.dialogs.tools_dialogs import neutral_controls_qss
from ui.styles import SPEC_AMBER, SPEC_VIOLET, TEXT_DIM, TEXT_MAIN
from ui.tab_header import dialog_masthead
from ui.theme import resolve_mode
from ui.tiff_preview import TiffPreview
from ui.tooltip_button import InfoDialog, TooltipButton
from ui.widgets import (
    NoScrollComboBox, NoScrollDoubleSpinBox, make_browse_button, open_file_dialog,
    tint_dialog_primary,
)
from workflow.icc_info import is_v4
from workflow.softproof_runner import (
    SoftproofParams, SoftproofResult, SoftproofRunner, prepare_input_tiff,
    resolve_source_profile,
)

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)

_ACCENT = SPEC_VIOLET

_HELP = tr(
    "Check how an image will print on a given printer/paper profile before you "
    "commit ink to it.\n\n"
    "Pick an image and a printer ICC profile, then click Soft-proof. The Preview "
    "shows an approximate on-screen rendering of the print; turn on “Highlight "
    "out-of-gamut” to mark the colours the printer can't reproduce. The Gamut fit "
    "view shows the image's colours as a 3D shape overlaid on the printer's gamut, "
    "so you can see exactly where they fall outside.\n\n"
    "The on-screen preview is approximate (it assumes an sRGB-like display) and is "
    "not a colour-managed proof. ArgyllCMS reads only ICC v2 profiles, so v4 "
    "profiles (often from i1Profiler) can't be used here — inspect them with the "
    "Profile info tool."
)


class SoftproofDialog(QDialog):
    def __init__(
        self,
        runner: "ArgyllRunner",
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._settings = settings
        self._mode = resolve_mode(settings.get("appearance", "auto"))

        self._image_path: Path | None = None
        self._profile_path: Path | None = None
        self._display_path: Path | None = None
        self._result: SoftproofResult | None = None
        self._web_view = None
        self._combined_html: str | None = None
        self._gamut_busy = False

        # 3D pipeline workflow objects (created lazily / reused)
        self._iccgamut = None
        self._tiffgamut = None
        self._viewgam = None
        self._printer_gam: str | None = None
        self._image_gam: str | None = None

        self.setWindowTitle(tr("Soft-proof / check image"))
        self.setMinimumWidth(1180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._softproof = SoftproofRunner(runner, settings, self)
        self._softproof.finished.connect(self._on_softproof_done)
        self._softproof.error.connect(self._on_softproof_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head, _header, stripe = dialog_masthead(
            self, tr("PROFILE · SOFT-PROOF"), tr("Soft-proof / check image"),
            tooltip_title=tr("Soft-proof / check image"), tooltip_body=_HELP,
            accent=_ACCENT)
        root.addLayout(head)
        root.addWidget(stripe)

        self._inner = QVBoxLayout()
        self._inner.setContentsMargins(22, 14, 22, 16)
        self._inner.setSpacing(12)
        root.addLayout(self._inner)

        self._body = QLabel(
            tr("Pick an image and a printer profile to soft-proof it."), self)
        self._body.setWordWrap(True)
        self._inner.addWidget(self._body)

        # Side by side: all the options on the left, the image / 3D preview on
        # the right (so the preview gets the room, not the path fields).
        split = QHBoxLayout()
        split.setSpacing(16)
        left_panel = QWidget(self)
        left_panel.setFixedWidth(380)
        self._left = QVBoxLayout(left_panel)
        self._left.setContentsMargins(0, 0, 0, 0)
        self._left.setSpacing(12)
        split.addWidget(left_panel, 0)

        self._build_inputs()         # image … banner  → self._left (top)
        self._left.addStretch(1)     # push the action group to the bottom

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._left.addWidget(sep)

        self._build_action_group()   # status, toggles, Preview/Gamut, Soft-proof btn
        viewer = self._build_viewer()  # stack + out-of-gamut metric + Close (right)
        split.addWidget(viewer, 1)
        self._inner.addLayout(split, 1)

        # This window is violet-themed throughout (masthead, primary button,
        # metric), so its checkboxes / focus rings use the violet accent too,
        # rather than the neutral indicator the other tool dialogs use.
        self.setStyleSheet(neutral_controls_qss(_ACCENT))
        tint_dialog_primary(self, _ACCENT)
        self._preselect_display_profile()
        # Pre-fill the last image used, as a convenience across sessions.
        last_image = str(settings.get("softproof_last_image", "") or "")
        if last_image and Path(last_image).is_file():
            self._set_image(Path(last_image))

    def _preselect_display_profile(self) -> None:
        """Pre-fill the monitor profile with the OS's currently active display
        profile, so soft-proofing is tuned to this screen out of the box. Best
        effort — silently leaves it empty (approximate sRGB) if undetectable."""
        path = detect_display_profile()
        if path is None or not path.exists():
            return
        self._display_path = path
        # Show the profile's own description when we can read it, else the file
        # name; full path on hover.
        label = path.name
        try:
            from workflow.icc_info import read_icc
            desc = read_icc(path).description
            if desc:
                label = desc
        except Exception:  # noqa: BLE001
            pass
        self._monitor_edit.setText(label)
        self._monitor_edit.setToolTip(str(path))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _path_row(self, label: str, placeholder: str, tip: str, on_browse) -> QLineEdit:
        """A 'label / read-only path edit / browse' block stacked in the left column."""
        self._left.addWidget(QLabel(label, self))
        row = QHBoxLayout()
        row.setSpacing(6)
        edit = QLineEdit(self)
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, 1)
        btn = make_browse_button(self, tip, "folder_check")
        btn.clicked.connect(on_browse)
        row.addWidget(btn)
        self._left.addLayout(row)
        return edit

    def _build_inputs(self) -> None:
        self._image_edit = self._path_row(
            tr("Image:"), tr("Browse for an image (TIFF, JPEG, PNG)…"),
            tr("Browse for an image"), self._on_browse_image)
        self._profile_edit = self._path_row(
            tr("Printer profile:"), tr("Browse for the printer's ICC profile (v2)…"),
            tr("Browse for the printer ICC profile"), self._on_browse_profile)

        # Optional monitor profile — when set, the preview is rendered for that
        # display (a truer proof); empty = approximate sRGB.
        self._left.addWidget(QLabel(tr("Monitor profile (optional):"), self))
        mon_row = QHBoxLayout()
        mon_row.setSpacing(6)
        self._monitor_edit = QLineEdit(self)
        self._monitor_edit.setReadOnly(True)
        self._monitor_edit.setPlaceholderText(tr("Approximate sRGB display"))
        mon_row.addWidget(self._monitor_edit, 1)
        mon_browse = make_browse_button(self, tr("Browse for your monitor's ICC profile"), "folder_check")
        mon_browse.clicked.connect(self._on_browse_monitor)
        mon_row.addWidget(mon_browse)
        mon_clear = QPushButton("✕", self)
        mon_clear.setObjectName("browse_compact")
        mon_clear.setFixedWidth(28)
        mon_clear.setToolTip(tr("Clear monitor profile"))
        mon_clear.clicked.connect(self._on_clear_monitor)
        mon_row.addWidget(mon_clear)
        self._left.addLayout(mon_row)

        # Colour space + intent (each on its own row to fit the narrow column).
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel(tr("Colour space:"), self), 0, 0)
        self._source_combo = NoScrollComboBox(self)
        self._source_combo.addItem(tr("Embedded (else sRGB)"), "embedded")
        self._source_combo.addItem(tr("sRGB"), "srgb")
        self._source_combo.addItem(tr("Adobe RGB (1998)"), "adobergb")
        self._source_combo.addItem(tr("Display P3"), "p3")
        self._source_combo.addItem(tr("ProPhoto RGB"), "prophoto")
        grid.addWidget(self._source_combo, 0, 1)
        grid.addWidget(TooltipButton(
            tr("Soft-proof options"),
            tr("Colour space — how the image's RGB numbers should be interpreted. "
               "“Embedded” uses the profile stored in the image (falling back to sRGB if "
               "there is none or it's ICC v4). Most web/phone images are sRGB.\n\n"
               "Intent — how out-of-gamut colours are handled when mapping to the printer. "
               "Relative colorimetric (recommended) keeps in-gamut colours exact and clips "
               "the rest to the gamut boundary, which is what the out-of-gamut detection "
               "relies on. Perceptual compresses the whole image to fit."),
            self, min_width=520, color=_ACCENT), 0, 2)

        grid.addWidget(QLabel(tr("Intent:"), self), 1, 0)
        self._intent_combo = NoScrollComboBox(self)
        self._intent_combo.addItem(tr("Relative colorimetric"), "r")
        self._intent_combo.addItem(tr("Perceptual"), "p")
        self._intent_combo.addItem(tr("Saturation"), "s")
        self._intent_combo.addItem(tr("Absolute colorimetric"), "a")
        grid.addWidget(self._intent_combo, 1, 1)

        grid.addWidget(QLabel(tr("Out-of-gamut ΔE:"), self), 2, 0)
        self._threshold_spin = NoScrollDoubleSpinBox(self)
        self._threshold_spin.setRange(0.5, 20.0)
        self._threshold_spin.setSingleStep(0.5)
        self._threshold_spin.setDecimals(1)
        self._threshold_spin.setValue(2.0)
        self._threshold_spin.setMinimumWidth(96)   # room for value + spin arrows
        thr_wrap = QHBoxLayout()
        thr_wrap.setContentsMargins(0, 0, 0, 0)
        thr_wrap.addWidget(self._threshold_spin)
        thr_wrap.addStretch(1)
        grid.addLayout(thr_wrap, 2, 1)
        grid.addWidget(TooltipButton(
            tr("Out-of-gamut sensitivity"),
            tr("A pixel counts as out of gamut when the printer would shift its colour by "
               "more than this ΔE. Lower = stricter (flags more pixels); 2 is a good "
               "default. The mark colour is what those pixels are painted in the preview."),
            self, min_width=460, color=_ACCENT), 2, 2)

        grid.addWidget(QLabel(tr("Mark colour:"), self), 3, 0)
        self._highlight_combo = NoScrollComboBox(self)
        self._highlight_combo.addItem(tr("Grey"), "gray")
        self._highlight_combo.addItem(tr("Magenta"), "magenta")
        self._highlight_combo.addItem(tr("Cyan"), "cyan")
        grid.addWidget(self._highlight_combo, 3, 1)
        self._left.addLayout(grid)

        # v4 / advisory banner
        self._banner = QLabel("", self)
        self._banner.setWordWrap(True)
        self._banner.setTextFormat(Qt.TextFormat.RichText)
        self._banner.setVisible(False)
        self._left.addWidget(self._banner)

    def _build_action_group(self) -> None:
        """Bottom-of-left group: status, on/off + highlight options, the
        Preview/Gamut-fit toggle, and the Soft-proof button (last)."""
        self._status = QLabel(tr("Pick an image and a printer profile to begin."), self)
        self._status.setStyleSheet(f"color: {TEXT_DIM};")
        self._status.setWordWrap(True)
        self._left.addWidget(self._status)

        # Display options (above the toggle and the Soft-proof button).
        self._softproof_cb = QCheckBox(tr("Show soft-proof"), self)
        self._softproof_cb.setChecked(True)
        self._softproof_cb.toggled.connect(self._refresh_preview)
        self._left.addWidget(self._softproof_cb)

        self._highlight_cb = QCheckBox(tr("Highlight out-of-gamut"), self)
        self._highlight_cb.setChecked(True)
        self._highlight_cb.toggled.connect(self._refresh_preview)
        self._left.addWidget(self._highlight_cb)

        # Preview / Gamut-fit view toggle (under the options, above the button).
        toggle = QHBoxLayout()
        toggle.setSpacing(8)
        self._preview_btn = QPushButton(tr("Preview"), self)
        self._gamut_btn = QPushButton(tr("Gamut fit"), self)
        for b in (self._preview_btn, self._gamut_btn):
            b.setCheckable(True)
            b.setObjectName("mode_btn")
            b.setFixedHeight(28)
        self._preview_btn.setChecked(True)
        self._preview_btn.clicked.connect(lambda: self._show_view(0))
        self._gamut_btn.clicked.connect(lambda: self._show_view(1))
        toggle.addWidget(self._preview_btn, 1)
        toggle.addWidget(self._gamut_btn, 1)
        self._left.addLayout(toggle)

        # The Soft-proof button sits last, at the very bottom of the column.
        self._run_btn = QPushButton(tr("Soft-proof"), self)
        self._run_btn.setObjectName("primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._on_run)
        self._left.addWidget(self._run_btn)

    def _build_viewer(self) -> QWidget:
        right = QWidget(self)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        self._stack = QStackedWidget(right)
        self._stack.setMinimumHeight(360)
        self._stack.setMinimumWidth(720)

        self._preview = TiffPreview(self._stack)
        self._preview.set_appearance(self._mode)
        self._preview.set_caption(tr("APPROXIMATE SOFT-PROOF"))
        self._preview.set_navigation_visible(False)   # always one image at a time
        self._stack.addWidget(self._preview)

        self._gamut_frame = self._make_web_view()
        self._stack.addWidget(self._gamut_frame)
        rv.addWidget(self._stack, 1)

        # Out-of-gamut metric sits under the preview, with Close on the same
        # baseline as the Soft-proof button (so there's no empty strip under
        # the left column's button).
        bottom = QHBoxLayout()
        self._oog_label = QLabel(tr("Out of gamut: —"), self)
        self._oog_label.setStyleSheet(
            f"color: {_ACCENT}; font-family: Menlo, Consolas, monospace;"
            " font-size: 13px; font-weight: bold;")
        bottom.addStretch(1)
        bottom.addWidget(self._oog_label)
        bottom.addStretch(1)
        close_btn = QPushButton(tr("Close"), self)
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        rv.addLayout(bottom)
        return right

    def _make_web_view(self) -> QWidget:
        bg = "#efebe6" if self._mode == "light" else "#111111"
        border = "#d0ccc6" if self._mode == "light" else "#333"
        container = QWidget(self)
        container.setObjectName("gamutViewerFrame")
        container.setStyleSheet(
            "QWidget#gamutViewerFrame {"
            f" background: {bg}; border: 1px solid {border}; }}")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(1, 1, 1, 1)
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView(container)
            view.page().setBackgroundColor(QColor(bg))
            try:
                from PyQt6.QtWebEngineCore import QWebEngineSettings
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            except (ImportError, AttributeError):
                pass
            self._web_view = view
            lay.addWidget(view)
            self._set_web_placeholder(tr("Run a soft-proof, then open this tab to build "
                                         "the 3D image-vs-printer gamut."))
        except ImportError:
            self._web_view = None
            lbl = QLabel(tr("Install PyQt6-WebEngine to view the 3D gamut."), container)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {TEXT_DIM};")
            lay.addWidget(lbl)
        return container

    def _set_web_placeholder(self, text: str) -> None:
        if self._web_view is None:
            return
        bg = "#efebe6" if self._mode == "light" else "#111111"
        fg = "#7a7570" if self._mode == "light" else TEXT_DIM
        self._web_view.setHtml(
            f"<html><body style='background:{bg};margin:0;display:flex;"
            "align-items:center;justify-content:center;height:100vh;'>"
            f"<p style='color:{fg};font-family:Menlo,monospace;font-size:12px;"
            f"text-align:center;'>{text}</p></body></html>")

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        pin_min_height(
            self, min_width=1180, min_height=600,
            wrap_labels=(self._body, self._banner, self._status),
            inner_margins=self._inner.contentsMargins(), resize_width=True)

    # ------------------------------------------------------------------
    # File pickers + v4 guard
    # ------------------------------------------------------------------
    def _on_browse_image(self) -> None:
        # Start where the user last picked an image (the file's folder), for
        # convenience across sessions.
        last = str(self._settings.get("softproof_last_image", "") or "")
        start = str(Path(last).parent) if last and Path(last).parent.is_dir() \
            else str(Path.home())
        path = open_file_dialog(
            self, tr("Select an image"),
            tr("Images (*.tif *.tiff *.jpg *.jpeg *.png);;All files (*)"),
            start_dir=start, preview=True)
        if path:
            self._set_image(Path(path))

    def _set_image(self, path: Path) -> None:
        self._image_path = path
        self._image_edit.setText(str(path))
        self._settings.set("softproof_last_image", str(path))
        self._update_run_enabled()

    def _on_browse_profile(self) -> None:
        sidebar = [str(d) for d in icc_system_dirs() if Path(d).exists()]
        path = open_file_dialog(
            self, tr("Select the printer ICC profile"),
            tr("ICC profiles (*.icc *.icm);;All files (*)"),
            start_dir=(sidebar[0] if sidebar else ""), extra_paths=sidebar)
        if path:
            self._profile_path = Path(path)
            self._profile_edit.setText(path)
            self._check_profile_version()
            self._update_run_enabled()

    def _on_browse_monitor(self) -> None:
        sidebar = [str(d) for d in icc_system_dirs() if Path(d).exists()]
        path = open_file_dialog(
            self, tr("Select your monitor's ICC profile"),
            tr("ICC profiles (*.icc *.icm);;All files (*)"),
            start_dir=(sidebar[0] if sidebar else ""), extra_paths=sidebar)
        if path:
            self._display_path = Path(path)
            self._monitor_edit.setText(path)

    def _on_clear_monitor(self) -> None:
        self._display_path = None
        self._monitor_edit.clear()

    def _check_profile_version(self) -> None:
        if self._profile_path and is_v4(self._profile_path):
            self._banner.setText(tr(
                "<b>This printer profile is ICC v4.</b> ArgyllCMS can only read v2 "
                "profiles, so it can't be soft-proofed here. Open it in the "
                "<b>Profile info</b> tool for details, or use a v2 profile."))
            self._banner.setStyleSheet(
                f"QLabel {{ background: rgba(255,180,45,0.12); color: {SPEC_AMBER};"
                f" border: 1px solid {SPEC_AMBER}; border-radius: 4px; padding: 8px 10px; }}")
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)

    def _update_run_enabled(self) -> None:
        ok = bool(self._image_path and self._profile_path
                  and not (self._profile_path and is_v4(self._profile_path)))
        self._run_btn.setEnabled(ok)

    # ------------------------------------------------------------------
    # Soft-proof run
    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        if not (self._image_path and self._profile_path):
            return
        if self._runner.is_running:
            self._status.setText(tr("Another process is running — try again in a moment."))
            return
        self._run_btn.setEnabled(False)
        self._status.setText(tr("Soft-proofing…"))
        # Invalidate any previous 3D result (inputs changed).
        self._combined_html = None
        self._printer_gam = None
        self._image_gam = None
        self._set_web_placeholder(tr("Open this tab to build the 3D gamut for the new image."))
        params = SoftproofParams(
            image_path=self._image_path,
            printer_profile=self._profile_path,
            source_choice=self._source_combo.currentData(),
            intent=self._intent_combo.currentData(),
            threshold=self._threshold_spin.value(),
            highlight=self._highlight_combo.currentData(),
            display_profile=self._display_path,
        )
        self._softproof.run(params)

    def _on_softproof_done(self, result: SoftproofResult) -> None:
        self._result = result
        self._run_btn.setEnabled(True)
        self._oog_label.setText(tr("Out of gamut: {pct:.1f}%").format(pct=result.oog_percent))
        self._status.setText(tr("Done — {note}.").format(note=result.source_note))
        self._refresh_preview()
        self._show_view(0)

    def _on_softproof_error(self, msg: str) -> None:
        self._run_btn.setEnabled(True)
        self._status.setText(tr("Soft-proof failed."))
        InfoDialog(tr("Soft-proof failed"), msg, self, min_width=480).exec()

    def _refresh_preview(self) -> None:
        if not self._result:
            return
        show_proof = self._softproof_cb.isChecked()
        self._highlight_cb.setEnabled(show_proof)
        if not show_proof:
            path = self._result.original_path
            self._preview.set_caption(tr("ORIGINAL IMAGE"))
        else:
            path = (self._result.highlight_path if self._highlight_cb.isChecked()
                    else self._result.proof_path)
            self._preview.set_caption(tr("APPROXIMATE SOFT-PROOF"))
        self._preview.load_tiff([Path(path)])

    # ------------------------------------------------------------------
    # View toggle + lazy 3D
    # ------------------------------------------------------------------
    def _show_view(self, index: int) -> None:
        self._preview_btn.setChecked(index == 0)
        self._gamut_btn.setChecked(index == 1)
        self._stack.setCurrentIndex(index)
        if index == 1:
            self._ensure_gamut()

    def _ensure_gamut(self) -> None:
        if self._combined_html:
            self._load_html(self._combined_html)
            return
        if self._gamut_busy or self._result is None or self._profile_path is None:
            if self._result is None:
                self._set_web_placeholder(tr("Run a soft-proof first."))
            return
        if self._runner.is_running:
            self._set_web_placeholder(tr("Another process is running — try again in a moment."))
            return
        self._gamut_busy = True
        self._set_web_placeholder(tr("Building the 3D gamut… this can take a few seconds."))
        self._start_printer_gamut()

    def _bg(self) -> str:
        return "#efebe6" if self._mode == "light" else "#111111"

    def _start_printer_gamut(self) -> None:
        from workflow.gamut_viewer import GamutViewer, GamutViewerParams
        self._iccgamut = GamutViewer(self._runner, self)
        self._iccgamut.finished.connect(self._on_printer_gamut)
        self._iccgamut.error.connect(self._on_gamut_error)
        self._iccgamut.run(
            GamutViewerParams(icc_path=self._profile_path, sres=10.0),
            on_line=lambda _l: None, on_finish=lambda _c: None,
            themed=True, bg=self._bg())

    def _on_printer_gamut(self, _vol: float, html: str, gam: str) -> None:
        self._printer_gam = gam
        self._printer_html = html
        QTimer.singleShot(0, self._start_image_gamut)

    def _start_image_gamut(self) -> None:
        from workflow.tiffgamut_runner import TiffgamutRunner, TiffgamutParams
        work = Path(self._result.proof_path).parent
        # A small copy of the image keeps tiffgamut fast (bounded, no hang).
        try:
            small = prepare_input_tiff(self._image_path, work, max_dim=500)
        except (OSError, ValueError) as exc:
            self._on_gamut_error(str(exc))
            return
        src, _note = resolve_source_profile(
            self._image_path, self._source_combo.currentData(), self._settings, work)
        if src is None:
            self._on_gamut_error(tr("No source colour-space profile found."))
            return
        self._tiffgamut = TiffgamutRunner(self._runner, self)
        self._tiffgamut.finished.connect(self._on_image_gamut)
        self._tiffgamut.error.connect(self._on_gamut_error)
        self._tiffgamut.run(
            TiffgamutParams(image_path=small, profile_path=src, sres=10.0, filter_perc=90.0),
            themed=True, bg=self._bg())

    def _on_image_gamut(self, _vol: float, html: str, gam: str) -> None:
        self._image_gam = gam
        self._image_html = html
        QTimer.singleShot(0, self._start_viewgam)

    def _start_viewgam(self) -> None:
        from workflow.viewgam_runner import ViewgamRunner
        if not (self._printer_gam and self._image_gam):
            self._on_gamut_error(tr("Gamut files missing."))
            return
        self._viewgam = ViewgamRunner(self._runner, self)
        self._viewgam.finished.connect(self._on_viewgam_done)
        self._viewgam.error.connect(self._on_gamut_error)
        # primary = image (opaque, keeps its colours); compare = printer (shell).
        self._viewgam.run(
            primary_gam=Path(self._image_gam),
            compare_gam=Path(self._printer_gam),
            primary_html=Path(self._image_html) if self._image_html else None,
            compare_html=Path(self._printer_html) if self._printer_html else None,
            on_line=lambda _l: None, on_finish=lambda _c: None,
            themed=True, bg=self._bg())

    def _on_viewgam_done(self, result) -> None:
        self._gamut_busy = False
        self._combined_html = result.html_path
        if result.html_path:
            self._load_html(result.html_path)
        else:
            self._set_web_placeholder(tr("Could not build the combined 3D gamut."))

    def _on_gamut_error(self, msg: str) -> None:
        self._gamut_busy = False
        log.warning("softproof 3D gamut: %s", msg)
        self._set_web_placeholder(tr("The 3D gamut could not be built:\n{msg}").format(msg=msg))

    def _load_html(self, html_path: str) -> None:
        if self._web_view is not None and html_path:
            self._web_view.setUrl(QUrl.fromLocalFile(html_path))

    # ------------------------------------------------------------------
    # WebEngine teardown (issue #38) — never leave a live Chromium subtree
    # ------------------------------------------------------------------
    def _teardown_webengine(self) -> None:
        drain_web_view(self._web_view)
        self._web_view = None

    def reject(self) -> None:  # noqa: D102
        self._teardown_webengine()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._teardown_webengine()
        super().closeEvent(event)
