"""Tests for the device-RGB patch-set generators (workflow/patch_generators.py).

Pure logic — no Qt, no Argyll. They pin the count formulas (which the New-chart
dialog mirrors for its live patch totals) and the device-value invariants every
generator must hold (0..100, three channels).
"""
from __future__ import annotations

import pytest

from workflow import patch_generators as G


def _all_in_range(patches) -> bool:
    return all(
        len(p) == 3 and all(0.0 <= c <= 100.0 for c in p) for p in patches
    )


# --- cube ------------------------------------------------------------------
@pytest.mark.parametrize("n", [2, 3, 4, 8, 17])
def test_rgb_cube_count_and_range(n):
    patches = G.rgb_cube(n)
    assert len(patches) == n ** 3 == G.rgb_cube_count(n)
    assert _all_in_range(patches)


def test_rgb_cube_hits_corners():
    patches = G.rgb_cube(4)
    assert (0.0, 0.0, 0.0) in patches
    assert (100.0, 100.0, 100.0) in patches


def test_rgb_cube_clamps_tiny_n():
    assert len(G.rgb_cube(1)) == 8  # floored to n=2
    assert G.rgb_cube_count(0) == 8


# --- skin tones ------------------------------------------------------------
@pytest.mark.parametrize("per", [1, 5, 12, 36])
def test_skin_tones_count_and_range(per):
    patches = G.skin_tones(per)
    assert len(patches) == 6 * per == G.skin_tones_count(per)
    assert _all_in_range(patches)


def test_skin_tones_light_to_dark_ordering():
    # Each group's mid patch should get darker from Fitzpatrick I to VI.
    per = 5
    patches = G.skin_tones(per)
    mids = [patches[g * per + per // 2] for g in range(6)]
    lums = [0.3 * r + 0.59 * gg + 0.11 * b for r, gg, b in mids]
    assert lums == sorted(lums, reverse=True)


# --- blues / greens --------------------------------------------------------
@pytest.mark.parametrize("count", [1, 5, 7, 20, 50])
def test_blues_count_and_range(count):
    patches = G.blues(count)
    assert len(patches) == count == G.blues_count(count)
    assert _all_in_range(patches)


def test_blues_are_blue_dominant():
    # Across the band the blue channel should dominate red on average.
    patches = G.blues(20)
    assert sum(b for _, _, b in patches) > sum(r for r, _, _ in patches)


@pytest.mark.parametrize("count", [1, 5, 7, 20, 50])
def test_greens_count_and_range(count):
    patches = G.greens(count)
    assert len(patches) == count == G.greens_count(count)
    assert _all_in_range(patches)


def test_greens_are_green_dominant():
    patches = G.greens(20)
    assert sum(g for _, g, _ in patches) > sum(r for r, _, _ in patches)
    assert sum(g for _, g, _ in patches) > sum(b for _, _, b in patches)


# --- near-neutral greys ----------------------------------------------------
@pytest.mark.parametrize("steps", [1, 4, 16, 32])
def test_near_neutral_count_and_range(steps):
    patches = G.near_neutral_greys(steps, 6.25)
    assert len(patches) == steps * 7 == G.near_neutral_greys_count(steps)
    assert _all_in_range(patches)


def test_near_neutral_first_of_each_step_is_neutral():
    steps = 8
    patches = G.near_neutral_greys(steps, 6.25)
    for i in range(steps):
        r, g, b = patches[i * 7]
        assert r == g == b  # the neutral anchor of each step


def test_near_neutral_shift_is_balanced():
    # A balanced hue shift keeps the mean at the grey level (away from the
    # 0/100 clamps), so each ring patch shares its step's luminance.
    patches = G.near_neutral_greys(5, 8.0)
    g = patches[2 * 7][0]  # neutral value of the middle step (~50)
    for k in range(1, 7):
        r, gg, b = patches[2 * 7 + k]
        assert abs((r + gg + b) / 3.0 - g) < 1e-6
        assert (r, gg, b) != (g, g, g)  # actually shifted
