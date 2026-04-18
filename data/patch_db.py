"""Empirical per-sheet patch capacity database.

Values measured with Argyll 3.1.0 / 3.5.0 using:
    printtarg -i<instr> -p<paper> -t300 -L <file>
i.e. default patch scale (-a 1.0), 300 DPI TIFF, left-clip border suppressed.

Key: (instrument_code, double_density, paper_size)
  instrument codes:
    "i1"  = i1Pro / i1Pro 2 / i1Pro 3
    "p3"  = i1Pro 3 Plus (same strip geometry as i1 — use i1 values)
    "CM"  = ColorMunki / i1Studio / ColorChecker Studio
    "SS"  = SpectroScan (flatbed XY scanner)
  double_density: True only meaningful for CM (-h flag)
  paper_size: A4, A4R, A3, 420x297, A2, Letter, LetterR, Legal, 11x17
"""
from __future__ import annotations

_PER_SHEET_CAPACITY: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A4"):       504,
    ("i1", False, "A4R"):      560,
    ("i1", False, "A3"):       735,
    ("i1", False, "420x297"):  1050,
    ("i1", False, "A2"):       1050,
    ("i1", False, "Letter"):   504,
    ("i1", False, "LetterR"):  512,
    ("i1", False, "Legal"):    504,
    ("i1", False, "11x17"):    672,
    # ---- i1Pro 3 Plus (same geometry as i1) ------------------------
    ("p3", False, "A4"):       504,
    ("p3", False, "A4R"):      560,
    ("p3", False, "A3"):       735,
    ("p3", False, "420x297"):  1050,
    ("p3", False, "A2"):       1050,
    ("p3", False, "Letter"):   504,
    ("p3", False, "LetterR"):  512,
    ("p3", False, "Legal"):    504,
    ("p3", False, "11x17"):    672,
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    ("CM", False, "A4"):       90,
    ("CM", False, "A4R"):      100,
    ("CM", False, "A3"):       240,
    ("CM", False, "420x297"):  210,
    ("CM", False, "A2"):       490,
    ("CM", False, "Letter"):   98,
    ("CM", False, "LetterR"):  90,
    ("CM", False, "Legal"):    133,
    ("CM", False, "11x17"):    216,
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A4"):       210,
    ("CM", True,  "A4R"):      180,
    ("CM", True,  "A3"):       460,
    ("CM", True,  "420x297"):  435,
    ("CM", True,  "A2"):       1015,
    ("CM", True,  "Letter"):   196,
    ("CM", True,  "LetterR"):  171,
    ("CM", True,  "Legal"):    266,
    ("CM", True,  "11x17"):    456,
    # ---- SpectroScan (flatbed XY scanner) --------------------------
    ("SS", False, "A4"):       1014,
    ("SS", False, "A4R"):      1026,
    ("SS", False, "A3"):       2166,
    ("SS", False, "420x297"):  2184,
    ("SS", False, "A2"):       4000,
    ("SS", False, "Letter"):   999,
    ("SS", False, "LetterR"):  1008,
    ("SS", False, "Legal"):    1296,
    ("SS", False, "11x17"):    2088,
}

# Baseline printtarg layout parameters these values were measured with
LOOKUP_BASELINE: dict[str, object] = {
    "-a": 1.0,
    "-L": True,
    "-m": 6,
    "-M": 6,
}

# Human-readable labels for UI combos
INSTRUMENT_LABELS: dict[str, str] = {
    "i1": "i1Pro / i1Pro 2 / i1Pro 3",
    "p3": "i1Pro 3 Plus",
    "CM": "ColorMunki / i1Studio / ColorChecker Studio",
    "SS": "SpectroScan",
}

PAPER_SIZES: list[str] = [
    "A4", "A4R", "A3", "420x297", "A2",
    "Letter", "LetterR", "Legal", "11x17",
]

PAPER_LABELS: dict[str, str] = {
    "A4":      "A4 (210 × 297 mm) Portrait",
    "A4R":     "A4 (297 × 210 mm) Landscape",
    "A3":      "A3 (297 × 420 mm) Portrait",
    "420x297": "A3 (420 × 297 mm) Landscape",
    "A2":      "A2 (420 × 594 mm)",
    "Letter":  "Letter (8.5 × 11\") Portrait",
    "LetterR": "Letter (11 × 8.5\") Landscape",
    "Legal":   "Legal (8.5 × 14\")",
    "11x17":   "Tabloid / 11 × 17\"",
}

# printtarg -p argument for each paper key
PAPER_PRINTTARG_ARG: dict[str, str] = {
    "A4":      "A4",
    "A4R":     "A4R",
    "A3":      "A3",
    "420x297": "420x297",
    "A2":      "A2",
    "Letter":  "Letter",
    "LetterR": "LetterR",
    "Legal":   "Legal",
    "11x17":   "11x17",
}


# Per-sheet capacity WITHOUT the -L flag (left clip border present).
# Measured with: printtarg -i<instr> -p<paper> -t300 <file>  (no -L)
# For CM the values are nearly identical to the -L baseline (strips are narrow).
# For SS (flatbed) the no-LB values are slightly higher than the -L baseline
# because -L restricts the flatbed layout area differently.
_PER_SHEET_CAPACITY_NO_LB: dict[tuple[str, bool, str], int] = {
    # ---- i1Pro / i1Pro 2 / i1Pro 3 --------------------------------
    ("i1", False, "A4"):       441,
    ("i1", False, "A4R"):      506,
    ("i1", False, "A3"):       670,
    ("i1", False, "420x297"):  986,
    ("i1", False, "A2"):       986,
    ("i1", False, "Letter"):   460,
    ("i1", False, "LetterR"):  476,
    ("i1", False, "Legal"):    460,
    ("i1", False, "11x17"):    628,
    # ---- i1Pro 3 Plus (same geometry as i1) ------------------------
    ("p3", False, "A4"):       441,
    ("p3", False, "A4R"):      506,
    ("p3", False, "A3"):       670,
    ("p3", False, "420x297"):  986,
    ("p3", False, "A2"):       986,
    ("p3", False, "Letter"):   460,
    ("p3", False, "LetterR"):  476,
    ("p3", False, "Legal"):    460,
    ("p3", False, "11x17"):    628,
    # ---- ColorMunki / i1Studio / ColorChecker Studio — standard ----
    ("CM", False, "A4"):       90,
    ("CM", False, "A4R"):      98,
    ("CM", False, "A3"):       239,
    ("CM", False, "420x297"):  206,
    ("CM", False, "A2"):       488,
    ("CM", False, "Letter"):   98,
    ("CM", False, "LetterR"):  90,
    ("CM", False, "Legal"):    132,
    ("CM", False, "11x17"):    216,
    # ---- ColorMunki + rig / double density (-h) --------------------
    ("CM", True,  "A4"):       206,
    ("CM", True,  "A4R"):      177,
    ("CM", True,  "A3"):       459,
    ("CM", True,  "420x297"):  435,
    ("CM", True,  "A2"):       1015,
    ("CM", True,  "Letter"):   192,
    ("CM", True,  "LetterR"):  167,
    ("CM", True,  "Legal"):    263,
    ("CM", True,  "11x17"):    454,
    # ---- SpectroScan (flatbed XY scanner) --------------------------
    ("SS", False, "A4"):       1010,
    ("SS", False, "A4R"):      1025,
    ("SS", False, "A3"):       2164,
    ("SS", False, "420x297"):  2184,
    ("SS", False, "A2"):       4588,
    ("SS", False, "Letter"):   996,
    ("SS", False, "LetterR"):  1006,
    ("SS", False, "Legal"):    1294,
    ("SS", False, "11x17"):    2088,
}


def query_patches(
    instrument: str,
    paper: str,
    double_density: bool = False,
    suppress_lb: bool = True,
) -> int | None:
    """Return patches-per-sheet for the given combination, or None if unknown.

    suppress_lb=True  → values measured with -L (left-clip border suppressed, default).
    suppress_lb=False → values measured without -L (left-clip border present).
    """
    # Normalise: SS never has double_density
    dd = double_density if instrument == "CM" else False
    db = _PER_SHEET_CAPACITY if suppress_lb else _PER_SHEET_CAPACITY_NO_LB
    return db.get((instrument, dd, paper))


def uses_default_layout(pt_params: dict) -> bool:
    """Return True when all printtarg layout params match the lookup baseline."""
    for key, default_val in LOOKUP_BASELINE.items():
        if pt_params.get(key, default_val) != default_val:
            return False
    return True
