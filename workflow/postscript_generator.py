"""Generate PostScript Level 2 from TIFF files for raw CUPS printing.

Converts each TIFF page to a PS document with exact pixel dimensions
and 1:1 pixel mapping — no colour conversion, no ICC profile.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.logger import get_logger

log = get_logger(__name__)

# Points per mm (72 pt/inch ÷ 25.4 mm/inch)
_PT_PER_MM = 72.0 / 25.4


class PostScriptGenerator:
    """Convert a single TIFF page to a PostScript Level 2 document string."""

    def generate(self, tiff_path: Path, dpi: int = 300) -> str:
        """Return a complete PS Level 2 document for *tiff_path*.

        The page size is computed exactly from pixel dimensions and DPI.
        Pixels are encoded as hex RGB with no colour transform.
        """
        img = self._load_strip_profile(tiff_path)

        width_px, height_px = img.size
        pts_w = width_px  * 72.0 / dpi
        pts_h = height_px * 72.0 / dpi

        log.debug("PS: %s  %dx%d px  %.1f×%.1f pt  %d dpi",
                  tiff_path.name, width_px, height_px, pts_w, pts_h, dpi)

        hex_data = self._encode_hex(img)
        ps = self._build_ps(width_px, height_px, pts_w, pts_h, hex_data)
        return ps

    # ------------------------------------------------------------------

    def _load_strip_profile(self, path: Path) -> Image.Image:
        img = Image.open(path)

        # Convert to RGB — no colour transform, just reinterpret
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Strip embedded ICC profile by re-saving raw pixels without metadata
        # (Pillow keeps profile in info dict; just don't pass it forward)
        clean = Image.new("RGB", img.size)
        clean.putdata(list(img.getdata()))
        return clean

    def _encode_hex(self, img: Image.Image) -> str:
        """Return hex-encoded RGB pixel data, 12 hex chars per pixel, 72 pixels/line."""
        raw = img.tobytes()   # 3 bytes per pixel, row-major
        chars_per_line = 72
        lines = []
        line_buf: list[str] = []
        pixel_in_line = 0

        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i+1], raw[i+2]
            line_buf.append(f"{r:02X}{g:02X}{b:02X}")
            pixel_in_line += 1
            if pixel_in_line >= chars_per_line:
                lines.append("".join(line_buf))
                line_buf = []
                pixel_in_line = 0

        if line_buf:
            lines.append("".join(line_buf))

        return "\n".join(lines)

    def _build_ps(
        self,
        w_px: int, h_px: int,
        w_pt: float, h_pt: float,
        hex_data: str,
    ) -> str:
        return f"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 {w_pt:.2f} {h_pt:.2f}
%%EndComments

% Set exact page size from pixel dimensions and DPI
<< /PageSize [{w_pt:.2f} {h_pt:.2f}] /ImagingBBox null >> setpagedevice

% Draw RGB raster image at 1:1 pixel mapping
{w_pt:.2f} {h_pt:.2f} scale
{w_px} {h_px} 8
[{w_px} 0 0 -{h_px} 0 {h_px}]
{{{hex_data}}}
false 3
colorimage

showpage
%%EOF
"""
