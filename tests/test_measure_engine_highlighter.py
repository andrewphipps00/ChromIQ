"""The Measure-tab highlighter reads exact strip geometry from an engine
chart's channels.json (issue #93) instead of detecting stripes from the image."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from ui.tabs.tab_measure import engine_strip_rects_from_sidecar


def _channels_with_layout(path: Path, *, dpi=300) -> None:
    layout = {
        "dpi": dpi, "steps_in_pass": 21,
        "strips": [
            {"page": 0, "pass": 0, "x": 100, "y": 200, "w": 50, "h": 1300},
            {"page": 0, "pass": 1, "x": 160, "y": 200, "w": 50, "h": 1300},
            {"page": 1, "pass": 0, "x": 100, "y": 200, "w": 50, "h": 900},
        ],
        "patches": [], "seed": 42,
    }
    path.write_text(json.dumps({"ink_channels": ["r", "g", "b"], "layout": layout}))


def test_reads_engine_geometry(tmp_path: Path):
    sc = tmp_path / "chart.channels.json"
    _channels_with_layout(sc)
    result = engine_strip_rects_from_sidecar(sc, n_pages=2)
    assert result is not None
    per_page, counts = result
    assert counts == [2, 1]
    assert per_page[0][0].x() == 100 and per_page[0][0].width() == 50
    assert per_page[1][0].height() == 900


def test_none_without_layout(tmp_path: Path):
    sc = tmp_path / "chart.channels.json"
    sc.write_text(json.dumps({"ink_channels": ["r", "g", "b"]}))  # legacy chart
    assert engine_strip_rects_from_sidecar(sc, 1) is None


def test_none_when_page_uncovered(tmp_path: Path):
    sc = tmp_path / "chart.channels.json"
    _channels_with_layout(sc)
    # only pages 0 and 1 present; asking for 3 pages → not every page covered
    assert engine_strip_rects_from_sidecar(sc, 3) is None


def test_missing_file(tmp_path: Path):
    assert engine_strip_rects_from_sidecar(tmp_path / "nope.channels.json", 1) is None


def test_real_engine_chart_roundtrip(tmp_path: Path):
    """Build a real engine chart and confirm the highlighter geometry matches."""
    targen = Path("/Applications/Argyll/bin/targen")
    if not targen.exists():
        pytest.skip("ArgyllCMS targen not available")
    import subprocess
    from workflow.layout_engine import chart, geometry, instruments, papers
    base = tmp_path / "chart"
    subprocess.run([str(targen), "-d2", "-f120", str(base)], check=True,
                   capture_output=True)
    res = chart.build_chart(f"{base}.ti1", base, instrument="i1", paper="A4",
                            seed=1, dpi=150)
    sc = base.with_suffix(".channels.json")
    # channels.json doesn't exist yet (build_chart writes .strips.json); emulate
    # the chart_creator embed step for this headless path:
    strips = json.loads((base.with_suffix(".strips.json")).read_text())
    sc.write_text(json.dumps({"ink_channels": ["r", "g", "b"], "layout": strips}))

    out = engine_strip_rects_from_sidecar(sc, res.layout.pages)
    assert out is not None
    per_page, counts = out
    # one strip-rect per pass on the (single) page
    assert counts[0] == res.layout.passes
