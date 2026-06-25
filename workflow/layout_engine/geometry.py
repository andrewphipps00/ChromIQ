"""Chart packing math — a faithful port of printtarg.c ``setup_pat``.

Given a resolved :class:`~workflow.layout_engine.instruments.Geom` and a page
size, compute how the patches lay out: steps per pass, passes (rows), strips
and pages, plus the number of padding patches printtarg appends to complete the
final pass.  Verified to reproduce printtarg's reported numbers exactly across
the instrument/paper matrix (see ``tests/test_layout_geometry.py``).

The variable names mirror printtarg.c so the two can be diffed line-for-line.
"""
from __future__ import annotations

from dataclasses import dataclass

from .instruments import Geom


class LayoutError(ValueError):
    """Raised when the page can't hold even a single pass of patches."""


@dataclass(frozen=True)
class Layout:
    steps_in_pass: int      # tpprow — test patches per pass (CGATS STEPS_IN_PASS)
    passes: int             # rows in the (last) strip (CGATS PASSES_IN_STRIPS2)
    strips_per_page: int    # sppage
    rows_in_partial_strip: int  # rppstrip
    patches_per_page: int   # pppage (test patches)
    pages: int              # npages
    padding: int            # extra media patches appended to fill the last pass
    total_patches: int      # input npat + padding
    pprow: int              # raw patches per pass incl. nextrap

    @property
    def fits_one_page(self) -> bool:
        return self.pages <= 1


def compute(geom: Geom, paper_w_mm: float, paper_h_mm: float, npat: int,
            *, scanc: int = 0) -> Layout:
    """Lay *npat* patches out for *geom* on a ``paper_w_mm`` × ``paper_h_mm`` sheet.

    *scanc* bit 2 set ⇒ scan-compatibility first-row extra width (printtarg -s).
    Returns a :class:`Layout`.  Raises :class:`LayoutError` if nothing fits.
    """
    if npat < 1:
        raise LayoutError("need at least one patch")

    pw, ph = paper_w_mm, paper_h_mm
    g = geom

    sxwi = g.pwid / 2.0 if (scanc & 2) else 0.0

    # Imageable width: left margin + clip border on the left, right margin on the
    # right (independent margins; default to the uniform border).
    iw = pw - g.margin_l - g.lbord - g.margin_r

    # Available pass length down the sheet (top/bottom margins, floored by the
    # instrument's own leader/trailer requirements).
    mints = g.margin_t + g.txhisl + g.lcar
    if mints < g.lspa:
        mints = g.lspa
    minbs = g.margin_b
    if minbs < g.tspa:
        minbs = g.tspa
    arowl = ph - mints - minbs - 2.0 * g.hxeh
    if arowl > g.mxrowl:
        arowl = g.mxrowl

    # Patches per pass (every patch may be surrounded by a spacer → pprow+1 gaps).
    if (g.plen + g.pspa) <= 0:
        raise LayoutError("degenerate patch length")
    pprow = int((arowl - g.pspa) / (g.plen + g.pspa))
    if pprow > g.mxpprow:
        pprow = g.mxpprow
    if pprow < (1 + g.nextrap):
        raise LayoutError(
            f"paper too short: a single pass of patches does not fit "
            f"({arowl:.1f} mm available)"
        )
    tpprow = pprow - g.nextrap

    tidnpat = npat  # no target-ID rows for the RGB path

    # Strip width and strips/rows across the sheet.
    if g.dorspace:
        swid = g.rpstrip * g.rrsp + g.pwid / 2.0
    else:
        swid = (g.rpstrip - 1) * g.rrsp + g.pwid + g.clwi

    avail_w = iw - g.rlwi - sxwi - 2.0 * g.hxew - (g.pglth if g.dopglabel else 0.0)
    sppage = int(avail_w / swid) + 1
    if g.dorspace:
        rppstrip = int((avail_w - swid * (sppage - 1) - g.pwid / 2.0) / g.rrsp)
    else:
        rppstrip = int((avail_w - swid * (sppage - 1) - g.pwid + g.rrsp) / g.rrsp)
    if rppstrip < 0:
        rppstrip = 0
    if rppstrip == 0:                 # last partial strip becomes a full strip
        sppage -= 1
        rppstrip = g.rpstrip
    if sppage <= 0:
        raise LayoutError("not enough width for even one row")

    pppage = tpprow * ((sppage - 1) * g.rpstrip + rppstrip)
    if pppage <= 0:
        raise LayoutError("page holds no patches")
    npages = (tidnpat + pppage - 1) // pppage
    ppstrip = tpprow * g.rpstrip

    rem = tidnpat - (npages - 1) * pppage
    lsppage = (rem + ppstrip - 1) // ppstrip
    rem -= (lsppage - 1) * ppstrip
    lrpstrip = (rem + tpprow - 1) // tpprow
    rem -= (lrpstrip - 1) * tpprow
    lpprow = rem + g.nextrap

    padding = max(0, pprow - lpprow) if g.padlrow else 0

    return Layout(
        steps_in_pass=tpprow,
        passes=lrpstrip,
        strips_per_page=sppage,
        rows_in_partial_strip=rppstrip,
        patches_per_page=pppage,
        pages=npages,
        padding=padding,
        total_patches=npat + padding,
        pprow=pprow,
    )


@dataclass(frozen=True)
class Placement:
    """Millimetre placement parameters for rendering, mirroring printtarg.

    A patch at (pass *p*, position *j* down the pass) occupies the rectangle
    ``(x_of(p), y_of(j), pwid, plen)``; the spacer below it is ``pspa`` tall.
    """
    x0: float            # left of the first pass (mm) = border + lbord
    y0_first: float      # top of the first patch in a pass (mm)
    plen: float
    pwid: float
    pspa: float
    rrsp: float
    steps_in_pass: int
    leader_top: float    # top of the leader area (for the strip label), mm

    def x_of(self, pass_idx: int) -> float:
        return self.x0 + pass_idx * self.rrsp

    def y_of(self, pos: int) -> float:
        return self.y0_first + pos * (self.plen + self.pspa)


def placement(geom: Geom, paper_w_mm: float, paper_h_mm: float, layout: Layout) -> Placement:
    """Resolve mm placement for *layout* on the sheet (single strip per page).

    Reproduces printtarg's vertical-space distribution (``amints``).  Exact for
    the strip-reader instruments that use one strip per page (i1/p3/CM/SS);
    multi-strip instruments (DTP41/51) will gain strip gutters in a later pass.
    """
    g = geom
    ph = paper_h_mm
    mints = max(g.border + g.txhisl + g.lcar, g.lspa)
    minbs = max(g.border, g.tspa)
    # Distribute slack like printtarg: amints = mints + 0.5*(slack - minbs)
    slack = ph - mints - g.pspa - layout.pprow * (g.plen + g.pspa)
    amints = mints + 0.5 * (slack - minbs)
    return Placement(
        x0=g.margin_l + g.lbord + g.offset_x,
        y0_first=amints + g.pspa + g.strip_indicator_gap + g.offset_y,
        plen=g.plen, pwid=g.pwid, pspa=g.pspa, rrsp=g.rrsp,
        steps_in_pass=layout.steps_in_pass,
        leader_top=g.margin_t + g.txhisl + g.offset_y,
    )


def strip_rects_px(geom: Geom, paper_w_mm: float, paper_h_mm: float,
                   layout: Layout, dpi: int) -> list[dict]:
    """Exact per-strip (pass) bounding rectangles in pixels, for the measure tab.

    Because the engine *knows* the geometry, the measure-tab highlighter can use
    these directly instead of detecting stripes from the image — a solid,
    guess-free path.  One entry per pass across all pages:
    ``{"page", "pass", "x", "y", "w", "h"}`` (pixel ints, top-left origin).
    """
    mm2px = dpi / 25.4
    place = placement(geom, paper_w_mm, paper_h_mm, layout)
    steps = layout.steps_in_pass
    pppage = layout.patches_per_page
    total = layout.total_patches

    def px(mm: float) -> int:
        return round(mm * mm2px)

    rects: list[dict] = []
    for page in range(layout.pages):
        first = page * pppage
        last = min(total, first + pppage)
        n_passes = (last - first + steps - 1) // steps
        for p in range(n_passes):
            col_n = min(last, first + (p + 1) * steps) - (first + p * steps)
            x = px(place.x_of(p))
            y = px(place.y_of(0))
            h = px(place.y_of(col_n - 1) + place.plen) - y
            rects.append({
                "page": page, "pass": p,
                "x": x, "y": y, "w": px(place.pwid), "h": h,
            })
    return rects


def patch_rects_px(geom: Geom, paper_w_mm: float, paper_h_mm: float,
                   layout: Layout, dpi: int,
                   strip_pattern: str = "A-Z, A-Z",
                   patch_pattern: str = "0-9,@-9,@-9;1-999") -> list[dict]:
    """Exact pixel rectangle of **every** patch slot, with its ``SAMPLE_LOC``.

    Because the engine generates the layout, the position of each patch is known
    exactly — no image detection.  One entry per occupied slot:
    ``{"page","slot","loc","x","y","w","h"}`` in STRIP_THEN_PATCH order.
    """
    from . import permutation  # local import to avoid a cycle

    mm2px = dpi / 25.4
    place = placement(geom, paper_w_mm, paper_h_mm, layout)
    steps = layout.steps_in_pass
    pppage = layout.patches_per_page
    total = layout.total_patches

    def px(mm: float) -> int:
        return round(mm * mm2px)

    out: list[dict] = []
    for page in range(layout.pages):
        first = page * pppage
        last = min(total, first + pppage)
        for wp in range(last - first):
            gslot = first + wp
            p, j = wp // steps, wp % steps
            loc = permutation.location_label(gslot, steps, strip_pattern, patch_pattern)
            out.append({
                "page": page, "slot": gslot, "loc": loc,
                "x": px(place.x_of(p)), "y": px(place.y_of(j)),
                "w": px(place.pwid), "h": px(place.plen),
            })
    return out


def patches_per_sheet(geom: Geom, paper_w_mm: float, paper_h_mm: float,
                      *, scanc: int = 0) -> int:
    """Max **test** patches that fit on one sheet for *geom* (the calculator).

    Independent of any requested count — uses a large request and reports the
    page capacity (``patches_per_page``).
    """
    return compute(geom, paper_w_mm, paper_h_mm, 100_000, scanc=scanc).patches_per_page
