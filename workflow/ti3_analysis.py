"""Analyse an ArgyllCMS ``.ti3`` measurement file — the raw data behind a
profile, before any modelling.

A ``.ti3`` is a CGATS table: one device RGB triplet plus the measured XYZ/Lab
(and usually the full spectral reflectance) for every patch on the chart. That
makes it the ground truth a profile is *fitted to*, so it can answer questions
the finished ``.icc`` can only approximate:

  * the **true** paper-white / max-black contrast (from the actual lightest and
    darkest patches, not a model's white/black point);
  * **grey balance** — how neutral the R=G=B patches really measured, i.e. any
    colour cast the profile then has to correct;
  * **gamut reach** — the most saturated colour the paper-and-ink achieved;
  * **measurement sanity** — non-monotonic neutrals or duplicate-patch scatter
    that betray a misread before you build a bad profile;
  * **appearance under other light** — recomputing the numbers from the spectral
    data under D65 / Illuminant A etc.

Pure Python + numpy; no ArgyllCMS call. Spectral maths uses :mod:`workflow.cie_data`
(CIE 1931 2° observer + standard illuminant SPDs); validated to reproduce the
file's own XYZ columns under D50 to ~0.1 %.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from workflow import cie_data
from workflow.icc_info import xyz_to_lab

# Reference white for the file's native (D50) Lab — matches xyz_to_lab's default.
_D50_REF = (0.96422, 1.0, 0.82521)

# Selectable illuminants for the spectral "under other light" section.
ILLUMINANTS: dict[str, tuple] = {
    "D50": cie_data.IL_D50,
    "D65": cie_data.IL_D65,
    "A (tungsten)": cie_data.IL_A,
    "F5 (fluor.)": cie_data.IL_F5,
    "F8 (fluor.)": cie_data.IL_F8,
}


class Ti3ParseError(ValueError):
    """Raised when a file is not a usable CGATS ``.ti3`` measurement."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
@dataclass
class Ti3Data:
    """Parsed contents of a ``.ti3`` file."""
    path: Path
    keywords: dict[str, str]
    fields: list[str]
    rows: list[list[str]]
    rgb: np.ndarray                # (N, 3), device 0–100
    xyz: np.ndarray                # (N, 3), measured XYZ 0–100 (D50)
    spectral: np.ndarray | None    # (N, B) reflectance %, or None
    wavelengths: np.ndarray | None  # (B,) nm, or None

    @property
    def n_patches(self) -> int:
        return len(self.rows)

    @property
    def has_spectral(self) -> bool:
        return self.spectral is not None


_KW_RE = re.compile(r'^([A-Z][A-Z0-9_]*)\s+"?(.*?)"?\s*$')


def parse_ti3(path: str | Path) -> Ti3Data:
    """Parse a ``.ti3`` (CGATS) file. Raises :class:`Ti3ParseError` on anything
    that isn't a readable measurement table with device + XYZ/Lab data."""
    p = Path(path)
    try:
        text = p.read_text(errors="replace")
    except OSError as exc:
        raise Ti3ParseError(str(exc)) from exc
    lines = text.splitlines()

    keywords: dict[str, str] = {}
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith(("BEGIN_", "END_", "NUMBER_OF")):
            continue
        m = _KW_RE.match(s)
        if m and m.group(2) != "":
            keywords.setdefault(m.group(1), m.group(2))

    try:
        fmt_start = next(i for i, ln in enumerate(lines)
                         if ln.strip() == "BEGIN_DATA_FORMAT")
        fields = lines[fmt_start + 1].split()
        data_start = next(i for i, ln in enumerate(lines)
                          if ln.strip() == "BEGIN_DATA")
        data_end = next(i for i, ln in enumerate(lines)
                        if ln.strip() == "END_DATA")
    except (StopIteration, IndexError) as exc:
        raise Ti3ParseError(
            "Not a CGATS measurement file (missing DATA_FORMAT / DATA).") from exc

    rows = [ln.split() for ln in lines[data_start + 1:data_end] if ln.strip()]
    if not rows:
        raise Ti3ParseError("The measurement table is empty.")

    def col(name: str) -> int | None:
        return fields.index(name) if name in fields else None

    rgb_i = [col(f"RGB_{c}") for c in "RGB"]
    xyz_i = [col(f"XYZ_{c}") for c in "XYZ"]
    lab_i = [col(f"LAB_{c}") for c in ("L", "A", "B")]
    if any(i is None for i in rgb_i):
        raise Ti3ParseError("No device RGB columns — only RGB charts are supported.")

    def grab(idx: list[int]) -> np.ndarray:
        return np.array([[float(r[i]) for i in idx] for r in rows], dtype=float)

    rgb = grab(rgb_i)

    if all(i is not None for i in xyz_i):
        xyz = grab(xyz_i)
    elif all(i is not None for i in lab_i):
        lab = grab(lab_i)
        xyz = _lab_to_xyz_array(lab)
    else:
        raise Ti3ParseError("No XYZ or Lab columns in the measurement.")

    spectral = wavelengths = None
    spec_i = [i for i, f in enumerate(fields) if f.startswith("SPEC_")]
    if len(spec_i) >= 3 and "SPECTRAL_BANDS" in keywords:
        try:
            bands = int(keywords["SPECTRAL_BANDS"])
            lo = float(keywords["SPECTRAL_START_NM"])
            hi = float(keywords["SPECTRAL_END_NM"])
            if len(spec_i) == bands:
                spectral = np.array([[float(r[i]) for i in spec_i] for r in rows])
                wavelengths = np.linspace(lo, hi, bands)
        except (ValueError, KeyError):
            spectral = wavelengths = None

    return Ti3Data(p, keywords, fields, rows, rgb, xyz, spectral, wavelengths)


def _f_inv(t: float) -> float:
    return t ** 3 if t ** 3 > 216.0 / 24389.0 else (116.0 * t - 16.0) / (24389.0 / 27.0)


def _lab_to_xyz_array(lab: np.ndarray) -> np.ndarray:
    out = np.empty_like(lab)
    for k, (L, a, b) in enumerate(lab):
        fy = (L + 16.0) / 116.0
        fx = fy + a / 500.0
        fz = fy - b / 200.0
        out[k] = [_f_inv(fx) * _D50_REF[0] * 100.0,
                  _f_inv(fy) * _D50_REF[1] * 100.0,
                  _f_inv(fz) * _D50_REF[2] * 100.0]
    return out


# ---------------------------------------------------------------------------
# Spectral integration (1 nm grid over the measured range)
# ---------------------------------------------------------------------------
class _Integrator:
    """Reflectance → XYZ under a chosen illuminant, on a 1 nm grid."""

    def __init__(self, wavelengths: np.ndarray):
        lo, hi = float(wavelengths[0]), float(wavelengths[-1])
        self.src = wavelengths
        self.grid = np.arange(math.ceil(lo), math.floor(hi) + 1, 1.0)
        self.xb = self._rs(cie_data.OBS_1931_2_X)
        self.yb = self._rs(cie_data.OBS_1931_2_Y)
        self.zb = self._rs(cie_data.OBS_1931_2_Z)

    def _rs(self, table: tuple) -> np.ndarray:
        lo, hi, vals = table
        return np.interp(self.grid, np.linspace(lo, hi, len(vals)), vals,
                         left=0.0, right=0.0)

    def white_xyz(self, illum: tuple) -> np.ndarray:
        I = self._rs(illum)
        k = 100.0 / float(np.sum(I * self.yb))
        return k * np.array([np.sum(I * self.xb), np.sum(I * self.yb),
                             np.sum(I * self.zb)])

    def reflect_xyz(self, refl_pct: np.ndarray, illum: tuple) -> np.ndarray:
        """XYZ (0–100, Y=100 for a perfect diffuser) of a reflectance spectrum."""
        R = np.interp(self.grid, self.src, refl_pct / 100.0, left=0.0, right=0.0)
        I = self._rs(illum)
        k = 100.0 / float(np.sum(I * self.yb))
        return k * np.array([np.sum(R * I * self.xb), np.sum(R * I * self.yb),
                             np.sum(R * I * self.zb)])


# ---------------------------------------------------------------------------
# Analysis result
# ---------------------------------------------------------------------------
@dataclass
class IlluminantPoint:
    name: str
    lab: tuple[float, float, float]   # paper white, ref = perfect diffuser
    contrast_ratio: float


@dataclass
class Ti3Analysis:
    data: Ti3Data
    # contrast (measured, native D50)
    white_lab: tuple[float, float, float]
    black_lab: tuple[float, float, float]
    contrast_ratio: float
    dynamic_range: float
    delta_lstar: float
    # grey balance
    n_neutral: int
    max_cast: float                 # largest C* among neutral patches
    max_cast_lstar: float
    mean_cast: float
    cast_token: str                 # tendency code (warm/cool/green/magenta/neutral/none)
    # gamut
    max_chroma: float
    max_chroma_hue: float           # degrees
    primary_chroma: dict[str, float]
    has_rolloff: bool               # full-ink primaries roll off (possible hidden CM)
    # quality
    neutral_non_monotonic: int
    duplicate_max_de: float | None  # max ΔEab between repeated device values
    duplicate_count: int
    # spectral
    illuminant_points: list[IlluminantPoint] = field(default_factory=list)
    paper_shift_d50_d65: float | None = None   # ΔEab of paper tint D50→D65


def analyse_ti3(data: Ti3Data) -> Ti3Analysis:
    xyz = data.xyz
    lab = np.array([xyz_to_lab((x / 100.0, y / 100.0, z / 100.0))
                    for x, y, z in xyz])
    Y = xyz[:, 1]

    # --- contrast: lightest vs darkest measured patch ----------------------
    wi, bi = int(np.argmax(Y)), int(np.argmin(Y))
    yw, yb = float(Y[wi]), float(Y[bi])
    ratio = yw / yb if yb > 0 else float("inf")
    drange = math.log10(ratio) if ratio > 0 and math.isfinite(ratio) else 0.0
    white_lab = tuple(float(v) for v in lab[wi])
    black_lab = tuple(float(v) for v in lab[bi])
    dl = white_lab[0] - black_lab[0]

    # --- grey balance: neutral (R≈G≈B) patches -----------------------------
    rgb = data.rgb
    spread = rgb.max(axis=1) - rgb.min(axis=1)
    neutral = spread <= 0.5
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    if neutral.any():
        ncast = chroma[neutral]
        mi = int(np.argmax(ncast))
        neutral_idx = np.where(neutral)[0]
        worst = neutral_idx[mi]
        max_cast = float(ncast[mi])
        max_cast_lstar = float(lab[worst, 0])
        mean_cast = float(ncast.mean())
        a_m, b_m = float(lab[neutral, 1].mean()), float(lab[neutral, 2].mean())
        cast_token = _cast_token(a_m, b_m)
    else:
        max_cast = max_cast_lstar = mean_cast = 0.0
        cast_token = "none"

    # --- gamut extremes ----------------------------------------------------
    ci = int(np.argmax(chroma))
    max_chroma = float(chroma[ci])
    hue = math.degrees(math.atan2(lab[ci, 2], lab[ci, 1])) % 360.0
    prim = _primary_chroma(lab, chroma)
    rolloff = _detect_rolloff(rgb, chroma)

    # --- measurement quality ----------------------------------------------
    nm = _neutral_non_monotonic(rgb, lab, neutral)
    dup_de, dup_n = _duplicate_scatter(rgb, lab)

    res = Ti3Analysis(
        data=data, white_lab=white_lab, black_lab=black_lab,
        contrast_ratio=ratio, dynamic_range=drange, delta_lstar=dl,
        n_neutral=int(neutral.sum()), max_cast=max_cast,
        max_cast_lstar=max_cast_lstar, mean_cast=mean_cast,
        cast_token=cast_token, max_chroma=max_chroma,
        max_chroma_hue=hue, primary_chroma=prim, has_rolloff=rolloff,
        neutral_non_monotonic=nm, duplicate_max_de=dup_de, duplicate_count=dup_n,
    )

    # --- spectral: under other light --------------------------------------
    if data.has_spectral:
        _add_spectral(res, data, wi, bi)
    return res


def _cast_token(a: float, b: float) -> str:
    """Tendency code for the neutral cast (the dialog turns it into localised
    text). One of: neutral / magenta / green / warm / cool."""
    if math.hypot(a, b) < 0.5:
        return "neutral"
    if abs(a) >= abs(b):
        return "magenta" if a > 0 else "green"
    return "warm" if b > 0 else "cool"


def _primary_chroma(lab: np.ndarray, chroma: np.ndarray) -> dict[str, float]:
    hues = (np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0)
    out: dict[str, float] = {}
    sectors = {"red": (330, 30), "yellow": (60, 120), "green": (120, 180),
               "cyan": (180, 240), "blue": (240, 300), "magenta": (300, 330)}
    for name, (lo, hi) in sectors.items():
        if lo > hi:
            sel = (hues >= lo) | (hues < hi)
        else:
            sel = (hues >= lo) & (hues < hi)
        out[name] = float(chroma[sel].max()) if sel.any() else 0.0
    return out


def _detect_rolloff(rgb: np.ndarray, chroma: np.ndarray) -> bool:
    """Detect the 'secretly colour-managed chart' signature: full-ink chromatic
    primaries notably *less* saturated than mid-tone ones — a hint the driver
    applied colour management despite a 'No Correction' setting. Conservative;
    False when nothing stands out."""
    hi = rgb.max(axis=1)
    lo = rgb.min(axis=1)
    full = (hi > 95) & (lo < 5) & (chroma > 5)        # full-ink primaries
    strong = (hi > 70) & (lo < 30) & (chroma > 5)     # strong mid-tone chromatics
    if full.sum() < 3 or strong.sum() < 5:
        return False
    return bool(chroma[full].max() < 0.7 * chroma[strong].max())


def _neutral_non_monotonic(rgb: np.ndarray, lab: np.ndarray,
                           neutral: np.ndarray) -> int:
    """Count steps where lightness falls as device grey level rises (beyond
    measurement noise) — a fingerprint of a misread strip."""
    idx = np.where(neutral)[0]
    if idx.size < 3:
        return 0
    level = rgb[idx].mean(axis=1)
    order = np.argsort(level)
    L = lab[idx][order, 0]
    drops = np.diff(L)
    return int(np.sum(drops < -1.0))


def _duplicate_scatter(rgb: np.ndarray, lab: np.ndarray) -> tuple[float | None, int]:
    """Max ΔEab between patches that share the same device RGB (repeat patches),
    a direct read on measurement repeatability. Returns (max_de, n_groups)."""
    keys: dict[tuple, list[int]] = {}
    for i, c in enumerate(rgb):
        keys.setdefault(tuple(np.round(c, 2)), []).append(i)
    worst = 0.0
    groups = 0
    for members in keys.values():
        if len(members) < 2:
            continue
        groups += 1
        sub = lab[members]
        for a in range(len(sub)):
            for b in range(a + 1, len(sub)):
                worst = max(worst, float(np.linalg.norm(sub[a] - sub[b])))
    return (worst if groups else None), groups


def _add_spectral(res: Ti3Analysis, data: Ti3Data, wi: int, bi: int) -> None:
    integ = _Integrator(data.wavelengths)
    white_spec = data.spectral[wi]
    black_spec = data.spectral[bi]
    pts: list[IlluminantPoint] = []
    lab_by_name: dict[str, tuple] = {}
    for name, illum in ILLUMINANTS.items():
        wref = integ.white_xyz(illum)
        wx = integ.reflect_xyz(white_spec, illum)
        bx = integ.reflect_xyz(black_spec, illum)
        ref = (wref[0] / 100.0, wref[1] / 100.0, wref[2] / 100.0)
        wlab = xyz_to_lab((wx[0] / 100.0, wx[1] / 100.0, wx[2] / 100.0), ref)
        ratio = (wx[1] / bx[1]) if bx[1] > 0 else float("inf")
        pts.append(IlluminantPoint(name, tuple(float(v) for v in wlab), ratio))
        lab_by_name[name] = wlab
    res.illuminant_points = pts
    if "D50" in lab_by_name and "D65" in lab_by_name:
        d50, d65 = np.array(lab_by_name["D50"]), np.array(lab_by_name["D65"])
        res.paper_shift_d50_d65 = float(np.linalg.norm(d50 - d65))
