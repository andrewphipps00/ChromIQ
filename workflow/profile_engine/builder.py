"""``build_profile()`` — the profile engine's front door (issue #122).

Turns a measured chart (``.ti3``) into a complete ICC v2 printer profile:
colorimetric A2B/B2A for any channel count, plus (when a source gamut is
given) distinct perceptual and saturation B2A tables, plus the ``gamt`` tag
ColorSync requires.

Option coverage mirrors colprof's behaviour for OUTPUT-class data, verified
against ``colprof.c`` / ``profout.c`` (ArgyllCMS 3.5.0) and against real
builds:

* table sizes follow ``-q``/``-b`` exactly (profout.c constants);
* ``-a``: output profiles are Lab or XYZ cLUTs — colprof errors on every
  matrix/gamma type for printer data ("Output profile can only be a cLUT
  algorithm") and the engine raises the same error;
* ``-r`` (avgdev) scales the fit smoothing; ``-V`` is a **no-op for output
  profiles in colprof itself** (colprof.c passes literal 1.0) and is
  likewise ignored here;
* ``-u``/``-ua``/``-uc`` error for output data in colprof; ``-u <scale>``
  scales the media white point; ``-R`` clips white Y ≤ 1 and negatives;
* ``-ni``/``-np`` disable the input curves, ``-no`` the output curves,
  ``-nc`` skips the embedded ``.ti3`` (targ/DevD/CIED);
* ``-Z`` sets the header attribute bits / default rendering intent;
* ``-A``/``-M`` become the dmnd/dmdd tags (colprof's device-ID tags).
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
from workflow.profile_engine.pcs import codec_for
from workflow.profile_engine.ti3_data import Ti3Measurement, read_ti3


class EngineError(RuntimeError):
    """A build failure with a user-facing message."""


# colprof's -q table-size contract (profout.c): quality index 0..3 = l/m/h/u.
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

# ICC header deviceAttributes bits (ICC.1): bit0 transparency, bit1 matte,
# bit2 negative, bit3 black & white. colprof -Z t/m/n/b sets the same bits.
_Z_ATTR_BITS = {"t": 1 << 0, "m": 1 << 1, "n": 1 << 2, "b": 1 << 3}
_Z_INTENT = {"p": 0, "r": 1, "s": 2, "a": 3}

# colprof's own refusal for matrix/gamma types on printer data (colprof.c).
_CLUT_ONLY_MSG = ("Output profile can only be a cLUT algorithm — "
                  "matrix/gamma profile types apply to display and input "
                  "devices (Argyll colprof refuses them for printer "
                  "measurements too).")


def _fit_lambda(grid: int) -> float:
    if grid in _FIT_LAMBDA_BY_GRID:
        return _FIT_LAMBDA_BY_GRID[grid]
    return max(0.005, 0.15 * ((grid - 1) / 32.0) ** 3)


@dataclass
class BuildSettings:
    quality: str = "m"                       # colprof -q (l/m/h/u)
    b2a_quality: str = ""                    # colprof -b; "" = same as -q
    algorithm: str = "l"                     # colprof -a: l or x (cLUT only)
    description: str | None = None           # default: the output file stem
    copyright: str = "Created with ChromIQ"
    manufacturer: str = ""                   # colprof -A → dmnd
    model: str = ""                          # colprof -M → dmdd
    ink_limit: float | None = None           # percent; None = from the .ti3
    smoothing: float = 0.5                   # colprof -r avgdev (percent)
    curve_rounds: int = 2
    no_input_shaper: bool = False            # colprof -ni / -np
    no_output_shaper: bool = False           # colprof -no
    embed_ti3: bool = True                   # False = colprof -nc
    wp_scale: float | None = None            # colprof -u <scale>
    clip_primaries: bool = False             # colprof -R
    z_attributes: str = ""                   # colprof -Z letters (m/t/n/b)
    z_default_intent: str = ""               # colprof -Z p/r/s/a
    # Gamut mapping (colprof -s/-S; ChromIQ passes ClayRGB by default, #121)
    source_gamut: Path | str | None = None
    perc_src_colorimetric: bool = False      # colprof -nP
    sat_src_colorimetric: bool = False       # colprof -nS
    inverse_gamut_a2b: bool = False          # colprof -nI
    perc_intent: str = ""                    # colprof -t
    sat_intent: str = ""                     # colprof -T
    src_viewing: str = ""                    # colprof -c
    dst_viewing: str = ""                    # colprof -d
    # Spectral computation (colprof -i/-o/-f) — needs SPEC_* data in the .ti3
    illuminant: str = ""
    observer: str = ""
    fwa: bool = False
    fwa_illum: str = ""
    # Argyll binaries directory — lets the mapped tables match colprof's
    # rendering exactly via the arm's-length oracle (gamut_map).
    argyll_bin: Path | str | None = None
    timestamp: datetime | None = None        # fixed → byte-reproducible
    progress: Callable[[str], None] | None = None
    # Gamut-mapping engine for the perceptual/saturation B2A tables:
    #   "fast"     — ChromIQ's built-in Python mapper (colprof's algorithm,
    #                validated against Argyll; a few seconds).
    #   "argyll"   — the bundled chromiq-gammap helper running Argyll's REAL
    #                gamut mapper (bit-exact; a little slower). Falls back to
    #                "fast" if the helper binary isn't present.
    #   "accurate" — bit-exact mapping PLUS the maximum-accuracy pipeline:
    #                duplicate-patch white/black averaging, cross-validated
    #                smoothing, outlier-robust fitting, boundary-aware
    #                inversion, measured extra-ink hues, Euclidean ink-limit
    #                projection, hue-preserving clipping and a denser
    #                multi-ink gamut shell. Slower; same option surface.
    gammap_mode: str = "fast"
    # Internal lever for the Python mapper: exact triangle geometry vs the
    # fast sampled-table surfaces. Retained for reference/fallback; not
    # exposed in the UI now that "argyll" is the accurate option.
    gammap_exact_geometry: bool = False


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
    # ΔE2000 companions to the ΔE76 fit statistics (perceptually weighted).
    fit_median_de00: float = 0.0
    fit_p95_de00: float = 0.0


def _emit(settings: BuildSettings, msg: str) -> None:
    if settings.progress is not None:
        settings.progress(msg)


# Ordered build stages → target percentage. Each progress message is matched
# by prefix (messages may carry format args), and the reported percentage
# only ever moves forward, so the number is monotonic even if a stage is
# skipped. Gamut mapping and the saturation table dominate the run time, so
# they get the widest spans. Unmatched messages keep the current percentage.
_STAGE_PCT: list[tuple[str, int]] = [
    ("Reading the measurement", 2),
    ("Computing colorimetry", 4),
    ("Fitting the printer model", 8),
    ("Inverting the model", 14),
    ("Anchoring the rendering", 18),
    ("Writing the profile", 20),
    ("Building the perceptual and saturation", 24),
    ("Gamut mapping: reading the source", 26),
    ("Gamut mapping: measuring the printer", 30),
    ("Gamut mapping: preparing colour surfaces", 40),
    ("Gamut mapping: matching source colours", 46),
    ("Gamut mapping: smoothing", 54),
    ("Gamut mapping: fine-tuning", 62),
    ("Gamut mapping: building the final colour table", 74),
    ("Saturation table: matching colprof", 78),
    ("Saturation table: fitting", 92),
]


class _PercentProgress:
    """Wraps the user's progress callback and prefixes each line with a
    monotonic percentage (``"42% · …"``), so the output field shows how
    far along the build is. The progress bar keeps its own pulse."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._pct = 0

    def __call__(self, msg: str) -> None:
        for prefix, pct in _STAGE_PCT:
            if msg.startswith(prefix) and pct > self._pct:
                self._pct = pct
                break
        if self._inner is not None:
            self._inner(f"{self._pct}% · {msg}")


def build_profile(ti3_path: Path | str, out_path: Path | str,
                  settings: BuildSettings | None = None) -> BuildResult:
    """Build an ICC printer profile from a measured chart.

    Wraps the build so every progress line is prefixed with a monotonic
    percentage; the original callback is restored afterwards.
    """
    settings = settings or BuildSettings()
    orig_progress = settings.progress
    settings.progress = _PercentProgress(orig_progress)
    try:
        return _build_profile_impl(ti3_path, out_path, settings)
    finally:
        settings.progress = orig_progress


def _build_profile_impl(ti3_path: Path | str, out_path: Path | str,
                        settings: BuildSettings) -> BuildResult:
    if settings.quality not in _QUALITY_INDEX:
        raise EngineError(f"Unknown quality {settings.quality!r} "
                          "(expected one of l, m, h, u).")
    if settings.algorithm not in ("l", "x"):
        raise EngineError(_CLUT_ONLY_MSG)
    q = _QUALITY_INDEX[settings.quality]
    qb = _QUALITY_INDEX.get(settings.b2a_quality, q)
    codec = codec_for(settings.algorithm)

    _emit(settings, "Reading the measurement…")
    meas = read_ti3(ti3_path)
    n = meas.n_channels
    if not 1 <= n <= 15:
        raise EngineError(f"{n} device channels — outside the ICC range.")
    if n == 1:
        # Not a parity gap: shipping colprof rejects grayscale output data
        # too — its mono support sits behind #ifdef IMP_MONO and is not
        # compiled in ("unhandled color representation", verified live).
        raise EngineError(
            "Single-channel (grayscale) measurements can't be built into a "
            "profile — Argyll colprof doesn't support these either.")

    if settings.illuminant or settings.observer or settings.fwa:
        from workflow.profile_engine.spectral import apply_spectral
        _emit(settings, "Computing colorimetry from the spectral data…")
        apply_spectral(meas, illuminant=settings.illuminant,
                       observer=settings.observer, fwa=settings.fwa,
                       fwa_illum=settings.fwa_illum)

    if settings.clip_primaries:
        # colprof -R: white Y restricted to ≤ 1.0, values clipped positive.
        meas.xyz = np.clip(meas.xyz, 0.0, None)
        _invalidate_bases(meas)
        wy = meas.media_white_xyz[1]
        if wy > 100.0:
            meas.xyz = meas.xyz * (100.0 / wy)
            _invalidate_bases(meas)
    if settings.wp_scale is not None:
        # colprof -u <scale>: scale the media white point.
        meas.xyz = meas.xyz.copy()
        meas.xyz[meas.white_index] *= float(settings.wp_scale)
        _invalidate_bases(meas)

    accurate = settings.gammap_mode == "accurate"
    if accurate:
        # Maximum-accuracy mode: unbiased media white/black from duplicate
        # patches (after the -R/-u mutations above), measured extra-ink hues.
        meas.average_endpoints()
    extra_hues = meas.extra_ink_hues() if accurate else None
    # Measured black L* anchors the GCR locus in accurate mode (shadow-
    # banding fix); None keeps the parity locus.
    black_l = float(meas.lab_relative[meas.black_index, 0]) \
        if accurate else None

    a2b_grid = (_A2B_GRID_34 if n >= 4 else _A2B_GRID_23)[q]
    b2a_grid = _B2A_GRID[qb]
    # Keep very high-dimensional grids inside sane memory: grid**n nodes.
    while a2b_grid ** n > 2_000_000 and a2b_grid > 3:
        a2b_grid -= 2

    _emit(settings, f"Fitting the printer model ({len(meas.device)} patches, "
                    f"grid {a2b_grid})…")
    # colprof -r: reading average deviation (default 0.5%); the smoothing
    # weight scales with its square (rspl semantics — more measurement noise
    # wants proportionally-squared more smoothing).
    lam = _fit_lambda(a2b_grid) * (max(settings.smoothing, 0.01) / 0.5) ** 2
    curve_rounds = 0 if settings.no_input_shaper else settings.curve_rounds
    if accurate:
        from workflow.profile_engine.accuracy import fit_forward_model_accurate
        model, outliers, _lam_used = fit_forward_model_accurate(
            meas.device, meas.lab_relative, grid=a2b_grid, base_lam=lam,
            curve_rounds=curve_rounds,
            progress=lambda m: _emit(settings, m))
        if len(outliers):
            ids = ", ".join(str(i + 1) for i in outliers[:8])
            more = "" if len(outliers) <= 8 else f" (+{len(outliers) - 8})"
            _emit(settings,
                  f"{len(outliers)} patch(es) disagree strongly with the "
                  f"model and were down-weighted — rows {ids}{more}. "
                  f"Consider remeasuring them.")
    else:
        model = fit_forward_model(meas.device, meas.lab_relative,
                                  grid=a2b_grid, lam=lam,
                                  curve_rounds=curve_rounds)
    fit_res = np.linalg.norm(model.predict(meas.device) - meas.lab_relative,
                             axis=1)

    _emit(settings, f"Inverting the model (B2A grid {b2a_grid})…")
    ink_limit = settings.ink_limit if settings.ink_limit is not None \
        else meas.ink_limit
    # Multi-ink: anchor the neutral rendering + K separation in colprof's
    # behaviour via a synthetic CMYK proxy (colprof can build THAT).
    anchor = None
    if n >= 5 and settings.argyll_bin is not None:
        from workflow.profile_engine.gamut_map import (OracleUnavailable,
                                                       fit_multiink_anchor)
        try:
            anchor = fit_multiink_anchor(
                model, meas, settings.source_gamut or "", settings,
                settings.argyll_bin, settings.progress)
        except OracleUnavailable as exc:
            _emit(settings, f"Using the engine's own rendering ({exc}).")
    node_lab = codec.node_lab(b2a_grid)
    dev_clut, residual = b2a_mod.build_b2a_clut(
        model, b2a_grid, channel_letters=meas.channel_letters,
        is_additive=meas.is_additive, ink_limit=ink_limit,
        node_lab=node_lab, k_prior=anchor, accurate=accurate,
        extra_hues=extra_hues, black_l=black_l)
    # refine_b2a_clut returns *curve-space* values — written straight into
    # the CLUT, with the inverse shaper curves as B2A output tables.
    dev_clut_shaped = b2a_mod.refine_b2a_clut(
        model, dev_clut, residual, b2a_grid,
        ink_limit=ink_limit, is_additive=meas.is_additive,
        channel_letters=meas.channel_letters,
        node_lab=node_lab, lab_to01=codec.lab_to01, k_prior=anchor,
        accurate=accurate, extra_hues=extra_hues, black_l=black_l)
    in_gamut = residual <= 1.0

    _emit(settings, "Writing the profile…")
    entries_a2b = _A2B_ENTRIES[q]
    entries_b2a = _B2A_ENTRIES[qb]
    a2b = icw.make_mft2(
        n, 3, a2b_grid, codec.encode(model.clut_lab()),
        in_tables=icw.curves_to_tables(model.curves, entries_a2b),
        out_tables=np.tile(icw._identity_table(entries_a2b), (3, 1)))
    if settings.no_output_shaper:
        b2a_col = icw.make_mft2(
            3, n, b2a_grid,
            icw.device_to_u16(model.unshape_device(dev_clut_shaped)),
            in_tables=np.tile(icw._identity_table(entries_b2a), (3, 1)),
            out_tables=np.tile(icw._identity_table(entries_b2a), (n, 1)))
    else:
        inv = b2a_mod.inverse_curves(model.curves)
        b2a_col = icw.make_mft2(
            3, n, b2a_grid, icw.device_to_u16(dev_clut_shaped),
            in_tables=np.tile(icw._identity_table(entries_b2a), (3, 1)),
            out_tables=icw.curves_to_tables(inv, entries_b2a))
    gamt = icw.make_mft2(
        3, 1, b2a_grid,
        (np.clip(residual, 0, 128)[:, None] / 128 * 0xFFFF).round())

    # The colorimetric tables own the bytes; the other intents alias them
    # (colprof's default A2B0/1/2 are byte-identical — verified) and mapped
    # builds replace the aliases without ever touching the colorimetric data.
    luts: dict[str, bytes | str] = {
        "A2B1": a2b, "A2B0": "A2B1", "A2B2": "A2B1",
        "B2A1": b2a_col, "gamt": gamt,
    }
    perceptual_distinct = False
    if settings.source_gamut is not None:
        from workflow.profile_engine.gamut_map import build_mapped_b2a
        _emit(settings, "Building the perceptual and saturation tables…")
        mapped = build_mapped_b2a(
            model, meas, b2a_grid, Path(settings.source_gamut),
            channel_letters=meas.channel_letters,
            is_additive=meas.is_additive, ink_limit=ink_limit,
            entries=entries_b2a, codec=codec, settings=settings,
            a2b_grid=a2b_grid, a2b_entries=entries_a2b, anchor=anchor)
        luts.update(mapped)
        perceptual_distinct = "B2A0" in mapped
    if "B2A0" not in luts:
        luts["B2A0"] = "B2A1"
    if "B2A2" not in luts:
        luts["B2A2"] = "B2A1"

    out = Path(out_path)
    attributes = 0
    for letter in settings.z_attributes:
        attributes |= _Z_ATTR_BITS.get(letter, 0)
    spec = icw.ProfileSpec(
        n_channels=n,
        color_rep=meas.color_rep,
        description=settings.description or out.stem,
        copyright=settings.copyright,
        manufacturer=settings.manufacturer,
        model=settings.model,
        wtpt=tuple(meas.media_white_xyz / 100.0),
        bkpt=tuple(meas.black_xyz / 100.0),
        targ=meas.text if settings.embed_ti3 else None,
        pcs=codec.signature,
        rendering_intent=_Z_INTENT.get(settings.z_default_intent, 1),
        attributes=attributes,
        timestamp=settings.timestamp,
    )
    icw.write_profile(out, spec, luts)

    gam_res = residual[in_gamut]
    from workflow.profile_engine.metrics import delta_e_2000
    fit_res00 = delta_e_2000(model.predict(meas.device), meas.lab_relative)
    if accurate:
        _emit(settings, f"Model fit (perceptual ΔE2000): median "
                        f"{float(np.median(fit_res00)):.2f}, 95% "
                        f"{float(np.percentile(fit_res00, 95)):.2f}.")
    return BuildResult(
        icc_path=out, n_channels=n, color_rep=meas.color_rep,
        a2b_grid=a2b_grid, b2a_grid=b2a_grid,
        fit_median_de=float(np.median(fit_res)),
        fit_p95_de=float(np.percentile(fit_res, 95)),
        b2a_ingamut_median_de=float(np.median(gam_res)) if len(gam_res) else 0.0,
        oog_fraction=float(1.0 - in_gamut.mean()),
        perceptual_distinct=perceptual_distinct,
        model=model, measurement=meas,
        fit_median_de00=float(np.median(fit_res00)),
        fit_p95_de00=float(np.percentile(fit_res00, 95)))


def _invalidate_bases(meas: Ti3Measurement) -> None:
    """Drop the cached colour bases after mutating ``meas.xyz``."""
    for attr in ("xyz_relative", "lab_relative", "lab_absolute",
                 "media_white_xyz", "white_index", "black_index",
                 "black_xyz"):
        meas.__dict__.pop(attr, None)
