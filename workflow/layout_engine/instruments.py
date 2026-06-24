"""Per-instrument chart geometry, reverse-engineered from ArgyllCMS ``printtarg.c``.

Every constant here was read from ``target/printtarg.c`` (v3.5.0) and then
**verified** against a live ``printtarg`` option matrix: for a 60-patch RGB
``.ti1`` on A4 these reproduce printtarg's reported ``STEPS_IN_PASS`` /
``PASSES_IN_STRIPS2`` / padded patch count exactly (i1 21×3→63, p3 9×7,
ColorMunki 15×4→60, DTP41 25×3→75, DTP51 19×4→76, SpectroScan 39×2,
SpectroScan hex 45×2, A4-landscape 16×4).

A :class:`Geom` carries every value :func:`workflow.layout_engine.geometry`
needs.  Values that depend on patch scale (``-a``), spacer scale (``-A``),
high-density / hex (``-h``), spacers on/off (``-n``), the page margin (``-m``)
or the left clip border (``-L``) are resolved in :func:`build`.

All lengths are millimetres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MAXPPROW = 500          # printtarg.c: absolute max patches per pass/row
MAXROWLEN = 5000.0      # printtarg.c MAXROWLEN — large enough to never bind for sheet sizes

# Instrument flag (printtarg -i value) -> CGATS TARGET_INSTRUMENT string.
TARGET_INSTRUMENT_NAME: dict[str, str] = {
    "i1": "GretagMacbeth i1 Pro",
    "p3": "GretagMacbeth i1 Pro",      # printtarg stamps the 3+ the same way
    "CM": "X-Rite ColorMunki",
    "41": "X-Rite DTP41",
    "51": "X-Rite DTP51",
    "SS": "GretagMacbeth SpectroScan",
}

# Instruments ChromIQ never lays out itself (delegated to i1Profiler).
DELEGATED = {"isis"}


def _inch(mm: float) -> float:
    return mm * 25.4


@dataclass(frozen=True)
class Geom:
    """Resolved geometry for one (instrument, scale, spacer, margin) combo."""
    key: str
    plen: float          # patch length along a pass (mm)
    pspa: float          # inter-patch spacer (mm); 0 if spacers off
    tspa: float          # trailer clear space after last patch (mm)
    pwid: float          # patch width (mm)
    rrsp: float          # row-centre to row-centre spacing (mm)
    lspa: float          # leader space before first patch (mm)
    lcar: float          # leading clear area (mm)
    txhisl: float        # strip/column label text height (mm)
    pglth: float         # page-label text height (mm)
    border: float        # page margin (-m), mm
    lbord: float         # extra left clip border (mm); 0 if suppressed (-L) / N/A
    hxeh: float          # hex/stagger extra height (mm)
    hxew: float          # hex extra width (mm)
    clwi: float          # cut-line width (mm)
    rlwi: float          # row-label width (mm)
    mxpprow: int         # max patches per pass
    mxrowl: float        # max pass length (mm)
    rpstrip: int         # rows per whole strip
    nextrap: int         # extra max/min/SID patches per pass (not test patches)
    dorspace: bool       # gutter between rows by rrsp (vs touching)
    dopglabel: bool      # reserve a per-page label column
    padlrow: bool        # pad the final pass up to full length
    target_name: str     # CGATS TARGET_INSTRUMENT
    has_clip_border: bool # whether this instrument supports a left clip border

    # Instrument-specific extra .ti2 keywords (e.g. DTP41 lengths, SS hex flag).
    extra_keywords: tuple[tuple[str, str], ...] = ()


def supported() -> list[str]:
    return ["i1", "p3", "CM", "41", "51", "SS"]


def build(
    key: str,
    *,
    pscale: float = 1.0,
    sscale: float = 1.0,
    hflag: bool = False,
    spacer_on: bool = True,
    border: float = 6.0,
    nolpcbord: bool = False,
    nolimit: bool = False,
) -> Geom:
    """Resolve :class:`Geom` for *key* with the given options.

    *pscale* = printtarg ``-a`` (patch+spacer scale), *sscale* = ``-A`` (spacer
    scale), *hflag* = ``-h`` (hex/high-density), *spacer_on* False = ``-n``,
    *border* = ``-m`` margin, *nolpcbord* True = ``-L``, *nolimit* True = ``-P``.
    """
    if key in DELEGATED:
        raise ValueError(f"instrument {key!r} is delegated to i1Profiler, not laid out here")
    if key not in TARGET_INSTRUMENT_NAME:
        raise ValueError(f"unknown instrument {key!r}")

    name = TARGET_INSTRUMENT_NAME[key]

    def spacer(base: float) -> float:
        return pscale * sscale * base if spacer_on else 0.0

    # ---- i1Pro family (5 mm and 8 mm apertures) -------------------------
    if key in ("i1", "p3"):
        lbord = (26.0 - border) if (not nolpcbord and border < 26.0) else 0.0
        if key == "i1":                       # 5 mm aperture
            lcar, plen_b, pspa_b, tspa = 10.0, 10.0, 1.0, 10.0
            pwid_b = rrsp_b = 8.0
        else:                                 # p3 = i1Pro 3+ / 8 mm aperture
            lcar, plen_b, pspa_b, tspa = 20.0, 20.0, 2.0, 20.0
            pwid_b = rrsp_b = 16.0
        txhisl = 7.0
        mxrowl = MAXROWLEN if nolimit else (260.0 - lcar - tspa)
        return Geom(
            key=key, plen=pscale * plen_b, pspa=spacer(pspa_b), tspa=tspa,
            pwid=pscale * pwid_b, rrsp=pscale * rrsp_b,
            lspa=border + txhisl + lcar, lcar=lcar, txhisl=txhisl, pglth=5.0,
            border=border, lbord=lbord, hxeh=0.0, hxew=0.0, clwi=0.0, rlwi=0.0,
            mxpprow=MAXPPROW, mxrowl=mxrowl, rpstrip=999, nextrap=0,
            dorspace=False, dopglabel=True, padlrow=True, target_name=name,
            has_clip_border=True,
        )

    # ---- X-Rite ColorMunki ---------------------------------------------
    if key == "CM":
        plen = pscale * 14.0
        if hflag:                             # high density (rig) — staggered
            pwid = rrsp = pscale * 13.7
            hxeh = 0.25 * plen
        else:
            pwid = rrsp = pscale * 28.0
            hxeh = 0.0
        txhisl, lcar = 7.0, 20.0
        return Geom(
            key=key, plen=plen, pspa=spacer(1.0), tspa=25.0, pwid=pwid, rrsp=rrsp,
            lspa=border + 7.0 + 20.0, lcar=lcar, txhisl=txhisl, pglth=5.0,
            border=border, lbord=0.0, hxeh=hxeh, hxew=0.0, clwi=0.0, rlwi=0.0,
            mxpprow=MAXPPROW, mxrowl=MAXROWLEN, rpstrip=999, nextrap=0,
            dorspace=False, dopglabel=True, padlrow=True, target_name=name,
            has_clip_border=False,
        )

    # ---- GretagMacbeth SpectroScan (flatbed) ---------------------------
    if key == "SS":
        if hflag:                             # hexagon patches
            plen = pscale * math.sqrt(0.75) * 7.0
            hxeh = (1.0 / 6.0) * plen
            hxew = pscale * 0.25 * 7.0
        else:
            plen = pscale * 7.0
            hxeh = hxew = 0.0
        extra = (("HEXAGON_PATCHES", "True"),) if hflag else ()
        return Geom(
            key=key, plen=plen, pspa=0.0, tspa=0.0, pwid=pscale * 7.0, rrsp=pscale * 7.0,
            lspa=border + 7.0, lcar=0.0, txhisl=5.0, pglth=5.0,
            border=border, lbord=0.0, hxeh=hxeh, hxew=hxew, clwi=0.0, rlwi=7.5,
            mxpprow=MAXPPROW, mxrowl=MAXROWLEN, rpstrip=999, nextrap=0,
            dorspace=False, dopglabel=True, padlrow=False, target_name=name,
            has_clip_border=False, extra_keywords=extra,
        )

    # ---- X-Rite DTP41 ---------------------------------------------------
    if key == "41":
        plen = pscale * _inch(0.29)
        pspa = spacer(_inch(0.08))
        tspa = 2.0 * (plen + pspa)
        mxrowl = MAXROWLEN if nolimit else _inch(55.0)
        extra = (
            ("PATCH_LENGTH", f"{plen:.6f}"),
            ("GAP_LENGTH", f"{pspa:.6f}"),
            ("TRAILER_LENGTH", f"{tspa:.6f}"),
        )
        return Geom(
            key=key, plen=plen, pspa=pspa, tspa=tspa,
            pwid=_inch(0.5), rrsp=_inch(0.5),
            lspa=_inch(1.5), lcar=_inch(0.5), txhisl=5.0, pglth=5.0,
            border=border, lbord=0.0, hxeh=0.0, hxew=0.0, clwi=0.3, rlwi=0.0,
            mxpprow=100, mxrowl=mxrowl, rpstrip=8, nextrap=0,
            dorspace=False, dopglabel=True, padlrow=True, target_name=name,
            has_clip_border=False, extra_keywords=extra,
        )

    # ---- X-Rite DTP51 ---------------------------------------------------
    if key == "51":
        plen = pscale * _inch(0.4)
        pspa = spacer(_inch(0.07))
        mxrowl = MAXROWLEN if nolimit else _inch(40.0)
        return Geom(
            key=key, plen=plen, pspa=pspa, tspa=0.0,
            pwid=_inch(0.4), rrsp=_inch(0.5),
            lspa=_inch(1.2), lcar=_inch(0.25), txhisl=5.0, pglth=5.0,
            border=border, lbord=0.0, hxeh=0.0, hxew=0.0, clwi=0.3, rlwi=0.0,
            mxpprow=72, mxrowl=mxrowl, rpstrip=6, nextrap=2,   # max+min header/trailer
            dorspace=True, dopglabel=False, padlrow=True, target_name=name,
            has_clip_border=False,
        )

    raise ValueError(f"unhandled instrument {key!r}")
