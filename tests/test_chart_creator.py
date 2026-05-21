"""Regression tests for the ChartCreator sidecar pipeline.

Issue #15 surfaced a gap: `_printtarg_done` writes the `<stem>.channels.json`
sidecar only when `self._pending_params` is non-None. The `generate()` entry
point sets it, but `load_ti1_and_generate_preview()` did not — so loading a
chart from an existing .ti1 produced no sidecar, leaving the preview unable
to identify inks in a future session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data.patch_db import (
    I1PRO_DEFAULT_PRESET_KEY,
    I1PRO_DEFAULT_PRESETS,
    INSTRUMENT_DEFAULT_MARGIN,
    i1_defaults_from_preset,
    query_patches,
)
from workflow.chart_creator import (
    ChartCreator,
    ChartParams,
    guided_neutrals,
    manual_neutrals,
)


class _MockRunner:
    """Synchronously fire on_finish(0) and stage the files printtarg would create."""

    def run(self, tool, args, cwd, on_line=None, on_finish=None):
        cwd = Path(cwd)
        stem = args[-1]
        if tool == "targen":
            (cwd / f"{stem}.ti1").write_text("FAKE TI1")
        elif tool == "printtarg":
            (cwd / f"{stem}.ti2").write_text("FAKE TI2")
            arr = np.zeros((100, 100, 3), dtype=np.uint8)
            tifffile.imwrite(
                str(cwd / f"{stem}_01.tif"),
                arr,
                resolution=(200, 200),
                resolutionunit="INCH",
            )
        if on_finish:
            on_finish(0)


class _MockFileManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_folder(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def clean_folder(self, exts: list[str]) -> None:
        for f in self.root.iterdir():
            if f.is_file() and f.suffix.lstrip(".").lower() in exts:
                f.unlink()


class _MockSettings:
    def get(self, key, default=None):
        return default


def _make_creator(tmp_path: Path) -> tuple[ChartCreator, Path]:
    work_dir = tmp_path / "chart_proj"
    return ChartCreator(_MockRunner(), _MockFileManager(work_dir), _MockSettings()), work_dir


def test_generate_writes_channels_sidecar(tmp_path: Path) -> None:
    creator, work_dir = _make_creator(tmp_path)
    finished: list[list[Path]] = []
    creator.generate(
        ChartParams(target_name="mychart", device_type="2"),
        on_line=lambda _: None,
        on_finish=lambda tiffs: finished.append(tiffs),
    )
    sidecar = work_dir / "mychart.channels.json"
    assert sidecar.exists(), "generate() must write the channels sidecar"
    assert json.loads(sidecar.read_text())["ink_channels"] == ["r", "g", "b"]


def test_query_patches_margin10_i1_a4_with_left_border() -> None:
    """margin=10 i1/A4 must return the measured table value (not the m=6 baseline)."""
    n_m6 = query_patches("i1", "A4", suppress_lb=True, margin_mm=6)
    n_m10 = query_patches("i1", "A4", suppress_lb=True, margin_mm=10)
    assert n_m6 is not None and n_m10 is not None
    assert n_m10 < n_m6, "margin=10 must fit fewer patches than margin=6"
    assert n_m10 == 483, "regression guard against accidental table edits"


def test_query_patches_margin10_respects_left_border_flag() -> None:
    """The -L vs no-L distinction must propagate through margin=10 lookups."""
    with_l = query_patches("i1", "A4", suppress_lb=True, margin_mm=10)
    without_l = query_patches("i1", "A4", suppress_lb=False, margin_mm=10)
    assert with_l is not None and without_l is not None
    assert with_l > without_l, "-L must yield more patches than no-L"


def test_query_patches_unsupported_margin_returns_none() -> None:
    """Margin values outside {6, 10} must return None so callers fall back to binary search."""
    assert query_patches("i1", "A4", margin_mm=8) is None
    assert query_patches("i1", "A4", margin_mm=15) is None


def test_query_patches_margin10_only_for_i1_and_p3() -> None:
    """CM and SS don't change defaults, so their margin=10 lookups should be missing."""
    assert query_patches("CM", "A4", margin_mm=10) is None
    assert query_patches("SS", "A4", margin_mm=10) is None


def test_query_patches_scale095_i1_a4_with_left_border() -> None:
    """scale=0.95 must return a measured value bigger than the scale=1.0 baseline."""
    n_10 = query_patches("i1", "A4", suppress_lb=True, margin_mm=6, patch_scale=1.0)
    n_95 = query_patches("i1", "A4", suppress_lb=True, margin_mm=6, patch_scale=0.95)
    assert n_10 is not None and n_95 is not None
    assert n_95 > n_10, "smaller patches must fit more per sheet"
    assert n_95 == 550, "regression guard against accidental table edits"


def test_query_patches_scale095_respects_margin_and_lb() -> None:
    """scale=0.95 dispatch must propagate margin and -L flags."""
    m6_lb  = query_patches("i1", "A4", suppress_lb=True,  margin_mm=6,  patch_scale=0.95)
    m6_nolb = query_patches("i1", "A4", suppress_lb=False, margin_mm=6,  patch_scale=0.95)
    m10_lb = query_patches("i1", "A4", suppress_lb=True,  margin_mm=10, patch_scale=0.95)
    assert m6_lb is not None and m6_nolb is not None and m10_lb is not None
    assert m6_lb > m6_nolb, "-L must yield more patches than no-L"
    assert m6_lb > m10_lb,  "m=6 must yield more patches than m=10"


def test_query_patches_scale095_covers_cm_double_density() -> None:
    """CM at scale=0.95 must be covered for both -h states; -h gives ~2× capacity."""
    cm_std = query_patches("CM", "A4", double_density=False, margin_mm=6, patch_scale=0.95)
    cm_dd  = query_patches("CM", "A4", double_density=True,  margin_mm=6, patch_scale=0.95)
    assert cm_std is not None and cm_dd is not None
    assert cm_dd > cm_std, "-h must roughly double CM capacity"


def test_query_patches_scale095_no_cm_at_margin10() -> None:
    """CM doesn't have m=10 entries at any scale — must return None to trigger fallback."""
    assert query_patches("CM", "A4", margin_mm=10, patch_scale=0.95) is None


def test_query_patches_unsupported_scale_returns_none() -> None:
    """Scales outside SUPPORTED_PATCH_SCALES must return None so callers fall back."""
    assert query_patches("i1", "A4", margin_mm=6, patch_scale=0.85) is None
    assert query_patches("i1", "A4", margin_mm=6, patch_scale=1.10) is None


def test_query_patches_no_strip_limit_i1_big_paper() -> None:
    """-P removes the strip-length cap; big papers gain a lot of capacity."""
    base = query_patches("i1", "A2", suppress_lb=True, margin_mm=6, patch_scale=1.0)
    nsl  = query_patches("i1", "A2", suppress_lb=True, margin_mm=6, patch_scale=1.0,
                         no_strip_limit=True)
    assert base is not None and nsl is not None
    assert nsl > base, "-P must add patches when the cap previously bit"
    assert nsl == 2500, "regression guard against accidental table edits"


def test_query_patches_no_strip_limit_respects_margin_scale_lb() -> None:
    """-P dispatch must propagate margin, scale and -L through to the right table."""
    m6_lb   = query_patches("i1", "A2", suppress_lb=True,  margin_mm=6,  patch_scale=1.0,  no_strip_limit=True)
    m6_nolb = query_patches("i1", "A2", suppress_lb=False, margin_mm=6,  patch_scale=1.0,  no_strip_limit=True)
    m10_lb  = query_patches("i1", "A2", suppress_lb=True,  margin_mm=10, patch_scale=1.0,  no_strip_limit=True)
    a095_lb = query_patches("i1", "A2", suppress_lb=True,  margin_mm=6,  patch_scale=0.95, no_strip_limit=True)
    assert all(v is not None for v in (m6_lb, m6_nolb, m10_lb, a095_lb))
    assert m6_lb > m6_nolb, "-L must yield more patches than no-L under -P"
    assert m6_lb >= m10_lb, "m=6 must yield at least as many patches as m=10 under -P"
    assert a095_lb > m6_lb, "smaller patches (-a 0.95) must fit more per sheet under -P"


def test_query_patches_no_strip_limit_ignored_for_cm_and_ss() -> None:
    """-P only affects i1/p3 strip layouts; CM/SS must return their normal values."""
    assert (query_patches("CM", "A4", no_strip_limit=True)
            == query_patches("CM", "A4", no_strip_limit=False))
    assert (query_patches("SS", "A4", no_strip_limit=True)
            == query_patches("SS", "A4", no_strip_limit=False))


def test_query_patches_a2_landscape_all_instruments() -> None:
    """A2 landscape (594x420) is measured for every instrument + dd combo."""
    assert query_patches("i1", "594x420", suppress_lb=True, margin_mm=6) is not None
    assert query_patches("p3", "594x420", suppress_lb=True, margin_mm=6) is not None
    assert query_patches("CM", "594x420", double_density=False) is not None
    assert query_patches("CM", "594x420", double_density=True) is not None
    assert query_patches("SS", "594x420", double_density=False) is not None
    assert query_patches("SS", "594x420", double_density=True) is not None
    assert query_patches("CM", "594x420", triple_density=True) is not None


def test_query_patches_a2_landscape_beats_portrait_for_strip_readers() -> None:
    """Landscape A2 packs more strips than portrait A2 on i1/p3 (the whole point)."""
    for instr in ("i1", "p3"):
        port = query_patches(instr, "A2", suppress_lb=True, margin_mm=6)
        land = query_patches(instr, "594x420", suppress_lb=True, margin_mm=6)
        assert port is not None and land is not None
        assert land > port, f"{instr}: landscape A2 should beat portrait"


def test_query_patches_a2_landscape_regression_guard() -> None:
    """Exact-value guards against accidental table edits."""
    assert query_patches("i1", "594x420", suppress_lb=True, margin_mm=6) == 1512
    assert query_patches("p3", "594x420", suppress_lb=True, margin_mm=6) == 324
    assert query_patches("i1", "594x420", suppress_lb=True, margin_mm=6,
                         no_strip_limit=True) == 2520
    assert query_patches("CM", "594x420", triple_density=True) == 1485


def test_grey_ramp_reference_default_anchor() -> None:
    """At the default anchor (560 patches) the result is the classic -g32 -e4 -B4."""
    assert manual_neutrals(560) == (32, 4, 4)


def test_grey_ramp_reference_lower_anchor_is_denser() -> None:
    """Halving the anchor must roughly double neutral density (eff_sheets doubles)."""
    g0, w0, b0 = manual_neutrals(560)
    g1, w1, b1 = manual_neutrals(560, ref_budget=280)
    assert g1 > g0 and w1 >= w0 and b1 >= b0


def test_grey_ramp_reference_higher_anchor_is_sparser() -> None:
    """Doubling the anchor must reduce neutral density (eff_sheets halves)."""
    g0, _, _ = manual_neutrals(560)
    g1, _, _ = manual_neutrals(560, ref_budget=1120)
    assert g1 < g0


def test_grey_ramp_reference_floors_hold_for_tiny_targets() -> None:
    """A tiny target must keep the minimum neutral set regardless of the anchor."""
    # 50 patches with a low anchor must still floor at grey>=8, white/black>=2.
    g, w, b = manual_neutrals(50, ref_budget=280)
    assert g == 8 and w == 2 and b == 2


def test_grey_ramp_reference_applies_to_guided() -> None:
    """The anchor must propagate through guided_neutrals too."""
    g0, _, _ = guided_neutrals("i1", "A4", 1, 4, 4, True)
    g1, _, _ = guided_neutrals("i1", "A4", 1, 4, 4, True, ref_budget=280)
    assert g1 > g0


def test_i1_defaults_from_preset_known_keys() -> None:
    """All three documented preset keys must return the printtarg values they encode."""
    assert i1_defaults_from_preset("m6_a1.0")   == (6,  1.0)
    assert i1_defaults_from_preset("m10_a1.0")  == (10, 1.0)
    assert i1_defaults_from_preset("m10_a0.95") == (10, 0.95)


def test_i1_defaults_from_preset_app_default() -> None:
    """The app-wide default key must resolve to m=10, a=0.95."""
    assert I1PRO_DEFAULT_PRESET_KEY == "m10_a0.95"
    assert i1_defaults_from_preset(I1PRO_DEFAULT_PRESET_KEY) == (10, 0.95)


def test_i1_defaults_from_preset_unknown_falls_back() -> None:
    """An unknown / corrupted preset key falls back to the recommended default."""
    assert i1_defaults_from_preset("bogus")    == (10, 0.95)
    assert i1_defaults_from_preset("")         == (10, 0.95)


def test_i1_defaults_only_uses_supported_values() -> None:
    """All preset outputs must be in SUPPORTED_MARGINS × SUPPORTED_PATCH_SCALES."""
    from data.patch_db import SUPPORTED_MARGINS, SUPPORTED_PATCH_SCALES
    for margin, scale in I1PRO_DEFAULT_PRESETS.values():
        assert margin in SUPPORTED_MARGINS
        assert any(abs(scale - s) <= 0.01 for s in SUPPORTED_PATCH_SCALES)


def test_instrument_default_margin_keys() -> None:
    """i1 defaults to 10mm (edge-drift headroom); p3/CM/SS use printtarg's 6mm."""
    assert INSTRUMENT_DEFAULT_MARGIN["i1"] == 10
    assert INSTRUMENT_DEFAULT_MARGIN["p3"] == 6
    assert INSTRUMENT_DEFAULT_MARGIN["CM"] == 6
    assert INSTRUMENT_DEFAULT_MARGIN["SS"] == 6


def test_printtarg_done_dedupes_case_insensitive_glob(tmp_path: Path) -> None:
    """Single chart.tif must not appear twice in the preview list on Windows.

    Regression guard for forum bug #148124's "Page 1/2 from one file" symptom:
    pathlib.Path.glob is case-insensitive on Windows, so the prior code's
    sorted([*glob('*.tif'), *glob('*.TIF'), *glob('*.tiff')]) returned the
    same file two or three times.
    """
    creator, work_dir = _make_creator(tmp_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Pretend printtarg produced a single TIFF for a one-page chart
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    tifffile.imwrite(
        str(work_dir / "single.tif"), arr,
        resolution=(200, 200), resolutionunit="INCH",
    )

    captured: list[list[Path]] = []
    creator._pending_params = ChartParams(target_name="single", device_type="2")
    creator._printtarg_done(0, work_dir, lambda t: captured.append(t), "single")

    assert captured, "on_finish was never called"
    assert len(captured[0]) == 1, f"expected 1 TIFF, got {len(captured[0])}: {captured[0]}"


def test_load_ti1_writes_channels_sidecar(tmp_path: Path) -> None:
    creator, work_dir = _make_creator(tmp_path)
    work_dir.mkdir(parents=True)
    src_ti1 = work_dir / "imported.ti1"
    src_ti1.write_text("FAKE TI1")

    finished: list[list[Path]] = []
    creator.load_ti1_and_generate_preview(
        src_ti1,
        ChartParams(target_name="imported", device_type="2"),
        on_line=lambda _: None,
        on_finish=lambda tiffs: finished.append(tiffs),
    )
    sidecar = work_dir / "imported.channels.json"
    assert sidecar.exists(), (
        "load_ti1_and_generate_preview() must set _pending_params so the "
        "sidecar is written — regression guard for the second half of #15"
    )
    assert json.loads(sidecar.read_text())["ink_channels"] == ["r", "g", "b"]
