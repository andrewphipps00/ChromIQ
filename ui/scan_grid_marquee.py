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

import re
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


_FID_MARKER = re.compile(
    r"(?mi)^#\s*CHROMIQ_FIDUCIALS\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


def fiducial_frame(text: str) -> tuple[float, float, float, float] | None:
    """The registration-mark frame ``(x0, x1, y0, y1)`` baked into a ChromIQ
    bundled ``.cht`` as a ``# CHROMIQ_FIDUCIALS left top right bottom`` marker
    (computed from the target's rectarg geometry), or None if the file has none.
    The marker carries the *real* fiducial positions; the ``F`` line stays the
    patch-area bbox so the default scanin path is unchanged."""
    m = _FID_MARKER.search(text)
    if not m:
        return None
    left, top, right, bottom = (float(g) for g in m.groups())
    return left, right, top, bottom


def cht_has_fiducials(text: str) -> bool:
    """True if the ``.cht`` carries a fiducial-mark frame distinct from the patch
    block, so framing by fiducials differs from framing by the patches. Bundled
    files with a ``# CHROMIQ_FIDUCIALS`` marker return True; the rest False."""
    fr = fiducial_frame(text)
    if fr is None:
        return False
    from workflow.cht_parser import ChtParseError, parse_cht
    try:
        geom = parse_cht(text)
    except ChtParseError:
        return False
    if not geom.patches:
        return False
    xs = [b.x1 for b in geom.patches] + [b.x2 for b in geom.patches]
    ys = [b.y1 for b in geom.patches] + [b.y2 for b in geom.patches]
    px0, px1, py0, py1 = min(xs), max(xs), min(ys), max(ys)
    span = max(px1 - px0, py1 - py0) or 1.0
    diff = abs(fr[0] - px0) + abs(fr[1] - px1) + abs(fr[2] - py0) + abs(fr[3] - py1)
    return diff > 0.02 * span


@dataclass
class GridSpec:
    """Normalised patch rectangles for the overlay, derived from the chart's
    exact geometry. Each rect is ``(u, v, w, h)`` in [0,1] with a **top-left**
    origin (the chart's bottom-left mm already flipped), so it maps straight
    through the quad homography. *aspect* is the patch block's width/height, so
    the marquee can seed a starting quad of the right shape."""
    rects: list[tuple[float, float, float, float]]
    aspect: float = 1.0
    ncols: int = 0        # set when the boxes form ONE regular contiguous grid,
    nrows: int = 0        # so the overlay can replicate rectarg's integer edges

    @staticmethod
    def _regular(patches, sw, sh, x0, y0) -> tuple[int, int]:
        """If every box is the same size and they tile a full contiguous
        ncols×nrows grid (rectarg's model), return (ncols, nrows); else (0, 0)."""
        xl = sorted({round((getattr(p, "x1", None) if hasattr(p, "x1")
                            else p["x"]), 2) for p in patches})
        yt = sorted({round((getattr(p, "y1", None) if hasattr(p, "y1")
                            else p["y"]), 2) for p in patches})
        nc, nr = len(xl), len(yt)
        if nc * nr != len(patches) or nc < 2 or nr < 2:
            return 0, 0
        # uniform column / row spacing?
        dxs = [xl[i + 1] - xl[i] for i in range(nc - 1)]
        dys = [yt[i + 1] - yt[i] for i in range(nr - 1)]
        if max(dxs) - min(dxs) > 0.02 * sw or max(dys) - min(dys) > 0.02 * sh:
            return 0, 0
        return nc, nr

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
        nc, nr = cls._regular(patches, sw, sh, x0, y0)
        return cls(rects, aspect=sw / sh, ncols=nc, nrows=nr)

    @classmethod
    def from_cht(cls, text: str, *, use_fiducials: bool = False) -> "GridSpec":
        """Build from *any* Argyll ``.cht`` (standard IT8 targets, ColorChecker,
        …). Boxes are normalised into the **total patch-area bounding box** — the
        union of *every* patch box across *all* areas — so the grid always covers
        the whole patch block, including multiple sub-areas (e.g. an IT8's GS
        greyscale strip). This matches how rectarg computes a target's extent
        (from the patch-area lines, not the fiducials); the ``.cht`` ``D`` line is
        "overall chart dimensions, not used". The user places the four corners on
        that same patch block.

        *use_fiducials* frames the grid by the ``.cht``'s fiducial-mark frame (the
        ``# CHROMIQ_FIDUCIALS`` marker) instead of the patch block, so the four
        corners go on the registration marks — when the file defines fiducials
        distinct from the patch block."""
        from workflow.cht_parser import ChtParseError, parse_cht
        try:
            geom = parse_cht(text)
        except ChtParseError:
            return cls([])
        if not geom.patches:
            return cls([])
        boxes = geom.patches
        xs = [b.x1 for b in boxes] + [b.x2 for b in boxes]
        ys = [b.y1 for b in boxes] + [b.y2 for b in boxes]
        frame = fiducial_frame(text) if use_fiducials else None
        if frame is not None:
            x0, x1, y0, y1 = frame
        else:
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        sw, sh = (x1 - x0) or 1.0, (y1 - y0) or 1.0
        nc, nr = cls._regular(boxes, sw, sh, x0, y0)
        return cls([((b.x1 - x0) / sw, (b.y1 - y0) / sh,
                     (b.x2 - b.x1) / sw, (b.y2 - b.y1) / sh) for b in boxes],
                   aspect=sw / sh, ncols=nc, nrows=nr)


_HANDLE_R = 8         # corner handle radius (screen px)
_HANDLE_OFFSET = 26   # handles sit this far OUTSIDE the true corner (screen px)
_HANDLE_DIRS = ((-1, -1), (1, -1), (1, 1), (-1, 1))   # TL, TR, BR, BL, outward
_SIDE_PAIRS = ((0, 1), (1, 2), (2, 3), (3, 0))   # top, right, bottom, left (corner idx)
_SIDE_R = 6           # mid-side handle radius (moves the whole edge, parallel)
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
        self._sample_frac = 0.5      # fraction of each patch AREA that scanin reads
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
        self._moving = False                  # dragging the whole grid to reposition
        self._move_ref: tuple | None = None
        self._side_drag = -1                  # dragging a mid-side handle (edge)
        self._side_ref: tuple | None = None
        self._allow_plain_wheel = False       # popped-out: plain wheel zooms too
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # for ⌘/Ctrl +/- zoom

    # ---------------------------------------------------------------- data
    def set_image(self, img: QImage) -> None:
        self._img = img
        self._rotation = 0
        self._rebuild_pixmap()
        self._reset_view()
        self._seed_corners()
        self.update()

    def reset_selection_grid(self) -> None:
        """Re-seed the quad from the chart geometry at the current image size — the
        "Reset Selection Grid" button. Recovers a placement that flew off-screen
        (e.g. after loading a different-resolution image)."""
        self._reset_view()
        self._seed_corners()
        self.update()

    def _seed_corners(self) -> None:
        """Starting quad: a centred rectangle matching the patch block's aspect
        ratio at ~90% of the image, so it's already the right shape to nudge onto
        the target — not a blind inset. Falls back to a plain inset when there's
        no grid to take an aspect from."""
        if not self._grid.rects or not self._img_w or not self._img_h:
            self._reset_corners()
            return
        iw, ih = float(self._img_w), float(self._img_h)
        ar = self._grid.aspect or (iw / ih)
        aw, ah = iw * 0.90, ih * 0.90
        if aw / ah > ar:
            h = ah; w = h * ar
        else:
            w = aw; h = w / ar
        cx, cy = iw / 2.0, ih / 2.0
        self._corners = [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
                         [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
        self.changed.emit()

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
        if self._pix is not None:            # target changed with a scan loaded
            self._seed_corners()
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

    def image_size(self) -> tuple[int, int]:
        """(width, height) of the loaded (rotated) image, or (0, 0) if none —
        lets the dialog store a placement as fractions and restore it at any
        resolution."""
        return self._img_w, self._img_h

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

    @staticmethod
    def _rectarg_edges(total_px: float, n: int) -> list[float]:
        """rectarg's integer-pixel column/row edges, normalised to [0,1]: each
        cell is floor(total/n) px, the leftover pixels going to the FIRST cells.
        Replicated so the overlay lines up exactly with a rectarg-rendered image
        (its cells aren't perfectly uniform at low dpi)."""
        import math
        base = int(math.floor(total_px / n))
        rem = int(round(total_px - base * n))
        edges = [0]
        for i in range(n):
            edges.append(edges[-1] + base + (1 if i < rem else 0))
        tot = edges[-1] or 1
        return [e / tot for e in edges]

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

        # Build the list of full-cell (u, v, w, hh) rects. For a single regular
        # grid, replicate rectarg's exact integer edges at the placed quad's
        # pixel size; otherwise fall back to each box's own rect.
        nc, nr = self._grid.ncols, self._grid.nrows
        if nc and nr:
            c = self._corners
            wpx = ((c[1][0] - c[0][0]) ** 2 + (c[1][1] - c[0][1]) ** 2) ** 0.5
            hpx = ((c[3][0] - c[0][0]) ** 2 + (c[3][1] - c[0][1]) ** 2) ** 0.5
            ue = self._rectarg_edges(wpx, nc)
            ve = self._rectarg_edges(hpx, nr)
            cells = [(ue[i], ve[j], ue[i + 1] - ue[i], ve[j + 1] - ve[j])
                     for j in range(nr) for i in range(nc)]
        else:
            cells = self._grid.rects

        for (u, v, w, hh) in cells:
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

    def _side_handle_pos(self, i: int) -> QPointF:
        """Midpoint handle of side *i*, offset OUTWARD (perpendicular) — drag it to
        move that whole edge parallel, without touching two corners."""
        import math
        a, b = _SIDE_PAIRS[i]
        ca = self._to_widget(*self._corners[a]); cb = self._to_widget(*self._corners[b])
        mx, my = (ca.x() + cb.x()) / 2, (ca.y() + cb.y()) / 2
        ex, ey = cb.x() - ca.x(), cb.y() - ca.y()
        L = math.hypot(ex, ey) or 1.0
        px, py = -ey / L, ex / L                 # perpendicular
        cx = sum(self._to_widget(*c).x() for c in self._corners) / 4
        cy = sum(self._to_widget(*c).y() for c in self._corners) / 4
        if (mx + px - cx) ** 2 + (my + py - cy) ** 2 < (mx - px - cx) ** 2 + (my - py - cy) ** 2:
            px, py = -px, -py                    # point away from the centre
        return QPointF(mx + px * _HANDLE_OFFSET, my + py * _HANDLE_OFFSET)

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
        for i in range(4):                       # mid-side handles (move an edge)
            a, b = _SIDE_PAIRS[i]
            mid = QPointF((wc[a].x() + wc[b].x()) / 2, (wc[a].y() + wc[b].y()) / 2)
            sp = self._side_handle_pos(i)
            p.setPen(conn)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(mid, sp)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_ACCENT)
            p.drawEllipse(sp, _SIDE_R, _SIDE_R)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _is_dark(self) -> bool:
        return self.palette().color(self.backgroundRole()).lightness() < 128

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self._pix is None:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.MiddleButton:   # middle drag always pans
            self._panning = True
            self._pan_ref = (pos.x(), pos.y(), self._pan[0], self._pan[1])
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self._drag = -1
        self._side_drag = -1
        for i in range(len(self._corners)):
            if (self._handle_pos(i) - pos).manhattanLength() <= _HANDLE_R * 2.4:
                self._drag = i
                return
        if len(self._corners) == 4:              # mid-side handle → move that edge
            for i in range(4):
                if (self._side_handle_pos(i) - pos).manhattanLength() <= _SIDE_R * 2.8:
                    self._side_drag = i
                    ix, iy = self._to_image(pos.x(), pos.y())
                    self._side_ref = (ix, iy, [c[:] for c in self._corners])
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return
        if len(self._corners) == 4 and self._point_in_quad(pos):
            self._moving = True                  # inside the grid → move the whole grid
            ix, iy = self._to_image(pos.x(), pos.y())
            self._move_ref = (ix, iy, [c[:] for c in self._corners])
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:                                    # outside → pan the image
            self._panning = True
            self._pan_ref = (pos.x(), pos.y(), self._pan[0], self._pan[1])
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _point_in_quad(self, pos) -> bool:
        pts = [self._to_widget(*c) for c in self._corners]
        inside = False
        n = len(pts)
        for i in range(n):
            xi, yi = pts[i].x(), pts[i].y()
            xj, yj = pts[i - 1].x(), pts[i - 1].y()
            if ((yi > pos.y()) != (yj > pos.y())) and \
               (pos.x() < (xj - xi) * (pos.y() - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
        return inside

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
        elif self._side_drag >= 0 and self._side_ref is not None:
            ix, iy = self._to_image(pos.x(), pos.y())
            ox, oy, ref = self._side_ref
            dx, dy = ix - ox, iy - oy            # translate both corners of the edge
            a, b = _SIDE_PAIRS[self._side_drag]
            self._corners = [c[:] for c in ref]
            self._corners[a] = [ref[a][0] + dx, ref[a][1] + dy]
            self._corners[b] = [ref[b][0] + dx, ref[b][1] + dy]
            self.changed.emit()
            self.update()
        elif self._moving and self._move_ref is not None:
            ix, iy = self._to_image(pos.x(), pos.y())
            ox, oy, ref = self._move_ref
            dx, dy = ix - ox, iy - oy
            self._corners = [[c[0] + dx, c[1] + dy] for c in ref]
            self.changed.emit()
            self.update()
        elif self._panning and self._pan_ref is not None:
            sx, sy, px, py = self._pan_ref
            self._pan = [px + (pos.x() - sx), py + (pos.y() - sy)]
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = -1
        self._side_drag = -1
        self._panning = False
        self._moving = False
        self.unsetCursor()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                            | Qt.KeyboardModifier.MetaModifier):
            if e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._zoom_at_centre(1.25); return
            if e.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self._zoom_at_centre(0.8); return
            if e.key() == Qt.Key.Key_0:
                self._reset_view(); return
        super().keyPressEvent(e)

    def _zoom_at_centre(self, factor: float) -> None:
        if self._pix is None:
            return
        cx, cy = self.width() / 2.0, self.height() / 2.0
        ix, iy = self._to_image(cx, cy)
        self._zoom = max(1.0, min(16.0, self._zoom * factor))
        if self._zoom <= 1.0:
            self._pan = [0.0, 0.0]
        else:
            s = self._scale * self._zoom
            self._pan = [cx - self._ox - ix * s, cy - self._oy - iy * s]
        self.update()

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
