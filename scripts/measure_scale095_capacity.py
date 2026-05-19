"""Measure per-sheet patch capacity at patch-scale -a 0.95 for i1 and p3.

Runs targen + printtarg over each (instrument, paper, margin, suppress_lb)
combination with -a0.95 -t300, binary-searches the largest patch count that
still fits on a single sheet, and prints four Python dict literals ready to
paste into data/patch_db.py as:

    _PER_SHEET_CAPACITY_A095            (margin=6,  -L on)
    _PER_SHEET_CAPACITY_A095_NO_LB      (margin=6,  -L off)
    _PER_SHEET_CAPACITY_A095_M10        (margin=10, -L on)
    _PER_SHEET_CAPACITY_A095_M10_NO_LB  (margin=10, -L off)

Usage:
    python scripts/measure_scale095_capacity.py --argyll-bin <path-to-bin>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Allow running from any cwd: add repo root to sys.path so data.patch_db imports
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from data.patch_db import (  # noqa: E402
    PAPER_SIZES,
    _PER_SHEET_CAPACITY,
    _PER_SHEET_CAPACITY_M10,
)

INSTRUMENTS = ("i1", "p3")
MARGINS = (6, 10)
DEVICE_TYPE = "2"  # RGB; layout doesn't depend on it but targen needs something
PATCH_SCALE = 0.95
# Capacity scales as ~1/scale²; 1/0.9025 ≈ 1.108 → seed estimates +11% above 1.0
SCALE_BOOST = 1.108


def probe(
    targen_bin: Path,
    printtarg_bin: Path,
    patches: int,
    pt_args: list[str],
    tmpdir: Path,
) -> int:
    """Return number of TIFF pages produced for the given patch count."""
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


def cleanup(tmpdir: Path) -> None:
    for f in tmpdir.glob("calc*"):
        try:
            f.unlink()
        except OSError:
            pass


def find_max_single_sheet(
    targen_bin: Path,
    printtarg_bin: Path,
    pt_args_no_target: list[str],
    initial_est: int,
) -> int:
    """Binary-search the largest patch count producing exactly 1 sheet."""
    lo = max(20, int(initial_est * 0.5))
    hi = max(lo + 50, int(initial_est * 3.0))
    best = 0

    with tempfile.TemporaryDirectory() as tmp_str:
        tmpdir = Path(tmp_str)
        # First widen `hi` upward until we hit ≥2 sheets, so the bracket
        # actually straddles the boundary.
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


def seed_estimate(instr: str, paper: str, margin: int) -> int:
    """Pick an initial estimate based on the existing -a 1.0 DB, boosted ~11%."""
    base_table = _PER_SHEET_CAPACITY if margin == 6 else _PER_SHEET_CAPACITY_M10
    base = base_table.get((instr, False, paper))
    if base is None:
        # Fallback per-instrument defaults
        base = 600 if instr == "i1" else 150
    return max(40, int(base * SCALE_BOOST))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--argyll-bin", required=True, type=Path,
                    help="Path to the directory containing targen and printtarg binaries")
    args = ap.parse_args()

    bin_dir: Path = args.argyll_bin
    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = bin_dir / f"targen{suffix}"
    printtarg = bin_dir / f"printtarg{suffix}"

    for binary in (targen, printtarg):
        if not binary.is_file():
            print(f"ERROR: not found: {binary}", file=sys.stderr)
            return 1

    # Four result tables keyed by (margin, suppress_lb)
    results: dict[tuple[int, bool], dict[tuple[str, bool, str], int]] = {
        (6,  True):  {},
        (6,  False): {},
        (10, True):  {},
        (10, False): {},
    }

    total = len(INSTRUMENTS) * len(PAPER_SIZES) * len(MARGINS) * 2
    done = 0
    for instr in INSTRUMENTS:
        # printtarg uses "3p" for i1Pro 3 Plus
        pt_instr = "3p" if instr == "p3" else instr
        for paper in PAPER_SIZES:
            for margin in MARGINS:
                for suppress_lb in (True, False):
                    done += 1
                    pt_args = [
                        f"-i{pt_instr}", f"-p{paper}",
                        "-t300",
                        f"-a{PATCH_SCALE}",
                        f"-m{margin}", f"-M{margin}",
                    ]
                    if suppress_lb:
                        pt_args.append("-L")
                    lb_tag = "-L " if suppress_lb else "noL"
                    print(f"[{done:3d}/{total}] {instr:>2} {paper:<8} m{margin:<2} {lb_tag} ...",
                          end="", flush=True)
                    est = seed_estimate(instr, paper, margin)
                    n = find_max_single_sheet(targen, printtarg, pt_args, est)
                    results[(margin, suppress_lb)][(instr, False, paper)] = n
                    print(f" {n}")

    def emit(name: str, d: dict[tuple[str, bool, str], int]) -> None:
        print(f"\n{name}: dict[tuple[str, bool, str], int] = {{")
        for instr in INSTRUMENTS:
            print(f"    # ---- {instr} -------------------------------------------")
            for paper in PAPER_SIZES:
                v = d.get((instr, False, paper), 0)
                key = f'("{instr}", False, "{paper}")'
                if v > 0:
                    print(f"    {key:<28} {v:>5},")
                else:
                    print(f"    # {key:<26}  -- infeasible at this margin")
        print("}")

    emit("_PER_SHEET_CAPACITY_A095",            results[(6,  True)])
    emit("_PER_SHEET_CAPACITY_A095_NO_LB",      results[(6,  False)])
    emit("_PER_SHEET_CAPACITY_A095_M10",        results[(10, True)])
    emit("_PER_SHEET_CAPACITY_A095_M10_NO_LB",  results[(10, False)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
