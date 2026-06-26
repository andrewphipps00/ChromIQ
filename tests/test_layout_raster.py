"""Tests for the TIFF raster, contrast guard, and strip geometry."""
import numpy as np
import tifffile

from workflow.layout_engine import contrast, geometry, instruments, raster
from workflow.layout_engine.colorants import to_display_rgb
from workflow.layout_engine.ti1_reader import ColorTarget


def _rgb_target(n):
    patches = []
    for i in range(n):
        patches.append(((float(i * 9 % 100), float(i * 17 % 100), float(i * 5 % 100)),
                        (40.0, 45.0, 50.0)))
    return ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                       patches=patches)


def test_render_dimensions_and_pages():
    target = _rgb_target(60)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    res = raster.render_pages(target, lay, geom, seed=42,
                              paper_w_mm=210.0, paper_h_mm=297.0, dpi=150)
    assert len(res.images) == lay.pages == 1
    w, h = res.images[0].size
    assert w == round(210.0 * 150 / 25.4)
    assert h == round(297.0 * 150 / 25.4)


def test_saved_tiff_colours_match_ti2_at_every_location(tmp_path):
    """The chartread-critical property end to end: every patch in the SAVED .tif
    must show the exact device colour the .ti2 records at that SAMPLE_LOC — so
    what gets printed is what chartread expects, for a *randomised* chart (#93)."""
    import random
    from PIL import Image
    from workflow.layout_engine import chart as le_chart, papers
    from workflow.layout_engine.presets import default_recipe
    import workflow.ti2_relayout as R

    random.seed(11)
    prog = [(random.random() * 100, random.random() * 100, random.random() * 100)
            for _ in range(300)]
    R.write_ti1(R.ChartSpec.new("i1", "A4"), prog, tmp_path / "s.ti1")
    rec = default_recipe("i1", "A4"); rec.randomize = True; rec.seed = 777
    kw = rec.build_kwargs(); kw["dpi"] = 200
    res = le_chart.build_chart(str(tmp_path / "s.ti1"), tmp_path / "chart", **kw)

    # The .ti2 chartread reads: SAMPLE_LOC -> device value.
    spec = R.ChartSpec.from_ti2(tmp_path / "chart.ti2")
    loc_dev = {p.loc: p.dev for p in spec.patches if p.loc}
    assert loc_dev, "no SAMPLE_LOC patches parsed from .ti2"

    geom = instruments.geom_from_build_kwargs(kw)
    w, h = papers.dimensions_mm("A4")
    rects = geometry.patch_rects_px(geom, w, h, res.layout, kw["dpi"],
                                    rec.strip_pattern, rec.patch_pattern)
    imgs = [np.asarray(Image.open(p).convert("RGB")) for p in res.tiff_paths]

    checked = 0
    for d in rects:
        dev = loc_dev.get(d["loc"])
        if dev is None:
            continue
        cx, cy = d["x"] + d["w"] // 2, d["y"] + d["h"] // 2
        got = tuple(int(v) for v in imgs[d["page"]][cy, cx])
        assert got == tuple(to_display_rgb(dev, spec.color_rep)), \
            f"{d['loc']}: tif {got} != ti2 {to_display_rgb(dev, spec.color_rep)}"
        checked += 1
    assert checked >= 300


def test_raster_matches_ti2_slot_assignment():
    # randomize=False -> patch 0 sits at slot 0 = top of column A.
    target = _rgb_target(60)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    res = raster.render_pages(target, lay, geom, seed=1, randomize=False,
                              paper_w_mm=210.0, paper_h_mm=297.0, dpi=300)
    rects = geometry.strip_rects_px(geom, 210.0, 297.0, lay, 300)
    r0 = rects[0]
    cx, cy = r0["x"] + r0["w"] // 2, r0["y"] + geometry.placement(
        geom, 210.0, 297.0, lay).plen * 300 / 25.4 / 2
    px = res.images[0].getpixel((int(cx), int(cy)))
    assert px == to_display_rgb(target.patches[0][0], "iRGB")


def test_strip_rects_within_bounds():
    target = _rgb_target(60)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    rects = geometry.strip_rects_px(geom, 210.0, 297.0, lay, 300)
    assert len(rects) == lay.passes          # single strip per page → passes columns
    W = round(210.0 * 300 / 25.4)
    H = round(297.0 * 300 / 25.4)
    for r in rects:
        assert 0 <= r["x"] and r["x"] + r["w"] <= W
        assert 0 <= r["y"] and r["y"] + r["h"] <= H


def test_save_tiff_resolution(tmp_path):
    target = _rgb_target(40)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 40)
    res = raster.render_pages(target, lay, geom, seed=7,
                              paper_w_mm=210.0, paper_h_mm=297.0, dpi=200)
    paths = raster.save_tiffs(res.images, tmp_path / "c.tif", dpi=200)
    assert len(paths) == 1 and paths[0].exists()
    with tifffile.TiffFile(str(paths[0])) as tf:
        page = tf.pages[0]
        xres = page.tags["XResolution"].value
        unit = page.tags["ResolutionUnit"].value
        res_val = xres[0] / xres[1]
        assert int(unit) == 3                      # CENTIMETER, like printtarg
        assert abs(res_val * 2.54 - 200) < 1.0     # px/cm -> dpi


def test_patch_rects_known_for_every_slot():
    target = _rgb_target(60)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    pr = geometry.patch_rects_px(geom, 210.0, 297.0, lay, 300)
    # one rect per slot, each labelled, all inside the page
    assert len(pr) == lay.total_patches
    assert pr[0]["loc"] == "A1"
    assert pr[20]["loc"] == "A21"
    assert pr[21]["loc"] == "B1"
    W, H = round(210.0 * 300 / 25.4), round(297.0 * 300 / 25.4)
    for r in pr:
        assert 0 <= r["x"] and r["x"] + r["w"] <= W
        assert 0 <= r["y"] and r["y"] + r["h"] <= H


def test_contrast_spacer_choice():
    assert contrast.spacer_rgb((255, 255, 255), (240, 240, 240)) == (0, 0, 0)
    assert contrast.spacer_rgb((0, 0, 0), (10, 10, 10)) == (255, 255, 255)


def test_colored_spacer_is_in_palette_and_contrasts():
    sp = contrast.colored_spacer_rgb((128, 128, 128), (130, 130, 130))
    assert sp in contrast._COLOURED_PALETTE
    # a coloured spacer between two mid-greys should be far from grey
    assert contrast._rgb_dist(sp, (128, 128, 128)) > 100
    # spacer_for_mode routes correctly
    assert contrast.spacer_for_mode("bw", (255, 255, 255), (240, 240, 240)) == (0, 0, 0)
    assert contrast.spacer_for_mode("colored", (128, 128, 128), (130, 130, 130)) == sp


def test_font_supports_bundled():
    # JetBrains Mono is a weight-axis variable font (bold yes, italic no in-file).
    has_bold, has_italic = raster.font_supports("JetBrains Mono")
    assert has_bold is True
    assert has_italic is False
    # Instrument Serif ships a real Italic face (used by the masthead "IQ"), but
    # no Bold face — so italic yes, bold no.
    assert raster.font_supports("Instrument Serif") == (False, True)
    # An unknown family supports nothing (renderer would fall back to default).
    assert raster.font_supports("No Such Font 123") == (False, False)


def test_instrument_serif_italic_face_resolves():
    """The masthead "IQ" needs the real Instrument Serif Italic face, not a
    sheared Regular — so italic must resolve to a different file (#93)."""
    reg = raster._font_path("Instrument Serif", "regular")
    ital = raster._font_path("Instrument Serif", "italic")
    assert reg and ital and reg != ital
    assert str(ital).endswith("Italic.ttf")


def test_underline_modes():
    import numpy as np
    target = _rgb_target(120)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 120)
    kw = dict(seed=7, paper_w_mm=210.0, paper_h_mm=297.0, dpi=150)
    accents = set(raster.ACCENT_RGB)

    def has_accent(res):
        arr = np.asarray(res.images[0])
        return any((arr == np.array(c)).all(axis=2).any() for c in accents)

    def accents_present(res):
        arr = np.asarray(res.images[0])
        return {c for c in accents if (arr == np.array(c)).all(axis=2).any()}

    # off → no accent rule pixels.
    assert not has_accent(raster.render_pages(target, lay, geom, underline_mode="off", **kw))
    # segments → all five accents appear (5-part bar under each strip).
    assert accents_present(raster.render_pages(
        target, lay, geom, underline_mode="segments", **kw)) == accents
    # legacy "colored" aliases to the 5-segment bar.
    assert accents_present(raster.render_pages(
        target, lay, geom, underline_mode="colored", **kw)) == accents
    # per-strip cycle → at least one accent present.
    assert has_accent(raster.render_pages(target, lay, geom,
                                          underline_mode="cycle", **kw))
    # hiding indicators suppresses the rule even if a mode is set.
    assert not has_accent(raster.render_pages(
        target, lay, geom, underline_mode="segments", draw_indicators=False, **kw))


def test_clip_content_modes():
    import numpy as np
    target = _rgb_target(120)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 120)
    ax, ay, aw, ah = geometry.clip_area_px(geom, 297.0, 200)

    def clip_ink(mode, **kw):
        res = raster.render_pages(target, lay, geom, seed=7, paper_w_mm=210.0,
                                  paper_h_mm=297.0, dpi=200,
                                  clip_content_mode=mode, **kw)
        sub = np.asarray(res.images[0])[ay:ay + ah, ax:ax + aw]
        return int((sub < 200).any(axis=2).sum())

    assert clip_ink("off") == 0
    assert clip_ink("text", clip_text="Sample 12") > 0
    assert clip_ink("branding") > 0
    assert clip_ink("notes") > 0


def test_export_clip_template(tmp_path):
    from PIL import Image
    paths = raster.export_clip_template(
        tmp_path / "tpl", width_px=160, height_px=2240,
        width_mm=20.0, height_mm=285.0, dpi=200)
    names = {p.suffix for p in paths}
    assert names == {".png", ".pdf"}
    for p in paths:
        assert p.exists() and p.stat().st_size > 0
    with Image.open(str(tmp_path / "tpl.png")) as im:
        assert im.size == (160, 2240)          # exact clip pixel size


def test_no_interstrip_gaps_from_rounding():
    """Touching strips must tile with no 1px white gap from px rounding (the
    8mm pitch rounds 94/95 while a fixed width stayed 94, gapping every other
    strip)."""
    import numpy as np
    # all patches the same non-white colour → strips form one solid block
    patches = [((50.0, 60.0, 70.0), (40.0, 45.0, 50.0)) for _ in range(441)]
    target = ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                         patches=patches)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 441)
    res = raster.render_pages(target, lay, geom, seed=1, randomize=False,
                              paper_w_mm=210.0, paper_h_mm=297.0, dpi=300)
    a = np.asarray(res.images[0])
    # within the patch band, no fully-white column between the first and last patch
    band = a[(a < 250).any(2).mean(1) > 0.3]
    colwhite = (band >= 250).all(2).mean(0)
    inked = np.where(colwhite <= 0.85)[0]
    interior = colwhite[inked.min():inked.max() + 1]
    assert not (interior > 0.85).any(), "found a white gap column between strips"


def test_indicator_rotation_renders():
    """Rotated strip labels still ink the leader area (0/90/180/270)."""
    import numpy as np
    target = _rgb_target(60)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    for deg in (0, 90, 180, 270):
        res = raster.render_pages(target, lay, geom, seed=1, paper_w_mm=210.0,
                                  paper_h_mm=297.0, dpi=150, indicator_rotation=deg)
        a = np.asarray(res.images[0])
        # the top leader band should contain black label ink
        band = a[: int(a.shape[0] * 0.12)]
        assert (band < 60).all(axis=2).any(), f"no label ink at {deg}°"


def test_indicator_align_rotated_multiletter():
    """For side-rotated labels, Left vs Right alignment must change the render
    once two-letter labels (AA…) appear — Left grows the label away from the
    patches, Right toward them (#93)."""
    import numpy as np
    n = 700                                   # >26 strips → AA, AB, … exist
    target = _rgb_target(n)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, n)

    def render(align):
        return raster.render_pages(
            target, lay, geom, seed=1, paper_w_mm=210.0, paper_h_mm=297.0,
            dpi=150, indicator_rotation=90, indicator_align=align)

    left, right = render("left"), render("right")
    assert any(not np.array_equal(np.asarray(l), np.asarray(r))
               for l, r in zip(left.images, right.images)), \
        "Left and Right alignment rendered identically"


def test_indicator_align_noop_without_multiletter():
    """Alignment is a no-op when every label is a single letter (no band to
    justify within) — Left / Center / Right then render identically."""
    import numpy as np
    n = 120                                   # well under 26 strips → A…single
    target = _rgb_target(n)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, n)

    def page0(align):
        return np.asarray(raster.render_pages(
            target, lay, geom, seed=1, paper_w_mm=210.0, paper_h_mm=297.0,
            dpi=150, indicator_rotation=90, indicator_align=align).images[0])

    assert np.array_equal(page0("left"), page0("right"))
    assert np.array_equal(page0("left"), page0("center"))


def test_edge_spacers_reclaim_when_off_and_draw_when_on():
    """Edge spacers bracket each strip when ON (printtarg parity); when OFF the
    two end gaps are reclaimed for patches (denser than printtarg). The render
    draws them only when on, and the block fits the page either way (#93)."""
    import numpy as np
    # Capacity: OFF reclaims, so it never fits fewer than ON; on a height-bound
    # page with a fat spacer it fits strictly more.
    on = geometry.patches_per_sheet(
        instruments.build("i1", spacer_width=8.0, edge_spacers=True), 210.0, 297.0)
    off = geometry.patches_per_sheet(
        instruments.build("i1", spacer_width=8.0, edge_spacers=False), 210.0, 297.0)
    assert off > on

    # Render: edge spacers appear only when on, and nothing overflows.
    target = _rgb_target(120)
    g_on = instruments.build("i1", spacer_width=8.0, edge_spacers=True)
    lay = geometry.compute(g_on, 210.0, 297.0, 120)
    pl = geometry.placement(g_on, 210.0, 297.0, lay)

    def render(edge):
        return raster.render_pages(
            target, lay, g_on, seed=1, randomize=False, paper_w_mm=210.0,
            paper_h_mm=297.0, dpi=150, spacer_mode="bw", edge_spacers=edge)

    img_off = np.asarray(render(False).images[0])
    img_on = np.asarray(render(True).images[0])
    assert not np.array_equal(img_off, img_on)        # spacers drawn when on
    # leading edge spacer sits in the reserved gap above the first patch
    first_top = int(pl.y_of(0) * 150 / 25.4)
    band = img_on[max(0, first_top - int(g_on.pspa * 150 / 25.4)):first_top]
    assert (band < 250).any(), "no leading edge spacer drawn"
    # trailing edge spacer stays within the usable area
    last_bottom = pl.y_of(lay.steps_in_pass - 1) + pl.plen + g_on.pspa
    assert last_bottom <= 297.0 - max(g_on.margin_b, g_on.tspa) + 0.5


def test_custom_spacer_palette():
    """Coloured spacers are drawn only from a supplied custom palette."""
    import numpy as np
    patches = [((50.0, 50.0, 50.0), (40.0, 45.0, 50.0)) for _ in range(60)]
    target = ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                         patches=patches)
    geom = instruments.build("i1")
    lay = geometry.compute(geom, 210.0, 297.0, 60)
    res = raster.render_pages(target, lay, geom, seed=1, paper_w_mm=210.0,
                              paper_h_mm=297.0, dpi=150, spacer_mode="colored",
                              spacer_palette=[(255, 0, 0), (255, 255, 0)])
    a = np.asarray(res.images[0])
    assert (a == [255, 0, 0]).all(2).any()          # a palette colour is used
    assert not (a == [0, 255, 0]).all(2).any()      # a non-palette colour isn't
