"""Embeddable 3D RGB-cube view of a patch set (a ``QWidget``, not a window).

Wraps the self-contained Plotly page built by :mod:`workflow.patch_cube` in a
``QWebEngineView`` — the same WebEngine path the gamut viewer uses (and which
``main.py`` pre-imports before the QApplication is created).

Two consumers:

* :class:`ui.dialogs.patch_cube_dialog.PatchCubeDialog` embeds one and pushes a
  single snapshot (the editor's "3D distribution…").
* The generator dialogs embed one *inline* and call :meth:`set_program` on every
  (debounced) colour-set change for a live preview. The page is built once and
  updated in place via ``Plotly.react`` (``window.cqUpdateCube``) — no reload,
  no Chromium/WebGL re-create — so it stays cheap and never spawns a second
  window (which on macOS would break the generator's modal session).

``teardown()`` drains the view synchronously (issue #38); call it from the host
dialog's ``done()`` / ``closeEvent`` while the event loop is still alive.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.logger import get_logger
from core.resource_path import resource_path
from core.webengine_shutdown import drain_web_view
from workflow import patch_cube
from core.i18n import tr

log = get_logger(__name__)

# Theme palette for the cube page (mirrors the gamut viewer's dark / light bg).
_THEME = {
    "dark":  {"bg": "#111111", "fg": "#cccccc", "grid": "#444444"},
    "light": {"bg": "#efebe6", "fg": "#3a352f", "grid": "#c7c2bb"},
}


class PatchCubePanel(QWidget):
    """A rotatable 3D RGB cube of a patch set, ready to drop into any layout."""

    def __init__(self, *, mode: str = "dark", parent=None) -> None:
        super().__init__(parent)
        self._theme = _THEME.get(mode, _THEME["dark"])
        self._tmp = tempfile.TemporaryDirectory()
        self._program: list[tuple] = []
        self._existing: list[tuple] = []
        # Live updates are pushed once the page has finished loading; a request
        # arriving before then is stashed and replayed in _on_load_finished.
        self._loaded = False
        self._pending_payload: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._web = self._make_web_view(self._theme["bg"])
        # Real QWebEngineView when WebEngine is present, else None (placeholder
        # label). Held separately so teardown() can drain it on close.
        self._web_view = self._web if hasattr(self._web, "setUrl") else None
        if self._web_view is not None:
            self._web_view.loadFinished.connect(self._on_load_finished)
        lay.addWidget(self._web, 1)
        self._render()

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

    def _render(self) -> None:
        if self._web_view is None:
            return  # WebEngine missing — placeholder label already shown
        plotly_path = resource_path("assets/plotly-gl3d.min.js")
        plotly_url = QUrl.fromLocalFile(str(plotly_path)).toString()
        html = patch_cube.build_cube_html(
            self._program, plotly_url, existing_program=self._existing,
            bg=self._theme["bg"], fg=self._theme["fg"],
            grid=self._theme["grid"])
        out = Path(self._tmp.name) / "patch_cube.html"
        out.write_text(html, encoding="utf-8")
        self._loaded = False
        self._web_view.setUrl(QUrl.fromLocalFile(str(out)))

    # ------------------------------------------------------------------
    # Live update
    # ------------------------------------------------------------------
    def set_program(self, program: list[tuple],
                    existing_program: list[tuple] | None = None) -> None:
        """Show a fresh patch set, redrawing the cube in place.

        Cheap and reload-free: builds a :func:`patch_cube.cube_payload` and hands
        it to the page's ``cqUpdateCube`` (Plotly.react). The generator dialogs
        call this on every debounced colour-set change."""
        self._program = list(program)
        self._existing = list(existing_program or [])
        if self._web_view is None:
            return
        payload = patch_cube.cube_payload(
            self._program, self._existing,
            fg=self._theme["fg"], grid=self._theme["grid"])
        if self._loaded:
            self._push(payload)
        else:
            self._pending_payload = payload  # replay once the page is ready

    def _push(self, payload: dict) -> None:
        if self._web_view is None:
            return
        js = "if(window.cqUpdateCube){cqUpdateCube(%s);}" % json.dumps(payload)
        self._web_view.page().runJavaScript(js)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return
        self._loaded = True
        if self._pending_payload is not None:
            self._push(self._pending_payload)
            self._pending_payload = None

    # ------------------------------------------------------------------
    def teardown(self) -> None:
        """Synchronously destroy the QWebEngineView (issue #38).

        Call from the host dialog's done()/closeEvent while the event loop is
        still alive. Without it the cube's Chromium subtree lingers until the app
        quits, where SIP walks a dangling pointer at _Py_Finalize and crashes
        with EXC_BAD_ACCESS. See :mod:`core.webengine_shutdown`. Idempotent."""
        drain_web_view(self._web_view)
        self._web_view = None
