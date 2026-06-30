"""Builds and runs ``collink`` to create an ICC **device-link** profile.

A device-link bakes a *source profile → destination profile* conversion, plus
its gamut mapping, into a single fixed transform. Unlike the normal ChromIQ
workflow (which prints charts raw and lets colprof's stock rendering intents do
the mapping at conversion time), a device-link gives finer, *repeatable* control
— a richer set of gamut-mapping intents, CIECAM02 viewing conditions, an
optional per-image source gamut, and abstract-profile tweaks — and the result is
applied later in Photoshop ("Convert to Profile") or a RIP.

This module mirrors :mod:`workflow.profcheck_runner` / the other Argyll runners:
a :class:`CollinkParams` dataclass, a :class:`CollinkRunner` that builds the CLI
args and runs them through the singleton :class:`~core.argyll_runner.ArgyllRunner`
(single-process, so it never collides with another operation), and structured
error parsing.

**ICC version:** Argyll's engine is v2-only — `collink` prints *"ICC V4 not
supported!"* and aborts on a v4 input. The caller is responsible for converting a
v4 source to v2 first (see :mod:`workflow.icc_convert`); this runner only flags it
if a v4 profile slips through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

from core.logger import get_logger

log = get_logger(__name__)


# Structured error patterns for collink (Argyll 3.5.0 link/collink.c). Each
# pairs a regex with a stable key and a plain-language explanation the dialog can
# surface. Keys stay English (dict keys); the dialog wraps the text in tr().
_COLLINK_ERROR_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Emitted (as a warning, then a fatal "Unable to locate usable conversion")
    # whenever any input is ICC v4 — Argyll's icclib can't read v4.
    (re.compile(r"ICC V4 not supported", re.IGNORECASE),
     "icc_v4",
     "One of the profiles is ICC version 4, which the ArgyllCMS engine can't "
     "read. Convert it to a version-2 profile first (ChromIQ can do this for "
     "standard RGB profiles), then try again."),
    # Generic profile-open failure.
    (re.compile(r"Error\s*-\s*.*[Cc]an'?t open (?:file|profile)\s*'([^']+)'"),
     "open_failed",
     "The profile '{0}' could not be opened. Check that the file exists and is "
     "a valid ICC profile."),
    # Profile lacks the lookup tables collink needs for the chosen direction.
    (re.compile(r"Unable to locate usable conversion"),
     "no_conversion",
     "collink couldn't build a conversion between these two profiles. One of "
     "them may be missing the lookup tables needed for this direction, or be "
     "ICC v4."),
]


@dataclass
class CollinkParams:
    """Inputs for one device-link build. Paths are absolute."""

    src_path: Path                 # source profile (e.g. a working space)
    dst_path: Path                 # destination profile (the printer .icc)
    out_path: Path                 # device-link to write

    # Gamut-mapping mode is always on (-g); these refine it.
    intent: str = "p"              # p/pa/lp/r/s/ms/a/aw/… (collink -i, gamut-map set)
    src_viewcond: str = "mt"       # source CIECAM02 viewing conditions (-c)
    dst_viewcond: str = "pp"       # destination CIECAM02 viewing conditions (-d)
    quality: str = "h"             # l/m/h/u clut resolution (-q)
    black_point_hack: bool = False  # RGB->RGB forced black point (-b)

    # Optional levers.
    src_gamut: Path | None = None  # per-image source gamut (.gam) → -g/-G <file>
    abstract: Path | None = None   # abstract "tweak" profile (-p)
    calibration: Path | None = None  # bake-in calibration curves (.cal) → -a
    lut3d: str = ""                # "" | "c" | "e" | "m" → -3 (3DLUT export)
    inverse_gamut: bool = False    # use inverse-A2B gamut mapping (-G not -g)
    forced_white: bool = False     # forced white-point hack (-w)
    diagnostic: bool = False       # emit gammap.x3d.html diagnostic (-P)

    # ICC identification strings stamped into the link.
    description: str = ""          # -D
    manufacturer: str = ""         # -A
    model: str = ""                # -M
    copyright: str = ""            # -C

    verbose: bool = True


class CollinkRunner:
    def __init__(self, runner: "ArgyllRunner") -> None:
        self._runner = runner
        self._last_log = ""
        self._matched_errors: list[tuple[str, str]] = []

    def run(
        self,
        params: CollinkParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        args = self._build_args(params)
        cwd = params.out_path.parent
        log.info("collink: %s  [cwd=%s]", " ".join(args), cwd)
        self._last_log = ""
        self._matched_errors = []

        def _accumulate(line: str) -> None:
            self._last_log += line + "\n"
            self._scan_line(line)
            on_line(line)

        self._runner.run(
            "collink",
            args,
            cwd,
            on_line=_accumulate,
            on_finish=on_finish,
        )

    def _scan_line(self, line: str) -> None:
        for pattern, key, fmt in _COLLINK_ERROR_PATTERNS:
            m = pattern.search(line)
            if m:
                groups = tuple(g or "" for g in m.groups())
                self._matched_errors.append((key, fmt.format(*groups)))

    def primary_failure(self) -> tuple[str, str] | None:
        return self._matched_errors[0] if self._matched_errors else None

    @property
    def last_log(self) -> str:
        return self._last_log

    # ------------------------------------------------------------------

    def _build_args(self, p: CollinkParams) -> list[str]:
        """Assemble ``collink [options] srcprofile dstprofile linkedprofile``.

        Single-letter value flags use Argyll's attached form (``-qh``, ``-ip``);
        full-string flags (``-D``/``-A``/``-M``/``-C``/``-p``) take the value as
        the next argv. The three profile paths are always last, in order.
        """
        args: list[str] = []
        if p.verbose:
            args.append("-v")

        args.append(f"-q{p.quality}")

        # Gamut Mapping Mode: -g (forward) or -G (inverse outprofile A2B). A bare
        # flag maps against the destination gamut; an attached path maps against
        # that (per-image) source gamut.
        flag = "-G" if p.inverse_gamut else "-g"
        args.append(f"{flag}{p.src_gamut}" if p.src_gamut is not None else flag)

        args.append(f"-i{p.intent}")
        args.append(f"-c{p.src_viewcond}")
        args.append(f"-d{p.dst_viewcond}")

        if p.black_point_hack:
            args.append("-b")
        if p.forced_white:
            args.append("-w")
        if p.diagnostic:
            args.append("-P")
        if p.abstract is not None:
            args += ["-p", str(p.abstract)]
        if p.calibration is not None:
            args += ["-a", str(p.calibration)]
        if p.lut3d:
            args.append(f"-3{p.lut3d}")

        if p.description:
            args += ["-D", p.description]
        if p.manufacturer:
            args += ["-A", p.manufacturer]
        if p.model:
            args += ["-M", p.model]
        if p.copyright:
            args += ["-C", p.copyright]

        args += [str(p.src_path), str(p.dst_path), str(p.out_path)]
        return args
