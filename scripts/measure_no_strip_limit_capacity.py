"""Measure per-sheet patch capacity with -P (no strip-length limit) for i1/p3.

Runs targen + printtarg over each (instrument, paper, margin, patch_scale,
suppress_lb) combination with -P added to the argv, binary-searches the
largest patch count that still fits on a single sheet, and prints eight
Python dict literals ready to paste into data/patch_db.py as:

    _PER_SHEET_CAPACITY_P                 (m=6,  -a 1.0,  -L on)
    _PER_SHEET_CAPACITY_NO_LB_P           (m=6,  -a 1.0,  -L off)
    _PER_SHEET_CAPACITY_M10_P             (m=10, -a 1.0,  -L on)
    _PER_SHEET_CAPACITY_M10_NO_LB_P       (m=10, -a 1.0,  -L off)
    _PER_SHEET_CAPACITY_A095_P            (m=6,  -a 0.95, -L on)
    _PER_SHEET_CAPACITY_A095_NO_LB_P      (m=6,  -a 0.95, -L off)
    _PER_SHEET_CAPACITY_A095_M10_P        (m=10, -a 0.95, -L on)
    _PER_SHEET_CAPACITY_A095_M10_NO_LB_P  (m=10, -a 0.95, -L off)

Usage:
    python scripts/measure_no_strip_limit_capacity.py --argyll-bin <path-to-bin>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from data.patch_db import (  # noqa: E402
    PAPER_SIZES,
    _PER_SHEET_CAPACITY,
    _PER_SHEET_CAPACITY_NO_LB,
    _PER_SHEET_CAPACITY_M10,
    _PER_SHEET_CAPACITY_M10_NO_LB,
    _PER_SHEET_CAPACITY_A095,
    _PER_SHEET_CAPACITY_A095_NO_LB,
    _PER_SHEET_CAPACITY_A095_M10,
    _PER_SHEET_CAPACITY_A095_M10_NO_LB,
)

INSTRUMENTS = ("i1", "p3")
MARGINS = (6, 10)
PATCH_SCALES = (1.0, 0.95)
DEVICE_TYPE = "2"  # RGB; layout doesn't depend on it

# -P removes the ~250-300mm strip-length cap. For large papers this lets the
# strip run full-bleed and adds 5-30% more patches; for small papers the cap
# never bit, so the value is unchanged. Seed +15% above the non-P table.
P_BOOST = 1.15


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


def seed_table(margin: int, scale: float, suppress_lb: bool) -> dict:
    """Pick the base (non-P) table that matches this combination."""
    if abs(scale - 1.0) <= 0.01:
        if margin == 6:
            return _PER_SHEET_CAPACITY if suppress_lb else _PER_SHEET_CAPACITY_NO_LB
        return _PER_SHEET_CAPACITY_M10 if suppress_lb else _PER_SHEET_CAPACITY_M10_NO_LB
    if margin == 6:
        return _PER_SHEET_CAPACITY_A095 if suppress_lb else _PER_SHEET_CAPACITY_A095_NO_LB
    return _PER_SHEET_CAPACITY_A095_M10 if suppress_lb else _PER_SHEET_CAPACITY_A095_M10_NO_LB


def seed_estimate(instr: str, paper: str, margin: int, scale: float, suppress_lb: bool) -> int:
    base_table = seed_table(margin, scale, suppress_lb)
    base = base_table.get((instr, False, paper))
    if base is None:
        base = 600 if instr == "i1" else 150
    return max(40, int(base * P_BOOST))


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

    # Eight result tables keyed by (margin, scale, suppress_lb)
    keys = [(m, s, l) for m in MARGINS for s in PATCH_SCALES for l in (True, False)]
    results: dict[tuple[int, float, bool], dict[tuple[str, bool, str], int]] = {
        k: {} for k in keys
    }

    total = len(INSTRUMENTS) * len(PAPER_SIZES) * len(keys)
    done = 0
    for instr in INSTRUMENTS:
        pt_instr = "3p" if instr == "p3" else instr
        for paper in PAPER_SIZES:
            for (margin, scale, suppress_lb) in keys:
                done += 1
                pt_args = [
                    f"-i{pt_instr}", f"-p{paper}",
                    "-t300",
                    f"-a{scale}",
                    f"-m{margin}", f"-M{margin}",
                    "-P",
                ]
                if suppress_lb:
                    pt_args.append("-L")
                lb_tag = "-L " if suppress_lb else "noL"
                print(f"[{done:3d}/{total}] {instr:>2} {paper:<8} m{margin:<2} a{scale} {lb_tag} ...",
                      end="", flush=True)
                est = seed_estimate(instr, paper, margin, scale, suppress_lb)
                n = find_max_single_sheet(targen, printtarg, pt_args, est)
                results[(margin, scale, suppress_lb)][(instr, False, paper)] = n
                print(f" {n}")

    name_of: dict[tuple[int, float, bool], str] = {
        (6,  1.0,  True):  "_PER_SHEET_CAPACITY_P",
        (6,  1.0,  False): "_PER_SHEET_CAPACITY_NO_LB_P",
        (10, 1.0,  True):  "_PER_SHEET_CAPACITY_M10_P",
        (10, 1.0,  False): "_PER_SHEET_CAPACITY_M10_NO_LB_P",
        (6,  0.95, True):  "_PER_SHEET_CAPACITY_A095_P",
        (6,  0.95, False): "_PER_SHEET_CAPACITY_A095_NO_LB_P",
        (10, 0.95, True):  "_PER_SHEET_CAPACITY_A095_M10_P",
        (10, 0.95, False): "_PER_SHEET_CAPACITY_A095_M10_NO_LB_P",
    }

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
                    print(f"    # {key:<26}  -- infeasible at this combo")
        print("}")

    for k in keys:
        emit(name_of[k], results[k])

    return 0


if __name__ == "__main__":
    sys.exit(main())
