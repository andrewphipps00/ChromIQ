"""The marquee's coordinate maths: unit-square→quad homography and the grid
normalisation from engine geometry (#98). Pure — no real scan needed."""
import numpy as np

from ui.scan_grid_marquee import GridSpec, apply_h, unit_quad_homography


def test_homography_axis_aligned_rect():
    # Quad = a plain rectangle → the homography is a simple affine scale/offset.
    quad = [(10, 20), (110, 20), (110, 220), (10, 220)]   # TL, TR, BR, BL
    h = unit_quad_homography(quad)
    for (u, v), corner in zip([(0, 0), (1, 0), (1, 1), (0, 1)], quad):
        x, y = apply_h(h, u, v)
        assert abs(x - corner[0]) < 1e-6 and abs(y - corner[1]) < 1e-6
    cx, cy = apply_h(h, 0.5, 0.5)                # centre
    assert abs(cx - 60) < 1e-6 and abs(cy - 120) < 1e-6


def test_homography_perspective_quad_maps_corners_exactly():
    quad = [(30, 12), (210, 40), (198, 300), (8, 262)]    # skewed (perspective)
    h = unit_quad_homography(quad)
    for (u, v), corner in zip([(0, 0), (1, 0), (1, 1), (0, 1)], quad):
        x, y = apply_h(h, u, v)
        assert abs(x - corner[0]) < 1e-6 and abs(y - corner[1]) < 1e-6


def test_grid_from_patches_normalises_to_unit_square():
    # Two patches spanning x∈[0,300], y∈[0,220] (top-left px, already flipped).
    patches = [{"x": 0, "y": 0, "w": 100, "h": 100},
               {"x": 200, "y": 120, "w": 100, "h": 100}]
    g = GridSpec.from_patches(patches)
    assert len(g.rects) == 2
    u0, v0, w0, h0 = g.rects[0]
    assert (u0, v0) == (0.0, 0.0)
    assert abs(w0 - 100 / 300) < 1e-9 and abs(h0 - 100 / 220) < 1e-9
    # last patch's far corner touches (1,1)
    u1, v1, w1, h1 = g.rects[1]
    assert abs((u1 + w1) - 1.0) < 1e-9 and abs((v1 + h1) - 1.0) < 1e-9


def test_grid_empty_is_safe():
    assert GridSpec.from_patches([]).rects == []
