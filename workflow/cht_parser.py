"""Parse an ArgyllCMS ``.cht`` recognition file into patch boxes + fiducials.

ChromIQ builds its *own* ``.cht`` for charts it generated (see
:mod:`workflow.layout_engine.cht_writer`), but to profile a scanner from a
**standard IT8 target** the user owns (Wolf Faust, LaserSoft, ColorChecker, …)
we must read *any* Argyll-format ``.cht``. This module does that with no Argyll
call, so the marquee can draw a grid that lands exactly where ``scanin`` will
read.

The ``.cht`` ``BOXES`` grammar (authoritative source: Argyll 3.5.0
``scanin/scanrd.c`` ``read_reference`` ~L2070) — every box line is 11 fields::

    <class> <xfix1> <xfix2> <yfix1> <yfix2> <w> <h> <ox> <oy> <xi> <yi>

* ``F`` — fiducials. The four corners (typically TL, TR, BR, BL) are
  ``(yfix1,yfix2) (w,h) (ox,oy) (xi,yi)``. Not a patch.
* ``X`` / ``Y`` — a rectangular block of patches. The label ranges ``xfix1..xfix2``
  and ``yfix1..yfix2`` expand (``strinc``) into cells; the cell at column *i*,
  row *j* sits at ``(ox + i·xi, oy + j·yi)`` with size ``w×h``. Name is
  ``yname+xname`` for a ``Y`` block, else ``xname+yname``. ``_`` is a null token
  (single row/column). A target with two patch areas (e.g. Wolf Faust: a main
  grid + a greyscale strip) is simply two blocks.
* ``D`` — diagnostic/registration marks. Expanded like X/Y (and counted in the
  ``BOXES n`` total) but never read as colour, so excluded from ``patches``.

``y`` increases *downward* (top-left origin), matching a scan — no flip needed.
``BOX_SHRINK`` is the amount each box is inset before sampling; callers may apply
it to show the actual read zone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BOX_LINE = re.compile(
    r"^\s*([FDXY])\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$")


@dataclass
class ChtBox:
    """One patch box in ``.cht`` units (top-left origin, y down)."""
    name: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class ChtGeometry:
    patches: list[ChtBox] = field(default_factory=list)   # X/Y colour patches
    fiducials: list[tuple[float, float]] = field(default_factory=list)  # TL,TR,BR,BL
    box_shrink: float = 0.0
    n_declared: int = 0        # the "BOXES n" header count (incl. diagnostics)
    n_diag: int = 0            # number of D (diagnostic) boxes expanded


class ChtParseError(ValueError):
    """The .cht couldn't be understood as an Argyll recognition file."""


# ---------------------------------------------------------------- label ranges
def _alpha_to_num(a: str) -> int:
    n = 0
    for c in a:
        n = n * 26 + (ord(c) - 64)
    return n


def _num_to_alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def expand_range(start: str, end: str) -> list[str]:
    """Expand a ``.cht`` label range like Argyll's ``strinc`` iteration:
    ``01``→``22``, ``A``→``L``, ``GS00``→``GS23``, ``2A``→``2D`` (prefixed), and
    Excel-style multi-letter (``A``→``AD``). ``_`` is a single null cell."""
    if start == "_" or end == "_":
        return ["_"]
    s, e = start.strip().upper(), end.strip().upper()

    m1, m2 = re.match(r"^(GS)(\d+)$", s), re.match(r"^(GS)(\d+)$", e)
    if m1 and m2:
        a, b = int(m1.group(2)), int(m2.group(2))
        w = max(len(m1.group(2)), len(m2.group(2)))
        return [f"GS{i:0{w}d}" for i in range(a, b + 1)]

    if re.match(r"^\d+$", s) and re.match(r"^\d+$", e):
        a, b = int(s), int(e)
        w = max(len(s), len(e))
        return [f"{i:0{w}d}" for i in range(a, b + 1)]

    m3, m4 = re.match(r"^(\d+)([A-Z]+)$", s), re.match(r"^(\d+)([A-Z]+)$", e)
    if m3 and m4 and m3.group(1) == m4.group(1):
        subs = _alpha_seq(m3.group(2), m4.group(2))
        return [f"{m3.group(1)}{x}" for x in subs]

    if re.match(r"^[A-Z]+$", s) and re.match(r"^[A-Z]+$", e):
        return _alpha_seq(s, e)

    return [s] if s == e else [s, e]


def _alpha_seq(start: str, end: str) -> list[str]:
    n1, n2 = _alpha_to_num(start), _alpha_to_num(end)
    if n2 < n1:
        n1, n2 = n2, n1
    return [_num_to_alpha(i) for i in range(n1, n2 + 1)]


# ---------------------------------------------------------------- parse
def parse_cht(text: str, rectarg: bool = False) -> ChtGeometry:
    """Parse a ``.cht`` file's ``BOXES`` section. Raises :class:`ChtParseError`
    if no boxes are found.

    With ``rectarg=True`` the last number pair on a patch-area line is read as
    rectarg's post-fiducial offset (patches contiguous at the ``tile`` pitch);
    otherwise it's Argyll's ``xi/yi`` pitch. Use rectarg mode to read an original
    rectarg range ``.cht`` (see docs/dev — Argyll and rectarg disagree)."""
    geom = ChtGeometry()
    m = re.search(r"(?mi)^\s*BOXES\s+(\d+)", text)
    if m:
        geom.n_declared = int(m.group(1))
    ms = re.search(r"(?mi)^\s*BOX_SHRINK\s+([-+]?\d*\.?\d+)", text)
    if ms:
        geom.box_shrink = float(ms.group(1))

    for line in text.splitlines():
        bm = _BOX_LINE.match(line)
        if not bm:
            continue
        cls = bm.group(1)
        xfix1, xfix2, yfix1, yfix2 = bm.group(2, 3, 4, 5)
        w, h, ox, oy, xi, yi = (float(bm.group(i)) for i in range(6, 12))

        if cls == "F":
            geom.fiducials = [(float(yfix1), float(yfix2)), (w, h), (ox, oy), (xi, yi)]
            continue

        xnames = expand_range(xfix1, xfix2)
        ynames = expand_range(yfix1, yfix2)
        px, py = (w, h) if rectarg else (xi, yi)     # patch pitch
        for j, yn in enumerate(ynames):
            row_y = oy + j * py
            for i, xn in enumerate(xnames):
                x = ox + i * px
                if xn == "_":
                    name = yn
                elif yn == "_":
                    name = xn
                elif cls == "Y":
                    name = f"{yn}{xn}"
                else:                       # X or D
                    name = f"{xn}{yn}"
                if cls == "D":
                    geom.n_diag += 1
                    continue                # diagnostic mark — not a colour patch
                geom.patches.append(ChtBox(name, x, row_y, x + w, row_y + h))

    if not geom.patches:
        raise ChtParseError(
            "No patch boxes found in this .cht — it doesn't look like an Argyll "
            "recognition file.")
    return geom


def n_expanded(geom: ChtGeometry) -> int:
    """Total boxes expanded (patches + diagnostics) — compares with the declared
    ``BOXES n`` header, the ground-truth self-check."""
    return len(geom.patches) + geom.n_diag


def _remap_contiguous(starts_sizes: dict) -> dict:
    """One axis: map each patch start to a new start so within-area gaps close to
    touching (pitch = tile). A jump larger than half the median tile marks a new
    area, which keeps its original start (so the gap *between* areas is preserved,
    exactly as rectarg draws it)."""
    import statistics
    items = sorted(starts_sizes.items())
    med = statistics.median([s for _, s in items]) or 1.0
    out: dict = {}
    prev_new_end = prev_orig_end = None
    for pos, size in items:
        if prev_orig_end is None:
            npos = pos                                   # first patch keeps its start
        elif pos - prev_orig_end > 0.5 * med:            # gap → new area, keep start
            npos = pos
        else:                                            # within area → contiguous
            npos = prev_new_end
        out[pos] = npos
        prev_new_end = npos + size
        prev_orig_end = pos + size
    return out


def contiguous_boxes(patches: list[ChtBox]) -> list[tuple[float, float, float, float]]:
    """Re-place patches with rectarg's contiguous model (pitch = tile, gaps closed
    within each area). Separable per axis; sizes unchanged. Returns new
    ``(x1, y1, x2, y2)`` per patch in the same order — matches how rectarg renders
    a *gapped* target (SpyderChecker, QPcard, CMP …)."""
    xsize: dict = {}
    ysize: dict = {}
    for b in patches:
        xsize.setdefault(round(b.x1, 3), b.x2 - b.x1)
        ysize.setdefault(round(b.y1, 3), b.y2 - b.y1)
    xmap, ymap = _remap_contiguous(xsize), _remap_contiguous(ysize)
    out = []
    for b in patches:
        nx, ny = xmap[round(b.x1, 3)], ymap[round(b.y1, 3)]
        out.append((nx, ny, nx + (b.x2 - b.x1), ny + (b.y2 - b.y1)))
    return out


def to_rectarg_geometry(text: str) -> str:
    """Rewrite a bundled ``.cht`` to rectarg's geometry: patches contiguous at the
    ``tile`` pitch, the ``F`` line set to the patch-area bounding box (so scanin's
    ``-F`` maps the same frame the marquee places its corners on), and BOX_SHRINK
    widened to leave a read margin now that patches touch. A no-op in effect for
    files already contiguous (tile==pitch)."""
    import re
    new = cht_contiguous(text)
    g = parse_cht(new)
    minx = min(b.x1 for b in g.patches); maxx = max(b.x2 for b in g.patches)
    miny = min(b.y1 for b in g.patches); maxy = max(b.y2 for b in g.patches)
    fline = ("  F _ _ %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f"
             % (minx, miny, maxx, miny, maxx, maxy, minx, maxy))
    new = re.sub(r'^\s*F .*$', fline, new, count=1, flags=re.M)
    tile = min(g.patches[0].x2 - g.patches[0].x1, g.patches[0].y2 - g.patches[0].y1)
    shrink = round(tile * 0.12, 2)
    if re.search(r'^\s*BOX_SHRINK', new, flags=re.M):
        new = re.sub(r'^\s*BOX_SHRINK.*$', "BOX_SHRINK %.2f" % shrink, new,
                     count=1, flags=re.M)
    else:
        new = new.rstrip() + "\n\nBOX_SHRINK %.2f\n" % shrink
    return new


def cht_contiguous(text: str) -> str:
    """Rewrite a per-patch ``.cht`` so patch positions use rectarg's contiguous
    model — hand this to ``scanin`` when the "match rectarg preview" toggle is on,
    so the diagnostic lines up with a rectarg-rendered image of a gapped target."""
    geom = parse_cht(text)
    remap = {(round(b.x1, 3), round(b.y1, 3)): nb
             for b, nb in zip(geom.patches, contiguous_boxes(geom.patches))}
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 9 and p[0] in ("X", "Y"):
            try:
                key = (round(float(p[7]), 3), round(float(p[8]), 3))
            except ValueError:
                out.append(line)
                continue
            nb = remap.get(key)
            if nb is not None:
                p[7], p[8] = f"{nb[0]:.3f}", f"{nb[1]:.3f}"
                out.append(" ".join(p))
                continue
        out.append(line)
    return "\n".join(out) + "\n"
