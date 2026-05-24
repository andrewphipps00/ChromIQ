"""Merge pre-conditioning measurement data into a freshly measured .ti3.

Part of the optional "ChromIQ-style refinement process" (gated by the
``chromiq_refinement`` setting). When the user opts in, the patches measured
for an earlier pre-conditioning profile — stashed beside the chart as a
``pre_*.json`` file (CGATS .ti3 content under a .json name so it survives
chart-regeneration cleanup) — are merged into the chart just measured. The
combined file is handed to colprof, which then builds the profile from more
data points.

The merged file is **self-describing**:
  * fresh patches stay first, as SAMPLE_ID 1..N, with their SAMPLE_LOC intact,
  * pre-conditioning patches are appended, renumbered to continue the sequence,
    with SAMPLE_LOC neutralised to "0" so they cannot masquerade as a strip,
  * a custom ``CHROMIQ_FRESH_COUNT N`` keyword records how many leading sets are
    fresh, so Check & Refine can restrict its strip analysis to those.

Validated against ArgyllCMS 3.5.0 colprof/profcheck: the custom keyword and the
neutralised SAMPLE_LOC are both tolerated, and colprof ingests every patch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)

FRESH_COUNT_KEYWORD = "CHROMIQ_FRESH_COUNT"
_NEUTRAL_LOC = '"0"'  # leading digit -> never matches the strip-letter regex


class Ti3MergeError(Exception):
    """Raised when two measurement files cannot be safely merged.

    Carries a human-readable ``message`` suitable for showing in a dialog.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class _Parsed:
    lines: list[str]
    color_rep: str
    data_format: list[str]
    header_end: int          # index of the BEGIN_DATA line
    rows: list[str]          # non-empty data rows between BEGIN_DATA / END_DATA


def _parse(path: Path) -> _Parsed:
    try:
        text = path.read_text()
    except OSError as exc:
        raise Ti3MergeError(f"Could not read '{path.name}': {exc}") from exc

    lines = text.splitlines()
    color_rep = ""
    data_format: list[str] = []

    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("COLOR_REP"):
            parts = s.split(None, 1)
            if len(parts) == 2:
                color_rep = parts[1].strip().strip('"')
        elif s == "BEGIN_DATA_FORMAT" and i + 1 < len(lines):
            data_format = lines[i + 1].split()

    def _find(token: str) -> int:
        for i, ln in enumerate(lines):
            if ln.strip() == token:
                return i
        return -1

    b, e = _find("BEGIN_DATA"), _find("END_DATA")
    if b == -1 or e == -1 or e <= b:
        raise Ti3MergeError(
            f"'{path.name}' is not a valid measurement file "
            "(missing BEGIN_DATA / END_DATA)."
        )
    if not color_rep or not data_format:
        raise Ti3MergeError(
            f"'{path.name}' is missing its COLOR_REP or DATA_FORMAT header."
        )

    rows = [ln for ln in lines[b + 1:e] if ln.strip()]
    if not rows:
        raise Ti3MergeError(f"'{path.name}' contains no measurement data.")

    return _Parsed(lines, color_rep, data_format, b, rows)


def read_fresh_count(path: Path) -> int | None:
    """Return the CHROMIQ_FRESH_COUNT value if the .ti3 carries the marker.

    Used by Check & Refine to limit strip analysis to freshly measured patches.
    Returns None for any ordinary (un-merged) file.
    """
    try:
        text = path.read_text()
    except OSError:
        return None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(FRESH_COUNT_KEYWORD) and not s.startswith("KEYWORD"):
            parts = s.split(None, 1)
            if len(parts) == 2:
                try:
                    return int(parts[1].strip().strip('"'))
                except ValueError:
                    return None
    return None


def merge_preconditioning(fresh_ti3: Path, pre_data: Path, out_ti3: Path) -> int:
    """Merge ``pre_data`` patches into ``fresh_ti3``; write the result to ``out_ti3``.

    ``pre_data`` holds CGATS .ti3 content (typically under a ``pre_*.json`` name).
    Returns the total number of sets in the merged file.

    Raises ``Ti3MergeError`` when the two files use a different COLOR_REP or
    DATA_FORMAT — colprof needs a single, consistent column layout.
    """
    fresh = _parse(fresh_ti3)
    pre = _parse(pre_data)

    if fresh.color_rep != pre.color_rep:
        raise Ti3MergeError(
            "The pre-conditioning measurements use a different colour "
            f"representation ({pre.color_rep}) than the chart just measured "
            f"({fresh.color_rep}). They were likely made with a different "
            "instrument or colour space and cannot be combined.\n\n"
            "The profile will be built from the new measurements only."
        )
    if fresh.data_format != pre.data_format:
        raise Ti3MergeError(
            "The pre-conditioning measurements have a different data layout "
            "(e.g. spectral vs. non-spectral, or a different field set) than "
            "the chart just measured, so they cannot be combined.\n\n"
            "The profile will be built from the new measurements only."
        )

    loc_idx = fresh.data_format.index("SAMPLE_LOC") if "SAMPLE_LOC" in fresh.data_format else None
    n_fresh = len(fresh.rows)

    merged_rows: list[str] = []
    # Fresh patches: keep verbatim, force a clean 1..N SAMPLE_ID sequence.
    for i, row in enumerate(fresh.rows, start=1):
        toks = row.split()
        toks[0] = str(i)
        merged_rows.append(" ".join(toks))
    # Pre-conditioning patches: continue the sequence, neutralise SAMPLE_LOC.
    for j, row in enumerate(pre.rows, start=n_fresh + 1):
        toks = row.split()
        toks[0] = str(j)
        if loc_idx is not None and loc_idx < len(toks):
            toks[loc_idx] = _NEUTRAL_LOC
        merged_rows.append(" ".join(toks))

    total = len(merged_rows)

    out: list[str] = []
    inserted_kw = False
    for ln in fresh.lines[:fresh.header_end]:
        s = ln.strip()
        if s.startswith("NUMBER_OF_SETS"):
            out.append(f"NUMBER_OF_SETS {total}")
            continue
        if s.startswith("NUMBER_OF_FIELDS") and not inserted_kw:
            out.append(f'KEYWORD "{FRESH_COUNT_KEYWORD}"')
            out.append(f'{FRESH_COUNT_KEYWORD} "{n_fresh}"')
            inserted_kw = True
        out.append(ln)

    out.append("BEGIN_DATA")
    out.extend(merged_rows)
    out.append("END_DATA")

    try:
        out_ti3.write_text("\n".join(out) + "\n")
    except OSError as exc:
        raise Ti3MergeError(f"Could not write merged file '{out_ti3.name}': {exc}") from exc

    log.info(
        "Merged measurements: %d fresh + %d pre-conditioning = %d sets -> %s",
        n_fresh, len(pre.rows), total, out_ti3.name,
    )
    return total
