"""ChromIQ profile engine (#122) — a pure-Python ICC printer-profile builder.

An **optional alternative to Argyll's colprof, never the default**. colprof
remains the engine for everything it covers; this package's unique value is
what colprof structurally cannot do: CMYK+N output profiles (colprof handles
Gray/RGB/CMY/CMYK only). Clean-room throughout — behaviour was measured
against real colprof output, no Argyll code was copied (the layout-engine ⇄
printtarg precedent).

Layering (pure functions, numpy-only, no Qt):

* :mod:`icc_writer`   — ICC v2 ``mft2`` byte writer (tags, aliases, header)
* :mod:`ti3_data`     — COLOR_REP-agnostic ``.ti3`` reader (device n-D + Lab)
* :mod:`forward_model`— regularised grid fit device→Lab with input shapers
* :mod:`b2a`          — Lab-grid → device inversion (Gauss–Newton + ink policy)
* :mod:`gamut`        — gamut surface mesh, volume, out-of-gamut distance
* :mod:`cam02`        — CIECAM02 forward/inverse (perceptual mapping space)
* :mod:`gamut_map`    — perceptual/saturation gamut mapping (guide + warp)
* :mod:`builder`      — ``build_profile()`` orchestration
"""
from workflow.profile_engine.builder import (  # noqa: F401
    BuildSettings,
    EngineError,
    build_profile,
)
