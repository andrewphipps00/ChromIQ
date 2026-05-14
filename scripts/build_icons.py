"""Generate platform icons from ``assets/app_icon.png``.

Cross-platform: uses Pillow only — no ``sips``, ``iconutil``, ImageMagick
or ``icnsutil`` required.  Pillow's ICNS writer is pure-Python and emits
all the entries macOS needs (ic07/ic08/ic09/ic10/ic11/ic12/ic13/ic14)
from a single source PNG, so the same script works on macOS, Linux and
Windows.

Outputs:
    assets/app_icon.icns   (macOS bundle icon)
    assets/app_icon.ico    (Windows EXE icon)

The Linux PyInstaller spec uses the PNG directly, so this script is
optional on Linux — but running it costs nothing and verifies the
toolchain works there too.

Usage:
    python scripts/build_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "app_icon.png"
OUT_ICNS = ROOT / "assets" / "app_icon.icns"
OUT_ICO = ROOT / "assets" / "app_icon.ico"


def main() -> int:
    if not SRC.exists():
        print(f"error: source icon not found at {SRC}", file=sys.stderr)
        return 1

    img = Image.open(SRC).convert("RGBA")

    img.save(OUT_ICNS, format="ICNS")
    print(f"wrote {OUT_ICNS}")

    img.save(
        OUT_ICO,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"wrote {OUT_ICO}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
