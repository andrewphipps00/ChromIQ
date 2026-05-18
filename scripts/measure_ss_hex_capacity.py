"""Measure per-sheet capacity for SpectroScan in HEX mode (-h) across all papers.

Same methodology as measure_ss_capacity.py but with -h added to printtarg.
For SS, -h selects hexagon-shaped patches which pack ~15% tighter than
the default square layout.

Usage:
    python scripts/measure_ss_hex_capacity.py --argyll-bin /Applications/Argyll/bin
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from data.patch_db import PAPER_SIZES  # noqa: E402

DEVICE_TYPE = "2"


def probe(targen_bin, printtarg_bin, patches, pt_args, tmpdir):
    for f in tmpdir.glob("calc*"):
        try:
            f.unlink()
        except OSError:
            pass
    tg = subprocess.run(
        [str(targen_bin), f"-d{DEVICE_TYPE}", f"-f{patches}", "calc"],
        capture_output=True, timeout=120, cwd=str(tmpdir),
    )
    if tg.returncode != 0 or not (tmpdir / "calc.ti1").exists():
        return 0
    pt = subprocess.run(
        [str(printtarg_bin)] + pt_args,
        capture_output=True, timeout=120, cwd=str(tmpdir),
    )
    if pt.returncode != 0:
        return 0
    return len(list(tmpdir.glob("calc*.tif")))


def find_max_single_sheet(targen_bin, printtarg_bin, pt_args_no_target, initial_est):
    lo = max(20, int(initial_est * 0.5))
    hi = max(lo + 50, int(initial_est * 2.5))
    best = 0
    with tempfile.TemporaryDirectory() as tmp_str:
        tmpdir = Path(tmp_str)
        while True:
            pages = probe(targen_bin, printtarg_bin, hi, pt_args_no_target + ["calc"], tmpdir)
            if pages >= 2:
                break
            if pages == 1:
                best = hi
                hi *= 2
                if hi > 100_000:
                    return best
            else:
                hi = max(lo + 50, int(hi * 0.75))
                if hi <= lo:
                    return best
        while lo <= hi:
            mid = (lo + hi) // 2
            pages = probe(targen_bin, printtarg_bin, mid, pt_args_no_target + ["calc"], tmpdir)
            if pages == 1:
                best = mid
                lo = mid + 1
            elif pages == 0:
                hi = mid - 1
            else:
                hi = mid - 1
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--argyll-bin", required=True, type=Path)
    args = ap.parse_args()

    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = args.argyll_bin / f"targen{suffix}"
    printtarg = args.argyll_bin / f"printtarg{suffix}"
    for b in (targen, printtarg):
        if not b.is_file():
            print(f"ERROR: not found: {b}", file=sys.stderr)
            return 1

    # SS hex packs ~15% tighter than square, so bump the seeds.
    initial_est = {
        "A2": 4800, "329x483": 3200, "483x329": 3200, "A3": 2400, "420x297": 2400,
        "11x17": 2400, "Legal": 1500, "A4": 1170, "A4R": 1180, "Letter": 1150,
        "LetterR": 1180, "203x254": 950, "127x178": 350, "4x6": 250,
    }

    results_lb: dict[str, int] = {}
    results_no_lb: dict[str, int] = {}
    total = len(PAPER_SIZES) * 2
    done = 0
    for paper in PAPER_SIZES:
        for suppress_lb in (True, False):
            done += 1
            pt_args = ["-iSS", "-h", f"-p{paper}", "-t300", "-m6", "-M6"]
            if suppress_lb:
                pt_args.append("-L")
            tag = "-L " if suppress_lb else "noL"
            print(f"[{done:3d}/{total}] SS-hex {paper:<8} {tag} ...", end="", flush=True)
            n = find_max_single_sheet(targen, printtarg, pt_args, initial_est.get(paper, 500))
            (results_lb if suppress_lb else results_no_lb)[paper] = n
            print(f" {n}")

    for name, d in (("with -L", results_lb), ("without -L", results_no_lb)):
        print(f"\n{name}:")
        for paper in PAPER_SIZES:
            key = f'("SS", True, "{paper}")'
            print(f"    {key:<28} {d.get(paper, 0):>5},")
    return 0


if __name__ == "__main__":
    sys.exit(main())
