"""The engine can emit an ArgyllCMS .cht recognition template from its exact
geometry (#93, Knut)."""
import random

from workflow.layout_engine import cht_writer, chart as le_chart
import workflow.ti2_relayout as R


def test_build_cht_text_structure():
    boxes = [
        {"loc": "A1", "x": 10.0, "y": 200.0, "w": 8.0, "h": 8.0},
        {"loc": "A2", "x": 10.0, "y": 190.0, "w": 8.0, "h": 8.0},
        {"loc": "B1", "x": 18.0, "y": 200.0, "w": 8.0, "h": 8.0},
    ]
    expected = [("A1", 95.0, 100.0, 108.0), ("A2", 0.0, 0.0, 0.0),
                ("B1", 41.0, 21.0, 1.9)]
    txt = cht_writer.build_cht_text(boxes, expected)
    # 3 patch boxes + 1 fiducial (F) line = BOXES 4
    assert "BOXES 4" in txt
    assert "  X A1 A1 _ _ 8.000000 8.000000 10.000000 200.000000 0 0" in txt
    assert "BOX_SHRINK" in txt and "REF_ROTATION 0.0" in txt
    # fiducial line: patch-area corners TL,TR,BR,BL over x∈[10,26], y∈[190,208]
    assert ("  F _ _ 10.000000 208.000000 26.000000 208.000000 "
            "26.000000 190.000000 10.000000 190.000000") in txt
    # no fiducials on request → back to BOXES 3
    assert "BOXES 3" in cht_writer.build_cht_text(boxes, expected, emit_fiducials=False)
    assert "EXPECTED XYZ 3" in txt
    assert "  A1 95.000000 100.000000 108.000000" in txt
    # XLIST: vertical edges at x = 10, 18, 26 → 3 unique positions
    assert [l for l in txt.splitlines() if l.startswith("XLIST")][0] == "XLIST 3"
    # YLIST: horizontal edges at y = 190, 198, 200, 208 → 4 unique
    assert [l for l in txt.splitlines() if l.startswith("YLIST")][0] == "YLIST 4"


def test_boxes_from_patch_rects_flips_origin():
    rects = [{"page": 0, "slot": 0, "loc": "A1", "x": 0, "y": 0, "w": 100, "h": 100},
             {"page": 1, "slot": 1, "loc": "A1", "x": 0, "y": 0, "w": 100, "h": 100}]
    boxes = cht_writer.boxes_from_patch_rects(rects, 297.0, 100, page=0)  # 1px=0.254mm
    assert len(boxes) == 1                       # only page 0
    b = boxes[0]
    assert abs(b["w"] - 25.4) < 1e-6 and abs(b["h"] - 25.4) < 1e-6
    assert abs(b["x"] - 0.0) < 1e-6
    assert abs(b["y"] - (297.0 - 25.4)) < 1e-6   # top-left flipped to bottom-left


def test_build_chart_emits_cht(tmp_path):
    random.seed(3)
    prog = [(random.random() * 100, random.random() * 100, random.random() * 100)
            for _ in range(90)]
    R.write_ti1(R.ChartSpec.new("i1", "A4"), prog, tmp_path / "s.ti1")
    res = le_chart.build_chart(str(tmp_path / "s.ti1"), tmp_path / "chart",
                               instrument="i1", paper="A4", dpi=120,
                               randomize=False, emit_cht=True)
    assert res.cht_paths and res.cht_paths[0].is_file()
    txt = res.cht_paths[0].read_text()
    # one X box and one EXPECTED row per printed patch slot (incl. grid padding)
    n_slots = res.layout.total_patches
    assert n_slots >= 90
    n_x = sum(1 for l in txt.splitlines() if l.strip().startswith("X "))
    n_exp = int([l for l in txt.splitlines() if l.startswith("EXPECTED")][0].split()[-1])
    assert n_x == n_exp == n_slots
    # one fiducial (F) line is prepended, so BOXES counts it too
    assert sum(1 for l in txt.splitlines() if l.strip().startswith("F ")) == 1
    assert f"BOXES {n_slots + 1}" in txt

    # Boxes must sit inside the paper (bottom-left origin, A4 = 210×297).
    for line in (l for l in txt.splitlines() if l.strip().startswith("X ")):
        _, _, _, _, _, w, h, x, y, _, _ = line.split()
        assert 0 <= float(x) <= 210 and 0 <= float(y) <= 297
