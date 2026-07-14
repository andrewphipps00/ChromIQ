"""Gamut mapping (P4, #122): analytic sources, mapper behaviour, intents.

The mapper's contract (measured against colprof's realized perceptual
behaviour, issue #122 maths F): neutral colours only move along L*, the
protected core below the knee stays put, far out-of-gamut colours land at or
inside the destination surface, and a build with a gamut source writes
*distinct* B2A0/B2A1/B2A2 tables.
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import numpy as np
import pytest

from tests.test_profile_engine import write_synth_ti3
from workflow.profile_engine import BuildSettings, build_profile
from workflow.profile_engine.forward_model import fit_forward_model
from workflow.profile_engine.gamut_map import (GamutMapper, GamutSourceError,
                                               destination_surface_lab,
                                               source_kind,
                                               source_surface_lab)


def test_source_kind_recognises_chromiq_gamut_sources():
    assert source_kind("/x/ClayRGB.icm") == "adobe"
    assert source_kind("AdobeRGB1998.icc") == "adobe"
    assert source_kind("sRGB.icm") == "srgb"
    with pytest.raises(GamutSourceError):
        source_kind("WideGamutRGB.icc")


def test_source_surfaces_are_sane():
    for kind in ("adobe", "srgb"):
        lab = source_surface_lab(kind)
        assert lab[:, 0].min() < 5.0 and lab[:, 0].max() > 99.0
        # AdobeRGB green corner is more saturated than sRGB's
    c_adobe = np.hypot(*source_surface_lab("adobe")[:, 1:].T).max()
    c_srgb = np.hypot(*source_surface_lab("srgb")[:, 1:].T).max()
    assert c_adobe > c_srgb


@pytest.fixture(scope="module")
def rgb_model(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("map")
    ti3 = write_synth_ti3(tmp / "m.ti3", "iRGB",
                          ["RGB_R", "RGB_G", "RGB_B"], additive=True,
                          n_per_axis=6)
    from workflow.profile_engine.ti3_data import read_ti3
    meas = read_ti3(ti3)
    return fit_forward_model(meas.device, meas.lab_relative, grid=9,
                             lam=0.03)


def test_mapper_contract(rgb_model):
    src = source_surface_lab("adobe")
    dst = destination_surface_lab(rgb_model, mesh=17)
    mapper = GamutMapper(src, dst)
    # neutral colours: chroma stays ~0, L compressed into the printable range
    neutral = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [100.0, 0.0, 0.0]])
    out = mapper.map_lab(neutral)
    assert np.abs(out[:, 1:]).max() < 1e-6
    assert out[0, 0] >= neutral[0, 0]           # black lifted to printable
    assert out[2, 0] <= 100.0
    assert np.all(np.diff(out[:, 0]) > 0)       # monotone in L
    # a comfortably in-gamut colour moves only by the tone-scale shift (the
    # synthetic printer's black is pale, L*≈32, so absolute L movement is
    # expected — but chroma must stay put in the protected core) …
    mid = np.array([[55.0, 20.0, 10.0]])
    mid_out = mapper.map_lab(mid)
    d_mid = np.linalg.norm(mid_out - mid)
    assert abs(np.hypot(*mid_out[0, 1:]) - np.hypot(*mid[0, 1:])) < 2.0
    # … while far out-of-gamut colours are compressed much further
    far = np.array([[50.0, 120.0, -100.0]])
    moved = mapper.map_lab(far)
    d_far = np.linalg.norm(moved - far)
    assert d_far > 2.0 * d_mid and d_far > 20.0
    # and land at/inside the destination radius for their direction
    hb = int((np.degrees(np.arctan2(moved[0, 2], moved[0, 1])) % 360)
             / 360 * 48) % 48
    assert np.hypot(moved[0, 1], moved[0, 2]) <= mapper.tab_dst[hb].max() * 1.05


def test_build_with_gamut_source_writes_distinct_intents(tmp_path):
    ti3 = write_synth_ti3(tmp_path / "s.ti3", "iRGB",
                          ["RGB_R", "RGB_G", "RGB_B"], additive=True)
    out = tmp_path / "s.icc"
    res = build_profile(ti3, out, BuildSettings(
        quality="l", source_gamut="ClayRGB.icm",
        timestamp=datetime(2026, 7, 14, tzinfo=timezone.utc)))
    assert res.perceptual_distinct
    blob = out.read_bytes()
    ntags = struct.unpack(">I", blob[128:132])[0]
    entries = {}
    for i in range(ntags):
        sig, off, size = struct.unpack_from(">4sII", blob, 132 + 12 * i)
        entries[sig] = (off, size)
    assert entries[b"B2A0"] != entries[b"B2A1"]
    assert entries[b"B2A2"] != entries[b"B2A1"]
    assert entries[b"B2A2"] != entries[b"B2A0"]


def test_build_with_unknown_gamut_source_fails_honestly(tmp_path):
    ti3 = write_synth_ti3(tmp_path / "s.ti3", "iRGB",
                          ["RGB_R", "RGB_G", "RGB_B"], additive=True)
    with pytest.raises(GamutSourceError):
        build_profile(ti3, tmp_path / "s.icc", BuildSettings(
            quality="l", source_gamut="ProPhoto.icc"))
