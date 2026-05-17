"""Multi-page TIFF preview widget with stripe highlight overlay."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger

log = get_logger(__name__)

_REFRESH_DELAY_MS = 80   # debounce repaint
_BORDER = 15             # white display border: all sides (px)

# QToolTip block appended to per-widget stylesheets so the tooltip popup
# uses the active theme even when the source label has its own QSS — on
# macOS the global QToolTip rule fails to reach tooltips originating from
# widgets with `background: transparent` or per-side border overrides.
_TOOLTIP_QSS_DARK = (
    "QToolTip { "
    "background-color: #262626; "
    "color: #e6e6e6; "
    "border: 1px solid #404040; "
    "padding: 4px;"
    " }"
)
_TOOLTIP_QSS_LIGHT = (
    "QToolTip { "
    "background-color: #ffffff; "
    "color: #22211f; "
    "border: 1px solid #d0ccc6; "
    "padding: 4px;"
    " }"
)
# Back-compat alias: existing dark stylesheet strings concatenate this.
_TOOLTIP_QSS = _TOOLTIP_QSS_DARK

# ---------------------------------------------------------------------------
# Ink channel tables
# ---------------------------------------------------------------------------

# ink code → (R_absorption, G_absorption, B_absorption) per unit ink value (0–1)
# Values calibrated against US Web Coated SWOP v2 ICC profile full-ink response.
# Used ONLY for extra channels (index 4+) that sit on top of the ICC-converted
# CMYK base. The c/m/y/k entries are retained as fallback for edge cases.
_INK_ABSORPTION: dict[str, tuple[float, float, float]] = {
    # CMYK primaries (calibrated from SWOP full-ink response on white paper)
    "c":   (1.00, 0.32, 0.06),   # Cyan:    R=0, G=174, B=239 at full ink on white
    "m":   (0.08, 1.00, 0.45),   # Magenta: R=236, G=0, B=140
    "y":   (0.00, 0.05, 1.00),   # Yellow:  R=255, G=242, B=0
    "k":   (0.86, 0.88, 0.87),   # Black:   R=35, G=31, B=32
    # Light inks ≈ parent at ~50% (extrapolated from SWOP midtone data)
    "lc":  (0.57, 0.18, 0.04),
    "lm":  (0.04, 0.57, 0.25),
    "ly":  (0.00, 0.03, 0.55),
    "lk":  (0.42, 0.42, 0.42),
    "llk": (0.22, 0.22, 0.22),
    # Medium inks ≈ parent at ~75%
    "mc":  (0.80, 0.25, 0.05),
    "mm":  (0.06, 0.80, 0.35),
    "my":  (0.00, 0.04, 0.78),
    "mk":  (0.65, 0.65, 0.65),
    # Spot / extended-gamut inks
    "o":   (0.02, 0.40, 0.95),   # Orange:      absorbs blue + some green
    "r":   (0.05, 0.90, 0.80),   # Red:         absorbs green + blue
    "g":   (0.90, 0.05, 0.80),   # Green:       absorbs red + blue
    "b":   (0.82, 0.68, 0.05),   # Blue/Violet: absorbs red + most green
    "v":   (0.76, 0.62, 0.04),   # Violet
    "w":   (0.00, 0.00, 0.00),   # White
}

# targen -d<N> → ordered ink codes (source: data/parameters.yaml device_type labels)
_D_TYPE_CHANNELS: dict[int, list[str]] = {
    0:  ["k"],
    1:  ["k"],
    2:  ["r", "g", "b"],
    3:  ["r", "g", "b"],
    4:  ["c", "m", "y", "k"],
    5:  ["c", "m", "y"],
    6:  ["c", "m", "y", "k", "lc", "lm"],
    7:  ["c", "m", "y", "k", "lc", "lm", "lk"],
    8:  ["c", "m", "y", "k", "r", "b"],
    9:  ["c", "m", "y", "k", "o", "g"],
    10: ["c", "m", "y", "k", "r", "g", "b"],
    11: ["c", "m", "y", "k", "o", "g", "v"],
    12: ["c", "m", "y", "k", "o", "g", "b"],
    13: ["c", "m", "y", "k", "lc", "lm", "lk", "llk"],
    14: ["c", "m", "y", "k", "o", "g", "lc", "lm"],
    15: ["c", "m", "y", "k", "lc", "lm", "mc", "mm"],
}

# targen -D <N> → ink code
_D_FLAG_CODE: dict[int, str] = {
    1: "c", 2: "m", 3: "y", 4: "k",
    5: "o", 6: "r", 7: "g", 8: "b", 9: "v", 10: "w",
    11: "lc", 12: "lm", 13: "ly", 14: "lk",
    15: "mc", 16: "mm", 17: "my", 18: "mk",
    19: "llk",
}

# Channel count → most common ink layout (fallback when sidecar is absent)
_N_CHANNELS_FALLBACK: dict[int, list[str]] = {
    1: ["k"],
    3: ["r", "g", "b"],
    4: ["c", "m", "y", "k"],
    6: ["c", "m", "y", "k", "lc", "lm"],
    7: ["c", "m", "y", "k", "lc", "lm", "lk"],
    8: ["c", "m", "y", "k", "lc", "lm", "lk", "llk"],
    9: ["c", "m", "y", "k", "o", "g", "lc", "lm", "lk"],
    10: ["c", "m", "y", "k", "o", "g", "lc", "lm", "lk", "llk"],
    11: ["c", "m", "y", "k", "o", "g", "v", "lc", "lm", "lk", "llk"],
}

# ---------------------------------------------------------------------------
# ICC transform cache
# ---------------------------------------------------------------------------

_cmyk_icc_transform: object = None  # None = not tried; False = unavailable; transform = ready


def _get_cmyk_transform():
    """Return a cached PIL.ImageCms CMYK→sRGB transform, or None if unavailable."""
    global _cmyk_icc_transform
    if _cmyk_icc_transform is not None:
        return _cmyk_icc_transform if _cmyk_icc_transform is not False else None
    from PIL import ImageCms
    from core.resource_path import resource_path
    import sys as _sys
    if _sys.platform == "win32":
        import os as _os
        _windir = Path(_os.environ.get("WINDIR", r"C:\Windows"))
        _extra = [
            _windir / "System32" / "spool" / "drivers" / "color" / "USWebCoatedSWOP.icc",
            Path(r"C:\Program Files\Common Files\Adobe\Color\Profiles\USWebCoatedSWOP.icc"),
        ]
    else:
        _extra = [
            Path("/Library/Application Support/Adobe/Color/Profiles/Recommended/USWebCoatedSWOP.icc"),
            Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"),
        ]
    candidates = [resource_path("assets/USWebCoatedSWOP.icc")] + _extra
    for p in candidates:
        if p.exists():
            try:
                src = ImageCms.getOpenProfile(str(p))
                dst = ImageCms.createProfile("sRGB")
                t = ImageCms.buildTransformFromOpenProfiles(
                    src, dst, "CMYK", "RGB",
                    renderingIntent=ImageCms.Intent.PERCEPTUAL,
                )
                _cmyk_icc_transform = t
                log.debug("ICC CMYK transform loaded from %s", p.name)
                return t
            except Exception:
                continue
    _cmyk_icc_transform = False
    log.debug("No CMYK ICC profile found; will use naive subtractive fallback")
    return None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def resolve_ink_channels(device_type: str, extra_targen_args: str = "") -> list[str]:
    """Return ordered ink codes for a targen -d / -D configuration.

    device_type:       ChartParams.device_type string (e.g. "6")
    extra_targen_args: ChartParams.extra_targen_args (may contain -D N)
    """
    try:
        base = list(_D_TYPE_CHANNELS.get(int(device_type), ["c", "m", "y", "k"]))
    except ValueError:
        return ["c", "m", "y", "k"]
    try:
        tokens = shlex.split(extra_targen_args)
    except ValueError:
        return base
    i = 0
    while i < len(tokens):
        m = re.match(r"^-D(\d+)?$", tokens[i])
        if m:
            raw = m.group(1)
            if raw is None and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i += 1
                raw = tokens[i]
            if raw is not None:
                code = _D_FLAG_CODE.get(int(raw))
                if code:
                    if code in base:
                        base.remove(code)
                    else:
                        base.append(code)
        i += 1
    return base


def _find_sidecar_channels(path: Path) -> list[str] | None:
    """Return ink channels from a ChromIQ sidecar file next to path, or None."""
    stem = path.stem
    while stem and (stem[-1].isdigit() or stem[-1] in "_- "):
        stem = stem[:-1]
    candidates = [path.parent / f"{path.stem}.channels.json"]
    if stem:
        candidates.append(path.parent / f"{stem}.channels.json")
    for candidate in candidates:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text())
                channels = data.get("ink_channels")
                if isinstance(channels, list):
                    return channels
            except Exception:
                pass
    return None


def load_tiff_as_rgb(
    path: Path, frame: int = 0, ink_channels: list[str] | None = None
) -> Image.Image:
    """Load any TIFF frame as RGB PIL Image, handling multi-channel Separated TIFFs."""
    return TiffPreview._load_frame(path, frame, ink_channels)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class TiffPreview(QWidget):
    """Displays multi-page TIFF files with optional stripe highlight overlay."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages: list[tuple[Path, int]] = []   # (file_path, frame_index)
        self._current: int = 0
        self._active_stripe: int = -1
        self._bidirectional: bool = False
        self._stripe_rects: list[QRect] = []
        self._pixmap: QPixmap | None = None
        self._ink_channels: list[str] | None = None
        self._mode: str = "dark"
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(_REFRESH_DELAY_MS)
        self._refresh_timer.timeout.connect(self._update_display)

        self._build_ui()

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        """Switch between dark and light visuals (called by MainWindow.apply_theme)."""
        new_mode = "light" if mode == "light" else "dark"
        if new_mode == self._mode:
            return
        self._mode = new_mode
        self._apply_mode_styles()

    def _apply_mode_styles(self) -> None:
        if self._mode == "light":
            tooltip = _TOOLTIP_QSS_LIGHT
            caption_color  = "#7a7570"
            filename_color = "#7a7570"
            page_color     = "#7a7570"
            img_bg         = "#efebe6"
            img_border     = "#d0ccc6"
            img_text       = "#a8a4a0"
        else:
            tooltip = _TOOLTIP_QSS_DARK
            caption_color  = "#808080"
            filename_color = "#b8b8b8"
            page_color     = "#909090"
            img_bg         = "#111111"
            img_border     = "#333"
            img_text       = "#606060"
        self._caption_lbl.setStyleSheet(
            f"QLabel {{ color: {caption_color}; background: transparent; padding: 4px;"
            " font-family: Menlo; font-size: 9px; font-weight: 300; }"
            + tooltip
        )
        self._filename_lbl.setStyleSheet(
            f"QLabel {{ color: {filename_color}; background: transparent; padding: 0 8px 0 8px;"
            " font-family: Menlo; font-size: 11px; }"
            + tooltip
        )
        self._img_label.setStyleSheet(
            f"QLabel {{ background: {img_bg};"
            f" border: 1px solid {img_border};"
            " border-left: none;"
            f" color: {img_text};"
            " font-family: 'Menlo'; }"
            + tooltip
        )
        self._page_label.setStyleSheet(f"color: {page_color}; font-size: 12px;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_tiff(self, paths: list[Path], ink_channels: list[str] | None = None) -> None:
        """Load a list of TIFF pages (one entry per printable page)."""
        self._ink_channels = ink_channels
        self._pages = []
        for p in paths:
            n = self._count_frames(p)
            if n == 0:
                log.warning("Cannot open TIFF %s", p)
                continue
            for i in range(n):
                self._pages.append((p, i))

        if not self._pages and paths:
            log.warning("TiffPreview: received %d path(s) but none could be opened: %s",
                        len(paths), [str(p) for p in paths])

        self._current = 0
        self._active_stripe = -1
        self._stripe_rects = []
        self._update_nav()
        self._update_filename_label(paths)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._update_display)
        self._schedule_refresh()
        log.debug("TiffPreview: loaded %d page(s)", len(self._pages))

    def set_caption(self, text: str) -> None:
        """Set the small ALL-CAPS caption above the preview (e.g. 'PRINT PREVIEW')."""
        self._caption_lbl.setText(text)
        self._caption_lbl.setVisible(bool(text))

    def _update_filename_label(self, paths: list[Path]) -> None:
        """Show the chart stem of the loaded files; full path on hover.

        Toggles the header→image spacer and broadcasts the tooltip to the
        caption, filename, and image labels so users find the hover info
        wherever they happen to point at.
        """
        if not paths:
            self._filename_lbl.clear()
            self._filename_lbl.setVisible(False)
            self._header_image_gap.setVisible(False)
            for w in (self._caption_lbl, self._filename_lbl, self._img_label):
                w.setToolTip("")
                w.unsetCursor()
            return
        first = paths[0]
        stem = re.sub(r"_(\d{1,3})$", "", first.stem)
        self._filename_lbl.setText(self._elide_middle(stem, 60))
        try:
            folder = first.resolve().parent
        except Exception:
            folder = first.parent
        tooltip = "\n".join([f"Folder: {folder}", "", *(p.name for p in paths)])
        for w in (self._caption_lbl, self._filename_lbl, self._img_label):
            w.setToolTip(tooltip)
        # Help cursor on the header text only — image gets a tooltip but keeps
        # its arrow cursor so it doesn't suggest the dark area itself is clickable.
        self._caption_lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._filename_lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._filename_lbl.setVisible(True)
        self._header_image_gap.setVisible(True)

    @staticmethod
    def _elide_middle(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        keep = max_len - 1
        head = keep // 2
        tail = keep - head
        return f"{text[:head]}…{text[-tail:]}"

    def highlight_stripe(self, stripe_index: int) -> None:
        """Highlight a strip (0-based) in the current preview page."""
        self._active_stripe = stripe_index
        self._schedule_refresh()

    def set_bidirectional(self, enabled: bool) -> None:
        """Toggle the second (bottom, upward-pointing) strip arrow."""
        if self._bidirectional == enabled:
            return
        self._bidirectional = enabled
        self._schedule_refresh()

    def set_stripe_rects(self, rects: list[QRect]) -> None:
        """Provide precomputed pixel rects for each stripe on current page."""
        self._stripe_rects = rects

    def show_page(self, index: int) -> None:
        """Switch to page by index and repaint."""
        if 0 <= index < len(self._pages) and index != self._current:
            self._current = index
            self._active_stripe = -1
            self._update_nav()
            self._schedule_refresh()

    def clear(self) -> None:
        self._pages = []
        self._current = 0
        self._active_stripe = -1
        self._bidirectional = False
        self._stripe_rects = []
        self._pixmap = None
        self._ink_channels = None
        self._img_label.setText("No preview")
        self._update_nav()
        self._update_filename_label([])

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header: caption (set by parent tab) + active filename (auto-updated).
        # Spacers below are explicit so the header→image gap only appears when
        # a filename is showing — caption alone hugs the image like the
        # pre-load layout did.
        header = QWidget(self)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        self._caption_lbl = QLabel("", header)
        self._caption_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption_lbl.setStyleSheet(
            "QLabel { color: #808080; background: transparent; padding: 4px;"
            " font-family: Menlo; font-size: 9px; font-weight: 300; }"
            + _TOOLTIP_QSS
        )
        self._caption_lbl.setVisible(False)
        hl.addWidget(self._caption_lbl)

        self._filename_lbl = QLabel("", header)
        self._filename_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filename_lbl.setStyleSheet(
            "QLabel { color: #b8b8b8; background: transparent; padding: 0 8px 0 8px;"
            " font-family: Menlo; font-size: 11px; }"
            + _TOOLTIP_QSS
        )
        self._filename_lbl.setVisible(False)
        hl.addWidget(self._filename_lbl)

        layout.addWidget(header)

        # Gap between header and image — only shown when a filename is present
        self._header_image_gap = QWidget(self)
        self._header_image_gap.setFixedHeight(12)
        self._header_image_gap.setVisible(False)
        layout.addWidget(self._header_image_gap)

        # Image label
        self._img_label = QLabel("No preview", self)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._img_label.setStyleSheet(
            "QLabel { background: #111111;"
            " border: 1px solid #333;"
            " border-left: none;"
            " color: #606060;"
            " font-family: 'Menlo'; }"
            + _TOOLTIP_QSS
        )
        _lbl_font = self._img_label.font()
        _lbl_font.setCapitalization(QFont.Capitalization.AllUppercase)
        self._img_label.setFont(_lbl_font)
        self._img_label.setMinimumSize(200, 200)
        layout.addWidget(self._img_label, stretch=1)

        # Gap between image and nav (replaces the old setSpacing(12))
        image_nav_gap = QWidget(self)
        image_nav_gap.setFixedHeight(12)
        layout.addWidget(image_nav_gap)

        # Navigation bar
        nav = QWidget(self)
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(12, 0, 12, 0)

        self._prev_btn = QPushButton("‹ Prev", nav)
        self._prev_btn.setFixedWidth(84)
        self._prev_btn.clicked.connect(self._go_prev)
        nav_layout.addWidget(self._prev_btn)

        nav_layout.addStretch()
        self._page_label = QLabel("", nav)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet("color: #909090; font-size: 12px;")
        nav_layout.addWidget(self._page_label)
        nav_layout.addStretch()

        self._next_btn = QPushButton("Next ›", nav)
        self._next_btn.setFixedWidth(84)
        self._next_btn.clicked.connect(self._go_next)
        nav_layout.addWidget(self._next_btn)

        layout.addWidget(nav)

        # Replace the inline dark styles set above with mode-aware versions.
        # No-op for dark (values match); swaps to light palette when active.
        self._apply_mode_styles()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_prev(self) -> None:
        if self._current > 0:
            self._current -= 1
            self._active_stripe = -1
            self._update_nav()
            self._schedule_refresh()

    def _go_next(self) -> None:
        if self._current < len(self._pages) - 1:
            self._current += 1
            self._active_stripe = -1
            self._update_nav()
            self._schedule_refresh()

    def _update_nav(self) -> None:
        n = len(self._pages)
        visible = n > 1
        self._prev_btn.setVisible(visible)
        self._next_btn.setVisible(visible)
        self._page_label.setVisible(n > 0)
        if n > 0:
            self._page_label.setText(f"Page {self._current + 1} / {n}")
        else:
            self._page_label.setText("")
        self._prev_btn.setEnabled(self._current > 0)
        self._next_btn.setEnabled(self._current < n - 1)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def _update_display(self) -> None:
        if not self._pages:
            self._img_label.setText("No preview")
            self._pixmap = None
            return

        path, frame = self._pages[self._current]
        try:
            img = self._load_frame(path, frame, self._ink_channels)
            self._pixmap = self._pil_to_pixmap(img)
        except Exception as exc:
            log.warning("Preview render error: %s", exc)
            self._img_label.setText(f"Preview error:\n{exc}")
            return

        self._repaint_label()

    def _repaint_label(self) -> None:
        if not self._pixmap:
            return
        B = _BORDER
        dpr = self._img_label.devicePixelRatioF()
        label_size = self._img_label.size()  # logical pixels

        # Scale to device pixels so the preview is sharp on HiDPI/Retina displays
        avail = QSize(
            max(1, int((label_size.width()  - 2 * B) * dpr)),
            max(1, int((label_size.height() - 2 * B) * dpr)),
        )
        scaled = self._pixmap.scaled(
            avail,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)

        # Canvas at device pixel dimensions; DPR tells Qt the logical display size
        canvas = QPixmap(scaled.width() + int(2 * B * dpr),
                         scaled.height() + int(2 * B * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.GlobalColor.white)

        # Painter coordinates are logical (canvas has DPR set)
        painter = QPainter(canvas)
        painter.drawPixmap(B, B, scaled)

        if self._active_stripe >= 0 and self._stripe_rects:
            # sx/sy: device pixels per original image pixel
            sx = scaled.width()  / self._pixmap.width()
            sy = scaled.height() / self._pixmap.height()

            if self._active_stripe < len(self._stripe_rects):
                r   = self._stripe_rects[self._active_stripe]
                # Convert device-pixel coords → logical coords for painter
                x   = r.x()     * sx / dpr + B
                y   = r.y()     * sy / dpr + B + 3
                rw  = max(1.0, r.width() * sx / dpr + 2.0 / dpr)
                cx  = x + rw / 2
                arrow_h = 20  # logical pixels — constant visual size on all displays
                path = QPainterPath()
                path.moveTo(cx - rw / 2, y)
                path.lineTo(cx + rw / 2, y)
                path.lineTo(cx, y + arrow_h)
                path.closeSubpath()
                painter.fillPath(path, QColor("#56d6a5"))

                # Bidirectional reading: mirror a second arrow near the
                # chart's bottom edge (not the strip's own bottom — that
                # overlaps patches on multi-strip layouts). Horizontal
                # extent still tracks the active strip so the two arrows
                # stay visually paired.
                if self._bidirectional:
                    chart_bottom = scaled.height() / dpr + B
                    y_bot = chart_bottom - 5
                    bot = QPainterPath()
                    bot.moveTo(cx - rw / 2, y_bot)
                    bot.lineTo(cx + rw / 2, y_bot)
                    bot.lineTo(cx, y_bot - arrow_h)
                    bot.closeSubpath()
                    painter.fillPath(bot, QColor("#56d6a5"))

        painter.end()
        self._img_label.setPixmap(canvas)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._repaint_label()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._pixmap:
            # Defer until Qt has activated the now-visible tab's layout —
            # otherwise _img_label.size() is still the hidden minimum and the
            # pixmap gets scaled too small, leaving a "border" around it.
            QTimer.singleShot(0, self._repaint_label)

    # ------------------------------------------------------------------
    # Image loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_frames(path: Path) -> int:
        """Return number of frames in a TIFF, trying PIL then tifffile."""
        try:
            return getattr(Image.open(path), "n_frames", 1)
        except Exception:
            pass
        try:
            import tifffile
            with tifffile.TiffFile(str(path)) as tif:
                return len(tif.pages)
        except Exception:
            return 0

    @staticmethod
    def _load_frame(path: Path, frame: int, ink_channels: list[str] | None) -> Image.Image:
        """Load one TIFF frame as RGB, handling CMYK and multi-channel Separated."""
        # Bypass PIL for known multi-channel files: PIL may silently return only 4 channels
        # from a Separated TIFF, dropping any extra ink channels without raising an exception.
        known_codes = ink_channels or _find_sidecar_channels(path)
        if known_codes and len(known_codes) > 4:
            return TiffPreview._tifffile_load_frame(path, frame, ink_channels)

        try:
            img = Image.open(path)
            if hasattr(img, "seek"):
                try:
                    img.seek(frame)
                except EOFError:
                    pass
            if img.mode in ("RGB", "RGBA"):
                return img.convert("RGB")
            if img.mode == "CMYK":
                return TiffPreview._cmyk_pil_to_rgb(img)
            return img.convert("RGB")
        except Exception:
            return TiffPreview._tifffile_load_frame(path, frame, ink_channels)

    @staticmethod
    def _cmyk_pil_to_rgb(img: Image.Image) -> Image.Image:
        """Convert CMYK PIL image to RGB using ICC profile (embedded or system SWOP)."""
        from PIL import ImageCms
        icc_data = img.info.get("icc_profile")
        if icc_data:
            try:
                import io
                src = ImageCms.ImageCmsProfile(io.BytesIO(icc_data))
                dst = ImageCms.createProfile("sRGB")
                t = ImageCms.buildTransformFromOpenProfiles(
                    src, dst, "CMYK", "RGB",
                    renderingIntent=ImageCms.Intent.PERCEPTUAL,
                )
                return ImageCms.applyTransform(img, t)
            except Exception:
                pass
        t = _get_cmyk_transform()
        if t:
            try:
                return ImageCms.applyTransform(img, t)
            except Exception:
                pass
        from PIL import ImageChops, ImageOps
        c, m, y, k = img.split()
        k_inv = ImageOps.invert(k)
        r = ImageChops.multiply(ImageOps.invert(c), k_inv)
        g = ImageChops.multiply(ImageOps.invert(m), k_inv)
        b = ImageChops.multiply(ImageOps.invert(y), k_inv)
        return Image.merge("RGB", (r, g, b))

    @staticmethod
    def _tifffile_load_frame(
        path: Path, frame: int, ink_channels: list[str] | None
    ) -> Image.Image:
        """Load multi-channel/malformed TIFF via tifffile and composite to RGB."""
        import tifffile
        import numpy as np

        with tifffile.TiffFile(str(path)) as tif:
            idx = min(frame, len(tif.pages) - 1)
            data = tif.pages[idx].asarray()

        if data.dtype != np.uint8:
            data = (data.astype(np.float32) * (255.0 / np.iinfo(data.dtype).max)).astype(np.uint8)

        if data.ndim == 2:
            return Image.fromarray(data.astype(np.uint8), "L").convert("RGB")

        n_ch = data.shape[2] if data.ndim == 3 else 1
        if n_ch == 3:
            return Image.fromarray(data.astype(np.uint8), "RGB")
        if n_ch < 2:
            return Image.fromarray(data[:, :, 0].astype(np.uint8), "L").convert("RGB")

        # Resolve ink codes: caller-supplied → sidecar → count heuristic → generic
        codes = (
            ink_channels
            or _find_sidecar_channels(path)
            or _N_CHANNELS_FALLBACK.get(n_ch)
            or (["c", "m", "y", "k"] + ["k"] * max(0, n_ch - 4))
        )

        # n_ch >= 4: ICC-convert CMYK base, then composite extra channels on top
        if n_ch >= 4:
            cmyk_img = Image.fromarray(data[:, :, :4], "CMYK")
            base = TiffPreview._cmyk_pil_to_rgb(cmyk_img)
            if n_ch == 4:
                return base
            ref = np.array(base, dtype=np.float32) / 255.0
            for i, code in enumerate(codes[4:n_ch], start=4):
                ink_val = data[:, :, i].astype(np.float32) / 255.0
                ar, ag, ab = _INK_ABSORPTION.get(code, (0.87, 0.87, 0.87))
                ref[:, :, 0] *= 1.0 - ink_val * ar
                ref[:, :, 1] *= 1.0 - ink_val * ag
                ref[:, :, 2] *= 1.0 - ink_val * ab
            return Image.fromarray((ref.clip(0, 1) * 255).astype(np.uint8), "RGB")

        # n_ch < 4 but > 1: pure absorption compositing (RGB/CMY devices)
        h, w = data.shape[:2]
        ref = np.ones((h, w, 3), dtype=np.float32)
        for ch_idx, code in enumerate(codes[:n_ch]):
            ink_val = data[:, :, ch_idx].astype(np.float32) / 255.0
            ar, ag, ab = _INK_ABSORPTION.get(code, (1.0, 1.0, 1.0))
            ref[:, :, 0] *= 1.0 - ink_val * ar
            ref[:, :, 1] *= 1.0 - ink_val * ag
            ref[:, :, 2] *= 1.0 - ink_val * ab
        return Image.fromarray((ref.clip(0, 1) * 255).astype(np.uint8), "RGB")

    @staticmethod
    def _pil_to_pixmap(img: Image.Image) -> QPixmap:
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        px = QPixmap()
        if not px.loadFromData(buf.read()):
            raise RuntimeError(
                f"QPixmap.loadFromData failed for {img.size} {img.mode} image"
            )
        return px
