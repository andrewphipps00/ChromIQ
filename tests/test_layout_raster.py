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
    # Instrument Serif ships as a single Regular static face — neither style.
    assert raster.font_supports("Instrument Serif") == (False, False)
    # An unknown family supports nothing (renderer would fall back to default).
    assert raster.font_supports("No Such Font 123") == (False, False)
