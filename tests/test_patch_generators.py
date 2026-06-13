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


def test_skin_tones_dark_ramp_not_cramped():
    # Knut #37: the darkest phototype (VI) used to span a tiny tonal range
    # because the brightness sweep was multiplicative. Every phototype's centre
    # ramp should now cover a comparable lightness length (no group much denser
    # than the others).
    import colorsys
    per = 8
    patches = G.skin_tones(per, 1)
    spans = []
    for gi in range(6):
        vs = [colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[2]
              for r, g, b in patches[gi * per:(gi + 1) * per]]
        spans.append(max(vs) - min(vs))
    # The darkest group (VI) is no longer a fraction of the lightest's length.
    assert min(spans) > 0.6 * max(spans)
    assert spans[5] > 0.2          # VI specifically has a real, usable range


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


@pytest.mark.parametrize("rings,per_step", [(1, 7), (2, 19), (3, 37)])
def test_near_neutral_rings_count(rings, per_step):
    # Ring n adds 6n tints: 1 + 6 (+12) (+18) per step.
    steps = 8
    patches = G.near_neutral_greys(steps, 6.0, rings)
    assert len(patches) == steps * per_step
    assert len(patches) == G.near_neutral_greys_count(steps, rings)
    assert _all_in_range(patches)


def test_near_neutral_rings_default_is_one_and_unchanged():
    # The default (rings=1) is byte-for-byte the original 6-tint hexagon.
    assert G.near_neutral_greys(10, 6.25) == G.near_neutral_greys(10, 6.25, 1)
    assert G.near_neutral_greys_count(10) == G.near_neutral_greys_count(10, 1)


def test_near_neutral_outer_ring_is_farther_and_balanced():
    # With 3 rings each step is: neutral, then ring1 (6), ring2 (12), ring3 (18).
    # Outer rings sit farther from neutral and stay balanced at mid-grey.
    patches = G.near_neutral_greys(5, 8.0, 3)
    base = 2 * 37  # middle step's neutral index
    g = patches[base][0]
    def dist(idx):
        r, gg, b = patches[base + idx]
        assert abs((r + gg + b) / 3.0 - g) < 1e-6   # balanced
        return ((r - g) ** 2 + (gg - g) ** 2 + (b - g) ** 2) ** 0.5
    r1 = dist(1)            # first tint of ring 1
    r2 = dist(1 + 6)        # first tint of ring 2
    r3 = dist(1 + 6 + 12)   # first tint of ring 3
    assert r1 < r2 < r3


# --- gamut edges -----------------------------------------------------------
@pytest.mark.parametrize("per_edge", [1, 2, 5, 20])
def test_gamut_edges_count_and_range(per_edge):
    patches = G.gamut_edges(per_edge)
    assert len(patches) == 12 * per_edge == G.gamut_edges_count(per_edge)
    assert _all_in_range(patches)


def test_gamut_edges_include_corners():
    patches = G.gamut_edges(3)
    for corner in [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 100),
                   (100, 100, 0), (0, 100, 100), (100, 0, 100), (100, 100, 100)]:
        assert tuple(float(c) for c in corner) in patches


# --- highlight & shadow detail ---------------------------------------------
@pytest.mark.parametrize("per_end", [1, 6, 12, 40])
def test_highlight_shadow_count_and_range(per_end):
    patches = G.highlight_shadow_detail(per_end)
    assert len(patches) == 2 * per_end == G.highlight_shadow_detail_count(per_end)
    assert _all_in_range(patches)


def test_highlights_lighter_than_shadows():
    import colorsys
    per = 16
    patches = G.highlight_shadow_detail(per)
    val = lambda p: colorsys.rgb_to_hsv(p[0] / 100, p[1] / 100, p[2] / 100)[2]
    hi = [val(p) for p in patches[:per]]
    lo = [val(p) for p in patches[per:]]
    assert min(hi) > max(lo)            # every highlight lighter than every shadow


# --- image palette ---------------------------------------------------------
def test_image_palette_recovers_distinct_colours():
    import numpy as np
    rng = np.random.default_rng(1)
    blobs = [(220, 30, 40), (40, 180, 60), (30, 50, 200)]
    px = np.clip(np.vstack([np.tile(c, (400, 1)) + rng.integers(-6, 6, (400, 3))
                            for c in blobs]), 0, 255)
    pal = G.image_palette(px, 3, seed=0)
    assert len(pal) == 3
    assert _all_in_range(pal)
    # Each source blob should have a near match among the three centres.
    for c in blobs:
        c100 = tuple(v / 255 * 100 for v in c)
        assert min(sum((a - b) ** 2 for a, b in zip(p, c100)) for p in pal) < 200


def test_image_palette_count_needs_image():
    assert G.image_palette_count(24, has_image=True) == 24
    assert G.image_palette_count(24, has_image=False) == 0
    assert G.image_palette([], 5) == []


# --- pastels ---------------------------------------------------------------
@pytest.mark.parametrize("count", [1, 7, 24, 60])
def test_pastels_count_and_range(count):
    patches = G.pastels(count)
    assert len(patches) == count == G.pastels_count(count)
    assert _all_in_range(patches)


def test_pastels_are_low_chroma():
    import colorsys
    sats = [colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[1]
            for r, g, b in G.pastels(40)]
    assert max(sats) < 0.45            # muted, never vivid
    assert sum(sats) / len(sats) > 0.05  # but not pure greys


# --- fill the gaps ---------------------------------------------------------
def test_fill_gaps_tops_up_to_total():
    existing = G.rgb_cube(3)           # 27 patches
    add = G.fill_gaps(existing, 60, seed=0)
    assert len(add) == 60 - 27 == G.fill_gaps_count(len(existing), 60)
    assert _all_in_range(add)


def test_fill_gaps_noop_when_already_full():
    existing = G.rgb_cube(4)           # 64 patches
    assert G.fill_gaps(existing, 50) == []
    assert G.fill_gaps_count(len(existing), 50) == 0


def test_fill_gaps_avoids_existing_points():
    # New points should sit away from a tight existing cluster, not on top of it.
    existing = [(50.0, 50.0, 50.0)] * 5
    add = G.fill_gaps(existing, 25, candidates=16, seed=2)
    assert len(add) == 20
    # Mean distance of added points from the cluster centre is substantial.
    import math as _m
    dists = [_m.dist(p, (50, 50, 50)) for p in add]
    assert sum(dists) / len(dists) > 20.0


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
