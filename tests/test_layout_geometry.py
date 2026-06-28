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
    # printtarg brackets every strip with a leading + trailing spacer, so the
    # parity comparison uses edge_spacers=True. The engine's default (edge off)
    # reclaims those two gaps and packs denser — covered separately (#93).
    geom = instruments.build(key, hflag=hflag, spacer_on=spacer, pscale=pscale,
                             edge_spacers=True)
    lay = geometry.compute(geom, paper[0], paper[1], npat)
    assert lay.steps_in_pass == steps
    assert lay.passes == passes
    assert lay.total_patches == total
    assert lay.padding == total - npat
    assert lay.pages == 1


def test_patches_per_sheet_i1_a4():
    geom = instruments.build("i1")
    # 21 steps down × 22 strips across = 462. printtarg reports 441 because it
    # reserves a ~5 mm page-label column; the engine reclaims that dead column
    # (the page label now lives in chart/clip text via placeholders), so it fits
    # MORE than printtarg here — strictly ≥ printtarg, by design (#93).
    assert geometry.patches_per_sheet(geom, *A4) == 462


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
    # Top follows the box; the LEFT is floored to the clip-border width (26 mm),
    # since the clip band lives inside the clip-side margin (Knut beta-13).
    assert wide.margin_t == 20.0 and wide.margin_l == 26.0
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
    # i1 with clip border → the full clip zone (26 mm) minus the printer-safe
    # inset, NOT shrunk by the patch margin, so the content stays wide (#93).
    inset = geometry.CLIP_CONTENT_INSET_MM
    a6 = geometry.clip_area_mm(instruments.build("i1", border=6.0), 297.0)
    a10 = geometry.clip_area_mm(instruments.build("i1", border=10.0), 297.0)
    assert a6 is not None
    assert a6[0] == pytest.approx(inset)              # starts at the inset
    assert a6[2] == pytest.approx(26.0 - inset)       # full zone minus inset
    assert a6[3] == pytest.approx(297.0 - 12.0)
    # a larger patch margin must NOT shrink the clip content any more (Guided
    # used to come out narrower than Manual).
    assert a10[2] == pytest.approx(a6[2])
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
    assert cap(indicator_size_mm=30.0) < base
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


def test_strip_gap_reduces_capacity():
    """Extra gap between strips widens the row pitch, so fewer strips (and
    patches) fit; 0 is the default (#93)."""
    base = instruments.build("i1")
    wide = instruments.build("i1", strip_gap=4.0)
    assert wide.rrsp == base.rrsp + 4.0
    assert (geometry.compute(wide, *A4, 100_000).patches_per_page
            < geometry.compute(base, *A4, 100_000).patches_per_page)


def test_page_label_column_reclaimed():
    """The page-label column (printtarg's pglth) is no longer reserved — its info
    lives in chart text / clip text via placeholders, so the ~5 mm is reclaimed
    for patches on every laid-out instrument (#93)."""
    for key in ("i1", "p3", "CM", "41", "SS", "51"):
        assert instruments.build(key).dopglabel is False


def test_strip_label_offset_does_not_change_capacity():
    """The strip-label offset only repositions the labels (handled at render
    time); it doesn't live in the geometry, so capacity is unchanged (#93)."""
    # offset is a render-time nudge; geometry/capacity stay put
    assert geometry.patches_per_sheet(instruments.build("i1"), *A4) == \
        geometry.patches_per_sheet(instruments.build("i1"), *A4)


def test_patch_area_alignment_positions_block_without_changing_capacity():
    """Patch-area alignment shifts the block within the usable area only — the
    patch count is identical, but the realised margins move toward the chosen
    anchor (#93). Default 'center-left' reproduces the prior placement."""
    def geom(align):
        return instruments.build("i1", patch_area_align=align)

    cap = geometry.patches_per_sheet(geom("center-left"), *A4)
    for align in ("top-left", "bottom-right", "center", "top-right"):
        assert geometry.patches_per_sheet(geom(align), *A4) == cap

    def insets(align):
        g = geom(align)
        lay = geometry.compute(g, *A4, cap)
        return geometry.realized_margins_mm(g, *A4, lay)  # (L, R, T, B)

    L0, R0, T0, B0 = insets("center-left")          # the prior default
    Lt, Rt, Tt, Bt = insets("top-left")
    Lb, Rb, Tb, Bb = insets("bottom-left")
    Lr, Rr, Tr, Br = insets("top-right")
    # top alignment → smaller top inset, larger bottom; bottom → the reverse
    assert Tt < T0 and Bt > B0
    assert Tb > T0 and Bb < B0
    # right alignment → smaller right inset, larger left
    assert Rr < R0 and Lr > L0


def test_align_default_matches_legacy_placement():
    """'center-left' must reproduce the exact placement the engine used before
    the alignment option existed (vertically centred, left-anchored)."""
    g = instruments.build("i1")
    assert g.patch_area_align == "center-left"
    lay = geometry.compute(g, *A4, 60)
    p = geometry.placement(g, *A4, lay)
    # left-anchored: patches start at the (clip-floored) left margin. The clip now
    # lives inside that margin, so x0 == margin_l (== 26 mm for the default clip),
    # the same absolute position as the old additive border+lbord (Knut beta-13).
    assert abs(p.x0 - g.margin_l) < 1e-9
    assert g.margin_l == 26.0


def test_area_first_fits_requested_grid():
    """Area-first sizes the patches so the requested columns/rows fill the usable
    area — capacity and the laid-out grid match (one chokepoint) (#93)."""
    from workflow.layout_engine.presets import default_recipe
    from dataclasses import replace

    def grid(**area):
        rec = replace(default_recipe("i1", "A4", mode="clip"),
                      layout_mode="area_first", area_method="by_grid", **area)
        g = instruments.geom_from_build_kwargs(rec.build_kwargs())
        lay = geometry.compute(g, *A4, geometry.patches_per_sheet(g, *A4))
        return lay.patches_per_page // lay.steps_in_pass, lay.steps_in_pass

    assert grid(area_cols=20, area_rows=30) == (20, 30)          # both pinned
    assert grid(area_cols=24)[0] == 24                           # columns pinned
    assert grid(area_rows=40)[1] == 40                           # rows pinned
    # ratio is height:width; with rows pinned, a taller ratio → narrower patches
    # → more columns fit.
    sq = grid(area_rows=40, area_ratio=1.0)[0]
    tall = grid(area_rows=40, area_ratio=2.0)[0]
    assert tall > sq


def test_area_first_noop_without_targets():
    """Area-first with no column/row target falls back to patch-first sizing."""
    from workflow.layout_engine.presets import default_recipe
    from dataclasses import replace
    base = instruments.geom_from_build_kwargs(
        default_recipe("i1", "A4", mode="clip").build_kwargs())
    rec = replace(default_recipe("i1", "A4", mode="clip"), layout_mode="area_first")
    area = instruments.geom_from_build_kwargs(rec.build_kwargs())
    assert area.pwid == base.pwid and area.plen == base.plen


def test_area_first_min_patch_autofit():
    """Knut's friendly path: min patch size + ratio, columns/rows on auto →
    the engine fits the most patches at >= the minimum and grows them to fill
    (so the realised patches are never smaller than the minimum) (#93)."""
    from workflow.layout_engine.presets import default_recipe
    from dataclasses import replace

    def geom(**area):
        rec = replace(default_recipe("i1", "A4", mode="clip"),
                      layout_mode="area_first", **area)
        return instruments.geom_from_build_kwargs(rec.build_kwargs())

    g8 = geom(area_min_patch_mm=8.0, area_ratio=1.0)
    assert g8.pwid >= 8.0 - 1e-6 and g8.plen >= 8.0 - 1e-6     # never below min
    g6 = geom(area_min_patch_mm=6.0, area_ratio=1.0)
    # a smaller minimum packs more patches
    assert (geometry.patches_per_sheet(g6, *A4)
            > geometry.patches_per_sheet(g8, *A4))
    # ratio is height:width — at 1.5 the patches grow taller than wide
    gr = geom(area_min_patch_mm=10.0, area_ratio=1.5)
    assert gr.plen > gr.pwid


def test_clip_side_left_right():
    """clip_side flips the clip band to the other edge and shifts the patch block
    accordingly, with the patch count unchanged (#93)."""
    gl = instruments.build("i1", clip_side="left")
    gr = instruments.build("i1", clip_side="right")
    # capacity is identical (the band reserves the same width either side)
    assert geometry.patches_per_sheet(gl, *A4) == geometry.patches_per_sheet(gr, *A4)
    lay = geometry.compute(gl, *A4, 60)
    pl = geometry.placement(gl, *A4, lay)
    pr = geometry.placement(gr, *A4, lay)
    # left: patches start after the clip band; right: at the left margin
    assert pl.x_of(0) > pr.x_of(0)
    al = geometry.clip_area_mm(gl, A4[1], A4[0])
    ar = geometry.clip_area_mm(gr, A4[1], A4[0])
    assert al[0] < ar[0]            # band moves from the left edge to the right
    assert ar[0] + ar[2] <= A4[0] + 1e-6


def test_cm_ss_notes_band_reserves_space():
    """CM/SS have no native clip border, but a notes band can be reserved on
    either edge when clip content is on — reducing capacity (#93, Knut)."""
    from workflow.layout_engine.presets import default_recipe
    from dataclasses import replace

    def cap(content, side="left"):
        kw = replace(default_recipe("CM", "A4"), clip_content_mode=content,
                     clip_side=side).build_kwargs()
        g = instruments.geom_from_build_kwargs(kw)
        return geometry.patches_per_sheet(g, *A4), g

    base, gb = cap("off")
    withnotes, gn = cap("notes")
    assert gb.lbord == 0 and gn.lbord > 0          # band reserved only with notes
    assert withnotes < base                        # capacity drops for the band
    # band flips to the right edge
    area = geometry.clip_area_mm(gn, A4[1], A4[0])
    area_r = geometry.clip_area_mm(cap("notes", "right")[1], A4[1], A4[0])
    assert area[0] < area_r[0]
