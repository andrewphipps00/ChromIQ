"""Interactive four-corner marquee over a scanned chart, with a live grid overlay
(#98).

The user drags four corner handles onto the printed patch area of the scan; a
grid of the chart's real patch boxes — perspective-mapped into that quad — is
drawn on top so they can *see* the alignment before running ``scanin``. On
confirm the four corners (image pixels, order **TL, TR, BR, BL**) become
``scanin -F``.

Coordinate note: the chart geometry is bottom-left millimetres, the image is
top-left pixels, so the grid mapping flips ``v`` (a patch at the chart *top*,
``ymax``, maps to the *top* of the quad).

The homography (unit square → the user's quad) is a pure function
(:func:`unit_quad_homography`) so it's unit-tested without Qt.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import QWidget


def unit_quad_homography(quad: list[tuple[float, float]]) -> np.ndarray:
    """3×3 homography mapping the unit square corners ``(0,0),(1,0),(1,1),(0,1)``
    (i.e. TL, TR, BR, BL in u-right/v-down coords) onto *quad* (four (x, y)).
    Exact for four points (DLT); normalised so ``H[2,2] == 1``."""
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    dst = np.array(quad, float)
    A = []
    for (u, v), (x, y) in zip(src, dst):
        A.append([u, v, 1, 0, 0, 0, -u * x, -v * x, -x])
        A.append([0, 0, 0, u, v, 1, -u * y, -v * y, -y])
    _, _, vt = np.linalg.svd(np.array(A))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def apply_h(h: np.ndarray, u: float, v: float) -> tuple[float, float]:
    """Map a unit-square point ``(u, v)`` through homography *h* to pixels."""
    p = h @ np.array([u, v, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


@dataclass
class GridSpec:
    """Normalised patch rectangles for the overlay, derived from the chart's
    exact geometry. Each rect is ``(u, v, w, h)`` in [0,1] with a **top-left**
    origin (the chart's bottom-left mm already flipped), so it maps straight
    through the quad homography."""
    rects: list[tuple[float, float, float, float]]

    @classmethod
    def from_patches(cls, patches: list[dict]) -> "GridSpec":
        """Build from engine ``channels.json["layout"]["patches"]`` (top-left px).
        Uses the patch-area bounding box to normalise; page filtering is the
        caller's job (pass one page's patches)."""
        if not patches:
            return cls([])
        xs = [p["x"] for p in patches] + [p["x"] + p["w"] for p in patches]
        ys = [p["y"] for p in patches] + [p["y"] + p["h"] for p in patches]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        sw, sh = (x1 - x0) or 1.0, (y1 - y0) or 1.0
        rects = [((p["x"] - x0) / sw, (p["y"] - y0) / sh, p["w"] / sw, p["h"] / sh)
                 for p in patches]
        return cls(rects)

    @classmethod
    def from_cht(cls, text: str) -> "GridSpec":
        """Build from *any* Argyll ``.cht`` (standard IT8 targets, ColorChecker,
        …). Boxes are normalised into the **total patch-area bounding box** — the
        union of *every* patch box across *all* areas — so the grid always covers
        the whole patch block, including multiple sub-areas (e.g. an IT8's GS
        greyscale strip). This matches how rectarg computes a target's extent
        (from the patch-area lines, not the fiducials); the ``.cht`` ``D`` line is
        "overall chart dimensions, not used", and the ``F`` fiducials can sit
        off the patch block. The user places the four corners on that same patch
        block. The rects are the **full** patch boxes; the sampled sub-area is
        drawn from the marquee's sample fraction (the "patch sample area"
        control), not baked in here."""
        from workflow.cht_parser import ChtParseError, parse_cht
        try:
            geom = parse_cht(text)
        except ChtParseError:
            return cls([])
        if not geom.patches:
            return cls([])
        xs = [b.x1 for b in geom.patches] + [b.x2 for b in geom.patches]
        ys = [b.y1 for b in geom.patches] + [b.y2 for b in geom.patches]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        sw, sh = (x1 - x0) or 1.0, (y1 - y0) or 1.0
        return cls([((b.x1 - x0) / sw, (b.y1 - y0) / sh,
                     (b.x2 - b.x1) / sw, (b.y2 - b.y1) / sh) for b in geom.patches])


_HANDLE_R = 8         # corner handle radius (screen px)
_HANDLE_OFFSET = 26   # handles sit this far OUTSIDE the true corner (screen px)
_HANDLE_DIRS = ((-1, -1), (1, -1), (1, 1), (-1, 1))   # TL, TR, BR, BL, outward
_ACCENT = QColor("#56d6a5")


class ScanGridMarquee(QWidget):
    """Displays a scan fit-to-view with a draggable 4-corner quad + grid overlay."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)
        self._img: QImage | None = None      # original, unrotated
        self._pix: QPixmap | None = None      # rotated, for display
        self._rotation = 0                    # 0/90/180/270, applied to _img
        self._img_w = self._img_h = 0         # dims of the (rotated) image
        self._grid = GridSpec([])
        self._sample_frac = 0.6      # fraction of each patch AREA that scanin reads
        # Quad corners in IMAGE pixels, order TL, TR, BR, BL.
        self._corners: list[list[float]] = []
        self._drag = -1
        # View transform: image px → widget px is (fit_scale·zoom)·p + fit_off + pan.
        self._scale = 1.0                     # fit-to-view scale
        self._ox = self._oy = 0.0             # fit-to-view offset
        self._zoom = 1.0                      # user zoom on top of the fit (≥1)
        self._pan = [0.0, 0.0]                # user pan, widget px
        self._panning = False
        self._pan_ref: tuple[float, float, float, float] | None = None
        self._allow_plain_wheel = False       # popped-out: plain wheel zooms too
        self.setMouseTracking(True)

    # ---------------------------------------------------------------- data
    def set_image(self, img: QImage) -> None:
        self._img = img
        self._rotation = 0
        self._rebuild_pixmap()
        self._reset_view()
        self._reset_corners()
        self.update()

    def _rebuild_pixmap(self) -> None:
        if self._img is None or self._img.isNull():
            self._pix = None
            self._img_w = self._img_h = 0
            return
        img = self._img
        if self._rotation:
            img = img.transformed(QTransform().rotate(self._rotation))
        self._pix = QPixmap.fromImage(img)
        self._img_w, self._img_h = img.width(), img.height()

    def _reset_view(self) -> None:
        """Back to fit-to-view (zoom 1, no pan)."""
        self._zoom = 1.0
        self._pan = [0.0, 0.0]
        self.update()

    def rotate_90(self) -> None:
        """Rotate the loaded scan 90° clockwise (for a sideways capture). Any
        placed corners rotate with it so the grid stays put."""
        if self._img is None:
            return
        h = self._img_h                                  # height before rotating
        self._corners = [[h - c[1], c[0]] for c in self._corners]
        self._rotation = (self._rotation + 90) % 360
        self._rebuild_pixmap()
        self._reset_view()
        self.changed.emit()
        self.update()

    def set_grid(self, grid: GridSpec) -> None:
        self._grid = grid
        self.update()

    def set_sample_fraction(self, frac: float) -> None:
        """Fraction (0–1) of each patch's AREA that scanin samples — drawn as an
        inner rectangle inside every patch cell so the read zone is visible."""
        self._sample_frac = max(0.05, min(1.0, float(frac)))
        self.update()

    def set_wheel_zoom(self, on: bool) -> None:
        """When True (the popped-out window), a plain scroll wheel zooms;
        otherwise only Ctrl/Cmd+wheel zooms, so a plain wheel scrolls the dialog."""
        self._allow_plain_wheel = bool(on)

    def _reset_corners(self) -> None:
        """Seed the quad inset ~8% from the image edges (a sensible starting box
        the user nudges onto the patch area)."""
        w, h = self._img_w, self._img_h
        mx, my = w * 0.08, h * 0.08
        self._corners = [[mx, my], [w - mx, my], [w - mx, h - my], [mx, h - my]]
        self.changed.emit()

    def corners_image_px(self) -> list[tuple[float, float]]:
        """The four quad corners in image pixels (TL, TR, BR, BL) — feeds
        ``scanin -F``."""
        return [(c[0], c[1]) for c in self._corners]

    def set_corners(self, corners: list[tuple[float, float]]) -> None:
        """Restore a saved placement (image px, TL/TR/BR/BL) — used to keep each
        page's quad when switching pages of a multi-page chart."""
        if corners and len(corners) == 4:
            self._corners = [[float(x), float(y)] for x, y in corners]
            self.update()

    def has_placement(self) -> bool:
        return bool(self._corners) and self._pix is not None

    # ---------------------------------------------------------------- view
    def _recompute_fit(self) -> None:
        if not self._img_w or not self._img_h:
            return
        aw, ah = self.width(), self.height()
        self._scale = min(aw / self._img_w, ah / self._img_h)
        self._ox = (aw - self._img_w * self._scale) / 2
        self._oy = (ah - self._img_h * self._scale) / 2

    def _to_widget(self, x: float, y: float) -> QPointF:
        s = self._scale * self._zoom
        return QPointF(self._ox + self._pan[0] + x * s, self._oy + self._pan[1] + y * s)

    def _to_image(self, x: float, y: float) -> tuple[float, float]:
        s = (self._scale * self._zoom) or 1.0
        return (x - self._ox - self._pan[0]) / s, (y - self._oy - self._pan[1]) / s

    # ---------------------------------------------------------------- paint
    def resizeEvent(self, e) -> None:  # noqa: N802
        self._recompute_fit()
        super().resizeEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111" if self._is_dark() else "#e8e8e8"))
        if self._pix is None:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Load a scan of the printed chart")
            return
        self._recompute_fit()
        s = self._scale * self._zoom
        target = QRectF(self._ox + self._pan[0], self._oy + self._pan[1],
                        self._img_w * s, self._img_h * s)
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_grid(p)
        self._draw_quad(p)

    def _draw_grid(self, p: QPainter) -> None:
        if not self._grid.rects or len(self._corners) != 4:
            return
        h = unit_quad_homography(self._corners)
        lin = self._sample_frac ** 0.5            # area frac → per-side scale
        inset = (1.0 - lin) / 2.0
        outline = QPen(QColor(86, 214, 165, 90))  # full patch cell — faint
        outline.setWidthF(1.0)
        sample = QPen(QColor(86, 214, 165, 220))  # sampled sub-area — solid
        sample.setWidthF(1.4)
        fill = QColor(86, 214, 165, 40)
        for (u, v, w, hh) in self._grid.rects:
            p.setPen(outline)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(*[self._to_widget(*apply_h(h, x, y)) for x, y in
                            ((u, v), (u + w, v), (u + w, v + hh), (u, v + hh))])
            iu, iv, iw, ih = u + w * inset, v + hh * inset, w * lin, hh * lin
            p.setPen(sample)
            p.setBrush(fill)
            p.drawPolygon(*[self._to_widget(*apply_h(h, x, y)) for x, y in
                            ((iu, iv), (iu + iw, iv), (iu + iw, iv + ih), (iu, iv + ih))])
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _handle_pos(self, i: int) -> QPointF:
        """The i-th grab handle, offset OUTSIDE the true corner along the diagonal
        so the big circle never hides the corner patch you're aiming at."""
        c = self._to_widget(*self._corners[i])
        dx, dy = _HANDLE_DIRS[i]
        return QPointF(c.x() + dx * _HANDLE_OFFSET, c.y() + dy * _HANDLE_OFFSET)

    def _draw_quad(self, p: QPainter) -> None:
        if len(self._corners) != 4:
            return
        pen = QPen(_ACCENT)
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        wc = [self._to_widget(*c) for c in self._corners]
        p.drawPolygon(*wc)
        conn = QPen(_ACCENT)
        conn.setStyle(Qt.PenStyle.DotLine)
        conn.setWidthF(1.2)
        for i, c in enumerate(wc):
            hp = self._handle_pos(i)
            p.setPen(conn)                       # 45° dotted line to the corner
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(c, hp)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_ACCENT)
            p.drawEllipse(hp, _HANDLE_R, _HANDLE_R)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _is_dark(self) -> bool:
        return self.palette().color(self.backgroundRole()).lightness() < 128

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self._pix is None:
            return
        pos = e.position()
        self._drag = -1
        for i in range(len(self._corners)):
            if (self._handle_pos(i) - pos).manhattanLength() <= _HANDLE_R * 2.4:
                self._drag = i
                break
        if self._drag < 0:                       # empty space → pan the view
            self._panning = True
            self._pan_ref = (pos.x(), pos.y(), self._pan[0], self._pan[1])
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        pos = e.position()
        if self._drag >= 0:
            dx, dy = _HANDLE_DIRS[self._drag]    # handle is offset — move the corner
            x, y = self._to_image(pos.x() - dx * _HANDLE_OFFSET,
                                  pos.y() - dy * _HANDLE_OFFSET)
            x = max(0.0, min(self._img_w, x))
            y = max(0.0, min(self._img_h, y))
            self._corners[self._drag] = [x, y]
            self.changed.emit()
            self.update()
        elif self._panning and self._pan_ref is not None:
            sx, sy, px, py = self._pan_ref
            self._pan = [px + (pos.x() - sx), py + (pos.y() - sy)]
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = -1
        self._panning = False
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._reset_view()                       # snap back to fit-to-view

    def wheelEvent(self, e) -> None:  # noqa: N802
        zoom_it = self._allow_plain_wheel or bool(
            e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                             | Qt.KeyboardModifier.MetaModifier))
        if self._pix is None or not zoom_it:
            e.ignore()                       # plain wheel → let the dialog scroll
            return
        pos = e.position()
        ix, iy = self._to_image(pos.x(), pos.y())
        self._zoom = max(1.0, min(16.0, self._zoom * (1.0015 ** e.angleDelta().y())))
        if self._zoom <= 1.0:
            self._pan = [0.0, 0.0]               # fit → recentre
        else:                                    # keep point under cursor fixed
            s = self._scale * self._zoom
            self._pan = [pos.x() - self._ox - ix * s, pos.y() - self._oy - iy * s]
        self.update()
        e.accept()
