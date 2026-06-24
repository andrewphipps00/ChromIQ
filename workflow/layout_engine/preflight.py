"""Pre-flight checks — "will this chart read correctly?".

Two layers:

* **Headless checks** (no ArgyllCMS needed) that power the in-app green/red
  badge before printing: patch-size reliability floor, inter-patch contrast,
  and that the layout actually fits.  These protect Design priority #1.
* An optional **Argyll-backed round-trip** (``fakeread`` → ``colprof``) for
  CI/dev to confirm the ``.ti2`` is structurally sound end to end.

The headless layer is Qt-free and unit-testable; the round-trip is skipped when
the binaries aren't present.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .contrast import LOW_CONTRAST_THRESHOLD
from .geometry import Layout
from .instruments import Geom

# Smallest reliable patch edge (printtarg enforces 6 mm). A printer/ink-spread
# allowance can raise this per the issue's reliability-floor requirement.
MIN_PATCH_MM = 6.0


@dataclass
class PreflightReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def check(geom: Geom, layout: Layout, *,
          low_contrast_passes: list[int] | None = None,
          min_patch_mm: float = MIN_PATCH_MM) -> PreflightReport:
    """Headless readability checks for a built layout."""
    rep = PreflightReport()

    smallest = min(geom.plen, geom.pwid)
    if smallest < min_patch_mm - 1e-6:
        rep.add_error(
            f"patch size {smallest:.1f} mm is below the {min_patch_mm:.1f} mm "
            f"reliability floor — the instrument may misread; reduce the scale or "
            f"use a larger paper/instrument"
        )

    if low_contrast_passes:
        rep.add_warning(
            f"{len(low_contrast_passes)} strip(s) have low patch-to-spacer "
            f"contrast (below ΔL {LOW_CONTRAST_THRESHOLD:.0f}) and may be harder "
            f"to read: passes {low_contrast_passes}"
        )

    rep.info.append(
        f"{layout.steps_in_pass} patches/pass × {layout.passes} pass(es), "
        f"{layout.total_patches} patches (incl. {layout.padding} padding), "
        f"{layout.pages} page(s)"
    )
    return rep


# --------------------------------------------------------------------------
# Optional Argyll-backed round-trip (CI / dev)
# --------------------------------------------------------------------------

@dataclass
class RoundTripResult:
    ok: bool
    avg_de: float | None = None
    peak_de: float | None = None
    message: str = ""


def _bin(name: str, argyll_bin: str | None) -> str | None:
    if argyll_bin:
        p = Path(argyll_bin) / name
        return str(p) if p.exists() else None
    return shutil.which(name)


def roundtrip_available(argyll_bin: str | None = None) -> bool:
    return all(_bin(n, argyll_bin) for n in ("fakeread", "colprof"))


def validate_roundtrip(ti2_path: str | Path, profile_icc: str | Path,
                       argyll_bin: str | None = None) -> RoundTripResult:
    """``fakeread`` the ``.ti2`` through *profile_icc* then ``colprof``; report ΔE.

    Confirms the ``.ti2`` is a valid measurement target the toolchain accepts.
    """
    fakeread = _bin("fakeread", argyll_bin)
    colprof = _bin("colprof", argyll_bin)
    if not (fakeread and colprof):
        return RoundTripResult(False, message="ArgyllCMS fakeread/colprof not found")

    with tempfile.TemporaryDirectory() as td:
        stem = Path(td) / "preflight"
        shutil.copy(ti2_path, stem.with_suffix(".ti2"))
        fr = subprocess.run([fakeread, str(profile_icc), str(stem)],
                            capture_output=True, text=True)
        if not stem.with_suffix(".ti3").exists():
            return RoundTripResult(False, message=f"fakeread failed: {fr.stderr.strip()}")
        cp = subprocess.run([colprof, "-v", "-qm", str(stem)],
                            capture_output=True, text=True)
        out = cp.stdout + cp.stderr
        m = re.search(r"peak err\s*=\s*([\d.]+),\s*avg err\s*=\s*([\d.]+)", out)
        if not m:
            return RoundTripResult(False, message="colprof produced no error report")
        peak, avg = float(m.group(1)), float(m.group(2))
        return RoundTripResult(True, avg_de=avg, peak_de=peak,
                               message=f"round-trip avg ΔE {avg:.2f}, peak {peak:.2f}")
