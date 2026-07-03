"""Every bundled scanner/camera target registers with scanin -F using the
PATCH-AREA CORNERS as the reference (Knut's approach — no fiducial marks needed),
and does so independently of the scan's resolution.

For each bundled ``.cht`` we render the patches from the file's own geometry at
several pixel scales (≈100/200/300 dpi), then drive the real ``scanin -F`` with
the four patch-area corners and check every patch reads back from the right place.
Guarded on ArgyllCMS ``scanin`` + Pillow.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from workflow.standard_targets import bundled_targets_dir
from workflow.cht_parser import parse_cht

_BIN = Path(os.environ.get("CHROMIQ_ARGYLL_BIN", "/Applications/Argyll/bin"))
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (_BIN / "scanin").exists(), reason="ArgyllCMS scanin not present")

_TARGETS = sorted(p.name for p in bundled_targets_dir().glob("*.cht"))
_M = 40


def _worst(cht_text, scale, tmp_path):
    g = parse_cht(cht_text)
    boxes = [(b.name, b.x1, b.y1, b.x2, b.y2) for b in g.patches]
    minx = min(b[1] for b in boxes); maxx = max(b[3] for b in boxes)
    miny = min(b[2] for b in boxes); maxy = max(b[4] for b in boxes)
    off = (_M - minx * scale, _M - miny * scale)
    W = int(maxx * scale + off[0] + _M); H = int(maxy * scale + off[1] + _M)
    img = Image.new("RGB", (W, H), "white"); px = img.load(); rendered = []
    s = g.box_shrink * scale
    for i, (nm, x1, y1, x2, y2) in enumerate(boxes):
        col = (30 + (i * 37) % 200, 30 + (i * 91) % 200, 30 + (i * 53) % 200)
        rendered.append([v / 255 * 100 for v in col])
        for yy in range(int(y1 * scale + s + off[1]), int(y2 * scale - s + off[1])):
            for xx in range(int(x1 * scale + s + off[0]), int(x2 * scale - s + off[0])):
                if 0 <= xx < W and 0 <= yy < H:
                    px[xx, yy] = col
    corners = [(minx*scale+off[0], miny*scale+off[1]), (maxx*scale+off[0], miny*scale+off[1]),
               (maxx*scale+off[0], maxy*scale+off[1]), (minx*scale+off[0], maxy*scale+off[1])]
    fstr = ",".join(f"{v:.1f}" for xy in corners for v in xy)
    img.save(tmp_path / "s.tif")
    (tmp_path / "r.cht").write_text(cht_text)
    cie = (["CGATS.17", "NUMBER_OF_FIELDS 4", "BEGIN_DATA_FORMAT",
            "SAMPLE_ID XYZ_X XYZ_Y XYZ_Z", "END_DATA_FORMAT",
            f"NUMBER_OF_SETS {len(boxes)}", "BEGIN_DATA"]
           + [f"{b[0]} 40 40 40" for b in boxes] + ["END_DATA", ""])
    (tmp_path / "ref.cie").write_text("\n".join(cie))
    r = subprocess.run([str(_BIN / "scanin"), "-v", "-p", "-F", fstr,
                        "s.tif", "r.cht", "ref.cie"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert (tmp_path / "s.ti3").is_file(), f"scanin failed:\n{r.stderr[-300:]}"
    lines = (tmp_path / "s.ti3").read_text().splitlines()
    fb = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA_FORMAT")
    fields = lines[fb + 1].split(); ri = [fields.index(f"RGB_{c}") for c in "RGB"]
    db = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA")
    de = next(i for i, l in enumerate(lines) if l.strip() == "END_DATA")
    worst = 0.0
    for l in lines[db + 1:de]:
        t = l.split()
        if len(t) != len(fields):
            continue
        read = [float(t[k]) for k in ri]
        worst = max(worst, min(max(abs(a - b) for a, b in zip(read, rr))
                               for rr in rendered))
    return worst


@pytest.mark.parametrize("name", _TARGETS)
@pytest.mark.parametrize("scale", [1.0, 2.0, 3.0])   # ≈100 / 200 / 300 dpi
def test_bundled_target_registers_at_scale(name, scale, tmp_path):
    cht = (bundled_targets_dir() / name).read_text(errors="ignore")
    worst = _worst(cht, scale, tmp_path)
    assert worst < 6.0, f"{name} @ {scale}×: misregistered (worst {worst:.1f}/100)"
