"""Right-side Gamut Volume panel for the Check & Refine tab."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer, QUrl, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.styles import SPEC_VIOLET, TEXT_DIM
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, make_browse_button, open_file_dialog
from workflow.gamut_viewer import GamutViewer, GamutViewerParams
from workflow.viewgam_runner import ViewgamResult, ViewgamRunner

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings

log = get_logger(__name__)

_ACCENT = SPEC_VIOLET


class GamutPanel(QWidget):
    """Embedded iccgamut/viewgam runner: 3D viewer + volume + coverage comparison."""

    def __init__(
        self,
        runner:   "ArgyllRunner",
        settings: "AppSettings",
        parent:   QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner   = runner
        self._settings = settings

        # Workflow objects
        self._viewer         = GamutViewer(runner, self)
        self._viewgam_runner = ViewgamRunner(runner, self)

        # State
        self._icc_path:         Path | None = None
        self._compare_path:     Path | None = None
        self._primary_volume:   float | None = None
        self._compare_volume:   float | None = None
        self._primary_html:     str | None = None
        self._compare_html:     str | None = None
        self._combined_html:    str | None = None
        self._primary_gam:      str | None = None
        self._compare_gam:      str | None = None
        self._viewgam_result:   ViewgamResult | None = None
        self._pending_compare   = False

        self._viewer.finished.connect(self._on_viewer_finished)
        self._viewer.error.connect(self._on_viewer_error)
        self._viewgam_runner.finished.connect(self._on_viewgam_finished)
        self._viewgam_runner.error.connect(self._on_viewgam_error)

        self._build_ui()
        self._load_defaults()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_icc_path(self, path: Path | None) -> None:
        """Called by TabCheckRefine when the active profile changes."""
        self._icc_path = path
        self._primary_edit.setText(str(path) if path else "")
        self._run_btn.setEnabled(path is not None)
        self._reset_results()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Section header
        hdr = QLabel("GAMUT VOLUME", self)
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setStyleSheet(
            f"color: {TEXT_DIM}; background: transparent; padding: 4px;"
            " font-family: Menlo, Consolas, 'Courier New', monospace;"
            " font-size: 9px; font-weight: 300;"
        )
        root.addWidget(hdr)

        # 3D viewer
        self._viewer_widget = self._make_viewer_widget()
        self._viewer_widget.setMinimumHeight(280)
        self._viewer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._viewer_widget, stretch=1)
        root.addSpacing(6)

        # ── View toggle (hidden until combined view is ready) ───────────
        _mode_font = QFont()
        _mode_font.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
        _mode_font.setPointSize(9)
        _mode_font.setWeight(QFont.Weight.Bold)

        self._view_toggle_row = QWidget(self)
        toggle_layout = QHBoxLayout(self._view_toggle_row)
        toggle_layout.setContentsMargins(8, 6, 8, 6)
        toggle_layout.setSpacing(4)

        self._view_primary_btn  = QPushButton("PROFILE A", self._view_toggle_row)
        self._view_combined_btn = QPushButton("COMBINED",  self._view_toggle_row)
        self._view_compare_btn  = QPushButton("PROFILE B", self._view_toggle_row)
        for btn in (self._view_primary_btn, self._view_combined_btn, self._view_compare_btn):
            btn.setCheckable(True)
            btn.setObjectName("mode_btn")
            btn.setFont(_mode_font)
            btn.setFixedHeight(30)
        self._view_combined_btn.setChecked(True)

        self._view_primary_btn.clicked.connect(self._on_view_primary)
        self._view_combined_btn.clicked.connect(self._on_view_combined)
        self._view_compare_btn.clicked.connect(self._on_view_compare)

        toggle_layout.addWidget(self._view_primary_btn)
        toggle_layout.addWidget(self._view_combined_btn)
        toggle_layout.addWidget(self._view_compare_btn)
        toggle_layout.addStretch()

        self._view_toggle_row.setVisible(False)
        root.addWidget(self._view_toggle_row)

        # ── Scrollable options area ─────────────────────────────────────
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(12, 10, 12, 8)
        inner_layout.setSpacing(6)

        # ── Volume results ──────────────────────────────────────────────
        vol_grp = QGroupBox("Results", inner)
        vg = QVBoxLayout(vol_grp)
        vg.setContentsMargins(8, 10, 8, 8)
        vg.setSpacing(3)

        _dim_style  = (f"color: {TEXT_DIM}; font-family: Menlo, Consolas, 'Courier New', monospace;"
                       " font-size: 11px;")
        _bold_style = (f"color: {_ACCENT}; font-family: Menlo, Consolas, 'Courier New', monospace;"
                       " font-size: 12px; font-weight: bold;")

        self._vol_label             = QLabel("Volume: —", vol_grp)
        self._compare_vol_label     = QLabel("Compare: —", vol_grp)
        self._intersection_label    = QLabel("Intersection: —", vol_grp)
        self._coverage_ab_label     = QLabel("A covered by B: —", vol_grp)
        self._coverage_ba_label     = QLabel("B covered by A: —", vol_grp)

        self._vol_label.setStyleSheet(_bold_style)
        for lbl in (self._compare_vol_label, self._intersection_label,
                    self._coverage_ab_label, self._coverage_ba_label):
            lbl.setStyleSheet(_dim_style)

        vg.addWidget(self._vol_label)
        vg.addWidget(self._compare_vol_label)
        vg.addWidget(self._intersection_label)
        vg.addWidget(self._coverage_ab_label)
        vg.addWidget(self._coverage_ba_label)

        inner_layout.addWidget(vol_grp)

        # ── Profile selectors ───────────────────────────────────────────
        profile_grp = QGroupBox("Profiles", inner)
        pg = QVBoxLayout(profile_grp)
        pg.setContentsMargins(8, 10, 8, 8)
        pg.setSpacing(4)

        prim_row = QHBoxLayout()
        prim_row.addWidget(QLabel("Profile:", profile_grp))
        self._primary_edit = QLineEdit(profile_grp)
        self._primary_edit.setReadOnly(True)
        self._primary_edit.setPlaceholderText("Auto-filled from left panel")
        prim_row.addWidget(self._primary_edit, stretch=1)
        pg.addLayout(prim_row)

        cmp_row = QHBoxLayout()
        cmp_row.addWidget(QLabel("Compare with:", profile_grp))
        self._compare_edit = QLineEdit(profile_grp)
        self._compare_edit.setReadOnly(True)
        self._compare_edit.setPlaceholderText("Optional — browse a second ICC/ICM")
        cmp_row.addWidget(self._compare_edit, stretch=1)
        cmp_browse = make_browse_button(profile_grp, "Browse for comparison ICC/ICM", "folder_check")
        cmp_browse.clicked.connect(self._on_browse_compare)
        cmp_row.addWidget(cmp_browse)
        cmp_clear = QPushButton("✕", profile_grp)
        cmp_clear.setObjectName("browse")
        cmp_clear.setFixedWidth(28)
        cmp_clear.setToolTip("Clear comparison profile")
        cmp_clear.clicked.connect(self._on_clear_compare)
        cmp_row.addWidget(cmp_clear)
        pg.addLayout(cmp_row)

        inner_layout.addWidget(profile_grp)

        # ── iccgamut Options ────────────────────────────────────────────
        opts_grp = QGroupBox("iccgamut Options", inner)
        og = QVBoxLayout(opts_grp)
        og.setContentsMargins(8, 10, 8, 8)
        og.setSpacing(6)

        intent_row = QHBoxLayout()
        intent_row.addWidget(QLabel("Rendering intent:", opts_grp))
        self._intent_combo = NoScrollComboBox(opts_grp)
        self._intent_combo.addItem("Absolute colorimetric (default)", "a")
        self._intent_combo.addItem("Relative colorimetric", "r")
        self._intent_combo.addItem("Perceptual", "p")
        self._intent_combo.addItem("Saturation", "s")
        intent_row.addWidget(self._intent_combo, stretch=1)
        intent_row.addWidget(TooltipButton(
            "Rendering intent",
            "Selects which ICC table is used to compute the gamut boundary.\n"
            "Absolute colorimetric is the standard choice for output profiling.",
            opts_grp,
        ))
        og.addLayout(intent_row)

        pcs_row = QHBoxLayout()
        pcs_row.addWidget(QLabel("Colour space:", opts_grp))
        self._pcs_combo = NoScrollComboBox(opts_grp)
        self._pcs_combo.addItem("Lab (default)", "l")
        self._pcs_combo.addItem("CIECAM02 Jab", "j")
        pcs_row.addWidget(self._pcs_combo, stretch=1)
        pcs_row.addWidget(TooltipButton(
            "Profile Connection Space",
            "Lab computes the gamut in CIELAB space.\n"
            "CIECAM02 Jab uses appearance correlates and is more perceptually uniform\n"
            "but requires viewing condition parameters (Argyll defaults are used).",
            opts_grp,
        ))
        og.addLayout(pcs_row)

        sres_row = QHBoxLayout()
        sres_row.addWidget(QLabel("Surface resolution:", opts_grp))
        self._sres_spin = NoScrollDoubleSpinBox(opts_grp)
        self._sres_spin.setRange(1.0, 50.0)
        self._sres_spin.setSingleStep(1.0)
        self._sres_spin.setDecimals(0)
        self._sres_spin.setValue(20.0)
        self._sres_spin.setFixedWidth(70)
        sres_row.addWidget(self._sres_spin)
        sres_row.addStretch()
        sres_row.addWidget(TooltipButton(
            "Surface resolution",
            "Controls the density of vertices on the gamut surface mesh\n"
            "(range 1–50). Higher values produce smoother 3D plots at the cost\n"
            "of longer processing time. 20 is a good default.",
            opts_grp,
        ))
        og.addLayout(sres_row)

        func_row = QHBoxLayout()
        func_row.addWidget(QLabel("Mapping:", opts_grp))
        self._function_combo = NoScrollComboBox(opts_grp)
        self._function_combo.addItem("Forward — output gamut (default)", "f")
        self._function_combo.addItem("Backward — input gamut", "b")
        func_row.addWidget(self._function_combo, stretch=1)
        func_row.addWidget(TooltipButton(
            "Mapping direction",
            "Forward mapping visualises the profile's output (printable) gamut.\n"
            "Backward mapping visualises the input gamut — the range of Lab values\n"
            "the profile maps back to device values.",
            opts_grp,
        ))
        og.addLayout(func_row)

        self._axes_cb  = QCheckBox("Show axes && white/black point", opts_grp)
        self._cusps_cb = QCheckBox("Mark primary/secondary cusp points (-k)", opts_grp)
        self._edges_cb = QCheckBox("Show edge plot (-e)", opts_grp)
        self._axes_cb.setChecked(True)
        og.addWidget(self._axes_cb)
        og.addWidget(self._cusps_cb)
        og.addWidget(self._edges_cb)

        inner_layout.addWidget(opts_grp)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── Buttons (outside scroll — always visible at the bottom) ─────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 6, 12, 12)
        self._run_btn = QPushButton("Run Gamut Analysis", self)
        self._run_btn.setObjectName("primary")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._on_run)
        self._reset_view_btn = QPushButton("Reset View", self)
        self._reset_view_btn.setFixedHeight(36)
        self._reset_view_btn.clicked.connect(self._on_reset_view)
        self._save_btn = QPushButton("Save as Defaults", self)
        self._save_btn.setFixedHeight(36)
        self._save_btn.clicked.connect(self._on_save_defaults)
        btn_row.addWidget(self._run_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._reset_view_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

    def _make_viewer_widget(self) -> QWidget:
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView(self)
            view.setStyleSheet("background: #111111; border: none;")
            self._web_view = view
            self._show_placeholder()
            return view
        except ImportError:
            log.warning("PyQt6-WebEngine not available — using fallback placeholder")
            self._web_view = None
            lbl = QLabel(
                "Install PyQt6-WebEngine to view\nthe interactive 3D gamut",
                self,
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {TEXT_DIM}; background: #111111;"
                " font-family: Menlo, Consolas, 'Courier New', monospace; font-size: 10px;"
            )
            return lbl

    def _show_placeholder(self) -> None:
        if self._web_view is None:
            return
        html = (
            "<html><body style='background:#111111; margin:0; display:flex;"
            " align-items:center; justify-content:center; height:100vh;'>"
            f"<p style='color:{TEXT_DIM}; font-family:monospace; font-size:12px;"
            " text-align:center;'>Run gamut analysis<br>to view the 3D gamut</p>"
            "</body></html>"
        )
        self._web_view.setHtml(html)

    def _load_html(self, html_path: str) -> None:
        if self._web_view is None or not html_path:
            return
        self._web_view.setUrl(QUrl.fromLocalFile(html_path))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_results(self) -> None:
        self._primary_volume  = None
        self._compare_volume  = None
        self._primary_html    = None
        self._compare_html    = None
        self._combined_html   = None
        self._primary_gam     = None
        self._compare_gam     = None
        self._viewgam_result  = None
        self._view_toggle_row.setVisible(False)
        self._update_volume_labels()

    def _set_toggle_checked(self, active: QPushButton) -> None:
        for btn in (self._view_primary_btn, self._view_combined_btn, self._view_compare_btn):
            btn.setChecked(btn is active)

    # ------------------------------------------------------------------
    # Slots — view toggle
    # ------------------------------------------------------------------

    def _on_view_primary(self) -> None:
        self._set_toggle_checked(self._view_primary_btn)
        self._load_html(self._primary_html or "")

    def _on_view_combined(self) -> None:
        self._set_toggle_checked(self._view_combined_btn)
        self._load_html(self._combined_html or "")

    def _on_view_compare(self) -> None:
        self._set_toggle_checked(self._view_compare_btn)
        self._load_html(self._compare_html or "")

    # ------------------------------------------------------------------
    # Slots — file browse
    # ------------------------------------------------------------------

    def _on_browse_compare(self) -> None:
        argyll_bin = self._settings.get("argyll_bin_path", "")
        argyll_ref = ""
        if argyll_bin:
            candidate = Path(argyll_bin).parent / "ref"
            if candidate.exists():
                argyll_ref = str(candidate)

        sidebar = _system_icc_paths()
        if argyll_ref:
            sidebar.insert(0, argyll_ref)

        path = open_file_dialog(
            self,
            "Select comparison ICC/ICM profile",
            "ICC profiles (*.icc *.icm);;All files (*)",
            start_dir=argyll_ref or (sidebar[0] if sidebar else ""),
            extra_paths=sidebar,
        )
        if path:
            self._compare_path = Path(path)
            self._compare_edit.setText(path)
            self._compare_volume = None
            self._update_volume_labels()

    def _on_clear_compare(self) -> None:
        self._compare_path = None
        self._compare_edit.clear()
        self._compare_volume = None
        self._combined_html = None
        self._compare_html  = None
        self._compare_gam   = None
        self._viewgam_result = None
        self._view_toggle_row.setVisible(False)
        self._update_volume_labels()

    # ------------------------------------------------------------------
    # Slots — analysis workflow
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        if self._icc_path is None:
            return
        self._run_btn.setEnabled(False)
        self._reset_results()
        self._pending_compare = self._compare_path is not None
        self._run_primary()

    def _run_primary(self) -> None:
        params = self._collect_params(self._icc_path)
        self._viewer.run(params, on_line=lambda _: None, on_finish=lambda _: None)

    def _run_compare(self) -> None:
        if self._compare_path is None:
            return
        params = self._collect_params(self._compare_path)
        sub = GamutViewer(self._runner, self)
        sub.finished.connect(self._on_compare_finished)
        sub.error.connect(self._on_compare_error)
        sub.run(params, on_line=lambda _: None, on_finish=lambda _: None)

    def _run_viewgam(self) -> None:
        if not self._primary_gam or not self._compare_gam:
            return
        self._viewgam_runner.run(
            primary_gam = Path(self._primary_gam),
            compare_gam = Path(self._compare_gam),
            on_line     = lambda _: None,
            on_finish   = lambda _: None,
        )

    def _on_viewer_finished(self, volume: float, html_path: str, gam_path: str) -> None:
        self._primary_volume = volume
        self._primary_html   = html_path
        self._primary_gam    = gam_path
        if html_path:
            self._load_html(html_path)
        self._update_volume_labels()
        if self._pending_compare and self._compare_path is not None:
            self._pending_compare = False
            # Defer so ArgyllRunner's QProcess is fully torn down before next run
            QTimer.singleShot(0, self._run_compare)
        else:
            self._pending_compare = False
            self._run_btn.setEnabled(self._icc_path is not None)

    def _on_compare_finished(self, volume: float, html_path: str, gam_path: str) -> None:
        self._compare_volume = volume
        self._compare_html   = html_path
        self._compare_gam    = gam_path
        self._update_volume_labels()
        if self._primary_gam and self._compare_gam:
            self._run_viewgam()
        else:
            self._run_btn.setEnabled(self._icc_path is not None)

    def _on_viewgam_finished(self, result: ViewgamResult) -> None:
        self._combined_html  = result.html_path
        self._viewgam_result = result
        self._update_volume_labels()
        if result.html_path:
            self._load_html(result.html_path)
            self._view_toggle_row.setVisible(True)
            self._set_toggle_checked(self._view_combined_btn)
        self._run_btn.setEnabled(self._icc_path is not None)

    def _on_viewgam_error(self, msg: str) -> None:
        log.warning("viewgam: %s", msg)
        self._run_btn.setEnabled(self._icc_path is not None)

    def _on_compare_error(self, msg: str) -> None:
        log.warning("compare iccgamut: %s", msg)
        self._run_btn.setEnabled(self._icc_path is not None)

    def _on_viewer_error(self, msg: str) -> None:
        self._run_btn.setEnabled(self._icc_path is not None)
        self._pending_compare = False
        log.warning("GamutViewer error: %s", msg)

    def _on_reset_view(self) -> None:
        if self._web_view is not None:
            self._web_view.page().runJavaScript(
                "var x = document.querySelector('x3d');"
                " if (x && x.runtime) x.runtime.resetView();"
            )

    def _update_volume_labels(self) -> None:
        if self._primary_volume is not None:
            self._vol_label.setText(f"Volume: {self._primary_volume:,.0f} cc")
        else:
            self._vol_label.setText("Volume: —")

        if self._compare_volume is not None and self._primary_volume is not None:
            delta = (self._compare_volume - self._primary_volume) / self._primary_volume * 100
            sign  = "+" if delta >= 0 else ""
            self._compare_vol_label.setText(
                f"Compare: {self._compare_volume:,.0f} cc  (Δ {sign}{delta:.1f}%)"
            )
        elif self._compare_volume is not None:
            self._compare_vol_label.setText(f"Compare: {self._compare_volume:,.0f} cc")
        else:
            self._compare_vol_label.setText("Compare: —")

        r = self._viewgam_result
        if r and r.intersection_volume is not None:
            self._intersection_label.setText(f"Intersection: {r.intersection_volume:,.0f} cc")
            self._coverage_ab_label.setText(
                f"A covered by B: {r.primary_coverage_pct:.1f}%"
                if r.primary_coverage_pct is not None else "A covered by B: —"
            )
            self._coverage_ba_label.setText(
                f"B covered by A: {r.compare_coverage_pct:.1f}%"
                if r.compare_coverage_pct is not None else "B covered by A: —"
            )
        else:
            self._intersection_label.setText("Intersection: —")
            self._coverage_ab_label.setText("A covered by B: —")
            self._coverage_ba_label.setText("B covered by A: —")

    def _collect_params(self, icc_path: Path) -> GamutViewerParams:
        return GamutViewerParams(
            icc_path = icc_path,
            intent   = self._intent_combo.currentData(),
            pcs      = self._pcs_combo.currentData(),
            sres     = self._sres_spin.value(),
            axes     = self._axes_cb.isChecked(),
            cusps    = self._cusps_cb.isChecked(),
            edges    = self._edges_cb.isChecked(),
            function = self._function_combo.currentData(),
        )

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_defaults(self) -> None:
        s = self._settings
        _set_combo(self._intent_combo, s.get("gamut_intent", "a"))
        _set_combo(self._pcs_combo, s.get("gamut_pcs", "l"))
        _set_combo(self._function_combo, s.get("gamut_function", "f"))
        self._sres_spin.setValue(float(s.get("gamut_sres", 20.0)))
        self._axes_cb.setChecked(bool(s.get("gamut_axes", True)))
        self._cusps_cb.setChecked(bool(s.get("gamut_cusps", False)))
        self._edges_cb.setChecked(bool(s.get("gamut_edges", False)))

    def _on_save_defaults(self) -> None:
        s = self._settings
        s.set("gamut_intent",    self._intent_combo.currentData())
        s.set("gamut_pcs",       self._pcs_combo.currentData())
        s.set("gamut_function",  self._function_combo.currentData())
        s.set("gamut_sres",      self._sres_spin.value())
        s.set("gamut_axes",      self._axes_cb.isChecked())
        s.set("gamut_cusps",     self._cusps_cb.isChecked())
        s.set("gamut_edges",     self._edges_cb.isChecked())


# ---------------------------------------------------------------------------

def _set_combo(combo: NoScrollComboBox, value: str) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def _system_icc_paths() -> list[str]:
    """Return existing platform-specific ICC/ICM profile directories."""
    import sys
    home = Path.home()
    if sys.platform == "win32":
        import os
        win = os.environ.get("WINDIR", r"C:\Windows")
        candidates = [
            r"C:\Windows\System32\spool\drivers\color",
            str(Path(win) / "System32" / "spool" / "drivers" / "color"),
        ]
    else:
        candidates = [
            str(home / "Library/ColorSync/Profiles"),
            "/Library/ColorSync/Profiles",
            "/System/Library/ColorSync/Profiles",
        ]
    seen: set[str] = set()
    result = []
    for p in candidates:
        if p not in seen and Path(p).exists():
            seen.add(p)
            result.append(p)
    return result
