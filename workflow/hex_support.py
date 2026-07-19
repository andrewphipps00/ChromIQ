"""Detect SpectroScan hexagonal-patch charts (Knut #126).

The CHT recognition-file format that ChromIQ's scanner / camera tools rely on
can only describe rectangular patch boxes — it has no way to express a
hexagon. So a chart made with SpectroScan *hexagonal* patches cannot be turned
into (or read back with) a CHT file, and the scanner/camera features are not
supported for it. We detect such charts both at creation (the recipe) and when
one is loaded into a scanner feature (its ``channels.json`` sidecar), and warn
the user to use square/rectangular patches instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.i18n import tr


def hex_unsupported_message() -> str:
    """Friendly, extensive explanation for the warning dialogs — lists exactly
    which features don't work and how to make a chart that does."""
    return tr(
        "This chart uses hexagonal SpectroScan patches.\n\n"
        "The recognition file (CHT) that ChromIQ's scanner and camera tools "
        "rely on can only describe rectangular patches — the file format has "
        "no way to represent a hexagon. So these features cannot be used with "
        "a hexagonal chart:\n\n"
        "  •  Create scanner or camera target\n"
        "  •  Build profile with scanner or camera\n\n"
        "Everything else works normally — you can still print the chart and "
        "measure it with the SpectroScan itself.\n\n"
        "If you want to profile with a scanner or camera instead, make the "
        "chart with square / rectangular patches: in Create Chart, with the "
        "SpectroScan selected, set “Patch shape” to “Rectangular”.")


def recipe_is_hexagonal(recipe) -> bool:
    """True for a SpectroScan hexagonal-patch recipe (a ``LayoutRecipe`` or the
    dict form). ``hflag`` is the SpectroScan-only hex flag."""
    if recipe is None:
        return False
    if isinstance(recipe, dict):
        inst = recipe.get("instrument")
        hflag = recipe.get("hflag")
    else:
        inst = getattr(recipe, "instrument", None)
        hflag = getattr(recipe, "hflag", None)
    return bool(hflag) and inst == "SS"


def chart_is_hexagonal(chart_path: "str | Path | None") -> bool:
    """True when the chart at *chart_path* was made with SpectroScan hexagonal
    patches, read from its ``channels.json`` sidecar. Accepts a .ti1/.ti2/
    .channels.json path (or the chart stem). Missing/unreadable sidecar → False
    (fail open: never block a chart we can't positively identify as hex)."""
    if not chart_path:
        return False
    p = Path(chart_path)
    candidates = []
    if p.name.endswith(".channels.json"):
        candidates.append(p)
    else:
        candidates.append(p.with_suffix(".channels.json"))
        # <stem>.channels.json when the path carries a compound suffix.
        candidates.append(p.parent / (p.name.split(".")[0] + ".channels.json"))
    for cj in candidates:
        try:
            if cj.is_file():
                data = json.loads(cj.read_text())
                recipe = (data.get("layout") or {}).get("recipe")
                return recipe_is_hexagonal(recipe)
        except Exception:
            continue
    return False
