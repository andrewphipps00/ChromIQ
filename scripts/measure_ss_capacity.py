"""Measure per-sheet patch capacity for SpectroScan across all paper sizes.

Runs targen + printtarg with -iSS -m6 -M6 -t300 -a1.0 over every paper key
(with and without -L), binary-searches the largest patch count that still
fits on a single sheet, and prints dict literals ready to paste into
data/patch_db.py.

Usage:
    python scripts/measure_ss_capacity.py --argyll-bin /Applications/Argyll/bin
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

DEVICE_TYPE = "2"  # RGB; layout doesn't depend on it


def probe(
    targen_bin: Path,
    printtarg_bin: Path,
    patches: int,
    pt_args: list[str],
    tmpdir: Path,
) -> int:
    """Return number of TIFF pages produced for the given patch count."""
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


def find_max_single_sheet(
    targen_bin: Path,
    printtarg_bin: Path,
    pt_args_no_target: list[str],
    initial_est: int,
) -> int:
    """Binary-search the largest patch count producing exactly 1 sheet."""
    lo = max(20, int(initial_est * 0.5))
    hi = max(lo + 50, int(initial_est * 2.5))
    best = 0

    with tempfile.TemporaryDirectory() as tmp_str:
        tmpdir = Path(tmp_str)
        # Widen `hi` upward until we hit ≥2 sheets so the bracket straddles.
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

    bin_dir: Path = args.argyll_bin
    suffix = ".exe" if sys.platform.startswith("win") else ""
    targen = bin_dir / f"targen{suffix}"
    printtarg = bin_dir / f"printtarg{suffix}"

    for binary in (targen, printtarg):
        if not binary.is_file():
            print(f"ERROR: not found: {binary}", file=sys.stderr)
            return 1

    # Heuristic per-paper initial estimate (rough, just for binary search start)
    initial_est = {
        "A2": 4000, "329x483": 2800, "483x329": 2800, "A3": 2100, "420x297": 2100,
        "11x17": 2000, "Legal": 1300, "A4": 1000, "A4R": 1000, "Letter": 1000,
        "LetterR": 1000, "203x254": 800, "127x178": 300, "4x6": 200,
    }

    results_lb: dict[str, int] = {}
    results_no_lb: dict[str, int] = {}

    total = len(PAPER_SIZES) * 2
    done = 0
    for paper in PAPER_SIZES:
        for suppress_lb in (True, False):
            done += 1
            pt_args = ["-iSS", f"-p{paper}", "-t300", "-m6", "-M6"]
            if suppress_lb:
                pt_args.append("-L")
            tag = "-L " if suppress_lb else "noL"
            print(f"[{done:3d}/{total}] SS {paper:<8} {tag} ...", end="", flush=True)
            n = find_max_single_sheet(targen, printtarg, pt_args, initial_est.get(paper, 500))
            if suppress_lb:
                results_lb[paper] = n
            else:
                results_no_lb[paper] = n
            print(f" {n}")

    def emit(name: str, d: dict[str, int]) -> None:
        print(f"\n{name}:")
        for paper in PAPER_SIZES:
            key = f'("SS", False, "{paper}")'
            print(f"    {key:<28} {d.get(paper, 0):>5},")

    emit("with -L", results_lb)
    emit("without -L", results_no_lb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
