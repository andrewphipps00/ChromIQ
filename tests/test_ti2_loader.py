"""Tests for the .ti2 instrument-detection helpers used by auto -B selection.

The Measure tab decides whether to disable bidirectional strip recognition
(chartread -B) from the chart's TARGET_INSTRUMENT. Verified against real files:
the i1 Pro family (i1 Pro / Pro 2 / Pro 3 / Pro 3+) is all tagged
"GretagMacbeth i1 Pro" and reads both directions; the ColorMunki (and its
i1Studio rebrand) is tagged "X-Rite ColorMunki" and reads one direction only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ui.ti2_loader import (
    KNOWN_INSTRUMENTS,
    _related_files,
    disable_bidir_for_instrument,
    has_spectral_data,
    instrument_label,
    is_colormunki,
    is_spectroscan,
    read_target_instrument,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "chart.ti2"
    p.write_text(body, encoding="utf-8")
    return p


def test_read_target_instrument_i1pro(tmp_path: Path) -> None:
    p = _write(tmp_path, 'CTI2\nKEYWORD "TARGET_INSTRUMENT"\nTARGET_INSTRUMENT "GretagMacbeth i1 Pro"\n')
    assert read_target_instrument(p) == "GretagMacbeth i1 Pro"


def test_read_target_instrument_colormunki(tmp_path: Path) -> None:
    p = _write(tmp_path, 'TARGET_INSTRUMENT "X-Rite ColorMunki"\n')
    assert read_target_instrument(p) == "X-Rite ColorMunki"


def test_read_target_instrument_missing_keyword(tmp_path: Path) -> None:
    p = _write(tmp_path, "CTI2\nNUMBER_OF_SETS 100\n")
    assert read_target_instrument(p) is None


def test_read_target_instrument_missing_file(tmp_path: Path) -> None:
    assert read_target_instrument(tmp_path / "nope.ti2") is None


@pytest.mark.parametrize(
    "name, expected",
    [
        ("GretagMacbeth i1 Pro", False),   # i1 Pro / Pro 2 / Pro 3 / Pro 3+
        ("X-Rite i1 Pro 3", False),        # robust to alternate spellings
        ("X-Rite ColorMunki", True),       # one direction only
        ("ColorMunki Photo", True),
        ("GretagMacbeth SpectroScan", False),
        (None, False),                     # unknown / no file -> bidir allowed
        ("", False),
    ],
)
def test_disable_bidir_for_instrument(name, expected) -> None:
    assert disable_bidir_for_instrument(name) is expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("X-Rite ColorMunki", True),
        ("colormunki photo", True),        # case-insensitive substring match
        ("GretagMacbeth i1 Pro", False),
        ("GretagMacbeth SpectroScan", False),
        (None, False),
        ("", False),
    ],
)
def test_is_colormunki(name, expected) -> None:
    assert is_colormunki(name) is expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("GretagMacbeth SpectroScan", True),
        ("spectroscan", True),             # case-insensitive substring match
        ("X-Rite ColorMunki", False),
        ("GretagMacbeth i1 Pro", False),
        (None, False),
        ("", False),
    ],
)
def test_is_spectroscan(name, expected) -> None:
    assert is_spectroscan(name) is expected


def test_disable_bidir_delegates_to_is_colormunki() -> None:
    # disable_bidir_for_instrument is now defined in terms of is_colormunki.
    for name in (*KNOWN_INSTRUMENTS, None, "", "Unknown device"):
        assert disable_bidir_for_instrument(name) is is_colormunki(name)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("X-Rite ColorMunki", "ColorMunki / i1Studio / CCStudio"),
        ("ColorMunki Photo", "ColorMunki / i1Studio / CCStudio"),
        ("GretagMacbeth i1 Pro", "i1Pro / i1Pro2 / i1Pro3(+)"),
        ("X-Rite i1Pro3", "i1Pro / i1Pro2 / i1Pro3(+)"),
        # SpectroScan and unknowns are shown unchanged.
        ("GretagMacbeth SpectroScan", "GretagMacbeth SpectroScan"),
        ("Some Other Device", "Some Other Device"),
        (None, None),
        ("", None),
    ],
)
def test_instrument_label(name, expected) -> None:
    assert instrument_label(name) == expected


def test_known_instruments_registry() -> None:
    assert KNOWN_INSTRUMENTS == (
        "X-Rite ColorMunki",
        "GretagMacbeth i1 Pro",
        "GretagMacbeth SpectroScan",
    )


def test_read_target_instrument_works_on_ti3(tmp_path: Path) -> None:
    p = tmp_path / "measured.ti3"
    p.write_text('CTI3\nTARGET_INSTRUMENT "GretagMacbeth SpectroScan"\n', encoding="utf-8")
    assert read_target_instrument(p) == "GretagMacbeth SpectroScan"


def test_has_spectral_data_present(tmp_path: Path) -> None:
    p = _write(tmp_path, 'CTI3\nSPECTRAL_BANDS "36"\nSPECTRAL_START_NM "380.0"\n')
    assert has_spectral_data(p) is True


def test_has_spectral_data_absent(tmp_path: Path) -> None:
    p = _write(tmp_path, "CTI3\nNUMBER_OF_SETS 100\n")
    assert has_spectral_data(p) is False


def test_has_spectral_data_zero_bands(tmp_path: Path) -> None:
    p = _write(tmp_path, 'CTI3\nSPECTRAL_BANDS "0"\n')
    assert has_spectral_data(p) is False


def test_has_spectral_data_missing_file(tmp_path: Path) -> None:
    assert has_spectral_data(tmp_path / "nope.ti3") is False


def test_related_files_dedupes_case_insensitive_glob(tmp_path: Path) -> None:
    """A single chart TIFF must appear once when loading an existing target.

    Regression guard for forum #148275's "Page 1/2 and 2/2 (same)" symptom:
    pathlib.Path.glob is case-insensitive on Windows, so the prior code's
    sorted([*glob('*.tif'), *glob('*.TIF'), *glob('*.tiff')]) returned the
    same file two or three times, doubling the page count in the preview.
    (On case-sensitive filesystems this passes trivially; it bites on Windows,
    which is where the bug was reported.)
    """
    ti2 = tmp_path / "chart.ti2"
    ti2.write_text('TARGET_INSTRUMENT "GretagMacbeth i1 Pro"\n', encoding="utf-8")
    (tmp_path / "chart.tif").write_bytes(b"II*\x00")  # one-page chart

    _, tiffs = _related_files(ti2)

    assert len(tiffs) == 1, f"expected 1 TIFF, got {len(tiffs)}: {tiffs}"
    resolved = [p.resolve() for p in tiffs]
    assert len(resolved) == len(set(resolved)), f"duplicate paths returned: {tiffs}"


def test_related_files_finds_tiff_extension(tmp_path: Path) -> None:
    """The dedup must not drop the legitimate .tiff extension variant."""
    ti2 = tmp_path / "chart.ti2"
    ti2.write_text("CTI2\n", encoding="utf-8")
    (tmp_path / "chart.tiff").write_bytes(b"II*\x00")

    _, tiffs = _related_files(ti2)

    assert [p.name for p in tiffs] == ["chart.tiff"]
