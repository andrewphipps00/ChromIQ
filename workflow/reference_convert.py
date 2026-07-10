"""Turn a manufacturer's target reference file into something ``scanin`` can read.

Standard scanner/camera targets ship their reference data in three shapes
(ArgyllCMS "Usage Scenarios"):

* **Ready to use** — a CGATS ``.txt`` / ``.cie`` / ``.ti3`` that already lists
  each patch's XYZ or Lab (Wolf Faust, HutchColor HCT, LaserSoft DCPro, CMP
  DT‑003/‑4). ``scanin`` reads these directly.
* **X‑Rite CxF** (``.cxf``) — LaserSoft's ISO 12641‑2 targets. Convert with
  ``cxf2ti3``.
* **Raw / spectral ``.txt``** — the CMP DT‑7/2019/Studio/Mini targets. Convert
  with ``txt2ti3`` then ``spec2cie`` to add the XYZ ``scanin`` needs.

ChromIQ runs the right Argyll converter for the user so they never have to touch
a command line. The converted file is written to *out_dir* (a scratch folder) so
the user's original download is left untouched. ``scanin`` happily reads a
``.ti3`` reference, so ``cxf2ti3``'s ``.ti3`` output is used as-is.
"""
from __future__ import annotations

import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Callable


class ReferenceKind(Enum):
    DIRECT = "direct"          # already has XYZ/Lab — use as-is
    CXF = "cxf"                # X-Rite CxF → cxf2ti3
    SPECTRAL_TXT = "spectral"  # raw/spectral .txt → txt2ti3 + spec2cie


class ReferenceConvertError(RuntimeError):
    """Conversion failed (carries a user-facing message)."""


# CGATS colorimetric columns that mean "ready to use".
_COLORIMETRIC = re.compile(r"\b(XYZ_[XYZ]|LAB_[LAB]|LAB_L)\b", re.IGNORECASE)


def classify_reference(path: str | Path) -> ReferenceKind:
    """Decide how *path* must be handled. ``.cxf`` → CxF; a ``.txt`` that already
    carries XYZ/Lab → direct, otherwise raw/spectral; ``.cie``/``.ti3`` → direct."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".cxf":
        return ReferenceKind.CXF
    if ext in (".cie", ".ti3"):
        return ReferenceKind.DIRECT
    # .txt (or anything else): sniff for colorimetric columns.
    try:
        head = p.read_text(errors="ignore")[:8000]
    except OSError:
        return ReferenceKind.DIRECT
    return (ReferenceKind.DIRECT if _COLORIMETRIC.search(head)
            else ReferenceKind.SPECTRAL_TXT)


def needs_conversion(path: str | Path) -> bool:
    return classify_reference(path) is not ReferenceKind.DIRECT


def is_ti3(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".ti3"


def convert_i1profiler_measurement(path: str | Path, argyll_bin: str | Path,
                                   out_dir: str | Path,
                                   runner: Callable[..., subprocess.CompletedProcess]
                                   = subprocess.run) -> Path:
    """Turn an i1Profiler **measurement** export into a ``.ti3`` ``scanin_target``
    can use, running ``txt2ti3`` for the user (the same tool as Tools → Convert
    i1Profiler → TI3). A file that is already a ``.ti3`` is returned unchanged.

    ``txt2ti3`` copies the export's ``SampleID`` into ``SAMPLE_LOC`` — the patch
    numbers ``1…N`` — which is exactly what the render-derived geometry is keyed
    on. Writes ``<out_dir>/<stem>.ti3``. Raises :class:`ReferenceConvertError`.
    """
    p = Path(path)
    if is_ti3(p):
        return p
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / p.stem
    _run(Path(argyll_bin), "txt2ti3", [str(p), str(base)], runner)
    out = base.with_suffix(".ti3")
    if not out.is_file():
        raise ReferenceConvertError(
            "txt2ti3 ran but produced no .ti3 — is this an i1Profiler "
            "measurement export?")
    return out


def _run(argyll_bin: Path, tool: str, args: list[str],
         runner: Callable[..., subprocess.CompletedProcess]) -> None:
    exe = argyll_bin / tool
    if not exe.exists():
        raise ReferenceConvertError(
            f"ChromIQ needs the ArgyllCMS tool “{tool}” to convert this file, but "
            f"couldn't find it. Check the ArgyllCMS folder in Settings.")
    r = runner([str(exe), *args], capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        raise ReferenceConvertError(f"{tool} couldn't convert the file: {tail[0]}")


def convert_reference(
    path: str | Path,
    argyll_bin: str | Path,
    out_dir: str | Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Return a reference file ``scanin`` can read. Ready-to-use files are
    returned unchanged; ``.cxf`` and raw/spectral ``.txt`` are converted into
    *out_dir* via Argyll. Raises :class:`ReferenceConvertError` on failure."""
    p = Path(path)
    kind = classify_reference(p)
    if kind is ReferenceKind.DIRECT:
        return p

    argyll_bin = Path(argyll_bin)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / p.stem

    if kind is ReferenceKind.CXF:
        # cxf2ti3 <in.cxf> <outbase>  ->  <outbase>.ti3
        _run(argyll_bin, "cxf2ti3", [str(p), str(base)], runner)
        out = base.with_suffix(".ti3")
    else:
        # txt2ti3 <in.txt> <tmpbase>  ->  <tmpbase>.ti3   (raw/spectral)
        # spec2cie <tmpbase>.ti3 <out>.cie  ->  adds the XYZ scanin needs
        tmp = out_dir / (p.stem + "_spec")
        _run(argyll_bin, "txt2ti3", [str(p), str(tmp)], runner)
        out = base.with_suffix(".cie")
        _run(argyll_bin, "spec2cie", [str(tmp.with_suffix(".ti3")), str(out)], runner)

    if not out.is_file():
        raise ReferenceConvertError(
            "The converter ran but produced no reference file. The download may "
            "not be a target reference of this type.")
    return out
