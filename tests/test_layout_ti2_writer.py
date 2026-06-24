"""Tests for the CGATS .ti2 writer."""
import re

from workflow.layout_engine import geometry, instruments
from workflow.layout_engine import ti2_writer as tw


RGB_FIELDS = ["RGB_R", "RGB_G", "RGB_B"]


def _patches(n):
    # deterministic dummy RGB+XYZ patches as (device, xyz)
    return [((float(i % 100), float((i * 7) % 100), float((i * 13) % 100)),
             (50.0, 50.0, 50.0)) for i in range(n)]


def _build(key="i1", npat=60, **kw):
    geom = instruments.build(key)
    lay = geometry.compute(geom, 210.0, 297.0, npat)
    text = tw.build_ti2_text(
        _patches(npat), RGB_FIELDS, lay, geom, color_rep="iRGB",
        paper_w_mm=210.0, paper_h_mm=297.0,
        created="Wed Jun 24 00:00:00 2026", **kw,
    )
    return geom, lay, text


def _sets(text):
    return int(re.search(r"NUMBER_OF_SETS (\d+)", text).group(1))


def _data_rows(text):
    body = text.split("BEGIN_DATA\n", 1)[1].split("END_DATA", 1)[0]
    return [ln for ln in body.splitlines() if ln.strip()]


def test_header_and_counts():
    geom, lay, text = _build(seed=42)
    assert _sets(text) == lay.total_patches == 63
    assert 'RANDOM_START "42"' in text
    assert 'STEPS_IN_PASS "21"' in text
    assert 'PASSES_IN_STRIPS2 "3"' in text
    assert 'TARGET_INSTRUMENT "GretagMacbeth i1 Pro"' in text
    assert 'INDEX_ORDER "STRIP_THEN_PATCH"' in text
    assert len(_data_rows(text)) == 63


def test_no_randomize_uses_chart_id_and_sequential_loc():
    geom, lay, text = _build(seed=25, randomize=False)
    assert 'CHART_ID "25"' in text
    assert 'RANDOM_START' not in text
    rows = _data_rows(text)
    locs = [re.search(r'^\d+ "([^"]+)"', r).group(1) for r in rows]
    assert locs[:3] == ["A1", "A2", "A3"]


def test_seed_reproducible():
    _, _, a = _build(seed=42)
    _, _, b = _build(seed=42)
    assert a == b


def test_dtp41_extra_keywords():
    _, _, text = _build(key="41", seed=1)
    assert "PATCH_LENGTH" in text
    assert "GAP_LENGTH" in text
    assert "TRAILER_LENGTH" in text


def test_padding_rows_are_media():
    # i1/A4/60 pads 3 media patches at the end (canonical order, before shuffle).
    geom, lay, text = _build(seed=42, randomize=False)
    assert lay.padding == 3
    rows = _data_rows(text)
    # last 3 canonical rows are media white (100 100 100)
    for r in rows[-3:]:
        vals = r.split('"')[2].split()
        assert vals[0:3] == ["100.00000", "100.00000", "100.00000"]


def test_cmyk_colorspace_passthrough():
    # A CMYK target writes CMYK_* fields and COLOR_REP "CMYK".
    fields = ["CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"]
    npat = 60
    patches = [((float(i % 100), 0.0, 0.0, 0.0), (40.0, 42.0, 38.0)) for i in range(npat)]
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, npat)
    text = tw.build_ti2_text(
        patches, fields, lay, geom, color_rep="CMYK",
        paper_w_mm=210.0, paper_h_mm=297.0,
        media=((0.0, 0.0, 0.0, 0.0), (95.0, 100.0, 108.0)), seed=1,
    )
    assert 'COLOR_REP "CMYK"' in text
    assert "SAMPLE_ID SAMPLE_LOC CMYK_C CMYK_M CMYK_Y CMYK_K XYZ_X XYZ_Y XYZ_Z" in text
    assert "NUMBER_OF_FIELDS 9" in text          # 2 + 4 device + 3 XYZ
    assert _sets(text) == lay.total_patches
