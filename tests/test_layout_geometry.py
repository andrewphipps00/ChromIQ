"""Golden tests for the layout-engine packing math.

Each expected (steps, passes, total) was captured from a live ``printtarg``
run (Argyll 3.5.0) on a 60-patch RGB ``.ti1`` — see issue #93's feasibility
matrix. If these drift, the engine no longer matches printtarg's geometry.
"""
import pytest

from workflow.layout_engine import geometry, instruments

A4 = (210.0, 297.0)
A4R = (297.0, 210.0)

# key, paper, hflag, spacer_on, pscale, npat, steps, passes, total
CASES = [
    ("i1", A4,  False, True,  1.0,   60, 21, 3, 63),
    ("p3", A4,  False, True,  1.0,   60,  9, 7, 63),
    ("CM", A4,  False, True,  1.0,   60, 15, 4, 60),
    ("CM", A4,  True,  True,  1.0,   60, 15, 4, 60),   # -h (rig): same logical grid
    ("41", A4,  False, True,  1.0,   60, 25, 3, 75),
    ("51", A4,  False, True,  1.0,   60, 19, 4, 76),
    ("SS", A4,  False, True,  1.0,   60, 39, 2, 60),
    ("SS", A4,  True,  True,  1.0,   60, 45, 2, 60),   # hex
    ("i1", A4R, False, True,  1.0,   60, 16, 4, 64),
    ("i1", A4,  False, False, 1.0,   60, 24, 3, 72),   # -n no spacers
    ("i1", A4,  False, True,  0.857, 60, 25, 3, 75),   # -a 0.857
    ("i1", A4,  False, True,  1.5,   60, 14, 5, 70),   # -a 1.5
]


@pytest.mark.parametrize("key,paper,hflag,spacer,pscale,npat,steps,passes,total", CASES)
def test_matches_printtarg(key, paper, hflag, spacer, pscale, npat, steps, passes, total):
    geom = instruments.build(key, hflag=hflag, spacer_on=spacer, pscale=pscale)
    lay = geometry.compute(geom, paper[0], paper[1], npat)
    assert lay.steps_in_pass == steps
    assert lay.passes == passes
    assert lay.total_patches == total
    assert lay.padding == total - npat
    assert lay.pages == 1


def test_patches_per_sheet_i1_a4():
    geom = instruments.build("i1")
    # 21 steps × 21 rows = 441 (matches printtarg "patches per page = 441").
    assert geometry.patches_per_sheet(geom, *A4) == 441


def test_tiny_paper_raises():
    geom = instruments.build("i1")
    with pytest.raises(geometry.LayoutError):
        geometry.compute(geom, 40.0, 40.0, 60)


def test_delegated_instrument_rejected():
    with pytest.raises(ValueError):
        instruments.build("isis")


def test_independent_margins_reduce_capacity():
    base = instruments.build("i1")
    wide = instruments.build("i1", margins=(20.0, 20.0, 20.0, 20.0))
    assert wide.margin_t == 20.0 and wide.margin_l == 20.0
    assert (geometry.patches_per_sheet(wide, *A4)
            < geometry.patches_per_sheet(base, *A4))
    # default margins (None) leave geometry identical to the uniform border
    assert (geometry.patches_per_sheet(instruments.build("i1"), *A4)
            == geometry.patches_per_sheet(instruments.build("i1", margins=(6.0,)*4), *A4))


def test_patch_size_override():
    g = instruments.build("i1", patch_w=12.0, patch_h=12.0)
    assert g.plen == 12.0 and g.pwid == 12.0
    # bigger patches → fewer fit per sheet
    assert (geometry.patches_per_sheet(g, *A4)
            < geometry.patches_per_sheet(instruments.build("i1"), *A4))


def test_colormunki_density_levels_increase_capacity():
    # ColorMunki: normal < high (rig, printtarg -h) < extra-high (ChromIQ ext).
    cap = []
    for d in (1, 2, 3):
        geom = instruments.build("CM", density=d)
        cap.append(geometry.patches_per_sheet(geom, *A4))
    assert cap[0] < cap[1] < cap[2]
    # level 2 reproduces printtarg's rig row spacing (13.7 mm) exactly
    assert instruments.build("CM", density=2).rrsp == 13.7
    # hflag back-compat still maps to the rig (density 2)
    assert instruments.build("CM", hflag=True).rrsp == 13.7


def test_clip_border_width_drives_lbord():
    # Default reserved clip zone is 26 mm; lbord = zone − margin.
    assert instruments.build("i1", border=6.0).lbord == pytest.approx(20.0)
    # Widening the zone widens the extra clip strip.
    assert instruments.build("i1", border=6.0, clip_border_width=40.0).lbord \
        == pytest.approx(34.0)
    # Margin already past the zone ⇒ no extra strip (never negative).
    assert instruments.build("i1", border=30.0, clip_border_width=26.0).lbord == 0.0
    # No clip border (-L) ⇒ no reserved zone regardless of width.
    assert instruments.build("i1", nolpcbord=True, clip_border_width=40.0).lbord == 0.0
    # p3 honours it too; non-clip instruments are unaffected (lbord stays 0).
    assert instruments.build("p3", clip_border_width=40.0).lbord == pytest.approx(34.0)
    assert instruments.build("CM", clip_border_width=40.0).lbord == 0.0
