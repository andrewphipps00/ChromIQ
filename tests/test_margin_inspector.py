"""Real-render tests for the margin inspector engine.

These render actual charts with ArgyllCMS ``printtarg`` and measure the result,
so they exercise the true layout geometry rather than a synthetic stand-in.
They skip cleanly where ``printtarg`` isn't installed (CI without Argyll).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from workflow.margin_inspector import measure_margins, _patch_area_bbox


def _find_printtarg() -> str | None:
    p = shutil.which("printtarg")
    if p:
        return p
    cand = Path("/Applications/Argyll/bin/printtarg")
    return str(cand) if cand.is_file() else None


PRINTTARG = _find_printtarg()
requires_argyll = pytest.mark.skipif(
    PRINTTARG is None, reason="ArgyllCMS printtarg not installed"
)

# A small i1Pro preset .ti1 shipped with the app — fast to render (1 page).
_TI1 = (
    Path(__file__).resolve().parent.parent
    / "assets/charts/knut/rgb/fulllayout/fls_i1pro_a4_484p_1page_portrait/chart.ti1"
)


def _render(tmp_path: Path, *args: str) -> Path:
    """Render the test .ti1 with the given extra printtarg args; return the TIF."""
    work = tmp_path / "chart.ti1"
    shutil.copy(_TI1, work)
    subprocess.run(
        [PRINTTARG, *args, "chart"],
        cwd=tmp_path, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return tmp_path / "chart.tif"


@requires_argyll
def test_full_page_tiff_margins_are_paper_edge(tmp_path):
    """-M includes the margin in the TIFF (full A4 sheet); margins are measured
    straight against the paper edge."""
    tif = _render(tmp_path, "-ii1", "-pA4", "-t300", "-P", "-L", "-M8")
    w, h = Image.open(tif).size
    assert (w, h) == (2480, 3508)            # full A4 at 300 dpi

    r = measure_margins(tif, dpi=300, ti2_path=tif.with_suffix(".ti2"))
    assert r is not None
    assert r.page_w_mm == pytest.approx(210, abs=0.5)
    assert r.page_h_mm == pytest.approx(297, abs=0.5)
    # -L suppresses the 26 mm clip border, so the left margin is just -M8.
    assert r.left_mm == pytest.approx(8, abs=1.5)
    # The strip-label side carries the larger margin (Knut's observation).
    assert r.right_mm > r.left_mm + 5
    # Top is measured to the first patch row, excluding the A/B/C label band.
    assert 10 < r.top_mm < 45
    assert 0 <= r.bottom_mm < 40
    # Estimated patch size in the reading direction is in a sane range.
    assert r.strip_width_mm is not None
    assert 6 < r.strip_width_mm < 16


@requires_argyll
def test_cropped_tiff_corrected_by_paper_size(tmp_path):
    """-m *subtracts* the margin (TIFF cropped to the imageable area); passing
    the true paper size restores the same paper-edge margins as the -M render."""
    tif = _render(tmp_path, "-ii1", "-pA4", "-t300", "-P", "-L", "-m8")
    w, h = Image.open(tif).size
    assert w < 2480 and h < 3508             # cropped: margin removed from raster

    bare = measure_margins(tif, dpi=300)                       # no paper size
    fixed = measure_margins(tif, dpi=300, paper_w_mm=210, paper_h_mm=297)
    assert bare is not None and fixed is not None
    # Without the paper size the left margin reads ~0 (patch hugs the crop edge);
    # with it, the trimmed 8 mm is added back.
    assert bare.left_mm == pytest.approx(0, abs=1.5)
    assert fixed.left_mm == pytest.approx(8, abs=1.5)
    assert fixed.page_w_mm == pytest.approx(210, abs=0.5)


@requires_argyll
def test_landscape_orientation_measured_in_tiff_frame(tmp_path):
    """Margins are reported in printtarg (TIFF) orientation — a landscape sheet
    is wider than tall and still resolves a patch area."""
    tif = _render(tmp_path, "-ii1", "-p420x297", "-t300", "-P", "-L", "-M8")
    r = measure_margins(tif, dpi=300, ti2_path=tif.with_suffix(".ti2"))
    assert r is not None
    assert r.page_w_mm > r.page_h_mm         # landscape in the TIFF
    # All margins are non-negative and the opposite pair fits inside the sheet
    # (a small patch set on a big sheet legitimately leaves a large margin).
    for v in (r.left_mm, r.right_mm, r.top_mm, r.bottom_mm):
        assert v >= 0
    assert r.left_mm + r.right_mm < r.page_w_mm
    assert r.top_mm + r.bottom_mm < r.page_h_mm


def test_blank_page_returns_none(tmp_path):
    """A bare white sheet has no patch area → None (caller shows a placeholder),
    not bogus numbers. Runs without Argyll."""
    blank = tmp_path / "white.tif"
    Image.fromarray(np.full((600, 400, 3), 255, np.uint8)).save(blank, dpi=(300, 300))
    assert measure_margins(blank, dpi=300) is None
    assert _patch_area_bbox(np.full((600, 400, 3), 255, np.uint8)) is None
