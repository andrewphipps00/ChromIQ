"""Render the chart page TIFF(s) — Qt-free, via Pillow + tifffile.

Places each patch at the *same* slot the ``.ti2`` assigns it (shared seeded
permutation), so the printed raster and the measurement file can't disagree.
Draws colour patches, contrast-chosen spacers, and per-column strip indicators.
TIFFs are written in pixels-per-centimetre (ResolutionUnit=3) exactly like
printtarg, so the existing `page_geometry` / print pipeline read the DPI right.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

from core.resource_path import resource_path

from . import contrast, geometry, permutation
from .colorants import to_display_rgb
from .geometry import Layout
from .instruments import Geom
from .ti1_reader import ColorTarget

# Bundled free fonts available for on-chart text (OFL).
FONTS = {
    "JetBrains Mono": "assets/fonts/JetBrainsMono-VariableFont_wght.ttf",
    "Inter": "assets/fonts/Inter-VariableFont_opsz,wght.ttf",
    "Instrument Serif": "assets/fonts/InstrumentSerif-Regular.ttf",
}
DEFAULT_INDICATOR_FONT = "JetBrains Mono"

# Masthead wordmark styling (ui.masthead_header): Instrument Serif, "Chrom" in
# near-black, "IQ" bold-italic in the magenta accent.
WORDMARK_FONT = "Instrument Serif"
WORDMARK_RGB = (28, 27, 24)     # #1c1b18 — light-mode "Chrom" colour
WORDMARK_IQ_RGB = (255, 69, 115)  # #ff4573 — magenta accent for "IQ"

# ChromIQ accent palette (ui.styles TAB_COLORS) as RGB, for the coloured
# under-indicator rule; cycled per strip so adjacent strips read distinctly.
ACCENT_RGB = (
    (255, 69, 115),    # magenta
    (255, 180, 45),    # amber
    (86, 214, 165),    # green
    (55, 188, 214),    # cyan
    (159, 130, 255),   # violet
)

_SYSTEM_FONT_MAP: dict[str, dict[str, str]] | None = None


def _system_font_dirs() -> list[Path]:
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                home / "Library/Fonts"]
    if sys.platform.startswith("win"):
        import os
        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            home / ".fonts", home / ".local/share/fonts"]


def _style_key(subfamily: str) -> str:
    s = (subfamily or "").lower()
    b = "bold" in s
    i = "italic" in s or "oblique" in s
    return ("bolditalic" if b and i else "bold" if b else "italic" if i else "regular")


def _system_font_map() -> dict[str, dict[str, str]]:
    """Lazy family→{style: file} map for installed fonts.

    Per family we record which style faces exist (regular/bold/italic/
    bolditalic) so we can both render the right face *and* report truthfully
    which styles a font actually supports.
    """
    global _SYSTEM_FONT_MAP
    if _SYSTEM_FONT_MAP is not None:
        return _SYSTEM_FONT_MAP
    out: dict[str, dict[str, str]] = {}
    for d in _system_font_dirs():
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                fam, sub = ImageFont.truetype(str(f), 12).getname()
            except Exception:
                continue
            out.setdefault(fam, {}).setdefault(_style_key(sub or ""), str(f))
    _SYSTEM_FONT_MAP = out
    return out


def _font_path(family: str, style: str = "regular") -> str | None:
    if family in FONTS:
        return resource_path(FONTS[family])
    faces = _system_font_map().get(family)
    if not faces:
        return None
    return faces.get(style) or faces.get("regular") or next(iter(faces.values()))


def font_supports(family: str) -> tuple[bool, bool]:
    """``(has_bold, has_italic)`` as the engine can actually render *family*.

    Bundled variable fonts are probed via their named instances; system fonts
    by which separate style faces are installed.  This is the single source of
    truth shared by the renderer and the UI's bold/italic enable logic.
    """
    if family in FONTS:
        try:
            f = ImageFont.truetype(resource_path(FONTS[family]), 12)
            low = [(_n.decode() if isinstance(_n, bytes) else _n).replace(" ", "").lower()
                   for _n in f.get_variation_names()]
        except Exception:
            return (False, False)
        return (any("bold" in n for n in low),
                any(("italic" in n or "oblique" in n) for n in low))
    faces = _system_font_map().get(family, {})
    return ("bold" in faces or "bolditalic" in faces,
            "italic" in faces or "bolditalic" in faces)


def _font(px: int, family: str = DEFAULT_INDICATOR_FONT,
          bold: bool = False, italic: bool = False
          ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    style = ("bolditalic" if bold and italic else "bold" if bold
             else "italic" if italic else "regular")
    path = _font_path(family, style) or resource_path(FONTS[DEFAULT_INDICATOR_FONT])
    try:
        f = ImageFont.truetype(path, max(6, px))
    except Exception:  # pragma: no cover - font load fallback
        return ImageFont.load_default()
    if bold or italic:
        want = ("Bold Italic" if bold and italic else "Bold" if bold else "Italic")
        want_key = want.replace(" ", "").lower()
        try:    # variable fonts (our bundled ones) expose named instances
            for n in f.get_variation_names():
                name = n.decode() if isinstance(n, bytes) else n
                if name.replace(" ", "").lower() == want_key:
                    f.set_variation_by_name(n)
                    break
        except Exception:
            pass    # static font without that instance — render regular
    return f


_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def effective_indicator_size_mm(geom, dpi: int, font: str, size_mm: float) -> float:
    """The indicator font size to use. An explicit *size_mm* is returned as-is;
    *size_mm* 0 = auto, where the size is chosen so the widest two-letter label
    fits the strip width (capped at the instrument text height)."""
    if size_mm:
        return float(size_mm)
    mm2px = dpi / 25.4
    target = geom.txhisl
    f = _font(max(6, round(target * mm2px)), font)
    try:
        widest2 = 2.0 * max(f.getlength(c) for c in _UPPER) / mm2px
    except Exception:
        return target
    return target if widest2 <= geom.pwid else target * geom.pwid / widest2


def render_clip_strip(mode: str, *, width_px: int, height_px: int, dpi: int,
                      text: str = "", font_family: str = "Inter",
                      image_path: str = "") -> Image.Image:
    """Render the left clip-strip content as a ``width_px × height_px`` image.

    The strip is tall and narrow, so text/branding are drawn on a landscape
    canvas and rotated 90° to read up the strip. Shared by the page renderer and
    the standalone template export.
    """
    mm2px = dpi / 25.4
    strip = Image.new("RGB", (max(1, width_px), max(1, height_px)), (255, 255, 255))

    if mode == "image" and image_path:
        try:
            logo = Image.open(image_path).convert("RGBA")
            scale = min(width_px / logo.width, height_px / logo.height)
            nw, nh = max(1, int(logo.width * scale)), max(1, int(logo.height * scale))
            logo = logo.resize((nw, nh))
            strip.paste(logo, ((width_px - nw) // 2, (height_px - nh) // 2), logo)
        except Exception:  # pragma: no cover - bad/missing image falls back blank
            pass
        return strip

    if mode == "notes":
        d = ImageDraw.Draw(strip)
        lw = max(1, round(0.3 * mm2px))
        d.rectangle([0, 0, width_px - 1, height_px - 1], outline=(0, 0, 0), width=lw)
        rule_gap = max(1, round(12.0 * mm2px))          # ~12 mm ruled lines
        y = rule_gap
        while y < height_px - rule_gap // 2:
            d.line([(round(2 * mm2px), y), (width_px - round(2 * mm2px), y)],
                   fill=(170, 170, 170), width=max(1, lw // 2))
            y += rule_gap
        caption = text or "Sample / Notes"
        cap = _vtext(caption, font_family, width_px, height_px,
                     valign="top", bold=True)
        strip.paste(cap, (0, 0), cap)
        return strip

    if mode == "branding":
        extra = [ln for ln in (text or "").splitlines() if ln.strip()]
        overlay = _vwordmark(extra, width_px, height_px)
        strip.paste(overlay, (0, 0), overlay)
        return strip

    # plain text → rotated text up the strip
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return strip
    overlay = _vtext("\n".join(lines), font_family, width_px, height_px)
    strip.paste(overlay, (0, 0), overlay)
    return strip


def _italic_tile(text: str, font, fill: tuple, stroke_w: int,
                 shear: float = 0.22) -> Image.Image:
    """Render *text* (faux-bold via stroke) and shear it right for faux-italic."""
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = stroke_w + 2
    tile = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((pad - bbox[0], pad - bbox[1]), text, font=font,
                              fill=fill, stroke_width=stroke_w, stroke_fill=fill)
    H = tile.height
    # AFFINE maps output→input: input_x = x + shear*(H - y) leans the top right.
    return tile.transform(
        (tile.width + int(H * shear), H), Image.AFFINE,
        (1, shear, -shear * H, 0, 1, 0), resample=Image.BICUBIC)


def _vwordmark(extra_lines: list[str], width_px: int, height_px: int) -> Image.Image:
    """The masthead "ChromIQ" wordmark — Instrument Serif, "Chrom" near-black,
    "IQ" bold-italic in magenta — plus optional lines, read up the strip."""
    canvas = Image.new("RGBA", (max(1, height_px), max(1, width_px)), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    n = 1 + len(extra_lines)
    chrom_fill = WORDMARK_RGB + (255,)
    iq_fill = WORDMARK_IQ_RGB + (255,)
    size = max(10, int(width_px * 0.55))
    for _ in range(40):
        f = _font(size, WORDMARK_FONT)
        wm_w = d.textlength("Chrom", font=f) + d.textlength("IQ", font=f) * 1.25
        widest = max([wm_w] + [d.textlength(l, font=f) for l in extra_lines])
        if size * 1.25 * n <= width_px * 0.92 and widest <= height_px * 0.95:
            break
        size = int(size * 0.9)
        if size <= 10:
            break
    f = _font(size, WORDMARK_FONT)
    line_h = size * 1.25
    cy = (width_px - line_h * n) / 2
    iq_tile = _italic_tile("IQ", f, iq_fill, 0)   # same face as "Chrom", just italic
    _b = iq_tile.getbbox()                 # trim transparent padding for tight kern
    if _b:
        iq_tile = iq_tile.crop(_b)
    chrom_w = d.textlength("Chrom", font=f)
    kern = size * 0.04
    wm_w = chrom_w + kern + iq_tile.width
    x = (height_px - wm_w) / 2
    y = cy + line_h * 0.5
    try:
        d.text((x, y), "Chrom", font=f, fill=chrom_fill, anchor="lm")
        canvas.paste(iq_tile, (int(x + chrom_w + kern), int(y - iq_tile.height / 2)),
                     iq_tile)
        for i, ln in enumerate(extra_lines, start=1):
            d.text((height_px / 2, cy + line_h * (i + 0.5)), ln, font=f,
                   fill=chrom_fill, anchor="mm")
    except Exception:  # pragma: no cover - default font without anchor
        d.text((x, y), "ChromIQ", font=f, fill=chrom_fill)
    return canvas.rotate(90, expand=True)


def _vtext(text: str, font_family: str, width_px: int, height_px: int,
           *, valign: str = "center", bold: bool = False) -> Image.Image:
    """A transparent ``width_px × height_px`` overlay with *text* read up the strip."""
    # Draw on a landscape canvas (long = height_px, short = width_px), rotate 90°.
    canvas = Image.new("RGBA", (max(1, height_px), max(1, width_px)), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    lines = text.split("\n")
    size = max(8, int(width_px * 0.42))
    f = _font(size, font_family, bold=bold)
    # shrink to fit the short dimension across all stacked lines
    for _ in range(40):
        f = _font(size, font_family, bold=bold)
        line_h = size * 1.2
        block_h = line_h * len(lines)
        widest = max((d.textlength(ln, font=f) for ln in lines), default=0)
        if block_h <= width_px * 0.9 and widest <= height_px * 0.95:
            break
        size = int(size * 0.9)
        if size <= 8:
            break
    line_h = size * 1.2
    block_h = line_h * len(lines)
    cy = (width_px - block_h) / 2
    cx = (height_px * 0.04 if valign == "top" else height_px / 2)
    anchor = "lm" if valign == "top" else "mm"
    for i, ln in enumerate(lines):
        y = cy + line_h * (i + 0.5)
        try:
            d.text((cx, y), ln, font=f, fill=(0, 0, 0, 255), anchor=anchor)
        except Exception:  # pragma: no cover
            d.text((cx, y), ln, font=f, fill=(0, 0, 0, 255))
    return canvas.rotate(90, expand=True)


@dataclass(frozen=True)
class RenderResult:
    images: list[Image.Image]
    low_contrast_passes: list[int]   # global pass indices flagged by the guard


def render_pages(
    target: ColorTarget,
    layout: Layout,
    geom: Geom,
    *,
    seed: int,
    randomize: bool = True,
    paper_w_mm: float,
    paper_h_mm: float,
    dpi: int = 300,
    strip_pattern: str = permutation.DEFAULT_STRIP_PATTERN,
    spacer_mode: str = "colored",
    draw_indicators: bool = True,
    indicator_font: str = DEFAULT_INDICATOR_FONT,
    indicator_size_mm: float = 0.0,
    indicator_bold: bool = False,
    indicator_italic: bool = False,
    underline_mode: str = "off",
    underline_thickness_mm: float = 0.5,
    underline_gap_mm: float = 0.5,
    chart_text: str = "",
    chart_text_font: str = "Inter",
    chart_text_size_mm: float = 0.0,
    chart_text_bold: bool = False,
    chart_text_italic: bool = False,
    stamp_text: str = "",
    clip_content_mode: str = "off",
    clip_text: str = "",
    clip_text_font: str = "Inter",
    clip_image_path: str = "",
) -> RenderResult:
    """Render one :class:`PIL.Image` per page for *target*.

    *spacer_mode* picks the inter-patch spacer colour: ``"colored"`` (default,
    like printtarg) or ``"bw"``.  No spacers are drawn when the geometry has no
    gap (``spacer_mode`` ``"none"`` ⇒ build with ``spacer_on=False``).
    """
    mm2px = dpi / 25.4
    W = max(1, round(paper_w_mm * mm2px))
    H = max(1, round(paper_h_mm * mm2px))

    # Patch list incl. padding, then slot assignment (identical to ti2_writer).
    media = target.media_patch()
    patches = list(target.patches) + [media] * layout.padding
    total = len(patches)
    slots = permutation.location_permutation(total, seed, randomize)
    rgb_by_slot: list[tuple[int, int, int]] = [(255, 255, 255)] * total
    for i, (dev, _xyz) in enumerate(patches):
        rgb_by_slot[slots[i]] = to_display_rgb(dev, target.color_rep)

    place = geometry.placement(geom, paper_w_mm, paper_h_mm, layout)
    steps = layout.steps_in_pass
    pppage = layout.patches_per_page
    label_strip = permutation.make_labeller(strip_pattern)

    def px(mm: float) -> int:
        return round(mm * mm2px)

    pw_px, pl_px = px(place.pwid), px(place.plen)
    sp_px = px(place.pspa)
    ind_px = px(effective_indicator_size_mm(
        geom, dpi, indicator_font, indicator_size_mm))
    font = _font(ind_px, indicator_font, indicator_bold, indicator_italic)
    if underline_mode == "colored":          # legacy alias → 5-segment bar
        underline_mode = "segments"
    underline_on = draw_indicators and underline_mode in ("segments", "cycle", "black")
    ul_th = max(1, px(underline_thickness_mm or 0.5))
    ul_gap = px(underline_gap_mm)

    images: list[Image.Image] = []
    for page in range(layout.pages):
        img = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        first = page * pppage
        last = min(total, first + pppage)
        n_on_page = last - first
        n_passes = (n_on_page + steps - 1) // steps

        for p in range(n_passes):
            x0 = px(place.x_of(p))
            global_strip = (first // steps) + p
            col_slots = list(range(first + p * steps,
                                   min(last, first + (p + 1) * steps)))
            if draw_indicators:
                _lbl = label_strip(global_strip + 1)
                _cx = x0 + pw_px // 2          # centre over the strip
                _y = px(place.leader_top)
                try:
                    draw.text((_cx, _y), _lbl, font=font, fill=(0, 0, 0), anchor="ma")
                except Exception:             # default bitmap font: no anchor
                    _tw = int(draw.textlength(_lbl, font=font))
                    draw.text((_cx - _tw // 2, _y), _lbl, font=font, fill=(0, 0, 0))
                if underline_on and underline_mode == "cycle":   # one accent / strip
                    _ly = _y + ind_px + ul_gap
                    draw.rectangle([x0, _ly, x0 + pw_px - 1, _ly + ul_th - 1],
                                   fill=ACCENT_RGB[global_strip % len(ACCENT_RGB)])
            for j, gslot in enumerate(col_slots):
                y0 = px(place.y_of(j))
                rgb = rgb_by_slot[gslot]
                draw.rectangle([x0, y0, x0 + pw_px - 1, y0 + pl_px - 1], fill=rgb)
                if sp_px > 0 and j + 1 < len(col_slots):
                    nxt = rgb_by_slot[col_slots[j + 1]]
                    draw.rectangle(
                        [x0, y0 + pl_px, x0 + pw_px - 1, y0 + pl_px + sp_px - 1],
                        fill=contrast.spacer_for_mode(spacer_mode, rgb, nxt),
                    )
        # Full-width rule under the whole label row (one continuous line):
        # "segments" splits it into the five accents across the entire width;
        # "black" is a single plain line. ("cycle" is drawn per strip above.)
        if (draw_indicators and underline_mode in ("segments", "black")
                and n_passes > 0):
            _ly = px(place.leader_top) + ind_px + ul_gap
            _yb = _ly + ul_th - 1
            x_left = px(place.x_of(0))
            x_right = px(place.x_of(n_passes - 1)) + pw_px - 1
            if underline_mode == "black":
                draw.rectangle([x_left, _ly, x_right, _yb], fill=(0, 0, 0))
            else:                                     # 5 equal segments full-width
                _span = x_right - x_left + 1
                _n = len(ACCENT_RGB)
                for _k in range(_n):
                    _sx0 = x_left + round(_span * _k / _n)
                    _sx1 = x_left + round(_span * (_k + 1) / _n) - 1
                    draw.rectangle([_sx0, _ly, _sx1, _yb], fill=ACCENT_RGB[_k])

        # Left clip-strip content (i1/p3): rendered natively into the reserved
        # lbord band, since the engine knows its exact geometry.
        if clip_content_mode != "off":
            _area = geometry.clip_area_px(geom, paper_h_mm, dpi)
            if _area is not None and _area[2] > 0 and _area[3] > 0:
                _ax, _ay, _aw, _ah = _area
                _clip = render_clip_strip(
                    clip_content_mode, width_px=_aw, height_px=_ah, dpi=dpi,
                    text=clip_text, font_family=clip_text_font,
                    image_path=clip_image_path)
                img.paste(_clip, (_ax, _ay))

        # Bottom-of-sheet text: custom chart text + optional command stamp,
        # drawn in the bottom margin (clear of the patches).
        _btxt = [t for t in (chart_text, stamp_text) if t]
        if _btxt:
            sfont = _font(px(chart_text_size_mm or 3.2), chart_text_font,
                          chart_text_bold, chart_text_italic)
            line_h = px(4.2)
            yy = H - px(1.5) - line_h * len(_btxt)
            for ln in _btxt:
                draw.text((px(geom.margin_l), yy), ln, font=sfont, fill=(0, 0, 0))
                yy += line_h
        images.append(img)

    flagged = contrast.low_contrast_passes(rgb_by_slot, steps)
    return RenderResult(images=images, low_contrast_passes=flagged)


def export_clip_template(out_base: str | Path, *, width_px: int, height_px: int,
                         width_mm: float, height_mm: float, dpi: int) -> list[Path]:
    """Write blank clip-strip design templates at the exact clip size.

    Produces ``<out_base>.png`` (pixels at *dpi*) and ``<out_base>.pdf`` (sized
    in mm) so a user can design a graphic in another tool and import it back at a
    perfect fit.  A faint border + corner ticks + a dimension caption mark the
    bounds and orientation; they sit on a separate guide layer so the user can
    delete them.  Returns the written paths.
    """
    base = Path(out_base).with_suffix("")
    mm2px = dpi / 25.4
    img = Image.new("RGB", (max(1, width_px), max(1, height_px)), (255, 255, 255))
    d = ImageDraw.Draw(img)
    guide = (200, 200, 200)
    d.rectangle([0, 0, width_px - 1, height_px - 1], outline=guide, width=1)
    tick = max(3, round(3 * mm2px))               # corner crop ticks
    for cx, cy in ((0, 0), (width_px - 1, 0), (0, height_px - 1),
                   (width_px - 1, height_px - 1)):
        d.line([(cx, cy), (cx + (tick if cx == 0 else -tick), cy)], fill=guide, width=2)
        d.line([(cx, cy), (cx, cy + (tick if cy == 0 else -tick))], fill=guide, width=2)
    cap = f"{width_mm:.0f} × {height_mm:.0f} mm @ {dpi} dpi"
    overlay = _vtext(cap, "Inter", width_px, height_px, valign="top")
    img.paste(overlay, (0, 0), overlay)
    out: list[Path] = []
    png = base.with_suffix(".png")
    img.save(str(png), dpi=(dpi, dpi))
    out.append(png)
    pdf = base.with_suffix(".pdf")
    img.save(str(pdf), "PDF", resolution=float(dpi))  # px/dpi → exact physical mm
    out.append(pdf)
    return out


def save_tiffs(images: list[Image.Image], base_path: str | Path, dpi: int = 300,
               *, bit16: bool = False, compression: str = "lzw") -> list[Path]:
    """Write *images* as TIFF(s) in px/cm (ResolutionUnit=3); return paths.

    Single page → ``base.tif``; multiple → ``base_01.tif`` ….  *bit16* writes
    16-bit channels (8-bit values scaled up); *compression* is the tifffile
    codec name ("lzw", "zlib", or "none").
    """
    base = Path(base_path)
    stem = base.with_suffix("")
    res = dpi / 2.54  # pixels per centimetre, matching printtarg
    comp = None if compression in ("none", "", None) else compression
    out: list[Path] = []
    for i, img in enumerate(images):
        arr = np.asarray(img)
        if bit16:
            arr = (arr.astype(np.uint16) * 257)   # 8-bit → 16-bit (×257)
        path = base if len(images) == 1 else stem.parent / f"{stem.name}_{i + 1:02d}.tif"
        tifffile.imwrite(
            str(path), arr, photometric="rgb",
            resolution=(res, res), resolutionunit=3, compression=comp,
        )
        out.append(path)
    return out
