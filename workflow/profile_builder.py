"""Orchestrates colprof for ICC profile creation and installation."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)


def _profile_dir() -> Path:
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return windir / "System32" / "spool" / "drivers" / "color"
    return Path.home() / "Library" / "ColorSync" / "Profiles"


@dataclass
class ProfileParams:
    ti3_path: Path
    description: str = ""
    algorithm: str = "l"
    quality: str = "m"
    b2a_quality: str = ""
    smoothing: float = 0.5
    dark_emphasis: float = 1.0
    gamut_src: str = ""
    manufacturer: str = ""
    model: str = ""
    copyright: str = ""
    no_input_shaper: bool = False
    no_output_shaper: bool = False
    extra_args: str = ""
    # Color science
    illuminant: str = ""
    observer: str = ""
    fwa_enabled: bool = False
    fwa_illum: str = ""
    # ICC media attributes & default intent (-Z)
    z_surface: str = ""        # "" = glossy (default), "m" = matte
    z_media_type: str = ""     # "" = reflective (default), "t" = transparent
    z_polarity: str = ""       # "" = positive (default), "n" = negative
    z_color_mode: str = ""     # "" = color (default), "b" = black & white
    z_default_intent: str = "" # "" = not set, "p"/"r"/"s"/"a"
    # Gamut mapping extended
    gamut_sat_src: str = ""
    no_perc_gamut: bool = False
    no_sat_gamut: bool = False
    inv_gamut_map: bool = False
    perc_intent: str = ""
    sat_intent: str = ""
    # Curve / embedding flags
    no_grid_pos: bool = False
    no_embedded_data: bool = False


class ProfileBuilder:
    def __init__(self, runner: "ArgyllRunner") -> None:
        self._runner = runner
        self._last_log: str = ""

    # ------------------------------------------------------------------

    def build(
        self,
        params: ProfileParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        args = self._build_args(params)
        cwd  = params.ti3_path.parent
        log.info("colprof: %s  [cwd=%s]", " ".join(args), cwd)
        self._last_log = ""

        def _accumulate(line: str) -> None:
            self._last_log += line + "\n"
            on_line(line)

        self._runner.run(
            "colprof",
            args,
            cwd,
            on_line=_accumulate,
            on_finish=on_finish,
        )

    def install_profile(self, icc_path: Path) -> Path:
        """Copy .icc file to the system ICC profile folder. Returns the installed path."""
        profile_dir = _profile_dir()
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            log.warning("Cannot create profile dir %s — elevation may be required", profile_dir)
            raise
        dest = profile_dir / icc_path.name
        shutil.copy2(icc_path, dest)
        log.info("Profile installed: %s", dest)
        return dest

    def sanity_check(self, icc_path: Path, log_output: str = "") -> list[str]:
        """Return list of warning strings; empty = pass."""
        issues: list[str] = []

        if not icc_path.exists():
            issues.append("Profile file not found.")
            return issues

        size = icc_path.stat().st_size
        if size < 1000:
            issues.append(f"Profile is suspiciously small ({size} bytes).")
        if size > 20_000_000:
            issues.append(f"Profile is very large ({size / 1e6:.1f} MB). Check quality setting.")

        combined = log_output or self._last_log
        for pattern, msg in [
            (r"delta E .{0,10}> 5",      "High ΔE values detected — consider more patches."),
            (r"Warning.*out of gamut",    "Out-of-gamut warnings in measurements."),
            (r"Profile creation failed", "colprof reported a failure."),
        ]:
            if re.search(pattern, combined, re.IGNORECASE):
                issues.append(msg)

        return issues

    def expected_icc_path(self, params: ProfileParams) -> Path:
        base = params.ti3_path.with_suffix("")
        for ext in (".icc", ".icm"):
            candidate = base.with_suffix(ext)
            if candidate.exists():
                return candidate
        return base.with_suffix(".icc")

    # ------------------------------------------------------------------

    def _build_args(self, p: ProfileParams) -> list[str]:
        args: list[str] = []
        desc = p.description or p.ti3_path.stem
        args += ["-D", desc]
        args.append(f"-a{p.algorithm}")
        args.append(f"-q{p.quality}")
        if p.b2a_quality:
            args.append(f"-b{p.b2a_quality}")
        if abs(p.smoothing - 0.5) > 0.01:
            args += [f"-r{p.smoothing:.2f}"]
        if abs(p.dark_emphasis - 1.0) > 0.01:
            args += [f"-V{p.dark_emphasis:.1f}"]
        if p.gamut_src:
            args += ["-s", p.gamut_src]
        if p.manufacturer:
            args += ["-A", p.manufacturer]
        if p.model:
            args += ["-M", p.model]
        if p.copyright:
            args += ["-C", p.copyright]
        if p.illuminant:
            args += ["-i", p.illuminant]
        if p.observer:
            args += ["-o", p.observer]
        if p.fwa_enabled:
            args.append(f"-f{p.fwa_illum}" if p.fwa_illum else "-f")
        z_attrs = "".join(filter(None, [p.z_surface, p.z_media_type, p.z_polarity, p.z_color_mode]))
        if z_attrs:
            args += ["-Z", z_attrs]
        if p.z_default_intent:
            args += ["-Z", p.z_default_intent]
        if p.gamut_sat_src:
            args += ["-S", p.gamut_sat_src]
        if p.no_perc_gamut:
            args.append("-nP")
        if p.no_sat_gamut:
            args.append("-nS")
        if p.inv_gamut_map:
            args.append("-nI")
        if p.perc_intent:
            args.append(f"-t{p.perc_intent}")
        if p.sat_intent:
            args.append(f"-T{p.sat_intent}")
        if p.no_grid_pos:
            args.append("-np")
        if p.no_embedded_data:
            args.append("-nc")
        if p.no_input_shaper:
            args.append("-ni")
        if p.no_output_shaper:
            args.append("-no")
        if p.extra_args:
            args += shlex.split(p.extra_args)
        # Base name without extension
        args.append(str(p.ti3_path.with_suffix("")))
        return args
