"""Tests for the TI2 layout-editor core (workflow/ti2_relayout.py).

Pure-logic tests run anywhere; the end-to-end test that shells out to printtarg
is skipped when ArgyllCMS isn't installed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from workflow import ti2_relayout as R

ARGYLL_BIN = Path("/Applications/Argyll/bin")
_HAS_ARGYLL = (ARGYLL_BIN / "printtarg").exists()
argyll = pytest.mark.skipif(not _HAS_ARGYLL, reason="ArgyllCMS not installed")


# --- a minimal but valid .ti2 fixture --------------------------------------
_TI2 = """CTI2

ORIGINATOR "test"
TARGET_INSTRUMENT "GretagMacbeth i1 Pro"
COLOR_REP "iRGB"
PAPER_SIZE "210.0x297.0"
APPROX_WHITE_POINT "95.1 100.0 108.8"

NUMBER_OF_FIELDS 8
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 4
BEGIN_DATA
1 "A1" 100.0 100.0 100.0 95.1 100.0 108.8
2 "A2" 0.0 0.0 0.0 0.0 0.0 0.0
3 "A3" 100.0 0.0 0.0 41.2 21.3 1.9
4 "B1" 0.0 0.0 100.0 18.0 7.2 95.0
END_DATA
"""


@pytest.fixture
def ti2(tmp_path: Path) -> Path:
    p = tmp_path / "src.ti2"
    p.write_text(_TI2)
    return p


# --- parsing ---------------------------------------------------------------
def test_parse_basics(ti2: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    assert len(spec.patches) == 4
    assert spec.dev_fields == ["RGB_R", "RGB_G", "RGB_B"]
    assert spec.has_xyz
    assert spec.instrument_flag == "i1"
    assert spec.paper_flag == "A4"
    assert spec.patches[0].loc == "A1"
    assert spec.patches[2].dev == (100.0, 0.0, 0.0)


def test_new_chart_from_scratch(tmp_path: Path):
    spec = R.ChartSpec.new("i1", "A4")
    assert spec.patches == []
    assert spec.dev_fields == ["RGB_R", "RGB_G", "RGB_B"]
    assert spec.paper_mm == (210.0, 297.0)
    prog = [(100, 100, 100), (0, 0, 0), (50, 50, 50)]  # built up by the editor
    out = R.write_ti1(spec, prog, tmp_path / "c.ti1")
    assert out.read_text().count("CTI1") == 3


def test_first_table_rgb_ignores_palette_tables(ti2: Path, tmp_path: Path):
    # write_ti1 emits 3 tables; _first_table_rgb must return only the patch list.
    spec = R.ChartSpec.from_ti2(ti2)
    prog = R.default_program(spec)
    out = R.write_ti1(spec, prog, tmp_path / "c.ti1")
    got = R._first_table_rgb(out)
    assert len(got) == len(prog)  # not len(prog) + 8 extremes + 9 combos
    assert got[2] == (100.0, 0.0, 0.0)


@argyll
def test_seed_from_targen_then_regenerate(tmp_path: Path):
    prog = R.seed_from_targen(ARGYLL_BIN, 30)
    assert 20 <= len(prog) <= 60          # ~requested count
    assert all(len(p) == 3 for p in prog)
    spec = R.ChartSpec.new("i1", "A4")    # new-from-scratch, seeded
    res = R.regenerate(spec, prog, tmp_path, ARGYLL_BIN)
    R.assert_data_integrity(prog, res.ti2)


def test_instrument_and_paper_maps():
    assert R.instrument_to_flag("X-Rite ColorMunki") == "CM"
    assert R.instrument_to_flag("GretagMacbeth SpectroScan") == "SS"
    assert R.instrument_to_flag(None) == "i1"
    assert R.paper_to_flag(210.0, 297.0) == "A4"
    assert R.paper_to_flag(297.0, 210.0) == "A4R"
    assert R.paper_to_flag(123.0, 456.0) == "123x456"  # custom fallback


# --- .ti1 synthesis --------------------------------------------------------
def test_write_ti1_three_tables_and_order(ti2: Path, tmp_path: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    dev_values = list(reversed(R.default_program(spec)))  # reverse order
    out = R.write_ti1(spec, dev_values, tmp_path / "c.ti1")
    text = out.read_text()
    # printtarg requires three CGATS tables
    assert text.count("CTI1") == 3
    assert "DENSITY_EXTREME_VALUES" in text
    assert "DEVICE_COMBINATION_VALUES" in text
    # first data patch must be the source's last (order honoured)
    main = text.split("DENSITY_EXTREME_VALUES")[0]
    first_row = main.split("BEGIN_DATA\n")[1].splitlines()[0]
    assert first_row.split()[1:4] == ["0.0000", "0.0000", "100.0000"]


def test_write_ti1_recolours_a_patch(ti2: Path, tmp_path: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    prog = R.default_program(spec)
    prog[0] = (12.0, 34.0, 56.0)  # recolour first patch
    out = R.write_ti1(spec, prog, tmp_path / "c.ti1")
    main = out.read_text().split("DENSITY_EXTREME_VALUES")[0]
    first_row = main.split("BEGIN_DATA\n")[1].splitlines()[0]
    assert first_row.split()[1:4] == ["12.0000", "34.0000", "56.0000"]


def test_write_ti1_custom_palette_lands_in_extremes(ti2: Path, tmp_path: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    pal = ((100, 100, 100), (30, 70, 100), (0, 0, 0))
    out = R.write_ti1(spec, R.default_program(spec), tmp_path / "c.ti1",
                      spacer_palette=pal)
    extremes = out.read_text().split("DENSITY_EXTREME_VALUES")[1]
    assert 'DENSITY_EXTREME_VALUES "3"' in out.read_text()
    assert "30.0000 70.0000 100.0000" in extremes


def test_write_ti1_rejects_non_rgb(tmp_path: Path):
    spec = R.ChartSpec(
        patches=[R.Patch("1", None, (0, 0, 0, 0), None)],
        dev_fields=["CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"],
        has_xyz=False, color_rep="CMYK", white_point=None,
        instrument_flag="i1", paper_flag="A4", paper_mm=(210.0, 297.0),
    )
    with pytest.raises(NotImplementedError):
        R.write_ti1(spec, [(0, 0, 0, 0)], tmp_path / "c.ti1")


# --- spacer segmentation + recolour (synthetic, no Argyll) -----------------
def test_segment_spacers_counts_components():
    mask = np.zeros((40, 40), dtype=bool)
    mask[5:9, 2:30] = True     # spacer 1
    mask[20:24, 2:30] = True   # spacer 2
    spacers = R.segment_spacers(mask, page=0)
    assert len(spacers) == 2
    areas = sorted(s.area for s in spacers)
    assert areas == [4 * 28, 4 * 28]
    assert all(s.page == 0 for s in spacers)


def test_recolor_only_touches_spacers(tmp_path: Path):
    import tifffile
    img = np.full((20, 20, 3), 200, dtype=np.uint8)
    src = tmp_path / "p.tif"
    tifffile.imwrite(str(src), img, photometric="rgb")
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:8, 5:15] = True
    spacers = R.segment_spacers(mask, page=0)
    out = tmp_path / "p_rec.tif"
    R.recolor_spacers(src, spacers, (255, 0, 0), out)
    R.assert_patches_untouched(src, out, mask)
    after = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(out).convert("RGB"))
    assert tuple(after[6, 6]) == (255, 0, 0)      # spacer recoloured
    assert tuple(after[0, 0]) == (200, 200, 200)  # patch untouched


# --- end-to-end through printtarg ------------------------------------------
@argyll
def test_regenerate_roundtrip_and_palette(ti2: Path, tmp_path: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    dev_values = list(reversed(R.default_program(spec)))
    pal = ((100, 100, 100), (30, 70, 100), (100, 70, 30), (70, 30, 100),
           (30, 100, 70), (100, 30, 70), (70, 100, 30), (0, 0, 0))
    res = R.regenerate(spec, dev_values, tmp_path, ARGYLL_BIN, spacer_palette=pal)
    R.assert_data_integrity(dev_values, res.ti2)
    assert len(res.tiffs) == len(res.bw_tiffs) >= 1
    mask = R.spacer_mask(res.tiffs[0], res.bw_tiffs[0])
    assert mask.sum() > 0
    spacers = R.segment_spacers(mask, page=0)
    assert len(spacers) > 0


@argyll
def test_recolour_patch_lands_in_regenerated_ti2(ti2: Path, tmp_path: Path):
    spec = R.ChartSpec.from_ti2(ti2)
    prog = R.default_program(spec)
    prog[2] = (20.0, 80.0, 75.0)  # recolour a patch to a custom teal
    res = R.regenerate(spec, prog, tmp_path, ARGYLL_BIN)
    R.assert_data_integrity(prog, res.ti2)  # the teal is present in the output
    new = R.ChartSpec.from_ti2(res.ti2)
    # printtarg snaps to 8-bit, so allow a tolerance of one code (~0.4/100).
    assert any(all(abs(a - b) <= 0.4 for a, b in zip(p.dev, (20.0, 80.0, 75.0)))
               for p in new.patches)
