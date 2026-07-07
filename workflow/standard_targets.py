"""Discover the standard scanner-target ``.cht`` recognition files that ship
with ArgyllCMS (in ``<argyll>/ref/``), so ChromIQ can profile a scanner from a
target the user physically owns (Wolf Faust IT8, LaserSoft, ColorChecker, …)
without generating a chart (Knut #98, ask 2).

We rely on Argyll's own ``.cht`` set — Argyll is a hard dependency, the files
are validated, and bundling copies would only risk version skew. The colour
**reference** (``.cie`` / ``.txt``) is *not* here: it's specific to the user's
physical target batch and must come from the target's vendor.
"""
from __future__ import annotations

from pathlib import Path

from core.resource_path import resource_path

# Friendly names for the well-known targets (filename stem → display). Anything
# not listed falls back to its stem. Ordered-priority list decides the combo
# order (most common scanner targets first).
_FRIENDLY: dict[str, str] = {
    "it8": "IT8.7/2 (generic)",
    "it8Wolf": "IT8 / ISO 12641-1 — Wolf Faust",
    "ISO12641_2_1": "IT8 / ISO 12641-2 — LaserSoft Advanced",
    "ISO12641_2_3_1": "ISO 12641-2 layout 1",
    "ISO12641_2_3_2": "ISO 12641-2 layout 2",
    "ISO12641_2_3_3": "ISO 12641-2 layout 3",
    "LaserSoftDCPro": "LaserSoft DCPro",
    "ColorChecker": "X-Rite ColorChecker (24)",
    "ColorCheckerSG": "ColorChecker SG",
    "ColorCheckerDC": "ColorChecker DC",
    "ColorCheckerPassport": "ColorChecker Passport",
    "ColorCheckerHalfPassport": "ColorChecker Passport (half)",
    "SpyderChecker": "SpyderChecker",
    "SpyderChecker24": "SpyderChecker 24",
    "QPcard_201": "QPcard 201",
    "QPcard_202": "QPcard 202",
    "Hutchcolor": "HutchColor HCT",
    "i1_RGB_Scan_1.4": "i1 RGB Scan 1.4",
    "MLG": "MLG",
    "CMP_Digital_Target-4": "CMP Digital Target 4",
    "CMP_Digital_Target-7": "CMP Digital Target 7",
    "CMP_Digital_Target-2019": "CMP Digital Target 2019",
    "CMP_Digital_Target_Studio": "CMP Digital Target Studio",
    "CMP_DT_003": "CMP DT 003",
    "CMP_DT_mini": "CMP DT mini",
}

# Preferred display order — the common flatbed scanner targets on top.
_ORDER = [
    "it8Wolf", "it8", "ISO12641_2_1", "LaserSoftDCPro",
    "ColorChecker", "ColorCheckerSG", "ColorCheckerDC",
    "ColorCheckerPassport", "ColorCheckerHalfPassport",
    "SpyderChecker", "SpyderChecker24", "QPcard_201", "QPcard_202",
    "Hutchcolor", "i1_RGB_Scan_1.4",
]


def argyll_ref_dir(settings) -> Path | None:
    """The ``ref/`` folder beside the configured Argyll ``bin`` dir, or None if
    it doesn't exist."""
    bin_path = settings.get("argyll_bin_path", "")
    if not bin_path:
        return None
    ref = Path(bin_path).parent / "ref"
    return ref if ref.is_dir() else None


def bundled_targets_dir() -> Path | None:
    """ChromIQ's bundled ``data/scanner_targets`` — Knut Larsson's corrected
    ``.cht`` files (see that folder's README). Preferred over Argyll's ``ref/``
    because several of Argyll's shipped files had wrong fiducial coordinates."""
    d = resource_path("data/scanner_targets")
    return d if d.is_dir() else None


def display_name(cht: Path) -> str:
    return _FRIENDLY.get(cht.stem, cht.stem)


def list_standard_targets(settings) -> list[tuple[str, Path]]:
    """``(display_name, cht_path)`` for every standard target ``.cht`` available,
    common flatbed targets first, then the rest alphabetically. Sources are
    ChromIQ's bundled corrected ``.cht`` (preferred) merged with the user's
    Argyll ``ref/`` (fallback), deduplicated by filename stem."""
    by_stem: dict[str, Path] = {}
    ref = argyll_ref_dir(settings)
    if ref is not None:
        by_stem.update({p.stem: p for p in ref.glob("*.cht")})
    bundled = bundled_targets_dir()
    if bundled is not None:                       # bundled corrected files win
        by_stem.update({p.stem: p for p in bundled.glob("*.cht")})
    if not by_stem:
        return []
    ordered: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for stem in _ORDER:
        p = by_stem.get(stem)
        if p is not None:
            ordered.append((display_name(p), p))
            seen.add(stem)
    for stem in sorted(by_stem):
        if stem not in seen:
            ordered.append((display_name(by_stem[stem]), by_stem[stem]))
    return ordered


def demo_patch_color(i: int, n: int) -> tuple[int, int, int]:
    """Colour for patch *i* of *n* in the demo scan. Deliberately spans dark→light
    with strongly-scrambled neighbours (golden-ratio hue + bit-reversed value), so
    that if the reading grid slips onto a neighbouring cell the colour it picks up
    is very different — turning the demo into a real misalignment detector rather
    than one that smooth gradients could hide (Knut). Deterministic."""
    import colorsys
    phi = 0.6180339887498949
    rev, f, k = 0.0, 0.5, i + 1                 # van der Corput (bit-reversal)
    while k > 0:
        rev += (k & 1) * f
        k >>= 1
        f *= 0.5
    h = (i * phi) % 1.0                          # consecutive hues far apart
    v = 0.16 + 0.80 * rev                        # wide, scrambled lightness
    s = 0.55 + 0.40 * ((i * phi * 2.0) % 1.0)
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v))


def make_test_scan(cht_path, out_dir):
    """Render a known-good test scan (``.tif``) + reference (``.cie``) from a
    target's ``.cht``, so the reading grid can be tried without hardware. Each
    patch is a distinct solid colour; a correctly-placed grid reads them exactly.
    Returns ``(tif_path, cie_path)``."""
    from pathlib import Path as _P
    from PIL import Image
    from workflow.cht_parser import parse_cht
    cht_path = _P(cht_path); out_dir = _P(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    text = cht_path.read_text(errors="ignore")
    boxes = parse_cht(text).patches
    minx = min(b.x1 for b in boxes); maxx = max(b.x2 for b in boxes)
    miny = min(b.y1 for b in boxes); maxy = max(b.y2 for b in boxes)
    scale = 1500.0 / max(maxx - minx, maxy - miny, 1.0); margin = 80
    W = int((maxx - minx) * scale + 2 * margin); H = int((maxy - miny) * scale + 2 * margin)
    from PIL import ImageDraw
    img = Image.new("RGB", (W, H), (236, 236, 236)); draw = ImageDraw.Draw(img)
    # Uniform grids render on rectarg's INTEGER cell edges — the same edges the
    # marquee overlay and the aligned scanin .cht use. Truncating each box's
    # float position separately drifted up to a pixel per cell against those
    # edges mid-grid (the corners stay pinned), so the demo scan itself looked
    # misaligned under the grid (Knut, #108 round 4).
    cell_edges = None
    from ui.scan_grid_marquee import GridSpec   # deferred: demo-scan path only
    grid = GridSpec.from_cht(text)
    if grid.ncols and grid.nrows and grid.cells:
        def _int_edges(total: float, n: int) -> list[int]:
            base = int(total // n); rem = int(round(total - base * n))
            e = [0]
            for i in range(n):
                e.append(e[-1] + base + (1 if i < rem else 0))
            return e
        cell_edges = (_int_edges((maxx - minx) * scale, grid.ncols),
                      _int_edges((maxy - miny) * scale, grid.nrows), grid.cells)
    cie = ['CGATS.17', 'KEYWORD "SAMPLE_LOC"', 'NUMBER_OF_FIELDS 4', 'BEGIN_DATA_FORMAT',
           'SAMPLE_ID XYZ_X XYZ_Y XYZ_Z', 'END_DATA_FORMAT',
           f'NUMBER_OF_SETS {len(boxes)}', 'BEGIN_DATA']
    for i, b in enumerate(boxes):
        r, g, bl = demo_patch_color(i, len(boxes))
        if cell_edges is not None:
            xe, ye, cells = cell_edges
            ci, ri = cells[i]
            x0 = margin + xe[ci]; x1 = margin + xe[ci + 1]
            y0 = margin + ye[ri]; y1 = margin + ye[ri + 1]
        else:
            x0 = int((b.x1 - minx) * scale + margin); y0 = int((b.y1 - miny) * scale + margin)
            x1 = int((b.x2 - minx) * scale + margin); y1 = int((b.y2 - miny) * scale + margin)
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(r, g, bl))
        # Reference Y = the rendered colour's LUMINANCE (not the green channel):
        # the alignment check rank-compares read luminance against reference Y,
        # and the old pseudo-Y capped even a PERFECT demo read at ~0.93
        # agreement — Knut's stringent-floor test flagged flawless placements.
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * bl
        cie.append(f"{b.name} {r / 2.55 * 0.95:.3f} {lum / 2.55:.3f} {bl / 2.55 * 1.09:.3f}")
    cie += ['END_DATA', '']
    # Real flatbeds soften edges (MTF) and add sensor noise; matching both
    # keeps the demo's Check-alignment behaviour calibrated to the same
    # thresholds as real scans (a razor-sharp noise-free render lets
    # sub-patch offsets sample pure colour far longer than any physical
    # scan would, and reads implausibly cleanly). Blur first, then ~1.5 %
    # Gaussian noise on top — the order of a real optical chain (Knut asked
    # for noise; the level is what his real Epson V700 scans actually
    # measure, ~0.6–1.2 % per box). Seeded, so demo scans stay
    # byte-reproducible for the tests.
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(2))
    import numpy as _np
    rng = _np.random.default_rng(42)
    arr = _np.asarray(img, dtype=_np.float64)
    arr = arr + rng.normal(0.0, 0.015 * 255.0, size=arr.shape)
    img = Image.fromarray(_np.clip(arr, 0, 255).astype(_np.uint8))
    tif = out_dir / f"{cht_path.stem}-test.tif"; ref = out_dir / f"{cht_path.stem}-test.cie"
    img.save(tif); ref.write_text("\n".join(cie))
    return tif, ref
