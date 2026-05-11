"""ChromIQ masthead — full-width header with embedded settings button.

The settings button is positioned top-right as an absolute child so the
header fills 100 % of the window width with no gap. Emits ``settings_clicked``
when the gear button is pressed.
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QGuiApplication, QIcon,
    QPainter, QPen, QPaintEvent, QPixmap,
)
from PyQt6.QtWidgets import QToolButton, QWidget

from core.resource_path import resource_path

_STOPS = (
    "#ff4573",  # Create Chart
    "#ffb42d",  # Print Chart
    "#56d6a5",  # Measure
    "#37bcd6",  # Build Profile
    "#9f82ff",  # Check & Refine
)

_BG         = "#0a0a0a"
_VER_BG     = "#070707"
_VER_FG     = "#6a6a6a"
_TAG_FG     = "#4a4a4a"
_WHITE      = "#ffffff"
_ACCENT     = "#ff4573"  # tab-1 tint for "IQ"


class MastheadHeader(QWidget):
    """Custom header: spectrum stripe + centred Chrom/IQ wordmark + version rail
    + embedded settings button (no layout gap to the right)."""

    settings_clicked = pyqtSignal()

    STRIPE_H  = 6
    VERSION_H = 22

    def __init__(
        self,
        parent: QWidget | None = None,
        version: str = "",
    ) -> None:
        super().__init__(parent)
        self._version = version
        self.setFixedHeight(110)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        # ---- Embedded settings button (absolute child) ----
        self._btn = QToolButton(self)
        self._btn.setObjectName("tooltip_btn")
        self._btn.setToolTip("Preferences")
        self._btn.setFixedSize(QSize(44, 44))
        self._btn.clicked.connect(self.settings_clicked)
        self._load_settings_icon()

    # ------------------------------------------------------------------
    def setVersion(self, v: str) -> None:
        if v != self._version:
            self._version = v
            self.update()

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Keep settings button pinned to top-right, vertically centred in body
        bw, bh = self._btn.width(), self._btn.height()
        body_top  = self.STRIPE_H
        body_bot  = self.height() - self.VERSION_H
        btn_y = body_top + (body_bot - body_top - bh) // 2
        self._btn.move(self.width() - bw - 12, btn_y)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(900, 110)

    # ------------------------------------------------------------------
    def paintEvent(self, _ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w = self.width()
        h = self.height()

        # Background
        p.fillRect(self.rect(), QColor(_BG))

        # Spectrum stripe
        n = len(_STOPS)
        for i, col in enumerate(_STOPS):
            x0 = int(round(i * w / n))
            x1 = int(round((i + 1) * w / n)) if i < n - 1 else w
            p.fillRect(x0, 0, x1 - x0, self.STRIPE_H, QColor(col))

        # Version rail
        ver_y = h - self.VERSION_H
        p.fillRect(0, ver_y, w, self.VERSION_H, QColor(_VER_BG))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawLine(0, ver_y, w, ver_y)

        mono = QFont()
        mono.setFamilies(["JetBrains Mono", "Menlo", "SF Mono", "Courier New", "monospace"])
        mono.setPixelSize(9)

        if self._version:
            mono_lc = QFont(mono)
            mono_lc.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 115)
            p.setFont(mono_lc)
            fm = QFontMetricsF(mono_lc)
            ver_text = f"v{self._version}"
            ver_tw = fm.horizontalAdvance(ver_text)
            base = int(ver_y + (self.VERSION_H + fm.ascent() - fm.descent()) / 2)
            p.setPen(QColor(_VER_FG))
            p.drawText(int(w - ver_tw - 18), base, ver_text)

            # Left tag
            tag_font = QFont(mono)
            tag_font.setPixelSize(9)
            tag_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 148)
            p.setFont(tag_font)
            p.setPen(QColor(_TAG_FG))
            p.drawText(18, base, "PRINTER PROFILING")

        # ---- Centred wordmark ----
        body_cy = (self.STRIPE_H + ver_y) / 2

        # "Chrom" — regular weight Instrument Serif
        font_r = QFont()
        font_r.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
        font_r.setPixelSize(62)
        font_r.setWeight(QFont.Weight.Normal)
        font_r.setItalic(False)

        # "IQ" — bold italic Instrument Serif
        font_i = QFont()
        font_i.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
        font_i.setPixelSize(62)
        font_i.setWeight(QFont.Weight.Bold)
        font_i.setItalic(True)

        fm_r = QFontMetricsF(font_r)
        fm_i = QFontMetricsF(font_i)
        chrom_w = fm_r.horizontalAdvance("Chrom")
        iq_w    = fm_i.horizontalAdvance("IQ")
        total_w = chrom_w + iq_w - 1   # -1 optical kern

        x_start  = (w - total_w) / 2
        baseline = body_cy + (fm_r.ascent() - fm_r.descent()) / 2 + 8

        p.setFont(font_r)
        p.setPen(QColor(_WHITE))
        p.drawText(int(x_start), int(baseline), "Chrom")

        p.setFont(font_i)
        p.setPen(QColor(_ACCENT))
        p.drawText(int(x_start + chrom_w - 1), int(baseline), "IQ")

        p.end()

    # ------------------------------------------------------------------
    def _load_settings_icon(self) -> None:
        """Load settings_v2.png if it exists, fall back to sliders drawn in code."""
        px = QPixmap(str(resource_path("assets/settings_v2.png")))
        if not px.isNull():
            dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
            phys = round(26 * dpr)
            scaled = px.scaled(
                phys, phys,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._btn.setIcon(QIcon(scaled))
            self._btn.setIconSize(QSize(26, 26))
        else:
            # Fallback: draw sliders icon programmatically
            self._btn.setIcon(self._draw_sliders_icon())
            self._btn.setIconSize(QSize(26, 26))

    def _draw_sliders_icon(self) -> QIcon:
        dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(26 * dpr)
        px   = QPixmap(phys, phys)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_cols = ["#ff4573", "#37bcd6", "#ffb42d"]
        knob_x     = [0.65, 0.30, 0.50]
        for i, (col, kx) in enumerate(zip(track_cols, knob_x)):
            y = int(phys * (0.28 + i * 0.22))
            # Track
            p.setPen(QPen(QColor("#555555"), max(1, int(phys * 0.07)),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(phys * 0.12), y, int(phys * 0.88), y)
            # Knob
            hx = int(phys * kx)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            r = max(2, int(phys * 0.13))
            p.drawEllipse(hx - r, y - r, r * 2, r * 2)
        p.end()
        px.setDevicePixelRatio(dpr)
        return QIcon(px)
