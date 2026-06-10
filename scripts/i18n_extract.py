#!/usr/bin/env python3
"""Extract tr() source strings and check catalog completeness.

Usage:
    python scripts/i18n_extract.py              # list all keys to stdout
    python scripts/i18n_extract.py --missing de # keys absent from de.json
    python scripts/i18n_extract.py --stale de   # de.json keys no longer in code
    python scripts/i18n_extract.py --stats de   # one-line coverage summary

The catalog key is the exact English source string passed to tr(), so
this only finds literal (incl. implicitly concatenated) arguments —
which is the project convention enforced by scripts/i18n_wrap.py.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("ui", "workflow", "core", "main.py")


def extract_keys() -> set[str]:
    keys: set[str] = set()
    files: list[Path] = []
    for entry in SCAN_DIRS:
        p = ROOT / entry
        if p.is_file():
            files.append(p)
        else:
            files.extend(sorted(p.rglob("*.py")))
    for f in files:
        if "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "tr"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def load_catalog(code: str) -> dict[str, str]:
    path = ROOT / "data" / "i18n" / f"{code}.json"
    with open(path, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("@")}


def main() -> int:
    args = sys.argv[1:]
    keys = extract_keys()
    if not args:
        for k in sorted(keys):
            print(json.dumps(k, ensure_ascii=False))
        print(f"# {len(keys)} keys", file=sys.stderr)
        return 0
    mode, code = args[0], args[1]
    catalog = load_catalog(code)
    if mode == "--missing":
        missing = sorted(keys - set(catalog))
        for k in missing:
            print(json.dumps(k, ensure_ascii=False))
        print(f"# {len(missing)} missing of {len(keys)}", file=sys.stderr)
        return 1 if missing else 0
    if mode == "--stale":
        stale = sorted(set(catalog) - keys)
        for k in stale:
            print(json.dumps(k, ensure_ascii=False))
        print(f"# {len(stale)} stale", file=sys.stderr)
        return 0
    if mode == "--stats":
        done = len(keys & set(catalog))
        print(f"{code}: {done}/{len(keys)} translated "
              f"({100 * done / max(1, len(keys)):.1f}%), "
              f"{len(set(catalog) - keys)} stale")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
