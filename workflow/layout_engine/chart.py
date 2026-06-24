"""High-level entry point: build a chart's ``.ti2`` from a targen ``.ti1``.

This wires the headless pieces together (read ``.ti1`` → pack → write ``.ti2``)
for any colorant the ``.ti1`` declares.  The page **TIFF** raster is a later
phase (issue #93); this module already produces the measurement-side ``.ti2``
that chartread consumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import geometry, instruments, papers, permutation
from . import ti1_reader, ti2_writer


@dataclass(frozen=True)
class ChartResult:
    ti2_path: Path
    seed: int
    randomize: bool
    color_rep: str
    layout: geometry.Layout


def build_ti2_from_ti1(
    ti1_path: str | Path,
    ti2_path: str | Path,
    *,
    instrument: str = "i1",
    paper: str = "A4",
    seed: int | None = None,
    randomize: bool = True,
    hflag: bool = False,
    spacer_on: bool = True,
    pscale: float = 1.0,
    sscale: float = 1.0,
    border: float = 6.0,
    nolpcbord: bool = False,
    nolimit: bool = False,
    strip_pattern: str = permutation.DEFAULT_STRIP_PATTERN,
    patch_pattern: str = permutation.DEFAULT_PATCH_PATTERN,
) -> ChartResult:
    """Read *ti1_path*, lay it out for *instrument* on *paper*, write *ti2_path*.

    *paper* is a named code ("A4", "A4R", "Letter", …) or a custom ``WxH`` (mm).
    *seed* defaults to a fresh reproducible value (surfaced in the result so the
    UI can show it and accept it back).
    """
    target = ti1_reader.read_ti1(ti1_path)
    geom = instruments.build(
        instrument, hflag=hflag, spacer_on=spacer_on, pscale=pscale,
        sscale=sscale, border=border, nolpcbord=nolpcbord, nolimit=nolimit,
    )
    w_mm, h_mm = papers.dimensions_mm(paper)
    layout = geometry.compute(geom, w_mm, h_mm, len(target.patches))

    if seed is None:
        seed = permutation.pick_seed()

    media = target.media_patch()
    white_point = media[1] if any(media[1]) else ti2_writer.DEFAULT_WHITE_POINT

    ti2_writer.write_ti2(
        ti2_path, target.patches, target.device_fields, layout, geom,
        color_rep=target.color_rep, seed=seed, randomize=randomize,
        strip_pattern=strip_pattern, patch_pattern=patch_pattern,
        paper_w_mm=w_mm, paper_h_mm=h_mm, media=media, white_point=white_point,
    )
    return ChartResult(
        ti2_path=Path(ti2_path), seed=seed, randomize=randomize,
        color_rep=target.color_rep, layout=layout,
    )
