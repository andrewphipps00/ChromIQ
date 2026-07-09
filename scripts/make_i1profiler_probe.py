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

Writes into ``--out``:

    probe.ti1                 the patch set (ChromIQ/Argyll form)
    probe.txt / probe.pxf     what you load into i1Profiler (patch set only)
    probe-A-autolayout.pwxf   workflow, NO Location tags → i1Profiler must lay out
    probe-B-reversed.pwxf     workflow, Location tags in REVERSED order
    README.txt                step-by-step for the person running it

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

You do NOT need a measurement device for any of this.

You have these files:

    probe.txt                 patch set, CGATS  (try this first)
    probe.pxf                 patch set, CxF3   (use if .txt won't load)
    probe-A-autolayout.pwxf   workflow, no patch positions
    probe-B-reversed.pwxf     workflow, patch positions in reverse order

Run all three tests below. For each one, export/print the chart to PDF and
keep the PDF. Name them exactly as shown.

-------------------------------------------------------------------------
TEST 1 — how does i1Profiler lay out a plain patch set?
-------------------------------------------------------------------------
1. Open i1Profiler, choose Printer Profiling → Testchart.
2. Load "probe.txt" as the patch set. (If it refuses, use "probe.pxf".)
3. Choose a measurement device: pick "i1Pro 3" if offered, otherwise ANY
   device — just write down which one you picked.
4. Leave patch size and page settings at their defaults. Write down what
   they say: patch width/height in mm, paper size, orientation, and the
   number of columns / rows / pages it reports.
5. Print the chart to PDF  →  save as  test1-autolayout.pdf
   (macOS: in the print dialog, PDF ▸ Save as PDF.)

-------------------------------------------------------------------------
TEST 2 — does it honour positions we supply?  (the important one)
-------------------------------------------------------------------------
6. Open "probe-A-autolayout.pwxf" in i1Profiler.
   Print to PDF  →  test2a-nolocations.pdf
7. Open "probe-B-reversed.pwxf" in i1Profiler.
   Print to PDF  →  test2b-reversed.pdf

That's the whole experiment. File B contains the same patches as A, but with
each patch's grid position reversed. So:

  * if test2b looks like test2a MIRRORED, i1Profiler honours our positions;
  * if test2a and test2b look IDENTICAL, it ignores them and lays out itself.

Either answer is useful. We just have to know which.

-------------------------------------------------------------------------
TEST 3 — (only if you can, no device needed)
-------------------------------------------------------------------------
8. If i1Profiler will let you save or export a measurement file WITHOUT
   having measured anything (an empty or partial one is fine), save it.
   We want to see its columns and headers, not its numbers.

-------------------------------------------------------------------------
What to send back
-------------------------------------------------------------------------
  * the three PDFs
  * the settings you noted in step 4 (device, patch size, paper, columns,
    rows, pages)
  * anything i1Profiler complained about
  * the measurement file from step 8, if you managed one

Every patch in this chart is painted a colour that encodes its own number, so
from the PDFs alone we can work out the exact grid, the margins, the patch
pitch, the page breaks, and the order in which i1Profiler fills the page.
Nothing needs to be measured.
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
    out.mkdir(parents=True, exist_ok=True)

    ti1 = out / "probe.ti1"
    write_ti1(ti1, a.patches)
    target = parse_ti1(ti1)
    assert len(target.rows) == a.patches

    txt, pxf = export_from_ti1(ti1, out, "probe",
                               descriptor="ChromIQ i1Profiler layout probe")

    pages = -(-a.patches // (a.columns * a.rows))
    base = dict(columns=a.columns, rows=a.rows, pages=pages,
                title="ChromIQ layout probe")
    write_pwxf(target, out / "probe-A-autolayout.pwxf", "probe",
               WorkflowOptions(emit_locations=False, **base))
    b = out / "probe-B-reversed.pwxf"
    write_pwxf(target, b, "probe", WorkflowOptions(emit_locations=True, **base))
    reverse_locations(b)

    (out / "README.txt").write_text(README, encoding="utf-8")

    print(f"{a.patches} patches → {out}")
    for p in sorted(out.iterdir()):
        print(f"  {p.name:28s} {p.stat().st_size:>9,} bytes")
    r, g, bl = encode(0)
    print(f"\nsanity: patch 0 → RGB8 ({r},{g},{bl}) → decodes to {decode((r, g, bl))}")
    r, g, bl = encode(a.patches - 1)
    print(f"        patch {a.patches - 1} → RGB8 ({r},{g},{bl}) → "
          f"decodes to {decode((r, g, bl))}")


if __name__ == "__main__":
    main()
