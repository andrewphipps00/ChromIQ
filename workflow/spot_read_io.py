"""Colour conversion and file output for the single-patch (spot) read tool.

spotread reports each reading as XYZ plus D50 L*a*b*. This module turns those
values into:

  * an on-screen sRGB swatch colour  (``lab_d50_to_srgb``)
  * a human-readable CSV             (``write_csv``)
  * an Argyll CGATS ``.ti3``         (``write_ti3`` — delegates to the existing
                                      ``colverify_runner.write_reference_ti3``)

The ``.ti3`` is written from the measured XYZ (Argyll derives Lab from XYZ), so
it opens cleanly in other Argyll tools; the CSV carries the user-given patch
names and the sRGB hex for quick reference in a spreadsheet.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from workflow.colverify_runner import write_reference_ti3


@dataclass
class SpotReading:
    """One spot reading: a name plus its measured XYZ and D50 Lab."""
    name: str
    xyz: tuple[float, float, float]
    lab: tuple[float, float, float]

    @property
    def hex(self) -> str:
        r, g, b = lab_d50_to_srgb(*self.lab)
        return f"#{r:02x}{g:02x}{b:02x}"


# --- Lab (D50) -> sRGB ------------------------------------------------------
#
# spotread prints D50 Lab. To show a swatch we go Lab(D50) -> XYZ(D50), adapt
# D50 -> D65 (Bradford), then the standard XYZ(D65) -> linear sRGB -> gamma.
# This is the inverse of the sRGB -> XYZ path in workflow/i1profiler_import.py.

# D50 reference white (Y = 1).
_D50 = (0.96422, 1.0, 0.82521)

# Bradford chromatic adaptation, D50 -> D65 (standard matrix).
_BRADFORD_D50_TO_D65 = (
    ( 0.9555766, -0.0230393,  0.0631636),
    (-0.0282895,  1.0099416,  0.0210077),
    ( 0.0122982, -0.0204830,  1.3299098),
)

# XYZ(D65) -> linear sRGB.
_XYZ_TO_RGB = (
    ( 3.2404542, -1.5371385, -0.4985314),
    (-0.9692660,  1.8760108,  0.0415560),
    ( 0.0556434, -0.2040259,  1.0572252),
)


def _lab_to_xyz_d50(L: float, a: float, b: float) -> tuple[float, float, float]:
    """D50 L*a*b* -> XYZ with Y on a 0..1 scale (white = _D50)."""
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def _f_inv(t: float) -> float:
        t3 = t ** 3
        return t3 if t3 > 0.008856 else (t - 16.0 / 116.0) / 7.787

    xr, yr, zr = _f_inv(fx), _f_inv(fy), _f_inv(fz)
    return xr * _D50[0], yr * _D50[1], zr * _D50[2]


def average_readings(readings: list["SpotReading"], name: str) -> "SpotReading":
    """Average several readings into one. Both XYZ (used for the .ti3/CSV) and
    Lab (used for the on-screen swatch) are averaged component-wise — spotread
    reports both for every reading, so the mean Lab is taken straight from the
    measured Labs rather than reconstructed from XYZ (whose scale we don't pin).
    Raises ``ValueError`` if ``readings`` is empty."""
    if not readings:
        raise ValueError("no readings to average")
    n = len(readings)
    xyz = tuple(sum(r.xyz[i] for r in readings) / n for i in range(3))
    lab = tuple(sum(r.lab[i] for r in readings) / n for i in range(3))
    return SpotReading(name=name, xyz=xyz, lab=lab)


def _mul(m: tuple, v: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(row[0] * v[0] + row[1] * v[1] + row[2] * v[2] for row in m)  # type: ignore[return-value]


def _gamma(c: float) -> float:
    """Linear-light 0..1 -> sRGB-encoded 0..1."""
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def lab_d50_to_srgb(L: float, a: float, b: float) -> tuple[int, int, int]:
    """D50 L*a*b* -> 8-bit sRGB ``(r, g, b)``, clamped to gamut.

    Out-of-gamut measurements (vivid inks, fluorescents) clamp per channel — the
    swatch is an *approximation* for orientation, never a colour-managed proof.
    """
    return xyz_d50_to_srgb(*_lab_to_xyz_d50(L, a, b))


def xyz_d50_to_srgb(X: float, Y: float, Z: float) -> tuple[int, int, int]:
    """D50 XYZ -> 8-bit sRGB ``(r, g, b)``, clamped to gamut. Expects Y on a
    0..1 scale (reference white ≈ 1.0); scale 0..100 input down before calling.

    Like :func:`lab_d50_to_srgb` this is an approximation for orientation /
    analysis (loading a CIE reference chart), not a colour-managed proof."""
    xyz_d65 = _mul(_BRADFORD_D50_TO_D65, (X, Y, Z))
    rgb_lin = _mul(_XYZ_TO_RGB, xyz_d65)
    return tuple(round(_gamma(c) * 255.0) for c in rgb_lin)  # type: ignore[return-value]


# --- File output ------------------------------------------------------------


def write_csv(path: Path, readings: list[SpotReading]) -> Path:
    """Write readings to a CSV: ``name,L,a,b,X,Y,Z,sRGB_hex`` (one row each)."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "L", "a", "b", "X", "Y", "Z", "sRGB_hex"])
        for r in readings:
            w.writerow([
                r.name,
                f"{r.lab[0]:.4f}", f"{r.lab[1]:.4f}", f"{r.lab[2]:.4f}",
                f"{r.xyz[0]:.4f}", f"{r.xyz[1]:.4f}", f"{r.xyz[2]:.4f}",
                r.hex,
            ])
    return path


def write_ti3(path: Path, readings: list[SpotReading]) -> Path:
    """Write readings to an Argyll CGATS ``.ti3`` (XYZ measurement set)."""
    return write_reference_ti3(path, [r.xyz for r in readings], space="XYZ")
