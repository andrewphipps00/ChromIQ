"""Convert an i1Profiler patch set into an Argyll TI1 target.

The reverse of :mod:`workflow.i1profiler_export`. i1Profiler stores a chart's
patch list either as a CxF3 XML (``.pxf``) or a CGATS ASCII table
(``.cgats`` / ``.txt``); both carry only device RGB code values (0..255), no
Argyll-usable colorimetry. ``printtarg``, however, needs each patch's
*approximate* XYZ: it optimises the strip layout so neighbouring patches are
visually distinct enough for a strip/scan reader to find patch boundaries (the
"direction distinction delta E" it reports). A zero-XYZ TI1 collapses that
optimisation to nothing.

We reconstruct that approximate colorimetry the same way Argyll's own targen
does for an uncharacterised RGB target: treat the device code values as sRGB
and convert to XYZ (D65). On the neutral axis this reproduces targen's TI1 XYZ
to within a fraction of a unit, and the ``.pxf`` even tags every patch
``ColorSpecification="sRGB"``.

Scope: RGB only. CMYK / CMYK+N patch sets raise :class:`ValueError` — there is
no standard CMYK->XYZ to make the layout-distinctness data meaningful, and the
RGB case covers the targets this tool exists for (e.g. TC9.18).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class RgbPatch:
    """One target patch, device RGB on the TI1 0..100 scale."""

    r: float
    g: float
    b: float


# --- sRGB (D65) -> XYZ ------------------------------------------------------


def _linearize(c: float) -> float:
    """sRGB code value in 0..1 -> linear-light 0..1."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def srgb_to_xyz(r100: float, g100: float, b100: float) -> tuple[float, float, float]:
    """Device RGB on the TI1 0..100 scale, treated as sRGB, -> XYZ scaled so
    white (100,100,100) is the D65 white point with Y=100."""
    r, g, b = (_linearize(v / 100.0) for v in (r100, g100, b100))
    x = 0.4124 * r + 0.3576 * g + 0.1805 * b
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = 0.0193 * r + 0.1192 * g + 0.9505 * b
    return x * 100.0, y * 100.0, z * 100.0


WHITE_XYZ = srgb_to_xyz(100.0, 100.0, 100.0)


# --- scale normalisation ---------------------------------------------------


def _scale_to_100(triples: list[tuple[float, float, float]]) -> list[RgbPatch]:
    """Normalise parsed RGB triples to the TI1 0..100 device scale.

    i1Profiler RGB is 0..255; an Argyll TI1 is 0..100. If any value exceeds
    100 the set is on the 0..255 scale and is divided by 2.55; otherwise it is
    assumed already 0..100 and passed through.
    """
    peak = max((max(t) for t in triples), default=0.0)
    factor = (100.0 / 255.0) if peak > 100.0 else 1.0
    return [RgbPatch(r * factor, g * factor, b * factor) for r, g, b in triples]


# --- CxF3 .pxf -------------------------------------------------------------


def _localname(tag: str) -> str:
    """Strip the XML namespace, leaving the bare element name."""
    return tag.rsplit("}", 1)[-1]


def parse_pxf(path: Path) -> list[RgbPatch]:
    """Parse a CxF3 ``.pxf`` patch set into RGB patches (TI1 0..100 scale).

    Namespace-agnostic (matches on local element names), so it reads both
    ChromIQ's export and X-Rite / third-party generators.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path.name}: not valid CxF/XML ({exc})") from exc

    triples: list[tuple[float, float, float]] = []
    saw_non_rgb = False
    for obj in root.iter():
        if _localname(obj.tag) != "Object":
            continue
        rgb_el = None
        for el in obj.iter():
            ln = _localname(el.tag)
            if ln == "ColorRGB":
                rgb_el = el
            elif ln in ("ColorCMYK", "ColorCMYKPlusN"):
                saw_non_rgb = True
        if rgb_el is None:
            continue
        vals = {_localname(c.tag): (c.text or "").strip() for c in rgb_el}
        try:
            triples.append((float(vals["R"]), float(vals["G"]), float(vals["B"])))
        except (KeyError, ValueError):
            continue

    if not triples:
        if saw_non_rgb:
            raise ValueError(
                f"{path.name}: patches are CMYK/extended-gamut. TI1 import "
                "supports RGB patch sets only."
            )
        raise ValueError(f"{path.name}: no RGB target patches found.")
    return _scale_to_100(triples)


# --- CGATS .cgats / .txt ---------------------------------------------------


def _rgb_columns(fields: list[str]) -> tuple[int, int, int] | None:
    up = [f.upper() for f in fields]
    for r, g, b in (("RGB_R", "RGB_G", "RGB_B"), ("R", "G", "B")):
        if r in up and g in up and b in up:
            return up.index(r), up.index(g), up.index(b)
    return None


def parse_cgats(path: Path) -> list[RgbPatch]:
    """Parse a CGATS / CTI1 ASCII table (``.cgats``/``.txt``) into RGB patches.

    Handles any CGATS dialect with ``RGB_R``/``RGB_G``/``RGB_B`` (or bare
    ``R``/``G``/``B``) columns; scale (0..100 vs 0..255) is auto-detected.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    fmt: list[str] = []
    rows: list[list[str]] = []
    in_fmt = in_data = False
    for raw in lines:
        line = raw.strip()
        if line == "BEGIN_DATA_FORMAT":
            in_fmt, fmt = True, []
        elif line == "END_DATA_FORMAT":
            in_fmt = False
        elif line == "BEGIN_DATA":
            in_data = True
        elif line == "END_DATA":
            in_data = False
        elif in_fmt:
            fmt.extend(line.split())
        elif in_data and line:
            rows.append(line.split())

    if not fmt or not rows:
        raise ValueError(f"{path.name}: no CGATS data table found.")

    cols = _rgb_columns(fmt)
    if cols is None:
        raise ValueError(
            f"{path.name}: no RGB columns (RGB_R/RGB_G/RGB_B) found — "
            "TI1 import supports RGB patch sets only."
        )
    ir, ig, ib = cols

    triples: list[tuple[float, float, float]] = []
    for row in rows:
        if max(ir, ig, ib) >= len(row):
            continue
        try:
            triples.append((float(row[ir]), float(row[ig]), float(row[ib])))
        except ValueError:
            continue

    if not triples:
        raise ValueError(f"{path.name}: no usable RGB rows.")
    return _scale_to_100(triples)


# --- TI1 emitter -----------------------------------------------------------

# The 9 fixed device-colorant combinations targen stores in its third table
# (DEVICE_COMBINATION_VALUES), in targen's order. printtarg expects this table
# alongside the patch list; for an RGB device the values are fixed, so we can
# regenerate it exactly.
_DEVICE_COMBINATIONS: tuple[tuple[float, float, float], ...] = (
    (100, 100, 100),  # white
    (0, 100, 100),    # cyan
    (100, 0, 100),    # magenta
    (0, 0, 100),      # blue
    (100, 100, 0),    # yellow
    (0, 100, 0),      # green
    (100, 0, 0),      # red
    (0, 0, 0),        # black
    (50, 50, 50),     # mid grey
)

# Density extremes (targen's second table). targen fits these to a device
# model; lacking one, we use the RGB cube corners — a model-free set that spans
# the density range. printtarg only needs the table present and parseable.
_DENSITY_EXTREMES: tuple[tuple[float, float, float], ...] = (
    (0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 100),
    (100, 100, 0), (100, 0, 100), (0, 100, 100), (100, 100, 100),
)


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def _patch_xyz(r: float, g: float, b: float) -> tuple[float, float, float]:
    """sRGB->XYZ for a device patch, flooring pure black to (1,1,1) the way
    targen does so printtarg never sees a zero-luminance (zero-density) patch."""
    x, y, z = srgb_to_xyz(r, g, b)
    if r <= 0.5 and g <= 0.5 and b <= 0.5:
        return 1.0, 1.0, 1.0
    return x, y, z


def _data_row(idx: int, r: float, g: float, b: float) -> str:
    x, y, z = _patch_xyz(r, g, b)
    return f"{idx} {_fmt(r)} {_fmt(g)} {_fmt(b)} {_fmt(x)} {_fmt(y)} {_fmt(z)}"


def _table(header_keywords: list[str], id_field: str,
           rows: list[tuple[int, tuple[float, float, float]]]) -> list[str]:
    lines = [
        "CTI1   ",
        "",
        'DESCRIPTOR "Argyll Calibration Target chart information 1"',
        'ORIGINATOR "ChromIQ"',
        *header_keywords,
        f'CREATED "{datetime.now().strftime("%a %b %d %H:%M:%S %Y")}"',
        "",
        "NUMBER_OF_FIELDS 7",
        "BEGIN_DATA_FORMAT",
        f"{id_field} RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z",
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(rows)}",
        "BEGIN_DATA",
    ]
    lines += [_data_row(idx, *rgb) for idx, rgb in rows]
    lines.append("END_DATA")
    return lines


def write_ti1(patches: list[RgbPatch], out_path: Path) -> Path:
    """Emit a CTI1 ``.ti1`` for ``patches`` (input order preserved).

    Writes the three tables targen produces — the patch list, the density
    extremes, and the fixed device combinations — so printtarg accepts it.
    Device values land on the 0..100 scale; every patch's XYZ is the sRGB(D65)
    estimate of its device RGB so printtarg can optimise the strip layout.
    """
    n_white = sum(1 for p in patches if p.r >= 99.5 and p.g >= 99.5 and p.b >= 99.5)
    n_black = sum(1 for p in patches if p.r <= 0.5 and p.g <= 0.5 and p.b <= 0.5)
    wx, wy, wz = WHITE_XYZ

    main = _table(
        header_keywords=[
            f'APPROX_WHITE_POINT "{wx:.6f} {wy:.6f} {wz:.6f}"',
            'COLOR_REP "RGB"',
            f'WHITE_COLOR_PATCHES "{n_white}"',
            f'BLACK_COLOR_PATCHES "{n_black}"',
        ],
        id_field="SAMPLE_ID",
        rows=[(i, (p.r, p.g, p.b)) for i, p in enumerate(patches, start=1)],
    )
    extremes = _table(
        header_keywords=[f'DENSITY_EXTREME_VALUES "{len(_DENSITY_EXTREMES)}"'],
        id_field="INDEX",
        rows=list(enumerate(_DENSITY_EXTREMES)),
    )
    combos = _table(
        header_keywords=[f'DEVICE_COMBINATION_VALUES "{len(_DEVICE_COMBINATIONS)}"'],
        id_field="INDEX",
        rows=list(enumerate(_DEVICE_COMBINATIONS)),
    )

    out_path.write_text("\n".join([*main, *extremes, *combos, ""]), encoding="utf-8")
    return out_path


# --- dispatcher ------------------------------------------------------------


def _looks_like_xml(path: Path) -> bool:
    head = path.read_text(encoding="utf-8", errors="replace")[:256].lstrip()
    return head.startswith("<")


def import_to_ti1(in_path: Path, out_path: Path) -> tuple[Path, int]:
    """Convert an i1Profiler patch set (``.pxf`` or CGATS) to a ``.ti1``.

    Dispatches on content, not just extension: a CxF saved as ``.cgats`` or a
    CGATS table saved as ``.pxf`` are both read correctly. Returns
    ``(out_path, patch_count)``.
    """
    if in_path.suffix.lower() == ".pxf" or _looks_like_xml(in_path):
        patches = parse_pxf(in_path)
    else:
        patches = parse_cgats(in_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_ti1(patches, out_path)
    return out_path, len(patches)
