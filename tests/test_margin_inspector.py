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
    # 1. On PATH (covers any OS where Argyll's bin dir is exported).
    p = shutil.which("printtarg")
    if p:
        return p
    # 2. The standard install locations the app itself probes, per-OS.
    from core.platform_paths import argyll_candidate_dirs
    for d in argyll_candidate_dirs():
        for name in ("printtarg", "printtarg.exe"):
            cand = d / name
            if cand.is_file():
                return str(cand)
    return None


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


def _preset_args(p) -> list[str]:
    """printtarg flags for a ti1 preset (mirrors tab_chart's render path)."""
    triple = p.triple_density and p.instrument == "CM"
    instr = "i1" if triple else ("3p" if p.instrument == "p3" else p.instrument)
    args = [f"-i{instr}", f"-p{p.paper}", "-t300"]
    if not triple and p.double_density and p.instrument in {"CM", "SS"}:
        args.append("-h")
    if p.suppress_left_clip or triple:
        args.append("-L")
    if abs(p.patch_scale - 1.0) > 0.01:
        args.append(f"-a{p.patch_scale:.2f}")
    if p.margin != 6:
        args.append(f"-m{p.margin}")
    args.append(f"-M{p.margin}")
    if p.no_strip_limit:
        args.append("-P")
    return args


def _measure_preset(tmp_path, slug):
    from core.resource_path import resource_path
    from ui.tabs.tab_chart import KNUT_PRESETS
    preset = next(p for p in KNUT_PRESETS if p.slug == slug)
    shutil.copy(resource_path(preset.ti1_asset), tmp_path / "chart.ti1")
    subprocess.run([PRINTTARG, *_preset_args(preset), "chart"],
                   cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reports = [measure_margins(t, dpi=300, ti2_path=tmp_path / "chart.ti2")
               for t in sorted(tmp_path.glob("chart*.tif"))]
    return preset, [r for r in reports if r is not None]


@requires_argyll
def test_colormunki_a4_preset_margins_not_inflated(tmp_path):
    """#83: a ColorMunki A4 2-page preset must measure small, sane margins from
    its full-page (-M) TIFF — NOT the inflated values seen when a wrong paper
    size was fed in (the bug was passing a stale A3 paper combo → +43/+61 mm)."""
    _, reports = _measure_preset(tmp_path, "fls_colormunki_a4_480p_2pages_portrait")
    assert reports
    for r in reports:
        assert r.page_w_mm == pytest.approx(210, abs=1.5)
        assert r.page_h_mm == pytest.approx(297, abs=1.5)
        assert r.left_mm < 15 and r.right_mm < 25     # not the 49/56 mm bug
        assert r.top_mm < 55 and r.bottom_mm < 55     # not the 102/94 mm bug


@requires_argyll
def test_wrong_paper_size_would_inflate_but_default_does_not(tmp_path):
    """Documents the #83 fix: measuring with no paper size (trusting the -M
    full-page TIFF) gives the true margins; feeding a too-large paper size adds
    a bogus (paper − tiff)/2 offset. The tab must therefore not pass a paper
    size that may be stale."""
    preset, _ = _measure_preset(tmp_path, "fls_colormunki_a4_480p_2pages_portrait")
    tif = sorted(tmp_path.glob("chart*.tif"))[0]
    good = measure_margins(tif, dpi=300)
    inflated = measure_margins(tif, dpi=300, paper_w_mm=297, paper_h_mm=420)
    assert good.left_mm < 15
    assert inflated.left_mm == pytest.approx(good.left_mm + (297 - 210) / 2, abs=1.0)


@requires_argyll
def test_strip_length_is_page_minus_top_bottom(tmp_path):
    """#87: strip length = patch-block extent in the reading direction =
    page height − top − bottom on a portrait chart."""
    _, reports = _measure_preset(tmp_path, "fls_colormunki_a4_480p_2pages_portrait")
    assert reports
    for r in reports:
        assert r.strip_length_mm is not None
        assert r.strip_length_mm == pytest.approx(
            r.page_h_mm - r.top_mm - r.bottom_mm, abs=0.2)
        assert 150 < r.strip_length_mm < 260


@requires_argyll
def test_landscape_strips_still_vertical(tmp_path):
    """#87: printtarg lays strips vertically even on a landscape page, so strip
    length is the vertical extent (page height − top − bottom) and patch width
    is block width ÷ strip count — both measured in the preview frame, not
    swapped by orientation."""
    work = tmp_path / "chart.ti1"
    src = (Path(__file__).resolve().parent.parent
           / "assets/charts/knut/rgb/fulllayout/fls_colormunki_a3_1224p_2pages_landscape/chart.ti1")
    shutil.copy(src, work)
    subprocess.run([PRINTTARG, "-iCM", "-p420x297", "-t200", "-h", "-a0.9",
                    "-M6", "-P", "chart"], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tif = sorted(tmp_path.glob("chart_*.tif"))[0]
    r = measure_margins(tif, dpi=200, ti2_path=tmp_path / "chart.ti2")
    assert r.page_w_mm > r.page_h_mm                      # landscape sheet
    assert r.strip_length_mm == pytest.approx(
        r.page_h_mm - r.top_mm - r.bottom_mm, abs=0.2)    # vertical extent
    assert 10 < r.strip_width_mm < 15                     # ~12.5 mm, not ~swapped


@requires_argyll
def test_patch_width_is_cross_strip_pitch(tmp_path):
    """#83: the patch-width readout is the strip pitch across the strips
    (block_w/passes on a portrait page) — a ruler measurement across a strip,
    ~12.7 mm for the ColorMunki A4 double-density chart, not the ~14 mm
    reading-direction pitch the old squareness heuristic returned."""
    _, reports = _measure_preset(tmp_path, "fls_colormunki_a4_480p_2pages_portrait")
    assert reports
    for r in reports:
        assert r.strip_width_mm == pytest.approx(12.7, abs=0.6)


def test_blank_page_returns_none(tmp_path):
    """A bare white sheet has no patch area → None (caller shows a placeholder),
    not bogus numbers. Runs without Argyll."""
    blank = tmp_path / "white.tif"
    Image.fromarray(np.full((600, 400, 3), 255, np.uint8)).save(blank, dpi=(300, 300))
    assert measure_margins(blank, dpi=300) is None
    assert _patch_area_bbox(np.full((600, 400, 3), 255, np.uint8)) is None
