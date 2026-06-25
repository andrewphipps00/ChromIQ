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

_SYSTEM_FONT_MAP: dict[str, str] | None = None


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


def _system_font_map() -> dict[str, str]:
    """Lazy family→file map for installed fonts (so any system font can render)."""
    global _SYSTEM_FONT_MAP
    if _SYSTEM_FONT_MAP is not None:
        return _SYSTEM_FONT_MAP
    out: dict[str, str] = {}
    for d in _system_font_dirs():
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                fam = ImageFont.truetype(str(f), 12).getname()[0]
            except Exception:
                continue
            out.setdefault(fam, str(f))
    _SYSTEM_FONT_MAP = out
    return out


def _font_path(family: str) -> str | None:
    if family in FONTS:
        return resource_path(FONTS[family])
    return _system_font_map().get(family)


def _font(px: int, family: str = DEFAULT_INDICATOR_FONT
          ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path(family) or resource_path(FONTS[DEFAULT_INDICATOR_FONT])
    try:
        return ImageFont.truetype(path, max(6, px))
    except Exception:  # pragma: no cover - font load fallback
        return ImageFont.load_default()


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
    font = _font(px(effective_indicator_size_mm(
        geom, dpi, indicator_font, indicator_size_mm)), indicator_font)

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
        images.append(img)

    flagged = contrast.low_contrast_passes(rgb_by_slot, steps)
    return RenderResult(images=images, low_contrast_passes=flagged)


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
