"""Tests for the colorspace-aware i1Profiler patch-set export.

Uses synthetic TI1 fixtures (no Argyll dependency) covering the three
colorspaces ChromIQ exports: RGB, CMYK and CMYK+N.
"""
from __future__ import annotations

from pathlib import Path
from xml.dom.minidom import parseString

import pytest

from workflow.i1profiler_export import (
    export_from_ti1,
    ink_colorspace_enum,
    parse_ti1,
    write_pxf,
)

RGB_TI1 = """CTI1

COLOR_REP "iRGB"

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 100.0000 100.0000 100.0000 95.0 100.0 108.0
2 0.00000 0.00000 0.00000 1.0 1.0 1.0
END_DATA
"""

CMYK_TI1 = """CTI1

COLOR_REP "CMYK"

NUMBER_OF_FIELDS 8
BEGIN_DATA_FORMAT
SAMPLE_ID CMYK_C CMYK_M CMYK_Y CMYK_K XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 0 69.8 0 0 50 50 50
2 100 100 100 100 1 1 1
END_DATA
"""

CMYKOGV_TI1 = """CTI1

COLOR_REP "CMYKOGV"

NUMBER_OF_FIELDS 11
BEGIN_DATA_FORMAT
SAMPLE_ID CMYKOGV_C CMYKOGV_M CMYKOGV_Y CMYKOGV_K CMYKOGV_O CMYKOGV_G CMYKOGV_V XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 0 0 0 0 100 0 100 50 50 50
2 30 20 20 0 0 0 0 40 40 40
END_DATA
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# --- parsing ---------------------------------------------------------------


def test_parse_detects_kinds_and_channels(tmp_path):
    rgb = parse_ti1(_write(tmp_path, "rgb.ti1", RGB_TI1))
    assert rgb.kind == "RGB"
    assert rgb.channels == ["R", "G", "B"]
    assert rgb.extras == []

    cmyk = parse_ti1(_write(tmp_path, "cmyk.ti1", CMYK_TI1))
    assert cmyk.kind == "CMYK"
    assert cmyk.channels == ["C", "M", "Y", "K"]
    assert cmyk.extras == []

    ogv = parse_ti1(_write(tmp_path, "ogv.ti1", CMYKOGV_TI1))
    assert ogv.kind == "CMYKPLUSN"
    assert ogv.channels == ["C", "M", "Y", "K", "O", "G", "V"]
    assert ogv.extras == ["O", "G", "V"]


def test_parse_drops_xyz_and_keeps_native_scale(tmp_path):
    cmyk = parse_ti1(_write(tmp_path, "cmyk.ti1", CMYK_TI1))
    sid, vals = cmyk.rows[0]
    assert sid == 1
    assert set(vals) == {"C", "M", "Y", "K"}  # XYZ dropped
    assert vals["M"] == pytest.approx(69.8)  # 0..100 preserved


def test_unsupported_colorspace_raises(tmp_path):
    grey = RGB_TI1.replace("RGB_R RGB_G RGB_B", "K_K").replace(
        "NUMBER_OF_FIELDS 7", "NUMBER_OF_FIELDS 2"
    ).replace("100.0000 100.0000 100.0000 95.0 100.0 108.0", "50").replace(
        "0.00000 0.00000 0.00000 1.0 1.0 1.0", "0"
    ).replace('COLOR_REP "iRGB"', 'COLOR_REP "K"')
    with pytest.raises(ValueError, match="unsupported colorspace"):
        parse_ti1(_write(tmp_path, "grey.ti1", grey))


# --- enum ------------------------------------------------------------------


def test_ink_colorspace_enum():
    assert ink_colorspace_enum(4) == (9, True)    # CMYK
    assert ink_colorspace_enum(7) == (15, True)   # CMYKOGV
    assert ink_colorspace_enum(5) == (11, False)  # CMYK+1 extrapolated
    assert ink_colorspace_enum(8) == (17, False)  # CMYK+4 extrapolated


# --- RGB export ------------------------------------------------------------


def test_rgb_export_scales_to_255_and_stays_minimal(tmp_path):
    txt, pxf = export_from_ti1(_write(tmp_path, "rgb.ti1", RGB_TI1))
    assert txt is not None and txt.suffix == ".txt"
    pxf_text = pxf.read_text()
    parseString(pxf_text)  # well-formed
    assert "<cc:ColorRGB" in pxf_text
    assert "<cc:R>255</cc:R>" in pxf_text  # 100 * 2.55 -> 255
    assert 'ColorSpace="RGB"' in pxf_text
    # RGB stays the minimal shipped form: no ProfileSettings / ColorSpecification
    assert "<ProfileSettings" not in pxf_text
    assert "ColorSpecificationCollection" not in pxf_text
    # .txt is CGATS.5 on the 0..255 scale
    txt_text = txt.read_text()
    assert txt_text.startswith("CGATS.5")
    assert "RGB_R RGB_G RGB_B" in txt_text
    assert "255.0000" in txt_text


# --- CMYK export -----------------------------------------------------------


def test_cmyk_export(tmp_path):
    txt, pxf = export_from_ti1(_write(tmp_path, "cmyk.ti1", CMYK_TI1))
    assert txt is not None
    pxf_text = pxf.read_text()
    parseString(pxf_text)
    assert "<cc:ColorCMYK " in pxf_text
    assert "ColorCMYKPlusN" not in pxf_text
    assert "<cc:Magenta>69.8</cc:Magenta>" in pxf_text  # 0..100 preserved
    assert 'ColorSpace="CMYK"' in pxf_text
    assert "<ColorSpace>9</ColorSpace>" in pxf_text
    assert "<InkLimit>400</InkLimit>" in pxf_text
    assert "ColorSpecificationCollection" in pxf_text
    # CMYK .txt is on the 0..100 scale
    assert "CMYK_C CMYK_M CMYK_Y CMYK_K" in txt.read_text()


# --- CMYK+N export ---------------------------------------------------------


def test_cmykogv_export(tmp_path):
    txt, pxf = export_from_ti1(_write(tmp_path, "ogv.ti1", CMYKOGV_TI1))
    assert txt is None  # extended gamut is .pxf only
    pxf_text = pxf.read_text()
    parseString(pxf_text)
    assert "<cc:ColorCMYKPlusN" in pxf_text
    for ink in ("Orange", "Green", "Violet"):
        assert f"<cc:Name>{ink}</cc:Name>" in pxf_text
    assert 'ColorSpace="CMYK + 3"' in pxf_text
    assert "<ColorSpace>15</ColorSpace>" in pxf_text
    assert "<InkLimit>700</InkLimit>" in pxf_text
    assert "<InkGeneration_3>" in pxf_text
    assert "<InkGeneration_4>" not in pxf_text
    # PLUS_n ink definitions with reference Lab
    assert 'PLUS_1_COLOR="Orange|71|33|71' in pxf_text
    assert 'PLUS_3_COLOR="Violet|39|75|-95' in pxf_text


def test_cmykogv_spotcolor_percentages(tmp_path):
    _txt, pxf = export_from_ti1(_write(tmp_path, "ogv.ti1", CMYKOGV_TI1))
    dom = parseString(pxf.read_text())
    first = dom.getElementsByTagName("cc:ColorCMYKPlusN")[0]
    spots = first.getElementsByTagName("cc:SpotColor")
    by_name = {
        s.getElementsByTagName("cc:Name")[0].firstChild.data:
        s.getElementsByTagName("cc:Percentage")[0].firstChild.data
        for s in spots
    }
    # patch 1 is O=100, G=0, V=100
    assert by_name == {"Orange": "100", "Green": "0", "Violet": "100"}
