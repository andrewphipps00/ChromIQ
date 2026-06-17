"""Pop-up 3D RGB-cube view of the layout editor's patch set.

Launched from the editor's Patches controls ("3D distribution…"). Embeds the
shared :class:`ui.patch_cube_panel.PatchCubePanel` (a self-contained Plotly page
in a ``QWebEngineView``) and shows it modally — a one-shot snapshot of the
current patch set. The generator dialogs reuse the same panel *inline* for their
live preview; see :mod:`ui.patch_cube_panel`.

View-only: the cube doesn't edit the chart, so the dialog holds no editor
state. It takes a snapshot of the current program and renders it; reopening
after edits re-renders the new snapshot.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QVBoxLayout, QWidget,
)

from core.logger import get_logger
from core.i18n import tr
from ui.patch_cube_panel import PatchCubePanel
from ui.tab_header import dialog_masthead

log = get_logger(__name__)


class PatchCubeDialog(QDialog):
    """Modal-ish popup showing the patch set as a rotatable 3D RGB cube.

    ``target_name`` labels the cube (the currently loaded chart). When
    ``compare_presets`` (``[(label, .ti1 path), …]``) is given, a "Compare with
    profile:" dropdown lets the user open a second, camera-synced cube of that
    preset's patches alongside (#66)."""

    def __init__(self, program: list[tuple], *, mode: str = "dark",
                 target_name: str = "",
                 compare_presets: list[tuple[str, Path]] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._target_name = target_name or tr("Current chart")
        self._compare_presets = list(compare_presets or [])
        self.setWindowTitle(tr("Patch distribution — 3D RGB cube"))
        self.resize(1040 if self._compare_presets else 820, 760)
        self.setMinimumSize(520, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Tab-style masthead (eyebrow + serif title) over a full-width spectrum
        # stripe, matching the chart-design windows; the cube sits beneath it.
        head, _header, stripe = dialog_masthead(
            self, tr("PATCH SET · 3D VIEW"), tr("Patch distribution"),
            top=14, bottom=10)
        lay.addLayout(head)
        lay.addWidget(stripe)

        # Target name + (optional) "Compare with profile" dropdown.
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 6)
        bar.setSpacing(8)
        name_lbl = QLabel(tr("Chart: {name}").format(name=self._target_name), self)
        name_lbl.setStyleSheet("font-weight: bold;")
        bar.addWidget(name_lbl)
        bar.addStretch(1)
        if self._compare_presets:
            bar.addWidget(QLabel(tr("Compare with profile:"), self))
            self._compare_combo = QComboBox(self)
            # Cap the popup height and make it scroll (combobox-popup:0 is what
            # makes Qt honour maxVisibleItems instead of an over-long list).
            self._compare_combo.setMaxVisibleItems(15)
            self._compare_combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
            self._compare_combo.addItem(tr("None"), None)
            for group, items in self._compare_presets:
                self._compare_combo.insertSeparator(self._compare_combo.count())
                self._compare_combo.addItem(group)          # instrument header
                hdr = self._compare_combo.model().item(self._compare_combo.count() - 1)
                if hdr is not None:
                    f = hdr.font(); f.setBold(True); hdr.setFont(f)
                    # Bold, but the same colour as the entries (not greyed) — and
                    # still not selectable (Enabled flag kept, Selectable dropped).
                    hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)
                for label, path in items:
                    self._compare_combo.addItem("    " + label, str(path))
            self._compare_combo.currentIndexChanged.connect(self._on_compare_changed)
            bar.addWidget(self._compare_combo)
        lay.addLayout(bar)

        self._panel = PatchCubePanel(mode=mode, parent=self)
        self._panel.set_primary_label(self._target_name)
        lay.addWidget(self._panel, 1)
        self._panel.set_program(program)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(buttons)

    # ------------------------------------------------------------------
    def _on_compare_changed(self, _index: int) -> None:
        """Open / close the side-by-side comparison cube for the picked preset."""
        path = self._compare_combo.currentData()
        if not path:
            self._panel.clear_compare()
            return
        label = self._compare_combo.currentText().strip()   # drop the indent
        from workflow.ti2_relayout import load_rgb_program
        try:
            program_b = load_rgb_program(Path(path))
        except (ValueError, OSError) as exc:
            log.warning("compare preset %s: %s", path, exc)
            program_b = []
        if program_b:
            self._panel.set_compare(program_b, label, self._target_name)
        else:
            self._panel.clear_compare()

    # ------------------------------------------------------------------
    def exec(self) -> int:  # noqa: A003
        """Build the cube's web view *before* entering the modal loop.

        Instantiating a ``QWebEngineView`` creates a native surface; doing that
        while this dialog already holds the application-modal grab wedges the
        grab on Windows and freezes the app (issue #38 follow-up). So realise
        the dialog non-modally, build the view off the grab, let the surface
        settle, then go modal with the cube already in place."""
        self.show()                      # non-modal: realize the dialog
        self._panel.ensure_view()        # build the web view off the grab
        QApplication.processEvents()     # let the native surface settle
        return super().exec()

    # ------------------------------------------------------------------
    def done(self, result: int) -> None:  # noqa: N802
        # done() is the single chokepoint for both accept() and reject().
        self._panel.teardown()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._panel.teardown()
        super().closeEvent(event)
