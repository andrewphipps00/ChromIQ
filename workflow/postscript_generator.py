"""Generate PostScript Level 2/3 from TIFF profiling targets.

Uses device-dependent colour spaces (DeviceGray, DeviceRGB, DeviceCMYK,
DeviceN) to bypass all CUPS and macOS colour management.  Supports 8-bit
and 16-bit TIFFs for any channel count produced by printtarg.
"""
from __future__ import annotations

from pathlib import Path

import zlib

import numpy as np
import tifffile

from core.logger import get_logger

log = get_logger(__name__)

_PT_PER_INCH = 72.0

# ChromIQ ink codes → PostScript colorant names for DeviceN
_INK_CODE_TO_PS_NAME: dict[str, str] = {
    "k":   "Black",
    "c":   "Cyan",
    "m":   "Magenta",
    "y":   "Yellow",
    "r":   "Red",
    "g":   "Green",
    "b":   "Blue",
    "o":   "Orange",
    "v":   "Violet",
    "w":   "White",
    "lc":  "LightCyan",
    "lm":  "LightMagenta",
    "lk":  "LightBlack",
    "ly":  "LightYellow",
    "llk": "LightLightBlack",
    "mc":  "LightCyan2",
    "mm":  "LightMagenta2",
}

# Approximate CMYK contribution per ink for the DeviceN fallback tint transform.
# Accuracy is not critical — this path is only used by PS interpreters that do
# not support DeviceN natively.
_INK_TO_CMYK: dict[str, tuple[float, float, float, float]] = {
    "c":   (1.0, 0.0, 0.0, 0.0),
    "m":   (0.0, 1.0, 0.0, 0.0),
    "y":   (0.0, 0.0, 1.0, 0.0),
    "k":   (0.0, 0.0, 0.0, 1.0),
    "r":   (0.0, 0.5, 0.5, 0.0),
    "g":   (0.5, 0.0, 0.5, 0.0),
    "b":   (0.5, 0.5, 0.0, 0.0),
    "o":   (0.0, 0.4, 0.7, 0.0),
    "v":   (0.6, 0.5, 0.0, 0.0),
    "w":   (0.0, 0.0, 0.0, 0.0),
    "lc":  (0.5, 0.0, 0.0, 0.0),
    "lm":  (0.0, 0.5, 0.0, 0.0),
    "ly":  (0.0, 0.0, 0.5, 0.0),
    "lk":  (0.0, 0.0, 0.0, 0.5),
    "llk": (0.0, 0.0, 0.0, 0.3),
    "mc":  (0.3, 0.0, 0.0, 0.0),
    "mm":  (0.0, 0.3, 0.0, 0.0),
}


class PostScriptGenerator:
    """Convert a TIFF profiling target to a PostScript Level 2/3 document."""

    def generate(
        self,
        tiff_path: Path,
        dpi: int = 300,
        ink_channels: list[str] | None = None,
        page_size_pt: tuple[float, float] | None = None,
    ) -> str:
        """Return a complete PS document for *tiff_path*.

        DPI is read from the TIFF's XResolution tag; *dpi* is used as fallback.
        ink_channels: ordered ink codes matching the TIFF channels (e.g.
        ['c','m','y','k','lc','lm']) — required only for N > 4 channel DeviceN
        naming.  A sidecar .channels.json file is the usual source for this.
        page_size_pt: physical media size (w_pt, h_pt). When set, drives the
        PostScript setpagedevice PageSize so the PS document and any
        `lp -o PageSize=...` agree. Defaults to the TIFF's own dimensions.
        """
        arr, actual_dpi = self._read_tiff(tiff_path, fallback_dpi=dpi)
        h, w, n_ch = arr.shape
        bits = 16 if arr.dtype == np.uint16 else 8

        tiff_w_pt = w * _PT_PER_INCH / actual_dpi
        tiff_h_pt = h * _PT_PER_INCH / actual_dpi
        if page_size_pt is None:
            page_w, page_h = tiff_w_pt, tiff_h_pt
            page_rotated = False
        else:
            page_w, page_h = page_size_pt
            # If the declared page orientation contradicts the TIFF's, swap
            # so setpagedevice agrees with the image we draw. Otherwise the
            # image overhangs the page bounds and pstops double-rotates.
            page_rotated = (page_w > page_h) != (tiff_w_pt > tiff_h_pt)
            if page_rotated:
                page_w, page_h = page_h, page_w
        level = 3 if (n_ch > 4 or bits > 8) else 2

        log.debug(
            "PS: %s  %dx%d px  TIFF %.1f×%.1f pt  Page %.1f×%.1f pt%s  "
            "%d dpi  %d-bit  %d-ch  Level %d",
            tiff_path.name, w, h, tiff_w_pt, tiff_h_pt, page_w, page_h,
            " (rotated to match TIFF aspect)" if page_rotated else "",
            actual_dpi, bits, n_ch, level,
        )

        cs_block = self._colorspace_block(n_ch, ink_channels)
        hex_data = self._encode_hex(arr, bits)
        return self._build_ps(
            w, h, tiff_w_pt, tiff_h_pt, page_w, page_h,
            bits, n_ch, cs_block, hex_data, level,
        )

    # ------------------------------------------------------------------
    # TIFF reading
    # ------------------------------------------------------------------

    @staticmethod
    def _read_tiff(path: Path, fallback_dpi: int = 300) -> tuple[np.ndarray, float]:
        """Return (H, W, C) array and DPI extracted from the TIFF."""
        with tifffile.TiffFile(str(path)) as tif:
            page = tif.pages[0]
            dpi = PostScriptGenerator._read_dpi(page, fallback_dpi)
            arr = page.asarray()

        # Grayscale (H, W) → (H, W, 1)
        if arr.ndim == 2:
            arr = arr[:, :, np.newaxis]

        return np.ascontiguousarray(arr), dpi

    @staticmethod
    def _read_dpi(page: tifffile.TiffPage, fallback: int) -> float:
        """Pixels-per-inch from a TIFF page, honouring ResolutionUnit.

        ArgyllCMS `printtarg` writes the resolution in pixels-per-centimetre
        (ResolutionUnit = 3); the raw value (~118 for a 300-dpi chart) must be
        scaled by 2.54 or every derived dimension comes out 2.54× too large.
        """
        tag = page.tags.get("XResolution")
        if tag is None:
            return float(fallback)
        v = tag.value
        if isinstance(v, tuple) and len(v) == 2 and v[1]:
            res = float(v[0]) / float(v[1])
        else:
            try:
                res = float(v)
            except (TypeError, ValueError):
                return float(fallback)
        if res <= 0:
            return float(fallback)

        unit_tag = page.tags.get("ResolutionUnit")
        try:
            unit = int(getattr(unit_tag, "value", 2))
        except (TypeError, ValueError):
            unit = 2
        if unit == 1:          # no absolute unit
            return float(fallback)
        if unit == 3:          # pixels per centimetre → pixels per inch
            res *= 2.54
        return res

    # ------------------------------------------------------------------
    # Hex encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_hex(arr: np.ndarray, bits: int) -> str:
        """Return hex-encoded pixel data in 72-char lines."""
        raw = arr.astype(">u2").tobytes() if bits == 16 else arr.tobytes()
        s = raw.hex().upper()
        return "\n".join(s[i:i + 72] for i in range(0, len(s), 72))

    # ------------------------------------------------------------------
    # Colour space blocks
    # ------------------------------------------------------------------

    def _colorspace_block(self, n_ch: int, ink_channels: list[str] | None) -> str:
        if n_ch == 1:
            return "/DeviceGray setcolorspace"
        if n_ch == 3:
            return "/DeviceRGB setcolorspace"
        if n_ch == 4:
            return "/DeviceCMYK setcolorspace"
        return self._devicen_block(n_ch, ink_channels)

    def _devicen_block(self, n_ch: int, ink_channels: list[str] | None) -> str:
        if ink_channels and len(ink_channels) >= n_ch:
            channels = list(ink_channels[:n_ch])
        else:
            channels = [f"ink{i + 1}" for i in range(n_ch)]

        ps_names = " ".join(
            f"/{_INK_CODE_TO_PS_NAME.get(c, c.capitalize())}"
            for c in channels
        )
        tint = self._devicen_tint(channels)
        return (
            f"[/DeviceN [{ps_names}]\n"
            f"/DeviceCMYK\n"
            f"{{\n{tint}\n}}\n"
            f"] setcolorspace"
        )

    def _devicen_tint(self, channels: list[str]) -> str:
        """PS procedure body: N ink values → 4 CMYK values (fallback only)."""
        n = len(channels)
        # Build unique PS variable names
        seen: dict[str, int] = {}
        varnames: list[str] = []
        for code in channels:
            safe = code.replace("-", "_")
            cnt = seen.get(safe, 0)
            seen[safe] = cnt + 1
            varnames.append(safe if cnt == 0 else f"{safe}_{cnt}")

        lines = [f"  {n} dict begin"]
        # Stack order: first channel deepest, last on top → pop in reverse order
        for vname in reversed(varnames):
            lines.append(f"  /{vname} exch def")

        for ci, comp in enumerate(("C", "M", "Y", "K")):
            terms: list[str] = []
            for i, code in enumerate(channels):
                w = _INK_TO_CMYK.get(code, (0.0, 0.0, 0.0, 0.0))[ci]
                if w > 0.0:
                    terms.append(
                        varnames[i] if w == 1.0
                        else f"{varnames[i]} {w:.3f} mul"
                    )
            if not terms:
                lines.append(f"  0.0  % {comp}")
            else:
                expr = terms[0]
                for t in terms[1:]:
                    expr += f" {t} add"
                lines.append(f"  {expr} 1.0 min  % {comp}")

        lines.append("  end")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # PS document assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ps(
        w_px: int, h_px: int,
        tiff_w_pt: float, tiff_h_pt: float,
        page_w_pt: float, page_h_pt: float,
        bits: int, n_ch: int,
        cs_block: str,
        hex_data: str,
        level: int,
    ) -> str:
        # Centre the TIFF on the (possibly larger) page. When page_size_pt
        # matches the TIFF, this collapses to the original behaviour.
        x_off = (page_w_pt - tiff_w_pt) / 2.0
        y_off = (page_h_pt - tiff_h_pt) / 2.0
        return (
            f"%!PS-Adobe-3.0\n"
            f"%%LanguageLevel: {level}\n"
            f"%cupsJobTicket: cups-disable-cmm\n"
            f"%%EndComments\n"
            f"\n"
            f"<< /PageSize [{page_w_pt:.2f} {page_h_pt:.2f}] /ImagingBBox null >> setpagedevice\n"
            f"\n"
            f"{cs_block}\n"
            f"\n"
            f"{x_off:.2f} {y_off:.2f} translate\n"
            f"{tiff_w_pt:.2f} {tiff_h_pt:.2f} scale\n"
            f"{w_px} {h_px} {bits}\n"
            f"[{w_px} 0 0 -{h_px} 0 {h_px}]\n"
            f"currentfile /ASCIIHexDecode filter\n"
            f"false {n_ch}\n"
            f"colorimage\n"
            f"{hex_data}\n"
            f">\n"
            f"\n"
            f"showpage\n"
            f"%%EOF\n"
        )


class PdfGenerator:
    """Convert a TIFF profiling target to a minimal PDF (bypass colour management)."""

    def generate(
        self,
        tiff_path: Path,
        dpi: int = 300,
        ink_channels: list[str] | None = None,
    ) -> bytes:
        """Return a complete PDF as bytes for *tiff_path*."""
        arr, actual_dpi = PostScriptGenerator._read_tiff(tiff_path, fallback_dpi=dpi)
        h, w, n_ch = arr.shape
        bits = 16 if arr.dtype == np.uint16 else 8
        pts_w = w * _PT_PER_INCH / actual_dpi
        pts_h = h * _PT_PER_INCH / actual_dpi

        log.debug(
            "PDF: %s  %dx%d px  %.1f×%.1f pt  %d dpi  %d-bit  %d-ch",
            tiff_path.name, w, h, pts_w, pts_h, actual_dpi, bits, n_ch,
        )

        raw = arr.astype(">u2").tobytes() if bits == 16 else arr.tobytes()
        img_data = zlib.compress(raw, 6)
        cs_str = self._pdf_colorspace(n_ch, ink_channels)
        content = (
            f"q\n{pts_w:.4f} 0 0 {pts_h:.4f} 0 0 cm\n/Im Do\nQ"
        ).encode("ascii")

        has_tint_fn = n_ch > 4
        n_objs = 6 if has_tint_fn else 5
        obj_bodies: list[bytes] = [b""] * (n_objs + 1)

        obj_bodies[1] = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        obj_bodies[2] = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        obj_bodies[3] = (
            f"3 0 obj\n"
            f"<< /Type /Page\n"
            f"   /MediaBox [0 0 {pts_w:.4f} {pts_h:.4f}]\n"
            f"   /Parent 2 0 R\n"
            f"   /Resources << /XObject << /Im 5 0 R >> >>\n"
            f"   /Contents 4 0 R >>\n"
            f"endobj\n"
        ).encode("ascii")
        obj_bodies[4] = (
            f"4 0 obj\n<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content + b"\nendstream\nendobj\n"
        )
        obj_bodies[5] = (
            f"5 0 obj\n"
            f"<< /Type /XObject /Subtype /Image\n"
            f"   /Width {w} /Height {h}\n"
            f"   /BitsPerComponent {bits}\n"
            f"   /ColorSpace {cs_str}\n"
            f"   /Filter /FlateDecode\n"
            f"   /Length {len(img_data)} >>\n"
            f"stream\n"
        ).encode("ascii") + img_data + b"\nendstream\nendobj\n"

        if has_tint_fn:
            tint_body = self._pdf_tint_fn_body(n_ch).encode("ascii")
            domain = " ".join(["0 1"] * n_ch)
            obj_bodies[6] = (
                f"6 0 obj\n"
                f"<< /FunctionType 4\n"
                f"   /Domain [{domain}]\n"
                f"   /Range [0 1 0 1 0 1 0 1]\n"
                f"   /Length {len(tint_body)} >>\n"
                f"stream\n"
            ).encode("ascii") + tint_body + b"\nendstream\nendobj\n"

        return self._assemble_pdf(obj_bodies, n_objs)

    def _pdf_colorspace(self, n_ch: int, ink_channels: list[str] | None) -> str:
        if n_ch == 1:
            return "/DeviceGray"
        if n_ch == 3:
            return "/DeviceRGB"
        if n_ch == 4:
            return "/DeviceCMYK"
        channels = (
            list(ink_channels[:n_ch]) if ink_channels and len(ink_channels) >= n_ch
            else [f"ink{i + 1}" for i in range(n_ch)]
        )
        names = " ".join(
            f"/{_INK_CODE_TO_PS_NAME.get(c, c.capitalize())}" for c in channels
        )
        return f"[/DeviceN [{names}] /DeviceCMYK 6 0 R]"

    @staticmethod
    def _pdf_tint_fn_body(n_ch: int) -> str:
        """PDF Type 4 tint function: N inks → CMYK via grayscale-average-to-K mapping.

        PDF Type 4 restricts operators to arithmetic/boolean/stack — no def/begin/end.
        """
        adds = (" ".join(["add"] * (n_ch - 1)) + " ") if n_ch > 1 else ""
        return f"{{ {adds}{n_ch} div dup 1 gt {{ pop 1 }} if 0 0 0 4 1 roll }}"

    @staticmethod
    def _assemble_pdf(obj_bodies: list[bytes], n_objs: int) -> bytes:
        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        parts: list[bytes] = [header]
        offsets: list[int] = [0] * (n_objs + 1)
        pos = len(header)
        for i in range(1, n_objs + 1):
            offsets[i] = pos
            parts.append(obj_bodies[i])
            pos += len(obj_bodies[i])

        xref_pos = pos
        xref_lines: list[bytes] = [
            b"xref\n",
            f"0 {n_objs + 1}\n".encode("ascii"),
            b"0000000000 65535 f\r\n",
        ]
        for i in range(1, n_objs + 1):
            xref_lines.append(f"{offsets[i]:010d} 00000 n\r\n".encode("ascii"))
        parts.extend(xref_lines)

        trailer = (
            f"trailer\n<< /Size {n_objs + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
        parts.append(trailer)
        return b"".join(parts)
