"""``build_profile()`` — the profile engine's front door (issue #122).

Turns a measured chart (``.ti3``) into a complete ICC v2 printer profile:
colorimetric A2B/B2A for any channel count, plus (when a source gamut is
available and mapping is enabled) distinct perceptual and saturation B2A
tables, plus the ``gamt`` tag ColorSync requires.

Table sizes follow colprof's ``-q`` exactly (lifted from
``profile/profout.c``, ArgyllCMS 3.5.0) so an engine build is structurally
interchangeable with a colprof build of the same quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from workflow.profile_engine import b2a as b2a_mod
from workflow.profile_engine import icc_writer as icw
from workflow.profile_engine.forward_model import ForwardModel, fit_forward_model
from workflow.profile_engine.ti3_data import Ti3Measurement, read_ti3


class EngineError(RuntimeError):
    """A build failure with a user-facing message."""


# colprof's -q table-size contract (profout.c): quality index 0..3 = l/m/h/u.
# Entries: (A2B shaper entries, A2B grid, B2A shaper entries, B2A grid).
_QUALITY_INDEX = {"l": 0, "m": 1, "h": 2, "u": 3}
_A2B_GRID_34 = {0: 5, 1: 9, 2: 17, 3: 23}       # 4+ device channels
_A2B_GRID_23 = {0: 9, 1: 17, 2: 33, 3: 45}      # 2-3 device channels
_A2B_ENTRIES = {0: 512, 1: 1024, 2: 2048, 3: 2048}
_B2A_GRID = {0: 9, 1: 17, 2: 33, 3: 45}
_B2A_ENTRIES = {0: 512, 1: 1024, 2: 2048, 3: 2048}

# Fit regularisation per A2B grid — tuned on the real fixtures so the
# self-fit lands in colprof's own band (~0.1–0.3 ΔE at the patches, i.e. the
# instrument-noise level; fitting tighter than the noise sharpens the inverse
# and blows up B2A round-trip errors — measured: max 11.8 → 2.3 by smoothing
# to parity). Roughly cubic in the grid spacing.
_FIT_LAMBDA_BY_GRID = {5: 0.02, 9: 0.03, 17: 0.04, 23: 0.08, 33: 0.15,
                       45: 0.4}


def _fit_lambda(grid: int) -> float:
    if grid in _FIT_LAMBDA_BY_GRID:
        return _FIT_LAMBDA_BY_GRID[grid]
    return max(0.005, 0.15 * ((grid - 1) / 32.0) ** 3)


@dataclass
class BuildSettings:
    quality: str = "m"                       # l / m / h / u, as colprof -q
    description: str | None = None           # default: the output file stem
    copyright: str = "Created with ChromIQ"
    ink_limit: float | None = None           # percent; None = from the .ti3
    curve_rounds: int = 2                    # 0 = bare grid fit (no shapers)
    # Perceptual/saturation gamut mapping (P4): path of a source gamut
    # profile. None = colorimetric-only intents (aliases, colprof's no -s/-S
    # behaviour).
    source_gamut: Path | str | None = None
    timestamp: datetime | None = None        # fixed → byte-reproducible
    progress: Callable[[str], None] | None = None


@dataclass
class BuildResult:
    icc_path: Path
    n_channels: int
    color_rep: str
    a2b_grid: int
    b2a_grid: int
    fit_median_de: float
    fit_p95_de: float
    b2a_ingamut_median_de: float
    oog_fraction: float
    perceptual_distinct: bool
    model: ForwardModel
    measurement: Ti3Measurement


def _emit(settings: BuildSettings, msg: str) -> None:
    if settings.progress is not None:
        settings.progress(msg)


def build_profile(ti3_path: Path | str, out_path: Path | str,
                  settings: BuildSettings | None = None) -> BuildResult:
    """Build an ICC printer profile from a measured chart."""
    settings = settings or BuildSettings()
    if settings.quality not in _QUALITY_INDEX:
        raise EngineError(f"Unknown quality {settings.quality!r} "
                          "(expected one of l, m, h, u).")
    q = _QUALITY_INDEX[settings.quality]

    _emit(settings, "Reading the measurement…")
    meas = read_ti3(ti3_path)
    n = meas.n_channels
    if not 1 <= n <= 15:
        raise EngineError(f"{n} device channels — outside the ICC range.")
    if n == 1:
        raise EngineError("Single-channel (grayscale) charts are not "
                          "supported by the ChromIQ engine yet — "
                          "Argyll colprof builds these.")

    a2b_grid = (_A2B_GRID_34 if n >= 4 else _A2B_GRID_23)[q]
    b2a_grid = _B2A_GRID[q]
    # Keep very high-dimensional grids inside sane memory: grid**n nodes.
    while a2b_grid ** n > 2_000_000 and a2b_grid > 3:
        a2b_grid -= 2

    _emit(settings, f"Fitting the printer model ({len(meas.device)} patches, "
                    f"grid {a2b_grid})…")
    model = fit_forward_model(meas.device, meas.lab_relative, grid=a2b_grid,
                              lam=_fit_lambda(a2b_grid),
                              curve_rounds=settings.curve_rounds)
    fit_res = np.linalg.norm(model.predict(meas.device) - meas.lab_relative,
                             axis=1)

    _emit(settings, f"Inverting the model (B2A grid {b2a_grid})…")
    ink_limit = settings.ink_limit if settings.ink_limit is not None \
        else meas.ink_limit
    dev_clut, residual = b2a_mod.build_b2a_clut(
        model, b2a_grid, channel_letters=meas.channel_letters,
        is_additive=meas.is_additive, ink_limit=ink_limit)
    # refine_b2a_clut returns *curve-space* values — written straight into
    # the CLUT, with the inverse shaper curves as B2A output tables.
    dev_clut_shaped = b2a_mod.refine_b2a_clut(
        model, dev_clut, residual, b2a_grid,
        ink_limit=ink_limit, is_additive=meas.is_additive)
    in_gamut = residual <= 1.0

    _emit(settings, "Writing the profile…")
    entries_a2b = _A2B_ENTRIES[q]
    entries_b2a = _B2A_ENTRIES[q]
    a2b = icw.make_mft2(
        n, 3, a2b_grid, icw.lab_to_u16(model.clut_lab()),
        in_tables=icw.curves_to_tables(model.curves, entries_a2b),
        out_tables=np.tile(icw._identity_table(entries_a2b), (3, 1)))
    inv = b2a_mod.inverse_curves(model.curves)
    b2a_col = icw.make_mft2(
        3, n, b2a_grid, icw.device_to_u16(dev_clut_shaped),
        in_tables=np.tile(icw._identity_table(entries_b2a), (3, 1)),
        out_tables=icw.curves_to_tables(inv, entries_b2a))
    gamt = icw.make_mft2(
        3, 1, b2a_grid,
        (np.clip(residual, 0, 128)[:, None] / 128 * 0xFFFF).round())

    luts: dict[str, bytes | str] = {
        "A2B0": a2b, "A2B1": "A2B0", "A2B2": "A2B0",
        "B2A1": b2a_col, "gamt": gamt,
    }
    perceptual_distinct = False
    if settings.source_gamut is not None:
        from workflow.profile_engine.gamut_map import build_mapped_b2a
        _emit(settings, "Building the perceptual and saturation tables…")
        b2a_perc, b2a_sat = build_mapped_b2a(
            model, meas, b2a_grid, Path(settings.source_gamut),
            channel_letters=meas.channel_letters,
            is_additive=meas.is_additive, ink_limit=ink_limit,
            entries=entries_b2a)
        luts["B2A0"] = b2a_perc
        luts["B2A2"] = b2a_sat
        perceptual_distinct = True
    else:
        luts["B2A0"] = "B2A1"
        luts["B2A2"] = "B2A1"

    out = Path(out_path)
    spec = icw.ProfileSpec(
        n_channels=n,
        color_rep=meas.color_rep,
        description=settings.description or out.stem,
        copyright=settings.copyright,
        wtpt=tuple(meas.media_white_xyz / 100.0),
        bkpt=tuple(meas.xyz[meas.black_index] / 100.0),
        targ=meas.text,
        timestamp=settings.timestamp,
    )
    icw.write_profile(out, spec, luts)

    gam_res = residual[in_gamut]
    return BuildResult(
        icc_path=out, n_channels=n, color_rep=meas.color_rep,
        a2b_grid=a2b_grid, b2a_grid=b2a_grid,
        fit_median_de=float(np.median(fit_res)),
        fit_p95_de=float(np.percentile(fit_res, 95)),
        b2a_ingamut_median_de=float(np.median(gam_res)) if len(gam_res) else 0.0,
        oog_fraction=float(1.0 - in_gamut.mean()),
        perceptual_distinct=perceptual_distinct,
        model=model, measurement=meas)
