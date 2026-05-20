"""Measure per-sheet patch capacity for triple-density ColorMunki mode.

Triple-density emulates the i1Pro strip layout for a ColorMunki + rig:
the chart is generated with printtarg -ii1 plus tuned -a 1.3 / -m 5 / -M 5 / -P,
yielding much denser packing than a native CM chart. The ChromIQ workflow
also rewrites the produced .ti2 so chartread still opens the ColorMunki —
that step doesn't affect patch count and is skipped here.

This script binary-searches the largest patch count that still fits on a
single sheet, for every paper size, in both -L states. The output is two
ready-to-paste dict literals for data/patch_db.py.

Usage:
    python scripts/measure_triple_density_capacity.py --argyll-bin <path-to-bin>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from data.patch_db import PAPER_SIZES, _PER_SHEET_CAPACITY  # noqa: E402

DEVICE_TYPE = "2"  # RGB
PATCH_SCALE = 1.3
MARGIN = 5
# i1 layout at scale 1.3 packs roughly 1/1.3² = ~0.59× the i1 baseline of
# its own layout — but vs CM native, it's much denser. Seed conservatively
# from the existing CM-with-rig table, scaled by ~1.5× to bracket above.
SCALE_BOOST = 1.5


def probe(targen_bin, printtarg_bin, patches, pt_args, tmpdir):
    tg = subprocess.run(
        [str(targen_bin), f"-d{DEVICE_TYPE}", f"-f{patches}", "calc"],
        capture_output=True, timeout=60, cwd=str(tmpdir),
    )
    if tg.returncode != 0 or not (tmpdir / "calc.ti1").exists():
        return 0
    pt = subprocess.run(
        [str(printtarg_bin)] + pt_args,
        capture_output=True, timeout=60, cwd=str(tmpdir),
    )
    if pt.returncode != 0:
        return 0
    return len(list(tmpdir.glob("calc*.tif")))


def cleanup(tmpdir):
    for f in tmpdir.glob("calc*"):
        try:
            f.unlink()
        except OSError:
            pass


def find_max_single_sheet(targen_bin, printtarg_bin, pt_args_no_target, initial_est):
    lo = max(20, int(initial_est * 0.5))
    hi = max(lo + 50, int(initial_est * 3.0))
    best = 0
    with tempfile.TemporaryDirectory() as tmp_str:
        tmpdir = Path(tmp_str)
        # Expand hi upward until it forces ≥2 pages, so the binary search
        # has a real upper bound.
        while True:
            pages = probe(targen_bin, printtarg_bin, hi, pt_args_no_target + ["calc"], tmpdir)
            cleanup(tmpdir)
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
            cleanup(tmpdir)
            if pages == 1:
                best = mid
                lo = mid + 1
            elif pages == 0:
                hi = mid - 1
            else:
                hi = mid - 1
    return best


def seed(paper):
    # Seed from CM + rig (-h) baseline since it's the closest existing point;
    # multiply by SCALE_BOOST so the search bracket sits above the expected
    # triple-density value.
    base = _PER_SHEET_CAPACITY.get(("CM", True, paper))
    if base is None:
        base = 100
    return max(30, int(base * SCALE_BOOST))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--argyll-bin", required=True, type=Path,
                    help="Path to Argyll bin directory (e.g. /Applications/Argyll/bin)")
    args = ap.parse_args()

    bin_dir: Path = args.argyll_bin
    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = bin_dir / f"targen{suffix}"
    printtarg = bin_dir / f"printtarg{suffix}"
    for binary in (targen, printtarg):
        if not binary.is_file():
            print(f"ERROR: not found: {binary}", file=sys.stderr)
            return 1

    results: dict[bool, dict[str, int]] = {True: {}, False: {}}

    total = len(PAPER_SIZES) * 2  # papers × LB
    done = 0
    for paper in PAPER_SIZES:
        for suppress_lb in (True, False):
            done += 1
            pt_args = [
                "-ii1", f"-p{paper}",
                "-t300", f"-a{PATCH_SCALE}",
                f"-m{MARGIN}", f"-M{MARGIN}",
                "-P",
            ]
            if suppress_lb:
                pt_args.append("-L")
            lb_tag = "-L " if suppress_lb else "noL"
            print(f"[{done:3d}/{total}] triple {paper:<8} {lb_tag} ...",
                  end="", flush=True)
            n = find_max_single_sheet(targen, printtarg, pt_args, seed(paper))
            results[suppress_lb][paper] = n
            print(f" {n}")

    def emit(name, d):
        print(f"\n{name}: dict[str, int] = {{")
        for paper in PAPER_SIZES:
            v = d.get(paper, 0)
            key = f'"{paper}"'
            if v > 0:
                print(f"    {key:<12} {v:>5},")
            else:
                print(f"    # {key:<10}  -- infeasible")
        print("}")

    print("\n=== _PER_SHEET_CAPACITY_TRIPLE (with -L) ===")
    emit("_PER_SHEET_CAPACITY_TRIPLE", results[True])

    print("\n=== _PER_SHEET_CAPACITY_TRIPLE_NO_LB (no -L) ===")
    emit("_PER_SHEET_CAPACITY_TRIPLE_NO_LB", results[False])
    return 0


if __name__ == "__main__":
    sys.exit(main())
