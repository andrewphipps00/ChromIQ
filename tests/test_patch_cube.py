"""Tests for the 3D patch-cube core (workflow/patch_cube.py)."""
from __future__ import annotations

import json
import re

from workflow import patch_cube as C


def test_stats_empty():
    s = C.cube_stats([])
    assert s.count == 0
    assert s.occupancy_pct == 0.0
    assert "No patches" in s.summary()


def test_stats_single_patch():
    s = C.cube_stats([(50.0, 50.0, 50.0)])
    assert s.count == 1
    assert s.nn_mean == 0.0
    # One patch occupies exactly one of the 1000 grid cells → 0.1%.
    assert abs(s.occupancy_pct - 0.1) < 1e-9


def test_stats_nearest_neighbour_and_occupancy():
    prog = [(0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (100.0, 100.0, 100.0)]
    s = C.cube_stats(prog)
    assert s.count == 3
    # Closest pair is the two black-ish points, 10 apart.
    assert abs(s.nn_min - 10.0) < 1e-6
    # 3 distinct cells out of 1000.
    assert abs(s.occupancy_pct - 0.3) < 1e-9


def test_stats_duplicate_flags_zero_spacing():
    s = C.cube_stats([(20.0, 30.0, 40.0), (20.0, 30.0, 40.0)])
    assert s.nn_min == 0.0


def test_occupancy_clamps_full_white():
    # 100.0 must land in the last grid cell, not overflow to index 10.
    s = C.cube_stats([(100.0, 100.0, 100.0)])
    assert abs(s.occupancy_pct - 0.1) < 1e-9


def test_cube_edges_are_twelve():
    assert len(C._CUBE_EDGES) == 12


def test_build_html_contains_data_and_js_url():
    prog = [(100.0, 0.0, 0.0), (0.0, 100.0, 0.0)]
    html = C.build_cube_html(prog, "file:///tmp/plotly.js", bg="#000", fg="#fff")
    assert "file:///tmp/plotly.js" in html
    assert "Plotly.newPlot" in html
    # Markers carry each patch's own colour.
    assert "rgb(255,0,0)" in html
    assert "rgb(0,255,0)" in html
    # The embedded JSON is well-formed (newPlot's data argument parses).
    m = re.search(r"Plotly\.newPlot\(\"plot\", (\[.*?\]), (\{.*?\}),\s*(\{.*?\})\);",
                  html, re.DOTALL)
    assert m, "newPlot call not found"
    data = json.loads(m.group(1))
    json.loads(m.group(2))
    json.loads(m.group(3))
    # cube + neutral axis + points
    assert len(data) == 3
    assert data[-1]["type"] == "scatter3d"
    assert len(data[-1]["x"]) == 2


def test_build_html_empty_program():
    html = C.build_cube_html([], "file:///tmp/plotly.js")
    assert "No patches" in html
    assert "Plotly.newPlot" in html
