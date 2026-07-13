"""Vector-PDF export fidelity tests (#72).

The authoritative check is **structural**: every patch's exact device value must
be encoded as a fill operator in the PDF content stream, at the position the
collected geometry (shared with the TIFF) dictates — so the PDF and TIFF cannot
diverge. A second, colour-management-proof check rasterises the PDF (macOS
``sips``) and compares the *ink-vs-paper layout* to the TIFF, catching any
misplaced or missing element without being fooled by the viewer's DeviceRGB
colour management (which shifts midtones on render but not the encoded values).
"""
import re
import shutil
import subprocess
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from workflow.layout_engine import (chart, geometry, instruments, papers,
                                     raster, ti1_reader, vector_pdf)
from workflow.layout_engine.ti1_reader import ColorTarget


def _rgb_target(n):
    return ColorTarget(color_rep="iRGB", device_fields=["RGB_R", "RGB_G", "RGB_B"],
                       patches=[((float(i * 9 % 100), float(i * 17 % 100),
                                  float(i * 5 % 100)), (40.0, 45.0, 50.0))
                                for i in range(n)])


def _cmyk_target(n):
    f = ["CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"]
    return ColorTarget(color_rep="CMYK", device_fields=f,
                       patches=[(tuple(float((i * (7 + c * 3)) % 100) for c in range(4)),
                                 (40.0, 45.0, 50.0)) for i in range(n)])


def _render(target, dpi=200, **kw):
    geom = instruments.build("i1")
    w, h = papers.dimensions_mm("A4")
    lay = geometry.compute(geom, w, h, len(target.patches))
    res = raster.render_pages(target, lay, geom, seed=3, randomize=False,
                              paper_w_mm=w, paper_h_mm=h, dpi=dpi,
                              collect_device_geom=True, **kw)
    return res, w, h


def _content(pdf_path: Path, page: int = 0) -> str:
    from pypdf import PdfReader
    data = PdfReader(str(pdf_path)).pages[page].get_contents().get_data()
    return data.decode("latin-1")


def test_pdf_is_valid_multipage_and_exact_page_size(tmp_path):
    from pypdf import PdfReader
    target = _cmyk_target(600)                      # enough to span 2 pages
    res, w, h = _render(target, dpi=150)
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=150)
    r = PdfReader(str(out))
    assert len(r.pages) == len(res.patch_geom) >= 1
    box = r.pages[0].mediabox
    assert abs(float(box.width) - w * 72 / 25.4) < 0.01
    assert abs(float(box.height) - h * 72 / 25.4) < 0.01


def test_rgb_patch_colours_encoded_exactly(tmp_path):
    """Every RGB patch's exact device value appears as an `rg` fill operator."""
    target = _rgb_target(120)
    res, w, h = _render(target, dpi=200)
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=200)
    content = _content(out)
    rects = [e for e in res.patch_geom[0] if e[0] == "rect"]
    assert rects
    for _, _, dev in rects:
        op = " ".join(f"{v / 100.0:.4f}" for v in dev) + " rg"
        assert op in content, f"missing exact fill {op}"


def test_cmyk_patch_colours_and_positions_exact(tmp_path):
    """CMYK patches: exact `k` fill AND the exact rectangle at the right point."""
    target = _cmyk_target(120)
    dpi = 200
    res, w, h = _render(target, dpi=dpi)
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=dpi)
    content = _content(out)
    px2pt = 72.0 / dpi
    page_h_pt = h * 72 / 25.4
    checked = 0
    for kind, (x0, y0, x1, y1), dev in (e for e in res.patch_geom[0] if e[0] == "rect"):
        col = " ".join(f"{v / 100.0:.4f}" for v in dev) + " k"
        assert col in content
        rect = (f"{x0 * px2pt:.3f} {page_h_pt - y1 * px2pt:.3f} "
                f"{(x1 - x0) * px2pt:.3f} {(y1 - y0) * px2pt:.3f} re f")
        assert rect in content, f"missing rect {rect}"
        checked += 1
    assert checked >= 100


def test_devicen_colourspace_present_for_extra_inks(tmp_path):
    f = ["CMYKOG_C", "CMYKOG_M", "CMYKOG_Y", "CMYKOG_K", "CMYKOG_O", "CMYKOG_G"]
    target = ColorTarget(color_rep="CMYKOG", device_fields=f,
                         patches=[(tuple(float((i * (5 + c)) % 100) for c in range(6)),
                                   (40.0, 45.0, 50.0)) for i in range(80)])
    res, w, h = _render(target, dpi=150)
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=150)
    raw = out.read_bytes()
    assert b"/DeviceN" in raw and b"/Orange" in raw and b"/Green" in raw
    # a 6-channel patch fill uses the `scn` operator in the DeviceN space
    assert " scn" in _content(out)


def test_export_pdf_via_build_chart(tmp_path):
    import workflow.ti2_relayout as R
    ti1 = tmp_path / "s.ti1"
    prog = [((i * 9 % 100), (i * 17 % 100), (i * 5 % 100)) for i in range(120)]
    R.write_ti1(R.ChartSpec.new("i1", "A4"), prog, ti1)
    res = chart.build_chart(str(ti1), tmp_path / "chart", instrument="i1",
                            paper="A4", seed=3, dpi=150, export_pdf=True,
                            chart_text="ChromIQ")
    assert res.pdf_path and res.pdf_path.exists()
    from pypdf import PdfReader
    assert len(PdfReader(str(res.pdf_path)).pages) >= 1
    # the TIFF is still written alongside
    assert res.tiff_paths and res.tiff_paths[0].exists()


def test_export_pdf_recipe_roundtrip():
    from workflow.layout_engine.presets import LayoutRecipe
    rec = LayoutRecipe(instrument="i1", paper="A4", export_pdf=True)
    from dataclasses import asdict
    back = LayoutRecipe.from_dict(asdict(rec))
    assert back.export_pdf is True
    assert rec.build_kwargs()["export_pdf"] is True


@pytest.mark.skipif(not shutil.which("sips"), reason="needs macOS sips to rasterise")
def test_devicen_tint_previews_real_colours(tmp_path):
    """The DeviceN tint transform must let a viewer preview each ink as its real
    colour (guards against the tint collapsing everything to one channel — the
    magenta-everything bug). Cyan reads blue-ish, yellow yellow, orange orange,
    green green — each ink distinct, not all pink."""
    f = ["CMYKOG_C", "CMYKOG_M", "CMYKOG_Y", "CMYKOG_K", "CMYKOG_O", "CMYKOG_G"]
    devs = [(100, 0, 0, 0, 0, 0), (0, 100, 0, 0, 0, 0), (0, 0, 100, 0, 0, 0),
            (0, 0, 0, 0, 100, 0), (0, 0, 0, 0, 0, 100)]      # C M Y O G
    patches = [(tuple(map(float, d)), (50., 50., 50.)) for d in devs] * 12
    target = ColorTarget("CMYKOG", f, patches)
    res, w, h = _render(target, dpi=200)
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=200)
    png = tmp_path / "c.png"
    subprocess.run(["sips", "-s", "format", "png", "--resampleWidth",
                    str(res.images[0].width), str(out), "--out", str(png)],
                   check=True, capture_output=True)
    pdf = np.asarray(Image.open(png).convert("RGB").resize(res.images[0].size))
    names = ["C", "M", "Y", "K", "O", "G"]
    seen = {}
    for _, (x0, y0, x1, y1), dev in [e for e in res.patch_geom[0] if e[0] == "rect"]:
        nz = [i for i, v in enumerate(dev) if v]
        if len(nz) != 1:                                  # only single-ink patches
            continue
        r, g, b = pdf[(y0 + y1) // 2, (x0 + x1) // 2]
        seen[names[nz[0]]] = (int(r), int(g), int(b))
        if {"C", "Y", "O", "G"} <= set(seen):
            break
    assert seen["C"][2] > seen["C"][0] + 30, f"cyan not blue-ish: {seen['C']}"
    assert seen["Y"][0] > 150 and seen["Y"][2] < 150, f"yellow wrong: {seen['Y']}"
    assert seen["O"][0] > seen["O"][2] + 40, f"orange not warm: {seen['O']}"
    assert seen["G"][1] > seen["G"][0] and seen["G"][1] > seen["G"][2], \
        f"green not green: {seen['G']}"


@pytest.mark.skipif(not shutil.which("sips"), reason="needs macOS sips to rasterise")
@pytest.mark.parametrize("rotation", [0, 90, 270, 180])
def test_pdf_text_layout_matches_tiff(tmp_path, rotation):
    """CM-proof text-placement check for every label rotation. Patch colour/
    position exactness is proven structurally above; here we verify the *vector
    text* lands where Pillow drew it by rasterising the PDF and comparing the
    strip-label band — black glyphs on white paper, which DeviceRGB colour
    management leaves untouched (unlike midtone patch colours, whose raster diff
    is CM-confounded and not used)."""
    target = _rgb_target(400)                       # many strips → a full label row
    res, w, h = _render(target, dpi=200, indicator_rotation=rotation)
    assert res.label_band_bottom_px, "no label band to test"
    tif = np.asarray(res.images[0].convert("L"))
    out = vector_pdf.save_vector_pdf(res, target, tmp_path / "c.pdf",
                                     paper_w_mm=w, paper_h_mm=h, dpi=200)
    png = tmp_path / "c.png"
    subprocess.run(["sips", "-s", "format", "png", "--resampleWidth",
                    str(tif.shape[1]), str(out), "--out", str(png)],
                   check=True, capture_output=True)
    pdf = np.asarray(Image.open(png).convert("L").resize((tif.shape[1], tif.shape[0])))
    # The strip-label band (page top → band bottom, above the first patch row) is
    # black glyphs on white paper — CM-invariant. Skip the first 3 rows (sips
    # renders a faint border artifact on the page edge).
    lo, hi = 3, int(res.label_band_bottom_px)
    band_diff = float(np.abs(tif[lo:hi].astype(int) - pdf[lo:hi].astype(int)).mean())
    # Aligned vector text diffs in the single digits (glyph rasterisation vs
    # Pillow's AA, larger for rotated glyphs); a *misplaced* label diffs in the
    # tens to hundreds, so this bound catches real placement errors.
    assert band_diff < 12.0, f"label-band text diff {band_diff:.2f} — text misplaced"
    # And the labels are actually present (not a blank band matching a blank band).
    assert (tif[lo:hi] < 80).sum() > 200 and (pdf[lo:hi] < 80).sum() > 200
