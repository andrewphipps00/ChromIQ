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

from PyQt6.QtCore import QEventLoop, QTimer, QUrl, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QDialogButtonBox, QLabel, QVBoxLayout, QDialog,
)

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
        # Real QWebEngineView when WebEngine is present, else None (placeholder
        # label). Held separately so _teardown_webengine can drain it on close.
        self._web_view = self._web if hasattr(self._web, "setUrl") else None
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

    # ------------------------------------------------------------------
    def _teardown_webengine(self) -> None:
        """Drain the QWebEngineView before the dialog is dismissed.

        Same shutdown drain GamutPanel / DriftPlotDialog use. Without it the
        cube's Chromium subtree stays alive (the dialog is parented, so it is
        not collected when .exec() returns) and is torn down only when the app
        quits — during _Py_Finalize, where SIP walks a dangling Chromium
        pointer and crashes with EXC_BAD_ACCESS. See issue #38.
        """
        view = self._web_view
        if view is None:
            return
        self._web_view = None
        try:
            view.loadFinished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            view.stop()
            view.setUrl(QUrl("about:blank"))
        except RuntimeError:
            pass
        # Let about:blank settle so pending Chromium IPC drains.
        loop = QEventLoop()
        QTimer.singleShot(200, loop.quit)
        loop.exec()
        try:
            page = view.page()
            if page is not None:
                page.setParent(None)
                page.deleteLater()
        except RuntimeError:
            pass
        try:
            view.setParent(None)
            view.deleteLater()
        except RuntimeError:
            pass
        # Actually run the deferred deletes before the dialog is destroyed.
        app = QApplication.instance()
        if app is not None:
            for _ in range(3):
                app.processEvents()

    def done(self, result: int) -> None:  # noqa: N802
        # done() is the single chokepoint for both accept() and reject().
        self._teardown_webengine()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._teardown_webengine()
        super().closeEvent(event)
