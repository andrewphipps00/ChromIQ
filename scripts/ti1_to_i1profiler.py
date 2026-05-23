#!/usr/bin/env python3
"""CLI wrapper around workflow.i1profiler_export.

Usage:
  python scripts/ti1_to_i1profiler.py path/to/chart.ti1
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.i1profiler_export import export_from_ti1  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    ti1_path = Path(argv[1]).expanduser().resolve()
    if not ti1_path.is_file():
        print(f"error: not a file: {ti1_path}", file=sys.stderr)
        return 1

    txt_out, pxf_out = export_from_ti1(ti1_path)
    print(f"wrote {txt_out}")
    print(f"wrote {pxf_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
