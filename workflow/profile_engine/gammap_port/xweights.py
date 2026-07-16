"""Weight expansion + per-point interpolation — nearsmth.c L1137–1600
(AGPL-3.0, Graeme W. Gill — see package ``__init__``).

The 23-value flat layout matches ``near_wcopy``'s field order exactly
(verified against the C): c.w.l/c/h, c.tw, c.cx | l.o/h/l (radial) |
a.o/h/wl/gl/bl/wlth/blpow/lxpow/lxthr | r.rdl/rdh/dsm | d.co/xo | f.x.

``expand_weights`` fills 14 hextant slots (6 light + neutral, 6 dark +
neutral) from the compact table via the channel-mask passes; -1 values
inherit (near_wcopy). ``interp_xweights`` blends slots per point: smooth-
step hue blend between the two enclosing cusps, neutral blend below C=20,
light/dark smoothstep between L 5 and 70, then converts the dominance
triples to raw component weights (``comp_iweight``), with the white/grey/
black L-dominance blended in **log-ratio space** (the C's exact formula).
"""
from __future__ import annotations

import numpy as np

# field indexes (near_wcopy order)
CWL, CWC, CWH, CTW, CCX = 0, 1, 2, 3, 4
LO, LH, LL = 5, 6, 7                      # radial dominance triple
AO, AH, AWL, AGL, ABL, AWLTH, ABLPOW, ALXPOW, ALXTHR = 8, 9, 10, 11, 12, 13, 14, 15, 16
RRDL, RRDH, RDSM = 17, 18, 19
DCO, DXO = 20, 21
FX = 22

LIGHT_L = 70.0
DARK_L = 5.0
NEUTRAL_C = 20.0

_MASKS = {
    "gmm_light_red": 0x101, "gmm_light_yellow": 0x102,
    "gmm_light_green": 0x104, "gmm_light_cyan": 0x108,
    "gmm_light_blue": 0x110, "gmm_light_magenta": 0x120,
    "gmm_light_neutral": 0x140,
    "gmm_dark_red": 0x201, "gmm_dark_yellow": 0x202,
    "gmm_dark_green": 0x204, "gmm_dark_cyan": 0x208,
    "gmm_dark_blue": 0x210, "gmm_dark_magenta": 0x220,
    "gmm_dark_neutral": 0x240,
    "gmm_l_d_red": 0x301, "gmm_l_d_yellow": 0x302, "gmm_l_d_green": 0x304,
    "gmm_l_d_cyan": 0x308, "gmm_l_d_blue": 0x310, "gmm_l_d_magenta": 0x320,
    "gmm_l_d_neutral": 0x340,
    "gmm_light_colors": 0x17F, "gmm_dark_colors": 0x27F,
    "gmm_default": 0x37F,
}
_GMC_LIGHT, _GMC_DARK, _GMC_L_D, _GMC_COLORS = 0x100, 0x200, 0x300, 0x07F

_SLOT_MASKS = [0x101, 0x102, 0x104, 0x108, 0x110, 0x120, 0x140,
               0x201, 0x202, 0x204, 0x208, 0x210, 0x220, 0x240]


def _wcopy(dst: np.ndarray, src: np.ndarray) -> None:
    """near_wcopy: fields < 0 inherit."""
    m = src >= 0.0
    dst[m] = src[m]


def expand_weights(table: list[tuple[str, list[float]]]) -> np.ndarray:
    """(14, 23) expanded hextant weights (nearsmth.c expand_weights,
    including the four mask-priority passes)."""
    out = np.full((14, 23), np.nan)
    outset = np.zeros(14, dtype=bool)
    entries = [(_MASKS[tag], np.asarray(v, dtype=float)) for tag, v in table]

    def apply(pred) -> None:
        for mask, vals in entries:
            if not pred(mask):
                continue
            for j, sm in enumerate(_SLOT_MASKS):
                if (mask & sm) == sm:
                    if not outset[j]:
                        out[j] = vals.copy()
                    else:
                        _wcopy(out[j], vals)
                    outset[j] = True

    apply(lambda m: m == _MASKS["gmm_default"])
    apply(lambda m: m in (_MASKS["gmm_light_colors"],
                          _MASKS["gmm_dark_colors"]))
    apply(lambda m: (m & _GMC_L_D) == _GMC_L_D
          and (m & _GMC_COLORS) != _GMC_COLORS)
    apply(lambda m: (m & _GMC_L_D) in (_GMC_LIGHT, _GMC_DARK)
          and (m & _GMC_COLORS) != _GMC_COLORS)
    if not outset.all():
        raise ValueError("weight table leaves hextant slots unset")
    return out


def comp_iweight(o: np.ndarray, h: np.ndarray, l: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dominance triple (overall, hue-dom, l-dom) → raw (l, c, h) weights
    (nearsmth.c comp_iweight, exact)."""
    h = np.clip(h, 0.0, 1.0)
    l = np.clip(l, 0.0, 1.0)
    lc = 1.0 - h
    c = (1.0 - l) * lc
    ll = l * lc
    oo = o / np.sqrt(ll * ll + c * c + h * h)
    return oo * ll, oo * c, oo * h


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def interp_xweights(pos: np.ndarray, xw: np.ndarray, cusp_mapping
                    ) -> dict[str, np.ndarray]:
    """Per-point weights (nearsmth.c interp_xweights, vectorised).

    Returns the raw fields (N, 23) plus the computed component weights
    ``rl`` (radial) and ``ra`` (absolute) as (N, 3) arrays.
    """
    pos = np.atleast_2d(np.asarray(pos, dtype=float))
    npts = len(pos)
    c = np.hypot(pos[:, 1], pos[:, 2])
    hh = np.degrees(np.arctan2(pos[:, 2], pos[:, 1])) % 360.0

    # hue location between the *raw source* cusps (C uses gam->getcusps)
    cusp_lch_h = np.array([
        np.degrees(np.arctan2(cusp_mapping.cusps[0][k][2],
                              cusp_mapping.cusps[0][k][1])) % 360.0
        for k in range(6)])
    light = np.empty((npts, 23))
    dark = np.empty((npts, 23))
    done = np.zeros(npts, dtype=bool)
    for li in range(6):
        ui = (li + 1) % 6
        lh = cusp_lch_h[li]
        uh = cusp_lch_h[ui]
        sh = hh.copy()
        if uh < lh:
            sh = np.where(sh < uh, sh + 360.0, sh)
            uh += 360.0
        m = (sh >= lh - 1e-12) & (sh < uh + 1e-12) & ~done
        if not m.any():
            continue
        done |= m
        uw = _smoothstep((sh[m] - lh) / max(uh - lh, 1e-9))[:, None]
        light[m] = (1.0 - uw) * xw[li][None, :] + uw * xw[ui][None, :]
        dark[m] = (1.0 - uw) * xw[7 + li][None, :] + uw * xw[7 + ui][None, :]
    if not done.all():                      # numeric wrap edge — nearest
        light[~done] = xw[0]
        dark[~done] = xw[7]

    # neutral blend below C = 20 (linear, per the C)
    nm = c < NEUTRAL_C
    if nm.any():
        lw = ((NEUTRAL_C - c[nm]) / NEUTRAL_C)[:, None]
        light[nm] = lw * xw[6][None, :] + (1.0 - lw) * light[nm]
        dark[nm] = lw * xw[13][None, :] + (1.0 - lw) * dark[nm]

    # light/dark smoothstep blend over L
    uw = _smoothstep((pos[:, 0] - DARK_L) / (LIGHT_L - DARK_L))[:, None]
    w = (1.0 - uw) * dark + uw * light

    rl = np.stack(comp_iweight(w[:, LO], w[:, LH], w[:, LL]), 1)

    # white/grey/black L dominance, blended in log-ratio space (C exact)
    lvc = cusp_mapping.comp_lvc(pos)
    wl, gl, bl = w[:, AWL], w[:, AGL], w[:, ABL]
    wlth = w[:, AWLTH]
    blpow = w[:, ABLPOW]
    lw_ = np.where(lvc >= 0,
                   np.where(lvc > 1.0 - wlth,
                            (lvc - 1.0 + wlth) / np.maximum(wlth, 1e-9), 0.0),
                   np.abs(lvc) ** blpow)
    log_w = np.log((1.0 - wl + 1e-5) / (wl + 1e-5))
    log_g = np.log((1.0 - gl + 1e-5) / (gl + 1e-5))
    log_b = np.log((1.0 - bl + 1e-5) / (bl + 1e-5))
    lr = np.where(lvc >= 0,
                  np.exp(lw_ * log_w + (1.0 - lw_) * log_g),
                  np.exp(lw_ * log_b + (1.0 - lw_) * log_g))
    ldom = ((1.0 - lr) * 1e-5 + 1.0) / (lr + 1.0)
    ra = np.stack(comp_iweight(w[:, AO], w[:, AH], ldom), 1)

    return {"w": w, "rl": rl, "ra": ra}
