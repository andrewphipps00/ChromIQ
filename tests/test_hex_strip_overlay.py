"""The measure preview's strip highlight follows the hexagonal patches and
their zigzag on SpectroScan hex charts, instead of a straight rect that spills
into the neighbouring column — and the swipe arrow is suppressed there, since a
SpectroScan XY table reads patch-by-patch (Knut/Basti)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.tiff_preview import TiffPreview  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _staggered_column(x0=100, w=80, h=60, y0=100, n=10, page=0):
    """A column of n hex patch boxes with the ±¼-width row stagger the renderer
    applies (odd patch numbers shift left, even shift right)."""
    boxes = []
    for j in range(n):
        dx = -(w // 4) if j % 2 == 0 else (w // 4)
        boxes.append(QRect(x0 + dx, y0 + j * h, w, h))
    return boxes


def test_hexagon_polygon_has_pointed_apexes_beyond_box(app):
    box = QRect(100, 200, 80, 60)
    poly = TiffPreview._hexagon_polygon(box)
    assert poly.count() == 6
    ys = [poly.at(i).y() for i in range(6)]
    # Top and bottom apexes overshoot the box by ~h/6 (pointed top/bottom).
    assert min(ys) < box.top()
    assert max(ys) > box.bottom()
    xs = [poly.at(i).x() for i in range(6)]
    assert min(xs) <= box.left() + 0.5 and max(xs) >= box.right() - 0.5


def test_strip_outline_follows_zigzag_not_a_straight_rect(app):
    p = TiffPreview()
    boxes = _staggered_column()
    p.set_page_patch_boxes({0: boxes})
    strip = QRect(100, 80, 80, 10 * 60)     # nominal (un-staggered) column span
    path = p._strip_zigzag_path(strip, 1.0, 0.0, 0.0)
    assert path is not None and not path.isEmpty()
    br = path.boundingRect()
    # The outline spans the full staggered width (box width + the ±¼ overhang on
    # both sides) — strictly wider than one un-staggered box.
    assert br.width() > 80 + 2 * (80 // 4) - 2
    # …and reaches above/below the boxes for the hex apexes.
    assert br.top() < 100
    assert br.bottom() > 100 + 10 * 60


def test_hex_zigzag_flag_toggles_and_repaints(app):
    p = TiffPreview()
    assert p._hex_zigzag is False
    p.set_hex_zigzag(True)
    assert p._hex_zigzag is True
    p.set_hex_zigzag(False)
    assert p._hex_zigzag is False


def test_zigzag_path_none_without_patch_geometry(app):
    p = TiffPreview()
    # No per-patch boxes on the page → fall back (None) to the plain rect path.
    assert p._strip_zigzag_path(QRect(0, 0, 80, 600), 1.0, 0.0, 0.0) is None
