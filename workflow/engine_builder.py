"""Qt adapter for the ChromIQ profile engine (#122) — Build-Profile side.

Wraps :func:`workflow.profile_engine.build_profile` in a ``QThread`` with the
same call surface the colprof :class:`~workflow.profile_builder.ProfileBuilder`
offers (``build(params, on_line, on_finish)`` + ``expected_icc_path``), so
``tab_profile`` can route a build to either engine without special-casing the
UI flow. The engine runs in-process — the worker thread keeps the UI alive
during the numeric fit (seconds for RGB, ~1 min for a mapped 6-channel
build).

Loss-free gating (#122 doctrine): :func:`engine_support` says whether the
engine fully covers a requested build. Anything it can't do loss-free —
spectral/FWA options, algorithm overrides, exotic gamut sources, custom
smoothing, extra CLI args — keeps the build on colprof; the caller shows the
reason. Multi-ink measurements are the reverse case: colprof cannot build
them at all, the engine is the only path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QThread, pyqtSignal

from core.i18n import tr
from core.logger import get_logger

if TYPE_CHECKING:
    from workflow.profile_builder import ProfileParams

log = get_logger(__name__)

# Device representations colprof itself accepts (profout.c) — everything
# else (CMYKOG, CMYKcm, …) is engine-only territory.
_COLPROF_REPS = {"RGB", "iRGB", "CMYK", "CMY", "K", "W", "GRAY"}


def ti3_device_rep(ti3_path: Path | str) -> str:
    """The device part of the file's COLOR_REP (``""`` when unreadable)."""
    try:
        head = Path(ti3_path).read_text(errors="replace")[:8000]
    except OSError:
        return ""
    m = re.search(r'^COLOR_REP\s+"([^"]+)"', head, re.M)
    return m.group(1).split("_")[0] if m else ""


def is_multi_ink(ti3_path: Path | str) -> bool:
    rep = ti3_device_rep(ti3_path)
    return bool(rep) and rep not in _COLPROF_REPS


def engine_support(params: "ProfileParams") -> tuple[bool, str]:
    """Can the engine run this exact build loss-free?

    Returns ``(supported, reason)`` — ``reason`` names the first option that
    needs colprof, in user-facing language.
    """
    checks: list[tuple[bool, str]] = [
        (params.algorithm in ("", "l"),
         tr("profile type overrides (-a) — the engine builds cLUT profiles")),
        (not params.fwa_enabled and not params.illuminant
         and not params.observer and not params.fwa_illum,
         tr("spectral options (illuminant, observer, FWA)")),
        (not params.extra_args.strip(),
         tr("additional command-line parameters")),
        (abs(params.smoothing - 0.5) < 1e-9,
         tr("a custom smoothing (-r) value")),
        (abs(params.dark_emphasis - 1.0) < 1e-9,
         tr("dark-region emphasis (-V)")),
        (not params.no_input_shaper and not params.no_output_shaper,
         tr("disabled shaper curves (-ni / -no)")),
        (not params.b2a_quality or params.b2a_quality == params.quality,
         tr("a separate B2A quality (-b)")),
        (not params.src_viewing_cond and not params.dst_viewing_cond,
         tr("viewing-condition overrides (-c / -d)")),
        # -s and -S are both covered: the engine always derives perceptual
        # AND saturation tables from the one source profile.
        (not params.inv_gamut_map
         and not params.perc_intent and not params.sat_intent
         and not params.no_perc_gamut and not params.no_sat_gamut,
         tr("extended gamut-mapping options")),
        (not params.wp_mode and not params.clip_primaries,
         tr("input white-point options (-u / -R)")),
        (not params.z_surface and not params.z_media_type
         and not params.z_polarity and not params.z_color_mode
         and not params.z_default_intent,
         tr("ICC media-attribute overrides (-Z)")),
    ]
    for ok, what in checks:
        if not ok:
            return False, what
    gamut_source = params.gamut_src or params.gamut_sat_src
    if gamut_source:
        # Any RGB/CMYK profile works — the surface is sampled live from the
        # file (littleCMS reads v2 and v4). Only unreadable files gate.
        from workflow.profile_engine.gamut_map import (
            GamutSourceError, source_surface_from_profile)
        try:
            source_surface_from_profile(gamut_source, mesh=5)
        except GamutSourceError as exc:
            return False, tr(
                "this gamut source profile ({reason})").format(reason=exc)
    return True, ""


class _EngineThread(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(int, str)

    def __init__(self, ti3_path: Path, out_path: Path, settings, parent=None):
        super().__init__(parent)
        self._ti3 = ti3_path
        self._out = out_path
        self._settings = settings

    def run(self) -> None:  # noqa: D102 — QThread worker
        from workflow.profile_engine import build_profile
        self._settings.progress = self.line.emit   # queued across threads
        try:
            res = build_profile(self._ti3, self._out, self._settings)
        except Exception as exc:            # noqa: BLE001 — surfaced to UI
            log.exception("engine build failed")
            self.done.emit(1, str(exc))
            return
        self.line.emit(tr(
            "Model fit at the measured patches: median {med:.2f} ΔE, "
            "95% {p95:.2f} ΔE.").format(med=res.fit_median_de,
                                        p95=res.fit_p95_de))
        if res.perceptual_distinct:
            self.line.emit(tr(
                "Perceptual and saturation tables built from the gamut "
                "source (approximate — the colorimetric intents are the "
                "reference)."))
        self.done.emit(0, "")


class EngineProfileBuilder:
    """ProfileBuilder-compatible front end for the in-process engine."""

    def __init__(self) -> None:
        self._thread: _EngineThread | None = None
        self._last_error: str = ""

    @property
    def is_running(self) -> bool:
        t = self._thread
        if t is None:
            return False
        try:
            return t.isRunning()
        except RuntimeError:      # C++ side already deleted (deleteLater)
            self._thread = None
            return False

    def expected_icc_path(self, params: "ProfileParams") -> Path:
        base = params.ti3_path
        return base.with_suffix(".icc")

    def build(self, params: "ProfileParams",
              on_line: Callable[[str], None],
              on_finish: Callable[[int], None]) -> None:
        from workflow.profile_engine import BuildSettings
        settings = BuildSettings(
            quality=params.quality or "m",
            description=params.description or None,
            copyright=params.copyright or "Created with ChromIQ",
            source_gamut=params.gamut_src or params.gamut_sat_src or None,
        )
        out = self.expected_icc_path(params)
        self._last_error = ""
        self._thread = t = _EngineThread(params.ti3_path, out, settings)
        t.line.connect(on_line)

        def _finished(code: int, err: str) -> None:
            self._last_error = err
            self._thread = None
            if err:
                on_line(tr("[ERROR] {msg}").format(msg=err))
            on_finish(code)

        t.done.connect(_finished)
        t.finished.connect(t.deleteLater)
        on_line(tr("Building with the ChromIQ profile engine (beta)…"))
        t.start()

    # ProfileBuilder-parity helpers the finish path may consult ------------
    def primary_failure(self) -> tuple[str, str] | None:
        return ("engine", self._last_error) if self._last_error else None

    def captured_warnings(self) -> list[tuple[str, str]]:
        return []
