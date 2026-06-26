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


def test_clip_area_only_when_clip_border():
    # i1 with clip border → a 20 mm-wide band (clip_width 26 − margin 6).
    area = geometry.clip_area_mm(instruments.build("i1", border=6.0), 297.0)
    assert area is not None
    x, y, w, h = area
    assert w == pytest.approx(20.0)
    assert h == pytest.approx(297.0 - 12.0)
    # no clip border → no area.
    assert geometry.clip_area_mm(instruments.build("i1", nolpcbord=True), 297.0) is None
    assert geometry.clip_area_mm(instruments.build("CM"), 297.0) is None


def test_spacer_rects_match_render_flat_index():
    """spacer_rects_px flat indices + positions match what the renderer paints,
    so an editor click maps to the spacer the engine recolours (#93)."""
    import numpy as np
    from workflow.layout_engine import raster
    from workflow.layout_engine.ti1_reader import ColorTarget
    target = ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                         patches=[((50.0, 50.0, 50.0), (40.0, 45.0, 50.0))
                                  for _ in range(60)])
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    rects = geometry.spacer_rects_px(geom, 210.0, 297.0, lay, 150)
    assert rects and rects[0]["flat"] == 0
    res = raster.render_pages(target, lay, geom, seed=1, randomize=False,
                              paper_w_mm=210.0, paper_h_mm=297.0, dpi=150,
                              spacer_overrides={rects[0]["flat"]: (255, 0, 255)})
    a = np.asarray(res.images[0])
    r0 = rects[0]
    assert tuple(a[r0["y"] + r0["h"] // 2, r0["x"] + r0["w"] // 2]) == (255, 0, 255)


def test_strip_indicator_gap_reduces_capacity_and_stays_in_bounds():
    """A larger strip-indicator gap must reduce the patch count to fit, never
    push patches off the usable area; a smaller gap fits more (#93)."""
    pw, ph = 210.0, 297.0
    prev = None
    for gap in (0.0, 40.0, 60.0, 90.0):
        g = instruments.build("i1", strip_indicator_gap=gap)
        lay = geometry.compute(g, pw, ph, 1000)
        pl = geometry.placement(g, pw, ph, lay)
        block_bottom = pl.y_of(lay.steps_in_pass - 1) + pl.plen
        usable_bottom = ph - max(g.margin_b, g.tspa)
        # the last patch in a pass never crosses the bottom usable edge
        assert block_bottom <= usable_bottom + 0.5, f"overflow at gap={gap}"
        # capacity is monotonically non-increasing as the gap grows
        if prev is not None:
            assert lay.patches_per_page <= prev, f"gap={gap} didn't reduce count"
        prev = lay.patches_per_page
    # a big gap genuinely fits fewer than no gap
    g0 = instruments.build("i1", strip_indicator_gap=0.0)
    g9 = instruments.build("i1", strip_indicator_gap=90.0)
    assert (geometry.compute(g9, pw, ph, 1000).patches_per_page
            < geometry.compute(g0, pw, ph, 1000).patches_per_page)


def test_geom_from_build_kwargs_honours_clip_width():
    """The shared geom builder must apply clip_border_width so capacity
    estimates match the render — a wider clip fits fewer patches (#93)."""
    from workflow.layout_engine.presets import LayoutRecipe
    pw, ph = 210.0, 297.0
    def cap(width):
        r = LayoutRecipe(instrument="i1", paper="A4", clip_border=True,
                         clip_border_width_mm=width)
        g = instruments.geom_from_build_kwargs(r.build_kwargs())
        return geometry.patches_per_sheet(g, pw, ph)
    wide, narrow = cap(60.0), cap(26.0)
    assert wide < narrow, f"wider clip ({wide}) should fit fewer than default ({narrow})"
    # and it matches building the geom the same way the renderer does
    r = LayoutRecipe(instrument="i1", paper="A4", clip_border=True,
                     clip_border_width_mm=60.0)
    g = instruments.geom_from_build_kwargs(r.build_kwargs())
    assert g.lbord == pytest.approx(60.0 - r.border)


def test_furniture_reserves_affect_capacity():
    """Rendered furniture (big/rotated indicators, underline, sheet text, stamp)
    must reserve space so capacity reflects it — while a default chart keeps its
    printtarg-parity count (#93)."""
    from workflow.layout_engine.presets import LayoutRecipe
    paper = "210x150"                     # short page → height-bound, not mxrowl
    w, h = geometry_papers(paper)

    def cap(**over):
        r = LayoutRecipe(instrument="i1", paper=paper, **over)
        g = instruments.geom_from_build_kwargs(r.build_kwargs())
        return geometry.patches_per_sheet(g, w, h)

    base = cap()
    # A large indicator reserves a taller label band → fewer patches fit
    assert cap(indicator_size_mm=20.0) < base
    # Underline / sheet text / stamp reserve space too (≤: they may sit in the
    # page's slack on a given size, but never exceed the no-furniture count)
    assert cap(underline_mode="black", underline_thickness_mm=3.0,
               underline_gap_mm=5.0) <= base
    assert cap(stamp_command=True, chart_text="{project}") <= base
    # an oversized stack of furniture clearly drops the count
    assert cap(indicator_size_mm=18.0, underline_mode="black",
               underline_thickness_mm=3.0, underline_gap_mm=6.0,
               stamp_command=True, chart_text="x") < base
    # Turning strip labels OFF, or choosing an explicit SMALL font, reclaims the
    # label band for more patches; auto size stays at the printtarg floor (#93).
    assert cap(show_strip_indicators=False) > base
    assert cap(indicator_size_mm=2.5) > base
    # A bare build() Geom (no furniture info) is unchanged from the default
    bare = geometry.patches_per_sheet(instruments.build("i1"), *geometry_papers("A4"))
    assert bare == geometry.patches_per_sheet(
        instruments.geom_from_build_kwargs(
            LayoutRecipe(instrument="i1", paper="A4").build_kwargs()),
        *geometry_papers("A4"))


def geometry_papers(code):
    from workflow.layout_engine import papers
    return papers.dimensions_mm(code)
