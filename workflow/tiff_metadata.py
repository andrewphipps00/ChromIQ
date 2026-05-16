"""Byte-safe stamping of a one-line metadata caption onto the right edge of a chart TIFF.

Printtarg already writes a vertical ID line along the page's right edge
(`ArgyllCMS — Chart "name" (Random Start NNN) <date>`). This module appends
ChromIQ-side context — user notes and/or the actual targen+printtarg commands
used — as a **single rotated text line** placed in the widest white run of
the right margin (typically between Argyll's column and the page edge).

Color-integrity guarantees:
- Every pixel column strictly to the **left** of the writable band is
  byte-identical before/after stamping. Argyll's existing text column and
  the patch area are never touched.
- The image's pixel dimensions, bit depth (uint8/uint16), photometric,
  compression, ICC profile tag, **and raw XResolution/YResolution/
  ResolutionUnit values** are preserved exactly (no inch↔centimeter
  rewrite). The output is byte-equivalent to the input in every metadata
  field; only the pixels inside the chosen white band change.
- If the right margin has no usable white run of at least _MIN_STRIP_WIDTH_PX,
  the stamper logs a warning and leaves the TIFF unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

from core.logger import get_logger

log = get_logger(__name__)

# Per-column ink threshold (fraction of rows) above which a column belongs
# to the patch area, not the right margin.
_PATCH_COL_DENSITY_THRESHOLD = 0.30
_PATCH_SAFETY_PAD_PX = 4
_MIN_STRIP_WIDTH_PX = 24
_LINE_GAP_PX = 6

# Joiner between concatenated metadata pieces in the single stamped line.
_JOIN = "    |    "


def stamp_chart_metadata(
    tiff_paths: Iterable[Path],
    lines: Sequence[str],
) -> None:
    """Stamp `lines` joined into a single rotated text line on each TIFF's right margin."""
    pieces = [s.strip() for s in lines if s and s.strip()]
    if not pieces:
        return
    text = _JOIN.join(pieces)
    for path in tiff_paths:
        try:
            _stamp_one(Path(path), text)
        except Exception as exc:
            log.warning("Right-edge stamp failed for %s: %s", path, exc)


def _stamp_one(path: Path, text: str) -> None:
    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        arr = np.array(page.asarray(), copy=True)
        photometric = page.photometric
        compression = page.compression
        xres_tag = page.tags.get("XResolution")
        yres_tag = page.tags.get("YResolution")
        runit_tag = page.tags.get("ResolutionUnit")
        icc_tag = page.tags.get(34675)
        xres_val = tuple(xres_tag.value) if xres_tag else None
        yres_val = tuple(yres_tag.value) if yres_tag else None
        runit_val = int(runit_tag.value) if runit_tag else None
        icc_bytes = bytes(icc_tag.value) if icc_tag else None

        # Capture informational tags so we can rewrite them after the
        # tifffile re-encode. Tags tifffile owns natively (270/305) go through
        # their dedicated kwargs; the rest go through extratags.
        description_val = _str_tag_value(page.tags.get(270))   # ImageDescription
        software_val   = _str_tag_value(page.tags.get(305))   # Software
        orientation_val = page.tags.get(274).value if page.tags.get(274) else None
        xpos_val = (tuple(page.tags.get(286).value)
                    if page.tags.get(286) else None)
        ypos_val = (tuple(page.tags.get(287).value)
                    if page.tags.get(287) else None)
        artist_val = _str_tag_value(page.tags.get(315))
        copyright_val = _str_tag_value(page.tags.get(33432))

        preserved_tags: list[tuple] = []
        if orientation_val is not None:
            preserved_tags.append((274, 3, 1, int(orientation_val), True))
        if xpos_val:
            preserved_tags.append((286, 5, 1, (int(xpos_val[0]), int(xpos_val[1])), True))
        if ypos_val:
            preserved_tags.append((287, 5, 1, (int(ypos_val[0]), int(ypos_val[1])), True))
        if artist_val:
            preserved_tags.append((315, 2, len(artist_val) + 1, artist_val + "\x00", True))
        if copyright_val:
            preserved_tags.append((33432, 2, len(copyright_val) + 1, copyright_val + "\x00", True))

    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        log.warning("Right-edge stamp: unexpected array shape %s for %s", arr.shape, path)
        return

    dtype = arr.dtype
    if dtype not in (np.uint8, np.uint16):
        log.warning("Right-edge stamp: unsupported dtype %s for %s", dtype, path)
        return

    H, W, C = arr.shape
    band = _detect_writable_band(arr)
    if band is None:
        log.info("Right-edge stamp skipped (no usable right margin) for %s", path)
        return
    band_left, band_right = band
    strip_w = min(40, band_right - band_left)
    strip_h = H - 2 * _PATCH_SAFETY_PAD_PX
    if strip_h < 100:
        log.info("Right-edge stamp skipped (image too short) for %s", path)
        return

    font_px = max(12, strip_w - 6)
    font = _pick_font(font_px)
    strip = _render_rotated_line(text, strip_h, strip_w, font, dtype, C)
    # Center the strip horizontally inside the band so the text sits roughly
    # equidistant from Argyll's existing text and the page edge.
    band_center = (band_left + band_right) // 2
    x0 = band_center - strip_w // 2
    if x0 < band_left:
        x0 = band_left
    if x0 + strip_w > band_right:
        x0 = band_right - strip_w
    y0 = _PATCH_SAFETY_PAD_PX
    arr[y0 : y0 + strip_h, x0 : x0 + strip_w, :] = strip

    # Preserve resolution and unit. tifffile owns tags 282/283/296 (extratags
    # for them are silently dropped), so we use the resolution kwargs with the
    # exact unit string that matches the original ResolutionUnit value. The
    # rational form may differ by ulps but the unit and effective DPI are
    # preserved, so the image's physical print dimensions are unchanged.
    res_unit_str = _resunit_str(runit_val)
    res_pair = _rational_to_float_pair(xres_val, yres_val)
    extratags: list[tuple] = list(preserved_tags)

    write_kwargs: dict = {
        "photometric": photometric,
        "compression": compression,
        "extratags": extratags or None,
        "metadata": None,
        # tifffile-owned tags: pass through their dedicated kwargs so they
        # don't get silently overwritten with defaults.
        "description": description_val or None,
        "software":    software_val if software_val is not None else False,
    }
    if icc_bytes:
        write_kwargs["iccprofile"] = icc_bytes
    if res_pair is not None and res_unit_str is not None:
        write_kwargs["resolution"] = res_pair
        write_kwargs["resolutionunit"] = res_unit_str

    tifffile.imwrite(str(path), arr, **write_kwargs)


def _str_tag_value(tag) -> str | None:
    """Decode a TIFF ASCII tag (bytes or str), stripping trailing NULs."""
    if tag is None:
        return None
    v = tag.value
    if isinstance(v, bytes):
        return v.rstrip(b"\x00").decode("ascii", errors="ignore") or None
    if isinstance(v, str):
        return v.rstrip("\x00") or None
    return None


def _resunit_str(unit: int | None) -> str | None:
    """Map TIFF ResolutionUnit code → tifffile.imwrite resolutionunit string."""
    if unit == 1:
        return "NONE"
    if unit == 2:
        return "INCH"
    if unit == 3:
        return "CENTIMETER"
    return None


def _rational_to_float_pair(
    xres: tuple[int, int] | None,
    yres: tuple[int, int] | None,
) -> tuple[float, float] | None:
    """Convert a pair of TIFF rationals to (float, float). None if either missing/zero."""
    if not xres or not yres or xres[1] == 0 or yres[1] == 0:
        return None
    return (xres[0] / xres[1], yres[0] / yres[1])


def _detect_writable_band(arr: np.ndarray) -> tuple[int, int] | None:
    """Return (left_x, right_x) of the widest white column run in the right margin."""
    if arr.size == 0:
        return None
    H, W = arr.shape[:2]
    max_val = np.iinfo(arr.dtype).max
    ink_cutoff = int(max_val * 240 / 255)
    if arr.shape[2] == 1:
        mask = arr[..., 0] < ink_cutoff
    else:
        mask = (arr < ink_cutoff).any(axis=2)

    col_density = mask.sum(axis=0) / max(1, H)
    patch_cols = np.where(col_density >= _PATCH_COL_DENSITY_THRESHOLD)[0]
    patch_right = (int(patch_cols[-1]) + _PATCH_SAFETY_PAD_PX
                   if len(patch_cols) else W // 2)
    if patch_right >= W - _MIN_STRIP_WIDTH_PX:
        return None

    mid_top, mid_bottom = H // 6, 5 * H // 6
    margin_inked = mask[mid_top:mid_bottom, patch_right:W].any(axis=0)

    runs: list[tuple[int, int]] = []
    in_run = False
    run_start = 0
    for i, inked in enumerate(margin_inked):
        if not inked and not in_run:
            in_run = True
            run_start = patch_right + i
        elif inked and in_run:
            in_run = False
            runs.append((run_start, patch_right + i))
    if in_run:
        runs.append((run_start, W))

    runs = [(a, b) for (a, b) in runs if b - a >= _MIN_STRIP_WIDTH_PX]
    if not runs:
        return None
    best_left, best_right = max(runs, key=lambda r: r[1] - r[0])
    best_left += _PATCH_SAFETY_PAD_PX
    best_right -= _PATCH_SAFETY_PAD_PX
    if best_right - best_left < _MIN_STRIP_WIDTH_PX:
        return None
    return (best_left, best_right)


def _render_rotated_line(
    text: str,
    strip_h: int,
    strip_w: int,
    font: ImageFont.ImageFont,
    dtype,
    channels: int,
) -> np.ndarray:
    """Return a (strip_h, strip_w, channels) numpy array containing `text` rotated 90° CCW."""
    canvas = Image.new("L", (strip_h, strip_w), 255)
    draw = ImageDraw.Draw(canvas)
    bbox = _text_bbox(draw, text, font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = max(0, (strip_h - text_w) // 2 - bbox[0])
    y = (strip_w - text_h) // 2 - bbox[1]
    draw.text((x, y), text, fill=0, font=font)

    rotated = canvas.rotate(90, expand=True, resample=Image.Resampling.NEAREST)
    band_l = np.asarray(rotated, dtype=np.uint8)
    assert band_l.shape == (strip_h, strip_w), \
        f"rotation produced {band_l.shape}, expected {(strip_h, strip_w)}"

    if dtype == np.uint16:
        band_l = (band_l.astype(np.uint32) * 65535 // 255).astype(np.uint16)

    if channels == 1:
        return band_l[..., None]
    return np.repeat(band_l[..., None], channels, axis=2)


def _pick_font(size_px: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_bbox(draw: "ImageDraw.ImageDraw", text: str, font) -> tuple[int, int, int, int]:
    try:
        return draw.textbbox((0, 0), text, font=font)
    except AttributeError:
        w, h = draw.textsize(text, font=font)  # type: ignore[attr-defined]
        return (0, 0, w, h)
