"""Extra deliverable files written alongside every generated chart.

A chart build leaves the load-bearing files (``.ti1`` / ``.ti2`` / ``.tif`` /
``.cht``) in the run folder.  This module adds the *hand-off* sidecars that let
the same chart be used outside ChromIQ's own measure tab:

* ``<stem>-colours.txt`` — a plain hex list of the device RGB values, the same
  format the New-chart "paste colour values" mode reads (RGB charts only).
* ``<stem>-i1profiler.txt`` / ``.pxf`` — the i1Profiler patch set (via
  :mod:`workflow.i1profiler_export`).
* ``<stem>.cie`` — a CGATS **reference** file (SAMPLE_ID / SAMPLE_LOC + device +
  aim XYZ) read straight from the ``.ti2``, so it lines up with the ``.cht``
  recognition template for a ``scanin`` flatbed read.  The XYZ are the chart's
  *aim* values (sRGB-reconstructed for RGB targets), not measurements.

Everything here is best-effort and pure-Python; callers log what was written.
"""
from __future__ import annotations

from pathlib import Path


def _parse_cgats(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return (field names, data rows) from a CGATS ``.ti1``/``.ti2`` file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    fields: list[str] = []
    rows: list[list[str]] = []
    in_fmt = in_data = False
    for line in text.splitlines():
        s = line.strip()
        if s == "BEGIN_DATA_FORMAT":
            in_fmt = True; continue
        if s == "END_DATA_FORMAT":
            in_fmt = False; continue
        if s == "BEGIN_DATA":
            in_data = True; continue
        if s == "END_DATA":
            in_data = False; continue
        if in_fmt:
            fields += s.split()
        elif in_data and s:
            rows.append(s.split())
    return fields, rows


def write_cie(ti2_path: str | Path, cie_path: str | Path) -> Path:
    """Write a CGATS ``.cie`` reference from a chart's ``.ti2``.

    Carries SAMPLE_ID, SAMPLE_LOC (when present), the device fields and the aim
    XYZ, so it can serve as the reference for ``scanin`` alongside the ``.cht``.
    Raises ``ValueError`` if the ``.ti2`` has no XYZ columns.
    """
    ti2_path, cie_path = Path(ti2_path), Path(cie_path)
    fields, rows = _parse_cgats(ti2_path)
    idx = {f: i for i, f in enumerate(fields)}
    if not all(ax in idx for ax in ("XYZ_X", "XYZ_Y", "XYZ_Z")):
        raise ValueError("no XYZ columns in .ti2 — cannot write a .cie reference")
    dev = [f for f in fields if f.startswith(("RGB_", "CMYK_", "GRAY_", "DEVICE_"))]
    out_fields = (["SAMPLE_ID"]
                  + (["SAMPLE_LOC"] if "SAMPLE_LOC" in idx else [])
                  + dev + ["XYZ_X", "XYZ_Y", "XYZ_Z"])

    lines = ["CGATS.17", "",
             'DESCRIPTOR "ChromIQ chart reference (aim values, not measured)"',
             'ORIGINATOR "ChromIQ"', ""]
    if "SAMPLE_LOC" in idx:
        lines.append('KEYWORD "SAMPLE_LOC"')
    lines += [f"NUMBER_OF_FIELDS {len(out_fields)}",
              "BEGIN_DATA_FORMAT", " ".join(out_fields), "END_DATA_FORMAT", "",
              f"NUMBER_OF_SETS {len(rows)}", "BEGIN_DATA"]
    for n, r in enumerate(rows, 1):
        def g(f: str) -> str:
            return r[idx[f]] if f in idx and idx[f] < len(r) else "0"
        vals = [str(n)]
        if "SAMPLE_LOC" in idx:
            vals.append(g("SAMPLE_LOC"))
        vals += [g(f) for f in dev] + [g("XYZ_X"), g("XYZ_Y"), g("XYZ_Z")]
        lines.append(" ".join(vals))
    lines += ["END_DATA", ""]
    cie_path.write_text("\n".join(lines), encoding="utf-8")
    return cie_path


def write_colours_txt(ti1_path: str | Path, txt_path: str | Path) -> Path | None:
    """Write a ``<stem>-colours.txt`` hex list from an RGB chart's device values.

    Returns the path, or ``None`` when the chart isn't RGB (nothing written).
    """
    ti1_path, txt_path = Path(ti1_path), Path(txt_path)
    fields, rows = _parse_cgats(ti1_path)
    idx = {f: i for i, f in enumerate(fields)}
    if not all(c in idx for c in ("RGB_R", "RGB_G", "RGB_B")):
        return None
    out = []
    for r in rows:
        try:
            rgb = [float(r[idx[c]]) for c in ("RGB_R", "RGB_G", "RGB_B")]
        except (ValueError, IndexError):
            continue
        out.append("#" + "".join(f"{max(0, min(255, round(v / 100 * 255))):02x}"
                                 for v in rgb))
    txt_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return txt_path


def write_sidecars(ti1_path: str | Path, ti2_path: str | Path,
                   out_dir: str | Path, base_name: str) -> list[Path]:
    """Write the colour list, i1Profiler pair and ``.cie`` into *out_dir*.

    Best-effort: a failure of one file logs and skips it, never raising. Returns
    the list of files actually written. The ``.cht`` is produced by the chart
    build itself (engine ``emit_cht`` / printtarg), not here.
    """
    import logging
    log = logging.getLogger(__name__)
    ti1_path, ti2_path, out_dir = Path(ti1_path), Path(ti2_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if ti1_path.is_file():
        try:
            p = write_colours_txt(ti1_path, out_dir / f"{base_name}-colours.txt")
            if p is not None:
                written.append(p)
        except OSError:
            log.warning("colour-list export failed", exc_info=True)
        try:
            from workflow.i1profiler_export import export_from_ti1
            txt, pxf = export_from_ti1(ti1_path, out_dir,
                                       base_name=f"{base_name}-i1profiler",
                                       descriptor=base_name)
            written += [q for q in (txt, pxf) if q is not None]
        except Exception:  # noqa: BLE001 — never block on the i1Profiler export
            log.warning("i1Profiler export failed", exc_info=True)

    if ti2_path.is_file():
        try:
            written.append(write_cie(ti2_path, out_dir / f"{base_name}.cie"))
        except Exception:  # noqa: BLE001 — CMYK+N / no-XYZ etc.
            log.warning("cie export failed", exc_info=True)
    return written
