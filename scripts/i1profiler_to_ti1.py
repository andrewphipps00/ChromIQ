#!/usr/bin/env python3
"""CLI wrapper around workflow.i1profiler_import.

Converts an i1Profiler patch set (.pxf CxF3, or a .cgats/.txt CGATS table) into
an Argyll TI1 chart definition. RGB only.

Usage:
  python scripts/i1profiler_to_ti1.py path/to/patches.pxf [out.ti1]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.i1profiler_import import import_to_ti1  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2

    src = Path(argv[1]).expanduser().resolve()
    if not src.is_file():
        print(f"error: not a file: {src}", file=sys.stderr)
        return 1

    out = Path(argv[2]).expanduser().resolve() if len(argv) == 3 else src.with_suffix(".ti1")

    try:
        out_path, n = import_to_ti1(src, out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"patches: {n}")
    print(f"wrote  : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
