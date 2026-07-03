"""Patch sample-area control: the % of each patch scanin reads maps to the
chart's BOX_SHRINK (Knut's request), and the marquee draws the inner read zone."""
import math

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


def test_cht_rewrites_box_shrink():
    out = cht_with_sample_area(_CHT, 0.6)
    want = round(40 * (1 - math.sqrt(0.6)) / 2, 3)
    assert f"BOX_SHRINK {want:.3f}" in out
    assert "BOX_SHRINK 6.000" not in out
    # Boxes and reference are untouched — only the read zone changes.
    assert out.count("\n  X ") == 4 and "EXPECTED XYZ 4" in out
    # A file with no BOX_SHRINK line gets one inserted.
    bare = "\n\nBOXES 1\n  X A A _ _ 40 40 10 10 0 0\n"
    assert "BOX_SHRINK" in cht_with_sample_area(bare, 0.5)


def test_marquee_sample_fraction_clamps(qtbot=None):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.scan_grid_marquee import ScanGridMarquee
    m = ScanGridMarquee()
    m.set_sample_fraction(0.6)
    assert m._sample_frac == 0.6
    m.set_sample_fraction(5.0); assert m._sample_frac == 1.0     # clamp high
    m.set_sample_fraction(0.0); assert m._sample_frac == 0.05    # clamp low
