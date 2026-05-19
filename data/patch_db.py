"""Empirical per-sheet patch capacity database.

Values measured with Argyll 3.5.0 using:
    printtarg -i<instr> -p<paper> -t300 [-a<scale>] -m<margin> -M<margin> [-L] <file>

The tables cover the lookup baseline (-a 1.0, -m6/-M6, -L) plus expansions for
-m10 and -a 0.95 (i1 / p3 only). Other layout parameter values fall back to a
live binary search.

Key: (instrument_code, double_density, paper_size)
  instrument codes:
    "i1"  = i1Pro / i1Pro 2 / i1Pro 3
    "p3"  = i1Pro 3 Plus (measured with -i3p; much larger patches than i1 — ~5× fewer per sheet)
    "CM"  = ColorMunki / i1Studio / ColorChecker Studio
    "SS"  = SpectroScan (flatbed XY scanner)
  double_density: -h flag — for CM means double density (rig), for SS means hexagon patches
  paper_size: A2, 329x483, 483x329, A3, 420x297, 11x17, Legal,
              A4, A4R, Letter, LetterR, 203x254, 127x178, 4x6
"""
from __future__ import annotations

_PER_SHEET_CAPACITY: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1050,
    ("i1", False, "329x483"):   819,
    ("i1", False, "483x329"):  1218,
    ("i1", False, "A3"):        735,
    ("i1", False, "420x297"):  1050,
    ("i1", False, "11x17"):     672,
    ("i1", False, "Legal"):     504,
    ("i1", False, "A4"):        504,
    ("i1", False, "A4R"):       560,
    ("i1", False, "Letter"):    504,
    ("i1", False, "LetterR"):   512,
    ("i1", False, "203x254"):   460,
    ("i1", False, "127x178"):   169,
    ("i1", False, "4x6"):       100,
    # ---- i1Pro 3 Plus (measured with -i3p; ~5× fewer per sheet than i1) --------
    ("p3", False, "A2"):        225,
    ("p3", False, "329x483"):   171,
    ("p3", False, "483x329"):   261,
    ("p3", False, "A3"):        153,
    ("p3", False, "420x297"):   225,
    ("p3", False, "11x17"):     144,
    ("p3", False, "Legal"):     108,
    ("p3", False, "A4"):        108,
    ("p3", False, "A4R"):       119,
    ("p3", False, "Letter"):    108,
    ("p3", False, "LetterR"):   112,
    ("p3", False, "203x254"):    99,
    ("p3", False, "127x178"):    30,
    ("p3", False, "4x6"):        20,
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    ("CM", False, "A2"):        490,
    ("CM", False, "329x483"):   308,
    ("CM", False, "483x329"):   288,
    ("CM", False, "A3"):        240,
    ("CM", False, "420x297"):   210,
    ("CM", False, "11x17"):     216,
    ("CM", False, "Legal"):     133,
    ("CM", False, "A4"):         90,
    ("CM", False, "A4R"):       100,
    ("CM", False, "Letter"):     98,
    ("CM", False, "LetterR"):    90,
    ("CM", False, "203x254"):    78,
    ("CM", False, "127x178"):    21,
    ("CM", False, "4x6"):        18,
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A2"):       1015,
    ("CM", True,  "329x483"):   594,
    ("CM", True,  "483x329"):   578,
    ("CM", True,  "A3"):        460,
    ("CM", True,  "420x297"):   435,
    ("CM", True,  "11x17"):     456,
    ("CM", True,  "Legal"):     266,
    ("CM", True,  "A4"):        210,
    ("CM", True,  "A4R"):       180,
    ("CM", True,  "Letter"):    196,
    ("CM", True,  "LetterR"):   171,
    ("CM", True,  "203x254"):   156,
    ("CM", True,  "127x178"):    56,
    ("CM", True,  "4x6"):        30,
    # ---- SpectroScan (flatbed XY scanner) --------------------------
    # Re-measured via scripts/measure_ss_capacity.py against Argyll 3.5.0.
    # SS layout is independent of -L (flatbed reads individual patches,
    # not strips), so these values are identical to _PER_SHEET_CAPACITY_NO_LB.
    ("SS", False, "A2"):       4592,
    ("SS", False, "329x483"):  2838,
    ("SS", False, "483x329"):  2860,
    ("SS", False, "A3"):       2166,
    ("SS", False, "420x297"):  2184,
    ("SS", False, "11x17"):    2088,
    ("SS", False, "Legal"):    1296,
    ("SS", False, "A4"):       1014,
    ("SS", False, "A4R"):      1026,
    ("SS", False, "Letter"):    999,
    ("SS", False, "LetterR"):  1008,
    ("SS", False, "203x254"):   825,
    ("SS", False, "127x178"):   308,
    ("SS", False, "4x6"):       209,
    # ---- SpectroScan + hexagon (-h): ~14% denser packing ------------
    # Measured via scripts/measure_ss_hex_capacity.py.
    ("SS", True,  "A2"):       5264,
    ("SS", True,  "329x483"):  3268,
    ("SS", True,  "483x329"):  3250,
    ("SS", True,  "A3"):       2470,
    ("SS", True,  "420x297"):  2520,
    ("SS", True,  "11x17"):    2345,
    ("SS", True,  "Legal"):    1430,
    ("SS", True,  "A4"):       1170,
    ("SS", True,  "A4R"):      1178,
    ("SS", True,  "Letter"):   1092,
    ("SS", True,  "LetterR"):  1120,
    ("SS", True,  "203x254"):   950,
    ("SS", True,  "127x178"):   350,
    ("SS", True,  "4x6"):       210,
}

# Baseline printtarg layout parameters these values were measured with
LOOKUP_BASELINE: dict[str, object] = {
    "-a": 1.0,
    "-L": True,
    "-m": 6,
    "-M": 6,
}

# Per-instrument default margin in mm. The i1Pro family's strip optics drift
# onto the bare paper edge when the last patch sits ~6 mm from it, ending the
# strip early ("not enough patches read"). Bumping to 10 mm reclaims the
# headroom. CM/SS read individual patches and aren't affected.
INSTRUMENT_DEFAULT_MARGIN: dict[str, int] = {
    "i1": 10,
    "p3": 10,
    "CM": 6,
    "SS": 6,
}

# Margins for which we have measured per-sheet capacity tables. Other margin
# values fall back to live binary search via workflow/chart_creator._binary_search.
SUPPORTED_MARGINS: tuple[int, ...] = (6, 10)

# Patch scales (-a flag) for which we have measured per-sheet capacity tables.
# Other scales fall back to live binary search.
SUPPORTED_PATCH_SCALES: tuple[float, ...] = (1.0, 0.95)

# i1Pro chart-layout presets exposed in the Preferences dialog. The setting
# `i1pro_default_preset` stores one of these keys; the tuple is (margin_mm,
# patch_scale). Applied only when the active instrument is "i1" — other
# instruments keep their existing defaults.
I1PRO_DEFAULT_PRESETS: dict[str, tuple[int, float]] = {
    "m6_a1.0":   (6,  1.0),
    "m10_a1.0":  (10, 1.0),
    "m10_a0.95": (10, 0.95),
}

I1PRO_PRESET_LABELS: dict[str, str] = {
    "m6_a1.0":   "−m 6  −a 1.0  (legacy)",
    "m10_a1.0":  "−m 10  −a 1.0",
    "m10_a0.95": "−m 10  −a 0.95  (default — denser packing)",
}

I1PRO_DEFAULT_PRESET_KEY: str = "m10_a0.95"


def i1_defaults_from_preset(preset_key: str) -> tuple[int, float]:
    """Return (margin_mm, patch_scale) for the given preset key.

    Unknown keys fall back to the recommended default (m10_a0.95).
    """
    return I1PRO_DEFAULT_PRESETS.get(preset_key, I1PRO_DEFAULT_PRESETS[I1PRO_DEFAULT_PRESET_KEY])

# Human-readable labels for UI combos
INSTRUMENT_LABELS: dict[str, str] = {
    "i1": "i1Pro / i1Pro 2 / i1Pro 3",
    "p3": "i1Pro 3 Plus",
    "CM": "ColorMunki / i1Studio / ColorChecker Studio",
    "SS": "SpectroScan",
}

PAPER_SIZES: list[str] = [
    "A2",
    "329x483", "483x329",
    "A3", "420x297",
    "11x17", "Legal",
    "A4", "A4R",
    "Letter", "LetterR",
    "203x254", "127x178", "4x6",
]

# Paper sizes hidden in guided mode for specific instruments.
# A3 portrait excluded for i1/p3: landscape variant (420x297) has ~43% more capacity.
# Smallest photo formats excluded for p3: patch counts too low for a usable profile.
EXCLUDED_PAPERS: dict[str, set[str]] = {
    "i1": {"A3"},
    "p3": {"A3", "329x483", "127x178", "4x6"},
}

# When the selected paper becomes excluded on instrument switch, use this fallback.
PAPER_FALLBACK: dict[str, str] = {
    "A3":     "420x297",
    "127x178": "A4",
    "4x6":    "A4",
}

PAPER_LABELS: dict[str, str] = {
    "A2":      "A2 (420 × 594 mm)",
    "329x483": "A3+ (329 × 483 mm) Portrait",
    "483x329": "A3+ (483 × 329 mm) Landscape",
    "A3":      "A3 (297 × 420 mm) Portrait",
    "420x297": "A3 (420 × 297 mm) Landscape",
    "11x17":   "Tabloid / 11 × 17\"",
    "Legal":   "Legal (8.5 × 14\")",
    "A4":      "A4 (210 × 297 mm) Portrait",
    "A4R":     "A4 (297 × 210 mm) Landscape",
    "Letter":  "Letter (8.5 × 11\") Portrait",
    "LetterR": "Letter (11 × 8.5\") Landscape",
    "203x254": "8×10\" (203 × 254 mm)",
    "127x178": "5×7\" (127 × 178 mm)",
    "4x6":     "4×6\" (102 × 152 mm)",
}

# printtarg -p argument for each paper key
PAPER_PRINTTARG_ARG: dict[str, str] = {
    "A2":      "A2",
    "329x483": "329x483",
    "483x329": "483x329",
    "A3":      "A3",
    "420x297": "420x297",
    "11x17":   "11x17",
    "Legal":   "Legal",
    "A4":      "A4",
    "A4R":     "A4R",
    "Letter":  "Letter",
    "LetterR": "LetterR",
    "203x254": "203x254",
    "127x178": "127x178",
    "4x6":     "4x6",
}


# Per-sheet capacity WITHOUT the -L flag (left clip border present).
# Measured with: printtarg -i<instr> -p<paper> -t300 <file>  (no -L)
# For CM the values are identical to the -L baseline (-L has no effect on CM layout).
# For SS (flatbed) the no-LB values are the same as with-L for these paper sizes.
_PER_SHEET_CAPACITY_NO_LB: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):        986,
    ("i1", False, "329x483"):   756,
    ("i1", False, "483x329"):  1155,
    ("i1", False, "A3"):        670,
    ("i1", False, "420x297"):   986,
    ("i1", False, "11x17"):     628,
    ("i1", False, "Legal"):     460,
    ("i1", False, "A4"):        441,
    ("i1", False, "A4R"):       506,
    ("i1", False, "Letter"):    460,
    ("i1", False, "LetterR"):   476,
    ("i1", False, "203x254"):   400,
    ("i1", False, "127x178"):   143,
    ("i1", False, "4x6"):        80,
    # ---- i1Pro 3 Plus (measured with -i3p; ~5× fewer per sheet than i1) --------
    ("p3", False, "A2"):        207,
    ("p3", False, "329x483"):   162,
    ("p3", False, "483x329"):   243,
    ("p3", False, "A3"):        144,
    ("p3", False, "420x297"):   207,
    ("p3", False, "11x17"):     135,
    ("p3", False, "Legal"):      99,
    ("p3", False, "A4"):         90,
    ("p3", False, "A4R"):       112,
    ("p3", False, "Letter"):     99,
    ("p3", False, "LetterR"):   105,
    ("p3", False, "203x254"):    90,
    ("p3", False, "127x178"):    25,
    ("p3", False, "4x6"):        16,
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    # -L has no effect on CM layout; values identical to with-L baseline
    ("CM", False, "A2"):        490,
    ("CM", False, "329x483"):   308,
    ("CM", False, "483x329"):   288,
    ("CM", False, "A3"):        240,
    ("CM", False, "420x297"):   210,
    ("CM", False, "11x17"):     216,
    ("CM", False, "Legal"):     133,
    ("CM", False, "A4"):         90,
    ("CM", False, "A4R"):       100,
    ("CM", False, "Letter"):     98,
    ("CM", False, "LetterR"):    90,
    ("CM", False, "203x254"):    78,
    ("CM", False, "127x178"):    21,
    ("CM", False, "4x6"):        18,
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A2"):       1015,
    ("CM", True,  "329x483"):   594,
    ("CM", True,  "483x329"):   578,
    ("CM", True,  "A3"):        460,
    ("CM", True,  "420x297"):   435,
    ("CM", True,  "11x17"):     456,
    ("CM", True,  "Legal"):     266,
    ("CM", True,  "A4"):        210,
    ("CM", True,  "A4R"):       180,
    ("CM", True,  "Letter"):    196,
    ("CM", True,  "LetterR"):   171,
    ("CM", True,  "203x254"):   156,
    ("CM", True,  "127x178"):    56,
    ("CM", True,  "4x6"):        30,
    # ---- SpectroScan (flatbed XY scanner) --------------------------
    # Re-measured via scripts/measure_ss_capacity.py against Argyll 3.5.0.
    # -L has no effect on SS layout — values identical to the with-L table.
    ("SS", False, "A2"):       4592,
    ("SS", False, "329x483"):  2838,
    ("SS", False, "483x329"):  2860,
    ("SS", False, "A3"):       2166,
    ("SS", False, "420x297"):  2184,
    ("SS", False, "11x17"):    2088,
    ("SS", False, "Legal"):    1296,
    ("SS", False, "A4"):       1014,
    ("SS", False, "A4R"):      1026,
    ("SS", False, "Letter"):    999,
    ("SS", False, "LetterR"):  1008,
    ("SS", False, "203x254"):   825,
    ("SS", False, "127x178"):   308,
    ("SS", False, "4x6"):       209,
    # ---- SpectroScan + hexagon (-h): -L is still a no-op on SS ------
    ("SS", True,  "A2"):       5264,
    ("SS", True,  "329x483"):  3268,
    ("SS", True,  "483x329"):  3250,
    ("SS", True,  "A3"):       2470,
    ("SS", True,  "420x297"):  2520,
    ("SS", True,  "11x17"):    2345,
    ("SS", True,  "Legal"):    1430,
    ("SS", True,  "A4"):       1170,
    ("SS", True,  "A4R"):      1178,
    ("SS", True,  "Letter"):   1092,
    ("SS", True,  "LetterR"):  1120,
    ("SS", True,  "203x254"):   950,
    ("SS", True,  "127x178"):   350,
    ("SS", True,  "4x6"):       210,
}


# ---------------------------------------------------------------------------
# Per-sheet capacity at margin = 10 mm (-m10 -M10), for i1 / p3 only.
# CM and SS keep their margin=6 tables (their default margin doesn't change).
# Measured by scripts/measure_margin10_capacity.py.
# ---------------------------------------------------------------------------

# Measured values (scripts/measure_margin10_capacity.py against Argyll 3.5.0).
# 127x178 and 4x6 are omitted on i1 and p3: margin=10 leaves no room for a
# single instrument strip on those paper sizes. query_patches returns None
# for those combos, and the chart workflow falls back to live binary search.
_PER_SHEET_CAPACITY_M10: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1029,
    ("i1", False, "329x483"):   798,
    ("i1", False, "483x329"):  1197,
    ("i1", False, "A3"):        714,
    ("i1", False, "420x297"):  1029,
    ("i1", False, "11x17"):     651,
    ("i1", False, "Legal"):     483,
    ("i1", False, "A4"):        483,
    ("i1", False, "A4R"):       510,
    ("i1", False, "Letter"):    483,
    ("i1", False, "LetterR"):   496,
    ("i1", False, "203x254"):   418,
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        216,
    ("p3", False, "329x483"):   171,
    ("p3", False, "483x329"):   252,
    ("p3", False, "A3"):        153,
    ("p3", False, "420x297"):   216,
    ("p3", False, "11x17"):     135,
    ("p3", False, "Legal"):      99,
    ("p3", False, "A4"):         99,
    ("p3", False, "A4R"):       102,
    ("p3", False, "Letter"):     99,
    ("p3", False, "LetterR"):   105,
    ("p3", False, "203x254"):    88,
}

_PER_SHEET_CAPACITY_M10_NO_LB: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):        987,
    ("i1", False, "329x483"):   756,
    ("i1", False, "483x329"):  1155,
    ("i1", False, "A3"):        672,
    ("i1", False, "420x297"):   987,
    ("i1", False, "11x17"):     609,
    ("i1", False, "Legal"):     441,
    ("i1", False, "A4"):        441,
    ("i1", False, "A4R"):       480,
    ("i1", False, "Letter"):    441,
    ("i1", False, "LetterR"):   464,
    ("i1", False, "203x254"):   380,
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        207,
    ("p3", False, "329x483"):   162,
    ("p3", False, "483x329"):   243,
    ("p3", False, "A3"):        144,
    ("p3", False, "420x297"):   207,
    ("p3", False, "11x17"):     126,
    ("p3", False, "Legal"):      90,
    ("p3", False, "A4"):         90,
    ("p3", False, "A4R"):        96,
    ("p3", False, "Letter"):     90,
    ("p3", False, "LetterR"):    98,
    ("p3", False, "203x254"):    80,
}


# ---------------------------------------------------------------------------
# Per-sheet capacity at patch-scale -a 0.95.
# i1 / p3 measured by scripts/measure_scale095_capacity.py (with m=6 and m=10).
# CM (both -h states) measured by scripts/measure_scale095_cm_capacity.py at m=6.
# SS isn't covered — falls back to live binary search at -a 0.95.
# ---------------------------------------------------------------------------

_PER_SHEET_CAPACITY_A095: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1166,
    ("i1", False, "329x483"):   902,
    ("i1", False, "483x329"):  1342,
    ("i1", False, "A3"):        792,
    ("i1", False, "420x297"):  1166,
    ("i1", False, "11x17"):     748,
    ("i1", False, "Legal"):     572,
    ("i1", False, "A4"):        550,
    ("i1", False, "A4R"):       576,
    ("i1", False, "Letter"):    572,
    ("i1", False, "LetterR"):   578,
    ("i1", False, "203x254"):   504,
    ("i1", False, "127x178"):   182,
    ("i1", False, "4x6"):       121,
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        260,
    ("p3", False, "329x483"):   200,
    ("p3", False, "483x329"):   300,
    ("p3", False, "A3"):        180,
    ("p3", False, "420x297"):   260,
    ("p3", False, "11x17"):     170,
    ("p3", False, "Legal"):     130,
    ("p3", False, "A4"):        120,
    ("p3", False, "A4R"):       126,
    ("p3", False, "Letter"):    130,
    ("p3", False, "LetterR"):   119,
    ("p3", False, "203x254"):   108,
    ("p3", False, "127x178"):    35,
    ("p3", False, "4x6"):        20,
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    # -L has no effect on CM layout; values identical to no-LB table.
    ("CM", False, "A2"):        555,
    ("CM", False, "329x483"):   319,
    ("CM", False, "483x329"):   306,
    ("CM", False, "A3"):        250,
    ("CM", False, "420x297"):   240,
    ("CM", False, "11x17"):     234,
    ("CM", False, "Legal"):     140,
    ("CM", False, "A4"):        112,
    ("CM", False, "A4R"):       100,
    ("CM", False, "Letter"):    105,
    ("CM", False, "LetterR"):    99,
    ("CM", False, "203x254"):    78,
    ("CM", False, "127x178"):    32,
    # ("CM", False, "4x6") omitted: infeasible at -a 0.95
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A2"):       1110,
    ("CM", True,  "329x483"):   667,
    ("CM", True,  "483x329"):   630,
    ("CM", True,  "A3"):        504,
    ("CM", True,  "420x297"):   480,
    ("CM", True,  "11x17"):     500,
    ("CM", True,  "Legal"):     300,
    ("CM", True,  "A4"):        224,
    ("CM", True,  "A4R"):       210,
    ("CM", True,  "Letter"):    225,
    ("CM", True,  "LetterR"):   200,
    ("CM", True,  "203x254"):   182,
    ("CM", True,  "127x178"):    56,
    ("CM", True,  "4x6"):        36,
}

_PER_SHEET_CAPACITY_A095_NO_LB: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1100,
    ("i1", False, "329x483"):   836,
    ("i1", False, "483x329"):  1276,
    ("i1", False, "A3"):        748,
    ("i1", False, "420x297"):  1100,
    ("i1", False, "11x17"):     682,
    ("i1", False, "Legal"):     506,
    ("i1", False, "A4"):        484,
    ("i1", False, "A4R"):       544,
    ("i1", False, "Letter"):    506,
    ("i1", False, "LetterR"):   527,
    ("i1", False, "203x254"):   441,
    ("i1", False, "127x178"):   143,
    ("i1", False, "4x6"):        88,
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        250,
    ("p3", False, "329x483"):   190,
    ("p3", False, "483x329"):   290,
    ("p3", False, "A3"):        170,
    ("p3", False, "420x297"):   250,
    ("p3", False, "11x17"):     150,
    ("p3", False, "Legal"):     110,
    ("p3", False, "A4"):        110,
    ("p3", False, "A4R"):       119,
    ("p3", False, "Letter"):    110,
    ("p3", False, "LetterR"):   105,
    ("p3", False, "203x254"):    90,
    ("p3", False, "127x178"):    25,
    # ("p3", False, "4x6") omitted: infeasible at -a 0.95 without -L
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    # -L has no effect on CM layout; values identical to with-L table.
    ("CM", False, "A2"):        555,
    ("CM", False, "329x483"):   319,
    ("CM", False, "483x329"):   306,
    ("CM", False, "A3"):        250,
    ("CM", False, "420x297"):   240,
    ("CM", False, "11x17"):     234,
    ("CM", False, "Legal"):     140,
    ("CM", False, "A4"):        112,
    ("CM", False, "A4R"):       100,
    ("CM", False, "Letter"):    105,
    ("CM", False, "LetterR"):    99,
    ("CM", False, "203x254"):    78,
    ("CM", False, "127x178"):    32,
    # ("CM", False, "4x6") omitted: infeasible at -a 0.95
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A2"):       1110,
    ("CM", True,  "329x483"):   667,
    ("CM", True,  "483x329"):   630,
    ("CM", True,  "A3"):        504,
    ("CM", True,  "420x297"):   480,
    ("CM", True,  "11x17"):     500,
    ("CM", True,  "Legal"):     300,
    ("CM", True,  "A4"):        224,
    ("CM", True,  "A4R"):       210,
    ("CM", True,  "Letter"):    225,
    ("CM", True,  "LetterR"):   200,
    ("CM", True,  "203x254"):   182,
    ("CM", True,  "127x178"):    56,
    ("CM", True,  "4x6"):        36,
}

_PER_SHEET_CAPACITY_A095_M10: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1122,
    ("i1", False, "329x483"):   880,
    ("i1", False, "483x329"):  1320,
    ("i1", False, "A3"):        770,
    ("i1", False, "420x297"):  1122,
    ("i1", False, "11x17"):     726,
    ("i1", False, "Legal"):     550,
    ("i1", False, "A4"):        528,
    ("i1", False, "A4R"):       560,
    ("i1", False, "Letter"):    550,
    ("i1", False, "LetterR"):   561,
    ("i1", False, "203x254"):   460,
    # 127x178 / 4x6 omitted: infeasible at margin=10
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        250,
    ("p3", False, "329x483"):   200,
    ("p3", False, "483x329"):   300,
    ("p3", False, "A3"):        170,
    ("p3", False, "420x297"):   250,
    ("p3", False, "11x17"):     160,
    ("p3", False, "Legal"):     120,
    ("p3", False, "A4"):        120,
    ("p3", False, "A4R"):       119,
    ("p3", False, "Letter"):    120,
    ("p3", False, "LetterR"):   112,
    ("p3", False, "203x254"):    99,
    # 127x178 / 4x6 omitted: infeasible at margin=10
}

_PER_SHEET_CAPACITY_A095_M10_NO_LB: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A2"):       1078,
    ("i1", False, "329x483"):   814,
    ("i1", False, "483x329"):  1276,
    ("i1", False, "A3"):        726,
    ("i1", False, "420x297"):  1078,
    ("i1", False, "11x17"):     682,
    ("i1", False, "Legal"):     506,
    ("i1", False, "A4"):        484,
    ("i1", False, "A4R"):       528,
    ("i1", False, "Letter"):    506,
    ("i1", False, "LetterR"):   527,
    ("i1", False, "203x254"):   420,
    # 127x178 / 4x6 omitted: infeasible at margin=10
    # ---- i1Pro 3 Plus ---------------------------------------------
    ("p3", False, "A2"):        240,
    ("p3", False, "329x483"):   180,
    ("p3", False, "483x329"):   290,
    ("p3", False, "A3"):        160,
    ("p3", False, "420x297"):   240,
    ("p3", False, "11x17"):     150,
    ("p3", False, "Legal"):     110,
    ("p3", False, "A4"):        110,
    ("p3", False, "A4R"):       112,
    ("p3", False, "Letter"):    110,
    ("p3", False, "LetterR"):   105,
    ("p3", False, "203x254"):    90,
    # 127x178 / 4x6 omitted: infeasible at margin=10
}


def query_patches(
    instrument: str,
    paper: str,
    double_density: bool = False,
    suppress_lb: bool = True,
    margin_mm: int = 6,
    patch_scale: float = 1.0,
) -> int | None:
    """Return patches-per-sheet for the given combination, or None if unknown.

    suppress_lb=True  → values measured with -L (left-clip border suppressed, default).
    suppress_lb=False → values measured without -L (left-clip border present).
    margin_mm         → must be one of SUPPORTED_MARGINS, else returns None.
    patch_scale       → must be one of SUPPORTED_PATCH_SCALES, else returns None.
    Callers fall back to a live binary search on a None result.
    """
    # Normalise: -h is meaningful on CM (double density via rig) and on
    # SS (hexagon patches). Strip instruments (i1, p3) never use it.
    dd = double_density if instrument in {"CM", "SS"} else False

    if abs(patch_scale - 1.0) <= 0.01:
        if margin_mm == 6:
            db = _PER_SHEET_CAPACITY if suppress_lb else _PER_SHEET_CAPACITY_NO_LB
        elif margin_mm == 10:
            db = _PER_SHEET_CAPACITY_M10 if suppress_lb else _PER_SHEET_CAPACITY_M10_NO_LB
        else:
            return None
    elif abs(patch_scale - 0.95) <= 0.01:
        if margin_mm == 6:
            db = _PER_SHEET_CAPACITY_A095 if suppress_lb else _PER_SHEET_CAPACITY_A095_NO_LB
        elif margin_mm == 10:
            db = _PER_SHEET_CAPACITY_A095_M10 if suppress_lb else _PER_SHEET_CAPACITY_A095_M10_NO_LB
        else:
            return None
    else:
        return None

    return db.get((instrument, dd, paper))


def uses_default_layout(pt_params: dict) -> bool:
    """Return True when all printtarg layout params match the lookup baseline."""
    for key, default_val in LOOKUP_BASELINE.items():
        if pt_params.get(key, default_val) != default_val:
            return False
    return True
