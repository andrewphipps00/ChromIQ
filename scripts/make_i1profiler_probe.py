"""Build a self-identifying probe patch set for i1Profiler (#120).

We need to learn three things about i1Profiler that can't be read out of any
file we have:

  1. does it lay the chart out itself, or honour per-patch Column/Row/Page
     Location tags in a .pwxf?
  2. what geometry does it use — margins, pitch, patch size, page breaks?
  3. in what order does it fill the grid from the patch list?

All three are answerable from a PDF of the printed chart, *if* every patch can
be identified by its colour alone. So this writes a patch set in which patch
number ``i`` is painted with a colour that encodes ``i``:

    v = i + 1
    R = (v % 16) * 17,  G = (v // 16 % 16) * 17,  B = (v // 256 % 16) * 17

Sixteen levels per channel, each a multiple of 17, so every patch is a distinct
8-bit-exact colour that survives PDF rendering and quantisation. Decode with
``scripts/decode_i1profiler_probe.py``.

Writes a ready-to-hand-over folder into ``--out``::

    README.txt                          step-by-step for whoever runs it
    1 - LOAD THESE INTO i1PROFILER/
        probe.txt / probe.pxf           the patch set to load
        probe-A-autolayout.pwxf         workflow, NO Location tags
        probe-B-reversed.pwxf           workflow, Location tags REVERSED
    2 - PUT YOUR RESULTS HERE/
        settings.txt                    form to fill in while in i1Profiler
    reference (not needed by you)/
        probe.ti1                       the patch set in Argyll form

Test B is the discriminator: its Location tags deliberately place patch 1 where
i1Profiler's own column-major fill would put the LAST patch. If the printed
chart from B is the mirror of the one from A, i1Profiler honours our layout and
`emit_locations=True` is viable. If A and B print identically, it recomputes the
grid and we must derive geometry from the render instead.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.i1profiler_export import (  # noqa: E402
    WorkflowOptions, export_from_ti1, parse_ti1, write_pwxf,
)

LEVELS = 17          # 0, 17, 34 … 255


def encode(i: int) -> tuple[int, int, int]:
    """Patch index (0-based) → an 8-bit RGB triple that spells it out."""
    v = i + 1                       # keep index 0 off pure black
    return ((v % 16) * LEVELS,
            (v // 16 % 16) * LEVELS,
            (v // 256 % 16) * LEVELS)


def decode(rgb8: tuple[int, int, int]) -> int | None:
    """Inverse of :func:`encode`; None if the colour isn't on the lattice."""
    lv = []
    for c in rgb8:
        k = round(c / LEVELS)
        if not (0 <= k <= 15) or abs(k * LEVELS - c) > 4:
            return None
        lv.append(k)
    v = lv[0] + lv[1] * 16 + lv[2] * 256
    return v - 1 if v >= 1 else None


def write_ti1(path: Path, n: int) -> None:
    rows = []
    for i in range(n):
        r, g, b = encode(i)
        rows.append(f"{i + 1} {r / 255 * 100:.6f} {g / 255 * 100:.6f} "
                    f"{b / 255 * 100:.6f}")
    path.write_text("\n".join([
        "CGATS.17", "", 'DESCRIPTOR "ChromIQ i1Profiler layout probe"',
        'ORIGINATOR "ChromIQ"', 'COLOR_REP "RGB"',
        "NUMBER_OF_FIELDS 4", "BEGIN_DATA_FORMAT",
        "SAMPLE_ID RGB_R RGB_G RGB_B", "END_DATA_FORMAT",
        f"NUMBER_OF_SETS {n}", "BEGIN_DATA", *rows, "END_DATA", "",
    ]), encoding="utf-8")


_LOC_RE = re.compile(
    r'(<cc:TagCollection Name="Location">.*?</cc:TagCollection>)', re.S)


def reverse_locations(pwxf: Path) -> None:
    """Reverse the order of the Location blocks: patch 1 gets the last cell."""
    txt = pwxf.read_text(encoding="utf-8")
    blocks = _LOC_RE.findall(txt)
    if not blocks:
        raise SystemExit(f"{pwxf}: no Location tags to reverse")
    rev = iter(list(reversed(blocks)))
    txt = _LOC_RE.sub(lambda _m: next(rev), txt)
    pwxf.write_text(txt, encoding="utf-8")


README = """\
ChromIQ — i1Profiler layout probe
=================================

Goal: find out how i1Profiler lays out a chart, so ChromIQ can build a
scanner/camera target from a chart that i1Profiler printed (issue #120).

You do NOT need a measurement device.
You do NOT need an i1Profiler profiling licence.
Nothing has to be printed on paper.

*** Save the chart as a TIFF if i1Profiler offers it. ***
TIFF is better than PDF for us: it's the chart's real pixels, at a known
resolution, with nothing re-drawn in between. If TIFF isn't offered, print to
PDF instead — that works too. Keep the same file names, just with .tif.

This folder has two subfolders:

    1 - LOAD THESE INTO i1PROFILER     the files you feed to i1Profiler
    2 - PUT YOUR RESULTS HERE          drop your charts and settings.txt in here

Every patch in this chart is painted a colour that encodes its own number. So
from the saved charts alone we can work out the exact grid, the margins, the
patch pitch, the page breaks, and the order in which i1Profiler fills the page.
Nothing needs to be measured.


-------------------------------------------------------------------------
TEST 1 — how does i1Profiler lay out a plain patch set?
-------------------------------------------------------------------------
1. Open i1Profiler and choose Printer Profiling -> Testchart.

2. Load  "1 - LOAD THESE INTO i1PROFILER/probe.txt"  as the patch set.
   If it refuses that file, use  probe.pxf  instead.

3. Pick a measurement device. Choose "i1Pro 3" if it's offered; otherwise any
   device at all — just write down which one you picked.

4. Leave patch size and page settings at their defaults, and fill in
   "2 - PUT YOUR RESULTS HERE/settings.txt" with what i1Profiler shows you
   (patch size in mm, paper, orientation, columns, rows, pages).

5. Save the chart as a TIFF (or, failing that, print it to PDF — on macOS
   the PDF button at the bottom left of the print dialog -> Save as PDF).

   Save it as:   2 - PUT YOUR RESULTS HERE/test1-autolayout.tif


-------------------------------------------------------------------------
TEST 2 — does i1Profiler honour positions we give it?   (the important one)
-------------------------------------------------------------------------
6. Open  "1 - LOAD THESE INTO i1PROFILER/probe-A-autolayout.pwxf".
   Save as TIFF  ->  2 - PUT YOUR RESULTS HERE/test2a-nolocations.tif

7. Open  "1 - LOAD THESE INTO i1PROFILER/probe-B-reversed.pwxf".
   Save as TIFF  ->  2 - PUT YOUR RESULTS HERE/test2b-reversed.tif

That's the whole experiment.

File B holds exactly the same patches as file A, but each patch carries a grid
position that is the reverse of A's. So when the two charts are compared:

  * if B is A turned back-to-front, i1Profiler honours our positions — and
    ChromIQ can tell it precisely where to put every patch;

  * if A and B come out identical, i1Profiler ignores them and always lays the
    chart out its own way — and ChromIQ must instead work the layout out from
    the printed chart itself.

Either answer moves us forward. We simply have to know which one is true.

You don't have to judge this by eye. The colours encode it, and the analysis
script will say for certain.


-------------------------------------------------------------------------
TEST 3 — optional, only if it turns out to be easy
-------------------------------------------------------------------------
8. If i1Profiler will let you save or export a measurement file WITHOUT having
   measured anything — an empty or half-finished one is perfectly fine — save
   it into  "2 - PUT YOUR RESULTS HERE".

   We only want to see its columns and headers, not any numbers. If it won't
   let you, skip it: tests 1 and 2 may well make it unnecessary.


-------------------------------------------------------------------------
WHEN YOU'RE DONE
-------------------------------------------------------------------------
"2 - PUT YOUR RESULTS HERE" should contain:

    test1-autolayout.tif        (or .pdf)
    test2a-nolocations.tif      (or .pdf)
    test2b-reversed.tif         (or .pdf)
    settings.txt            (filled in)
    ...and anything from test 3

Then just say so, and the analysis runs from there.

If i1Profiler refuses one of the files, that is a result too — note which one
and what it said in settings.txt, and carry on with the rest.
"""

SETTINGS_FORM = """\
Fill this in while you're in i1Profiler (Test 1, step 4).
Just type after each colon. Leave anything blank that i1Profiler doesn't show.

Which patch-set file loaded successfully?  (probe.txt or probe.pxf)
    file loaded:

Measurement device you selected:
    device:

Paper / page:
    paper size:
    orientation (portrait / landscape):
    page width x height in mm (if shown):

Patch size, as i1Profiler reports it:
    patch width mm:
    patch height mm:

Grid, as i1Profiler reports it:
    columns:
    rows:
    pages:

Did i1Profiler warn or complain about anything?
    warnings:

Did opening probe-A-autolayout.pwxf and probe-B-reversed.pwxf work at all?
    A opened:
    B opened:

Anything else that seemed odd:

"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("-n", "--patches", type=int, default=600,
                    help="patch count (default 600 → spills past one A4 page)")
    ap.add_argument("--columns", type=int, default=29)
    ap.add_argument("--rows", type=int, default=20)
    a = ap.parse_args()
    if a.patches > 4096:
        raise SystemExit("the colour encoding addresses at most 4096 patches")

    out = a.out.expanduser()
    load = out / "1 - LOAD THESE INTO i1PROFILER"
    results = out / "2 - PUT YOUR RESULTS HERE"
    ref = out / "reference (not needed by you)"
    for d in (load, results, ref):
        d.mkdir(parents=True, exist_ok=True)

    ti1 = ref / "probe.ti1"
    write_ti1(ti1, a.patches)
    target = parse_ti1(ti1)
    assert len(target.rows) == a.patches

    txt, pxf = export_from_ti1(ti1, load, "probe",
                               descriptor="ChromIQ i1Profiler layout probe")

    pages = -(-a.patches // (a.columns * a.rows))
    base = dict(columns=a.columns, rows=a.rows, pages=pages,
                title="ChromIQ layout probe")
    write_pwxf(target, load / "probe-A-autolayout.pwxf", "probe",
               WorkflowOptions(emit_locations=False, **base))
    b = load / "probe-B-reversed.pwxf"
    write_pwxf(target, b, "probe", WorkflowOptions(emit_locations=True, **base))
    reverse_locations(b)

    (out / "README.txt").write_text(README, encoding="utf-8")
    (results / "settings.txt").write_text(SETTINGS_FORM, encoding="utf-8")

    print(f"{a.patches} patches → {out}")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"  {str(p.relative_to(out)):55s} {p.stat().st_size:>9,} bytes")
    r, g, bl = encode(0)
    print(f"\nsanity: patch 0 → RGB8 ({r},{g},{bl}) → decodes to {decode((r, g, bl))}")
    r, g, bl = encode(a.patches - 1)
    print(f"        patch {a.patches - 1} → RGB8 ({r},{g},{bl}) → "
          f"decodes to {decode((r, g, bl))}")


if __name__ == "__main__":
    main()
