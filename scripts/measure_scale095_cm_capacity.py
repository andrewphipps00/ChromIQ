"""Measure per-sheet patch capacity at -a 0.95 for ColorMunki (CM), both -h states.

Runs targen + printtarg over each (paper, dd, suppress_lb) combination with
-iCM -a0.95 -m6 -M6 -t300 (optionally -h, -L) and binary-searches the largest
patch count that still fits on a single sheet. Prints two Python dict literals
ready to paste into data/patch_db.py.

CM uses margin=6 only — the existing CM tables have no m=10 entries because
the patch-by-patch read pattern doesn't benefit from the wider strip headroom
that i1Pro family needs.

Usage:
    python scripts/measure_scale095_cm_capacity.py --argyll-bin <path-to-bin>
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
PATCH_SCALE = 0.95
SCALE_BOOST = 1.108  # 1/0.9025


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


def seed(dd, paper):
    base = _PER_SHEET_CAPACITY.get(("CM", dd, paper))
    if base is None:
        base = 100
    return max(30, int(base * SCALE_BOOST))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--argyll-bin", required=True, type=Path)
    args = ap.parse_args()

    bin_dir: Path = args.argyll_bin
    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = bin_dir / f"targen{suffix}"
    printtarg = bin_dir / f"printtarg{suffix}"
    for binary in (targen, printtarg):
        if not binary.is_file():
            print(f"ERROR: not found: {binary}", file=sys.stderr)
            return 1

    # Tables keyed by (dd, suppress_lb)
    results: dict[tuple[bool, bool], dict[tuple[str, bool, str], int]] = {
        (False, True):  {},
        (False, False): {},
        (True,  True):  {},
        (True,  False): {},
    }

    total = 2 * len(PAPER_SIZES) * 2  # dd × papers × LB
    done = 0
    for dd in (False, True):
        for paper in PAPER_SIZES:
            for suppress_lb in (True, False):
                done += 1
                pt_args = [
                    "-iCM", f"-p{paper}",
                    "-t300", f"-a{PATCH_SCALE}",
                    "-m6", "-M6",
                ]
                if dd:
                    pt_args.append("-h")
                if suppress_lb:
                    pt_args.append("-L")
                dd_tag = "-h " if dd else "   "
                lb_tag = "-L " if suppress_lb else "noL"
                print(f"[{done:3d}/{total}] CM {paper:<8} {dd_tag} {lb_tag} ...",
                      end="", flush=True)
                n = find_max_single_sheet(targen, printtarg, pt_args, seed(dd, paper))
                results[(dd, suppress_lb)][("CM", dd, paper)] = n
                print(f" {n}")

    def emit(name, d):
        print(f"\n{name}: dict[tuple[str, bool, str], int] = {{")
        for dd in (False, True):
            print(f"    # ---- CM dd={dd} -------------------------------------------")
            for paper in PAPER_SIZES:
                v = d.get(("CM", dd, paper), 0)
                key = f'("CM", {dd}, "{paper}")'
                if v > 0:
                    print(f"    {key:<30} {v:>5},")
                else:
                    print(f"    # {key:<28}  -- infeasible")
        print("}")

    # The four CM result tables get merged into the two patch_db dicts by
    # the editor — we emit them separately so it's easy to spot any LB asymmetry.
    print("\n=== Existing CM -a 0.95 entries to merge into _PER_SHEET_CAPACITY_A095 (with -L) ===")
    merged_lb: dict[tuple[str, bool, str], int] = {}
    merged_lb.update(results[(False, True)])
    merged_lb.update(results[(True,  True)])
    emit("CM (with -L, scale=0.95)", merged_lb)

    print("\n=== Existing CM -a 0.95 entries to merge into _PER_SHEET_CAPACITY_A095_NO_LB ===")
    merged_nolb: dict[tuple[str, bool, str], int] = {}
    merged_nolb.update(results[(False, False)])
    merged_nolb.update(results[(True,  False)])
    emit("CM (no -L, scale=0.95)", merged_nolb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
