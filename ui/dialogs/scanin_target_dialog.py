"""Tools → "Create scanner target" — write a chart's ``.cht`` + ``.cie`` from a
measurement, so the printed chart can be read off a flatbed scan (#97).

Pick a **measured** chart (its ``.ti3``); ChromIQ pairs it with the chart's exact
engine geometry and writes two files next to the chart:

* ``<chart>.cht`` — where every patch sits (per page, with fiducial corners),
* ``<chart>.cie`` — what every patch truly measured.

``scanin`` uses that pair to turn a scan of the printed chart into a measurement,
which ``colprof`` builds into a **scanner** profile. Pure file generation — no
Argyll binary is called here.

This is the standalone fallback; the primary entry point is an opt-in checkbox
after measuring. Both need an **engine** chart (exact geometry) plus a
measurement. Follows the shared Tools chrome (:class:`_ToolDialogBase`): green
masthead (the measure/scanner family), a ⓘ per option, ChromIQ's non-native
pickers.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from core.i18n import tr
from core.logger import get_logger
from ui.dialogs.tools_dialogs import (
    _ToolDialogBase,
    _initial_dir,
    _remember_dir,
    neutral_controls_qss,
)
from ui.styles import SPEC_GREEN
from ui.theme import resolve_mode
from ui.tooltip_button import TooltipButton
from ui.widgets import make_browse_button, open_file_dialog
from workflow.scanin_target import (
    ScaninTargetError,
    build_scanin_target_from_paths,
    has_scanner_geometry,
)

log = get_logger(__name__)

_TI3_FILTER = "Measurements (*.ti3);;All files (*)"


def _chart_base(ti3: Path) -> Path:
    """The chart's stem path for a chosen ``.ti3`` — strips a ``-verify`` suffix
    so a colour-managed verification read still resolves to ``<chart>``."""
    stem = ti3.stem
    if stem.endswith("-verify"):
        stem = stem[: -len("-verify")]
    return ti3.with_name(stem)


# Shared "which chart should I use?" guidance, appended to both scanner tool
# dialogs' ⓘ help. Defined once so the two dialogs share a single catalog key.
WHICH_CHART_HELP = tr(
    "Which chart makes the best scanner profile?\n\n"
    "The reference colours always come from your spectrophotometer "
    "measurement, so the profile is correct as long as you scan the very sheet "
    "you measured. The choice is only about how well the target matches what "
    "you'll actually scan:\n\n"
    "• Reuse the chart you already measured (no reprint) — free, correct, and "
    "ideal for general use and most photographers. Printed without colour "
    "management, it actually spans your printer's full gamut.\n\n"
    "• Print a fresh chart through your normal colour-managed workflow — the "
    "same paper and settings you use for real prints — then measure THAT sheet "
    "and scan it. This is best when you mainly scan your own colour-managed "
    "prints, because the target then matches the exact colours and rendering "
    "you produce. You must measure the reprint: you can't reuse the earlier "
    "measurement for a different sheet.\n\n"
    "Whichever you choose, match the paper and finish (glossy vs. matte) to "
    "what you'll scan.")


class ScaninTargetDialog(_ToolDialogBase):
    TOOL_KEY    = "scanner_target"
    TITLE       = tr("Create scanner target")
    EYEBROW     = tr("MEASURE · SCANNER TARGET")
    ACCENT      = SPEC_GREEN
    RUN_LABEL   = tr("Create scanner files")
    MIN_WIDTH   = 640

    HELP = tr(
        "Builds two small files from a chart you've already measured, so you can "
        "later profile a scanner from that same chart — no need to print or "
        "measure anything again.\n\n"
        "• <chart>.cht — where each patch sits on the page.\n"
        "• <chart>.cie — the real colours the spectrophotometer measured.\n\n"
        "Pick the chart's measurement (its .ti3); the two files are saved next to "
        "the chart. Then scan the printed chart on your scanner and use "
        "'Build scanner profile' — ArgyllCMS's scanin reads the scan against "
        "these files, and colprof turns that into your scanner's ICC profile.\n\n"
        "Works for charts created with ChromIQ's layout engine, which knows each "
        "patch's exact position. (Support for older or imported charts is planned.)"
    ) + "\n\n───────────────\n" + WHICH_CHART_HELP
    DESCRIPTION = tr(
        "Turn a measured chart into scanner-recognition files (.cht + .cie) so you "
        "can profile a scanner from the same chart.")

    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._ti3_path: Path | None = None
        # Readable secondary-text colour (palette(mid) is too faint on the dialog
        # background in both themes).
        light = resolve_mode(settings.get("appearance", "auto")) == "light"
        self._hint_color = "#4a4a4a" if light else "#b8b8b8"
        self._build_inputs()
        self._run_btn.setObjectName("primary")
        self.setStyleSheet(self.styleSheet() + neutral_controls_qss(SPEC_GREEN))
        self._style_primary_button()
        self._refresh()

    # ------------------------------------------------------------------ style
    def _style_primary_button(self) -> None:
        light = resolve_mode(self._settings.get("appearance", "auto")) == "light"
        c = SPEC_GREEN
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        hover = "#{:02x}{:02x}{:02x}".format(int(r * 0.86), int(g * 0.86), int(b * 0.86))
        dis_bg = "#e8e6e1" if light else "#1e1e1e"
        dis_fg = "#a8a4a0" if light else "#484848"
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background: {c}; border: 1px solid {c}; color: #0a0a0a;"
            f" font-weight: 700; }}"
            f"QPushButton:hover {{ background: {hover}; border-color: {hover}; }}"
            f"QPushButton:disabled {{ background: {dis_bg}; border: 1px solid {c};"
            f" color: {dis_fg}; }}")

    # ------------------------------------------------------------------ UI
    def _tip(self, title: str, body: str) -> TooltipButton:
        return TooltipButton(title, body, self, min_width=500, color=SPEC_GREEN)

    def _build_inputs(self) -> None:
        form = self._content

        head = QHBoxLayout()
        head.addWidget(QLabel(tr("Measured chart (.ti3):"), self))
        head.addStretch(1)
        head.addWidget(self._tip(
            tr("Measured chart"),
            tr("The measurement of the chart you want to scan later — its .ti3 "
            "file, produced when you measured the chart. ChromIQ pairs it with "
            "the chart's exact layout to write the scanner files. Pick the "
            "chart's own measurement; a colour-managed verification read works "
            "too.")), 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(head)

        row = QHBoxLayout()
        self._ti3_field = QLineEdit(self)
        self._ti3_field.setReadOnly(True)
        self._ti3_field.setPlaceholderText(tr("Pick the chart's measurement (.ti3)…"))
        row.addWidget(self._ti3_field, 1)
        browse = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        browse.clicked.connect(self._pick_ti3)
        row.addWidget(browse)
        form.addLayout(row)

        self._note = QLabel("", self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {self._hint_color}; font-size: 12px;")
        form.addWidget(self._note)

        out_note = QLabel(
            tr("The scanner files (.cht + .cie) are saved next to your chart. "
            "Multi-page charts get one .cht per page and a single .cie."), self)
        out_note.setWordWrap(True)
        out_note.setStyleSheet(f"color: {self._hint_color}; font-size: 12px;")
        form.addWidget(out_note)

    # ------------------------------------------------------------------ pick
    def _pick_ti3(self) -> None:
        path = open_file_dialog(
            self, tr("Choose the chart's measurement"), _TI3_FILTER,
            start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._ti3_path = Path(path)
        self._ti3_field.setText(path)
        _remember_dir(self._settings, self.TOOL_KEY, self._ti3_path.parent)
        self._refresh_note()
        self._refresh()

    def _refresh_note(self) -> None:
        if self._ti3_path is None:
            self._note.setText("")
            return
        base = _chart_base(self._ti3_path)
        channels = base.with_name(base.name + ".channels.json")
        if has_scanner_geometry(channels):
            self._note.setText(tr(
                "✓ Ready — scanner files will be written as {stem}.cht / .cie."
            ).format(stem=base.name))
        else:
            self._note.setText(tr(
                "⚠ ChromIQ doesn't have this chart's patch positions. Recreate the "
                "chart in a current ChromIQ version to enable scanner files."))

    # ------------------------------------------------------------------ run
    def _can_run(self) -> bool:
        return self._ti3_path is not None

    def _execute(self) -> None:
        assert self._ti3_path is not None
        self._log.clear()
        base = _chart_base(self._ti3_path)
        channels = base.with_name(base.name + ".channels.json")
        try:
            res = build_scanin_target_from_paths(channels, self._ti3_path, base)
        except ScaninTargetError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return
        for p in res.cht_paths:
            self._log.appendPlainText(tr("[OK] Wrote {path}").format(path=p))
        self._log.appendPlainText(tr("[OK] Wrote {path}").format(path=res.cie_path))
        self._log.appendPlainText((
            tr("Scanner files for {n} patches on one page saved next to your "
               "chart. Scan the printed chart, then use 'Build scanner profile'.")
            if res.n_pages == 1 else
            tr("Scanner files for {n} patches on {pages} pages saved next to your "
               "chart. Scan the printed chart, then use 'Build scanner profile'.")
        ).format(n=res.n_patches, pages=res.n_pages))
        _remember_dir(self._settings, self.TOOL_KEY, self._ti3_path.parent)
        self._finish(True)
