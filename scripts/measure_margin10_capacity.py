"""Measure per-sheet patch capacity at margin=10mm for i1 and p3 instruments.

Runs targen + printtarg over each (instrument, paper, suppress_lb) combination
with -m10 -M10 -t300 -a1.0, binary-searches the largest patch count that still
fits on a single sheet, and prints two Python dict literals ready to paste
into data/patch_db.py as _PER_SHEET_CAPACITY_M10 and _PER_SHEET_CAPACITY_M10_NO_LB.

Usage:
    python scripts/measure_margin10_capacity.py --argyll-bin <path-to-bin>
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

from data.patch_db import PAPER_SIZES  # noqa: E402

INSTRUMENTS = ("i1", "p3")
DEVICE_TYPE = "2"  # RGB; layout doesn't depend on it but targen needs something


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
        # First widen `hi` upward until we hit ≥2 sheets, so we know the
        # search bracket actually straddles the boundary.
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
                # printtarg failed at this size; back off
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

    # Heuristic initial estimate per instrument so binary search has a useful start
    initial_est = {"i1": 600, "p3": 150}

    results_lb: dict[tuple[str, bool, str], int] = {}
    results_no_lb: dict[tuple[str, bool, str], int] = {}

    total = len(INSTRUMENTS) * len(PAPER_SIZES) * 2
    done = 0
    for instr in INSTRUMENTS:
        # printtarg uses "3p" for i1Pro 3 Plus
        pt_instr = "3p" if instr == "p3" else instr
        for paper in PAPER_SIZES:
            for suppress_lb in (True, False):
                done += 1
                pt_args = [
                    f"-i{pt_instr}", f"-p{paper}",
                    "-t300", "-m10", "-M10",
                ]
                if suppress_lb:
                    pt_args.append("-L")
                tag = "-L" if suppress_lb else "noL"
                print(f"[{done:3d}/{total}] {instr:>2} {paper:<8} {tag} ...", end="", flush=True)
                n = find_max_single_sheet(targen, printtarg, pt_args, initial_est[instr])
                if suppress_lb:
                    results_lb[(instr, False, paper)] = n
                else:
                    results_no_lb[(instr, False, paper)] = n
                print(f" {n}")

    def emit(name: str, d: dict[tuple[str, bool, str], int]) -> None:
        print(f"\n{name}: dict[tuple[str, bool, str], int] = {{")
        for instr in INSTRUMENTS:
            print(f"    # ---- {instr} -------------------------------------------")
            for paper in PAPER_SIZES:
                v = d.get((instr, False, paper), 0)
                key = f'("{instr}", False, "{paper}")'
                print(f"    {key:<28} {v:>5},")
        print("}")

    emit("_PER_SHEET_CAPACITY_M10", results_lb)
    emit("_PER_SHEET_CAPACITY_M10_NO_LB", results_no_lb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
