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


def test_cht_shrinks_boxes_per_axis_keeping_aspect():
    """#119 (Knut): the read zone must keep each patch's own height-to-width
    relationship, so the boxes themselves are shrunk √f per axis (centres
    kept) and BOX_SHRINK — which insets all sides by the same AMOUNT and
    distorts non-square patches — is pinned to 0."""
    out = cht_with_sample_area(_CHT, 0.6)
    lin = math.sqrt(0.6)
    w = 40 * lin
    ox = 20 + 40 * (1 - lin) / 2
    assert f"X A01 A01 _ _ {w:g} {w:g} {ox:g}" in out
    assert "BOX_SHRINK 0.0" in out and "BOX_SHRINK 6.000" not in out
    # Fiducials and the reference are untouched.
    assert "F _ _ 0 0 200 0 200 200 0 200" in out
    assert out.count("\n  X ") == 4 and "EXPECTED XYZ 4" in out
    # A non-square box keeps its aspect: 20×40 at 60 % area → (20·√f)×(40·√f).
    tall = "\n\nBOXES 1\n  X GS0 GS0 _ _ 20 40 10 10 0 0\n"
    out2 = cht_with_sample_area(tall, 0.6)
    assert f"{20 * lin:g} {40 * lin:g}" in out2
    assert "BOX_SHRINK 0.0" in out2      # inserted when absent


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
