"""Shared strip-letter utilities used by measurement and profcheck modules."""
from __future__ import annotations


def letter_to_idx(letter: str) -> int:
    """Convert a strip letter to a 0-based sort index.

    A=0, B=1, … Z=25, AA=26, AB=27, … AZ=51, BA=52, …
    """
    idx = 0
    for c in letter.upper():
        idx = idx * 26 + (ord(c) - ord("A") + 1)
    return idx - 1
