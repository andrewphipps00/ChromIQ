"""Patch-label handling ported from rectarg (workflow/scanner_labels.py):
range expansion, row+col combination (alpha-first, 2A1), padding-tolerant
normalisation, and expansion of a rectarg area-format .cht into a per-patch one.
"""
from __future__ import annotations

from workflow.scanner_labels import (
    generate_labels, make_patch_label, normalize_sid, label_mode_for,
    expand_rectarg_cht,
)


def test_generate_labels_ranges():
    assert generate_labels("A", "D") == ["A", "B", "C", "D"]
    # rectarg pads a numeric range to the widest endpoint: 1..19 → 01..19.
    assert generate_labels("1", "19")[:3] == ["01", "02", "03"]
    assert generate_labels("01", "19")[:3] == ["01", "02", "03"]
    assert generate_labels("1", "9") == [str(i) for i in range(1, 10)]  # width 1
    assert generate_labels("2A", "2D") == ["2A", "2B", "2C", "2D"]  # numeric prefix
    assert generate_labels("GS00", "GS02") == ["GS00", "GS01", "GS02"]
    assert generate_labels("A", "AB")[-3:] == ["Z", "AA", "AB"]   # Excel wrap
    assert generate_labels("_", "_") == []                        # disabled axis


def test_make_patch_label_alpha_first():
    # letters in the column, numbers in the row → "A1" (CMP area Y)
    assert make_patch_label("1", "A") == "A1"
    # letters in the row, numbers in the column → still "A1" (QPcard-style)
    assert make_patch_label("A", "1") == "A1"
    # numeric-prefixed second area keeps its order via label_mode
    assert make_patch_label("1", "2A", mode="prefixed_x") == "2A1"


def test_normalize_sid_padding():
    assert normalize_sid("A01") == "A1" == normalize_sid("A001")
    assert normalize_sid("GS01") == "GS1"
    assert normalize_sid("2A01") == "2A1"
    assert normalize_sid("007") == "7"
    assert normalize_sid('"a1"'.strip('"').upper()) == "A1"


def test_label_mode_detection():
    assert label_mode_for("2A", "2D", "1", "19") == "prefixed_x"
    assert label_mode_for("A", "Z", "1", "19") is None


def test_expand_rectarg_cht_cmp_side_by_side():
    """A minimal CMP-like two-area rectarg cht expands to per-patch boxes with the
    real F line kept and 2A-prefixed names — the case that broke scanin."""
    rect = (
        "BOXES 3\n"
        "  F _ _ 0 0 3300 0 3300 2200 0 2200\n"
        "  Y A B 1 2 100 100 150 150 100 100\n"
        "  X 2A 2B 1 2 100 100 2750 150 100 100\n"
        "BOX_SHRINK 12\n"
    )
    out = expand_rectarg_cht(rect)
    names = [l.split()[1] for l in out.splitlines() if l.strip().startswith("X ")]
    # area Y → A1,A2,B1,B2 ; area X → 2A1,2A2,2B1,2B2
    assert set(names) == {"A1", "A2", "B1", "B2", "2A1", "2A2", "2B1", "2B2"}
    assert "  F _ _ 0 0 3300 0 3300 2200 0 2200" in out   # real fiducials kept
    assert "XLIST" in out and "YLIST" in out              # scanin needs both


def test_expand_rectarg_cht_filters_empty_grid():
    """When an EXPECTED list names only a subset of a full grid (Hutchcolor), the
    empty positions are dropped — no phantom boxes."""
    rect = (
        "BOXES 1\n"
        "  F _ _ 0 0 300 0 300 300 0 300\n"
        "  Y 1 2 A B 100 100 50 50 50 50\n"
        "EXPECTED XYZ 3\n"
        "A1 40 40 40\nA2 40 40 40\nB1 40 40 40\n"     # B2 omitted
    )
    out = expand_rectarg_cht(rect)
    names = {l.split()[1] for l in out.splitlines() if l.strip().startswith("X ")}
    assert names == {"A1", "A2", "B1"}                 # B2 filtered out
