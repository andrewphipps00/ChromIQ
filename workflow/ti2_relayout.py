"""TI2 layout editor — headless core.

Takes any ArgyllCMS ``.ti2``, lets the caller reorder its patches and recolor
the inter-patch spacers, and emits a *new* ``.ti2`` + page TIFF(s) that are
valid measurement targets.

Design (validated 2026-05-28 — see memory project_ti2_layout_editor):

  * Reorder is realised by writing a fresh ``.ti1`` in the chosen order and
    running ``printtarg -r`` (don't randomise), so printtarg — the authority on
    layout — regenerates a mutually consistent ``.ti2`` + ``.tif``. Device
    values are copied verbatim; we never hand-edit the patch raster.
  * Spacers are located by a render diff: the same ``.ti1`` rendered with
    default (coloured) spacers vs ``-b`` (B&W) is pixel-identical *except* at
    the spacers (``-b`` does not change geometry). The differing pixels are the
    spacer mask — no coordinate math, no ``.cht`` transform. Both renders use
    the *same basename in separate temp dirs* so the stamped chart label can't
    pollute the mask.
  * Recolouring writes only masked pixels; patch interiors stay byte-identical,
    so measured patch values are provably unaffected.

This module is Qt-free and unit-testable. The popup UI lives in ui/dialogs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.logger import get_logger
from core.strip_utils import letter_to_idx, parse_passes_per_page

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Instrument / paper reverse maps  (CGATS keyword -> printtarg flag value)
# ---------------------------------------------------------------------------
# Mirror of ui.ti2_loader.KNOWN_INSTRUMENTS, kept local so workflow/ doesn't
# import the ui layer. printtarg -i accepts: 20|22|41|51|SS|i1|3p|CM.
def instrument_to_flag(target_instrument: str | None) -> str:
    name = (target_instrument or "").lower()
    if "colormunki" in name:
        return "CM"
    if "spectroscan" in name:
        return "SS"
    if "i1 pro" in name or "i1pro" in name:
        return "i1"
    return "i1"  # safe default: i1Pro strip layout reads the widest range


# printtarg -p named sizes (mm, width x height incl. orientation).
# Mirrors the dropdown in the Create Chart tab (data.patch_db.PAPER_LABELS).
_NAMED_PAPERS: dict[tuple[float, float], str] = {
    (420.0, 594.0): "A2",       (594.0, 420.0): "594x420",
    (329.0, 483.0): "329x483",  (483.0, 329.0): "483x329",   # A3+
    (297.0, 420.0): "A3",       (420.0, 297.0): "420x297",
    (279.4, 431.8): "11x17",
    (215.9, 355.6): "Legal",
    (210.0, 297.0): "A4",       (297.0, 210.0): "A4R",
    (215.9, 279.4): "Letter",   (279.4, 215.9): "LetterR",
    (203.0, 254.0): "203x254",
    (127.0, 178.0): "127x178",
    (101.6, 152.4): "4x6",
}


def paper_to_flag(w_mm: float, h_mm: float) -> str:
    """Map a PAPER_SIZE (mm) to a printtarg ``-p`` value.

    Falls back to printtarg's custom ``WWWxHHH`` form for unrecognised sizes.
    """
    for (w, h), name in _NAMED_PAPERS.items():
        if abs(w - w_mm) < 0.6 and abs(h - h_mm) < 0.6:
            return name
    return f"{w_mm:g}x{h_mm:g}"


# ---------------------------------------------------------------------------
# Parsed chart
# ---------------------------------------------------------------------------
@dataclass
class Patch:
    sample_id: str
    loc: str | None                       # SAMPLE_LOC, e.g. "A1" (None if absent)
    dev: tuple[float, ...]                # device values in dev_fields order
    xyz: tuple[float, float, float] | None


@dataclass
class ChartSpec:
    patches: list[Patch]
    dev_fields: list[str]                 # e.g. ["RGB_R","RGB_G","RGB_B"]
    has_xyz: bool
    color_rep: str                        # e.g. "iRGB"
    white_point: str | None               # raw APPROX_WHITE_POINT triplet
    instrument_flag: str                  # printtarg -i value, e.g. "i1"
    paper_flag: str                       # printtarg -p value, e.g. "A4"
    paper_mm: tuple[float, float]
    # Spacer palette read from the sibling .ti1's DENSITY_EXTREME_VALUES table.
    # Populated only when loading a chart whose .ti1 is alongside the .ti2 —
    # restores the original spacer palette on load so the preview matches the
    # source chart instead of resetting to printtarg's defaults.
    density_extremes: tuple[tuple[float, float, float], ...] | None = None

    @property
    def n_channels(self) -> int:
        return len(self.dev_fields)

    # -- parsing -----------------------------------------------------------
    @classmethod
    def from_ti2(cls, path: Path) -> "ChartSpec":
        text = Path(path).read_text(encoding="utf-8", errors="ignore")

        def _kw(key: str) -> str | None:
            m = re.search(rf'^\s*{key}\s+"?([^"\n]*)"?\s*$', text, re.MULTILINE)
            return m.group(1).strip() if m else None

        color_rep = _kw("COLOR_REP") or "iRGB"
        white_point = _kw("APPROX_WHITE_POINT")
        instrument = _kw("TARGET_INSTRUMENT")

        paper = _kw("PAPER_SIZE") or "210.0x297.0"
        mp = re.match(r"\s*([\d.]+)\s*x\s*([\d.]+)", paper)
        paper_mm = (float(mp.group(1)), float(mp.group(2))) if mp else (210.0, 297.0)

        # DATA_FORMAT: field names between BEGIN/END (may span lines).
        fm = re.search(r"BEGIN_DATA_FORMAT(.*?)END_DATA_FORMAT", text, re.DOTALL)
        if not fm:
            raise ValueError(f"{path}: no BEGIN_DATA_FORMAT block")
        fields = fm.group(1).split()

        # Device fields are the colour-rep channels, identified by the
        # COLOR_REP token (e.g. iRGB -> RGB_*, CMYK -> CMYK_*).
        rep = color_rep.lstrip("i")  # iRGB -> RGB
        dev_fields = [f for f in fields if f.startswith(rep + "_")]
        if not dev_fields:
            # Fallback: anything that looks like a device channel, not XYZ/Lab.
            dev_fields = [f for f in fields
                          if "_" in f and not f.startswith(("XYZ", "LAB", "SPEC"))
                          and f not in ("SAMPLE_ID", "SAMPLE_LOC", "SAMPLE_NAME")]
        has_xyz = all(c in fields for c in ("XYZ_X", "XYZ_Y", "XYZ_Z"))

        idx = {name: i for i, name in enumerate(fields)}
        loc_i = idx.get("SAMPLE_LOC")
        id_i = idx.get("SAMPLE_ID", 0)
        dev_i = [idx[f] for f in dev_fields]
        xyz_i = [idx[c] for c in ("XYZ_X", "XYZ_Y", "XYZ_Z")] if has_xyz else []

        dm = re.search(r"BEGIN_DATA(?!_FORMAT)(.*?)END_DATA", text, re.DOTALL)
        if not dm:
            raise ValueError(f"{path}: no BEGIN_DATA block")

        patches: list[Patch] = []
        for line in dm.group(1).splitlines():
            toks = _split_cgats(line)
            if len(toks) < len(fields):
                continue
            patches.append(Patch(
                sample_id=toks[id_i],
                loc=toks[loc_i].strip('"') if loc_i is not None else None,
                dev=tuple(float(toks[i]) for i in dev_i),
                xyz=tuple(float(toks[i]) for i in xyz_i) if has_xyz else None,
            ))
        if not patches:
            raise ValueError(f"{path}: no data rows parsed")
        # Sort patches into their visual order (top-to-bottom of strip A,
        # then strip B, …) so the editor's grid matches the printed chart's
        # layout. The .ti2's SAMPLE_ID order is the order printtarg wrote
        # rows in — for randomised charts (no `-r`) that's spatially
        # arbitrary. SAMPLE_LOC carries the spatial truth, so we sort by it
        # whenever it's populated.
        if any(p.loc for p in patches):
            patches.sort(key=_loc_sort_key)

        return cls(
            patches=patches, dev_fields=dev_fields, has_xyz=has_xyz,
            color_rep=color_rep, white_point=white_point,
            instrument_flag=instrument_to_flag(instrument),
            paper_flag=paper_to_flag(*paper_mm), paper_mm=paper_mm,
            density_extremes=_read_sibling_density_extremes(Path(path)),
        )

    # -- from scratch ------------------------------------------------------
    @classmethod
    def new(cls, instrument_flag: str = "i1", paper_flag: str = "A4") -> "ChartSpec":
        """An empty RGB chart spec for building a layout from scratch.

        Same downstream path as a parsed chart — the caller supplies the patch
        list via :func:`default_program`-style edits (add patches, set colours),
        then :func:`regenerate`. instrument/paper are chosen by the user rather
        than read from a source file.
        """
        from workflow.i1profiler_import import WHITE_XYZ
        inv = {v: k for k, v in _NAMED_PAPERS.items()}
        return cls(
            patches=[], dev_fields=["RGB_R", "RGB_G", "RGB_B"], has_xyz=True,
            color_rep="iRGB",
            white_point=" ".join(f"{v:.6f}" for v in WHITE_XYZ),
            instrument_flag=instrument_flag, paper_flag=paper_flag,
            paper_mm=inv.get(paper_flag, (210.0, 297.0)),
        )


def _split_cgats(line: str) -> list[str]:
    """Split a CGATS data row, honouring double-quoted tokens."""
    return re.findall(r'"[^"]*"|\S+', line.strip())


_LOC_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _loc_sort_key(p: "Patch") -> tuple[int, int]:
    """Sort key turning a SAMPLE_LOC ("A12") into (strip-index, step) for
    visual ordering. Patches with no/unparseable LOC sort last."""
    loc = (p.loc or "").upper().strip()
    m = _LOC_RE.match(loc)
    if not m:
        return (10**9, 10**9)
    return (letter_to_idx(m.group(1)), int(m.group(2)))


def _read_sibling_density_extremes(
    ti2_path: Path,
) -> tuple[tuple[float, float, float], ...] | None:
    """Pull DENSITY_EXTREME_VALUES from the .ti1 next to a .ti2 (if present).

    printtarg always writes the .ti1 + .ti2 as a pair with matching stems. The
    .ti1's second CGATS table carries the spacer-colour palette the chart was
    rendered with, so reading it back on load lets the editor restore the
    original palette instead of resetting to printtarg's defaults. Returns
    None when no sibling .ti1 exists or the table is missing/malformed.
    """
    ti1 = ti2_path.with_suffix(".ti1")
    if not ti1.is_file():
        return None
    try:
        text = ti1.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    # Split into per-table sections by CTI1 marker so we read the 2nd table
    # only. The first table is the patch list; the second is the extremes.
    sections = re.split(r"^CTI1\s*$", text, flags=re.MULTILINE)
    # sections[0] is the file header; sections[1..] are the tables.
    extreme_section = None
    for s in sections[1:]:
        if "DENSITY_EXTREME_VALUES" in s:
            extreme_section = s
            break
    if extreme_section is None:
        return None
    fm = re.search(r"BEGIN_DATA_FORMAT(.*?)END_DATA_FORMAT", extreme_section, re.DOTALL)
    dm = re.search(r"BEGIN_DATA(?!_FORMAT)(.*?)END_DATA", extreme_section, re.DOTALL)
    if not fm or not dm:
        return None
    fields = fm.group(1).split()
    try:
        ri, gi, bi = fields.index("RGB_R"), fields.index("RGB_G"), fields.index("RGB_B")
    except ValueError:
        return None
    out: list[tuple[float, float, float]] = []
    for line in dm.group(1).splitlines():
        toks = _split_cgats(line)
        if len(toks) <= max(ri, gi, bi):
            continue
        try:
            out.append((float(toks[ri]), float(toks[gi]), float(toks[bi])))
        except ValueError:
            continue
    return tuple(out) if out else None


# ---------------------------------------------------------------------------
# .ti1 synthesis
# ---------------------------------------------------------------------------
# Printtarg layout knobs the editor exposes per chart.
@dataclass
class LayoutOptions:
    spacer_mode: str = "colored"        # "colored" | "bw" | "none"
    patch_scale: float = 1.0            # -a
    spacer_scale: float = 1.0           # -A
    margin_mm: int = 6                  # -m + -M (printtarg's default is 6 mm)
    suppress_left_clip: bool = False    # -L
    no_strip_limit: bool = False        # -P
    double_density: bool = False        # -h (ColorMunki double / SpectroScan hex)
    # ChromIQ-internal: rewrites -i to "i1" + applies the preset values, then
    # patches the produced .ti2's TARGET_INSTRUMENT back to ColorMunki. Mutually
    # exclusive with double_density. Mirrors tab_chart's triple-density preset
    # (see workflow/chart_creator.py's triple_density rewrite logic).
    triple_density: bool = False
    tiff_16bit: bool = False            # -T (vs -t) DPI flag
    dpi: int = 300                      # printtarg -t / -T value

    def to_printtarg_args(self) -> list[str]:
        """Build the printtarg flag list this options bundle implies."""
        args: list[str] = []
        if self.spacer_mode == "bw":
            args.append("-b")
        elif self.spacer_mode == "none":
            args.append("-n")
        if abs(self.patch_scale - 1.0) > 0.01:
            args.append(f"-a{self.patch_scale:.2f}")
        if abs(self.spacer_scale - 1.0) > 0.01:
            args.append(f"-A{self.spacer_scale:.2f}")
        if self.margin_mm != 6:
            # -m sets the inter-strip margin, -M sets the outer page margin.
            # Both default to 6 mm in printtarg; ChromIQ's Create Chart tab
            # ties them together (see ui/tabs/tab_chart.py manual mode), so
            # we do the same here.
            args.append(f"-m{self.margin_mm}")
            args.append(f"-M{self.margin_mm}")
        if self.suppress_left_clip:
            args.append("-L")
        if self.no_strip_limit:
            args.append("-P")
        if self.double_density:
            args.append("-h")
        return args


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color_values(text: str) -> list[tuple[float, float, float]]:
    """Parse user-pasted colour values into a list of 0..100 RGB tuples.

    Accepts one colour per line in any of:
        #RRGGBB  or  RRGGBB        — hex 0..255 per channel
        R, G, B  or  R G B         — decimal; scale auto-detected (0..1 /
                                     0..100 / 0..255 / 0..65535) from the
                                     peak value across the input.

    Lines that don't look like a colour are skipped silently, so '#' comments
    or blank lines in pasted files are tolerated. Returns an empty list when
    nothing parseable was found.
    """
    triples: list[tuple[float, float, float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEX_RE.match(line)
        if m:
            h = m.group(1)
            triples.append((float(int(h[0:2], 16)),
                            float(int(h[2:4], 16)),
                            float(int(h[4:6], 16))))
            continue
        parts = re.split(r"[,;\s]+", line)
        if len(parts) >= 3:
            try:
                triples.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                pass
    if not triples:
        return []
    peak = max(max(t) for t in triples)
    if peak <= 1.5:
        f = 100.0
    elif peak <= 100.0:
        f = 1.0
    elif peak <= 255.0:
        f = 100.0 / 255.0
    else:
        f = 100.0 / 65535.0
    return [(r * f, g * f, b * f) for r, g, b in triples]


def default_program(spec: ChartSpec) -> list[tuple[float, ...]]:
    """The chart's current patches as an editable ordered device-value list.

    This is the unit the editor mutates: reordering permutes it, recolouring a
    patch replaces an entry, add/remove changes its length. Feed the result to
    :func:`write_ti1` / :func:`regenerate`.
    """
    return [p.dev for p in spec.patches]


def seed_from_targen(
    bin_dir: Path,
    n_patches: int,
    *,
    device: str = "2",
    grey_steps: int = 0,
    good_mode: bool = True,
    extra_args: list[str] | None = None,
) -> list[tuple[float, float, float]]:
    """Generate an optimised RGB patch set via targen, returned as a program.

    The "seed from targen" path for new-from-scratch mode: targen spreads
    patches well across the gamut (OFPS), giving a good base the user can then
    drag-arrange and recolour. Blank-canvas mode just skips this (empty program).
    """
    targen = Path(bin_dir) / "targen"
    args = [f"-d{device}", f"-f{n_patches}"]
    if good_mode:
        args.append("-G")
    if grey_steps > 0:
        args.append(f"-g{grey_steps}")
    args += (extra_args or [])
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        r = subprocess.run([str(targen), *args, "seed"], cwd=str(work),
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"targen failed ({r.returncode}): {r.stderr.strip()}")
        return _first_table_rgb(work / "seed.ti1")


def _first_table_rgb(ti1_path: Path) -> list[tuple[float, float, float]]:
    """Parse RGB device values from a CTI1 file's **first** table only.

    A targen .ti1 holds three tables (patch list + density extremes + device
    combinations); we want only the patch list, so we stop at the first
    ``END_DATA`` rather than concatenating all three.
    """
    text = Path(ti1_path).read_text(encoding="utf-8", errors="ignore")
    fm = re.search(r"BEGIN_DATA_FORMAT(.*?)END_DATA_FORMAT", text, re.DOTALL)
    if not fm:
        raise ValueError(f"{ti1_path}: no data format block")
    fields = fm.group(1).split()
    idx = {f: i for i, f in enumerate(fields)}
    try:
        ri, gi, bi = idx["RGB_R"], idx["RGB_G"], idx["RGB_B"]
    except KeyError as exc:
        raise ValueError(f"{ti1_path}: no RGB columns") from exc
    dm = re.search(r"BEGIN_DATA(?!_FORMAT)(.*?)END_DATA", text, re.DOTALL)
    if not dm:
        raise ValueError(f"{ti1_path}: no data block")
    out: list[tuple[float, float, float]] = []
    for line in dm.group(1).splitlines():
        toks = _split_cgats(line)
        if len(toks) > max(ri, gi, bi):
            out.append((float(toks[ri]), float(toks[gi]), float(toks[bi])))
    return out


def write_ti1(
    spec: ChartSpec,
    dev_values: list[tuple[float, ...]],
    out_path: Path,
    *,
    spacer_palette: tuple[tuple[float, float, float], ...] | None = None,
) -> Path:
    """Write a printtarg-ready ``.ti1`` whose patches are exactly ``dev_values``.

    ``dev_values`` is the final ordered list of device tuples (0..100 RGB) — the
    edited chart program. Reordering, recolouring a patch (a changed entry), and
    add/remove are all just transforms of this list; printtarg places each value
    *and* writes it into the .ti2, so a recoloured patch's pixel and its .ti2
    device value stay coupled by construction.

    printtarg rejects a single-table file ("doesn't contain two or three
    tables") — it needs the patch list **plus** the density-extremes table
    (which doubles as the spacer-colour palette; see printtarg.c ~L3576) and
    the device-combinations table. We delegate to the battle-tested 3-table
    emitter in :mod:`workflow.i1profiler_import`. RGB only for now (matching
    that emitter and ChromIQ's RGB workflow); CMYK relayout is out of scope.

    ``spacer_palette`` (0..100 RGB triples) is forwarded as the density-extremes
    table so printtarg renders spacers in those colours natively — the "native
    palette" half of the spacer feature. Keep entry 0 white and the last black.
    """
    rep = spec.color_rep.lstrip("i").upper()
    if rep != "RGB":
        raise NotImplementedError(
            f"TI2 relayout currently supports RGB charts only (got COLOR_REP "
            f"{spec.color_rep!r})."
        )
    from workflow.i1profiler_import import RgbPatch, write_ti1 as _write_ti1

    patches = [RgbPatch(*rgb) for rgb in dev_values]
    return _write_ti1(patches, Path(out_path), density_extremes=spacer_palette)


# ---------------------------------------------------------------------------
# Regeneration via printtarg
# ---------------------------------------------------------------------------
@dataclass
class RegenResult:
    ti2: Path
    tiffs: list[Path]               # default-spacer pages (the deliverable)
    bw_tiffs: list[Path]            # B&W-spacer twin pages (mask source)
    basename: str


def regenerate(
    spec: ChartSpec,
    dev_values: list[tuple[float, ...]],
    out_dir: Path,
    bin_dir: Path,
    *,
    basename: str = "chart",
    dpi: int = 300,
    extra_args: list[str] | None = None,
    spacer_palette: tuple[tuple[float, float, float], ...] | None = None,
    options: "LayoutOptions | None" = None,
) -> RegenResult:
    """Run printtarg twice (default + ``-b``) and return the artefact paths.

    Both runs use the *same* basename in *separate* directories so only the
    spacers differ between them (the stamped chart label is identical).

    ``spacer_palette`` recolours spacers natively on the deliverable render (the
    ``-b`` twin is only used to locate spacer pixels, so it keeps the default
    palette — geometry is pinned by ``-r`` regardless of palette).

    ``options`` carries the printtarg layout knobs the editor exposes (scale,
    spacer mode, ``-L``, ``-P``, ``-h``, margin, DPI, bit depth, triple-density).
    All non-spacer-mode flags are applied to BOTH renders so geometry matches;
    spacer-mode flags are stripped from the twin which always uses ``-b`` to
    provide a colour-only diff. ``options.dpi`` and ``options.tiff_16bit``
    override the ``dpi`` kwarg + 8-bit default; ``options.triple_density``
    overrides ``spec.instrument_flag`` to "i1" and rewrites the deliverable
    .ti2's TARGET_INSTRUMENT back to ColorMunki post-render (mirroring
    workflow/chart_creator.py's triple-density behaviour).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bw_dir = out_dir / "_spacer_twin"
    bw_dir.mkdir(parents=True, exist_ok=True)

    printtarg = Path(bin_dir) / "printtarg"
    layout_args = options.to_printtarg_args() if options else []
    # Non-spacer-mode flags ride along on both renders so geometry matches.
    geometry_args = [a for a in layout_args
                     if not (a == "-b" or a == "-n" or a == "-c")]
    deliverable_args = list(layout_args)
    # Triple-density: render with the i1Pro strip layout regardless of the
    # chart's stored instrument flag. The .ti2's TARGET_INSTRUMENT is patched
    # back to ColorMunki after the run (see _patch_ti2_for_triple_density).
    triple = bool(options and options.triple_density)
    instr_flag = "i1" if triple else spec.instrument_flag
    use_dpi = options.dpi if options else dpi
    dpi_flag = "-T" if options and options.tiff_16bit else "-t"
    base_args = [
        f"-i{instr_flag}",
        f"-p{spec.paper_flag}",
        f"{dpi_flag}{use_dpi}",
        "-r",                       # honour our .ti1 order, don't randomise
        *(extra_args or []),
    ]

    def _run(work: Path, bw: bool) -> list[Path]:
        # The bw twin always gets the shifted palette so default-mode white &
        # black spacer choices in the deliverable still diff against it.
        write_ti1(spec, dev_values, work / f"{basename}.ti1",
                  spacer_palette=_BW_TWIN_PALETTE if bw else spacer_palette)
        flags = (geometry_args + ["-b"]) if bw else deliverable_args
        args = [str(printtarg), *base_args, *flags, basename]
        r = subprocess.run(args, cwd=str(work), capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"printtarg failed ({r.returncode}): {r.stderr.strip()}")
        return sorted({*work.glob(f"{basename}*.tif"), *work.glob(f"{basename}*.tiff")})

    tiffs = _run(out_dir, bw=False)
    bw_tiffs = _run(bw_dir, bw=True)
    if not tiffs:
        raise RuntimeError("printtarg produced no TIFF pages")
    if len(tiffs) != len(bw_tiffs):
        raise RuntimeError(
            f"page-count mismatch: {len(tiffs)} default vs {len(bw_tiffs)} bw"
        )
    ti2 = out_dir / f"{basename}.ti2"
    if triple:
        _patch_ti2_for_triple_density(ti2)
    return RegenResult(ti2, tiffs, bw_tiffs, basename)


# ---------------------------------------------------------------------------
# "Tag as randomised" — gate + keyword rewrite
# ---------------------------------------------------------------------------
#
# The editor renders with printtarg ``-r`` (keep our order), so the .ti2 carries
# CHART_ID and chartread treats it as fixed-order: no auto strip-ID, no
# bidirectional reading. Re-labelling it RANDOM_START unlocks both — but only
# *safely* when the patch order is actually well mixed. On a structured order
# (a smooth ramp, or an i1Profiler-style RGB-cube grid) neighbouring strips look
# alike, so chartread can latch onto the wrong strip/direction and silently scramble
# the readings → a colour-cast profile (see the project_norandomize_chartread_bug
# diagnosis). The risk grows with strip count, i.e. with big charts.
#
# analyze_randomisation() measures, on the *produced* layout, the two things that
# break: whether any strip reads the same forwards and backwards (direction
# ambiguous), and whether any two strips are near-identical (strip ambiguous).
# Thresholds were calibrated on synthetic ramp / grid / shuffled charts from 24
# to 3000 patches: shuffled (safe) stayed at symmetry>=38 & confusability>=50 at
# every size, ramps collapsed to ~0-2, and grids collapsed in confusability as
# they grew (58 -> 10 by 3000 patches). The cut sits conservatively below the
# safe band so an uncertain chart is reported unsafe (the user can still override).

_SYM_THRESHOLD = 25.0     # min mean RGB distance between a strip and its reverse
_CONF_THRESHOLD = 40.0    # min mean RGB distance between any two strips


@dataclass
class RandomisationReport:
    """Verdict on whether a chart's layout is safe to tag as randomised."""
    safe: bool
    n_strips: int
    min_symmetry: float        # inf when undefined (e.g. single-patch strips)
    min_confusability: float   # inf when < 2 strips
    reason: str


_LOC_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
# printtarg writes CHART_ID (fixed order) / RANDOM_START (randomised); chartread
# keys auto strip-ID + bidirectional reading off the latter (chartread.c:2980).
_RANDOM_START_RE = re.compile(r"\bRANDOM_START\b")


def _read_ti2_strips(ti2_path: Path) -> list["np.ndarray"]:
    """Group a .ti2's patches into per-strip RGB sequences (by SAMPLE_LOC).

    Returns one ``[n_patches, 3]`` float array per strip, patches ordered by the
    numeric part of their SAMPLE_LOC (e.g. A1, A2, …). Strips are returned in
    first-seen order. Empty list if the file can't be parsed.
    """
    try:
        text = ti2_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    try:
        fmt_i = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA_FORMAT")
        fields = lines[fmt_i + 1].split()
        loc_ix = fields.index("SAMPLE_LOC")
        r_ix, g_ix, b_ix = (fields.index(f) for f in ("RGB_R", "RGB_G", "RGB_B"))
        data_i = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA")
    except (StopIteration, ValueError):
        return []

    strips: dict[str, list[tuple[int, tuple[float, float, float]]]] = {}
    order: list[str] = []
    for l in lines[data_i + 1:]:
        if l.strip() == "END_DATA":
            break
        p = l.split()
        if len(p) <= max(loc_ix, r_ix, g_ix, b_ix):
            continue
        m = _LOC_RE.match(p[loc_ix].strip('"'))
        if not m:
            continue
        letter, num = m.group(1), int(m.group(2))
        try:
            rgb = (float(p[r_ix]), float(p[g_ix]), float(p[b_ix]))
        except ValueError:
            continue
        if letter not in strips:
            strips[letter] = []
            order.append(letter)
        strips[letter].append((num, rgb))

    out: list[np.ndarray] = []
    for letter in order:
        seq = [rgb for _, rgb in sorted(strips[letter])]
        out.append(np.asarray(seq, dtype=float))
    return out


def _mean_row_dist(a: "np.ndarray", b: "np.ndarray") -> float:
    """Mean per-patch Euclidean RGB distance over the common length of two strips."""
    n = min(len(a), len(b))
    if n == 0:
        return float("inf")
    return float(np.linalg.norm(a[:n] - b[:n], axis=1).mean())


def analyze_randomisation(ti2_path: Path) -> RandomisationReport:
    """Judge whether ``ti2_path``'s layout is well-mixed enough to tag as randomised.

    Safe requires every strip to differ from its own reverse (direction is
    decidable) and every pair of strips to differ from each other in both
    orientations (the right strip is decidable), each by a calibrated margin.
    A chart with fewer than two multi-patch strips is trivially safe.
    """
    strips = _read_ti2_strips(ti2_path)
    multi = [s for s in strips if len(s) >= 2]
    n = len(strips)

    if len(multi) < 2:
        return RandomisationReport(True, n, float("inf"), float("inf"),
                                   "Too few strips to be confusable.")

    # Direction ambiguity: any strip that reads ~the same forwards and backwards.
    min_sym = min(_mean_row_dist(s, s[::-1]) for s in multi)
    if min_sym < _SYM_THRESHOLD:
        return RandomisationReport(
            False, n, min_sym, float("nan"),
            "A strip reads almost the same in both directions, so the reading "
            "direction can't be told apart.")

    # Strip ambiguity: any two strips near-identical in either orientation.
    # Compare equal-length strips vectorised; the last strip may be shorter, so
    # truncate everything to the shortest length for the pairwise pass.
    min_len = min(len(s) for s in multi)
    arr = np.stack([s[:min_len] for s in multi])           # [S, L, 3]
    rev = arr[:, ::-1, :]
    min_conf = float("inf")
    for i in range(len(arr)):
        # distance from strip i to every strip, forwards and reversed
        d_fwd = np.linalg.norm(arr - arr[i], axis=2).mean(axis=1)   # [S]
        d_rev = np.linalg.norm(rev - arr[i], axis=2).mean(axis=1)   # [S]
        d_fwd[i] = np.inf                                            # skip self
        local = float(min(d_fwd.min(), d_rev.min()))
        if local < min_conf:
            min_conf = local
        if min_conf < _CONF_THRESHOLD:
            return RandomisationReport(
                False, n, min_sym, min_conf,
                "Two strips look almost identical, so chartread can't reliably "
                "tell which strip is which.")

    return RandomisationReport(True, n, min_sym, min_conf,
                               "Layout is well mixed.")


def tag_ti2_randomised(ti2_path: Path) -> bool:
    """Relabel a fixed-order .ti2 as randomised (CHART_ID → RANDOM_START).

    chartread keys auto strip-ID and bidirectional reading off this keyword
    (chartread.c:2980); the physical layout (SAMPLE_LOC + values) is untouched,
    so the chart on paper is identical. Returns True if a rewrite happened,
    False if the file was already randomised or couldn't be read.
    """
    try:
        text = ti2_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _RANDOM_START_RE.search(text):
        return False
    new = re.sub(r'\bCHART_ID\b', "RANDOM_START", text, count=1)
    if new == text:
        return False
    try:
        ti2_path.write_text(new, encoding="utf-8")
    except OSError as exc:
        log.warning("tag-as-randomised rewrite failed for %s: %s", ti2_path, exc)
        return False
    return True


def _patch_ti2_for_triple_density(ti2: Path) -> None:
    """Rewrite TARGET_INSTRUMENT from i1 Pro back to ColorMunki.

    Triple density uses the i1Pro strip layout so printtarg writes
    ``TARGET_INSTRUMENT "GretagMacbeth i1 Pro"`` into the .ti2; chartread
    needs to drive the ColorMunki the user actually has, so we patch that
    string post-run. Mirrors workflow.chart_creator._patch_ti2_instrument.
    """
    if not ti2.is_file():
        return
    try:
        text = ti2.read_text(encoding="utf-8", errors="ignore")
        new = text.replace(
            'TARGET_INSTRUMENT "GretagMacbeth i1 Pro"',
            'TARGET_INSTRUMENT "X-Rite ColorMunki"',
        )
        if new != text:
            ti2.write_text(new, encoding="utf-8")
    except OSError as exc:
        log.warning("triple-density ti2 patch failed for %s: %s", ti2, exc)


# ---------------------------------------------------------------------------
# Spacer detection + segmentation
# ---------------------------------------------------------------------------
@dataclass
class Spacer:
    """One contiguous spacer region on a page."""
    page: int
    pixels: tuple[np.ndarray, np.ndarray]   # (ys, xs) for fast recolour
    bbox: tuple[int, int, int, int]         # x0, y0, x1, y1 (inclusive)
    centroid: tuple[float, float]           # (cx, cy)

    @property
    def area(self) -> int:
        return int(self.pixels[0].size)


# Palette the ``-b`` twin renders with: defaults, but `pcol[0]` and `pcol[7]`
# are nudged a few code values off pure white / pure black. printtarg's `-b`
# mode picks one of these two entries per gap (see printtarg.c setup_spacer
# L1167), and the default deliverable palette has pure white/black at the
# same positions — so a default-mode WHITE or BLACK spacer choice (common:
# black between light patches, white between dark patches) used to collide
# with the twin's identical choice and was invisible to the diff. Nudging
# only the twin keeps the deliverable visually pure white/black; the small
# (~10/255) shift clears the diff threshold so those gaps now register.
_BW_TWIN_PALETTE: tuple[tuple[float, float, float], ...] = (
    (98.0, 100.0, 98.0),     # near-white (was 100,100,100)
    (0.0,  100.0, 100.0),    # cyan       (unchanged; -b ignores entries 1-6)
    (100.0, 0.0,  100.0),    # magenta
    (0.0,  0.0,   100.0),    # blue
    (100.0, 100.0, 0.0),     # yellow
    (0.0,  100.0, 0.0),      # green
    (100.0, 0.0,  0.0),      # red
    (2.0,  0.0,   2.0),      # near-black (was 0,0,0)
)


def _label_band_end(arr: np.ndarray) -> int | None:
    """Return the y of the last row in the strip-label band (A B C…), or None.

    The label band is at the top of the deliverable: a row of sparse darks
    (single-letter strip labels) on white. Detected as the topmost
    contiguous span of rows whose dark-pixel count is in
    ``[MIN_LABEL_DARK, 30% of width]`` — narrow enough to admit single
    letters but exclude solid patch rows. Extracted so callers needing
    only the y-anchor (e.g. patch_geometry_for_page) don't have to run
    the multi-strip-fragile bbox math in :func:`_patch_grid_bbox`.
    """
    h, w = arr.shape[:2]
    if h < 50 or w < 50:
        return None
    gray = arr.mean(axis=2)
    DARK            = 80
    MIN_LABEL_DARK  = max(5, w // 200)
    MAX_LABEL_FRAC  = 0.30
    EMPTY_STOP      = 8
    max_label_dark = int(w * MAX_LABEL_FRAC)
    y_lab_start: int | None = None
    y_lab_end:   int | None = None
    empty_streak = 0
    for y in range(h * 30 // 100):
        count = int((gray[y] < DARK).sum())
        if MIN_LABEL_DARK <= count <= max_label_dark:
            if y_lab_start is None:
                y_lab_start = y
            y_lab_end = y
            empty_streak = 0
        else:
            empty_streak += 1
            if y_lab_start is not None and empty_streak >= EMPTY_STOP:
                break
    return y_lab_end


def _patch_grid_bbox(arr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of the patch grid in a deliverable page.

    Adapted from ``ui.tabs.tab_measure._detect_uniform_stripe_rects`` — the
    same algorithm the Measure tab uses to position its strip highlighter
    over the patch block while explicitly ignoring the rotated title string
    printtarg prints down the right margin. Three passes:

    1. Find the label band at the top via :func:`_label_band_end`.
    2. Below the labels, look at every column's "has-content" count and
       take the **widest contiguous run** of content columns. The patch
       block is one solid edge-to-edge run; the right-margin title is a
       narrower run separated by a wide white gap and gets dropped.
    3. Take the vertical extent from the topmost to bottommost content row.

    Returns ``(y0, y1, x0, x1)`` inclusive, or ``None`` if the page can't be
    analysed (callers fall back to using the full image).
    """
    h, w = arr.shape[:2]
    if h < 50 or w < 50:
        return None
    gray = arr.mean(axis=2)  # 0..255 luminance proxy

    # ── 1. Label band → vertical anchor ───────────────────────────────────
    WHITE = 240
    y_lab_end = _label_band_end(arr)
    if y_lab_end is None:
        return None

    # ── 2. Patch block = widest contiguous run of content columns ─────────
    y0 = y_lab_end + 1
    y1 = int(h * 0.97)
    if y1 <= y0:
        return None
    col_content = (gray[y0:y1] < WHITE).sum(axis=0)
    thr = (y1 - y0) * 0.10
    gap = max(2, w // 250)
    best: tuple[int, int] | None = None
    run_start: int | None = None
    last = 0
    for x in range(w):
        if int(col_content[x]) > thr:
            if run_start is None:
                run_start = x
            last = x
        elif run_start is not None and x - last > gap:
            if best is None or (last - run_start) > (best[1] - best[0]):
                best = (run_start, last)
            run_start = None
    if run_start is not None and (
        best is None or (last - run_start) > (best[1] - best[0])
    ):
        best = (run_start, last)
    if best is None:
        return None
    block_l, block_r = best

    # ── 3. Vertical extent (top/bottom rows that contain any content) ─────
    sample = max(1, w // 250)
    any_content = (gray[:, ::sample] < WHITE).any(axis=1)
    nz = np.where(any_content)[0]
    y_top    = int(nz[0])  if nz.size else 0
    y_bottom = int(nz[-1]) if nz.size else h - 1
    return (max(y_lab_end + 1, y_top), y_bottom, block_l, block_r)


def spacer_mask(default_tif: Path, bw_tif: Path, *, thresh: int = 8) -> np.ndarray:
    """Boolean mask of spacer pixels in ``default_tif``.

    Computed as ``|default - bw_twin| > thresh`` and then clamped to the
    deliverable's patch-grid bounding box so the twin's near-white background
    diff in the margins doesn't bleed into the mask. The twin should be
    rendered via :func:`regenerate` (which uses :data:`_BW_TWIN_PALETTE`) so
    pure-white and pure-black spacer choices register too.
    """
    a = np.asarray(_imread_rgb(default_tif), dtype=np.int16)
    b = np.asarray(_imread_rgb(bw_tif), dtype=np.int16)
    if a.shape != b.shape:
        raise ValueError("default/bw page size mismatch — geometry not preserved")
    diff = np.abs(a - b).sum(axis=2) > thresh
    bbox = _patch_grid_bbox(a)
    if bbox is None:
        return diff
    y0, y1, x0, x1 = bbox
    out = np.zeros_like(diff)
    out[y0:y1 + 1, x0:x1 + 1] = diff[y0:y1 + 1, x0:x1 + 1]
    return out


def segment_spacers(
    mask: np.ndarray,
    page: int,
    *,
    min_area: int = 12,
    min_extent: int = 20,
    ref_arr: np.ndarray | None = None,
    strip_xs: list[int] | None = None,
) -> list[Spacer]:
    """Label connected spacer components (4-connectivity, scipy-free BFS).

    The mask is sparse (~1% of the page), so BFS over True pixels is cheap.

    ``min_extent`` rejects components whose longest bbox dimension is below
    this threshold (default 20 px). Real spacers are elongated bars — even a
    single-cell spacer is ≥ a patch width long. Stray label-text characters
    that survive the bbox restriction are typically <10 px in either
    dimension and get filtered out here.

    ``strip_xs`` (a list of x-coordinates where adjacent strips meet, derived
    from the .ti2's ``PASSES_IN_STRIPS2``) is the **authoritative** way to
    split wide horizontal bands into per-strip cells — it works even when two
    adjacent strips happen to pick the same spacer colour and the colour-jump
    heuristic can't see the boundary.

    Otherwise, when ``ref_arr`` (the deliverable page as HxWx3) is supplied,
    wide horizontal bands get split by colour discontinuity along the central
    row. This is a usable fallback when the strip layout is unknown.
    """
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    ys_all, xs_all = np.where(mask)
    raw: list[Spacer] = []

    for sy, sx in zip(ys_all.tolist(), xs_all.tolist()):
        if seen[sy, sx]:
            continue
        comp_y: list[int] = []
        comp_x: list[int] = []
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        while q:
            y, x = q.popleft()
            comp_y.append(y)
            comp_x.append(x)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp_y) < min_area:
            continue
        ay = np.array(comp_y)
        ax = np.array(comp_x)
        bw = int(ax.max() - ax.min() + 1)
        bh = int(ay.max() - ay.min() + 1)
        if max(bw, bh) < min_extent:
            continue   # too small to be a spacer bar — likely stray text
        raw.append(Spacer(
            page=page,
            pixels=(ay, ax),
            bbox=(int(ax.min()), int(ay.min()), int(ax.max()), int(ay.max())),
            centroid=(float(ax.mean()), float(ay.mean())),
        ))

    if strip_xs is not None:
        refined: list[Spacer] = []
        for sp in raw:
            refined.extend(_split_band_by_strips(
                sp, strip_xs, page=page, min_area=min_area))
        return refined

    if ref_arr is None:
        return raw

    refined = []
    for sp in raw:
        sub = _split_band_by_colour(sp, ref_arr, page=page, min_area=min_area)
        refined.extend(sub)
    return refined


def _split_band_by_strips(
    sp: Spacer, strip_xs: list[int], *, page: int, min_area: int,
) -> list[Spacer]:
    """Split a wide horizontal band at known inter-strip x-boundaries.

    Each ``strip_xs`` entry is an x-coordinate where two adjacent strips meet.
    Pixels in the component are partitioned by which strip-cell they fall into.
    Components that aren't wide bands (bbox aspect ratio < 2) pass through.
    """
    x0, y0, x1, y1 = sp.bbox
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    if bw <= 2 * bh:
        return [sp]
    bounds = sorted({x0, x1 + 1, *(b for b in strip_xs if x0 < b <= x1)})
    if len(bounds) <= 2:
        return [sp]
    ys, xs = sp.pixels
    cells: list[Spacer] = []
    for left, right in zip(bounds[:-1], bounds[1:]):
        sel = (xs >= left) & (xs < right)
        if int(sel.sum()) < min_area:
            continue
        cy = ys[sel]
        cx = xs[sel]
        cells.append(Spacer(
            page=page,
            pixels=(cy, cx),
            bbox=(int(cx.min()), int(cy.min()), int(cx.max()), int(cy.max())),
            centroid=(float(cx.mean()), float(cy.mean())),
        ))
    return cells or [sp]


def _split_band_by_colour(
    sp: Spacer, ref_arr: np.ndarray, *, page: int, min_area: int,
) -> list[Spacer]:
    """Split a wide horizontal band into per-strip cells by colour jumps.

    Only triggers when the bbox is at least 2× wider than tall — narrow /
    isolated spacers fall through untouched. Boundaries are detected by
    sampling colours along the band's central row and finding where consecutive
    pixels differ by more than ~30 (sum-of-channels). Single-strip bands
    naturally yield one cell == the original component.
    """
    x0, y0, x1, y1 = sp.bbox
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    if bw <= 2 * bh:
        return [sp]

    yc = (y0 + y1) // 2
    row = ref_arr[yc, x0:x1 + 1].astype(np.int16)
    diff = np.abs(np.diff(row, axis=0)).sum(axis=1)
    splits = np.where(diff > 30)[0]
    # Build cell x-ranges (inclusive)
    boundaries = [x0] + [x0 + int(s) + 1 for s in splits] + [x1 + 1]
    ys, xs = sp.pixels
    cells: list[Spacer] = []
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        sel = (xs >= left) & (xs < right)
        if int(sel.sum()) < min_area:
            continue
        cy = ys[sel]
        cx = xs[sel]
        cells.append(Spacer(
            page=page,
            pixels=(cy, cx),
            bbox=(int(cx.min()), int(cy.min()), int(cx.max()), int(cy.max())),
            centroid=(float(cx.mean()), float(cy.mean())),
        ))
    return cells or [sp]


# ---------------------------------------------------------------------------
# Recolour + integrity
# ---------------------------------------------------------------------------
def recolor_spacers(
    page_tif: Path,
    spacers: list[Spacer],
    rgb: tuple[int, int, int],
    out_tif: Path,
    *,
    dpi: int = 300,
) -> None:
    """Paint ``rgb`` into the given spacers' pixels; write a format-faithful TIFF.

    Only the listed pixels change — every other pixel (all patches) is copied
    byte-for-byte from ``page_tif``.
    """
    import tifffile

    arr = _imread_rgb(page_tif).copy()
    for sp in spacers:
        ys, xs = sp.pixels
        arr[ys, xs] = rgb
    res = (dpi, dpi)
    tifffile.imwrite(str(out_tif), arr, photometric="rgb",
                     resolution=res, resolutionunit="INCH")


def assert_data_integrity(
    dev_values: list[tuple[float, ...]], new_ti2: Path
) -> int:
    """Raise unless every requested patch is present in the regenerated .ti2.

    "What you designed is what got built" — for a pure reorder ``dev_values`` is
    the source patches resequenced, and for recolours it's the edited values.
    Either way we require the requested device-value multiset to be contained in
    the output's. Two printtarg behaviours are accounted for:

    * It may **add** patches to complete a partial final strip (a full-strip
      chart round-trips exactly; only a partial last row gets padded).
    * It **quantises device values to 8-bit** (e.g. a hand-entered 75.0 becomes
      191/255 = 74.9). Real charts are already 8-bit-aligned so this is a no-op
      for them; we compare on the 8-bit grid so a snapped hand-picked colour
      still counts as present.

    Returns the number of padding patches printtarg added.
    """
    new = ChartSpec.from_ti2(new_ti2)

    def _q8(v: float) -> float:
        # Snap a 0..100 device value to the nearest 8-bit code, as printtarg does.
        return round(round(v / 100 * 255) / 255 * 100, 3)

    def _bag(values) -> dict[tuple, int]:
        bag: dict[tuple, int] = {}
        for v in values:
            key = tuple(_q8(x) for x in v)
            bag[key] = bag.get(key, 0) + 1
        return bag

    want = _bag(dev_values)
    out_bag = _bag(p.dev for p in new.patches)
    for key, n in want.items():
        if out_bag.get(key, 0) < n:
            raise AssertionError(
                f"requested patch {key} missing from regenerated chart "
                f"({out_bag.get(key, 0)} < {n})"
            )
    return len(new.patches) - len(dev_values)


def assert_patches_untouched(before_tif: Path, after_tif: Path, mask: np.ndarray) -> None:
    """Raise unless every non-spacer pixel is identical before vs after recolour."""
    before = _imread_rgb(before_tif)
    after = _imread_rgb(after_tif)
    if before.shape != after.shape:
        raise AssertionError("page size changed during recolour")
    outside = ~mask
    if not np.array_equal(before[outside], after[outside]):
        raise AssertionError("non-spacer pixels changed during recolour")


# ---------------------------------------------------------------------------
def _imread_rgb(path: Path) -> np.ndarray:
    """Read a TIFF page as an HxWx3 uint8 array (RGB)."""
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Per-patch pixel geometry (for the preview's click + highlight overlay)
# ---------------------------------------------------------------------------
def patch_geometry_for_page(
    ti2_path: Path, tif_path: Path, page: int,
    *, bw_tif_path: Path | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    """Pixel bbox for each patch on a rendered page, keyed by SAMPLE_ID.

    Strategy:
        * The BW-twin diff gives the **outer** patch-block bbox (works for
          any strip count — spacers tile the block edge-to-edge).
        * That bbox is divided uniformly: ``n_strips`` columns × ``steps``
          rows. printtarg's i1Pro / ColorMunki layouts space patches
          uniformly within a strip, so a uniform divide places each rect
          close enough to its patch for click + highlight to feel right.
        * SAMPLE_LOC (e.g. "A12") in the .ti2 maps each SAMPLE_ID to a
          (strip, step) cell — strips go A,B,…,AA,AB,… across the chart,
          steps run 1..N down the strip.

    With ``-r`` (no-randomise) the .ti2's SAMPLE_ID order matches the
    editor's program order, so SAMPLE_ID N corresponds to grid index N-1
    and clicks can hop straight to the matching swatch.

    The simpler central-column-scan approach (find non-spacer runs)
    breaks on consecutive same-colour patches — printtarg picks a spacer
    colour that matches the patch, so the spacer is invisible in the diff
    and N consecutive patches merge into one run. Uniform divide sidesteps
    that.

    Returns an empty dict if anything can't be resolved (no ``bw_tif_path``,
    no SAMPLE_LOC, diff shape mismatch, …) — callers fall back gracefully.
    """
    if bw_tif_path is None:
        return {}
    try:
        text = Path(ti2_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    passes = parse_passes_per_page(ti2_path)
    if not passes or page >= len(passes):
        return {}
    sm = re.search(r'STEPS_IN_PASS\s+"?(\d+)"?', text)
    if not sm:
        return {}
    steps = int(sm.group(1))

    a = _imread_rgb(tif_path).astype(np.int16)
    b = _imread_rgb(bw_tif_path).astype(np.int16)
    if a.shape != b.shape:
        return {}
    diff = np.abs(a - b).sum(axis=2) > 8
    ys, xs = np.where(diff)
    if not ys.size:
        return {}
    y0_all, y1, x0, x1 = (int(ys.min()), int(ys.max()),
                          int(xs.min()), int(xs.max()))
    # The BW-diff x range is contaminated by the right-margin "ArgyllCMS …"
    # title that printtarg renders alongside the patch block — that text
    # diffs against the twin too, so x1 lands beyond the rightmost strip.
    # _patch_grid_bbox excludes the title by picking the widest content
    # column run, so use its x range when available.
    grid = _patch_grid_bbox(a.astype(np.uint8))
    if grid is not None:
        _, _, x0, x1 = grid
    # Snap y_top to just below the strip-label row (label letters diff
    # against the twin's near-white spacer 0). Without this, downstream
    # math lands the first row in the label band.
    lab_end = _label_band_end(a.astype(np.uint8))
    y_top = (lab_end + 2) if (lab_end is not None and lab_end + 2 < y1) else y0_all

    n_strips = passes[page]
    strip_w = (x1 - x0 + 1) / n_strips
    strips_before = sum(passes[:page])

    # Per-strip: walk the central column for non-spacer runs (= patches),
    # then keep only patch-sized ones — printtarg precedes each strip's
    # data with a tall leader patch (~3× a regular patch) and appends a
    # trailer at the bottom; both are too tall to be data patches, so
    # filtering by run-height drops them automatically. The remaining
    # `steps` runs in top→bottom order map straight onto step 1..N.
    strip_ranges: list[list[tuple[int, int]] | None] = []
    for s in range(n_strips):
        cx = int(x0 + (s + 0.5) * strip_w)
        col = diff[y_top:y1 + 1, cx]
        runs: list[tuple[int, int]] = []
        in_run = False
        start = 0
        for i, is_spacer in enumerate(col):
            if not is_spacer:
                if not in_run:
                    start = i
                    in_run = True
            elif in_run:
                runs.append((y_top + start, y_top + i - 1))
                in_run = False
        if in_run:
            runs.append((y_top + start, y_top + len(col) - 1))
        if not runs:
            strip_ranges.append(None)
            continue
        # Estimate a normal patch height: the median of the bottom N
        # smallest runs. Leader and trailer are outliers and won't
        # contribute. Allow ±50 % around it as the "patch-like" band.
        heights = sorted(r[1] - r[0] + 1 for r in runs)
        med = heights[len(heights) // 2]
        keep_min = max(20, int(med * 0.5))
        keep_max = int(med * 1.5)
        patch_runs = [r for r in runs
                      if keep_min <= (r[1] - r[0] + 1) <= keep_max]
        # Take the BOTTOM-MOST `steps` of them: leader sits above and
        # trailer below the data — if filtering missed one, the bottom-
        # anchor is still the safer side because the trailer is usually
        # smaller and more uniform than the leader.
        if len(patch_runs) >= steps:
            strip_ranges.append(patch_runs[-steps:])
        else:
            strip_ranges.append(patch_runs or None)

    fm = re.search(r"BEGIN_DATA_FORMAT(.*?)END_DATA_FORMAT", text, re.DOTALL)
    dm = re.search(r"BEGIN_DATA(?!_FORMAT)(.*?)END_DATA", text, re.DOTALL)
    if not fm or not dm:
        return {}
    fields = fm.group(1).split()
    idx = {f: i for i, f in enumerate(fields)}
    id_i = idx.get("SAMPLE_ID", 0)
    loc_i = idx.get("SAMPLE_LOC")
    if loc_i is None:
        return {}

    # Width shrink (each strip column has inter-strip spacers around it).
    shrink_w = 0.75
    half_w = strip_w * shrink_w / 2
    # Uniform y fallback (when strip_ranges couldn't locate enough runs).
    fallback_row_h = (y1 - y_top + 1) / steps

    geom: dict[int, tuple[int, int, int, int]] = {}
    for line in dm.group(1).splitlines():
        toks = _split_cgats(line)
        if len(toks) <= max(id_i, loc_i):
            continue
        try:
            sid = int(toks[id_i])
        except ValueError:
            continue
        # printtarg pads a partial last strip with white patches whose
        # SAMPLE_ID is 0 — they don't correspond to anything the user
        # placed, so skip them (they'd also collide on the key).
        if sid <= 0:
            continue
        loc = toks[loc_i].strip('"')
        m = _LOC_RE.match(loc)
        if not m:
            continue
        strip_idx = letter_to_idx(m.group(1))
        step_idx = int(m.group(2)) - 1
        if not (strips_before <= strip_idx < strips_before + n_strips):
            continue
        within_strip = strip_idx - strips_before
        cx = x0 + (within_strip + 0.5) * strip_w
        ranges = strip_ranges[within_strip]
        if ranges and step_idx < len(ranges):
            ty, by = ranges[step_idx]
        else:
            cy = y_top + (step_idx + 0.5) * fallback_row_h
            ty = int(cy - fallback_row_h * 0.4)
            by = int(cy + fallback_row_h * 0.4)
        geom[sid] = (int(cx - half_w), int(ty),
                     int(cx + half_w), int(by))
    return geom
