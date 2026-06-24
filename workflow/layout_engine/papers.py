"""Human-readable paper sizes for the layout engine.

Reuses the app's existing paper definitions (``data.patch_db.PAPER_SIZES`` /
``PAPER_LABELS`` / ``EXCLUDED_PAPERS``) so the layout engine offers the *same*
named sizes — "A4 (210 × 297 mm) Portrait" etc. — as the rest of ChromIQ,
rather than a parallel list.  Adds the millimetre dimensions the engine needs.
"""
from __future__ import annotations

from data.patch_db import EXCLUDED_PAPERS, PAPER_LABELS, PAPER_SIZES

# Millimetre dimensions for the named (non ``WxH``) codes. The ``WxH`` codes in
# PAPER_SIZES (e.g. "594x420") are parsed directly.
_NAMED_MM: dict[str, tuple[float, float]] = {
    "A2":      (420.0, 594.0),
    "A3":      (297.0, 420.0),
    "A4":      (210.0, 297.0),
    "A4R":     (297.0, 210.0),
    "Letter":  (215.9, 279.4),
    "LetterR": (279.4, 215.9),
    "11x17":   (279.4, 431.8),
    "Legal":   (215.9, 355.6),
    "4x6":     (101.6, 152.4),
}


def parse_custom(code: str) -> tuple[float, float] | None:
    """Parse a ``WxH`` custom code (mm) → (w, h), else None."""
    if "x" not in code:
        return None
    try:
        w, h = code.lower().split("x", 1)
        return float(w), float(h)
    except (ValueError, AttributeError):
        return None


def dimensions_mm(code: str) -> tuple[float, float]:
    """Return (width_mm, height_mm) for a paper *code* (named or ``WxH``)."""
    if code in _NAMED_MM:
        return _NAMED_MM[code]
    dims = parse_custom(code)
    if dims is None:
        raise ValueError(f"unknown paper code {code!r}")
    return dims


def label(code: str) -> str:
    """Human-readable label, falling back to the code for custom sizes."""
    return PAPER_LABELS.get(code, code)


def list_papers(instrument: str | None = None) -> list[tuple[str, str, tuple[float, float]]]:
    """``[(code, label, (w_mm, h_mm))]`` in the app's canonical order.

    If *instrument* is given, sizes excluded for it (``EXCLUDED_PAPERS``) are
    omitted — matching the dropdown behaviour elsewhere in the app.
    """
    excluded = EXCLUDED_PAPERS.get(instrument or "", set())
    out: list[tuple[str, str, tuple[float, float]]] = []
    for code in PAPER_SIZES:
        if code in excluded:
            continue
        out.append((code, label(code), dimensions_mm(code)))
    return out
