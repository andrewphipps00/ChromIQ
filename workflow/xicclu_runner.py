"""Batch colour lookups through an ICC profile via Argyll's ``xicclu`` (#72).

The perceptual bridge for N-channel chart generation: RGB-generator colours →
Lab → **backward** (``-fb``) through a preconditioning profile → device ink
values; device values → **forward** (``-ff``) → XYZ for the ``.ti1`` (or Lab
for the out-of-gamut round-trip check).

I/O grammar (verified live against ArgyllCMS 3.5.0, issue #72):

* one query per stdin line, whitespace-separated; one result line per query::

      50.000000 40.000000 30.000000 [Lab] -> Lut -> 0.126686 0.742860 0.733718 0.071004 [CMYK]

  → parse the tokens between the last ``->`` and the trailing ``[…]`` tag.
* device values are 0..1 on the wire (scaled ×100/÷100 at this boundary —
  TI1/TI2 files use 0..100); ``-pX`` returns XYZ already ×100 (TI1-ready);
  ``-pl`` returns Lab unscaled.
* ``-fb`` (Lut backward) emits **no clip marker** on out-of-gamut input — OOG
  detection is the caller's forward round-trip (#72 appendix B). ``-fif``
  (inverse forward) *does* append a ``(clip)`` marker after the tag; the
  parser tolerates both.
* an ink limit (``-l``) is only **enforced** by the numeric inverse-forward
  path (``-fif``) — on ``-fb`` the baked B2A table can't be limited and the
  ``TAC <n>`` pair is merely reported (verified live: ``-fb -l250`` happily
  returned TAC 2.87). :func:`backward_device` therefore switches to ``-fif``
  whenever an ink limit is given. The trailing ``TAC``/tag/marker tokens are
  all stripped from parsed values.

Process model: ``subprocess.run`` with an injectable ``runner`` (the
``reference_convert.py`` house pattern) — one process per batch, **never** the
ArgyllRunner QProcess singleton, whose ``is_running`` guard would make live
generator previews clash with a running chartread/colprof.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence

from core.logger import get_logger
from core.resource_path import argyll_binary

log = get_logger(__name__)

_TIMEOUT_S = 120


class XiccluError(RuntimeError):
    """xicclu failed or returned unparseable output (user-facing message)."""


def _run_xicclu(
    bin_dir: str | Path,
    args: list[str],
    profile: str | Path,
    input_lines: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess],
) -> list[list[float]]:
    """One xicclu process over all ``input_lines``; parsed per-line results."""
    exe = Path(bin_dir) / argyll_binary("xicclu")
    if not exe.exists():
        raise XiccluError(f"xicclu not found in {bin_dir}")
    cmd = [str(exe), *args, str(profile)]
    try:
        r = runner(cmd, input="\n".join(input_lines) + "\n",
                   capture_output=True, text=True, timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise XiccluError(f"xicclu timed out after {_TIMEOUT_S}s") from exc
    if r.returncode != 0:
        raise XiccluError(
            f"xicclu failed ({r.returncode}): {(r.stderr or r.stdout).strip()}")

    out: list[list[float]] = []
    for line in r.stdout.splitlines():
        if "->" not in line:
            continue                     # ignore any banner/blank lines
        # The result values are the leading float run after the last "->";
        # everything after it is annotation ("TAC <n>", "[CMYK]", "(clip)").
        vals: list[float] = []
        for tok in line.rsplit("->", 1)[1].split():
            try:
                vals.append(float(tok))
            except ValueError:
                break
        if not vals:
            raise XiccluError(f"unparseable xicclu line: {line!r}")
        out.append(vals)
    if len(out) != len(input_lines):
        raise XiccluError(
            f"xicclu returned {len(out)} results for {len(input_lines)} queries")
    return out


def forward_xyz(
    device_rows: Sequence[tuple[float, ...]],
    profile: str | Path,
    bin_dir: str | Path,
    *,
    intent: str = "r",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[tuple[float, float, float]]:
    """Device values (0..100) → XYZ (Y=100 scale, TI1-ready) via ``-ff -pX``."""
    lines = [" ".join(f"{v / 100.0:.6f}" for v in row) for row in device_rows]
    res = _run_xicclu(bin_dir, ["-ff", f"-i{intent}", "-pX"],
                      profile, lines, runner)
    return [tuple(row) for row in res]   # -pX is already ×100


def forward_lab(
    device_rows: Sequence[tuple[float, ...]],
    profile: str | Path,
    bin_dir: str | Path,
    *,
    intent: str = "r",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[tuple[float, float, float]]:
    """Device values (0..100) → Lab via ``-ff -pl`` (the OOG round-trip leg)."""
    lines = [" ".join(f"{v / 100.0:.6f}" for v in row) for row in device_rows]
    res = _run_xicclu(bin_dir, ["-ff", f"-i{intent}", "-pl"],
                      profile, lines, runner)
    return [tuple(row) for row in res]


def backward_device(
    lab_rows: Sequence[tuple[float, float, float]],
    profile: str | Path,
    bin_dir: str | Path,
    *,
    intent: str = "r",
    k_rule: str | None = "r",
    ink_limit: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[tuple[float, ...]]:
    """Lab targets → device values (0..100).

    Uses ``-fb`` (fast Lut backward) normally, but switches to ``-fif``
    (numeric inverse-forward) when ``ink_limit`` is given — only that path
    actually *enforces* ``-l``; on ``-fb`` the baked B2A table ignores it and
    the TAC pair is merely informational (verified live, ArgyllCMS 3.5.0).

    ``k_rule`` is xicclu's ``-k`` black-generation rule (#72 decision: ``"r"``
    for v1, no UI knobs); it only applies to profiles with a K channel — pass
    ``None`` to omit. Trailing ``TAC``/``(clip)`` annotations are stripped
    from the parsed values.
    """
    args = ["-fif" if ink_limit is not None else "-fb", f"-i{intent}", "-pl"]
    if k_rule:
        args.append(f"-k{k_rule}")
    if ink_limit is not None:
        args.append(f"-l{ink_limit:g}")
    lines = [" ".join(f"{v:.6f}" for v in row) for row in lab_rows]
    res = _run_xicclu(bin_dir, args, profile, lines, runner)
    return [tuple(v * 100.0 for v in row) for row in res]
