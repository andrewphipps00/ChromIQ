"""Vector text for the PDF chart: glyph outlines of the engine's exact fonts (#72).

The engine picks a font file for every label / caption via ``raster._font_path``
and Pillow draws it on the TIFF. To make the PDF match, we load *that same file*
with freetype at the *same pixel size*, so glyph advances equal Pillow's metrics,
pull each glyph outline and convert it to PDF path operators. The **anchor**
(left-x and baseline) is computed by the caller with Pillow's own metrics and
handed in, so horizontal/vertical placement matches the TIFF by construction —
only the glyph rasterisation (vector outline vs Pillow's antialiased raster)
differs. Quadratic (TrueType) contours become the cubic Béziers PDF uses.
"""
from __future__ import annotations

import math

import freetype

_FACE_CACHE: dict[str, freetype.Face] = {}


def _face(path: str) -> freetype.Face:
    key = str(path)
    f = _FACE_CACHE.get(key)
    if f is None:
        f = freetype.Face(key)
        _FACE_CACHE[key] = f
    return f


def _prepare(font_path: str, size_px: float, variation: str | None) -> freetype.Face:
    face = _face(font_path)
    if variation:
        try:
            names = [(_n.decode() if isinstance(_n, bytes) else _n)
                     for _n in face.get_variation_names()]
            want = variation.replace(" ", "").lower()
            for i, nm in enumerate(names):
                if nm.replace(" ", "").lower() == want:
                    face.set_var_named_instance(i)
                    break
        except Exception:
            pass
    face.set_pixel_sizes(0, max(1, int(round(size_px))))
    return face


def ascent_px(font_path: str, size_px: float, variation: str | None = None) -> float:
    """Pixel ascent at *size_px* — used by the caller to derive the baseline."""
    face = _prepare(font_path, size_px, variation)
    return face.size.ascender / 64.0


def _glyph_ops(face: freetype.Face, ch: str, ox: float, oy: float, px2pt: float
               ) -> tuple[list[str], float]:
    """PDF path ops for one glyph placed with its origin at (*ox*, *oy*) points
    (baseline), plus the pen advance in points. Outline units are 26.6 px."""
    face.load_char(ch, freetype.FT_LOAD_NO_HINTING)
    outline = face.glyph.outline
    advance = (face.glyph.advance.x / 64.0) * px2pt
    ops: list[str] = []
    cur = [0.0, 0.0]

    def P(v):
        return ox + (v.x / 64.0) * px2pt, oy + (v.y / 64.0) * px2pt

    def move_to(a, _c):
        x, y = P(a); ops.append(f"{x:.3f} {y:.3f} m"); cur[0], cur[1] = x, y

    def line_to(a, _c):
        x, y = P(a); ops.append(f"{x:.3f} {y:.3f} l"); cur[0], cur[1] = x, y

    def conic_to(q, to, _c):
        qx, qy = P(q); x, y = P(to)
        c1x = cur[0] + 2 / 3 * (qx - cur[0]); c1y = cur[1] + 2 / 3 * (qy - cur[1])
        c2x = x + 2 / 3 * (qx - x); c2y = y + 2 / 3 * (qy - y)
        ops.append(f"{c1x:.3f} {c1y:.3f} {c2x:.3f} {c2y:.3f} {x:.3f} {y:.3f} c")
        cur[0], cur[1] = x, y

    def cubic_to(a, b, to, _c):
        ax, ay = P(a); bx, by = P(b); x, y = P(to)
        ops.append(f"{ax:.3f} {ay:.3f} {bx:.3f} {by:.3f} {x:.3f} {y:.3f} c")
        cur[0], cur[1] = x, y

    outline.decompose(move_to=move_to, line_to=line_to,
                      conic_to=conic_to, cubic_to=cubic_to)
    if ops:
        ops.append("h")
    return ops, advance


def run_ops(text: str, font_path: str, size_px: float,
            left_x_pt: float, baseline_y_pt: float, px2pt: float,
            rgb01: tuple[float, float, float], *,
            spacing_pt: float = 0.0, rotation_deg: int = 0,
            variation: str | None = None) -> str:
    """A PDF fragment filling *text* as vector glyphs, left edge at *left_x_pt*
    and baseline at *baseline_y_pt* (page points). *spacing_pt* adds between
    letters (matching the engine's INDICATOR_LETTER_SPACING). *rotation_deg*
    rotates the whole run about the (left, baseline) anchor."""
    if not text or not font_path:
        return ""
    face = _prepare(font_path, size_px, variation)
    rot = rotation_deg % 360
    if rot:
        ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        frame = (f"q {ca:.6f} {sa:.6f} {-sa:.6f} {ca:.6f} "
                 f"{left_x_pt:.3f} {baseline_y_pt:.3f} cm")
        ox0, oy0 = 0.0, 0.0                       # glyphs laid in the rotated frame
    else:
        frame = "q"
        ox0, oy0 = left_x_pt, baseline_y_pt

    glyph_ops: list[str] = []
    pen = 0.0
    for ch in text:
        sub, adv = _glyph_ops(face, ch, ox0 + pen, oy0, px2pt)
        glyph_ops.extend(sub)
        pen += adv + spacing_pt
    if not glyph_ops:
        return ""
    r, g, b = rgb01
    return f"{frame}\n{r:.4f} {g:.4f} {b:.4f} rg\n" + " ".join(glyph_ops) + "\nf\nQ"
