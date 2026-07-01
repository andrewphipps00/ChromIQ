"""Build a **scanner-recognition target** (``.cht`` + ``.cie``) from a measured
chart, so ArgyllCMS ``scanin`` can read the printed chart off a flatbed scan and
``colprof`` can turn that into a scanner input profile (#97).

The two halves and how they line up:

* ``.cht`` (:mod:`~workflow.layout_engine.cht_writer`) — *where* every patch
  sits on the page, per page, plus an ``F`` fiducial line for ``scanin -F``.
  Geometry comes from the engine's exact ``channels.json["layout"]["patches"]``
  (top-left px), flipped to the ``.cht``'s bottom-left mm.
* ``.cie`` (:mod:`~workflow.layout_engine.cie_writer`) — *what colour* every
  patch truly is, the **measured** XYZ from the run's ``.ti3``.

Both are keyed by the patch **loc** (``A01`` …): the ``.cht`` box loc, the
``.cie`` ``SAMPLE_ID`` and the ``.ti3`` ``SAMPLE_LOC`` are one and the same
because they all come from the engine's single permutation. We *assert* that
alignment and fail loudly rather than let ``scanin`` misregister silently.

Multi-page charts get one ``.cht`` **per page** (``<stem>_01.cht`` …) and a
single whole-chart ``.cie`` (a superset the per-page ``.cht`` indexes into).

This module needs only data ChromIQ already holds — the engine geometry and the
measurement — so it never calls an Argyll binary. It requires an **engine**
chart (an exact ``layout`` block); inferring geometry for older/printtarg charts
from the ``.ti2`` is a separate, validated follow-up (#97 item 7). The ``.cie``
half, by contrast, works from *any* measured ``.ti3``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from workflow.layout_engine import cht_writer, cie_writer
from workflow.ti3_analysis import Ti3Data, parse_ti3


class ScaninTargetError(ValueError):
    """Base for scanner-target build failures (all carry a user-facing message)."""


class NotAnEngineChart(ScaninTargetError):
    """The chart has no exact engine geometry, so a trustworthy ``.cht`` can't be
    built from it yet (older / imported / printtarg chart — see #97 item 7)."""


class GeometryMismatch(ScaninTargetError):
    """The measured ``.ti3`` doesn't line up with the chart geometry (a patch is
    missing a measurement, or vice-versa) — refuse rather than misregister."""


@dataclass
class ScaninTargetResult:
    cht_paths: list[Path]      # one per page
    cie_path: Path
    n_patches: int
    n_pages: int


def _load_engine_layout(channels_json: str | Path) -> dict:
    """Return the ``layout`` block of an engine chart's ``channels.json``, or
    raise :class:`NotAnEngineChart`."""
    p = Path(channels_json)
    if not p.is_file():
        raise NotAnEngineChart(
            "This chart has no layout sidecar, so ChromIQ doesn't know where its "
            "patches sit. Scanner targets are supported for charts created with "
            "ChromIQ's layout engine.")
    try:
        layout = json.loads(p.read_text()).get("layout") or {}
    except (OSError, ValueError) as exc:
        raise NotAnEngineChart(f"Couldn't read the chart layout: {exc}") from exc
    if (layout.get("engine") != "chromiq" or not layout.get("patches")
            or "dpi" not in layout or "paper_mm" not in layout):
        raise NotAnEngineChart(
            "This chart wasn't created with ChromIQ's layout engine, so its exact "
            "patch positions aren't available. Create the chart with the layout "
            "engine and scanner files can be built from it. (Support for older / "
            "imported charts is planned.)")
    return layout


def build_scanin_target_from_paths(channels_json: str | Path, ti3_path: str | Path,
                                   out_base: str | Path) -> ScaninTargetResult:
    """Core worker: from an engine ``channels.json`` + a measured ``.ti3``, write
    per-page ``<out_base>[_NN].cht`` and one ``<out_base>.cie``. *out_base* is a
    path without extension (e.g. ``run.dir / run.stem``). Overwrites any existing
    pair, so it always reflects the latest measurement.
    """
    layout = _load_engine_layout(channels_json)
    patches: list[dict] = layout["patches"]
    dpi = int(layout["dpi"])
    paper_h_mm = float(layout["paper_mm"][1])

    data: Ti3Data = parse_ti3(ti3_path)
    measured: dict[str, tuple[float, float, float]] = {
        loc: (float(x), float(y), float(z))
        for loc, (x, y, z) in zip(data.sample_locs, data.xyz)}

    geom_locs = [p["loc"] for p in patches]
    geom_set, ti3_set = set(geom_locs), set(measured)
    if len(geom_set) != len(geom_locs):
        raise GeometryMismatch("The chart geometry has duplicate patch locations.")
    missing = geom_set - ti3_set
    if missing:
        raise GeometryMismatch(
            f"{len(missing)} chart patch(es) have no measurement — the .ti3 "
            f"doesn't match this chart (e.g. {sorted(missing)[:3]}). Re-measure "
            "the whole chart before building scanner files.")

    out_base = Path(out_base)
    pages = sorted({int(p.get("page", 0)) for p in patches})
    single = len(pages) == 1
    cht_paths: list[Path] = []
    for pg in pages:
        boxes = cht_writer.boxes_from_patch_rects(patches, paper_h_mm, dpi, page=pg)
        expected = [(b["loc"], *measured[b["loc"]]) for b in boxes]
        cht = (out_base.with_suffix(".cht") if single
               else out_base.parent / f"{out_base.name}_{pg + 1:02d}.cht")
        cht_paths.append(cht_writer.write_cht(cht, boxes, expected))

    cie_path = cie_writer.write_cie(out_base.with_suffix(".cie"), data,
                                    descriptor=out_base.name)
    return ScaninTargetResult(cht_paths=cht_paths, cie_path=cie_path,
                              n_patches=len(geom_locs), n_pages=len(pages))


def build_scanin_target(run) -> ScaninTargetResult:
    """Build the scanner target for a :class:`~core.file_manager.Run` — reads its
    chart geometry (``chart_channels_json``) + measurement (``measurement_ti3``)
    and writes the pair next to the chart (``<stem>.cht`` / ``<stem>.cie``)."""
    ti3 = run.measurement_ti3
    if not Path(ti3).is_file():
        raise ScaninTargetError(
            "This chart hasn't been measured yet — measure it first, then ChromIQ "
            "can build scanner files from the measurement.")
    return build_scanin_target_from_paths(run.chart_channels_json, ti3,
                                          Path(run.dir) / run.stem)


def is_engine_geometry(channels_json: str | Path) -> bool:
    """True if *channels_json* is an engine chart with exact geometry — the gate
    for *offering* the scanner-target option (the ``.ti3`` need not exist yet, so
    it can be shown right after measuring, before the file is finalised). Never
    raises."""
    try:
        _load_engine_layout(channels_json)
    except ScaninTargetError:
        return False
    return True


def can_build_scanin_target(run) -> bool:
    """True if *run* is an engine chart with a measurement — the gate for showing
    the checkbox / enabling the Tool. Never raises."""
    try:
        _load_engine_layout(run.chart_channels_json)
    except ScaninTargetError:
        return False
    return Path(run.measurement_ti3).is_file()
