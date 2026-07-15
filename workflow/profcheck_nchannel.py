"""A profcheck-compatible accuracy check that also handles CMY+N profiles.

ArgyllCMS's ``profcheck`` refuses device colorspaces beyond 4 inks (its device
reader is hardcoded to Grey/RGB/CMY/CMYK). But the accuracy check itself is
simple: look the measured device values up through the profile's forward (A2B)
table and compare the result to the measured CIE value. The forward lookup works
for any channel count via Argyll's ``icclu`` (icclib supports 2..15 colour
channels), so this module reproduces profcheck for >4-ink profiles by driving
``icclu`` and computing the ΔE itself.

The output is byte-for-byte in profcheck's format so the existing
:mod:`workflow.profcheck_runner` parsing — summary line, per-patch
``[dE] SID @ LOC:`` lines, quality grading and refine-strip flagging — works
unchanged. Validated line-for-line against real profcheck on RGB/CMYK profiles
(where both can run).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from core.logger import get_logger

log = get_logger(__name__)


class NChannelCheckError(RuntimeError):
    """The N-channel accuracy check could not run."""


def ti3_device_part(color_rep: str) -> str:
    """Device part of a COLOR_REP, e.g. ``CMYKOG_XYZ`` -> ``CMYKOG``."""
    return (color_rep or "").split("_")[0]


def _read_ti3(ti3_path: Path) -> dict:
    """Parse the fields we need from a .ti3: device + CIE + ids."""
    text = Path(ti3_path).read_text(errors="replace")
    m = re.search(r'^COLOR_REP\s+"([^"]+)"', text, re.M)
    if not m:
        raise NChannelCheckError("no COLOR_REP in measurement file")
    color_rep = m.group(1)
    dev = ti3_device_part(color_rep)
    is_lab = color_rep.endswith("_LAB") or color_rep.startswith("LAB_")

    fmt = re.search(r"BEGIN_DATA_FORMAT\s*\n(.*?)\nEND_DATA_FORMAT",
                    text, re.S)
    if not fmt:
        raise NChannelCheckError("no DATA_FORMAT in measurement file")
    fields = fmt.group(1).split()

    def col(name: str) -> int:
        try:
            return fields.index(name)
        except ValueError as exc:
            raise NChannelCheckError(f"missing field {name}") from exc

    # device columns: <DEV>_<letter> for each ink letter in the rep
    dev_cols = [col(f"{dev}_{ch}") for ch in dev]
    sid_col = col("SAMPLE_ID")
    loc_col = fields.index("SAMPLE_LOC") if "SAMPLE_LOC" in fields else -1
    if is_lab:
        cie_cols = [col("LAB_L"), col("LAB_A"), col("LAB_B")]
    else:
        cie_cols = [col("XYZ_X"), col("XYZ_Y"), col("XYZ_Z")]

    body = re.search(r"\nBEGIN_DATA\s*\n(.*?)\nEND_DATA", text, re.S)
    if not body:
        raise NChannelCheckError("no DATA in measurement file")
    sids, locs, devs, cies = [], [], [], []
    for line in body.group(1).splitlines():
        p = line.split()
        if len(p) < len(fields):
            continue
        sids.append(p[sid_col])
        locs.append(p[loc_col] if loc_col >= 0 else "")
        devs.append([float(p[c]) for c in dev_cols])
        cies.append([float(p[c]) for c in cie_cols])
    return {
        "dev": dev, "n": len(dev), "is_lab": is_lab,
        "sid": sids, "loc": locs,
        "device": np.array(devs), "cie": np.array(cies),
    }


def _xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    """XYZ (Y≈100 scale) -> D50 Lab, matching Argyll's icmXYZ2Lab."""
    wp = np.array([96.420288, 100.0, 82.490540])  # Argyll icmD50 * 100
    f = np.where((xyz / wp) > (6/29)**3,
                 np.cbrt(xyz / wp),
                 (xyz / wp) / (3 * (6/29)**2) + 4/29)
    return np.stack([116 * f[:, 1] - 16,
                     500 * (f[:, 0] - f[:, 1]),
                     200 * (f[:, 1] - f[:, 2])], 1)


def _icclu_forward(icc: Path, bin_dir: Path, device01: np.ndarray,
                   intent: str) -> np.ndarray:
    """Device (0..1, N-channel) -> Lab via icclu forward A2B."""
    icclu = bin_dir / "icclu"
    if not icclu.exists():
        raise NChannelCheckError("icclu not found")
    ic = {"a": "-ia", "r": "-ir", "p": "-ip", "s": "-is"}.get(intent, "-ir")
    inp = "\n".join(" ".join(f"{v:.8f}" for v in row) for row in device01)
    r = subprocess.run([str(icclu), "-ff", ic, "-pl", str(icc)],
                       input=inp + "\n", capture_output=True, text=True)
    num = re.compile(r"-?\d+\.\d+")
    out = []
    for ln in r.stdout.splitlines():
        if "->" in ln and "Lab" in ln:
            tail = ln.split("->")[-1].split("[")[0]
            out.append([float(x) for x in num.findall(tail)][:3])
    if len(out) != len(device01):
        raise NChannelCheckError(
            f"icclu returned {len(out)} of {len(device01)} rows: "
            f"{(r.stderr or '')[:160]}")
    return np.array(out)


def _delta_e(a: np.ndarray, b: np.ndarray, formula: str) -> np.ndarray:
    """ΔE between two Lab arrays. formula '' = CIE76, '-c' = CIE94, '-k' = 2000."""
    if formula == "-c":
        return _de94(a, b)
    if formula == "-k":
        return _de2000(a, b)
    return np.sqrt(((a - b) ** 2).sum(1))


def _de94(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    dL = lab1[:, 0] - lab2[:, 0]
    C1 = np.hypot(lab1[:, 1], lab1[:, 2])
    C2 = np.hypot(lab2[:, 1], lab2[:, 2])
    dC = C1 - C2
    da = lab1[:, 1] - lab2[:, 1]
    db = lab1[:, 2] - lab2[:, 2]
    dH2 = np.maximum(da * da + db * db - dC * dC, 0.0)
    sC = 1 + 0.045 * C1
    sH = 1 + 0.015 * C1
    return np.sqrt(dL * dL + (dC / sC) ** 2 + dH2 / (sH * sH))


def _de2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    L1, a1, b1 = lab1[:, 0], lab1[:, 1], lab1[:, 2]
    L2, a2, b2 = lab2[:, 0], lab2[:, 1], lab2[:, 2]
    Cb = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    G = 0.5 * (1 - np.sqrt(Cb**7 / (Cb**7 + 25.0**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, np.where(dhp < -180, dhp + 360, dhp))
    dhp = np.where((C1p * C2p) == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2)
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    hsum = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2, hsum / 2)
    hbp = np.where((C1p * C2p) == 0, hsum, hbp)
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30))
         + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6))
         - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dth = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbp**7 / (Cbp**7 + 25.0**7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


def _pdv(n: int, dev: np.ndarray) -> str:
    """icmPdv: device values joined with a space, 8 decimals (as profcheck)."""
    return " ".join(f"{v:.8f}" for v in dev[:n])


def run_check(ti3_path, icc_path, *, bin_dir, de_formula="", intent="a",
              sort=True, verbosity="2"):
    """Run the check and return profcheck-format output lines (list of str)."""
    ti3_path, icc_path = Path(ti3_path), Path(icc_path)
    bin_dir = Path(bin_dir)
    data = _read_ti3(ti3_path)
    n = data["n"]
    device01 = data["device"] / 100.0
    pred = _icclu_forward(icc_path, bin_dir, device01, intent)
    meas = data["cie"] if data["is_lab"] else _xyz_to_lab(data["cie"])
    de = _delta_e(pred, meas, de_formula)

    lines = [f"No of test patches = {len(de)}"]
    order = np.argsort(-de) if sort else np.arange(len(de))
    if verbosity and int(verbosity) >= 2:
        for i in order:
            loc = data["loc"][i]
            tag = f"{data['sid'][i]}{(' @ ' + loc) if loc else ''}"
            lines.append(
                f"[{de[i]:f}] {tag}: {_pdv(n, data['device'][i] / 100.0)} -> "
                f"{pred[i,0]:f} {pred[i,1]:f} {pred[i,2]:f} should be "
                f"{meas[i,0]:f} {meas[i,1]:f} {meas[i,2]:f}")
    suffix = {"-c": " (CIE94)", "-k": " (CIEDE2000)"}.get(de_formula, "")
    rms = float(np.sqrt((de ** 2).mean()))
    lines.append(
        f"Profile check complete, errors{suffix}: "
        f"max. = {de.max():f}, avg. = {de.mean():f}, RMS = {rms:f}")
    return lines
