"""Emit an ArgyllCMS ``.cht`` chart-recognition template for an engine chart.

A ``.cht`` lets ``scanin`` read patch colours out of a *scanned image* of the
printed chart (a cheap flatbed-scanner alternative to a spectro), and carries the
per-patch reference layout used by the SpectroScan. ``printtarg -s`` writes one by
tracking the rectangle edges it draws; because the ChromIQ engine *computes* the
layout, we know every patch box exactly and can emit the same file directly from
geometry — no image edge-detection heuristics (#93, Knut).

Format (origin is **bottom-left, millimetres**, matching printtarg):

    BOXES <n>
      X <loc> <loc> _ _ <w> <h> <xo> <yo> 0 0     # one per patch
    BOX_SHRINK <mm>
    REF_ROTATION 0.0
    XLIST <n> / YLIST <n>                          # vertical / horizontal edges
      <pos> <len> <cc>                             # normalised length + count
    EXPECTED XYZ <n>
      <loc> <X> <Y> <Z>

NOTE: this writes the exact geometry, but ``scanin`` registration normally also
needs fiducial marks printed on the chart (printtarg's ``-s`` adds them); the
engine does not draw fiducials yet, so a real scan test is still required before
relying on scanner reading.
"""
from __future__ import annotations

from pathlib import Path

_TOL = 0.05  # mm: merge edges this close together


def _edge_list(positions_len: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Collapse ``(position, edge_length)`` pairs into sorted ``(pos, len, cc)``
    rows with *len* and *cc* (count) normalised to their maxima, the way
    printtarg's XLIST/YLIST are."""
    merged: list[list[float]] = []   # [pos, total_len, count]
    for pos, ln in sorted(positions_len):
        if merged and abs(pos - merged[-1][0]) <= _TOL:
            merged[-1][1] += ln
            merged[-1][2] += 1
        else:
            merged.append([pos, ln, 1.0])
    if not merged:
        return []
    max_len = max(m[1] for m in merged) or 1.0
    max_cc = max(m[2] for m in merged) or 1.0
    return [(m[0], m[1] / max_len, m[2] / max_cc) for m in merged]


def build_cht_text(boxes: list[dict], expected: list[tuple[str, float, float, float]]) -> str:
    """Render the ``.cht`` text. *boxes* are ``{loc,x,y,w,h}`` in mm with a
    bottom-left origin; *expected* is ``(loc, X, Y, Z)`` reference values."""
    out: list[str] = ["", "", f"BOXES {len(boxes)}"]
    mins = 1e6
    for b in boxes:
        out.append("  X {loc} {loc} _ _ {w:f} {h:f} {x:f} {y:f} 0 0".format(
            loc=b["loc"], w=b["w"], h=b["h"], x=b["x"], y=b["y"]))
        mins = min(mins, b["w"], b["h"])
    out.append("")
    out.append("BOX_SHRINK {:f}".format((mins if mins < 1e6 else 1.0) * 0.15))
    out.append("")
    out.append("REF_ROTATION 0.0")
    out.append("")

    # Vertical edges (constant x) → XLIST, length = patch height; horizontal
    # edges (constant y) → YLIST, length = patch width.
    xedges = [(b["x"], b["h"]) for b in boxes] + [(b["x"] + b["w"], b["h"]) for b in boxes]
    yedges = [(b["y"], b["w"]) for b in boxes] + [(b["y"] + b["h"], b["w"]) for b in boxes]
    xl = _edge_list(xedges)
    yl = _edge_list(yedges)
    out.append(f"XLIST {len(xl)}")
    out += [f"  {p:f} {ln:f} {cc:f}" for p, ln, cc in xl]
    out.append("")
    out.append(f"YLIST {len(yl)}")
    out += [f"  {p:f} {ln:f} {cc:f}" for p, ln, cc in yl]
    out.append("")
    out.append("")

    out.append(f"EXPECTED XYZ {len(expected)}")
    out += [f"  {loc} {x:f} {y:f} {z:f}" for loc, x, y, z in expected]
    out.append("")
    return "\n".join(out)


def boxes_from_patch_rects(patch_rects: list[dict], paper_h_mm: float, dpi: int,
                           page: int = 0) -> list[dict]:
    """Convert engine ``patch_rects_px`` (top-left origin, px) for *page* into
    ``.cht`` boxes (bottom-left origin, mm)."""
    s = 25.4 / dpi
    boxes = []
    for r in patch_rects:
        if r.get("page", 0) != page:
            continue
        w, h = r["w"] * s, r["h"] * s
        x = r["x"] * s
        y = paper_h_mm - (r["y"] * s + h)        # flip to bottom-left origin
        boxes.append({"loc": r["loc"], "x": x, "y": y, "w": w, "h": h})
    return boxes


def write_cht(path: str | Path, boxes: list[dict],
              expected: list[tuple[str, float, float, float]]) -> Path:
    p = Path(path)
    p.write_text(build_cht_text(boxes, expected), encoding="utf-8")
    return p
