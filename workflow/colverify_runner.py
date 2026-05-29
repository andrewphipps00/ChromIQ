"""Build a reference .ti3 from expected CIE values and run Argyll ``colverify``.

Powers the Tools → "Verify against reference" utility. The user prints a chart
through a candidate profile, measures it (``chartread`` → measured ``.ti3``), and
wants to know how far each patch lands from a set of *expected* CIE values
supplied by someone else (e.g. a known-good profile-evaluation target). Argyll's
``colverify`` reports per-patch and summary ΔE between two CGATS files, matched
by ``SAMPLE_ID`` — no ``colprof`` step required.

The expected values rarely arrive as a ready ``.ti3``; they're a plain table of
Lab (or XYZ) triples in patch order. This module turns that table into a
``colverify``-ready reference ``.ti3`` whose SAMPLE_IDs line up with the measured
file:

  * SAMPLE_ID is written ``1..N`` in table order. ``chartread`` numbers patches
    ``1..N`` in the chart's patch order, so row *i* of the table == SAMPLE_ID *i*
    == the *i*-th patch measured. (Argyll's CGATS reader knows ``SAMPLE_ID`` is a
    string field and promotes the integer literals, satisfying ``colverify``'s
    string-type check — cgats.c ~L702.)
  * If the originating chart (``.ti1``/``.ti2``) is supplied, its device RGB is
    copied in (``COLOR_REP "RGB_LAB"``) and its patch count is cross-checked
    against the table — the robustness guarantee that the reference can't
    silently misalign with the chart it came from.

``colverify`` reads ``LAB_L``/``LAB_A``/``LAB_B`` directly (Argyll 3.5.0
colverify.c ~L559), so Lab tables need no conversion; XYZ tables are emitted as
``XYZ_X``/``XYZ_Y``/``XYZ_Z``. This module is Qt-free and unit-testable; the
dialog lives in ui/dialogs/tools_dialogs.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

# colverify.c ~L1442:  "  Total errors:     peak = %f, avg = %f"
# (an optional " (CIEDE2000)" / " (CIE94)" suffix sits before the colon).
_SUMMARY_RE = re.compile(
    r"Total errors[^:]*:\s*peak\s*=\s*([\d.]+),\s*avg\s*=\s*([\d.]+)",
    re.IGNORECASE,
)
# Per-patch line (verbose ≥ 2): "<id>[ <loc>]: l a b <=> l a b  de <value>"
# colverify prints the id immediately followed by a colon ("%s%s%s:"), so the
# id is everything up to the first colon.
_PATCH_RE = re.compile(r"^(\S+?):.*?\bde\s+([\d.]+)\s*$")


def _fmt(v: float) -> str:
    return f"{v:.4f}"


# ---------------------------------------------------------------------------
# Reference table parsing
# ---------------------------------------------------------------------------

def parse_reference_values(text: str) -> list[tuple[float, float, float]]:
    """Parse a pasted/loaded table of CIE triples into ``(c0, c1, c2)`` rows.

    One patch per line, in patch order. Blank lines and ``#`` comments are
    ignored. Each data line must contain at least three numeric tokens; the
    **last three** numbers on the line are taken as the triple, so an optional
    leading index or sample name (e.g. ``GS01  50 0 0`` or ``1, 100, 0, 0``) is
    tolerated. Commas and tabs count as separators.

    Raises ``ValueError`` (naming the offending line) if a non-blank line has
    fewer than three numbers, or if no rows are found.
    """
    rows: list[tuple[float, float, float]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nums: list[float] = []
        for tok in line.replace(",", " ").replace("\t", " ").split():
            try:
                nums.append(float(tok))
            except ValueError:
                continue
        if len(nums) < 3:
            raise ValueError(
                f"Line {lineno} doesn't have three numbers: {line!r}"
            )
        rows.append((nums[-3], nums[-2], nums[-1]))
    if not rows:
        raise ValueError("No reference values found — the table is empty.")
    return rows


def chart_patch_count(path: Path) -> int:
    """Patch count of a chart's **first** CGATS table (``.ti1``/``.ti2``).

    A ``.ti1`` carries three tables (patch list, density extremes, device
    combinations); only the first is the patch list, so this counts rows in the
    first ``BEGIN_DATA``…``END_DATA`` block. Used purely to cross-check that a
    pasted reference table has the same number of patches as the chart it came
    from. Returns 0 if no data block is found.
    """
    n = 0
    in_data = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if s == "BEGIN_DATA":
            in_data, n = True, 0
        elif s == "END_DATA":
            return n
        elif in_data and s:
            n += 1
    return n


# ---------------------------------------------------------------------------
# Reference .ti3 emitter
# ---------------------------------------------------------------------------

def write_reference_ti3(
    out_path: Path,
    rows: list[tuple[float, float, float]],
    *,
    space: str = "LAB",
    rgb: list[tuple[float, float, float]] | None = None,
) -> Path:
    """Emit a ``colverify``-ready reference ``.ti3`` from expected CIE values.

    ``rows`` are the expected triples in patch order; ``space`` is ``"LAB"`` or
    ``"XYZ"``. If ``rgb`` is given (device values on the 0..100 scale, same order
    and length as ``rows``), it is written alongside so the file is a normal
    device+PCS ``.ti3`` and ``colverify -d`` works. SAMPLE_ID is ``1..N``.
    """
    space = space.upper()
    if space not in ("LAB", "XYZ"):
        raise ValueError(f"space must be 'LAB' or 'XYZ', got {space!r}")
    if rgb is not None and len(rgb) != len(rows):
        raise ValueError(
            f"RGB count ({len(rgb)}) doesn't match value count ({len(rows)})."
        )

    pcs_fields = ("LAB_L", "LAB_A", "LAB_B") if space == "LAB" else ("XYZ_X", "XYZ_Y", "XYZ_Z")
    if rgb is not None:
        fields = ("SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B", *pcs_fields)
        color_rep = "RGB_LAB" if space == "LAB" else "RGB_XYZ"
    else:
        fields = ("SAMPLE_ID", *pcs_fields)
        color_rep = space

    head = [
        "CTI3   ",
        "",
        'DESCRIPTOR "Argyll Calibration Target chart information 3"',
        'ORIGINATOR "ChromIQ"',
        f'CREATED "{datetime.now().strftime("%a %b %d %H:%M:%S %Y")}"',
        'DEVICE_CLASS "OUTPUT"',
        f'COLOR_REP "{color_rep}"',
        "",
        f"NUMBER_OF_FIELDS {len(fields)}",
        "BEGIN_DATA_FORMAT",
        " ".join(fields),
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(rows)}",
        "BEGIN_DATA",
    ]
    data: list[str] = []
    for i, triple in enumerate(rows, start=1):
        cells = [str(i)]
        if rgb is not None:
            cells += [_fmt(c) for c in rgb[i - 1]]
        cells += [_fmt(c) for c in triple]
        data.append(" ".join(cells))

    out_path.write_text("\n".join([*head, *data, "END_DATA", ""]), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# colverify runner
# ---------------------------------------------------------------------------

@dataclass
class ColverifyParams:
    ref_ti3: Path                 # target (reference) — built by write_reference_ti3
    measured_ti3: Path            # the user's chartread measurement
    de_formula: str = "-k"        # "" = CIE76, "-c" = CIE94, "-k" = CIEDE2000
    sort: bool = True             # -s : sort patches worst-first
    match_by_location: bool = False  # -l : pair by SAMPLE_LOC instead of SAMPLE_ID
    per_patch: bool = True        # -v 2 : print every patch's ΔE


@dataclass
class ColverifyResult:
    avg_de: float | None = None
    peak_de: float | None = None
    patch_errors: list[tuple[str, float]] = None  # type: ignore[assignment]
    raw_log: str = ""

    def __post_init__(self) -> None:
        if self.patch_errors is None:
            self.patch_errors = []


class ColverifyRunner:
    def __init__(self, runner: "ArgyllRunner") -> None:
        self._runner = runner
        self._last_log = ""

    def _build_args(self, p: ColverifyParams) -> list[str]:
        args: list[str] = []
        if p.per_patch:
            args += ["-v", "2"]
        if p.de_formula:
            args.append(p.de_formula)
        if p.match_by_location:
            args.append("-l")
        if p.sort:
            args.append("-s")
        args += [p.ref_ti3.name, p.measured_ti3.name]
        return args

    def run(
        self,
        params: ColverifyParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        args = self._build_args(params)
        cwd = params.measured_ti3.parent
        log.info("colverify: %s  [cwd=%s]", " ".join(args), cwd)
        self._last_log = ""

        def _accumulate(line: str) -> None:
            self._last_log += line + "\n"
            on_line(line)

        self._runner.run(
            "colverify", args, cwd, on_line=_accumulate, on_finish=on_finish,
        )

    @property
    def last_log(self) -> str:
        return self._last_log

    def parse_results(self, log_text: str = "") -> ColverifyResult:
        text = log_text or self._last_log
        result = ColverifyResult(raw_log=text)
        m = _SUMMARY_RE.search(text)
        if m:
            result.peak_de = float(m.group(1))
            result.avg_de = float(m.group(2))
        for line in text.splitlines():
            pm = _PATCH_RE.match(line.strip())
            if pm:
                result.patch_errors.append((pm.group(1), float(pm.group(2))))
        return result
