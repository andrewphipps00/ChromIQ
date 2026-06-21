"""Tests for the device-RGB patch-set generators (workflow/patch_generators.py).

Pure logic — no Qt, no Argyll. They pin the count formulas (which the New-chart
dialog mirrors for its live patch totals) and the device-value invariants every
generator must hold (0..100, three channels).
"""
from __future__ import annotations

import math

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


def _lab_hue_angles(patches):
    import math as _m
    labs = G._srgb_to_lab([[r / 100, g / 100, b / 100] for r, g, b in patches])
    return labs, [_m.degrees(_m.atan2(b, a)) % 360.0 for _, a, b in labs]


def test_skin_tones_stay_in_skin_locus_with_many_ranges():
    # #53: extra ranges must not wander out of the warm skin wedge — every patch
    # keeps a positive a* (never greenish) and a hue angle inside the band, even
    # at the maximum five ranges where the old HSV fan drifted toward 85°.
    labs, hues = _lab_hue_angles(G.skin_tones(10, 5))
    assert (labs[:, 1] >= 0).all()                 # no green undertones
    assert all(G._SKIN_HUE_LO - 1.0 <= h <= G._SKIN_HUE_HI + 1.0 for h in hues)


def test_skin_tones_undertone_fan_clamped_to_wedge():
    # The undertone fan is symmetric about each anchor but clamped, so even the
    # widest fan stays inside the wedge rather than escaping it.
    _, hues = _lab_hue_angles(G.skin_tones(3, 5))
    assert min(hues) >= G._SKIN_HUE_LO - 1.0
    assert max(hues) <= G._SKIN_HUE_HI + 1.0


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


# --- sunrises (warm band) --------------------------------------------------
@pytest.mark.parametrize("count", [1, 5, 7, 20, 50])
@pytest.mark.parametrize("layers", [1, 2, 3, 5])
def test_sunrises_count_and_range(count, layers):
    patches = G.sunrises(count, layers)
    assert len(patches) == count == G.sunrises_count(count)
    assert _all_in_range(patches)


def test_sunrises_are_warm():
    # The warm band is red-dominant: red leads blue everywhere, and on average
    # red is the strongest channel (yellows/oranges/reds/pinks all have high R).
    patches = G.sunrises(40)
    assert all(r >= b for r, _, b in patches)
    assert sum(r for r, _, _ in patches) > sum(g for _, g, _ in patches)
    assert sum(r for r, _, _ in patches) > sum(b for _, _, b in patches)


def test_sunrises_span_yellow_through_pink():
    import colorsys
    hues = sorted((colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[0] * 360.0)
                  for r, g, b in G.sunrises(80))
    # Yellows near 60° and warm pinks/magentas up near 330°+ are both present.
    assert any(h >= 55.0 for h in hues)
    assert any(h >= 320.0 for h in hues)


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


@pytest.mark.parametrize("steps", [1, 8, 21])
def test_near_neutral_zero_rings_is_pure_ramp(steps):
    # rings=0 ⇒ just the neutral ramp, no tints; offset has no effect.
    patches = G.near_neutral_greys(steps, 6.0, 0)
    assert len(patches) == steps == G.near_neutral_greys_count(steps, 0)
    for r, g, b in patches:
        assert r == g == b                      # every patch is neutral
    # The offset value is irrelevant when there are no rings.
    assert G.near_neutral_greys(steps, 99.0, 0) == patches


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


# --- near-neutral greys "in between" --------------------------------------
@pytest.mark.parametrize("steps,between", [(2, 1), (16, 1), (16, 3), (8, 2)])
def test_greys_between_count_and_range(steps, between):
    # rings=0 ⇒ (steps-1)*between pure greys, lining up between the parent ramp.
    patches = G.near_neutral_greys_between(steps, between, 4.0, 0)
    assert len(patches) == (steps - 1) * between
    assert len(patches) == G.near_neutral_greys_between_count(steps, between, 0)
    for r, g, b in patches:
        assert r == g == b                      # every patch is neutral
    assert _all_in_range(patches)


def test_greys_between_sits_at_gap_midpoints_and_avoids_endpoints():
    # between=1 ⇒ the midpoint of every parent gap; never the 0/100 endpoints.
    steps = 5                                    # parent at 0,25,50,75,100
    patches = G.near_neutral_greys_between(steps, 1, 4.0, 0)
    ls = sorted(p[0] for p in patches)
    assert ls == pytest.approx([12.5, 37.5, 62.5, 87.5])
    assert all(0.0 < l < 100.0 for l in ls)


def test_greys_between_two_per_gap_evenly_spaced():
    # between=2 ⇒ the 1/3 and 2/3 points of each gap.
    patches = G.near_neutral_greys_between(2, 2, 4.0, 0)   # one gap 0..100
    ls = sorted(p[0] for p in patches)
    assert ls == pytest.approx([100 / 3.0, 200 / 3.0])


@pytest.mark.parametrize("rings,per", [(1, 7), (2, 19), (3, 37)])
def test_greys_between_rings_match_parent(rings, per):
    # Each inserted grey carries the same ring count as the parent set.
    steps, between = 16, 1
    patches = G.near_neutral_greys_between(steps, between, 6.0, rings)
    assert len(patches) == (steps - 1) * between * per
    assert len(patches) == G.near_neutral_greys_between_count(steps, between, rings)
    assert _all_in_range(patches)


def test_greys_between_degenerate_cases_are_empty():
    assert G.near_neutral_greys_between(1, 5, 4.0, 0) == []   # no parent gaps
    assert G.near_neutral_greys_between(16, 0, 4.0, 0) == []  # nothing per gap
    assert G.near_neutral_greys_between_count(1, 5, 0) == 0
    assert G.near_neutral_greys_between_count(16, 0, 0) == 0


def test_greys_between_interleaves_parent_without_duplicates():
    # The combined ramp (parent + in-between, both pure) has no repeated greys.
    parent = G.near_neutral_greys(16, 4.0, 0)
    mid = G.near_neutral_greys_between(16, 1, 4.0, 0)
    combined = sorted(p[0] for p in parent + mid)
    assert len(combined) == len(set(round(l, 6) for l in combined))


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


# --- gamut faces -----------------------------------------------------------
@pytest.mark.parametrize("per_face", [0, 1, 2, 5])
def test_gamut_faces_count_and_range(per_face):
    patches = G.gamut_faces(per_face)
    assert len(patches) == 6 * per_face * per_face == G.gamut_faces_count(per_face)
    assert _all_in_range(patches)


def test_gamut_faces_interior_and_on_a_face():
    # Every face point pins exactly one channel at 0 or 100 (it's on a face),
    # and the other two are strictly interior (not on an edge/corner).
    for p in G.gamut_faces(4):
        pinned = [i for i, c in enumerate(p) if c in (0.0, 100.0)]
        assert len(pinned) == 1
        free = [c for i, c in enumerate(p) if i not in pinned]
        assert all(0.0 < c < 100.0 for c in free)


# --- saturated edges/faces are boundary-aware (#53) ------------------------
def test_gamut_edges_standalone_unchanged():
    # With no existing patches the layout is the original even spacing.
    assert G.gamut_edges(6, None) == G.gamut_edges(6)
    assert G.gamut_edges(6, []) == G.gamut_edges(6)
    assert G.gamut_faces(5, None) == G.gamut_faces(5)


def test_gamut_edges_fill_gaps_left_by_a_cube():
    # When the 3D cube has already sampled the boundary, the saturated set must
    # land at the midpoints between those points, not on top of them (#53, Knut).
    cube = G.rgb_cube(8)
    naive = G.gamut_edges(6) + G.gamut_faces(6)
    aware = G.gamut_edges(6, cube) + G.gamut_faces(6, cube)
    assert len(aware) == len(naive)                       # count is unchanged
    assert G.overlap_count(cube, aware) == 0              # no exact re-sampling
    assert G.overlap_count(cube, naive) > 0               # the old behaviour did
    # And the boundary-aware patches sit farther from the cube points on average.
    import math as _m
    mean = lambda S: sum(min(_m.dist(p, c) for c in cube) for p in S) / len(S)
    assert mean(aware) > mean(naive)


def test_gamut_boundary_aware_stays_on_the_boundary():
    # Even when filling gaps, every patch keeps a channel pinned at 0 or 100.
    cube = G.rgb_cube(6)
    aware = G.gamut_edges(5, cube) + G.gamut_faces(5, cube)
    for p in aware:
        assert any(abs(c) < 1e-6 or abs(c - 100.0) < 1e-6 for c in p)
    assert _all_in_range(aware)


# --- even stepwise edges / faces between the cube steps (Knut, #78) ---------
@pytest.mark.parametrize("cube_n,per_gap", [(2, 3), (5, 1), (6, 2), (8, 4)])
def test_gamut_edges_between_count_and_range(cube_n, per_gap):
    pts = G.gamut_edges_between(cube_n, per_gap)
    assert len(pts) == 12 * per_gap * (cube_n - 1)
    assert len(pts) == G.gamut_edges_between_count(cube_n, per_gap)
    assert _all_in_range(pts)


def test_gamut_edges_between_is_evenly_spaced():
    # On any edge, the fill points unioned with the cube's own edge points must
    # be exactly evenly spaced — the whole point of the stepwise control.
    cube_n, per_gap = 5, 2
    pts = G.gamut_edges_between(cube_n, per_gap)
    cube_levels = [i / (cube_n - 1) * 100.0 for i in range(cube_n)]
    fill = [p[0] for p in pts if p[1] == 0 and p[2] == 0]        # K→R edge
    assert len(fill) == per_gap * (cube_n - 1)
    # No fill point lands on a cube point …
    assert all(min(abs(x - cl) for cl in cube_levels) > 1e-6 for x in fill)
    # … and cube + fill together are uniformly spaced.
    allpos = sorted(cube_levels + fill)
    diffs = [b - a for a, b in zip(allpos, allpos[1:])]
    assert max(diffs) - min(diffs) < 1e-6


def test_gamut_edges_between_zero_and_cube_off():
    assert G.gamut_edges_between(8, 0) == []
    # Cube off ⇒ cube_n = 2: a single gap per edge, so per_gap patches per edge.
    assert len(G.gamut_edges_between(2, 3)) == 12 * 3


@pytest.mark.parametrize("cube_n,per_gap", [(4, 1), (4, 2), (6, 3)])
def test_gamut_faces_between_even_lattice(cube_n, per_gap):
    pts = G.gamut_faces_between(cube_n, per_gap)
    interior = (cube_n - 1) * (per_gap + 1) - 1
    assert len(pts) == 6 * (interior ** 2 - (cube_n - 2) ** 2)
    assert len(pts) == G.gamut_faces_between_count(cube_n, per_gap)
    assert _all_in_range(pts)
    cube_levels = [round(i / (cube_n - 1) * 100.0, 6) for i in range(cube_n)]
    for p in pts:
        # No face point is a cube point (both free coords on cube levels) and none
        # sits on the face perimeter (that's the edges set's job) — but points DO
        # land on the cube grid lines between cube dots, filling the old cross gap.
        on_face = sum(1 for c in p if c in (0.0, 100.0))
        assert on_face == 1                              # exactly the face value
        u, v = [c for c in p if c not in (0.0, 100.0)]
        assert 0.0 < u < 100.0 and 0.0 < v < 100.0       # interior, not perimeter
        both_cube = (min(abs(u - cl) for cl in cube_levels) < 1e-6
                     and min(abs(v - cl) for cl in cube_levels) < 1e-6)
        assert not both_cube                             # not a cube point
    # The fill + the cube's interior points form one uniform grid (no cross gap):
    # the free coordinate takes every interior lattice position, evenly spaced.
    last = (cube_n - 1) * (per_gap + 1)
    expected = [iu / last * 100.0 for iu in range(1, last)]
    actual = sorted({r for r, g, b in pts if b == 0.0})
    assert len(actual) == len(expected)
    assert all(abs(a - e) < 1e-9 for a, e in zip(actual, expected))


def test_gamut_faces_between_zero_empty():
    assert G.gamut_faces_between(8, 0) == []
    assert G.gamut_faces_between_count(8, 0) == 0


# --- Colour extremes: spiral cones at the 6 chromatic corners (#78) ---------
@pytest.mark.parametrize("per_end,reach", [(1, 16), (5, 16), (10, 24)])
def test_gamut_corners_count_and_range(per_end, reach):
    pts = G.gamut_corners(per_end, reach)
    assert len(pts) == 6 * per_end == G.gamut_corners_count(per_end)
    assert _all_in_range(pts)


def test_gamut_corners_spiral_in_each_colour_corner_not_white_black():
    pts = G.gamut_corners(8, 16.0)
    for p in pts:
        assert p not in G._CORNER_PTS                       # never the exact tip (Q2)
        assert math.dist(p, (50.0, 50.0, 50.0)) > 25.0      # away from the centre
        # Nowhere near the white or black corner (those are H&S's job).
        assert math.dist(p, (0.0, 0.0, 0.0)) > 15.0
        assert math.dist(p, (100.0, 100.0, 100.0)) > 15.0
    # Each of the six chromatic corners gets its own cone.
    closest = {min(range(6), key=lambda i: math.dist(p, G._CHROMATIC_CORNERS[i]))
               for p in pts}
    assert len(closest) == 6


def test_gamut_corners_reach_controls_depth():
    far = lambda d: max(math.dist(p, min(G._CHROMATIC_CORNERS,
                                         key=lambda c: math.dist(p, c)))
                        for p in G.gamut_corners(12, d))
    assert far(30) > far(8)                               # bigger reach goes further


def test_gamut_corners_include_corners_adds_the_six_colour_tips():
    pts = G.gamut_corners(3, 16, include_corners=True)
    assert len(pts) == 6 * 3 + 6 == G.gamut_corners_count(3, include_corners=True)
    assert all(t in pts for t in G._CHROMATIC_CORNERS)     # the six colour tips
    assert (0.0, 0.0, 0.0) not in pts and (100.0, 100.0, 100.0) not in pts


def test_gamut_edges_between_includes_tips_without_the_cube():
    # Cube off → Saturated edges must restore the 8 corner tips (Nelson/Knut).
    e = G.gamut_edges_between(2, 3, include_corners=True)
    assert len(e) == 12 * 3 + 8 == G.gamut_edges_between_count(2, 3, True)
    assert all(t in e for t in G._CORNER_PTS)
    # Cube on → unchanged, no tips injected (the cube supplies them).
    assert G.gamut_edges_between(8, 1) == G.gamut_edges_between(8, 1, False)
    assert G.gamut_edges_between_count(8, 1) == 84


# --- Corner edges: extra patches on the edge lines near each tip (#78) -------
@pytest.mark.parametrize("per_branch", [1, 2, 5])
def test_gamut_corner_edges_count_range_and_on_the_lines(per_branch):
    pts = G.gamut_corner_edges(per_branch)
    assert len(pts) == 24 * per_branch == G.gamut_corner_edges_count(per_branch)
    assert _all_in_range(pts)
    for p in pts:
        # On a cube edge line (two coords pinned at 0 or 100) and near a tip.
        assert sum(1 for v in p if v in (0.0, 100.0)) >= 2
        assert min(math.dist(p, c) for c in G._CORNER_PTS) <= G._CORNER_EDGE_NEAR + 1e-6


def test_gamut_corner_edges_interleave_avoids_existing():
    # Given a dense cube on the edges, the corner-edge patches sit in the gaps,
    # never landing on a cube point.
    cube = G.rgb_cube(8)
    pts = G.gamut_corner_edges(2, existing=cube)
    assert all(min(math.dist(p, c) for c in cube) > 1.0 for p in pts)


def test_gamut_corner_edges_include_corners_and_zero():
    assert G.gamut_corner_edges(0) == []
    full = G.gamut_corner_edges(2, include_corners=True)
    assert len(full) == 24 * 2 + 8 == G.gamut_corner_edges_count(2, include_corners=True)
    assert all(t in full for t in G._CORNER_PTS)


# --- Flamingos — the pink / magenta / indigo band (Knut, #78) --------------
@pytest.mark.parametrize("count", [1, 30, 192])
def test_flamingos_count_and_range(count):
    f = G.flamingos(count, 3)
    assert len(f) == count == G.flamingos_count(count)
    assert _all_in_range(f)


def test_flamingos_lives_in_the_pink_magenta_wedge():
    # The band fills the gap between blues and sunrises: green is the weakest
    # channel for the great majority of its patches (pinks / magentas / indigos).
    f = G.flamingos(150, 3)
    pinkish = sum(1 for r, g, b in f if g <= r and g <= b)
    assert pinkish > 0.8 * len(f)


def test_sunrises_reaches_dark_tones():
    # Sunrises' value floor was lowered so the warm band starts near the dark
    # corner like greens, not at mid lightness (Knut, #78).
    s = G.sunrises(192, 3)
    assert min(max(p) for p in s) <= 35.0


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


def test_highlight_shadow_reach_deepens_the_bands():
    import colorsys
    per = 12
    val = lambda p: colorsys.rgb_to_hsv(p[0] / 100, p[1] / 100, p[2] / 100)[2]
    shallow = G.highlight_shadow_detail(per, reach=8)
    deep = G.highlight_shadow_detail(per, reach=30)
    # Deeper reach => shadows extend higher toward the midtones.
    assert max(val(p) for p in deep[per:]) > max(val(p) for p in shallow[per:])
    # ...and highlights extend lower.
    assert min(val(p) for p in deep[:per]) < min(val(p) for p in shallow[:per])
    assert _all_in_range(deep) and len(deep) == 2 * per


def _chroma(p):
    m = sum(p) / 3.0
    return math.sqrt(sum((c - m) ** 2 for c in p))


@pytest.mark.parametrize("per_end", [1, 6, 24])
def test_highlight_shadow_ends_are_mirror_images(per_end):
    # The shadow cap is the exact point-inversion of the highlight cap, so the
    # two ends come out congruent (Knut's lopsided-cone fix, #37).
    patches = G.highlight_shadow_detail(per_end)
    hi, sh = patches[:per_end], patches[per_end:]
    for h, s in zip(hi, sh):
        assert all(abs(s[j] - (100.0 - h[j])) < 1e-9 for j in range(3))
    # Congruent => identical set of chroma radii at both ends.
    assert sorted(round(_chroma(p), 6) for p in hi) == \
           sorted(round(_chroma(p), 6) for p in sh)


def test_highlight_shadow_no_clash_with_grey_discs():
    # With greys on, no H&S patch may sit both inside the greys' ring radius AND
    # at (near) a grey step's lightness — that's where a grey disc actually is.
    steps, off, rings = 16, 5.0, 1
    greys_outer = rings * (math.sqrt(6) / 3.0) * off
    step_ls = [k / (steps - 1) * 100.0 for k in range(steps)]
    patches = G.highlight_shadow_detail(64, reach=20, greys_enabled=True,
                                        greys_steps=steps, greys_offset=off,
                                        greys_rings=rings)
    assert _all_in_range(patches)
    for p in patches:
        if _chroma(p) < greys_outer:                  # inside a grey ring radius
            L = sum(p) / 3.0                           # balanced tint => mean = level
            assert min(abs(L - sl) for sl in step_ls) > G._GREY_CLASH_TOL


def test_highlight_shadow_fills_gaps_between_grey_steps():
    # Few grey steps => the cones still reach the axis in the wide gaps between
    # them (so the chart isn't left with empty bands), unlike many tight steps.
    common = dict(greys_enabled=True, greys_offset=5.0, greys_rings=1)
    sparse = G.highlight_shadow_detail(64, reach=20, greys_steps=8, **common)
    dense = G.highlight_shadow_detail(64, reach=20, greys_steps=48, **common)
    greys_outer = (math.sqrt(6) / 3.0) * 5.0
    near_axis = lambda ps: sum(_chroma(p) < greys_outer for p in ps)
    assert near_axis(sparse) > near_axis(dense)       # gaps populated when sparse


def test_highlight_shadow_reaches_neutral_when_greys_off():
    # With greys off, the filled cones must reach the neutral axis itself — so
    # the near-neutral light/dark tones are covered (Knut's "greys missing", #37).
    patches = G.highlight_shadow_detail(40, reach=20, greys_enabled=False)
    assert min(_chroma(p) for p in patches) < 0.5     # a genuine on-axis patch


def test_highlight_shadow_cones_reach_the_corners():
    # The cones must run all the way to paper white / pure black, not stop in
    # mid-air short of the ends (Knut's "cone stops in mid air", #37).
    patches = G.highlight_shadow_detail(64, reach=16, greys_enabled=False)
    hi = patches[:64]
    assert max(max(p) for p in hi) > 96              # highlights reach near white
    # mirror => shadows reach symmetrically near black
    assert min(min(p) for p in patches[64:]) < 4


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
@pytest.mark.parametrize("layers", [1, 2, 3, 4])
def test_pastels_count_and_range(count, layers):
    patches = G.pastels(count, layers)
    assert len(patches) == count == G.pastels_count(count)
    assert _all_in_range(patches)


def test_pastels_are_low_chroma():
    import colorsys
    sats = [colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[1]
            for r, g, b in G.pastels(40, 2)]
    assert max(sats) < 0.45            # muted, never vivid
    assert sum(sats) / len(sats) > 0.05  # but not pure greys


def test_pastel_layers_widen_chroma_spread():
    import colorsys
    sat = lambda ps: [colorsys.rgb_to_hsv(r / 100, g / 100, b / 100)[1]
                      for r, g, b in ps]
    one = sat(G.pastels(60, 1))
    three = sat(G.pastels(60, 3))
    # More layers = chroma sampled at more depths = a wider saturation spread.
    assert (max(three) - min(three)) > (max(one) - min(one))


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


def _nn_spacing(pts):
    """List of each point's distance to its nearest neighbour."""
    import math as _m
    out = []
    for i, a in enumerate(pts):
        out.append(min(_m.dist(a, b) for j, b in enumerate(pts) if j != i))
    return out


def test_fill_gaps_relaxation_is_more_even():
    # Lloyd relaxation should make the fill more uniform than the raw blue-noise
    # seed: the spread of nearest-neighbour spacings (its coefficient of
    # variation) drops once the added points settle onto their cell centroids.
    import statistics as st
    raw = G.fill_gaps([], 80, seed=1, relax=0)
    even = G.fill_gaps([], 80, seed=1, relax=6)
    cv_raw = st.pstdev(_nn_spacing(raw)) / st.mean(_nn_spacing(raw))
    cv_even = st.pstdev(_nn_spacing(even)) / st.mean(_nn_spacing(even))
    assert cv_even < cv_raw
    assert _all_in_range(even)


def test_fill_gaps_relaxed_keeps_clear_of_existing():
    # Relaxation must not pull added points onto the fixed existing patches.
    existing = G.rgb_cube(3)
    add = G.fill_gaps(existing, 90, seed=3, relax=6)
    assert len(add) == 90 - 27
    assert G.overlap_count(existing, add) == 0


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


def test_overlap_count_counts_only_collisions_with_existing():
    existing = G.rgb_cube(3)                       # 27 distinct corners/edges/centre
    # Half the new set reuses existing colours, half is brand new.
    reused = existing[:5]
    fresh = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)]
    assert G.overlap_count(existing, reused + fresh) == 5
    assert G.overlap_count(existing, fresh) == 0
    assert G.overlap_count([], fresh) == 0


def test_dedupe_against_relocates_only_the_new_and_keeps_count():
    existing = [(50.0, 50.0, 50.0), (0.0, 0.0, 0.0)]
    new = [(50.0, 50.0, 50.0), (0.0, 0.0, 0.0), (80.0, 10.0, 20.0)]
    out = G.dedupe_against(existing, new)
    assert len(out) == len(new)                    # count preserved
    # None of the returned patches now collides with existing …
    assert G.overlap_count(existing, out) == 0
    # … nor with each other, and the already-unique one is untouched.
    assert (80.0, 10.0, 20.0) in out
    assert _all_in_range(out)


def _min_nn(pts):
    import math
    best = 1e18
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            best = min(best, math.dist(a, b))
    return best


def test_enforce_min_distance_assures_real_spacing():
    # A realistic combine — overlapping cube + grey ramp + saturated edges — ends
    # up with every patch at least min_dist apart (the grid de-duplicator only
    # guaranteed distinct cells, which left near-touching pairs). Count preserved.
    md = 2.0
    pts = (G.rgb_cube(6) + G.near_neutral_greys(16, 4.0, 1)
           + G.gamut_edges_between(6, 2) + G.flamingos(120, 3))
    out = G.enforce_min_distance(pts, md)
    assert len(out) == len(pts)
    assert _all_in_range(out)
    assert _min_nn(out) >= md - 1e-6
    # A handful of coincident points get spread apart too (room to place them).
    spread = G.enforce_min_distance([(50.0, 50.0, 50.0)] * 8, md)
    assert _min_nn(spread) >= md - 1e-6


def test_enforce_min_distance_respects_and_keeps_existing_fixed():
    md = 3.0
    existing = [(10.0, 10.0, 10.0), (90.0, 90.0, 90.0)]
    new = [(10.0, 10.0, 10.0), (11.0, 10.0, 10.0), (40.0, 40.0, 40.0)]
    out = G.enforce_min_distance(new, md, existing=existing)
    assert len(out) == len(new)
    # every returned point clears the (untouched) existing points …
    for q in out:
        for e in existing:
            import math
            assert math.dist(q, e) >= md - 1e-6
    # … and each other.
    assert _min_nn(out) >= md - 1e-6


def test_enforce_min_distance_incremental_equals_one_shot():
    # Spacing the whole concatenation at once == spacing set-by-set top to bottom
    # (the property that lets the panel process generators in display order).
    a = G.rgb_cube(4)
    b = G.near_neutral_greys(6, 6.0, 1)
    c = G.gamut_edges_between(4, 2)
    one = G.enforce_min_distance(a + b + c, 2.0)
    inc = []
    for chunk in (a, b, c):
        inc = G.enforce_min_distance(inc + chunk, 2.0)
    assert inc == one


def test_enforce_min_distance_zero_is_passthrough():
    src = [(50.0, 50.0, 50.0)] * 3
    assert G.enforce_min_distance(src, 0.0) == src


def test_count_and_drop_too_close_flag_crowding_not_just_exact():
    existing = [(50.0, 50.0, 50.0), (10.0, 10.0, 10.0)]
    new = [
        (50.0, 50.0, 50.0),    # exact duplicate    -> too close
        (51.0, 50.0, 50.0),    # 1.0 away (< 2)     -> too close (crowds)
        (10.0, 11.5, 10.0),    # 1.5 away (< 2)     -> too close (crowds)
        (80.0, 80.0, 80.0),    # far                -> clear
        (50.0, 53.0, 50.0),    # 3.0 away (> 2)     -> clear
    ]
    assert G.count_too_close(existing, new, 2.0) == 3
    assert G.drop_too_close(existing, new, 2.0) == [
        (80.0, 80.0, 80.0), (50.0, 53.0, 50.0)]
    # Detection is a strict superset of exact duplicates (overlap_count, 0.5
    # grid): with crowding it flags more than the old exact-match check did.
    assert (G.count_too_close(existing, new, 2.0)
            > G.overlap_count(existing, new))
    # min_dist = 0 disables it (nothing flagged / dropped).
    assert G.count_too_close(existing, new, 0.0) == 0
    assert G.drop_too_close(existing, new, 0.0) == new


def test_drop_then_refill_preserves_count_without_overlaps():
    # The overlap dialog's "Add new ones and fill the gaps" = drop the crowders,
    # then fill back up to the original count with fresh non-overlapping patches.
    md = 2.0
    existing = G.rgb_cube(6)
    extra = [(r + 1.0, g, b) for r, g, b in G.rgb_cube(6)[:30]] \
        + [(83.0, 17.0, 51.0), (11.0, 47.0, 93.0)]
    keep = G.drop_too_close(existing, extra, md)
    fresh = G.fill_gaps(existing + keep, len(existing) + len(extra))
    result = keep + fresh
    assert len(result) == len(extra)                      # total preserved
    assert _all_in_range(result)
    for q in result:                                      # nothing crowds the chart
        assert min(math.dist(q, e) for e in existing) >= md - 1e-6


def test_only_new_drops_existing_and_keeps_the_rest():
    existing = [(50.0, 50.0, 50.0), (0.0, 0.0, 0.0)]
    new = [(50.0, 50.0, 50.0), (0.0, 0.0, 0.0), (80.0, 10.0, 20.0)]
    out = G.only_new(existing, new)
    assert out == [(80.0, 10.0, 20.0)]            # only the genuinely-new one
    assert G.only_new([], new) == new             # nothing to drop
    assert G.only_new(existing, existing) == []   # all already present


def test_white_black_is_the_two_corners():
    wb = G.white_black()
    assert wb == [(100.0, 100.0, 100.0), (0.0, 0.0, 0.0)]
    assert len(wb) == 2 == G.white_black_count()
    assert _all_in_range(wb)


def test_white_black_amount_and_topup():
    # N of each when nothing is there yet.
    assert G.white_black(3) == [(100.0, 100.0, 100.0)] * 3 + [(0.0, 0.0, 0.0)] * 3
    assert G.white_black_count(3) == 6
    # One of each already present => only two more each, for a total of three.
    assert G.white_black(3, have_white=1, have_black=1) == \
        [(100.0, 100.0, 100.0)] * 2 + [(0.0, 0.0, 0.0)] * 2
    assert G.white_black_count(3, 1, 1) == 4
    # Already at/over the target => adds nothing.
    assert G.white_black(2, have_white=2, have_black=5) == []


def test_count_white_black_finds_existing_corners():
    prog = G.rgb_cube(3) + [(100.0, 100.0, 100.0), (0.0, 0.0, 0.0)]
    w, b = G.count_white_black(prog)
    assert w == 2 and b == 2          # cube corner + the explicit one, each
    assert G.count_white_black([(50.0, 50.0, 50.0)]) == (0, 0)
