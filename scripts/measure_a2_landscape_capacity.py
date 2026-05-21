"""Measure per-sheet patch capacity for A2 Landscape (594x420 mm) across every
instrument + option combination already represented in data/patch_db.py.

printtarg has no named A2-landscape size, so A2 landscape is the custom size
594x420 (mirrors 420x297 = A3 landscape and 483x329 = A3+ landscape).

This is a one-off, surgical companion to the existing per-feature measurement
scripts: instead of re-measuring all 14 papers in seven scripts, it measures
*only* the new 594x420 key, but across the full job matrix — including the
baseline (m6, a1.0) i1/p3/CM tables, which no standing script covers.

For each combination it runs:
    targen  -d2 -f<N> calc
    printtarg <recipe-args> calc
binary-searches the largest patch count that still fits on a single sheet, and
prints the new `("instr", dd, "594x420"): N,` line grouped under the patch_db
table it belongs to — ready to paste in right after each table's existing A2 row.

Usage:
    python scripts/measure_a2_landscape_capacity.py --argyll-bin /Applications/Argyll/bin
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
    _PER_SHEET_CAPACITY,
    _PER_SHEET_CAPACITY_NO_LB,
    _PER_SHEET_CAPACITY_M10,
    _PER_SHEET_CAPACITY_M10_NO_LB,
    _PER_SHEET_CAPACITY_A095,
    _PER_SHEET_CAPACITY_A095_NO_LB,
    _PER_SHEET_CAPACITY_A095_M10,
    _PER_SHEET_CAPACITY_A095_M10_NO_LB,
    _PER_SHEET_CAPACITY_P,
    _PER_SHEET_CAPACITY_NO_LB_P,
    _PER_SHEET_CAPACITY_M10_P,
    _PER_SHEET_CAPACITY_M10_NO_LB_P,
    _PER_SHEET_CAPACITY_A095_P,
    _PER_SHEET_CAPACITY_A095_NO_LB_P,
    _PER_SHEET_CAPACITY_A095_M10_P,
    _PER_SHEET_CAPACITY_A095_M10_NO_LB_P,
    _PER_SHEET_CAPACITY_TRIPLE,
    _PER_SHEET_CAPACITY_TRIPLE_NO_LB,
)

PAPER = "594x420"          # A2 landscape, custom printtarg size
SRC = "A2"                 # seed from the A2-portrait value of the matching table
DEVICE_TYPE = "2"          # RGB; layout doesn't depend on it


def probe(targen_bin: Path, printtarg_bin: Path, patches: int,
          pt_args: list[str], tmpdir: Path) -> int:
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


def find_max_single_sheet(targen_bin: Path, printtarg_bin: Path,
                          pt_args_no_target: list[str], initial_est: int) -> int:
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
            else:
                hi = mid - 1
    return best


def seed_from(table: dict, key) -> int:
    """Initial estimate: A2-portrait value of the same table (≈ same area)."""
    base = table.get(key)
    if base is None:
        base = 400
    return max(40, int(base))


def build_jobs() -> list[dict]:
    """Each job: table name, db key, printtarg args (no basename), seed estimate."""
    jobs: list[dict] = []

    # ---- i1 / p3: 16 tables (8 base + 8 -P), m∈{6,10} a∈{1.0,0.95} lb∈{T,F} -----
    i1p3_tables = {
        # (margin, scale, suppress_lb, nsl): (with_L_name/table, no_LB_name/table)
        (6,  1.0,  False): ("_PER_SHEET_CAPACITY", _PER_SHEET_CAPACITY,
                            "_PER_SHEET_CAPACITY_NO_LB", _PER_SHEET_CAPACITY_NO_LB),
        (10, 1.0,  False): ("_PER_SHEET_CAPACITY_M10", _PER_SHEET_CAPACITY_M10,
                            "_PER_SHEET_CAPACITY_M10_NO_LB", _PER_SHEET_CAPACITY_M10_NO_LB),
        (6,  0.95, False): ("_PER_SHEET_CAPACITY_A095", _PER_SHEET_CAPACITY_A095,
                            "_PER_SHEET_CAPACITY_A095_NO_LB", _PER_SHEET_CAPACITY_A095_NO_LB),
        (10, 0.95, False): ("_PER_SHEET_CAPACITY_A095_M10", _PER_SHEET_CAPACITY_A095_M10,
                            "_PER_SHEET_CAPACITY_A095_M10_NO_LB", _PER_SHEET_CAPACITY_A095_M10_NO_LB),
        (6,  1.0,  True):  ("_PER_SHEET_CAPACITY_P", _PER_SHEET_CAPACITY_P,
                            "_PER_SHEET_CAPACITY_NO_LB_P", _PER_SHEET_CAPACITY_NO_LB_P),
        (10, 1.0,  True):  ("_PER_SHEET_CAPACITY_M10_P", _PER_SHEET_CAPACITY_M10_P,
                            "_PER_SHEET_CAPACITY_M10_NO_LB_P", _PER_SHEET_CAPACITY_M10_NO_LB_P),
        (6,  0.95, True):  ("_PER_SHEET_CAPACITY_A095_P", _PER_SHEET_CAPACITY_A095_P,
                            "_PER_SHEET_CAPACITY_A095_NO_LB_P", _PER_SHEET_CAPACITY_A095_NO_LB_P),
        (10, 0.95, True):  ("_PER_SHEET_CAPACITY_A095_M10_P", _PER_SHEET_CAPACITY_A095_M10_P,
                            "_PER_SHEET_CAPACITY_A095_M10_NO_LB_P", _PER_SHEET_CAPACITY_A095_M10_NO_LB_P),
    }
    for instr in ("i1", "p3"):
        pt_instr = "3p" if instr == "p3" else "i1"
        for (margin, scale, nsl), (lname, ltab, nname, ntab) in i1p3_tables.items():
            for suppress_lb, tname, tab in ((True, lname, ltab), (False, nname, ntab)):
                args = [f"-i{pt_instr}", f"-p{PAPER}", "-t300",
                        f"-a{scale}", f"-m{margin}", f"-M{margin}"]
                if nsl:
                    args.append("-P")
                if suppress_lb:
                    args.append("-L")
                jobs.append({
                    "table": tname,
                    "key": (instr, False, PAPER),
                    "args": args,
                    "seed": seed_from(tab, (instr, False, SRC)),
                })

    # ---- CM: baseline + a0.95, both -h states, m6 only -------------------------
    cm_tables = {
        (1.0,  True):  ("_PER_SHEET_CAPACITY", _PER_SHEET_CAPACITY),
        (1.0,  False): ("_PER_SHEET_CAPACITY_NO_LB", _PER_SHEET_CAPACITY_NO_LB),
        (0.95, True):  ("_PER_SHEET_CAPACITY_A095", _PER_SHEET_CAPACITY_A095),
        (0.95, False): ("_PER_SHEET_CAPACITY_A095_NO_LB", _PER_SHEET_CAPACITY_A095_NO_LB),
    }
    for (scale, suppress_lb), (tname, tab) in cm_tables.items():
        for dd in (False, True):
            args = ["-iCM", f"-p{PAPER}", "-t300", f"-a{scale}", "-m6", "-M6"]
            if dd:
                args.append("-h")
            if suppress_lb:
                args.append("-L")
            jobs.append({
                "table": tname,
                "key": ("CM", dd, PAPER),
                "args": args,
                "seed": seed_from(tab, ("CM", dd, SRC)),
            })

    # ---- CM triple density (printtarg laid out as i1, -a1.3 -m5 -M5 -P) --------
    for suppress_lb, tname, tab in (
        (True,  "_PER_SHEET_CAPACITY_TRIPLE", _PER_SHEET_CAPACITY_TRIPLE),
        (False, "_PER_SHEET_CAPACITY_TRIPLE_NO_LB", _PER_SHEET_CAPACITY_TRIPLE_NO_LB),
    ):
        args = ["-ii1", f"-p{PAPER}", "-t300", "-a1.3", "-m5", "-M5", "-P"]
        if suppress_lb:
            args.append("-L")
        jobs.append({
            "table": tname,
            "key": PAPER,  # triple tables are keyed by paper string alone
            "args": args,
            "seed": seed_from(tab, SRC),
        })

    # ---- SS: square + hexagon, both -L states ---------------------------------
    for suppress_lb, tname, tab in (
        (True,  "_PER_SHEET_CAPACITY", _PER_SHEET_CAPACITY),
        (False, "_PER_SHEET_CAPACITY_NO_LB", _PER_SHEET_CAPACITY_NO_LB),
    ):
        for hexp in (False, True):
            args = ["-iSS", f"-p{PAPER}", "-t300", "-m6", "-M6"]
            if hexp:
                args.append("-h")
            if suppress_lb:
                args.append("-L")
            jobs.append({
                "table": tname,
                "key": ("SS", hexp, PAPER),
                "args": args,
                "seed": seed_from(tab, ("SS", hexp, SRC)),
            })

    return jobs


def fmt_key(key) -> str:
    if isinstance(key, str):
        return f'"{key}"'
    instr, dd, paper = key
    return f'("{instr}", {dd}, "{paper}")'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--argyll-bin", required=True, type=Path,
                    help="Directory containing targen and printtarg binaries")
    args = ap.parse_args()

    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = args.argyll_bin / f"targen{suffix}"
    printtarg = args.argyll_bin / f"printtarg{suffix}"
    for binary in (targen, printtarg):
        if not binary.is_file():
            print(f"ERROR: not found: {binary}", file=sys.stderr)
            return 1

    jobs = build_jobs()
    results: dict[str, list[tuple]] = {}
    total = len(jobs)
    for i, job in enumerate(jobs, 1):
        print(f"[{i:3d}/{total}] {job['table']:<38} {' '.join(job['args'])} ...",
              end="", flush=True)
        n = find_max_single_sheet(targen, printtarg, job["args"], job["seed"])
        print(f" {n}")
        results.setdefault(job["table"], []).append((job["key"], n))

    print("\n\n========== PASTE INTO data/patch_db.py "
          "(one line after each table's A2 row) ==========")
    for table in results:
        print(f"\n# --- {table} ---")
        for key, n in results[table]:
            print(f"    {fmt_key(key) + ':':<28} {n:>5},")

    return 0


if __name__ == "__main__":
    sys.exit(main())
