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

    # Imageable width (left clip border eats into it). printtarg: x1=bord+lbord, x2=pw-bord
    iw = pw - 2.0 * g.border - g.lbord

    # Available pass length down the sheet.
    mints = g.border + g.txhisl + g.lcar
    if mints < g.lspa:
        mints = g.lspa
    minbs = g.border
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


def patches_per_sheet(geom: Geom, paper_w_mm: float, paper_h_mm: float,
                      *, scanc: int = 0) -> int:
    """Max **test** patches that fit on one sheet for *geom* (the calculator).

    Independent of any requested count — uses a large request and reports the
    page capacity (``patches_per_page``).
    """
    return compute(geom, paper_w_mm, paper_h_mm, 100_000, scanc=scanc).patches_per_page
