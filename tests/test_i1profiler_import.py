"""Tests for the i1Profiler -> Argyll TI1 import (reverse of the export).

Synthetic .pxf / .cgats fixtures, no Argyll dependency. One test additionally
parses the emitted TI1 back to confirm the three-table structure printtarg
requires; the live printtarg acceptance is verified manually (see the tool's
plan), not here, since it needs the Argyll binaries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workflow.i1profiler_import import (
    WHITE_XYZ,
    import_to_ti1,
    parse_cgats,
    parse_pxf,
    srgb_to_xyz,
    write_ti1,
    RgbPatch,
)

# Two RGB patches (white, black) plus a mid grey, on the i1Profiler 0..255 scale.
PXF = """<?xml version='1.0' encoding='utf-8'?>
<cc:CxF xmlns:cc="http://colorexchangeformat.com/CxF3-core">
  <cc:Resources>
    <cc:ObjectCollection>
      <cc:Object ObjectType="Target" Name="Target1" Id="c1">
        <cc:DeviceColorValues>
          <cc:ColorRGB ColorSpecification="sRGB">
            <cc:R>255</cc:R><cc:G>255</cc:G><cc:B>255</cc:B>
          </cc:ColorRGB>
        </cc:DeviceColorValues>
      </cc:Object>
      <cc:Object ObjectType="Target" Name="Target2" Id="c2">
        <cc:DeviceColorValues>
          <cc:ColorRGB ColorSpecification="sRGB">
            <cc:R>0</cc:R><cc:G>0</cc:G><cc:B>0</cc:B>
          </cc:ColorRGB>
        </cc:DeviceColorValues>
      </cc:Object>
      <cc:Object ObjectType="Target" Name="Target3" Id="c3">
        <cc:DeviceColorValues>
          <cc:ColorRGB ColorSpecification="sRGB">
            <cc:R>128</cc:R><cc:G>128</cc:G><cc:B>128</cc:B>
          </cc:ColorRGB>
        </cc:DeviceColorValues>
      </cc:Object>
    </cc:ObjectCollection>
  </cc:Resources>
</cc:CxF>
"""

# CGATS table, 0..255 scale (the i1Profiler / PKPatches convention).
CGATS_255 = """CTI1

COLOR_REP "RGB"

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 255 0 255 0 0 0
2 0 255 0 0 0 0
END_DATA
"""

# CGATS table already on the Argyll 0..100 scale (no rescale expected).
CGATS_100 = """CGATS.5

NUMBER_OF_FIELDS 4
BEGIN_DATA_FORMAT
SampleID RGB_R RGB_G RGB_B
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 100 0 0
2 0 100 0
END_DATA
"""

CMYK_PXF = """<?xml version='1.0' encoding='utf-8'?>
<cc:CxF xmlns:cc="http://colorexchangeformat.com/CxF3-core">
  <cc:Resources>
    <cc:ObjectCollection>
      <cc:Object ObjectType="Target" Name="Target1" Id="c1">
        <cc:DeviceColorValues>
          <cc:ColorCMYK ColorSpecification="Unknown">
            <cc:Cyan>0</cc:Cyan><cc:Magenta>0</cc:Magenta>
            <cc:Yellow>0</cc:Yellow><cc:Black>100</cc:Black>
          </cc:ColorCMYK>
        </cc:DeviceColorValues>
      </cc:Object>
    </cc:ObjectCollection>
  </cc:Resources>
</cc:CxF>
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# --- sRGB -> XYZ -----------------------------------------------------------


def test_white_maps_to_d65_white_point():
    x, y, z = WHITE_XYZ
    assert y == pytest.approx(100.0)
    assert x == pytest.approx(95.05, abs=0.1)
    assert z == pytest.approx(108.9, abs=0.2)


def test_srgb_matches_targen_neutral():
    """targen's TI1 stores Y=93.687 for a 97.14 % device grey; the sRGB
    estimate must land within a fraction of a unit of that."""
    _, y, _ = srgb_to_xyz(97.14286, 97.14286, 97.14286)
    assert y == pytest.approx(93.687, abs=0.2)


# --- parsing ---------------------------------------------------------------


def test_parse_pxf_rescales_255_to_100(tmp_path):
    patches = parse_pxf(_write(tmp_path, "p.pxf", PXF))
    assert len(patches) == 3
    assert (patches[0].r, patches[0].g, patches[0].b) == pytest.approx((100, 100, 100))
    assert (patches[1].r, patches[1].g, patches[1].b) == pytest.approx((0, 0, 0))
    assert patches[2].r == pytest.approx(128 * 100 / 255, abs=1e-6)


def test_parse_pxf_preserves_order(tmp_path):
    patches = parse_pxf(_write(tmp_path, "p.pxf", PXF))
    # white, black, grey — exactly as authored.
    assert patches[0].r > 99 and patches[1].r < 1 and 49 < patches[2].r < 51


def test_parse_cgats_detects_255_scale(tmp_path):
    patches = parse_cgats(_write(tmp_path, "c.cgats", CGATS_255))
    assert (patches[0].r, patches[0].g, patches[0].b) == pytest.approx((100, 0, 100))


def test_parse_cgats_keeps_100_scale(tmp_path):
    patches = parse_cgats(_write(tmp_path, "c.txt", CGATS_100))
    assert (patches[0].r, patches[0].g, patches[0].b) == pytest.approx((100, 0, 0))


def test_parse_pxf_rejects_cmyk(tmp_path):
    with pytest.raises(ValueError, match="RGB patch sets only"):
        parse_pxf(_write(tmp_path, "cmyk.pxf", CMYK_PXF))


def test_parse_cgats_rejects_non_rgb(tmp_path):
    no_rgb = CGATS_100.replace("RGB_R RGB_G RGB_B", "CMYK_C CMYK_M CMYK_Y")
    with pytest.raises(ValueError, match="RGB"):
        parse_cgats(_write(tmp_path, "x.txt", no_rgb))


# --- TI1 emission ----------------------------------------------------------


def test_write_ti1_has_three_tables(tmp_path):
    out = write_ti1([RgbPatch(100, 100, 100), RgbPatch(0, 0, 0)], tmp_path / "o.ti1")
    text = out.read_text()
    assert text.count("CTI1") == 3
    assert text.count("BEGIN_DATA\n") == 3
    assert 'DENSITY_EXTREME_VALUES "8"' in text
    assert 'DEVICE_COMBINATION_VALUES "9"' in text
    assert 'COLOR_REP "RGB"' in text


def test_write_ti1_counts_white_and_black(tmp_path):
    out = write_ti1(
        [RgbPatch(100, 100, 100), RgbPatch(100, 100, 100), RgbPatch(0, 0, 0),
         RgbPatch(50, 50, 50)],
        tmp_path / "o.ti1",
    )
    text = out.read_text()
    assert 'WHITE_COLOR_PATCHES "2"' in text
    assert 'BLACK_COLOR_PATCHES "1"' in text


def test_write_ti1_main_table_patch_count(tmp_path):
    patches = [RgbPatch(i, i, i) for i in range(10)]
    out = write_ti1(patches, tmp_path / "o.ti1")
    # First (main) table's set count is the input length, not the fixed aux tables.
    main = out.read_text().split("CTI1", 2)[1]
    assert "NUMBER_OF_SETS 10" in main


def test_black_patch_xyz_floored(tmp_path):
    """Pure black must not emit a zero-luminance patch (printtarg divides by Y)."""
    out = write_ti1([RgbPatch(0, 0, 0)], tmp_path / "o.ti1")
    main = out.read_text().split("CTI1", 2)[1]
    data = main.split("BEGIN_DATA\n")[1].split("END_DATA")[0].strip()
    cols = data.split()
    assert cols[4:7] == ["1.0000", "1.0000", "1.0000"]


# --- dispatcher ------------------------------------------------------------


def test_import_dispatches_pxf(tmp_path):
    out, n = import_to_ti1(_write(tmp_path, "in.pxf", PXF), tmp_path / "out.ti1")
    assert n == 3 and out.read_text().count("CTI1") == 3


def test_import_dispatches_cgats(tmp_path):
    out, n = import_to_ti1(_write(tmp_path, "in.cgats", CGATS_255), tmp_path / "out.ti1")
    assert n == 2


def test_import_detects_xml_despite_extension(tmp_path):
    """A CxF saved with a .cgats extension is still read as XML."""
    out, n = import_to_ti1(_write(tmp_path, "mislabelled.cgats", PXF), tmp_path / "out.ti1")
    assert n == 3


def test_roundtrip_export_then_import(tmp_path):
    """ti1 -> i1Profiler -> ti1 preserves device values (the common values)."""
    from workflow.i1profiler_export import export_from_ti1

    src = _write(
        tmp_path, "src.ti1",
        "CTI1\n\nCOLOR_REP \"RGB\"\n\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\n"
        "NUMBER_OF_SETS 2\nBEGIN_DATA\n"
        "1 100.0000 50.0000 0.0000 40 40 40\n"
        "2 0.0000 0.0000 0.0000 1 1 1\nEND_DATA\n",
    )
    _txt, pxf = export_from_ti1(src, tmp_path, base_name="round")
    patches = parse_pxf(pxf)
    # 50 % on a 0..100 device -> 128/255 on export -> ~50.2 back. Within rounding.
    assert patches[0].r == pytest.approx(100, abs=0.5)
    assert patches[0].g == pytest.approx(50, abs=0.5)
    assert patches[0].b == pytest.approx(0, abs=0.5)
