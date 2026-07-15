"""Perceptual / saturation gamut mapping for the profile engine (P4, #122).

Builds distinct B2A0 (perceptual) and B2A2 (saturation) tables when a source
gamut is given — the engine's counterpart of colprof's ``-S``.

The v1 mapping is the measured closed-form family from the issue-#122
prototypes (validated against colprof's realized perceptual behaviour on the
trusted ET-8550 profile):

* neutral-axis luminance-range compression ``L' = L + L_bk·(1 − L/100)^1.38``
  (exponent fitted to the measured colprof curve; black gains density, the
  highlights stay put);
* per-hue-sector radial compression toward a focal point on the neutral
  axis blended toward the destination cusp: colours inside the knee are
  untouched, out-of-gamut colours land just inside the surface (smooth tanh
  knee — no hard clip);
* saturation intent = the same map with a shorter protected core and a mild
  chroma push (colprof's saturation table is likewise a boosted perceptual).

Honesty note (in the issue, agreed with Basti): this family measures ≈4.5 ΔE
median against colprof's own mapping — inside the band professional tools
disagree with each other (5.2 median, measured), but not colprof-exact. The
engine therefore ships these intents as *approximate*; the colorimetric
tables remain authoritative. Full gammap-style guide-vector parity is the
documented P4 follow-up.

Source gamuts are computed live from whatever profile is given — like
colprof, the engine doesn't care which source you throw at it. ClayRGB /
Adobe RGB and sRGB take an exact analytic fast path; every other RGB or
CMYK profile (ProPhoto, Display P3, a printer profile, v2 or v4) is sampled
through littleCMS. ICC v4 works here even though the Argyll tools can't
read it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from workflow.profile_engine import b2a as b2a_mod
from workflow.profile_engine import icc_writer as icw
from workflow.profile_engine.forward_model import ForwardModel
from workflow.profile_engine.ti3_data import Ti3Measurement, xyz_to_lab

# Bradford D65→D50 adaptation (both analytic sources are D65-white).
_MB = np.array([[0.8951, 0.2664, -0.1614],
                [-0.7502, 1.7135, 0.0367],
                [0.0389, -0.0685, 1.0296]])
_D65 = np.array([0.95047, 1.0, 1.08883])
_D50 = np.array([0.9642, 1.0, 0.8249])
_AD = np.linalg.inv(_MB) @ np.diag((_MB @ _D50) / (_MB @ _D65)) @ _MB

# Published primaries; gamma per spec (sRGB piecewise, Adobe 563/256).
_PRIMARIES = {
    "adobe": (np.array([[0.64, 0.33], [0.21, 0.71], [0.15, 0.06]]), "adobe"),
    "srgb": (np.array([[0.64, 0.33], [0.30, 0.60], [0.15, 0.06]]), "srgb"),
}

NH, NP = 48, 24          # hue × elevation bins for the radial tables


class GamutSourceError(ValueError):
    """The gamut-source profile could not be read (user-facing message)."""


def source_kind(path: Path | str) -> str | None:
    """Analytic fast path for the two sources ChromIQ recommends (#121).

    Exact primaries, no quantisation, no profile I/O. Anything else returns
    ``None`` and is sampled live from the actual profile — the engine, like
    colprof, doesn't care which source you throw at it.
    """
    name = Path(path).stem.lower()
    if "clay" in name or "adobe" in name:
        return "adobe"
    if "srgb" in name:
        return "srgb"
    return None


def source_surface_from_profile(path: Path | str, mesh: int = 33,
                                intent: int = 1) -> np.ndarray:
    """Source-gamut boundary cloud in Lab, computed live from any ICC.

    The device-cube boundary is mapped through the profile with littleCMS
    (PIL ``ImageCms``) — which, unlike the Argyll tools, also reads ICC v4
    (Display P3, i1Profiler output, …). RGB and CMYK sources are supported.
    The 8-bit LAB transform quantises to ≈0.5 ΔE — irrelevant for a gamut
    surface that only feeds binned radial maxima.
    """
    kind = source_kind(path)
    if kind is not None:
        return source_surface_lab(kind, mesh)
    p = Path(path)
    if not p.is_file():
        raise GamutSourceError(
            f"Gamut source profile not found: {p}")
    try:
        from PIL import Image, ImageCms
        prof = ImageCms.getOpenProfile(str(p))
        space = (prof.profile.xcolor_space or "").strip()
    except Exception as exc:
        raise GamutSourceError(
            f"'{p.name}' could not be read as an ICC profile "
            f"({exc}).") from exc
    if space not in ("RGB", "CMYK"):
        raise GamutSourceError(
            f"'{p.name}' is a {space or 'non-device'} profile — gamut "
            "sources must be RGB or CMYK.")
    n = 3 if space == "RGB" else 4
    if n == 3:
        u = np.linspace(0.0, 1.0, mesh)
        faces = []
        for ax in range(3):
            for val in (0.0, 1.0):
                g = np.zeros((mesh, mesh, 3))
                a, b = [i for i in range(3) if i != ax]
                g[:, :, ax] = val
                g[:, :, a] = u[:, None]
                g[:, :, b] = u[None, :]
                faces.append(g.reshape(-1, 3))
        dev = np.vstack(faces)
    else:
        rng = np.random.default_rng(17)
        m = 12000
        dev = rng.uniform(0.0, 1.0, (m, 4))
        dev[np.arange(m), rng.integers(0, 4, m)] = \
            rng.integers(0, 2, m).astype(float)
    try:
        lab_prof = ImageCms.createProfile("LAB")
        # intent: 0 perceptual / 1 rel col / 2 saturation — colprof samples
        # the source through the matching intent per table (-nP/-nS switch
        # to colorimetric); matrix-only profiles are intent-invariant.
        tf = ImageCms.buildTransform(prof, lab_prof, space, "LAB",
                                     renderingIntent=intent)
        dev8 = np.clip(dev * 255.0, 0, 255).round().astype(np.uint8)
        img = Image.merge(space, [
            Image.fromarray(dev8[:, c].reshape(-1, 1), "L")
            for c in range(n)])
        out = ImageCms.applyTransform(img, tf)
        chans = [np.asarray(ch, dtype=float).reshape(-1)
                 for ch in out.split()]
    except Exception as exc:
        raise GamutSourceError(
            f"'{p.name}' could not be sampled as a gamut source "
            f"({exc}).") from exc
    return np.stack([chans[0] * 100.0 / 255.0,
                     chans[1] - 128.0,
                     chans[2] - 128.0], 1)


def _rgb_to_xyz_matrix(xy: np.ndarray) -> np.ndarray:
    w = np.array([0.3127, 0.3290])
    xr = np.stack([xy[:, 0] / xy[:, 1], np.ones(3),
                   (1 - xy[:, 0] - xy[:, 1]) / xy[:, 1]], 0)
    s = np.linalg.solve(xr, np.array([w[0] / w[1], 1.0,
                                      (1 - w[0] - w[1]) / w[1]]))
    return xr * s[None, :]


def source_surface_lab(kind: str, mesh: int = 33) -> np.ndarray:
    """Source-gamut boundary cloud in Lab(D50) — analytic primaries."""
    xy, gamma = _PRIMARIES[kind]
    m = _rgb_to_xyz_matrix(xy)
    u = np.linspace(0.0, 1.0, mesh)
    faces = []
    for ax in range(3):
        for val in (0.0, 1.0):
            g = np.zeros((mesh, mesh, 3))
            a, b = [i for i in range(3) if i != ax]
            g[:, :, ax] = val
            g[:, :, a] = u[:, None]
            g[:, :, b] = u[None, :]
            faces.append(g.reshape(-1, 3))
    rgb = np.vstack(faces)
    if gamma == "srgb":
        lin = np.where(rgb <= 0.04045, rgb / 12.92,
                       ((rgb + 0.055) / 1.055) ** 2.4)
    else:
        lin = rgb ** (563.0 / 256.0)
    xyz = (_AD @ (m @ lin.T)).T * 100.0
    return xyz_to_lab(xyz)


def destination_surface_lab(model: ForwardModel, mesh: int = 33,
                            ink_limit: float | None = None,
                            is_additive: bool = True) -> np.ndarray:
    """Destination boundary cloud: the mapped device-cube boundary (the
    verified surface construction; for n > 3 the boundary image is a superset
    seed — segmented maxima below pick the true outer shell)."""
    n = model.n_channels
    rng = np.random.default_rng(11)
    if n <= 3:
        u = np.linspace(0.0, 1.0, mesh)
        faces = []
        for ax in range(n):
            for val in (0.0, 1.0):
                g = np.zeros((mesh, mesh, n))
                a, b = [i for i in range(n) if i != ax][:2]
                g[:, :, ax] = val
                g[:, :, a] = u[:, None]
                g[:, :, b] = u[None, :]
                faces.append(g.reshape(-1, n))
        dev = np.vstack(faces)
    else:
        # High-dimensional cube boundary: dense random face samples.
        m = 20000
        dev = rng.uniform(0.0, 1.0, (m, n))
        dev[np.arange(m), rng.integers(0, n, m)] = \
            rng.integers(0, 2, m).astype(float)
    if ink_limit is not None and not is_additive:
        total = dev.sum(1)
        over = total > ink_limit / 100.0
        dev[over] *= (ink_limit / 100.0 / total[over])[:, None]
    return model.predict(dev)


def _radial_tables(cloud: np.ndarray, cusp_l: np.ndarray
                   ) -> np.ndarray:
    """(NH, NP) max radius per hue sector / elevation from the focal point."""
    tab = np.zeros((NH, NP))
    h = np.degrees(np.arctan2(cloud[:, 2], cloud[:, 1])) % 360.0
    chroma = np.hypot(cloud[:, 1], cloud[:, 2])
    for hb in range(NH):
        centre = (hb + 0.5) * 360.0 / NH
        m = np.abs(((h - centre + 180.0) % 360.0) - 180.0) <= 360.0 / NH * 1.5
        if not m.any():
            continue
        pl = cloud[m]
        dl = pl[:, 0] - cusp_l[hb]
        phi = np.arctan2(chroma[m], dl)
        r = np.hypot(chroma[m], dl)
        pb = np.clip((phi / np.pi * NP).astype(int), 0, NP - 1)
        np.maximum.at(tab[hb], pb, r)
    # fill empty bins from neighbours, then smooth over hue (window 3)
    for hb in range(NH):
        for pb in range(NP):
            if tab[hb, pb] == 0:
                nb = [tab[hb, q] for q in (pb - 1, pb + 1)
                      if 0 <= q < NP and tab[hb, q] > 0]
                tab[hb, pb] = max(nb) if nb else tab[hb].max()
    tab[:] = (tab + np.roll(tab, 1, 0) + np.roll(tab, -1, 0)) / 3.0
    return tab


class GamutMapper:
    """Closed-form Lab→Lab mapping from a source to a destination gamut."""

    def __init__(self, src_cloud: np.ndarray, dst_cloud: np.ndarray, *,
                 knee: float = 0.62, sharp: float = 1.5,
                 l_exp: float = 1.38, cusp_blend: float = 0.7,
                 chroma_boost: float = 1.0):
        self.knee = knee
        self.sharp = sharp
        self.l_exp = l_exp
        self.cusp_blend = cusp_blend
        self.chroma_boost = chroma_boost
        h = np.degrees(np.arctan2(dst_cloud[:, 2], dst_cloud[:, 1])) % 360.0
        chroma = np.hypot(dst_cloud[:, 1], dst_cloud[:, 2])
        self.cusp_l = np.full(NH, 50.0)
        for hb in range(NH):
            centre = (hb + 0.5) * 360.0 / NH
            m = np.abs(((h - centre + 180.0) % 360.0) - 180.0) <= 360.0 / NH
            if m.any():
                self.cusp_l[hb] = dst_cloud[m][chroma[m].argmax(), 0]
        neut = chroma < 8.0
        self.l_black = float(dst_cloud[neut][:, 0].min()) if neut.any() else 5.0
        self.l_white = float(dst_cloud[neut][:, 0].max()) if neut.any() else 100.0
        self.tab_dst = _radial_tables(dst_cloud, self.cusp_l)
        self.tab_src = np.maximum(_radial_tables(src_cloud, self.cusp_l),
                                  self.tab_dst)

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        """(N,3) Lab → mapped Lab (vectorised)."""
        L = lab[:, 0]
        a = lab[:, 1]
        b = lab[:, 2]
        # luminance-range compression (measured colprof family)
        l1 = L + self.l_black * np.clip(1.0 - L / 100.0, 0.0, 1.0) ** self.l_exp
        l1 = np.minimum(l1, 100.0)
        chroma = np.hypot(a, b) * self.chroma_boost
        h = np.degrees(np.arctan2(b, a)) % 360.0
        hb = np.clip((h / 360.0 * NH).astype(int), 0, NH - 1)
        # focal point: neutral axis at a blend of own L and the cusp L
        focal = (self.cusp_blend * self.cusp_l[hb]
                 + (1.0 - self.cusp_blend) * np.clip(l1, self.l_black, 100.0))
        dl = l1 - focal
        phi = np.arctan2(chroma, dl)
        r = np.hypot(chroma, dl)
        pb = np.clip((phi / np.pi * NP).astype(int), 0, NP - 1)
        rb_dst = self.tab_dst[hb, pb]
        rb_src = np.maximum(self.tab_src[hb, pb], rb_dst)
        t = np.divide(r, rb_dst, out=np.zeros_like(r), where=rb_dst > 0)
        smax = np.divide(rb_src, rb_dst, out=np.ones_like(rb_dst),
                         where=rb_dst > 0)
        # smooth tanh knee: identity below the knee, compress above
        uu = (t - self.knee) / np.maximum(smax - self.knee, 1e-6)
        t2 = np.where(
            t <= self.knee, t,
            self.knee + (1.0 - self.knee)
            * np.tanh(np.clip(uu, 0.0, None) * self.sharp)
            / np.tanh(self.sharp))
        r2 = t2 * rb_dst
        out = np.empty_like(lab)
        out[:, 0] = focal + r2 * np.cos(phi)
        cn = r2 * np.sin(phi)
        out[:, 1] = cn * np.cos(np.radians(h))
        out[:, 2] = cn * np.sin(np.radians(h))
        neutral = chroma < 1e-6
        out[neutral, 0] = l1[neutral]
        out[neutral, 1:] = lab[neutral, 1:] * 1.0
        return out


# colprof -t/-T gamut-mapping intent presets. The colorimetric-family codes
# (r, rl, a, aw, al) are exact by construction — a colorimetric "mapping" is
# the identity, so those tables alias the colorimetric B2A (verified in
# colprof output: its default A2B0/A2B1/A2B2 are byte-identical the same
# way). The perceptual/saturation-family codes tune the measured mapping
# family; the appearance codes (pa, aa) run the mapping in CIECAM02 Jab.
_INTENT_PRESETS: dict[str, dict] = {
    "": {},                                       # default perceptual
    "p": {},
    "lp": {"cusp_blend": 0.3},                    # luminance-preserving
    "ms": {"knee": 0.50, "chroma_boost": 1.03},   # moderate saturation
    "s": {"knee": 0.45, "chroma_boost": 1.06},    # enhanced saturation
    "pa": {"jab": True},                          # perceptual appearance
    "aa": {"jab": True, "absolute": True},        # absolute appearance
}
_COLORIMETRIC_INTENTS = {"r", "rl", "a", "aw", "al"}
_SAT_DEFAULT = {"knee": 0.45, "chroma_boost": 1.06}


def _mapper_for(intent: str, default_kw: dict, src: np.ndarray,
                dst: np.ndarray, settings) -> "GamutMapper | None":
    """A mapper for a -t/-T intent code; None = alias the colorimetric B2A."""
    if intent in _COLORIMETRIC_INTENTS:
        return None
    kw = dict(_INTENT_PRESETS.get(intent, default_kw or {}))
    if not kw and default_kw:
        kw = dict(default_kw)
    jab = kw.pop("jab", False)
    kw.pop("absolute", False)
    if settings is not None and (settings.src_viewing or settings.dst_viewing):
        jab = True
    if jab:
        from workflow.profile_engine.cam02 import DEFAULT_VC, VIEWING_PRESETS
        vc_s = VIEWING_PRESETS.get(getattr(settings, "src_viewing", ""),
                                   DEFAULT_VC)
        vc_d = VIEWING_PRESETS.get(getattr(settings, "dst_viewing", ""),
                                   DEFAULT_VC)
        return JabGamutMapper(src, dst, vc_s, vc_d, **kw)
    return GamutMapper(src, dst, **kw)


class JabGamutMapper:
    """The same radial mapping run in CIECAM02 Jab under viewing conditions.

    Source colours are taken to appearance space under the *source* viewing
    conditions, mapped between the Jab gamut surfaces, and brought back
    under the *destination* conditions — the appearance-match construction
    colprof's CAM-based mapping uses. The CAM code is round-trip gated at
    construction (inverse must reproduce XYZ to < 1e-4).
    """

    def __init__(self, src_lab: np.ndarray, dst_lab: np.ndarray,
                 vc_src, vc_dst, **mapper_kw) -> None:
        from workflow.profile_engine.cam02 import Cam02
        from workflow.profile_engine.ti3_data import lab_to_xyz
        self._cam_s = Cam02(vc_src)
        self._cam_d = Cam02(vc_dst)
        probe = np.array([[50.0, 20.0, -30.0], [80.0, 0.0, 0.0],
                          [20.0, 40.0, 30.0]])
        err = self._cam_d.roundtrip_error(lab_to_xyz(probe))
        if err > 1e-4:
            raise RuntimeError(f"CAM02 inverse round-trip failed ({err})")
        src_jab = self._cam_s.xyz_to_jab(lab_to_xyz(src_lab))
        dst_jab = self._cam_d.xyz_to_jab(lab_to_xyz(dst_lab))
        self._inner = GamutMapper(src_jab, dst_jab, **mapper_kw)

    def map_lab(self, lab: np.ndarray) -> np.ndarray:
        from workflow.profile_engine.ti3_data import lab_to_xyz, xyz_to_lab
        jab = self._cam_s.xyz_to_jab(lab_to_xyz(lab))
        mapped = self._inner.map_lab(jab)
        return xyz_to_lab(np.clip(self._cam_d.jab_to_xyz(mapped),
                                  0.0, None))


def invert_mapping(mapper, mapped_lab: np.ndarray, iters: int = 20
                   ) -> np.ndarray:
    """Numeric inverse of a gamut map (colprof -nI: the perceptual A2B gets
    the inverse mapping applied so A2B0∘B2A0 ≈ identity in gamut).

    Damped fixed-point iteration — the map is a contraction near identity
    in the protected core and monotone along its rays, so this converges
    for the in-gamut colours the A2B actually carries.
    """
    lab0 = mapped_lab.copy()
    for _ in range(iters):
        err = mapped_lab - mapper.map_lab(lab0)
        lab0 = lab0 + 0.7 * err
        if np.abs(err).max() < 1e-3:
            break
    return lab0


def build_mapped_b2a(model: ForwardModel, meas: Ti3Measurement, grid: int,
                     source_gamut: Path, *, channel_letters: list[str],
                     is_additive: bool, ink_limit: float | None,
                     entries: int, codec=None, settings=None,
                     a2b_grid: int | None = None,
                     a2b_entries: int | None = None) -> dict:
    """Mapped tables per intent → dict of mft2 tags/aliases for the writer.

    Returns entries for ``B2A0``/``B2A2`` (bytes or the alias string
    ``"B2A1"``) and, with colprof's ``-nI``, inverse-mapped ``A2B0``/``A2B2``.
    """
    from workflow.profile_engine.pcs import LabPcs
    codec = codec or LabPcs
    # colprof default computes the source surface through the source
    # profile's perceptual/saturation intent per table; -nP/-nS switch that
    # table's source to the colorimetric intent (xicc: noptop/nostos).
    perc_int = 1 if getattr(settings, "perc_src_colorimetric", False) else 0
    sat_int = 1 if getattr(settings, "sat_src_colorimetric", False) else 2
    dst = destination_surface_lab(model, ink_limit=ink_limit,
                                  is_additive=is_additive)
    node_lab = codec.node_lab(grid)
    inv_curves = b2a_mod.inverse_curves(model.curves)
    out: dict[str, bytes | str] = {}
    mappers: dict[str, object] = {}

    for tag, intent, s_int, default_kw in (
            ("B2A0", getattr(settings, "perc_intent", ""), perc_int, {}),
            ("B2A2", getattr(settings, "sat_intent", ""), sat_int,
             _SAT_DEFAULT)):
        src = source_surface_from_profile(source_gamut, intent=s_int)
        mapper = _mapper_for(intent, default_kw, src, dst, settings)
        if mapper is None:
            out[tag] = "B2A1"          # colorimetric-family intent: exact
            continue
        mappers[tag] = mapper
        mapped = mapper.map_lab(node_lab)
        # Mapped targets land inside (or at) the gamut surface, so per-node
        # inversion converges everywhere — the boundary-cell kink that makes
        # the colorimetric table need a global refit doesn't arise here.
        dev, _residual = b2a_mod.invert_to_device(
            model, mapped, channel_letters=channel_letters,
            is_additive=is_additive, ink_limit=ink_limit)
        shaped = model.shape_device(dev)
        out[tag] = icw.make_mft2(
            3, model.n_channels, grid, icw.device_to_u16(shaped),
            in_tables=np.tile(icw._identity_table(entries), (3, 1)),
            out_tables=icw.curves_to_tables(inv_curves, entries))

    if getattr(settings, "inverse_gamut_a2b", False) and mappers \
            and a2b_grid is not None:
        # colprof -nI: perceptual/saturation A2B tables carry the INVERSE
        # gamut mapping, so table pairs round-trip to identity in gamut.
        n = model.n_channels
        for a2b_tag, b2a_tag in (("A2B0", "B2A0"), ("A2B2", "B2A2")):
            mapper = mappers.get(b2a_tag)
            if mapper is None:
                continue
            inv_lab = invert_mapping(mapper, model.clut_lab())
            out[a2b_tag] = icw.make_mft2(
                n, 3, a2b_grid, codec.encode(inv_lab),
                in_tables=icw.curves_to_tables(model.curves,
                                               a2b_entries or entries),
                out_tables=np.tile(icw._identity_table(a2b_entries or entries),
                                   (3, 1)))
    return out
