"""Discover the standard scanner-target ``.cht`` recognition files that ship
with ArgyllCMS (in ``<argyll>/ref/``), so ChromIQ can profile a scanner from a
target the user physically owns (Wolf Faust IT8, LaserSoft, ColorChecker, …)
without generating a chart (Knut #98, ask 2).

We rely on Argyll's own ``.cht`` set — Argyll is a hard dependency, the files
are validated, and bundling copies would only risk version skew. The colour
**reference** (``.cie`` / ``.txt``) is *not* here: it's specific to the user's
physical target batch and must come from the target's vendor.
"""
from __future__ import annotations

from pathlib import Path

# Friendly names for the well-known targets (filename stem → display). Anything
# not listed falls back to its stem. Ordered-priority list decides the combo
# order (most common scanner targets first).
_FRIENDLY: dict[str, str] = {
    "it8": "IT8.7/2 (generic)",
    "it8Wolf": "IT8.7/2 — Wolf Faust",
    "ISO12641_2_1": "IT8 / ISO 12641-2 (LaserSoft)",
    "ISO12641_2_3_1": "ISO 12641-2 layout 1",
    "ISO12641_2_3_2": "ISO 12641-2 layout 2",
    "ISO12641_2_3_3": "ISO 12641-2 layout 3",
    "LaserSoftDCPro": "LaserSoft DCPro",
    "ColorChecker": "X-Rite ColorChecker (24)",
    "ColorCheckerSG": "ColorChecker SG",
    "ColorCheckerDC": "ColorChecker DC",
    "ColorCheckerPassport": "ColorChecker Passport",
    "ColorCheckerHalfPassport": "ColorChecker Passport (half)",
    "SpyderChecker": "SpyderChecker",
    "SpyderChecker24": "SpyderChecker 24",
    "QPcard_201": "QPcard 201",
    "QPcard_202": "QPcard 202",
    "Hutchcolor": "HutchColor HCT",
    "i1_RGB_Scan_1.4": "i1 RGB Scan 1.4",
    "MLG": "MLG",
    "CMP_Digital_Target-4": "CMP Digital Target 4",
    "CMP_Digital_Target-7": "CMP Digital Target 7",
    "CMP_Digital_Target-2019": "CMP Digital Target 2019",
    "CMP_Digital_Target_Studio": "CMP Digital Target Studio",
    "CMP_DT_003": "CMP DT 003",
    "CMP_DT_mini": "CMP DT mini",
}

# Preferred display order — the common flatbed scanner targets on top.
_ORDER = [
    "it8Wolf", "it8", "ISO12641_2_1", "LaserSoftDCPro",
    "ColorChecker", "ColorCheckerSG", "ColorCheckerDC",
    "ColorCheckerPassport", "ColorCheckerHalfPassport",
    "SpyderChecker", "SpyderChecker24", "QPcard_201", "QPcard_202",
    "Hutchcolor", "i1_RGB_Scan_1.4",
]


def argyll_ref_dir(settings) -> Path | None:
    """The ``ref/`` folder beside the configured Argyll ``bin`` dir, or None if
    it doesn't exist."""
    bin_path = settings.get("argyll_bin_path", "")
    if not bin_path:
        return None
    ref = Path(bin_path).parent / "ref"
    return ref if ref.is_dir() else None


def display_name(cht: Path) -> str:
    return _FRIENDLY.get(cht.stem, cht.stem)


def list_standard_targets(settings) -> list[tuple[str, Path]]:
    """``(display_name, cht_path)`` for every standard target ``.cht`` Argyll
    ships, common flatbed targets first, then the rest alphabetically. Empty if
    Argyll's ``ref/`` can't be found."""
    ref = argyll_ref_dir(settings)
    if ref is None:
        return []
    by_stem = {p.stem: p for p in ref.glob("*.cht")}
    ordered: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for stem in _ORDER:
        p = by_stem.get(stem)
        if p is not None:
            ordered.append((display_name(p), p))
            seen.add(stem)
    for stem in sorted(by_stem):
        if stem not in seen:
            ordered.append((display_name(by_stem[stem]), by_stem[stem]))
    return ordered
