"""Right-side Gamut Volume panel for the Check & Refine tab."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEventLoop, QSize, QTimer, QUrl, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.styles import SPEC_VIOLET, TEXT_DIM
from ui.tooltip_button import InfoDialog, TooltipButton
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
        toggle_layout.setContentsMargins(12, 6, 12, 6)
        toggle_layout.setSpacing(8)

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

        # ── Per-compare-profile controls (shown only in Combined mode) ──
        _slider_ss = (
            "QSlider::groove:horizontal { height: 4px; background: #333333;"
            " border-radius: 2px; }"
            f"QSlider::handle:horizontal {{ background: {SPEC_VIOLET}; border: none;"
            " width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }"
            f"QSlider::sub-page:horizontal {{ background: {SPEC_VIOLET};"
            " border-radius: 2px; }"
        )
        self._compare_controls = QWidget(self._view_toggle_row)
        _cl = QHBoxLayout(self._compare_controls)
        _cl.setContentsMargins(0, 10, 0, 0)
        _cl.setSpacing(6)
        _cl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, self._compare_controls)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(50)
        self._opacity_slider.setFixedWidth(80)
        self._opacity_slider.setStyleSheet(_slider_ss)
        self._opacity_label = QLabel("50%", self._compare_controls)
        self._opacity_label.setFixedWidth(34)
        self._sat_slider = QSlider(Qt.Orientation.Horizontal, self._compare_controls)
        self._sat_slider.setRange(0, 100)
        self._sat_slider.setValue(100)
        self._sat_slider.setFixedWidth(80)
        self._sat_slider.setStyleSheet(_slider_ss)
        self._sat_label = QLabel("100%", self._compare_controls)
        self._sat_label.setFixedWidth(34)
        _cl.addWidget(QLabel("Opacity:"))
        _cl.addWidget(self._opacity_slider)
        _cl.addWidget(self._opacity_label)
        _cl.addSpacing(10)
        _cl.addWidget(QLabel("Sat.:"))
        _cl.addWidget(self._sat_slider)
        _cl.addWidget(self._sat_label)
        self._compare_controls.setVisible(False)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._sat_slider.valueChanged.connect(self._on_saturation_changed)

        toggle_layout.addSpacing(16)
        toggle_layout.addWidget(self._compare_controls)
        toggle_layout.setAlignment(self._compare_controls, Qt.AlignmentFlag.AlignVCenter)
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
        pg.setSpacing(8)

        prim_row = QHBoxLayout()
        prim_row.addWidget(QLabel("Profile:", profile_grp))
        self._primary_edit = QLineEdit(profile_grp)
        self._primary_edit.setObjectName("compact_path")
        self._primary_edit.setReadOnly(True)
        self._primary_edit.setPlaceholderText("Auto-filled from left panel")
        prim_row.addWidget(self._primary_edit, stretch=1)
        pg.addLayout(prim_row)

        cmp_row = QHBoxLayout()
        cmp_row.setSpacing(4)
        cmp_row.addWidget(QLabel("Compare with:", profile_grp))
        self._compare_edit = QLineEdit(profile_grp)
        self._compare_edit.setObjectName("compact_path")
        self._compare_edit.setReadOnly(True)
        self._compare_edit.setPlaceholderText("Optional — browse a second ICC/ICM")
        cmp_row.addWidget(self._compare_edit, stretch=1)
        cmp_browse = make_browse_button(profile_grp, "Browse for comparison ICC/ICM", "folder_check")
        cmp_browse.setObjectName("browse_compact")
        cmp_browse.setIconSize(QSize(14, 14))
        cmp_browse.setFixedHeight(22)
        cmp_browse.clicked.connect(self._on_browse_compare)
        cmp_row.addWidget(cmp_browse)
        cmp_clear = QPushButton("✕", profile_grp)
        cmp_clear.setObjectName("browse_compact")
        cmp_clear.setFixedWidth(28)
        cmp_clear.setFixedHeight(22)
        cmp_clear.setToolTip("Clear comparison profile")
        cmp_clear.clicked.connect(self._on_clear_compare)
        cmp_row.addWidget(cmp_clear)
        pg.addLayout(cmp_row)

        inner_layout.addWidget(profile_grp)

        # ── iccgamut Options ────────────────────────────────────────────
        opts_grp = QGroupBox("iccgamut Options", inner)
        og = QVBoxLayout(opts_grp)
        og.setContentsMargins(8, 10, 8, 8)
        og.setSpacing(8)

        intent_row = QHBoxLayout()
        intent_row.addWidget(QLabel("Rendering intent:", opts_grp))
        self._intent_combo = NoScrollComboBox(opts_grp)
        self._intent_combo.addItem("Absolute colorimetric (default)", "a")
        self._intent_combo.addItem("Relative colorimetric", "r")
        self._intent_combo.addItem("Perceptual", "p")
        self._intent_combo.addItem("Saturation", "s")
        self._intent_combo.setObjectName("compact_input")
        self._intent_combo.style().unpolish(self._intent_combo)
        self._intent_combo.style().polish(self._intent_combo)
        intent_row.addWidget(self._intent_combo, stretch=1)
        intent_row.addWidget(TooltipButton(
            "Rendering Intent",
            "Selects which ICC rendering table iccgamut uses to compute the gamut boundary.\n\n"
            "• Absolute colorimetric (default) — shows the true colorimetric gamut of the\n"
            "  device including its media white point. Best for comparing one profile against\n"
            "  another or against a reference colour space such as sRGB or AdobeRGB.\n\n"
            "• Relative colorimetric — normalises to the media white before computing the gamut.\n"
            "  Shows how much of the colour space the device covers relative to white, which\n"
            "  reflects how colours are reproduced in a standard print workflow.\n\n"
            "• Perceptual — uses the perceptual rendering table. The gamut may appear smaller or\n"
            "  differently shaped because this table compresses or re-maps colours to avoid hard\n"
            "  clipping at the gamut boundary.\n\n"
            "• Saturation — uses the saturation rendering table, which prioritises vivid colours\n"
            "  over colorimetric accuracy.\n\n"
            "For most ICC profile analysis, use Absolute colorimetric.",
            opts_grp,
            min_width=520,
        ))
        og.addLayout(intent_row)

        pcs_row = QHBoxLayout()
        pcs_row.addWidget(QLabel("Colour space:", opts_grp))
        self._pcs_combo = NoScrollComboBox(opts_grp)
        self._pcs_combo.addItem("Lab (default)", "l")
        self._pcs_combo.addItem("CIECAM02 Jab", "j")
        self._pcs_combo.setObjectName("compact_input")
        self._pcs_combo.style().unpolish(self._pcs_combo)
        self._pcs_combo.style().polish(self._pcs_combo)
        pcs_row.addWidget(self._pcs_combo, stretch=1)
        pcs_row.addWidget(TooltipButton(
            "Profile Connection Space",
            "Controls which colour space the gamut volume is computed and displayed in.\n\n"
            "• Lab (default) — CIELAB D50, the standard ICC profile connection space. The three\n"
            "  axes represent L* (lightness, 0–100), a* (green↔red), and b* (blue↔yellow).\n"
            "  Easy to interpret and the correct choice for comparing ICC profiles.\n\n"
            "• CIECAM02 Jab — uses a modern perceptual appearance model that accounts for\n"
            "  chromatic adaptation and luminance-level effects. More perceptually uniform across\n"
            "  lightness levels, but harder to interpret directly and slower to compute.\n\n"
            "For everyday profile analysis, Lab is the right choice.",
            opts_grp,
            min_width=500,
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
        self._sres_spin.setObjectName("compact_input")
        self._sres_spin.style().unpolish(self._sres_spin)
        self._sres_spin.style().polish(self._sres_spin)
        sres_row.addWidget(self._sres_spin)
        sres_row.addStretch()
        sres_row.addWidget(TooltipButton(
            "Surface Resolution",
            "Controls how densely iccgamut samples the gamut surface (range 1–50).\n\n"
            "A higher value produces a finer mesh — the 3D shape looks smoother and more\n"
            "accurate, but takes longer to compute.\n\n"
            "   5–10   fast, coarse — good for quick checks\n"
            "  15–25   balanced, suitable for most work  (default: 20)\n"
            "  30–50   very fine and detailed — slow\n\n"
            "If the gamut surface appears jagged or has visible holes, increase this value.",
            opts_grp,
            min_width=460,
        ))
        og.addLayout(sres_row)

        func_row = QHBoxLayout()
        func_row.addWidget(QLabel("Mapping:", opts_grp))
        self._function_combo = NoScrollComboBox(opts_grp)
        self._function_combo.addItem("Forward — output gamut (default)", "f")
        self._function_combo.addItem("Backward — input gamut", "b")
        self._function_combo.setObjectName("compact_input")
        self._function_combo.style().unpolish(self._function_combo)
        self._function_combo.style().polish(self._function_combo)
        func_row.addWidget(self._function_combo, stretch=1)
        func_row.addWidget(TooltipButton(
            "Mapping Direction",
            "Controls which direction iccgamut traverses the ICC profile tables.\n\n"
            "• Forward — output gamut (default): maps device values (e.g. RGB ink percentages)\n"
            "  through the profile to Lab and builds the gamut from the resulting Lab points.\n"
            "  This shows every Lab colour the device can physically reproduce.\n\n"
            "• Backward — input gamut: maps Lab values back through the profile to device values.\n"
            "  Shows which Lab colours can be addressed by the profile's lookup tables — this\n"
            "  can differ from the forward gamut due to LUT quantisation or non-invertibility.\n\n"
            "Use Forward for almost all profiling work. Backward is mainly useful for diagnosing\n"
            "profile inversion quality or gamut mapping behaviour.",
            opts_grp,
            min_width=520,
        ))
        og.addLayout(func_row)

        self._axes_cb  = QCheckBox("Show axes && white/black point", opts_grp)
        self._cusps_cb = QCheckBox("Mark cusp points", opts_grp)
        self._edges_cb = QCheckBox("Show edge plot", opts_grp)
        self._axes_cb.setChecked(True)

        _cb_rows = [
            (self._axes_cb, TooltipButton(
                "Lab Axes & Reference Points",
                "Draws the CIELAB coordinate axes and two reference points inside the 3D viewer.\n\n"
                "The axes show the direction of each colour dimension:\n"
                "  • L* axis (vertical) — lightness, from black at the bottom to white at the top\n"
                "  • a* axis — green (−a*) toward red/magenta (+a*)\n"
                "  • b* axis — blue/violet (−b*) toward yellow/amber (+b*)\n\n"
                "Two small spheres mark the white point (lightest reproducible colour) and the\n"
                "black point (darkest reproducible colour) of the profile.\n\n"
                "Keeping this enabled makes it much easier to read and orient the 3D gamut model.",
                opts_grp,
                min_width=460,
            )),
            (self._cusps_cb, TooltipButton(
                "Mark Cusp Points",
                "Marks the primary and secondary colour cusp points on the gamut surface\n"
                "(iccgamut -k flag).\n\n"
                "Cusps are the most saturated colours in each of the six main hue directions:\n"
                "red, yellow, green, cyan, blue, and magenta. On a printer profile these represent\n"
                "the extremes of what the ink set can achieve.\n\n"
                "Marking them helps you:\n"
                "  • See at a glance how far the gamut extends in each hue direction\n"
                "  • Compare the saturation envelope of one profile against another\n"
                "  • Spot compression or irregularities in specific hue regions",
                opts_grp,
                min_width=460,
            )),
            (self._edges_cb, TooltipButton(
                "Show Edge Plot",
                "Overlays the triangle edges of the gamut mesh on the 3D model\n"
                "(iccgamut -e flag).\n\n"
                "Instead of only showing the solid shaded surface, this draws lines along every\n"
                "triangle edge, giving a wireframe-like appearance that can reveal:\n"
                "  • The resolution and density of the underlying mesh\n"
                "  • Sharp corners or unusual topology in the gamut shape\n"
                "  • Areas where the boundary has been smoothed or interpolated\n\n"
                "Most useful at higher surface resolution values where the mesh structure\n"
                "is meaningful.",
                opts_grp,
                min_width=460,
            )),
        ]
        for cb, tip in _cb_rows:
            _row = QHBoxLayout()
            _row.addWidget(cb)
            _row.addStretch()
            _row.addWidget(tip)
            og.addLayout(_row)

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
        # The QWebEngineView's Chromium surface paints over the widget's own
        # stylesheet border, so the border must live on a wrapper QWidget. The
        # wrapper draws the border via an objectName-scoped stylesheet and uses
        # contentsMargins to inset the view by the border thickness on the
        # three bordered sides (top/right/bottom).
        container = QWidget(self)
        container.setObjectName("gamutViewerFrame")
        container.setStyleSheet(
            "QWidget#gamutViewerFrame {"
            " background: #111111;"
            " border: 1px solid #333;"
            " border-left: none;"
            "}"
        )
        wrap_layout = QVBoxLayout(container)
        wrap_layout.setContentsMargins(0, 1, 1, 1)
        wrap_layout.setSpacing(0)
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView(container)
            # Set the Chromium page surface colour BEFORE any content loads —
            # otherwise the first compositor frame on first tab open paints at
            # the default white, flashing through before the placeholder HTML
            # renders.
            view.page().setBackgroundColor(QColor("#111111"))
            try:
                from PyQt6.QtWebEngineCore import QWebEngineSettings
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
                )
            except (ImportError, AttributeError):
                pass
            view.loadFinished.connect(self._on_page_loaded)
            self._web_view = view
            wrap_layout.addWidget(view)
            self._show_placeholder()
            return container
        except ImportError:
            log.warning("PyQt6-WebEngine not available — using fallback placeholder")
            self._web_view = None
            lbl = QLabel(
                "Install PyQt6-WebEngine to view\nthe interactive 3D gamut",
                container,
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {TEXT_DIM}; background: #111111;"
                " font-family: Menlo, Consolas, 'Courier New', monospace; font-size: 10px;"
            )
            wrap_layout.addWidget(lbl)
            return container

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
        self._compare_controls.setVisible(False)
        self._load_html(self._primary_html or "")

    def _on_view_combined(self) -> None:
        self._set_toggle_checked(self._view_combined_btn)
        self._compare_controls.setVisible(True)
        self._load_html(self._combined_html or "")

    def _on_view_compare(self) -> None:
        self._set_toggle_checked(self._view_compare_btn)
        self._compare_controls.setVisible(False)
        self._load_html(self._compare_html or "")

    def _on_page_loaded(self, ok: bool) -> None:
        if not ok or self._web_view is None or not self._view_combined_btn.isChecked():
            return
        t = 1.0 - self._opacity_slider.value() / 100.0
        s = self._sat_slider.value() / 100.0
        self._web_view.page().runJavaScript(
            f"window._chromiqCompareOpacity={t:.2f};"
            f" window._chromiqCompareSat={s:.2f};"
        )

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity_label.setText(f"{value}%")
        if self._web_view is None or not self._view_combined_btn.isChecked():
            return
        t = 1.0 - value / 100.0
        self._web_view.page().runJavaScript(
            f"window._chromiqCompareOpacity={t:.2f};"
            " if(window._chromiqApplyCompare) window._chromiqApplyCompare();"
        )

    def _on_saturation_changed(self, value: int) -> None:
        self._sat_label.setText(f"{value}%")
        if self._web_view is None or not self._view_combined_btn.isChecked():
            return
        s = value / 100.0
        self._web_view.page().runJavaScript(
            f"window._chromiqCompareSat={s:.2f};"
            " if(window._chromiqApplyCompare) window._chromiqApplyCompare();"
        )

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
        params  = self._collect_params(self._icc_path)
        themed  = bool(self._settings.get("gamut_themed_colors", True))
        self._viewer.run(params, on_line=lambda _: None, on_finish=lambda _: None, themed=themed)

    def _run_compare(self) -> None:
        if self._compare_path is None:
            return
        themed = bool(self._settings.get("gamut_themed_colors", True))
        params = self._collect_params(self._compare_path)
        sub = GamutViewer(self._runner, self)
        sub.finished.connect(self._on_compare_finished)
        sub.error.connect(self._on_compare_error)
        sub.run(params, on_line=lambda _: None, on_finish=lambda _: None, themed=themed)

    def _run_viewgam(self) -> None:
        if not self._primary_gam or not self._compare_gam:
            return
        themed = bool(self._settings.get("gamut_themed_colors", True))
        self._viewgam_runner.run(
            primary_gam  = Path(self._primary_gam),
            compare_gam  = Path(self._compare_gam),
            primary_html = Path(self._primary_html) if self._primary_html else None,
            compare_html = Path(self._compare_html) if self._compare_html else None,
            on_line      = lambda _: None,
            on_finish    = lambda _: None,
            themed       = themed,
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
            self._compare_controls.setVisible(True)
            self._set_toggle_checked(self._view_combined_btn)
        self._run_btn.setEnabled(self._icc_path is not None)

    def _on_viewgam_error(self, msg: str) -> None:
        log.warning("viewgam: %s", msg)
        self._run_btn.setEnabled(self._icc_path is not None)

    def _on_compare_error(self, msg: str) -> None:
        log.warning("compare iccgamut: %s", msg)
        self._run_btn.setEnabled(self._icc_path is not None)
        self._show_gamut_error_dialog(msg)

    def _on_viewer_error(self, msg: str) -> None:
        self._run_btn.setEnabled(self._icc_path is not None)
        self._pending_compare = False
        log.warning("GamutViewer error: %s", msg)
        self._show_gamut_error_dialog(msg)

    def _show_gamut_error_dialog(self, msg: str) -> None:
        if msg.startswith("empty:"):
            path = msg[len("empty:"):]
            body = (
                f"The ICC profile file could not be analysed because it is empty (0 bytes):\n\n"
                f"{path}\n\n"
                "Why this happens:\n"
                "An empty ICC profile is usually left behind when a profiling run was\n"
                "interrupted, aborted, or failed before colprof could finish writing the\n"
                "output file. The file exists on disk but contains no data.\n\n"
                "What to do:\n"
                "• Go to the Build Profile tab and run the profiling workflow again for\n"
                "  this printer/paper combination to generate a valid ICC profile.\n"
                "• If the file was created by a different application, re-export or\n"
                "  re-create it from your profiling software."
            )
            InfoDialog("Gamut Analysis Failed — Empty Profile File", body, self, min_width=520).exec()
        elif msg.startswith("tool_error:"):
            details = msg[len("tool_error:"):]
            body = (
                "iccgamut was not able to analyse the ICC profile and exited with an error.\n\n"
                f"Technical detail:\n{details}\n\n"
                "Common causes:\n"
                "• The ICC profile file is corrupt or was not written correctly.\n"
                "• The profile was created by an application using a non-standard ICC\n"
                "  structure that this version of Argyll cannot parse.\n"
                "• The file was modified or truncated after it was created.\n\n"
                "What to do:\n"
                "• Try rebuilding the profile via the Build Profile tab.\n"
                "• If the file came from a different application, check whether it opens\n"
                "  correctly in ColorSync Utility (macOS) or ICC Profile Inspector.\n"
                "• Check the log file at ~/Library/Logs/ChromIQ/chromiq.log for the\n"
                "  full iccgamut output."
            )
            InfoDialog("Gamut Analysis Failed", body, self, min_width=540).exec()
        elif "Another process is already running" not in msg:
            InfoDialog(
                "Gamut Analysis Failed",
                f"The gamut analysis could not complete:\n\n{msg}",
                self,
                min_width=480,
            ).exec()

    def shutdown_webengine(self) -> None:
        # PyQt6 + QtWebEngine shutdown race: if the view (and its Chromium
        # child objects) is still alive when QApplication enters its
        # destructor, SIP walks the wrapper graph and follows a dangling
        # pointer in the Chromium subtree — EXC_BAD_ACCESS inside
        # sip_api_visit_wrappers/dealloc_QApplication. Called from
        # MainWindow.closeEvent so the main event loop is still running and
        # the deleteLater/processEvents drain below actually fires before SIP
        # tears down QApplication. (aboutToQuit is too late — events posted
        # from that slot are not reliably delivered.)
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
        _loop = QEventLoop()
        QTimer.singleShot(200, _loop.quit)
        _loop.exec()

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

        # Actually run the deferred deletes before QApplication destructs.
        app = QApplication.instance()
        if app is not None:
            for _ in range(3):
                app.processEvents()

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
        self._opacity_slider.setValue(int(s.get("gamut_compare_opacity", 50)))
        self._sat_slider.setValue(int(s.get("gamut_compare_sat", 100)))

    def _on_save_defaults(self) -> None:
        s = self._settings
        s.set("gamut_intent",    self._intent_combo.currentData())
        s.set("gamut_pcs",       self._pcs_combo.currentData())
        s.set("gamut_function",  self._function_combo.currentData())
        s.set("gamut_sres",      self._sres_spin.value())
        s.set("gamut_axes",      self._axes_cb.isChecked())
        s.set("gamut_cusps",     self._cusps_cb.isChecked())
        s.set("gamut_edges",     self._edges_cb.isChecked())
        s.set("gamut_compare_opacity", self._opacity_slider.value())
        s.set("gamut_compare_sat",     self._sat_slider.value())


# ---------------------------------------------------------------------------

def _set_combo(combo: NoScrollComboBox, value: str) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def _system_icc_paths() -> list[str]:
    """Return existing platform-specific ICC/ICM profile directories."""
    from core.platform_paths import icc_system_dirs
    seen: set[str] = set()
    result: list[str] = []
    for p in (str(d) for d in icc_system_dirs()):
        if p not in seen and Path(p).exists():
            seen.add(p)
            result.append(p)
    return result
