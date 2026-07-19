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


def _subpaths(path) -> int:
    return sum(1 for i in range(path.elementCount())
               if path.elementAt(i).isMoveTo())


def test_strip_outline_is_one_frame_for_the_whole_column(app):
    p = TiffPreview()
    boxes = _staggered_column()
    p.set_page_patch_boxes({0: boxes})
    strip = QRect(100, 80, 80, 10 * 60)     # nominal (un-staggered) column span
    path = p._strip_zigzag_path(strip, 1.0, 0.0, 0.0)
    assert path is not None and not path.isEmpty()
    # ONE outline for the whole strip — not a frame per patch (the union of the
    # edge-tessellating hexagons would have left 10 closed sub-loops).
    assert _subpaths(path) == 1
    br = path.boundingRect()
    # Spans the full staggered width (box width + the ±¼ overhang on both sides),
    # strictly wider than one un-staggered box…
    assert br.width() > 80 + 2 * (80 // 4) - 2
    # …and reaches above/below the boxes for the pointed hex apexes.
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
