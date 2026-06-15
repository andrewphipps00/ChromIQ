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

from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QVBoxLayout, QDialog

from core.logger import get_logger
from core.i18n import tr
from ui.patch_cube_panel import PatchCubePanel
from ui.tab_header import dialog_masthead

log = get_logger(__name__)


class PatchCubeDialog(QDialog):
    """Modal-ish popup showing the patch set as a rotatable 3D RGB cube."""

    def __init__(self, program: list[tuple], *, mode: str = "dark",
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Patch distribution — 3D RGB cube"))
        self.resize(820, 760)
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

        self._panel = PatchCubePanel(mode=mode, parent=self)
        lay.addWidget(self._panel, 1)
        self._panel.set_program(program)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(buttons)

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
