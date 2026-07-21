"""Generate a realistic Measurement Report demo set (Knut's request).

One printer + one paper, three profiles that differ by chart size, 14 dated
verification measurements over six months with a believable drift story:
a slow creep, two profile rebuilds that pull it back, and one bad day
(a partly clogged nozzle) that the report catches.

Physically shaped error model, then a per-run severity solved so each build
lands on its intended average dE00.

    python scripts/make_report_demo.py            # dry run: print the numbers
    python scripts/make_report_demo.py --write    # write ~/Desktop/ChromIQ-Demo-PRO300

The data is SYNTHETIC — no printer and no instrument were involved. It exists so
the Measurement Report window and its trend charts can be shown with a full,
coherent history behind them.
"""
from __future__ import annotations

import itertools
import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow.i1profiler_import import _patch_xyz, WHITE_XYZ            # noqa: E402
from workflow.measurement_report import _bradford_d65_to_d50            # noqa: E402
from workflow.ti3_analysis import (                                     # noqa: E402
    _lab_to_xyz_array, ciede2000, xyz_to_lab,
)

INSTRUMENT = "X-Rite i1 Pro 2"
PAPER = "Hahnemuehle Photo Rag 308"

# ---------------------------------------------------------------------------
# The charts (same printer, same paper — different chart size)
# ---------------------------------------------------------------------------
CHARTS = {
    "PRO300-PhotoRag-A4-294":  dict(n=294,  paper_size="210.0x297.0", seed=11),
    "PRO300-PhotoRag-A4-546":  dict(n=546,  paper_size="210.0x297.0", seed=22),
    "PRO300-PhotoRag-A3-1029": dict(n=1029, paper_size="297.0x420.0", seed=33),
}

# date, chart, target average dE00, white L*, black L*, note
BUILDS = [
    ("2026-01-13", "PRO300-PhotoRag-A3-1029", 1.72, 96.74,  9.05, "first profile"),
    ("2026-01-27", "PRO300-PhotoRag-A4-294",  1.75, 96.71,  9.11, ""),
    ("2026-02-10", "PRO300-PhotoRag-A4-294",  1.81, 96.69,  9.24, ""),
    ("2026-02-24", "PRO300-PhotoRag-A4-546",  1.79, 96.72,  9.31, ""),
    ("2026-03-10", "PRO300-PhotoRag-A4-294",  1.92, 96.66,  9.48, ""),
    ("2026-03-24", "PRO300-PhotoRag-A4-546",  1.88, 96.63,  9.57, ""),
    ("2026-04-07", "PRO300-PhotoRag-A3-1029", 1.70, 96.68,  9.44, "profile rebuilt"),
    ("2026-04-21", "PRO300-PhotoRag-A4-294",  1.86, 96.61,  9.62, ""),
    ("2026-05-05", "PRO300-PhotoRag-A4-546",  1.97, 96.58,  9.79, ""),
    ("2026-05-19", "PRO300-PhotoRag-A4-294",  3.10, 96.55, 10.21, "CLOG"),
    ("2026-06-02", "PRO300-PhotoRag-A4-546",  1.95, 96.60,  9.71, "after head clean"),
    ("2026-06-16", "PRO300-PhotoRag-A3-1029", 1.71, 96.62,  9.55, "profile rebuilt"),
    ("2026-06-30", "PRO300-PhotoRag-A4-294",  1.90, 96.57,  9.83, ""),
    ("2026-07-14", "PRO300-PhotoRag-A4-546",  2.08, 96.52, 10.06, ""),
]

# --- printer gamut: max chroma per hue (deg) at its best lightness ----------
# A pigment inkjet on matte rag: strong yellow/red, weaker cyan and blue —
# so the saturated sRGB corners clip, exactly as they do in real life.
# hue (deg) -> (max chroma at that hue's cusp, lightness of the cusp)
HUE_ANCHORS = [(0, 88, 48), (30, 92, 55), (60, 96, 72), (95, 104, 92),
               (140, 78, 58), (180, 70, 62), (215, 66, 48), (250, 62, 38),
               (285, 66, 32), (320, 84, 42), (350, 88, 46), (360, 88, 48)]
GAMUT_SCALE = 1.45
CHROMA_LOSS = 0.006          # slight overall desaturation of a good print
BASE_SIGMA = 0.13            # per-patch reproduction noise (Lab units)
PAPER_A, PAPER_B = 0.42, -1.05   # the paper's own tint (a slightly cool white)


README = """ChromIQ — Measurement Report demo data
=====================================

Three profiles for ONE printer on ONE paper (a Canon PIXMA PRO-300 on a matte
fine-art rag), measured fourteen times between January and July 2026. The three
profiles differ only in the size of the chart used:

    PRO300-PhotoRag-A4-294     A4,  294 patches   (the quick check)
    PRO300-PhotoRag-A4-546     A4,  546 patches
    PRO300-PhotoRag-A3-1029    A3, 1029 patches   (the full rebuild)

How to look at it
-----------------
ChromIQ → Tools → Measurement Report → "Add Profile's Measurements…", then pick
the .ti3 inside any run folder of each of the three profiles. Each one brings its
whole history with it, so three files give you all fourteen measurements and the
trend charts across six months.

What the history contains
-------------------------
  * a slow rise in the average error as the print head and inks age,
  * two profile rebuilds (7 April, 16 June) that pull it back down,
  * one bad day (19 May) — a partly starved light-cyan channel — that shows up
    as a spike in every chart and a full red column in Report Results,
  * the darkest black lifting from L* 9.0 to L* 10.1 over the six months,
  * the paper white drifting very slightly, as paper batches do.

IMPORTANT
---------
This data is SYNTHETIC. No printer and no measuring instrument were involved: it
was computed by scripts/make_report_demo.py in the ChromIQ repository. It exists
so the report and its trend charts can be shown with a full, coherent history
behind them. Do not use it to judge any real printer, paper or instrument.
"""


def gamut_c(h_deg: np.ndarray, L: np.ndarray) -> np.ndarray:
    """Max printable chroma for hue *h_deg* at lightness *L*."""
    hs = np.array([a[0] for a in HUE_ANCHORS], float)
    cs = np.array([a[1] for a in HUE_ANCHORS], float)
    ls = np.array([a[2] for a in HUE_ANCHORS], float)
    hh = np.mod(h_deg, 360.0)
    peak = np.interp(hh, hs, cs)
    cusp = np.interp(hh, hs, ls)
    # A double cone with the cusp at each hue's own lightness — yellow's widest
    # point is light, blue's is dark, the way a real ink set behaves.
    Lc = np.clip(L, 0.0, 100.0)
    below = Lc <= cusp
    t = np.where(below, np.divide(Lc, np.maximum(cusp, 1e-6)),
                 np.divide(100.0 - Lc, np.maximum(100.0 - cusp, 1e-6)))
    shape = np.clip(t, 0.0, 1.0) ** 0.45
    return peak * shape * GAMUT_SCALE


def design_lab(rgb: np.ndarray) -> np.ndarray:
    out = np.empty_like(rgb)
    for i, (r, g, b) in enumerate(rgb):
        xyz = _bradford_d65_to_d50(*_patch_xyz(float(r), float(g), float(b)))
        out[i] = xyz_to_lab(tuple(v / 100.0 for v in xyz))
    return out


DESIGN_BLACK_L = float(design_lab(np.array([[0.0, 0.0, 0.0]]))[0][0])


def measured_lab(dl: np.ndarray, rgb: np.ndarray, *, white_L: float,
                 black_L: float, sev: float, clog_k: float, rng) -> np.ndarray:
    """Apply the print model: tone curve, gamut clip, drift, paper cast, noise."""
    L, a, b = dl[:, 0], dl[:, 1], dl[:, 2]
    C = np.hypot(a, b)
    h = np.degrees(np.arctan2(b, a))

    # 1. tone reproduction — design black/white map onto the real paper's
    #    darkest black and paper white, with a small gamma error that grows
    #    with the drift severity.
    t = np.clip((L - DESIGN_BLACK_L) / (100.0 - DESIGN_BLACK_L), 0.0, 1.0)
    gamma = 1.0 + 0.010 * sev
    Lm = black_L + (white_L - black_L) * t ** gamma

    # 2. chroma — clip to the printer's gamut, lose a little saturation
    Cmax = gamut_c(h, Lm)
    Cm = np.minimum(C, Cmax) * (1.0 - CHROMA_LOSS * (1.0 + 0.9 * sev))
    # a small hue rotation that grows with drift (head/ink ageing), strongest
    # in the cyan-blue region where the light inks do the work
    hm = h + sev * (0.55 + 0.45 * np.cos(np.radians(h - 215.0)))

    am = Cm * np.cos(np.radians(hm))
    bm = Cm * np.sin(np.radians(hm))

    # 3. the paper's own tint, visible in the highlights, fading into the shadows
    w = (np.clip(Lm, 0, 100) / white_L) ** 3
    am += PAPER_A * w
    bm += PAPER_B * w

    # 4. a bad day: one starved light-cyan channel. It lays down the mid-tones,
    #    so the damage sits in the middle of the scale — paper white (no ink)
    #    and the maximum black (a different ink) are untouched.
    if clog_k > 0:
        cyanish = np.clip((100.0 - rgb[:, 0]) / 100.0, 0, 1)
        mid = np.exp(-((Lm - 55.0) / 26.0) ** 2)          # bell around L*55
        hit = cyanish * mid
        Lm += 3.1 * clog_k * hit
        am += 2.4 * clog_k * hit
        bm += -1.7 * clog_k * hit

    # 5. measurement / reproduction noise
    sigma = BASE_SIGMA * (1.0 + 0.7 * sev)
    Lm = Lm + rng.normal(0, sigma, Lm.shape)
    am = am + rng.normal(0, sigma * 1.15, am.shape)
    bm = bm + rng.normal(0, sigma * 1.15, bm.shape)

    # 6. The blank paper is the lightest thing on the sheet and the full-ink
    #    patch the darkest — nothing printed can fall outside them. Both extremes
    #    are pinned exactly, so the report's paper-white / darkest-black figures
    #    are this build's real values and not the luck of the noise.
    Lm = np.clip(Lm, black_L + 0.05, white_L - 0.05)
    ink = rgb.sum(axis=1)
    wi = int(np.argmax(ink))
    bi = int(np.argmin(ink))
    Lm[wi], am[wi], bm[wi] = white_L, PAPER_A, PAPER_B
    Lm[bi], am[bi], bm[bi] = black_L, -0.14, -0.28
    return np.column_stack([Lm, am, bm])


def des(dl: np.ndarray, ml: np.ndarray) -> np.ndarray:
    return np.array([ciede2000(tuple(dl[i]), tuple(ml[i])) for i in range(len(dl))])


def stats(d: np.ndarray) -> dict:
    a = np.sort(d)
    n = a.size
    k = max(1, min(n - 1, int(round(n * 0.95))))
    return dict(avg_all=a.mean(), avg_low95=a[:k].mean(), avg_high5=a[k:].mean(),
                max_all=a.max(), max_low95=a[:k].max())


# ---------------------------------------------------------------------------
# Patch sets
# ---------------------------------------------------------------------------
def patch_set(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pts: list[tuple[float, float, float]] = []
    seen: set[tuple[int, int, int]] = set()

    def add(p):
        key = tuple(int(round(v)) for v in p)
        if key not in seen:
            seen.add(key)
            pts.append(tuple(float(v) for v in p))

    for c in itertools.product((0.0, 100.0), repeat=3):      # the eight corners
        add(c)
    for i in range(21):                                      # neutral ramp
        add((i * 5.0, i * 5.0, i * 5.0))
    k = 3
    while (k + 1) ** 3 <= n - len(pts):
        k += 1
    grid = np.linspace(0.0, 100.0, k)
    for r in grid:
        for g in grid:
            for b in grid:
                if len(pts) < n:
                    add((r, g, b))
    guard = 0
    while len(pts) < n and guard < n * 40:
        guard += 1
        add(tuple(rng.uniform(0, 100, 3)))
    return np.array(pts[:n], float)


def sample_locs(n: int, steps: int = 21) -> list[str]:
    """A1…A21, B1… like a real strip chart."""
    out = []
    for i in range(n):
        strip, step = divmod(i, steps)
        letters = ""
        s = strip
        while True:
            letters = chr(ord("A") + s % 26) + letters
            s = s // 26 - 1
            if s < 0:
                break
        out.append(f"{letters}{step + 1}")
    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def _fmt(v: float) -> str:
    return f"{v:.5f}"


def write_ti2(path: Path, name: str, rgb: np.ndarray, locs: list[str],
              paper_size: str, when: str) -> None:
    wx, wy, wz = WHITE_XYZ
    lines = [
        "CTI2   ", "",
        'DESCRIPTOR "Argyll Calibration Target chart information 2"',
        'ORIGINATOR "ChromIQ layout engine"',
        f'CREATED "{when}"',
        f'TARGET_INSTRUMENT "{INSTRUMENT}"',
        f'APPROX_WHITE_POINT "{wx:.6f} {wy:.6f} {wz:.6f}"',
        'COLOR_REP "iRGB"',
        f'PAPER_SIZE "{paper_size}"',
        'STEPS_IN_PASS "21"',
        "", "NUMBER_OF_FIELDS 8", "BEGIN_DATA_FORMAT",
        "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z ",
        "END_DATA_FORMAT", "",
        f"NUMBER_OF_SETS {len(rgb)}", "BEGIN_DATA",
    ]
    for i, (r, g, b) in enumerate(rgb):
        x, y, z = _patch_xyz(float(r), float(g), float(b))
        lines.append(f'{i + 1} "{locs[i]}" {_fmt(r)} {_fmt(g)} {_fmt(b)} '
                     f"{_fmt(x)} {_fmt(y)} {_fmt(z)} ")
    lines += ["END_DATA", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ti3(path: Path, rgb: np.ndarray, locs: list[str], lab: np.ndarray,
              when: str, measured_date: str) -> None:
    xyz = _lab_to_xyz_array(lab)
    lines = [
        "CTI3   ", "",
        'DESCRIPTOR "Argyll Calibration Target chart information 3"',
        'ORIGINATOR "Argyll chartread"',
        f'CREATED "{when}"',
        'KEYWORD "CHROMIQ_MEASURED"',
        f'CHROMIQ_MEASURED "{measured_date}"',
        'DEVICE_CLASS "OUTPUT"',
        'COLOR_REP "iRGB_XYZ"',
        f'TARGET_INSTRUMENT "{INSTRUMENT}"',
        "", "NUMBER_OF_FIELDS 8", "BEGIN_DATA_FORMAT",
        "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z ",
        "END_DATA_FORMAT", "",
        f"NUMBER_OF_SETS {len(rgb)}", "BEGIN_DATA",
    ]
    for i, (r, g, b) in enumerate(rgb):
        x, y, z = xyz[i]
        lines.append(f'{i + 1} "{locs[i]}" {_fmt(r)} {_fmt(g)} {_fmt(b)} '
                     f"{_fmt(x)} {_fmt(y)} {_fmt(z)} ")
    lines += ["END_DATA", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# The severity a normal run sits at when the CLOG run is solved for the size of
# its defect instead — a bad day is a localised fault, not global drift.
CLOG_BASE_SEV = 2.0


def solve(dl, rgb, target, white_L, black_L, seed, *, clog: bool) -> tuple:
    lo, hi = 0.0, 14.0
    for _ in range(38):
        mid = (lo + hi) / 2
        sev = CLOG_BASE_SEV if clog else mid
        clog_k = mid if clog else 0.0
        rng = np.random.default_rng(seed)
        d = des(dl, measured_lab(dl, rgb, white_L=white_L, black_L=black_L,
                                 sev=sev, clog_k=clog_k, rng=rng)).mean()
        if d < target:
            lo = mid
        else:
            hi = mid
    mid = (lo + hi) / 2
    return (CLOG_BASE_SEV, mid) if clog else (mid, 0.0)


def main(out_root: Path, dry: bool) -> int:
    charts = {}
    for name, cfg in CHARTS.items():
        rgb = patch_set(cfg["n"], cfg["seed"])
        charts[name] = dict(rgb=rgb, locs=sample_locs(len(rgb)),
                            dl=design_lab(rgb), **cfg)

    print(f"{'date':12} {'chart':26} {'avg':>5} {'lo95':>5} {'hi5%':>5} "
          f"{'max':>5} {'mx95':>5}  note")
    runs_by_chart: dict[str, int] = {}
    plan = []
    for idx, (date, chart, target, white_L, black_L, note) in enumerate(BUILDS):
        c = charts[chart]
        seed = 1000 + idx
        sev, clog_k = solve(c["dl"], c["rgb"], target, white_L, black_L, seed,
                            clog=note == "CLOG")
        rng = np.random.default_rng(seed)
        ml = measured_lab(c["dl"], c["rgb"], white_L=white_L, black_L=black_L,
                          sev=sev, clog_k=clog_k, rng=rng)
        st = stats(des(c["dl"], ml))
        print(f"{date:12} {chart:26} {st['avg_all']:5.2f} {st['avg_low95']:5.2f} "
              f"{st['avg_high5']:5.2f} {st['max_all']:5.2f} {st['max_low95']:5.2f}"
              f"  {note}")
        runs_by_chart[chart] = runs_by_chart.get(chart, 0) + 1
        plan.append((date, chart, runs_by_chart[chart], ml, note))

    if dry:
        return 0

    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    total_runs = dict(runs_by_chart)
    for name in CHARTS:
        proj = out_root / name
        (proj / "runs").mkdir(parents=True)
        n = total_runs.get(name, 0)
        (proj / "project.json").write_text(json.dumps({
            "schema_version": 2,
            "created_at": f"{BUILDS[0][0]}T09:00:00",
            "target_name": name,
            "current_run": f"run{n}",
            "runs": [f"run{i + 1}" for i in range(n)],
        }, indent=2), encoding="utf-8")

    from workflow.measurement_report import build_report

    for date, chart, run_no, ml, note in plan:
        c = charts[chart]
        run_dir = out_root / chart / "runs" / f"run{run_no}"
        (run_dir / "reports").mkdir(parents=True)
        when = datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %d 11:24:07 %Y")
        write_ti2(run_dir / f"{chart}.ti2", chart, c["rgb"], c["locs"],
                  c["paper_size"], when)
        write_ti3(run_dir / f"{chart}.ti3", c["rgb"], c["locs"], ml, when, date)
        (run_dir / "meta.json").write_text(json.dumps({
            "run_id": f"run{run_no}",
            "created_at": f"{date}T11:24:07",
            "instrument": INSTRUMENT,
            "paper": PAPER,
            "status": "complete",
        }, indent=2), encoding="utf-8")
        rep = build_report(run_dir / f"{chart}.ti3")
        stamp = f"{date}_11-31-0{run_no % 10}"
        (run_dir / "reports" / f"report_{stamp}.json").write_text(
            json.dumps(rep, indent=2), encoding="utf-8")
        print(f"  wrote {chart}/runs/run{run_no}  ({date}) {note}")

    (out_root / "README.txt").write_text(README, encoding="utf-8")
    print("DONE →", out_root)
    return 0


if __name__ == "__main__":
    dry = "--write" not in sys.argv
    raise SystemExit(main(Path.home() / "Desktop" / "ChromIQ-Demo-PRO300", dry))
