"""Pop-up 3D RGB-cube view of the layout editor's patch set.

Launched from the editor's Patches controls ("3D distribution…"). Writes the
self-contained Plotly page built by :mod:`workflow.patch_cube` to a temp file
and shows it in a ``QWebEngineView`` — the same WebEngine path the gamut viewer
uses (and which ``main.py`` pre-imports before the QApplication is created).

View-only: the cube doesn't edit the chart, so the dialog holds no editor
state. It takes a snapshot of the current program and renders it; reopening
after edits re-renders the new snapshot.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialogButtonBox, QLabel, QVBoxLayout, QDialog

from core.logger import get_logger
from core.resource_path import resource_path
from workflow import patch_cube
from core.i18n import tr

log = get_logger(__name__)

# Theme palette for the cube page (mirrors the gamut viewer's dark / light bg).
_THEME = {
    "dark":  {"bg": "#111111", "fg": "#cccccc", "grid": "#444444"},
    "light": {"bg": "#efebe6", "fg": "#3a352f", "grid": "#c7c2bb"},
}


class PatchCubeDialog(QDialog):
    """Modal-ish popup showing the patch set as a rotatable 3D RGB cube."""

    def __init__(self, program: list[tuple], *, mode: str = "dark",
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Patch distribution — 3D RGB cube"))
        self.resize(820, 760)
        self.setMinimumSize(520, 480)
        self._program = list(program)
        self._tmp = tempfile.TemporaryDirectory()
        theme = _THEME.get(mode, _THEME["dark"])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._web = self._make_web_view(theme["bg"])
        lay.addWidget(self._web, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(buttons)

        self._render(theme)

    # ------------------------------------------------------------------
    def _make_web_view(self, bg: str):
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            log.warning("PyQt6-WebEngine unavailable — 3D cube disabled")
            lbl = QLabel(
                tr("Install PyQt6-WebEngine to view the 3D patch cube."), self)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lbl
        view = QWebEngineView(self)
        view.page().setBackgroundColor(QColor(bg))
        try:
            from PyQt6.QtWebEngineCore import QWebEngineSettings
            view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                True)
        except (ImportError, AttributeError):
            pass
        return view

    def _render(self, theme: dict) -> None:
        if not hasattr(self._web, "setUrl"):
            return  # WebEngine missing — placeholder label already shown
        plotly_path = resource_path("assets/plotly-gl3d.min.js")
        plotly_url = QUrl.fromLocalFile(str(plotly_path)).toString()
        html = patch_cube.build_cube_html(
            self._program, plotly_url,
            bg=theme["bg"], fg=theme["fg"], grid=theme["grid"])
        out = Path(self._tmp.name) / "patch_cube.html"
        out.write_text(html, encoding="utf-8")
        self._web.setUrl(QUrl.fromLocalFile(str(out)))
