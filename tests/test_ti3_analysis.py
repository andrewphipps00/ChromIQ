"""Tests for the .ti3 measurement-analysis tool (parser, contrast, grey
balance, spectral integration)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from workflow import cie_data
from workflow.ti3_analysis import (
    Ti3ParseError,
    _Integrator,
    analyse_ti3,
    parse_ti3,
)


def _write_ti3(path: Path, rows: list[str], *, spectral: bool = False) -> None:
    if spectral:
        bands = [f"SPEC_{nm}" for nm in (400, 500, 600, 700)]
        fmt = "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z " + " ".join(bands)
        header = ('CTI3\n\nDEVICE_CLASS "OUTPUT"\nCOLOR_REP "iRGB_XYZ"\n'
                  'TARGET_INSTRUMENT "Test"\nSPECTRAL_BANDS "4"\n'
                  'SPECTRAL_START_NM "400.000000"\nSPECTRAL_END_NM "700.000000"\n')
    else:
        fmt = "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z"
        header = ('CTI3\n\nDEVICE_CLASS "OUTPUT"\nCOLOR_REP "iRGB_XYZ"\n'
                  'TARGET_INSTRUMENT "Test"\n')
    body = (f"{header}\nNUMBER_OF_FIELDS {len(fmt.split())}\nBEGIN_DATA_FORMAT\n"
            f"{fmt}\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS {len(rows)}\n"
            f"BEGIN_DATA\n" + "\n".join(rows) + "\nEND_DATA\n")
    path.write_text(body)


def test_parse_and_contrast(tmp_path: Path):
    # white (Y≈90), mid grey, black (Y≈1) — all device-neutral
    rows = [
        "1 100 100 100 86 90 75 ",
        "2 50 50 50 18 19 16 ",
        "3 0 0 0 0.9 1.0 1.1 ",
    ]
    p = tmp_path / "m.ti3"
    _write_ti3(p, rows)
    a = analyse_ti3(parse_ti3(p))
    assert a.data.n_patches == 3
    assert not a.data.has_spectral
    # contrast = Y_white / Y_black = 90 / 1 = 90
    assert a.contrast_ratio == pytest.approx(90.0, rel=1e-3)
    assert a.white_lab[0] > 95          # L* of Y=90 white
    assert a.black_lab[0] < 12          # L* of Y=1 black
    assert a.dynamic_range == pytest.approx(np.log10(90.0), rel=1e-3)
    assert a.n_neutral == 3             # all three are R=G=B


def test_grey_cast_direction(tmp_path: Path):
    # neutral patches pushed warm (higher X/lower Z → +a,+b)
    rows = [
        "1 100 100 100 92 90 60 ",   # warm-ish white
        "2 50 50 50 20 19 12 ",
    ]
    p = tmp_path / "warm.ti3"
    _write_ti3(p, rows)
    a = analyse_ti3(parse_ti3(p))
    assert a.max_cast > 1.0
    assert a.cast_token in ("warm", "magenta")


def test_non_monotonic_neutral_flagged(tmp_path: Path):
    # grey level 50 measures *darker* than level 25 → one reversal
    rows = [
        "1 0 0 0 0.9 1.0 1.1 ",
        "2 25 25 25 30 32 28 ",
        "3 50 50 50 18 19 16 ",   # should be lighter than #2 but isn't
        "4 100 100 100 86 90 75 ",
    ]
    p = tmp_path / "bad.ti3"
    _write_ti3(p, rows)
    a = analyse_ti3(parse_ti3(p))
    assert a.neutral_non_monotonic >= 1


def test_rejects_non_ti3(tmp_path: Path):
    p = tmp_path / "x.ti3"
    p.write_text("this is not a cgats file\n")
    with pytest.raises(Ti3ParseError):
        parse_ti3(p)


def test_requires_rgb(tmp_path: Path):
    p = tmp_path / "cmyk.ti3"
    p.write_text("CTI3\n\nNUMBER_OF_FIELDS 4\nBEGIN_DATA_FORMAT\n"
                 "SAMPLE_ID XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\n"
                 "NUMBER_OF_SETS 1\nBEGIN_DATA\n1 50 50 50 \nEND_DATA\n")
    with pytest.raises(Ti3ParseError):
        parse_ti3(p)


# --- spectral integration --------------------------------------------------

def test_integrator_perfect_diffuser_is_d50_white():
    wl = np.linspace(380, 730, 36)
    integ = _Integrator(wl)
    white = integ.white_xyz(cie_data.IL_D50)
    perfect = integ.reflect_xyz(np.full(wl.size, 100.0), cie_data.IL_D50)
    # a 100 % reflector equals the illuminant's own white, Y normalised to 100
    assert white[1] == pytest.approx(100.0, abs=1e-6)
    assert np.allclose(perfect, white, rtol=1e-6)
    # D50 chromaticity: x≈0.3457, y≈0.3585
    x = white[0] / white.sum()
    y = white[1] / white.sum()
    assert x == pytest.approx(0.3457, abs=0.004)
    assert y == pytest.approx(0.3585, abs=0.004)


def test_spectral_section_present(tmp_path: Path):
    rows = [
        "1 100 100 100 86 90 75 " + "90 92 91 93 ",   # bright, near-flat
        "2 0 0 0 0.9 1.0 1.1 " + "1 1 1 1 ",
    ]
    p = tmp_path / "spec.ti3"
    _write_ti3(p, rows, spectral=True)
    a = analyse_ti3(parse_ti3(p))
    assert a.data.has_spectral
    names = [pt.name for pt in a.illuminant_points]
    assert "D50" in names and "D65" in names
    assert a.paper_shift_d50_d65 is not None
