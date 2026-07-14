"""Engine ↔ colprof routing (#122): support gate, multi-ink detection,
Qt build thread, settings round-trip.

The loss-free doctrine under test: the ChromIQ engine only ever takes a
build it fully covers; every colprof-only option pushes the build back to
colprof with a named reason; multi-ink measurements are engine-only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from tests.test_profile_engine import write_synth_ti3  # noqa: E402
from workflow.engine_builder import (EngineProfileBuilder, engine_support,
                                     is_multi_ink, ti3_device_rep)  # noqa: E402
from workflow.profile_builder import ProfileParams  # noqa: E402

_TI3_RGB = '''CTI3
COLOR_REP "iRGB_XYZ"
NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT
NUMBER_OF_SETS 1
BEGIN_DATA
1 100.0 100.0 100.0 96.4 100.0 82.5
END_DATA
'''


def _params(ti3: Path, **kw) -> ProfileParams:
    return ProfileParams(ti3_path=ti3, description="t", **kw)


# ---------------------------------------------------------------------------
# COLOR_REP detection
# ---------------------------------------------------------------------------

def test_multi_ink_detection(tmp_path):
    rgb = tmp_path / "rgb.ti3"
    rgb.write_text(_TI3_RGB)
    assert ti3_device_rep(rgb) == "iRGB"
    assert not is_multi_ink(rgb)
    og = tmp_path / "og.ti3"
    og.write_text(_TI3_RGB.replace('"iRGB_XYZ"', '"CMYKOG_XYZ"'))
    assert is_multi_ink(og)
    cmyk = tmp_path / "c.ti3"
    cmyk.write_text(_TI3_RGB.replace('"iRGB_XYZ"', '"CMYK_XYZ"'))
    assert not is_multi_ink(cmyk)          # colprof covers CMYK
    assert not is_multi_ink(tmp_path / "missing.ti3")


# ---------------------------------------------------------------------------
# Loss-free support gate
# ---------------------------------------------------------------------------

def test_engine_support_defaults_pass(tmp_path):
    ok, why = engine_support(_params(tmp_path / "x.ti3"))
    assert ok and why == ""


def test_engine_support_known_gamut_sources(tmp_path):
    ok, _ = engine_support(_params(tmp_path / "x.ti3",
                                   gamut_src="/ref/ClayRGB1998.icm"))
    assert ok
    ok, why = engine_support(_params(tmp_path / "x.ti3",
                                     gamut_src="/ref/ProPhoto.icm"))
    assert not ok and "gamut source" in why


@pytest.mark.parametrize("kw", [
    dict(algorithm="g"),
    dict(fwa_enabled=True),
    dict(illuminant="D50"),
    dict(extra_args="-y"),
    dict(smoothing=2.0),
    dict(dark_emphasis=2.0),
    dict(no_input_shaper=True),
    dict(b2a_quality="h"),
    dict(src_viewing_cond="pp"),
    dict(perc_intent="la"),
    dict(no_perc_gamut=True),
    dict(wp_mode="u"),
    dict(clip_primaries=True),
    dict(z_surface="m"),
])
def test_engine_support_colprof_only_options(tmp_path, kw):
    ok, why = engine_support(_params(tmp_path / "x.ti3", **kw))
    assert not ok and why


# ---------------------------------------------------------------------------
# The Qt build thread, end to end on a synthetic measurement
# ---------------------------------------------------------------------------

def test_engine_builder_builds_profile(tmp_path, qtbot):
    ti3 = write_synth_ti3(tmp_path / "s.ti3", "iRGB",
                          ["RGB_R", "RGB_G", "RGB_B"], additive=True)
    builder = EngineProfileBuilder()
    params = _params(ti3, quality="l")
    lines: list[str] = []
    finished: list[int] = []
    builder.build(params, on_line=lines.append, on_finish=finished.append)

    def done() -> bool:
        return bool(finished)
    qtbot.waitUntil(done, timeout=60000)
    assert finished == [0]
    icc = builder.expected_icc_path(params)
    assert icc == ti3.with_suffix(".icc") and icc.exists()
    assert any("ChromIQ profile engine" in ln for ln in lines)
    assert builder.primary_failure() is None


def test_engine_builder_reports_failure(tmp_path, qtbot):
    bad = tmp_path / "bad.ti3"
    bad.write_text("not a measurement")
    builder = EngineProfileBuilder()
    finished: list[int] = []
    lines: list[str] = []
    builder.build(_params(bad), on_line=lines.append,
                  on_finish=finished.append)
    qtbot.waitUntil(lambda: bool(finished), timeout=30000)
    assert finished == [1]
    assert builder.primary_failure() is not None
    assert any("[ERROR]" in ln for ln in lines)
