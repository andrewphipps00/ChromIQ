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
@pytest.mark.parametrize("ranges", [1, 3, 5])
def test_skin_tones_count_and_range(per, ranges):
    patches = G.skin_tones(per, ranges)
    assert len(patches) == 6 * ranges * per == G.skin_tones_count(per, ranges)
    assert _all_in_range(patches)


def test_skin_tones_default_ranges_is_three():
    assert G.skin_tones_count(5) == 6 * 3 * 5
    assert len(G.skin_tones(5)) == 90


def test_skin_tones_light_to_dark_ordering():
    # Each phototype's mid patch (centre ramp) should get darker I → VI.
    per, ranges = 5, 1
    patches = G.skin_tones(per, ranges)
    mids = [patches[g * per + per // 2] for g in range(6)]
    lums = [0.3 * r + 0.59 * gg + 0.11 * b for r, gg, b in mids]
    assert lums == sorted(lums, reverse=True)


def test_skin_tones_ranges_add_hue_variation():
    # With >1 range, a phototype's patches span more than one hue.
    import colorsys
    per, ranges = 4, 3
    patches = G.skin_tones(per, ranges)
    block = patches[:per * ranges]  # all ramps of Fitzpatrick I
    hues = {round(colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[0], 3)
            for r, g, b in block}
    assert len(hues) > 1


# --- blues / greens --------------------------------------------------------
@pytest.mark.parametrize("count", [1, 5, 7, 20, 50])
@pytest.mark.parametrize("layers", [1, 2, 3, 5])
def test_blues_count_and_range(count, layers):
    patches = G.blues(count, layers)
    assert len(patches) == count == G.blues_count(count)
    assert _all_in_range(patches)


def test_blues_are_blue_dominant():
    # Across the band the blue channel should dominate red on average.
    patches = G.blues(20)
    assert sum(b for _, _, b in patches) > sum(r for r, _, _ in patches)


def test_blues_reach_greenish_turquoise():
    # The widened band must include patches where green leads red (the
    # green-turquoise corner Knut asked for).
    patches = G.blues(40)
    assert any(g > r and g > 10 for r, g, _ in patches)


@pytest.mark.parametrize("count", [1, 5, 7, 20, 50])
@pytest.mark.parametrize("layers", [1, 2, 3, 5])
def test_greens_count_and_range(count, layers):
    patches = G.greens(count, layers)
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


# --- de-duplication --------------------------------------------------------
def _keys(patches, quantum=0.5):
    return [tuple(round(c / quantum) for c in p) for p in patches]


def test_deduplicate_preserves_count_and_makes_unique():
    # A program full of identical patches stays the same length but every
    # entry ends up on a distinct grid cell.
    dupes = [(50.0, 50.0, 50.0)] * 20
    out = G.deduplicate(dupes)
    assert len(out) == 20
    assert _all_in_range(out)
    assert len(set(_keys(out))) == 20


def test_deduplicate_leaves_unique_input_untouched():
    src = G.rgb_cube(4)  # already distinct
    out = G.deduplicate(src)
    assert out == src


def test_deduplicate_collapses_shared_corners():
    # Cube + grey ramp share black/white/grey corners; after dedupe none repeat.
    combined = G.rgb_cube(3) + G.near_neutral_greys(3, 6.0)
    out = G.deduplicate(combined)
    assert len(out) == len(combined)
    assert len(set(_keys(out))) == len(out)
