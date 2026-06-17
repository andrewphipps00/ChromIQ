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
    m = re.search(r"Plotly\.newPlot\(\"plot\", (\[.*?\]), CQ_LAYOUT, CQ_CONFIG\);",
                  html, re.DOTALL)
    assert m, "newPlot call not found"
    data = json.loads(m.group(1))
    # Layout + config are hoisted to consts the live-update hook reuses.
    json.loads(re.search(r"var CQ_LAYOUT = (\{.*?\});", html, re.DOTALL).group(1))
    json.loads(re.search(r"var CQ_CONFIG = (\{.*?\});", html, re.DOTALL).group(1))
    # cube + neutral axis + points
    assert len(data) == 3
    assert data[-1]["type"] == "scatter3d"
    assert len(data[-1]["x"]) == 2


def test_build_html_has_middle_drag_pan():
    # #64: middle-button (wheel-click) drag pans the cube via Plotly.relayout.
    html = C.build_cube_html([(50.0, 50.0, 50.0)], "file:///tmp/plotly.js")
    assert "e.button===1" in html             # middle-button gesture
    assert "scene.camera" in html             # pans by moving the camera
    assert "Plotly.relayout" in html
    assert 'cqInstallPan("plot")' in html


def test_numbered_patch_hover_for_layout_editor():
    # #67: the layout-editor 3D view labels each patch with its 1-based number
    # (to locate it in the swatch/preview); other windows stay un-numbered.
    prog = [(100.0, 0.0, 0.0), (0.0, 100.0, 0.0)]
    pts = C.cube_traces(prog, numbered=True)[-1]      # the markers trace
    assert pts["text"][0].startswith("patch #: 1 ") and "RGB 255 0 0" in pts["text"][0]
    assert pts["text"][1].startswith("patch #: 2 ")
    plain = C.cube_traces(prog)[-1]
    assert "#:" not in plain["text"][0] and plain["text"][0].startswith("patch ·")
    # Surfaces through build_cube_html(numbered=True).
    assert "patch #: 1 " in C.build_cube_html(prog, "file:///p.js", numbered=True)


def test_build_html_has_keyboard_and_help_hint():
    # #66 follow-up (Knut): keyboard controls + a visible mouse/keyboard help line.
    html = C.build_cube_html([(50.0, 50.0, 50.0)], "file:///tmp/plotly.js")
    assert "cqInstallKeys" in html
    assert "ArrowLeft" in html and "shiftKey" in html      # rotate / pan keys
    assert 'id="help"' in html                              # visible hint bar


def test_build_dual_cube_html():
    # #66: two cubes, each labelled, with synced cameras + per-cube pan.
    a = [(100.0, 0.0, 0.0), (0.0, 100.0, 0.0)]
    b = [(0.0, 0.0, 100.0)]
    html = C.build_dual_cube_html(a, "My Chart", b, "TC9.18 preset",
                                  "file:///tmp/plotly.js")
    assert 'id="plotA"' in html and 'id="plotB"' in html
    assert "My Chart" in html and "TC9.18 preset" in html
    assert "cqLinkCameras" in html            # cameras stay in sync
    assert html.count("cqInstallPan") >= 2    # pan on both cubes
    # Both newPlot data args parse.
    for m in re.finditer(r'Plotly\.newPlot\("plot[AB]", (\[.*?\]), CQ_LAYOUT',
                         html, re.DOTALL):
        json.loads(m.group(1))
    # Label HTML-escaping guards against breakage from preset names.
    evil = C.build_dual_cube_html(a, "a<b&\"c", b, "x", "file:///p.js")
    assert "a<b&\"c" not in evil and "a&lt;b" in evil


def test_build_html_empty_program():
    html = C.build_cube_html([], "file:///tmp/plotly.js")
    assert "No patches" in html
    assert "Plotly.newPlot" in html
    # The live-update hook is always present so the host can push fresh data.
    assert "window.cqUpdateCube" in html


def test_build_html_existing_split_adds_trace():
    # Existing patches are drawn as their own dimmed trace beneath the new ones.
    html = C.build_cube_html(
        [(100.0, 0.0, 0.0)], "file:///tmp/plotly.js",
        existing_program=[(0.0, 0.0, 0.0), (50.0, 50.0, 50.0)])
    m = re.search(r"Plotly\.newPlot\(\"plot\", (\[.*?\]), CQ_LAYOUT, CQ_CONFIG\);",
                  html, re.DOTALL)
    data = json.loads(m.group(1))
    # cube + neutral + existing + new
    assert len(data) == 4
    names = [d.get("name") for d in data]
    assert "existing" in names and "new" in names
    # Footer shows the split over the combined set.
    assert "2 existing + 1 new" in html


def test_cube_payload_shape():
    p = C.cube_payload([(10.0, 20.0, 30.0)],
                       existing_program=[(0.0, 0.0, 0.0)])
    assert set(p) == {"data", "stats"}
    assert isinstance(p["data"], list) and len(p["data"]) == 4
    assert "1 existing + 1 new" in p["stats"]
    # Serialisable for runJavaScript hand-off.
    json.loads(json.dumps(p))


def test_combined_summary_no_existing_is_plain():
    s = C.combined_summary([(10.0, 20.0, 30.0), (90.0, 90.0, 90.0)])
    assert "existing" not in s
    assert "2 patches" in s
