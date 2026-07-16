"""CIECAM02 with Argyll's modifications — xicc/cam02.c, literal port
(AGPL-3.0, Graeme W. Gill — see package ``__init__``).

Active build configuration transcribed verbatim (cam02.c L135–199):
ENABLE_COMPR, ENABLE_BLUE_ANGLE_FIX, ENABLE_DDL, ENABLE_BLUELIN and
SYMETRICJ are defined; ENABLE_DECOMPR, ENABLE_SS, DISABLE_* are not.
HK effect on with HHKR_MUL 0.25 (xicc default hk=XICC_USE_HK=1,
hkscale=1.0). Vectorised over (N, 3) XYZ/Jab arrays.

Viewing conditions: :func:`view_d` builds xicc.c's "d — Default Viewing
Condition" (the colprof default when no -c/-d is given): vc_average,
La=50, Yb=0.2, Yf=0, Yg=0.05, glare colour = media white.
"""
from __future__ import annotations

import numpy as np

HHKR_MUL = 0.25
BC_WHMINY = 0.2
BC_RANGE = np.array([0.01, 0.01, 0.01])     # R, G, B
BC_MAXRANGE = 0.13
BC_LIMIT = 0.7
BLUE_BL_MAX = 0.9
BLUE_BL_POW = 3.5
NLDLIMIT = 0.00001
NLDICEPT = -0.18
NLULIMIT = 1e5
DDLLIMIT = 0.55
DDULIMIT = 0.34
SSMINcJ = 0.005
JLIMIT = 0.005
HKLIMIT = 0.7
BLUELIN_h0, BLUELIN_h1 = 210.0, 330.0
BLUELIN_C0, BLUELIN_C10, BLUELIN_C11 = 50.0, 80.0, 140.0
BLUELIN_AMNT = 0.60

# spectrally sharpened cone response (CAT02)
_SCR = np.array([[0.7328, 0.4296, -0.1624],
                 [-0.7036, 1.6975, 0.0061],
                 [0.0000, 0.0000, 1.0000]])
# Hunt-Pointer-Estevez (cam02.c exact values)
_HPE = np.array([[0.7409744840453773, 0.2180245944753982,
                  0.0410009214792244],
                 [0.2853532916858801, 0.6242015741188157,
                  0.0904451341953042],
                 [-0.0096276087384294, -0.0056980312161134,
                  1.0153256399545427]])

ICM_D50 = np.array([0.9642, 1.0000, 0.8249])


def view_d(wxyz=ICM_D50):
    """xicc.c default viewing condition 'd' (colprof with no -c/-d)."""
    return dict(Ev="average", Wxyz=np.asarray(wxyz, float), La=50.0,
                Yb=0.2, Lv=250.0, Yf=0.0, Yg=0.05, Gxyz=None,
                hk=1, hkscale=1.0)


class Cam02:
    def __init__(self, vc: dict) -> None:
        Ev = vc.get("Ev", "average")
        Wxyz = np.asarray(vc["Wxyz"], float)
        La, Yb, Lv = vc["La"], vc["Yb"], vc.get("Lv", 250.0)
        Yf, Yg = vc.get("Yf", 0.0), vc.get("Yg", 0.05)
        Gxyz = vc.get("Gxyz")
        self.hk = vc.get("hk", 1)
        self.hkscale = vc.get("hkscale", 1.0)
        self.hklimit = 1.0 / HKLIMIT

        if Ev == "none":
            r = np.clip(La / max(Lv, 1e-10), 0.0, 1.0)
            t_C = [0.525, 0.59, 0.69, 1.0]
            t_Nc = [0.800, 0.95, 1.00, 1.0]
            t_F = [0.800, 0.90, 1.00, 1.0]
            if r < 0.1:
                i, bf = 0, r / 0.1
            elif r < 0.2:
                i, bf = 1, (r - 0.1) / 0.1
            else:
                i, bf = 2, (r - 0.2) / 0.8
            self.C = t_C[i] * (1 - bf) + t_C[i + 1] * bf
            self.Nc = t_Nc[i] * (1 - bf) + t_Nc[i + 1] * bf
            self.F = t_F[i] * (1 - bf) + t_F[i + 1] * bf
        else:
            self.C, self.Nc, self.F, dv = {
                "dark": (0.525, 0.8, 0.8, 0.033),
                "dim": (0.59, 0.95, 0.9, 0.1),
                "average": (0.69, 1.0, 1.0, 0.2),
                "cut_sheet": (0.41, 0.8, 0.8, 0.02),
            }[Ev]
            Lv = La / dv

        self.Wxyz = Wxyz
        self.La = La
        self.Yb = max(Yb, 0.005)
        self.Lv = Lv
        if Gxyz is not None and np.all(np.asarray(Gxyz) > 0.0):
            g = np.asarray(Gxyz, float)
            self.Gxyz = g * (Wxyz[1] / g[1])
        else:
            self.Gxyz = Wxyz.copy()

        # flare + glare contribution (Fsxyz), rescaled so flare+white ≤ W
        Fsxyz = Yf * Wxyz + (Yg * La / Lv) * self.Gxyz
        self.Fsc = Wxyz[1] / (Fsxyz[1] + Wxyz[1])
        self.Fsxyz = Fsxyz * self.Fsc
        self.Fisc = 1.0 / self.Fsc

        rgbW = _SCR @ Wxyz
        self.D = self.F * (1.0 - np.exp((-La - 42.0) / 92.0) / 3.6)
        Drgb = self.D * (Wxyz[1] / rgbW) + 1.0 - self.D
        rgbcW = Drgb * rgbW
        self.rgbpW = _HPE @ rgbcW
        # combined cone + chromatic transform (cc) and inverse
        self.cc = _HPE @ (np.diag(Drgb) @ _SCR)
        self.icc = np.linalg.inv(self.cc)
        self.crange = BC_RANGE

        self.n = self.Yb / Wxyz[1]
        self.nn = (1.64 - 0.29 ** self.n) ** 0.73
        k = 1.0 / (5.0 * La + 1.0)
        self.Fl = (0.2 * k ** 4 * 5.0 * La
                   + 0.1 * (1.0 - k ** 4) ** 2 * (5.0 * La) ** (1.0 / 3.0))
        self.Nbb = 0.725 * (1.0 / self.n) ** 0.2
        self.Ncb = self.Nbb
        self.z = 1.48 + np.sqrt(self.n)

        tt = (self.Fl * self.rgbpW) ** 0.42
        self.rgbaW = 400.0 * tt / (tt + 27.13) + 0.1
        self.Aw = ((2.0 * self.rgbaW[0] + self.rgbaW[1]
                    + self.rgbaW[2] / 20.0) - 0.305) * self.Nbb

        tt = (self.Fl * NLDLIMIT) ** 0.42
        self.nldxval = 400.0 * tt / (tt + 27.13) + 0.1
        self.nldxslope = (self.nldxval - 0.1) / (NLDLIMIT - NLDICEPT)
        tt = (self.Fl * NLULIMIT) ** 0.42
        self.nluxval = 400.0 * tt / (tt + 27.13) + 0.1
        t1 = self.Fl * NLULIMIT
        t2 = t1 ** 0.42 + 27.13
        self.nluxslope = (0.42 * self.Fl * 400.0 * 27.13
                          / (t1 ** 0.58 * t2 * t2))
        self.lA = JLIMIT ** (1.0 / (self.C * self.z)) * self.Aw

    # ---- forward: XYZ (Y 0..1) → Jab ----
    def xyz_to_cam(self, xyz_in: np.ndarray) -> np.ndarray:
        xyz = np.atleast_2d(np.asarray(xyz_in, float))
        xyz = self.Fsc * xyz + self.Fsxyz[None, :]
        rgbp = xyz @ self.cc.T

        # ENABLE_COMPR: soft-compress each channel to stay above zero
        wy = np.maximum(xyz[:, 1], BC_WHMINY)
        for i in range(3):
            wrgb = self.rgbpW[None, :] * (wy / self.Wxyz[1])[:, None]
            cvec = wrgb - rgbp
            ok = cvec[:, i] >= 1e-9
            cvecn = np.where(ok[:, None],
                             cvec / np.where(ok, cvec[:, i], 1.0)[:, None],
                             0.0)
            isec = rgbp - cvecn * rgbp[:, i][:, None]
            offs = np.linalg.norm(isec, axis=1) ** 0.85
            rng = np.minimum(self.crange[i] * offs, BC_MAXRANGE)
            asym = rng - 0.2 * (rng + 0.01 * self.crange[i])
            cv = rgbp[:, i]
            need = ok & (cv < rng - 1e-12)
            aa = 1.0 / np.where(need, rng - cv, 1.0)
            bb = 1.0 / np.where(need, rng - asym, 1.0)
            ctv = rng - 1.0 / (aa + bb)
            cd = np.minimum(np.where(need, ctv - cv, 0.0), BC_LIMIT)
            rgbp = rgbp + cvecn * cd[:, None]

        # ENABLE_BLUE_ANGLE_FIX
        ssum = rgbp.sum(1)
        bl = np.where(ssum < 1e-9, 0.0,
                      (rgbp[:, 2] / np.where(ssum < 1e-9, 1.0, ssum)
                       - 1.0 / 3.0) * 1.5)
        bl = np.where(bl > 0.0,
                      BLUE_BL_MAX * np.maximum(bl, 0.0) ** BLUE_BL_POW,
                      0.0)
        bl = np.clip(bl, 0.0, 1.0)
        tt = 0.5 * (rgbp[:, 0] + rgbp[:, 1])
        rgbp[:, 0] = bl * tt + (1.0 - bl) * rgbp[:, 0]
        rgbp[:, 1] = bl * tt + (1.0 - bl) * rgbp[:, 1]

        # post-adapted cone response with linear extensions
        rgba = np.empty_like(rgbp)
        lo = rgbp < NLDLIMIT
        hi = rgbp > NLULIMIT
        mid = ~lo & ~hi
        rgba[lo] = self.nldxval + self.nldxslope * (rgbp[lo] - NLDLIMIT)
        t = (self.Fl * rgbp[mid]) ** 0.42
        rgba[mid] = 400.0 * t / (t + 27.13) + 0.1
        rgba[hi] = self.nluxval + self.nluxslope * (rgbp[hi] - NLULIMIT)

        ttA = 2.0 * rgba[:, 0] + rgba[:, 1] + rgba[:, 2] / 20.0
        A = (ttA - 0.305) * self.Nbb
        a = rgba[:, 0] - 12.0 / 11.0 * rgba[:, 1] + rgba[:, 2] / 11.0
        b = (rgba[:, 0] + rgba[:, 1] - 2.0 * rgba[:, 2]) / 9.0
        rS = np.maximum(np.hypot(a, b), np.finfo(float).eps)

        # SYMETRICJ
        J = np.where(A >= 0.0,
                     np.abs(A / self.Aw) ** (self.C * self.z),
                     -np.abs(-A / self.Aw) ** (self.C * self.z))
        cJ = np.where(A > 0.0,
                      np.maximum(np.abs(A / self.Aw) ** (self.C * self.z),
                                 SSMINcJ), SSMINcJ)

        h = np.degrees(np.arctan2(b, a))
        h = np.where(h < 0.0, h + 360.0, h)
        e = (12500.0 / 13.0 * self.Nc * self.Ncb
             * (np.cos(np.radians(h) + 2.0) + 3.8))
        k1 = (self.nn ** (1.0 / 0.9) * e * cJ ** (1.0 / 1.8)
              / rS ** (1.0 / 9.0))
        k2 = cJ ** (1.0 / (self.C * self.z)) * self.Aw / self.Nbb + 0.305
        k3 = -11.0 / 23.0 * a - 108.0 / 23.0 * b
        # ENABLE_DDL clamps
        k3 = np.maximum(k3, -k2 * DDLLIMIT)
        k3 = np.minimum(k3, k2 * DDULIMIT / (1.0 - DDULIMIT))
        ss = (k1 / (k2 + k3)) ** 0.9

        ja = a * ss
        jb = b * ss
        Cc = np.hypot(ja, jb)
        JJ = J.copy()
        if self.hk:
            kk = (self.hkscale * HHKR_MUL * Cc / 300.0
                  * np.sin(np.pi * np.abs(0.5 * (h - 90.0)) / 180.0))
            kk = np.where(kk > 1e-6, 1.0 / (self.hklimit + 1.0 / kk), kk)
            lift = (1.0 - np.maximum(J, 0.0)) * kk
            JJ = np.where(J < 1.0, J + lift, J)
        out = np.stack([JJ * 100.0, ja, jb], 1)
        return _bluelin(out, fwd=True)

    # ---- reverse: Jab → XYZ ----
    def cam_to_xyz(self, jab_in: np.ndarray) -> np.ndarray:
        jab = _bluelin(np.atleast_2d(np.asarray(jab_in, float)), fwd=False)
        JJ = jab[:, 0] * 0.01
        ja, jb = jab[:, 1], jab[:, 2]
        h = np.degrees(np.arctan2(jb, ja))
        h = np.where(h < 0.0, h + 360.0, h)
        Cc = np.hypot(ja, jb)
        rC = np.maximum(Cc, np.finfo(float).eps)
        J = JJ.copy()
        if self.hk:
            kk = (self.hkscale * HHKR_MUL * Cc / 300.0
                  * np.sin(np.pi * np.abs(0.5 * (h - 90.0)) / 180.0))
            kk = np.where(kk > 1e-6, 1.0 / (self.hklimit + 1.0 / kk), kk)
            Jn = (JJ - kk) / (1.0 - kk)
            Jn = np.where(Jn < 0.0, JJ - kk, Jn)
            J = np.where(JJ < 1.0, Jn, JJ)
        # SYMETRICJ
        A = np.where(J >= 0.0,
                     np.abs(J) ** (1.0 / (self.C * self.z)) * self.Aw,
                     -np.abs(-J) ** (1.0 / (self.C * self.z)) * self.Aw)
        ttA = A / self.Nbb + 0.305
        cJ = np.where(A > 0.0,
                      np.maximum(np.abs(A / self.Aw) ** (self.C * self.z),
                                 SSMINcJ), SSMINcJ)
        e = (12500.0 / 13.0 * self.Nc * self.Ncb
             * (np.cos(np.radians(h) + 2.0) + 3.8))
        k1 = (self.nn ** (1.0 / 0.9) * e * cJ ** (1.0 / 1.8)
              / rC ** (1.0 / 9.0))
        k2 = cJ ** (1.0 / (self.C * self.z)) * self.Aw / self.Nbb + 0.305
        k3 = -11.0 / 23.0 * ja - 108.0 / 23.0 * jb
        k3 = np.minimum(k3, k1 * DDULIMIT)
        k3 = np.maximum(k3, -k1 * DDLLIMIT / (1.0 - DDLLIMIT))
        ss = (k1 - k3) / k2
        a = ja / ss
        b = jb / ss
        rgba = np.stack([
            20.0 / 61.0 * ttA + (41.0 * 11.0) / (61.0 * 23.0) * a
            + (288.0 * 1.0) / (61.0 * 23.0) * b,
            20.0 / 61.0 * ttA - (81.0 * 11.0) / (61.0 * 23.0) * a
            - (261.0 * 1.0) / (61.0 * 23.0) * b,
            20.0 / 61.0 * ttA - (20.0 * 11.0) / (61.0 * 23.0) * a
            - (20.0 * 315.0) / (61.0 * 23.0) * b], 1)
        rgbp = np.empty_like(rgba)
        lo = rgba < self.nldxval
        hi = rgba > self.nluxval
        mid = ~lo & ~hi
        rgbp[lo] = NLDLIMIT + (rgba[lo] - self.nldxval) / self.nldxslope
        t = rgba[mid] - 0.1
        rgbp[mid] = ((27.13 * t) / (400.0 - t)) ** (1.0 / 0.42) / self.Fl
        rgbp[hi] = NLULIMIT + (rgba[hi] - self.nluxval) / self.nluxslope
        # undo blue angle fix
        ssum = rgbp.sum(1)
        bl = np.where(ssum < 1e-9, 0.0,
                      (rgbp[:, 2] / np.where(ssum < 1e-9, 1.0, ssum)
                       - 1.0 / 3.0) * 1.5)
        bl = np.where(bl > 0.0,
                      BLUE_BL_MAX * np.maximum(bl, 0.0) ** BLUE_BL_POW,
                      0.0)
        bl = np.clip(bl, 0.0, 1.0)
        tt = 0.5 * (rgbp[:, 0] + rgbp[:, 1])
        rgbp[:, 0] = (rgbp[:, 0] - bl * tt) / (1.0 - bl)
        rgbp[:, 1] = (rgbp[:, 1] - bl * tt) / (1.0 - bl)
        # (ENABLE_DECOMPR is undef — no un-compression, as in the C)
        xyz = rgbp @ self.icc.T
        return self.Fisc * (xyz - self.Fsxyz[None, :])


def _bluelin(jab: np.ndarray, fwd: bool) -> np.ndarray:
    """ENABLE_BLUELIN hue-linearity tweak (cam02.c L222–299)."""
    out = jab.copy()
    C = np.hypot(jab[:, 1], jab[:, 2])
    h = np.degrees(np.arctan2(jab[:, 2], jab[:, 1]))
    h = np.where(h < 0.0, h + 360.0, h)
    sel = (h >= BLUELIN_h0) & (h <= BLUELIN_h1) & (C > BLUELIN_C0)
    if sel.any():
        hh = (h[sel] - BLUELIN_h0) / (BLUELIN_h1 - BLUELIN_h0)
        Cs = C[sel]
        if fwd:
            c1 = (1.0 - hh) * BLUELIN_C10 + hh * BLUELIN_C11
            gr = np.clip((Cs - BLUELIN_C0) / (c1 - BLUELIN_C0), 0.0, 1.0)
            amnt = (1.0 - gr) + gr * BLUELIN_AMNT
            ho = np.where(hh < 0.5, hh * amnt,
                          0.5 * amnt + (hh - 0.5) * (1.0 - 0.5 * amnt)
                          / 0.5)
        else:
            ho = hh.copy()
            c1 = np.zeros_like(hh)
            pc1 = np.full_like(hh, -100.0)
            for _ in range(20):
                if np.all(np.abs(c1 - pc1) <= 0.02):
                    break
                pc1 = c1
                c1 = (1.0 - ho) * BLUELIN_C10 + ho * BLUELIN_C11
                gr = np.clip((Cs - BLUELIN_C0) / (c1 - BLUELIN_C0),
                             0.0, 1.0)
                amnt = (1.0 - gr) + gr * BLUELIN_AMNT
                ho = np.where(hh < 0.5 * amnt, hh / amnt,
                              0.5 + (hh - 0.5 * amnt) * 0.5
                              / (1.0 - 0.5 * amnt))
        hn = np.radians(BLUELIN_h0 + ho * (BLUELIN_h1 - BLUELIN_h0))
        out[sel, 1] = Cs * np.cos(hn)
        out[sel, 2] = Cs * np.sin(hn)
    return out


# Bradford cone matrix (icclib arts-tag absolute↔relative transform)
_BFD = np.array([[0.8951, 0.2664, -0.1614],
                 [-0.7502, 1.7135, 0.0367],
                 [0.0389, -0.0685, 1.0296]])


def bradford(xyz: np.ndarray, src_white, dst_white) -> np.ndarray:
    d = ((_BFD @ np.asarray(dst_white, float))
         / (_BFD @ np.asarray(src_white, float)))
    m = np.linalg.inv(_BFD) @ np.diag(d) @ _BFD
    return np.atleast_2d(np.asarray(xyz, float)) @ m.T


class Appearance:
    """The exact xicc icxAppearance conversion: relative (D50) Lab ↔ Jab.

    rel Lab → XYZ → Bradford D50→media-white (icclib v3 'arts' absolute
    intent) → cam02 under the 'd' default viewing condition with the
    media white. Validated against ``xicclu -ir -pj`` on both a matrix
    source profile and a printer cLUT profile: 0.0001 median.
    """

    def __init__(self, media_white_xyz) -> None:
        self.white = np.asarray(media_white_xyz, float)
        self._cam = Cam02(view_d(self.white))

    def lab_to_jab(self, lab: np.ndarray) -> np.ndarray:
        xyz = bradford(lab_to_xyz(lab), ICM_D50, self.white)
        return self._cam.xyz_to_cam(xyz)

    def jab_to_lab(self, jab: np.ndarray) -> np.ndarray:
        xyz = bradford(self._cam.cam_to_xyz(jab), self.white, ICM_D50)
        return xyz_to_lab(xyz)


def lab_to_xyz(lab: np.ndarray, white=ICM_D50) -> np.ndarray:
    """CIE Lab (D50 by default) → XYZ with Y 0..1 (icclib convention)."""
    lab = np.atleast_2d(np.asarray(lab, float))
    fy = (lab[:, 0] + 16.0) / 116.0
    fx = fy + lab[:, 1] / 500.0
    fz = fy - lab[:, 2] / 200.0

    def fi(f):
        return np.where(f > 24.0 / 116.0, f ** 3,
                        (f - 16.0 / 116.0) / 7.787036979)
    return np.stack([fi(fx) * white[0], fi(fy) * white[1],
                     fi(fz) * white[2]], 1)


def xyz_to_lab(xyz: np.ndarray, white=ICM_D50) -> np.ndarray:
    xyz = np.atleast_2d(np.asarray(xyz, float))
    r = xyz / np.asarray(white, float)[None, :]

    def f(t):
        return np.where(t > 0.008856451586,
                        np.cbrt(np.maximum(t, 1e-30)),
                        7.787036979 * t + 16.0 / 116.0)
    fx, fy, fz = f(r[:, 0]), f(r[:, 1]), f(r[:, 2])
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy),
                     200.0 * (fy - fz)], 1)
