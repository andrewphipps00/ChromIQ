"""Regression tests for PostScript page-size handling.

Issues #14 (landscape chart prints as portrait, columns cut off) and #15
(landscape chart silently disappears from the queue) both came from
PostScriptGenerator emitting a portrait `setpagedevice` PageSize while
drawing a landscape image on top — the image overhung the page and pstops
either clipped it or rejected it.

Pin the aspect-aware swap so we don't regress.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
import tifffile

from workflow.postscript_generator import PostScriptGenerator


def _write_tiff(path: Path, w_px: int, h_px: int, dpi: int = 200) -> None:
    """Write a synthetic RGB TIFF with INCH resolution units."""
    arr = np.zeros((h_px, w_px, 3), dtype=np.uint8)
    tifffile.imwrite(str(path), arr, resolution=(dpi, dpi), resolutionunit="INCH")


def _page_size(ps_text: str) -> tuple[float, float]:
    m = re.search(r"/PageSize \[(\S+) (\S+)\]", ps_text)
    assert m, "no setpagedevice PageSize emitted"
    return float(m.group(1)), float(m.group(2))


def _translate(ps_text: str) -> tuple[float, float]:
    m = re.search(r"^(\S+) (\S+) translate$", ps_text, re.MULTILINE)
    assert m, "no translate emitted"
    return float(m.group(1)), float(m.group(2))


# Sub-pixel tolerance for tifffile's rational-encoded XResolution: writing
# resolution=200 round-trips back at ~199.91 dpi, so derived points drift by
# ≲0.5 pt. The bug we're testing for was a ~350 pt overhang.
_PT_SLOP = 1.0


# Issue #15 in miniature: A3 landscape TIFF + A3 portrait page_size_pt.
def test_landscape_tiff_on_portrait_page_swaps(tmp_path: Path) -> None:
    tiff = tmp_path / "landscape.tif"
    _write_tiff(tiff, w_px=3307, h_px=2339, dpi=200)  # ~1190 × 842 pt landscape
    ps = PostScriptGenerator().generate(tiff, page_size_pt=(842, 1191))
    pw, ph = _page_size(ps)
    assert pw > ph, f"expected landscape PageSize, got {pw}×{ph}"
    x_off, y_off = _translate(ps)
    assert x_off >= -_PT_SLOP and y_off >= -_PT_SLOP, (
        f"translate offsets {x_off}, {y_off} should be ~non-negative — "
        "image must fit inside the page"
    )


def test_portrait_tiff_on_landscape_page_swaps(tmp_path: Path) -> None:
    tiff = tmp_path / "portrait.tif"
    _write_tiff(tiff, w_px=2339, h_px=3307, dpi=200)  # ~842 × 1190 pt portrait
    ps = PostScriptGenerator().generate(tiff, page_size_pt=(1191, 842))
    pw, ph = _page_size(ps)
    assert ph > pw, f"expected portrait PageSize, got {pw}×{ph}"
    x_off, y_off = _translate(ps)
    assert x_off >= -_PT_SLOP and y_off >= -_PT_SLOP


def test_matching_aspect_does_not_swap(tmp_path: Path) -> None:
    tiff = tmp_path / "landscape.tif"
    _write_tiff(tiff, w_px=3307, h_px=2339, dpi=200)
    ps = PostScriptGenerator().generate(tiff, page_size_pt=(1191, 842))
    assert _page_size(ps) == (1191.0, 842.0)


def test_no_page_size_falls_back_to_tiff_dims(tmp_path: Path) -> None:
    tiff = tmp_path / "landscape.tif"
    _write_tiff(tiff, w_px=3307, h_px=2339, dpi=200)
    ps = PostScriptGenerator().generate(tiff)
    pw, ph = _page_size(ps)
    # 3307 px / 200 dpi * 72 pt/in = 1190.52; 2339 / 200 * 72 = 841.68
    assert pw == pytest.approx(1190.52, abs=_PT_SLOP)
    assert ph == pytest.approx(841.68, abs=_PT_SLOP)


def test_square_tiff_keeps_portrait_page(tmp_path: Path) -> None:
    # Strict `>` semantics: a perfectly square TIFF is neither landscape nor
    # portrait, so it must not trigger a swap when the page is portrait.
    tiff = tmp_path / "square.tif"
    _write_tiff(tiff, w_px=1000, h_px=1000, dpi=200)
    ps = PostScriptGenerator().generate(tiff, page_size_pt=(842, 1191))
    assert _page_size(ps) == (842.0, 1191.0)


# Issue #15 second failure mode: 16-bit TIFFs from `printtarg -T` produced PS
# with `colorimage … 16 …`, which older HP PostScript interpreters silently
# drop. The generator must downcast to 8-bit so the inline image is the
# universally-accepted format.
def test_16bit_tiff_emits_8bit_colorimage(tmp_path: Path) -> None:
    tiff = tmp_path / "chart16.tif"
    arr = np.full((100, 100, 3), 0x8080, dtype=np.uint16)
    tifffile.imwrite(str(tiff), arr, resolution=(200, 200), resolutionunit="INCH")

    ps = PostScriptGenerator().generate(tiff)
    m = re.search(r"^(\d+) (\d+) (\d+)\n\[", ps, re.MULTILINE)
    assert m, "no `W H BPC` line preceding the transform matrix"
    bits = int(m.group(3))
    assert bits == 8, f"expected 8-bit colorimage, got {bits}-bit"


# Issue #15 third failure mode: HP CLJ 5550 honoured its printer-panel duplex
# default and ignored `lp -o Duplex=None`, so single-page chart jobs got
# pulled back through the duplexer and "Print All Pages" paired two charts
# onto one sheet. The PS-level setpagedevice directive overrides the device
# default — pin it here so a future refactor can't strip it.
def test_setpagedevice_disables_duplex(tmp_path: Path) -> None:
    tiff = tmp_path / "chart.tif"
    _write_tiff(tiff, w_px=1000, h_px=1000, dpi=200)
    ps = PostScriptGenerator().generate(tiff)
    m = re.search(r"<<[^>]*?>>\s*setpagedevice", ps)
    assert m, "no setpagedevice block emitted"
    block = m.group(0)
    assert "/Duplex false" in block, (
        f"setpagedevice must force /Duplex false to override device defaults — "
        f"got: {block!r}"
    )
    assert "/Tumble false" in block, (
        f"setpagedevice should include /Tumble false for spec correctness — "
        f"got: {block!r}"
    )


def test_16bit_downcast_preserves_high_byte(tmp_path: Path) -> None:
    # The downcast right-shifts by 8, so 0xABCD becomes 0xAB. Confirms we're
    # taking the MSB (the colorimetrically meaningful half) and not just
    # truncating to the LSB by casting.
    tiff = tmp_path / "msb.tif"
    arr = np.full((50, 50, 3), 0xABCD, dtype=np.uint16)
    tifffile.imwrite(str(tiff), arr, resolution=(200, 200), resolutionunit="INCH")

    ps = PostScriptGenerator().generate(tiff)
    # Round-trip the encoded payload back to bytes and check the high byte.
    import base64
    import zlib

    m = re.search(r"colorimage\n(.+?)~>", ps, re.DOTALL)
    assert m, "no colorimage payload found"
    a85 = m.group(1).replace("\n", "") + "~>"
    decoded = zlib.decompress(base64.a85decode(a85, adobe=True))
    assert decoded[:6] == b"\xab" * 6, (
        f"expected high-byte 0xAB after downcast, got {decoded[:6].hex()}"
    )
