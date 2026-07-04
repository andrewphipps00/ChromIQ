"""Cross-platform ArgyllCMS discovery for the real-render tests.

The scanner / registration tests shell out to real Argyll tools (``scanin``,
``colprof``, …) and read reference targets from Argyll's ``ref/`` directory.
They historically probed only the macOS default (``/Applications/Argyll/bin``)
and a bare ``scanin`` name, so they silently *skipped* on Windows even with
Argyll installed (the executable is ``scanin.exe`` in a versioned per-user dir).

Resolve both the bin dir and the ref dir the way the app itself does —
``argyll_candidate_dirs()`` covers the standard per-OS install locations,
including versioned Windows installs — while still honouring the
``CHROMIQ_ARGYLL_BIN`` / ``CHROMIQ_ARGYLL_REF`` overrides used by CI.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.platform_paths import argyll_candidate_dirs
from core.resource_path import argyll_binary

__all__ = ["argyll_binary", "argyll_bin_dir", "argyll_tool", "argyll_ref_dir"]


def argyll_bin_dir() -> Path | None:
    """First directory that actually contains Argyll's ``scanin`` — the
    ``CHROMIQ_ARGYLL_BIN`` override first, then the app's own candidate dirs.
    ``None`` when Argyll isn't installed."""
    candidates: list[Path] = []
    env = os.environ.get("CHROMIQ_ARGYLL_BIN")
    if env:
        candidates.append(Path(env))
    candidates.extend(argyll_candidate_dirs())
    for d in candidates:
        if (d / argyll_binary("scanin")).exists():
            return d
    return None


def argyll_tool(name: str) -> str | None:
    """Absolute path (as ``str``) to an Argyll tool, or ``None`` if not found."""
    d = argyll_bin_dir()
    if d is None:
        return None
    p = d / argyll_binary(name)
    return str(p) if p.exists() else None


def argyll_ref_dir() -> Path | None:
    """Argyll's ``ref/`` directory — a sibling of ``bin/`` on every platform.
    Honours the ``CHROMIQ_ARGYLL_REF`` override and falls back to the macOS
    default; ``None`` when it can't be located."""
    env = os.environ.get("CHROMIQ_ARGYLL_REF")
    if env and Path(env).is_dir():
        return Path(env)
    d = argyll_bin_dir()
    if d is not None and (d.parent / "ref").is_dir():
        return d.parent / "ref"
    mac = Path("/Applications/Argyll/ref")
    return mac if mac.is_dir() else None
