"""Measurement report — statistics for a printed-chart measurement (.ti3).

Knut's request: after measuring, show how the reading compares to the chart's
expected colours (mean / median / worst / spread ΔE00, the worst patches, the
paper white and darkest black), save each report so they can be **compared
over time** on the same printer — surfacing ink ageing, printer drift or
instrument drift.

The "expected" reference is the chart's design colours (the sRGB-derived XYZ
that printtarg / the engine store in the ``.ti2``), matched to the measured
patches by ``SAMPLE_ID``. On a printer the absolute ΔE against sRGB is not
meaningful in isolation, but with a **fixed** reference the *change* between
two dated reports of the same chart is a clean drift signal.

Pure Python (numpy) — no Argyll process. Reuses ``ti3_analysis``.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

import numpy as np

from core.logger import get_logger
from workflow.ti3_analysis import (
    Ti3ParseError, ciede2000, parse_ti3, xyz_to_lab,
)

log = get_logger(__name__)

REPORT_SCHEMA = 1


def _srgb_hex(xyz100: "tuple[float, float, float]") -> str:
    """D50 XYZ (0..100) → #rrggbb for display (Bradford to D65, sRGB gamma)."""
    x, y, z = (v / 100.0 for v in xyz100)
    xd = 0.9555766 * x - 0.0230393 * y + 0.0631636 * z
    yd = -0.0282895 * x + 1.0099416 * y + 0.0210077 * z
    zd = 0.0122982 * x - 0.0204830 * y + 1.3299098 * z
    r = 3.2404542 * xd - 1.5371385 * yd - 0.4985314 * zd
    g = -0.9692660 * xd + 1.8760108 * yd + 0.0415560 * zd
    b = 0.0556434 * xd - 0.2040259 * yd + 1.0572252 * zd

    def enc(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return max(0, min(255, round(c * 255.0)))

    return "#{:02x}{:02x}{:02x}".format(enc(r), enc(g), enc(b))


def _reference_labs(ti2_path: Path) -> "dict[str, tuple]":
    """{SAMPLE_ID: expected Lab} from the chart's .ti2 design XYZ, or {}."""
    try:
        d = parse_ti3(ti2_path)
    except (Ti3ParseError, OSError):
        return {}
    out = {}
    for i, sid in enumerate(d.sample_ids):
        x, y, z = d.xyz[i]
        out[sid] = xyz_to_lab((x / 100.0, y / 100.0, z / 100.0))
    return out


def _stats(vals: "list[float]") -> dict:
    if not vals:
        return {"n": 0}
    a = np.asarray(vals, float)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 3),
        "median": round(float(np.median(a)), 3),
        "max": round(float(a.max()), 3),
        "min": round(float(a.min()), 3),
        "std": round(float(a.std(ddof=1)) if a.size > 1 else 0.0, 3),
        "p95": round(float(np.percentile(a, 95)), 3),
    }


def build_report(ti3_path: str | Path, worst_n: int = 15) -> dict:
    """Compute a measurement report from a measured ``.ti3``.

    Finds the sibling ``.ti2`` for the expected reference. Returns a JSON-able
    dict; ``de`` blocks are absent when no reference is available (then only
    white/black and patch-count are reported).
    """
    ti3_path = Path(ti3_path)
    data = parse_ti3(ti3_path)
    lab = [xyz_to_lab((x / 100.0, y / 100.0, z / 100.0)) for x, y, z in data.xyz]

    report: dict = {
        "schema": REPORT_SCHEMA,
        "created": datetime.now().isoformat(timespec="seconds"),
        "ti3": ti3_path.name,
        "chart": ti3_path.stem,
        "patches": data.n_patches,
    }

    # Paper white (lightest) and darkest black by measured L*.
    Ls = [l[0] for l in lab]
    wi = int(np.argmax(Ls))
    bi = int(np.argmin(Ls))
    report["paper_white"] = {
        "loc": data.sample_locs[wi] if data.sample_locs else data.sample_ids[wi],
        "lab": [round(v, 2) for v in lab[wi]],
        "hex": _srgb_hex(tuple(data.xyz[wi])),
    }
    report["max_black"] = {
        "loc": data.sample_locs[bi] if data.sample_locs else data.sample_ids[bi],
        "lab": [round(v, 2) for v in lab[bi]],
        "hex": _srgb_hex(tuple(data.xyz[bi])),
    }

    ref = _reference_labs(ti3_path.with_suffix(".ti2"))
    if ref:
        des: list[tuple[float, int]] = []
        for i, sid in enumerate(data.sample_ids):
            r = ref.get(sid)
            if r is not None:
                des.append((ciede2000(tuple(lab[i]), r), i))
        if des:
            report["de00"] = _stats([d for d, _ in des])
            worst = sorted(des, key=lambda t: -t[0])[:worst_n]
            report["worst_patches"] = [{
                "loc": data.sample_locs[i] if data.sample_locs else data.sample_ids[i],
                "de": round(de, 2),
                "expected_hex": _srgb_hex(ref_xyz(ref, data, i)),
                "measured_hex": _srgb_hex(tuple(data.xyz[i])),
                "expected_lab": [round(v, 2) for v in ref[data.sample_ids[i]]],
                "measured_lab": [round(v, 2) for v in lab[i]],
            } for de, i in worst]
    return report


def ref_xyz(ref_labs, data, i):
    """Expected XYZ(0..100) for patch i, recovered from its reference Lab."""
    from workflow.ti3_analysis import _lab_to_xyz_array
    lab = np.array([ref_labs[data.sample_ids[i]]])
    xyz = _lab_to_xyz_array(lab)[0] * 100.0
    return tuple(xyz)


def save_report(report: dict, run_dir: str | Path) -> Path:
    """Write the report as timestamped JSON under ``<run_dir>/reports/`` and
    return the path. Timestamped so a printer's reports accrue for comparison."""
    reports = Path(run_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = reports / f"report_{ts}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("measurement report saved: %s", path)
    return path


def list_reports(run_dir: str | Path) -> list[Path]:
    """All saved reports for a run, oldest first."""
    reports = Path(run_dir) / "reports"
    if not reports.is_dir():
        return []
    return sorted(reports.glob("report_*.json"))


def compare_reports(older: dict, newer: dict) -> dict:
    """Summarise the change between two reports of the same chart — the drift
    signal Knut wants (ink/printer/instrument ageing over time)."""
    out = {"older": older.get("created"), "newer": newer.get("created")}
    for key in ("mean", "median", "max", "p95", "std"):
        o = (older.get("de00") or {}).get(key)
        n = (newer.get("de00") or {}).get(key)
        if o is not None and n is not None:
            out[f"de00_{key}_delta"] = round(n - o, 3)
    # Paper white / black drift (ΔE00 between the two datings' white & black).
    for pt in ("paper_white", "max_black"):
        a, b = older.get(pt), newer.get(pt)
        if a and b:
            out[f"{pt}_de"] = round(
                ciede2000(tuple(a["lab"]), tuple(b["lab"])), 2)
    return out
