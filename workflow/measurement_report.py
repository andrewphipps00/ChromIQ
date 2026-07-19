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

REPORT_SCHEMA = 2

# The eight corners of the RGB device cube, by device value (0..100). These are
# the paper white, the composite black and the six primary/secondary ink colours
# — so they say as much about the INKS as about the measurement (Knut). Order:
# neutral pair first, then primaries, then secondaries.
CUBE_CORNERS: "list[tuple[str, tuple[float, float, float]]]" = [
    ("W", (100.0, 100.0, 100.0)),
    ("K", (0.0, 0.0, 0.0)),
    ("R", (100.0, 0.0, 0.0)),
    ("G", (0.0, 100.0, 0.0)),
    ("B", (0.0, 0.0, 100.0)),
    ("C", (0.0, 100.0, 100.0)),
    ("M", (100.0, 0.0, 100.0)),
    ("Y", (100.0, 100.0, 0.0)),
]


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

    # The eight cube corners (paper white, composite black, the six ink
    # primaries/secondaries) — nearest patch to each corner by device RGB. Each
    # carries its measured colour and, when a reference exists, its expected
    # colour and ΔE00, so the report says something about the inks, not only the
    # instrument (Knut). rgb is device 0..100.
    report["corners"] = []
    if data.rgb is not None and len(data.rgb):
        rgb = np.asarray(data.rgb, dtype=float)
        for name, target in CUBE_CORNERS:
            ci = int(np.argmin(((rgb - np.array(target)) ** 2).sum(axis=1)))
            entry: dict = {
                "name": name,
                "loc": data.sample_locs[ci] if data.sample_locs else data.sample_ids[ci],
                "rgb": [round(v, 1) for v in rgb[ci]],
                "lab": [round(v, 2) for v in lab[ci]],
                "hex": _srgb_hex(tuple(data.xyz[ci])),
            }
            r = ref.get(data.sample_ids[ci]) if ref else None
            if r is not None:
                entry["expected_lab"] = [round(v, 2) for v in r]
                entry["expected_hex"] = _srgb_hex(ref_xyz(ref, data, ci))
                entry["de"] = round(ciede2000(tuple(lab[ci]), r), 2)
            report["corners"].append(entry)

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
    # _lab_to_xyz_array already scales to 0..100 (it multiplies the white point
    # by 100 internally); a second ×100 here overflowed _srgb_hex to white on
    # every expected swatch (worst-patches and cube corners). One scale only.
    return tuple(_lab_to_xyz_array(lab)[0])


def save_report(report: dict, run_dir: str | Path) -> Path:
    """Write the report as timestamped JSON under ``<run_dir>/reports/`` and
    return the path. Timestamped so a printer's reports accrue for comparison."""
    from core.file_manager import reports_subdir
    reports = reports_subdir(run_dir)
    reports.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = reports / f"report_{ts}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("measurement report saved: %s", path)
    return path


def list_reports(run_dir: str | Path) -> list[Path]:
    """All saved reports for a run, oldest first."""
    from core.file_manager import reports_subdir
    reports = reports_subdir(run_dir)
    if not reports.is_dir():
        return []
    return sorted(reports.glob("report_*.json"))


def list_project_reports(run_dir: str | Path) -> list[Path]:
    """Every saved report across ALL runs of this project — the printer's full
    measurement history (#40, Knut). *run_dir* is any run folder; its sibling
    ``run*`` folders are the printer's other builds. Sorted oldest-first by the
    report's ``created`` stamp (falling back to the filename). Falls back to the
    single run's reports when the folder isn't a ``runs/runN`` layout."""
    from core.file_manager import REPORTS_DIRNAME
    run_dir = Path(run_dir)
    runs_root = run_dir.parent
    paths: list[Path] = []
    if runs_root.is_dir() and run_dir.name.startswith("run"):
        paths = list(runs_root.glob(f"*/{REPORTS_DIRNAME}/report_*.json"))
    if not paths:                                   # not a runs/runN layout
        paths = list_reports(run_dir)

    def _created(p: Path) -> str:
        try:
            return str(json.loads(p.read_text()).get("created", "")) or p.name
        except Exception:  # noqa: BLE001
            return p.name
    return sorted(paths, key=_created)


def report_trend(reports: "list[dict]") -> "list[dict]":
    """A time series for the trend chart from a list of report dicts (#40).

    One point per report that carries at least one plottable metric, in the
    input order (already oldest-first from :func:`list_project_reports`):
    ``{"created", "chart", "mean", "max", "p95", "white_L", "black_L"}`` —
    metric keys absent when the report lacks them (no design reference)."""
    series: list[dict] = []
    for r in reports:
        pt: dict = {"created": r.get("created"), "chart": r.get("chart")}
        de = r.get("de00") or {}
        for k in ("mean", "max", "p95"):
            if de.get(k) is not None:
                pt[k] = float(de[k])
        w, b = r.get("paper_white"), r.get("max_black")
        if w and w.get("lab"):
            pt["white_L"] = float(w["lab"][0])
        if b and b.get("lab"):
            pt["black_L"] = float(b["lab"][0])
        # Per-corner ΔE00-from-design, so the cube-corner chart can plot how
        # each ink drifts over time (Knut).
        corners = {c["name"]: float(c["de"])
                   for c in (r.get("corners") or []) if c.get("de") is not None}
        if corners:
            pt["corners"] = corners
        if len(pt) > 2:                             # more than just created+chart
            series.append(pt)
    return series


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
