"""N-channel generators + N-D spacing utilities (#72 Tier C).

Pure maths from the issue's worked appendix — every formula asserted here is
stated (and numerically verified) there: ring-clamp geometry (A), count
formulas (F), anchor mapping (F/H), the 2.0 device-% distance policy (E).
The perceptual bridge's live test runs against Apple's Generic CMYK profile.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from workflow import patch_generators as G
from workflow import patch_generators_nd as ND

ARGYLL_BIN = Path("/Applications/Argyll/bin")
GENERIC_CMYK = Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc")
live = pytest.mark.skipif(
    not (ARGYLL_BIN / "xicclu").exists() or not GENERIC_CMYK.exists(),
    reason="ArgyllCMS or Generic CMYK profile not installed")


# --- N-native sets ------------------------------------------------------------


def test_per_ink_ramps_counts_and_values():
    out = ND.per_ink_ramps(6, 8)
    assert len(out) == ND.per_ink_ramps_count(6, 8) == 48
    # First ramp is ink 0 alone, hitting exactly 100 at the top (appendix F).
    assert out[0] == (12.5, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert out[7] == (100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    # Every patch touches exactly one ink.
    assert all(sum(1 for v in p if v > 0) == 1 for p in out)


def test_ink_pair_overprints_respect_limit_by_construction():
    out = ND.ink_pair_overprints(4, 5, ink_limit=300.0)
    assert len(out) == ND.ink_pair_overprints_count(4, 5) == 30   # C(4,2)*5
    assert all(sum(p) <= 300.0 + 1e-9 for p in out)
    # L/2 cap only bites under 200% (appendix F): at L=150 both inks max 75.
    tight = ND.ink_pair_overprints(4, 5, ink_limit=150.0)
    assert max(max(p) for p in tight) == 75.0
    assert all(sum(p) <= 150.0 + 1e-9 for p in tight)


def test_near_neutrals_device_preserves_ring_radius():
    steps, offset, rings = 16, 4.0, 2
    rgb = G.near_neutrals(steps, offset, rings)
    dev = ND.near_neutrals_device(steps, offset, rings, n_channels=4,
                                  ink_limit=300.0)
    assert len(dev) == len(rgb) == ND.near_neutrals_device_count(steps, rings)
    for p_rgb, p_dev in zip(rgb, dev):
        c, m, y, k = p_dev
        assert k == 0.0                                # v1: K stays 0
        # Naive inversion maps the neutral axis onto equal-CMY; the clamp
        # (grey-axis shift) preserves the perpendicular ring component
        # exactly (appendix A) — check radius equality vs the RGB source.
        r_rgb = _perp_radius((100 - p_rgb[0], 100 - p_rgb[1], 100 - p_rgb[2]))
        assert _perp_radius((c, m, y)) == pytest.approx(r_rgb, abs=1e-9)
        assert sum((c, m, y)) <= 300.0 + 1e-9


def _perp_radius(v):
    mean = sum(v) / 3.0
    return math.sqrt(sum((x - mean) ** 2 for x in v))


def test_near_neutrals_device_clamp_fires_only_below_default_limit():
    # Verified in the issue: at L=300 the clamp never fires (max sum 297.69
    # even at offset 50 / 3 rings). A stricter 250% limit must clamp.
    dev300 = ND.near_neutrals_device(16, 50.0, 3, n_channels=4, ink_limit=300.0)
    assert max(sum(p[:3]) for p in dev300) < 300.0
    dev250 = ND.near_neutrals_device(16, 50.0, 3, n_channels=4, ink_limit=250.0)
    assert max(sum(p[:3]) for p in dev250) <= 250.0 + 1e-9
    assert all(min(p[:3]) >= 0.0 for p in dev250)      # no negative channels


def test_white_black_device_anchors():
    out = ND.white_black_device(2, n_channels=6, k_index=3)
    assert out[:2] == [(0.0,) * 6] * 2                 # ink white = bare paper
    assert out[2:] == [(0.0, 0.0, 0.0, 100.0, 0.0, 0.0)] * 2
    w, b = ND.count_white_black_device(out, n_channels=6, k_index=3)
    assert (w, b) == (2, 2)
    # K-less set: equal CMY inside the limit (appendix H).
    cmy = ND.white_black_device(1, n_channels=3, k_index=None, ink_limit=240.0)
    assert cmy[1] == (80.0, 80.0, 80.0)


# --- N-D spacing utilities ----------------------------------------------------


def test_deduplicate_nd_separates_collisions():
    pts = [(50.0, 50.0, 50.0, 0.0)] * 3 + [(10.0, 0.0, 0.0, 0.0)]
    out = ND.deduplicate_nd(pts)
    assert len(out) == 4
    assert len(set(out)) == 4                           # all distinct
    assert out[0] == (50.0, 50.0, 50.0, 0.0)            # first kept exact


def test_enforce_min_distance_nd_spaces_points():
    pts = [(50.0, 50.0, 50.0, 50.0), (50.5, 50.0, 50.0, 50.0)]
    out = ND.enforce_min_distance_nd(pts, 2.0)
    assert len(out) == 2
    assert math.dist(out[0], out[1]) >= 2.0 - 1e-9
    # Existing points are never moved.
    out2 = ND.enforce_min_distance_nd([(50.5, 50.0, 50.0, 50.0)], 2.0,
                                      existing=[(50.0, 50.0, 50.0, 50.0)])
    assert math.dist(out2[0], (50.0, 50.0, 50.0, 50.0)) >= 2.0 - 1e-9


def test_count_and_drop_too_close_nd():
    existing = [(50.0, 50.0, 50.0, 50.0)]
    new = [(50.5, 50.0, 50.0, 50.0), (80.0, 0.0, 0.0, 0.0)]
    assert ND.count_too_close_nd(existing, new, 2.0) == 1
    assert ND.drop_too_close_nd(existing, new, 2.0) == [(80.0, 0.0, 0.0, 0.0)]


def test_fill_gaps_nd_fills_to_total_in_bounds():
    existing = ND.per_ink_ramps(4, 4)
    out = ND.fill_gaps_nd(existing, 40)
    assert len(out) == 40 - len(existing)
    assert all(len(p) == 4 for p in out)
    assert all(0.0 <= v <= 100.0 for p in out for v in p)


# --- perceptual bridge (live) ---------------------------------------------------


@live
def test_bridge_skin_tones_stay_in_gamut_blues_move():
    from workflow.xicclu_runner import to_device_via_profile

    skin = G.skin_tones(4, 3)
    dev, moved = to_device_via_profile(skin, GENERIC_CMYK, ARGYLL_BIN)
    assert len(dev) == len(skin) and all(len(p) == 4 for p in dev)
    # Appendix B: skin tones sit entirely inside even a bog-standard CMYK
    # gamut (max ΔE 2.64 measured) — none may count as moved.
    assert moved == 0
    blues = G.blues(30, 3)
    dev_b, moved_b = to_device_via_profile(blues, GENERIC_CMYK, ARGYLL_BIN)
    assert len(dev_b) == len(blues)
    # Saturated blues are far outside CMYK (median ΔE 17.9 measured):
    # a healthy majority must be reported as moved — honestly.
    assert moved_b > len(blues) / 3


@live
def test_bridge_respects_ink_limit():
    from workflow.xicclu_runner import to_device_via_profile

    # Dark saturated colours want lots of ink; the limit must hold.
    dark = [(20.0, 5.0, 30.0), (10.0, 10.0, 40.0), (5.0, 25.0, 5.0)]
    dev, _ = to_device_via_profile(dark, GENERIC_CMYK, ARGYLL_BIN,
                                   ink_limit=250.0)
    assert all(sum(p) <= 250.0 + 1e-6 for p in dev)


# ---------------------------------------------------------------------------
# #123 follow-up: ink-limit everywhere, triples, rich black, recentred rings
# ---------------------------------------------------------------------------

def test_per_ink_ramps_respect_sub_100_limit():
    out = ND.per_ink_ramps(4, 4, ink_limit=80.0)
    assert max(max(p) for p in out) == 80.0
    assert all(sum(p) <= 80.0 + 1e-9 for p in out)


def test_black_anchor_respects_sub_100_limit():
    _w, black = ND._device_anchors(4, 3, ink_limit=60.0)
    assert black[3] == 60.0 and sum(black) == 60.0


def test_ink_triple_overprints_counts_and_limit():
    out = ND.ink_triple_overprints(4, 2, ink_limit=280.0)
    assert len(out) == ND.ink_triple_overprints_count(4, 2) == 8   # C(4,3)*2
    assert all(sum(p) <= 280.0 + 1e-9 for p in out)
    assert all(sum(1 for v in p if v > 0) == 3 for p in out)
    # L/3 cap by construction: at L=240 each ink tops out at 80.
    tight = ND.ink_triple_overprints(6, 3, ink_limit=240.0)
    assert len(tight) == ND.ink_triple_overprints_count(6, 3) == 60
    assert max(max(p) for p in tight) == 80.0


def test_rich_black_ramp_shape_and_limit():
    out = ND.rich_black_ramp(3, 2, 4, k_index=3, ink_limit=280.0)
    assert len(out) == ND.rich_black_ramp_count(3, 2) == 6
    assert all(sum(p) <= 280.0 + 1e-9 for p in out)
    # Every patch is neutral CMY + some K.
    for p in out:
        assert p[0] == p[1] == p[2] and p[3] > 0
    # K-less ink set: the set adds nothing (and the count agrees).
    assert ND.rich_black_ramp(3, 2, 3, k_index=None) == []
    assert ND.rich_black_ramp_count(3, 2, 3, None) == 0


def test_fill_gaps_nd_respects_ink_limit():
    seed = [(0.0, 0.0, 0.0, 0.0), (10.0, 10.0, 10.0, 10.0)]
    out = ND.fill_gaps_nd(seed, 60, n_channels=4, ink_limit=280.0)
    assert len(out) == 58
    assert all(sum(p) <= 280.0 + 1e-6 for p in out)
    # Without a limit the old behaviour (full cube) is unchanged.
    free = ND.fill_gaps_nd(seed, 60, n_channels=4)
    assert any(sum(p) > 300.0 for p in free)


def test_project_ink_limit_matches_euclidean_projection():
    pts = [(100.0, 100.0, 100.0, 100.0), (10.0, 20.0, 5.0, 0.0)]
    out = ND.project_ink_limit(pts, 280.0)
    assert out[1] == (10.0, 20.0, 5.0, 0.0)          # under limit: untouched
    assert abs(sum(out[0]) - 280.0) < 1e-9
    assert out[0] == (70.0, 70.0, 70.0, 70.0)        # equal subtraction


def test_recentred_rings_preserve_offsets_and_limit():
    steps, offset, rings, n = 8, 4.0, 1, 4
    greys = ND.ring_grey_levels(steps, offset, rings)
    assert greys and all(0.0 <= g <= 100.0 for g in greys)
    # A fake profile answer: the printer's neutral needs +5 C, -3 M vs naive.
    centers = {}
    for g in greys:
        naive = ND._invert_rgb_to_cmy((g, g, g), n, 1e9)
        centers[g] = (naive[0] + 5.0, naive[1] - 3.0, naive[2], 0.0)
    naive_rings = ND.near_neutrals_device(steps, offset, rings, n,
                                          ink_limit=300.0)
    recentred = ND.near_neutrals_device_recentred(steps, offset, rings, n,
                                                  centers, ink_limit=300.0)
    assert len(recentred) == len(naive_rings)
    # The ring geometry survives: pairwise offsets shift by the same
    # (+5, -3) wherever no clamp bites.
    moved = [(r[0] - v[0], r[1] - v[1]) for r, v in
             zip(recentred, naive_rings)
             if 10.0 < r[0] < 90.0 and 10.0 < r[1] < 90.0]
    assert moved and all(abs(dx - 5.0) < 1e-6 and abs(dy + 3.0) < 1e-6
                         for dx, dy in moved)
    # Empty centres dict = graceful fallback to the naive rings.
    fallback = ND.near_neutrals_device_recentred(steps, offset, rings, n,
                                                 {}, ink_limit=300.0)
    assert fallback == naive_rings
