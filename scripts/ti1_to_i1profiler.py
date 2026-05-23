#!/usr/bin/env python3
"""CLI wrapper around workflow.i1profiler_export.

Converts an Argyll TI1 target into i1Profiler patch-set files, picking the
format from the TI1's colorspace (RGB, CMYK, or CMYK+N).

Usage:
  python scripts/ti1_to_i1profiler.py path/to/chart.ti1
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.i1profiler_export import (  # noqa: E402
    EXTRA_INK,
    _PLACEHOLDER_LAB,
    export_from_ti1,
    ink_colorspace_enum,
    parse_ti1,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    ti1_path = Path(argv[1]).expanduser().resolve()
    if not ti1_path.is_file():
        print(f"error: not a file: {ti1_path}", file=sys.stderr)
        return 1

    target = parse_ti1(ti1_path)
    txt_out, pxf_out = export_from_ti1(ti1_path)

    if target.kind == "RGB":
        cs = "RGB"
    elif target.kind == "CMYK":
        cs = "CMYK"
    else:
        cs = f"CMYK + {len(target.extras)}"
    enum, verified = ink_colorspace_enum(target.n_channels)

    print(f"COLOR_REP : {target.color_rep}")
    print(f"colorspace: {cs}  (channels {target.channels})")
    if target.extras:
        print(f"extra inks: {[EXTRA_INK[c][0] for c in target.extras]}")
        print(f"ink enum  : {enum}  ({'verified' if verified else 'EXTRAPOLATED 2*ch+1'})")
    print(f"patches   : {len(target.rows)}")
    if txt_out is not None:
        print(f"wrote     : {txt_out}")
    print(f"wrote     : {pxf_out}")

    flagged = [EXTRA_INK[c][0] for c in target.extras if c in _PLACEHOLDER_LAB]
    if flagged:
        print(f"WARNING   : placeholder Lab used for {flagged} (not from a reference)")
    if target.extras and not verified:
        print(
            "WARNING   : no i1Profiler reference ships for this colorspace; "
            "ink enum and ink Lab are best-effort and untested."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
