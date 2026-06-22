#!/usr/bin/env python3
"""Derive empirical margin-threshold seeds from the shipped ColorMunki presets.

Knut's point: those presets were tested in practice and read correctly, so the
margins they actually produce are *known-good* values — a legitimate basis for
the editable seed defaults the Margin Thresholds tab ships with.

For every ColorMunki built-in preset that bundles its own ``.ti1`` this renders
the chart with the same printtarg flags the app uses, measures the realised
page margins, and prints a table grouped by (paper, orientation). Pick rounded,
slightly-relaxed numbers from the "min across presets" row for the seed table in
``core/settings.py``.

Run from the repo root with ArgyllCMS installed:
    PYTHONPATH=. python scripts/derive_margin_seeds.py
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from core.resource_path import resource_path
from ui.tabs.tab_chart import KNUT_PRESETS, _canonical_paper_name
from workflow.margin_inspector import measure_margins


def _printtarg() -> str:
    p = shutil.which("printtarg") or "/Applications/Argyll/bin/printtarg"
    if not Path(p).is_file():
        raise SystemExit("printtarg not found — install ArgyllCMS")
    return p


def _args_for(p) -> list[str]:
    """printtarg flags mirroring tab_chart's ti1-preset render path."""
    triple = p.triple_density and p.instrument == "CM"
    instr = "i1" if triple else ("3p" if p.instrument == "p3" else p.instrument)
    args = [f"-i{instr}", f"-p{p.paper}",
            ("-T" if p.tiff_16bit else "-t") + str(300)]
    if not triple and p.double_density and p.instrument in {"CM", "SS"}:
        args.append("-h")
    if p.suppress_left_clip or triple:
        args.append("-L")
    if abs(p.patch_scale - 1.0) > 0.01:
        args.append(f"-a{p.patch_scale:.2f}")
    if p.margin != 6:
        args.append(f"-m{p.margin}")
    args.append(f"-M{p.margin}")
    if p.no_strip_limit:
        args.append("-P")
    return args


def main() -> int:
    pt = _printtarg()
    grouped: dict[str, list[tuple]] = defaultdict(list)
    for p in KNUT_PRESETS:
        if p.instrument != "CM" or not p.ti1_asset:
            continue
        ti1 = Path(resource_path(p.ti1_asset))
        if not ti1.is_file():
            print(f"skip {p.slug}: ti1 missing")
            continue
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            shutil.copy(ti1, d / "chart.ti1")
            subprocess.run([pt, *_args_for(p), "chart"], cwd=d, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tifs = sorted(d.glob("chart*.tif"))
            ti2 = d / "chart.ti2"
            for tif in tifs:
                r = measure_margins(tif, dpi=300, ti2_path=ti2)
                if r is None:
                    continue
                name = _canonical_paper_name(r.page_w_mm, r.page_h_mm) or "?"
                orient = "Landscape" if r.page_w_mm > r.page_h_mm else "Portrait"
                grouped[f"{name} {orient}"].append(
                    (p.slug, r.left_mm, r.right_mm, r.top_mm, r.bottom_mm))

    print(f"\n{'combo':24} {'L':>6} {'R':>6} {'T':>6} {'B':>6}   (min across presets)")
    for combo in sorted(grouped):
        rows = grouped[combo]
        mins = [min(r[i] for r in rows) for i in range(1, 5)]
        print(f"{combo:24} " + " ".join(f"{m:6.1f}" for m in mins)
              + f"   (n={len(rows)})")
        for slug, *m in rows:
            print(f"   {slug:42} " + " ".join(f"{x:6.1f}" for x in m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
