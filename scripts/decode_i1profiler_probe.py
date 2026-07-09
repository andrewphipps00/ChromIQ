"""Read i1Profiler's layout straight off a printed probe chart (#120).

Companion to ``scripts/make_i1profiler_probe.py``. That script paints every
patch with a colour that encodes its own index; this one takes the PDF (or
image) of the chart i1Profiler printed and recovers:

  * the grid — columns, rows, pages;
  * the fill order — which patch of the list landed in which cell;
  * the geometry in mm — page size, chart origin, patch pitch, patch size.

Nothing is measured and no instrument is involved. Run it as::

    python scripts/decode_i1profiler_probe.py test1-autolayout.pdf

The grid is not assumed: every plausible (columns, rows) is tried and only one
that decodes EVERY cell to a distinct, in-range patch index is accepted. So a
wrong grid cannot silently produce a wrong answer — it produces no answer.

Pass two charts to compare them, which is the whole point of tests 2a/2b::

    python scripts/decode_i1profiler_probe.py test2a-nolocations.pdf test2b-reversed.pdf

If the two fill orders differ, i1Profiler honoured the Location tags we wrote
and ``emit_locations=True`` is viable. If they agree, it recomputes the grid
itself and ChromIQ must derive the geometry from the render instead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.make_i1profiler_probe import decode  # noqa: E402

RENDER_DPI = 150.0
WHITE = 245          # a page pixel at/above this in all channels is paper


# --------------------------------------------------------------------------
# page loading
# --------------------------------------------------------------------------

def load_pages(path: Path) -> list[tuple[np.ndarray, float]]:
    """[(RGB uint8 array, pixels-per-mm), …], one per page."""
    if path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise SystemExit(
                "Reading PDFs needs pypdfium2 — `pip install pypdfium2`, or "
                "export the chart as PNG/TIFF instead.")
        pdf = pdfium.PdfDocument(str(path))
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            bmp = page.render(scale=RENDER_DPI / 72.0)
            out.append((np.asarray(bmp.to_pil().convert("RGB")),
                        RENDER_DPI / 25.4))
        return out
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(path)
    dpi = (im.info.get("dpi") or (RENDER_DPI, RENDER_DPI))[0] or RENDER_DPI
    return [(np.asarray(im.convert("RGB")), float(dpi) / 25.4)]


def ink_bbox(img: np.ndarray) -> tuple[int, int, int, int]:
    """Bounding box of everything that isn't paper."""
    ink = (img < WHITE).any(axis=2)
    ys, xs = np.where(ink)
    if not len(xs):
        raise SystemExit("page is blank")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# --------------------------------------------------------------------------
# grid search — self-validating
# --------------------------------------------------------------------------

def _read_cells(img, bbox, cols, rows, frac=0.4):
    x0, y0, x1, y1 = bbox
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    out = []
    for c in range(cols):
        for r in range(rows):
            cx, cy = x0 + (c + 0.5) * cw, y0 + (r + 0.5) * ch
            hx, hy = cw * frac / 2, ch * frac / 2
            a = img[int(cy - hy):int(cy + hy), int(cx - hx):int(cx + hx)]
            if a.size == 0:
                return None
            med = np.median(a.reshape(-1, 3), axis=0)
            out.append(((c, r), tuple(int(round(v)) for v in med)))
    return out


def solve_grid(img: np.ndarray, bbox, n_hint: int | None):
    """Find (cols, rows) such that every cell decodes to a distinct index."""
    x0, y0, x1, y1 = bbox
    best = None
    for cols in range(2, 61):
        for rows in range(2, 41):
            if n_hint and cols * rows > n_hint:
                continue
            cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
            if cw < 6 or ch < 6:
                continue
            cells = _read_cells(img, bbox, cols, rows)
            if cells is None:
                continue
            idx = {}
            ok = True
            for pos, rgb in cells:
                d = decode(rgb)
                if d is None or d in idx:
                    ok = False
                    break
                idx[d] = pos
            if ok:
                # prefer the grid that uses the most cells
                if best is None or len(idx) > len(best[2]):
                    best = (cols, rows, idx)
    return best


def describe(path: Path, n_hint: int | None):
    pages = load_pages(path)
    print(f"\n=== {path.name}  ({len(pages)} page(s))")
    fill: dict[int, tuple[int, int, int]] = {}     # patch idx -> (page, col, row)
    for pno, (img, px_mm) in enumerate(pages, start=1):
        h, w = img.shape[:2]
        bb = ink_bbox(img)
        sol = solve_grid(img, bb, n_hint)
        if sol is None:
            print(f"  page {pno}: could not find a grid that decodes cleanly "
                  f"— is this the probe chart?")
            continue
        cols, rows, idx = sol
        x0, y0, x1, y1 = bb
        cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
        print(f"  page {pno}: {cols} columns x {rows} rows = {cols*rows} cells, "
              f"all decoded")
        print(f"    page size      {w/px_mm:7.2f} x {h/px_mm:7.2f} mm")
        print(f"    chart origin   {x0/px_mm:7.2f} , {y0/px_mm:7.2f} mm "
              f"(top-left of the patch area)")
        print(f"    right/bottom margin {(w-x1)/px_mm:6.2f} , "
              f"{(h-y1)/px_mm:6.2f} mm")
        print(f"    patch pitch    {cw/px_mm:7.3f} x {ch/px_mm:7.3f} mm")
        lo, hi = min(idx), max(idx)
        print(f"    patch indices  {lo} … {hi}")
        first = idx[lo]
        print(f"    patch {lo} sits at column {first[0]}, row {first[1]}")
        # infer the fill rule
        by_pos = {v: k for k, v in idx.items()}
        colmaj = all(by_pos.get((c, r)) == by_pos.get((0, 0), 0) + c * rows + r
                     for c in range(cols) for r in range(rows)
                     if (c, r) in by_pos)
        rowmaj = all(by_pos.get((c, r)) == by_pos.get((0, 0), 0) + r * cols + c
                     for c in range(cols) for r in range(rows)
                     if (c, r) in by_pos)
        rule = ("column-major (down each column)" if colmaj else
                "row-major (across each row)" if rowmaj else
                "neither column- nor row-major — see the dump below")
        print(f"    fill order     {rule}")
        for i, pos in idx.items():
            fill[i] = (pno, *pos)
    return fill


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("charts", nargs="+", type=Path)
    ap.add_argument("-n", "--patches", type=int, default=600)
    a = ap.parse_args()

    fills = [describe(p, a.patches) for p in a.charts]

    if len(fills) >= 2:
        print("\n=== comparison")
        base, *rest = fills
        for path, f in zip(a.charts[1:], rest):
            common = set(base) & set(f)
            same = sum(1 for i in common if base[i] == f[i])
            print(f"  {a.charts[0].name} vs {path.name}: "
                  f"{same}/{len(common)} patches in the same cell")
            if same == len(common):
                print("  → IDENTICAL layout. i1Profiler IGNORED our Location "
                      "tags and laid the chart out itself.")
            elif same < len(common) * 0.05:
                print("  → DIFFERENT layout. i1Profiler HONOURED our Location "
                      "tags; emit_locations=True is viable.")
            else:
                print("  → partially different — worth a look by hand.")


if __name__ == "__main__":
    main()
