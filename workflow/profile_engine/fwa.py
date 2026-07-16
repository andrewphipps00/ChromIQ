"""Paper-whitener (FWA/OBA) compensation — colprof's ``-f`` for the engine.

Method per the published approach (G. Gill, *A Practical Approach to
Measuring and Modelling Paper Fluorescence…*, IS&T/SID CIC 2003; also
Argyll's FWA.html):

1. Estimate the media's *FWA-inactive* spectrum from the measured media
   white: a flat-line continuation of the >520 nm region (papers without
   whitener are spectrally flat there; the blue-region excess above that
   base is the whitener's emission under the *instrument's* illuminant).
2. The instrument illuminates with source A (tungsten — i1Pro / ColorMunki
   class instruments); the *target* illuminant carries a different UV load.
   The emission scales with the UV that actually reaches the paper:
   ``r_uv = ∫UV(target)/∫UV(A)`` over 300–400 nm.
3. Ink filters both the UV on the way in and the emitted blue on the way
   out: per sample, the emission is attenuated by the sample's own
   transmittance at the emission wavelengths (≈ reflectance ratio to the
   media white).
4. Corrected reflectance = measured − emission·(1 − r_uv)·attenuation.

Parity is *measured*, not assumed: the engine build with FWA is compared
against ``colprof -f`` on the same spectral measurement, and the option is
only offered where that comparison holds (the test pins it).
"""
from __future__ import annotations

import numpy as np

from workflow.profile_engine.spectral import SpectralError, illuminant_spd
from workflow.profile_engine.ti3_data import Ti3Measurement

# FWA emission region (nm) — whitener re-emits between ~400 and 460 nm;
# the estimation base is the flat region beyond 520 nm.
_EMIT_LO, _EMIT_HI = 400.0, 470.0
_BASE_LO, _BASE_HI = 520.0, 600.0
_UV_LO, _UV_HI = 300.0, 400.0


def _emission_gain(target_illuminant: str, lam_emit: np.ndarray
                   ) -> np.ndarray:
    """Per-emission-wavelength gain of the whitener's *reflectance-factor*
    excess when the illuminant changes from the instrument's tungsten (A)
    to the target.

    Two factors compose it: the emitted energy scales with the UV the
    illuminant delivers (``k_uv = ∫S_t·U / ∫S_A·U`` over the excitation
    band), and the *reflectance factor* the instrument reports divides the
    emitted energy by the illuminant's own power at the emission wavelength
    (``S_A(λ)/S_t(λ)``) — A is blue-weak and D50 blue-strong, so the two
    factors partly cancel; a scalar UV ratio alone overshoots (measured)."""
    uv = np.linspace(_UV_LO, _UV_HI, 101)
    s_a_uv = illuminant_spd("A", uv)
    s_t_uv = illuminant_spd(target_illuminant, uv)
    s_a_e = illuminant_spd("A", lam_emit)
    s_t_e = illuminant_spd(target_illuminant, lam_emit)
    k_uv = float(s_t_uv.sum() / max(s_a_uv.sum(), 1e-9))
    return k_uv * s_a_e / np.clip(s_t_e, 1e-9, None)


# Instruments whose illumination carries UV (tungsten source A) — the exact
# list Argyll accepts for FWA (spectro/insttypes.c inst_illuminant): the
# ColorMunki family explicitly has "No U.V." and is refused, matching
# colprof's "Instrument doesn't have an FWA illuminent" error.
_UV_INSTRUMENTS = ("i1 pro", "spectrolino", "spectroscan", "dtp20", "dtp22",
                   "dtp41", "dtp51")


def instrument_supports_fwa(instrument: str) -> bool:
    name = (instrument or "").lower()
    return any(tok in name for tok in _UV_INSTRUMENTS)


def fwa_corrected_reflectance(meas: Ti3Measurement, *,
                              target_illuminant: str = "D50") -> np.ndarray:
    """(N, bands) reflectance corrected for the whitener's behaviour under
    the target illuminant instead of the instrument's tungsten source."""
    if meas.spectral is None or meas.wavelengths is None:
        raise SpectralError(
            "Paper-whitener compensation needs spectral measurement data.")
    instrument = meas.keywords.get("TARGET_INSTRUMENT", "")
    if not instrument_supports_fwa(instrument):
        raise SpectralError(
            f"The measuring instrument ({instrument or 'unknown'}) doesn't "
            "illuminate with UV, so the paper whitener can't be measured — "
            "the same limit applies in Argyll colprof. Instruments with a "
            "tungsten light source (i1 Pro family, Spectrolino, DTP-series) "
            "support this.")
    lam = meas.wavelengths
    refl = meas.spectral.astype(float)
    scale = 100.0 if np.nanmax(refl) > 2.0 else 1.0
    r = refl / scale

    white = r[meas.white_index]
    base_sel = (lam >= _BASE_LO) & (lam <= _BASE_HI)
    emit_sel = (lam >= _EMIT_LO) & (lam <= _EMIT_HI)
    base_level = float(white[base_sel].mean())
    # Whitener emission under the instrument illuminant: blue-region excess
    # of the media white over its flat-line base (≥0 — papers darker in the
    # blue than their base have tint, not whitener).
    emission = np.zeros_like(lam)
    emission[emit_sel] = np.clip(white[emit_sel] - base_level, 0.0, None)

    if emission.max() <= 1e-4:
        return refl                       # no measurable whitener

    gain = np.ones_like(lam)
    gain[emit_sel] = _emission_gain(target_illuminant, lam[emit_sel])
    # Per-sample attenuation of the emission by the overprinted ink: the
    # emitted blue passes the ink once on the way out, the exciting UV once
    # on the way in — approximated by the sample/media reflectance ratio at
    # the emission wavelengths (bounded 0..1).
    ratio = np.clip(
        r[:, emit_sel].mean(1) / max(white[emit_sel].mean(), 1e-9), 0.0, 1.0)
    corrected = r + (gain[None, :] - 1.0) * ratio[:, None] * emission[None, :]
    return np.clip(corrected, 0.0, None) * scale
