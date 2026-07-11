"""Patch sample-area control: the % of each patch scanin reads maps to the
chart's BOX_SHRINK (Knut's request), and the marquee draws the inner read zone."""
import math

import pytest

from workflow.scanin_runner import sample_area_box_shrink, cht_with_sample_area

_CHT = """

BOXES 4
  F _ _ 0 0 200 0 200 200 0 200
  X A01 A01 _ _ 40 40 20 20 0 0
  X A02 A02 _ _ 40 40 80 20 0 0
  X B01 B01 _ _ 40 40 20 80 0 0
  X B02 B02 _ _ 40 40 80 80 0 0

BOX_SHRINK 6.000

REF_ROTATION 0.0

XLIST 2
  20 40 1
  80 40 1

YLIST 2
  20 40 1
  80 40 1

EXPECTED XYZ 4
  A01 40 40 40
  A02 40 40 40
  B01 40 40 40
  B02 40 40 40
"""


def test_shrink_from_area_fraction():
    # 60% of a 40-unit box → inner side 40·√0.6, per-side shrink 40·(1−√0.6)/2.
    want = round(40 * (1 - math.sqrt(0.6)) / 2, 3)
    assert sample_area_box_shrink(_CHT, 0.6) == want
    # Full area → no shrink; empty geometry → None.
    assert sample_area_box_shrink(_CHT, 1.0) == 0.0
    assert sample_area_box_shrink("no boxes here", 0.6) is None
    # Smaller fraction → larger shrink (reads less of each patch).
    assert sample_area_box_shrink(_CHT, 0.4) > sample_area_box_shrink(_CHT, 0.8)


@pytest.mark.parametrize("frac", [0.4, 0.5, 0.6, 0.7, 0.8])
@pytest.mark.parametrize("w,h", [(40.0, 40.0), (20.0, 40.0), (25.62, 51.25)])
def test_sample_margin_equal_sides_and_exact_area(frac, w, h):
    """Knut's #119 verification request: for square AND rectangular patches,
    at 40–80 % sample area, the read zone keeps the SAME distance to the
    patch border on all four sides while its area is exactly the chosen
    fraction — and the inverse recovers the full patch from the shrunk box."""
    from workflow.scanin_runner import sample_margin, sample_margin_inverse
    m = sample_margin(w, h, frac)
    assert m > 0
    sw, sh = w - 2 * m, h - 2 * m
    assert sw > 0 and sh > 0
    assert abs(sw * sh - frac * w * h) < 1e-9 * w * h     # exact area
    # square patches keep the familiar √f-per-side form (no recalibration)
    if w == h:
        assert abs(m - w * (1 - math.sqrt(frac)) / 2) < 1e-9
    # the inverse round-trips
    assert abs(sample_margin_inverse(sw, sh, frac) - m) < 1e-9


@pytest.mark.parametrize("frac", [0.4, 0.6, 0.8])
def test_cht_boxes_get_equal_margins_per_block(frac):
    """The prepared .cht encodes the equal-margin rule per box block — the
    square main grid and a 1:2 GS strip each get their own margin — with
    BOX_SHRINK pinned to 0 so scanin (and its diagnostic image) read exactly
    these boxes."""
    from workflow.scanin_runner import sample_margin
    cht = _CHT + "  X GS0 GS23 _ _ 20 40 10 300 21 0\n"
    out = cht_with_sample_area(cht, frac)
    m1 = sample_margin(40, 40, frac)
    m2 = sample_margin(20, 40, frac)
    assert f"X A01 A01 _ _ {40 - 2 * m1:g} {40 - 2 * m1:g} {20 + m1:g} {20 + m1:g}" in out
    assert f"X GS0 GS23 _ _ {20 - 2 * m2:g} {40 - 2 * m2:g} {10 + m2:g} {300 + m2:g} 21 0" in out
    # Fiducials and the reference are untouched; increments (pitch) kept.
    assert "F _ _ 0 0 200 0 200 200 0 200" in out
    assert "EXPECTED XYZ 4" in out
    assert "BOX_SHRINK 0.0" in out and "BOX_SHRINK 6.000" not in out


def test_knuts_cmp4_measurement_is_exact():
    """Knut measured a 230×230 px patch's 60 % sample box at 180×180 px
    (61.25 % area) on the CMP-4 demo (#119). The mathematical box is
    178.15 px per side = 31 737 px² = 59.99…% — exactly the set fraction;
    the extra ~2 px in a screenshot is the 1.4 px outline, which QPainter
    strokes centred on the boundary. This pins the arithmetic at his exact
    numbers."""
    from workflow.scanin_runner import sample_margin
    m = sample_margin(230.0, 230.0, 0.6)
    side = 230.0 - 2 * m
    assert abs(side - 178.1572) < 1e-3
    assert abs(side * side / (230.0 * 230.0) - 0.6) < 1e-12


def test_full_area_still_pins_baked_box_shrink():
    """#119 (Knut's ChromIQ_scanner_target_480): every ChromIQ chart .cht
    carries a baked-in default BOX_SHRINK (a read margin for third-party use
    of the sidecar). At a 100 % sample area the boxes stay untouched but the
    baked shrink must STILL be pinned to 0 — otherwise "100 %" silently read
    ≈ 50 % of each patch."""
    out = cht_with_sample_area(_CHT, 1.0)
    assert "  X A01 A01 _ _ 40 40 20 20 0 0" in out    # boxes untouched
    assert "BOX_SHRINK 0.0" in out and "BOX_SHRINK 6.000" not in out


def test_marquee_sample_fraction_clamps(qtbot=None):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.scan_grid_marquee import ScanGridMarquee
    m = ScanGridMarquee()
    m.set_sample_fraction(0.6)
    assert m._sample_frac == 0.6
    m.set_sample_fraction(5.0); assert m._sample_frac == 1.0     # clamp high
    m.set_sample_fraction(0.0); assert m._sample_frac == 0.05    # clamp low
