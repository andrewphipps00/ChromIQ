"""Byte-golden snapshot of the RGB 3-table TI1 emitter (#72, R3 countermeasure).

``i1profiler_import.write_ti1``'s output quirks are load-bearing: printtarg
parses the 3-table structure, refinement merges match on the exact
``COLOR_REP "iRGB"`` label, and the density-extremes table doubles as
printtarg's spacer palette. Issue #72 adds a *separate* N-channel writer next
to this emitter — this snapshot guarantees the RGB emitter itself is never
edited, byte for byte.

The only run-dependent line is ``CREATED "…"`` (wall-clock timestamp); it is
masked on both sides before comparison. Everything else must match exactly.

To regenerate after an *intentional* change (should essentially never happen):
    python tests/test_ti1_emitter_golden.py --regenerate
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from workflow.i1profiler_import import RgbPatch, write_ti1

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_DEFAULT = GOLDEN_DIR / "ti1_emitter_default.ti1"
GOLDEN_CUSTOM_EXTREMES = GOLDEN_DIR / "ti1_emitter_custom_extremes.ti1"

_CREATED_RE = re.compile(r'^CREATED ".*"$', flags=re.MULTILINE)

# Deterministic patch list exercising the formatter and the white/black
# counters: pure white/black (counted), near-white/near-black just outside the
# 99.5/0.5 thresholds (not counted), primaries, a mid grey and fractional
# values that stress the "%.4f" formatting and the sRGB+flare XYZ path.
PATCHES = [
    RgbPatch(100.0, 100.0, 100.0),   # white  -> WHITE_COLOR_PATCHES
    RgbPatch(0.0, 0.0, 0.0),         # black  -> BLACK_COLOR_PATCHES
    RgbPatch(99.4, 99.6, 100.0),     # near-white, below threshold on R
    RgbPatch(0.6, 0.4, 0.0),         # near-black, above threshold on R
    RgbPatch(100.0, 0.0, 0.0),
    RgbPatch(0.0, 100.0, 0.0),
    RgbPatch(0.0, 0.0, 100.0),
    RgbPatch(50.0, 50.0, 50.0),
    RgbPatch(12.3456, 65.4321, 33.3333),
    RgbPatch(0.0001, 99.9999, 42.5),
]

CUSTOM_EXTREMES = (
    (100.0, 100.0, 100.0),           # white first (media reference)
    (12.5, 0.0, 87.5),
    (0.0, 55.5, 0.0),
    (0.0, 0.0, 0.0),                 # black last (max-density reference)
)


def _mask_created(text: str) -> str:
    return _CREATED_RE.sub('CREATED "<masked>"', text)


def _emit(tmp_path: Path) -> tuple[str, str]:
    default = write_ti1(PATCHES, tmp_path / "default.ti1")
    custom = write_ti1(
        PATCHES, tmp_path / "custom.ti1", density_extremes=CUSTOM_EXTREMES
    )
    return default.read_text(encoding="utf-8"), custom.read_text(encoding="utf-8")


def test_rgb_emitter_matches_golden_default(tmp_path):
    got, _ = _emit(tmp_path)
    want = GOLDEN_DEFAULT.read_text(encoding="utf-8")
    assert _mask_created(got) == _mask_created(want), (
        "workflow/i1profiler_import.write_ti1 output changed byte-for-byte. "
        "This emitter's quirks are load-bearing (printtarg 3-table contract, "
        "COLOR_REP 'iRGB', spacer palette) — #72's N-channel writer must be a "
        "separate function, never an edit here."
    )


def test_rgb_emitter_matches_golden_custom_extremes(tmp_path):
    _, got = _emit(tmp_path)
    want = GOLDEN_CUSTOM_EXTREMES.read_text(encoding="utf-8")
    assert _mask_created(got) == _mask_created(want)


if __name__ == "__main__" and "--regenerate" in sys.argv:
    GOLDEN_DIR.mkdir(exist_ok=True)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        default_text, custom_text = _emit(Path(td))
    GOLDEN_DEFAULT.write_text(default_text, encoding="utf-8")
    GOLDEN_CUSTOM_EXTREMES.write_text(custom_text, encoding="utf-8")
    print(f"regenerated {GOLDEN_DEFAULT} and {GOLDEN_CUSTOM_EXTREMES}")
