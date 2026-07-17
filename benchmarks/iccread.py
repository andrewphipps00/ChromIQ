"""Minimal ICC v2 ``mft2`` evaluator — the battery judges profile *bytes*.

Scoring the in-memory model would miss quantisation, table layout and
encoding bugs; this reader replays what a CMM does: input shaper tables →
multilinear CLUT interpolation → output shaper tables, with the v2 legacy
Lab16 / u1.15 XYZ PCS encodings. Verified round-trip against
``icc_writer.make_mft2`` in the tests.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from workflow.profile_engine.icc_writer import LAB16_MAX_L, LAB16_MIN_AB, \
    LAB16_MAX_AB, XYZ16_MAX
from workflow.profile_engine.ti3_data import lab_to_xyz, xyz_to_lab


@dataclass
class Mft2:
    n_in: int
    n_out: int
    grid: int
    in_tables: np.ndarray        # (n_in, entries) float 0..1
    clut: np.ndarray             # (grid**n_in, n_out) float 0..1
    out_tables: np.ndarray       # (n_out, entries) float 0..1

    def apply(self, x01: np.ndarray) -> np.ndarray:
        """(N, n_in) encoded 0..1 → (N, n_out) encoded 0..1 (CMM replay)."""
        from workflow.profile_engine.forward_model import _interp_weights
        x = np.clip(np.atleast_2d(x01), 0.0, 1.0)
        shaped = np.empty_like(x)
        for c in range(self.n_in):
            t = self.in_tables[c]
            shaped[:, c] = np.interp(x[:, c],
                                     np.linspace(0.0, 1.0, len(t)), t)
        w, cols = _interp_weights(shaped, self.grid, self.n_in)
        mid = (w[:, :, None] * self.clut[cols]).sum(1)
        out = np.empty_like(mid)
        for c in range(self.n_out):
            t = self.out_tables[c]
            out[:, c] = np.interp(mid[:, c],
                                  np.linspace(0.0, 1.0, len(t)), t)
        return out


def _parse_mft2(blob: bytes) -> Mft2:
    if blob[:4] != b"mft2":
        raise ValueError(f"not an mft2 tag: {blob[:4]!r}")
    n_in, n_out, grid = blob[8], blob[9], blob[10]
    off = 12 + 36                                   # header + 3×3 matrix
    n_ine, n_oute = struct.unpack(">HH", blob[off:off + 4])
    off += 4
    def take(count):
        nonlocal off
        arr = np.frombuffer(blob, dtype=">u2", count=count, offset=off)
        off += 2 * count
        return arr.astype(float) / 0xFFFF
    in_tables = take(n_in * n_ine).reshape(n_in, n_ine)
    clut = take(grid ** n_in * n_out).reshape(grid ** n_in, n_out)
    out_tables = take(n_out * n_oute).reshape(n_out, n_oute)
    return Mft2(n_in, n_out, grid, in_tables, clut, out_tables)


class IccProfile:
    """Just enough ICC reading to run A2B1/B2A1/gamt and decode the PCS."""

    def __init__(self, path: Path | str) -> None:
        data = Path(path).read_bytes()
        self.pcs = data[20:24]
        ntags = struct.unpack(">I", data[128:132])[0]
        self.tags: dict[str, bytes] = {}
        for i in range(ntags):
            rec = data[132 + 12 * i:144 + 12 * i]
            sig = rec[:4].decode("latin1")
            off, size = struct.unpack(">II", rec[4:12])
            self.tags[sig] = data[off:off + size]
        self._luts: dict[str, Mft2] = {}

    def lut(self, name: str) -> Mft2:
        if name not in self._luts:
            self._luts[name] = _parse_mft2(self.tags[name])
        return self._luts[name]

    # -- PCS encodings (LUT-side 0..1 coordinates) --------------------------
    def pcs_encode(self, lab: np.ndarray) -> np.ndarray:
        """Lab → the 0..1 coordinates a B2A/gamt LUT is indexed with."""
        lab = np.atleast_2d(lab)
        if self.pcs == b"XYZ ":
            return np.clip(lab_to_xyz(lab) / 100.0 / XYZ16_MAX, 0.0, 1.0)
        lo = np.array([0.0, LAB16_MIN_AB, LAB16_MIN_AB])
        hi = np.array([LAB16_MAX_L, LAB16_MAX_AB, LAB16_MAX_AB])
        return np.clip((lab - lo[None]) / (hi - lo)[None], 0.0, 1.0)

    def pcs_decode(self, enc: np.ndarray) -> np.ndarray:
        """A2B LUT output 0..1 → Lab."""
        enc = np.atleast_2d(enc)
        if self.pcs == b"XYZ ":
            return xyz_to_lab(enc * XYZ16_MAX * 100.0)
        lo = np.array([0.0, LAB16_MIN_AB, LAB16_MIN_AB])
        hi = np.array([LAB16_MAX_L, LAB16_MAX_AB, LAB16_MAX_AB])
        return enc * (hi - lo)[None] + lo[None]

    # -- the two directions the battery scores ------------------------------
    def a2b_lab(self, device01: np.ndarray, tag: str = "A2B1") -> np.ndarray:
        return self.pcs_decode(self.lut(tag).apply(device01))

    def b2a_device(self, lab: np.ndarray, tag: str = "B2A1") -> np.ndarray:
        return self.lut(tag).apply(self.pcs_encode(lab))

    def gamut_distance(self, lab: np.ndarray) -> np.ndarray:
        """gamt tag: 0 in gamut, ΔE-scaled distance outside."""
        return self.lut("gamt").apply(self.pcs_encode(lab))[:, 0] * 128.0
