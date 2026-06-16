"""Soft-proof an image against a printer profile, and flag out-of-gamut colour.

Two ``cctiff`` passes (run async via :class:`~core.argyll_runner.ArgyllRunner`,
which is single-process, so they're chained) plus a NumPy compare:

  * **ref Lab**   — ``cctiff source.icm img`` → the image's own colorimetry.
  * **proof Lab** — ``cctiff source.icm printer.icm printer.icm img`` → the
    colour the printer would actually reproduce. Sandwiching the printer
    profile (PCS→device→PCS) simulates the print: relative-colorimetric
    intent *clips* out-of-gamut colours onto the gamut boundary, which is
    exactly what we detect.

Per pixel, ``ΔE(ref, proof)`` is ~0 where the colour is in gamut and large
where it was clipped. Pixels above a small threshold are out of gamut → we
build a highlight overlay and an out-of-gamut percentage. The soft-proof
preview itself is the proof Lab rendered to sRGB (an honest *approximate*
on-screen proof — see the dialog caption).

Everything ArgyllCMS touches must be **ICC v2** (the caller guards on
:func:`workflow.icc_info.is_v4`). cctiff is launched via QProcess, so it never
blocks the UI; large images are downsampled by the caller to bound runtime.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import numpy as np
from PIL import Image
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.i18n import tr
from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)


# --- Lab(D50) -> sRGB, vectorised (mirrors workflow.spot_read_io scalars) ---
_D50 = np.array([0.96422, 1.0, 0.82521], dtype=np.float64)
_BRADFORD_D50_TO_D65 = np.array([
    ( 0.9555766, -0.0230393,  0.0631636),
    (-0.0282895,  1.0099416,  0.0210077),
    ( 0.0122982, -0.0204830,  1.3299098),
])
_XYZ_TO_RGB = np.array([
    ( 3.2404542, -1.5371385, -0.4985314),
    (-0.9692660,  1.8760108,  0.0415560),
    ( 0.0556434, -0.2040259,  1.0572252),
])


def lab_d50_to_srgb_array(lab: np.ndarray) -> np.ndarray:
    """(...,3) D50 L*a*b* -> (...,3) uint8 sRGB, clamped to gamut."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def finv(t):
        t3 = t ** 3
        return np.where(t3 > 0.008856, t3, (t - 16.0 / 116.0) / 7.787)

    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=-1) * _D50
    xyz65 = xyz @ _BRADFORD_D50_TO_D65.T
    rgb_lin = xyz65 @ _XYZ_TO_RGB.T
    rgb_lin = np.clip(rgb_lin, 0.0, 1.0)
    srgb = np.where(rgb_lin <= 0.0031308,
                    12.92 * rgb_lin,
                    1.055 * np.power(rgb_lin, 1.0 / 2.4) - 0.055)
    return np.clip(srgb * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _decode_lab_tiff(path: Path) -> np.ndarray:
    """Read an 8-bit CIELab TIFF (cctiff output) into float L*a*b*."""
    arr = np.asarray(Image.open(path)).astype(np.float32)
    if arr.ndim == 2:
        arr = np.dstack([arr, np.zeros_like(arr), np.zeros_like(arr)])
    # 8-bit TIFF CIELab (cctiff "CIELab" photometric): L* is unsigned
    # 0..255 → 0..100; a*/b* are *signed* two's-complement int8 stored in a
    # uint8 byte (so byte 1 ≈ 0, byte 255 = −1), NOT an offset-128 value.
    # Decoding them as byte−128 is what gave the proof its blue cast.
    L = arr[..., 0] * 100.0 / 255.0
    a = np.where(arr[..., 1] < 128, arr[..., 1], arr[..., 1] - 256.0)
    b = np.where(arr[..., 2] < 128, arr[..., 2], arr[..., 2] - 256.0)
    return np.dstack([L, a, b])


# --- Source-space + image preparation ---------------------------------------

def argyll_ref_dir(settings) -> Path | None:
    """The Argyll ``ref`` directory (sibling of the configured ``bin``)."""
    bin_path = settings.get("argyll_bin_path", "/Applications/Argyll/bin")
    if not bin_path:
        return None
    cand = Path(bin_path).parent / "ref"
    return cand if cand.exists() else None


def resolve_source_profile(
    image_path: Path, source_choice: str, settings, work_dir: Path
) -> tuple[Path | None, str]:
    """Return ``(profile_path, note)`` for the image's source colour space.

    ``source_choice`` is "embedded", "srgb" or "adobergb". For "embedded" we
    pull the image's embedded ICC; if it's absent or ICC v4 (Argyll can't read
    v4) we fall back to sRGB and say so in ``note``.
    """
    ref = argyll_ref_dir(settings)
    named = {
        "srgb":     ("sRGB.icm",        tr("source assumed sRGB")),
        "adobergb": ("ClayRGB1998.icm", tr("source assumed Adobe RGB (1998)")),
        "p3":       ("DisplayP3.icm",   tr("source assumed Display P3")),
        "prophoto": ("ProPhoto.icm",    tr("source assumed ProPhoto RGB")),
    }
    srgb = (ref / "sRGB.icm") if ref else None

    if source_choice == "embedded":
        try:
            profile_bytes = Image.open(image_path).info.get("icc_profile")
        except OSError:
            profile_bytes = None
        if profile_bytes:
            emb = work_dir / "embedded_source.icc"
            emb.write_bytes(profile_bytes)
            from workflow.icc_info import is_v4
            if is_v4(emb):
                return srgb, tr("the image's embedded profile is ICC v4 (unreadable) — assumed sRGB")
            return emb, tr("using the image's embedded profile")
        return srgb, tr("no embedded profile — assumed sRGB")

    name, note = named.get(source_choice, named["srgb"])
    prof = (ref / name) if ref else None
    if prof is None or not prof.exists():
        return srgb, named["srgb"][1]   # fall back to sRGB if the ref profile is absent
    return prof, note


def prepare_input_tiff(image_path: Path, work_dir: Path, max_dim: int = 1600) -> Path:
    """Normalise any image to an RGB TIFF (cctiff input), downsampled so the
    longest side is at most ``max_dim`` to bound cctiff/tiffgamut runtime."""
    img = Image.open(image_path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
    out = work_dir / "input.tif"
    img.save(out, compression="tiff_lzw")
    return out


# --- Result + runner ---------------------------------------------------------

@dataclass
class SoftproofResult:
    proof_path: str          # soft-proof preview (sRGB, no highlight)
    highlight_path: str      # same + out-of-gamut pixels marked
    original_path: str       # the loaded image (for the soft-proof on/off toggle)
    oog_percent: float       # % of pixels out of gamut
    source_note: str         # how the source space was determined


_HIGHLIGHTS = {
    "gray":    (128, 128, 128),
    "magenta": (255, 0, 255),
    "cyan":    (0, 255, 255),
}


@dataclass
class SoftproofParams:
    image_path: Path
    printer_profile: Path
    source_choice: str = "srgb"     # embedded | srgb | adobergb | p3 | prophoto
    intent: str = "r"               # cctiff -i (r=relative recommended for OOG)
    threshold: float = 2.0          # ΔE above which a pixel is "out of gamut"
    highlight: str = "gray"
    display_profile: Path | None = None  # monitor profile for a truer on-screen proof


class SoftproofRunner(QObject):
    """Two chained cctiff passes + NumPy ΔE → soft-proof preview + OOG mask."""

    finished = pyqtSignal(object)   # SoftproofResult
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._settings = settings

    def run(self, params: SoftproofParams) -> None:
        if self._runner.is_running:
            self.error.emit(tr("Another process is already running."))
            return
        self._params = params
        self._work = Path(tempfile.mkdtemp(prefix="chromiq_softproof_"))

        try:
            # 2400 px keeps the preview crisp on HiDPI/Retina displays (the
            # preview pane can be ~1100 logical px → ~2200 device px at 2×);
            # TiffPreview renders at the display's device-pixel ratio.
            self._input_tif = prepare_input_tiff(params.image_path, self._work, max_dim=2400)
        except (OSError, ValueError) as exc:
            self.error.emit(tr("Could not read the image: {exc}").format(exc=exc))
            return

        self._source_profile, self._source_note = resolve_source_profile(
            params.image_path, params.source_choice, self._settings, self._work)
        if self._source_profile is None or not self._source_profile.exists():
            self.error.emit(tr(
                "Could not find a source colour-space profile (sRGB/Adobe RGB). "
                "Check that the ArgyllCMS 'ref' folder is present next to its 'bin'."))
            return

        # Pass 1: reference Lab (image's own colorimetry).
        self._ref_tif = self._work / "ref_lab.tif"
        self._run_cctiff(
            [str(self._source_profile), str(self._input_tif), str(self._ref_tif)],
            self._on_ref_done)

    # ------------------------------------------------------------------
    def _run_cctiff(self, tail_args: list[str], done: Callable[[int], None]) -> None:
        # -i before the relevant profile; we apply the same intent to every
        # profile in the chain for a consistent proof.
        args: list[str] = []
        intent = self._params.intent or "r"
        # tail_args = [prof, (prof, prof,) input, output]; prefix -i to each prof.
        rebuilt: list[str] = []
        for a in tail_args:
            if a.endswith(".icm") or a.endswith(".icc"):
                rebuilt += ["-i", intent, a]
            else:
                rebuilt.append(a)
        args = rebuilt
        log.info("cctiff: %s", " ".join(args))
        self._runner.run("cctiff", args, self._work, on_line=lambda _l: None,
                         on_finish=done)

    def _on_ref_done(self, code: int) -> None:
        if code != 0 or not self._ref_tif.exists():
            self.error.emit(tr("cctiff failed while reading the image (code {c}).").format(c=code))
            return
        # Pass 2 (deferred so QProcess is fully torn down): proof Lab.
        QTimer.singleShot(0, self._run_proof)

    def _run_proof(self) -> None:
        self._proof_tif = self._work / "proof_lab.tif"
        p = str(self._params.printer_profile)
        self._run_cctiff(
            [str(self._source_profile), p, p, str(self._input_tif), str(self._proof_tif)],
            self._on_proof_done)

    def _on_proof_done(self, code: int) -> None:
        if code != 0 or not self._proof_tif.exists():
            self.error.emit(tr("cctiff failed while simulating the print (code {c}). "
                               "Is the printer profile ICC v2?").format(c=code))
            return
        # Optional truer on-screen proof: render the proof Lab through the
        # monitor profile (Lab → display RGB) instead of the approximate sRGB.
        disp = self._params.display_profile
        if disp is not None and disp.exists():
            from workflow.icc_info import is_v4
            if is_v4(disp):
                self._display_note = tr(" (monitor profile is ICC v4 — used approximate sRGB)")
                QTimer.singleShot(0, lambda: self._finish_compute(None))
            else:
                self._display_tif = self._work / "proof_display.tif"
                self._display_note = tr(" (rendered for your monitor profile)")
                QTimer.singleShot(0, lambda: self._run_cctiff(
                    [str(disp), str(self._proof_tif), str(self._display_tif)],
                    self._on_display_done))
        else:
            self._display_note = ""
            self._finish_compute(None)

    def _on_display_done(self, code: int) -> None:
        rgb = self._display_tif if (code == 0 and self._display_tif.exists()) else None
        if rgb is None:
            self._display_note = ""  # fell back to approximate
        self._finish_compute(rgb)

    def _finish_compute(self, display_rgb: Path | None) -> None:
        try:
            result = self._compute(display_rgb)
        except (OSError, ValueError) as exc:
            self.error.emit(tr("Could not compute the soft-proof: {exc}").format(exc=exc))
            return
        self.finished.emit(result)

    # ------------------------------------------------------------------
    def _compute(self, display_rgb: Path | None = None) -> SoftproofResult:
        ref = _decode_lab_tiff(self._ref_tif)
        proof = _decode_lab_tiff(self._proof_tif)
        h = min(ref.shape[0], proof.shape[0])
        w = min(ref.shape[1], proof.shape[1])
        ref, proof = ref[:h, :w], proof[:h, :w]

        de = np.sqrt(((ref - proof) ** 2).sum(-1))
        mask = de > self._params.threshold
        oog_percent = 100.0 * float(mask.mean())

        # Soft-proof preview: rendered through the monitor profile if one was
        # given (truer proof), otherwise proof Lab → sRGB (approximate proof).
        if display_rgb is not None:
            disp_img = Image.open(display_rgb)
            if disp_img.mode != "RGB":
                disp_img = disp_img.convert("RGB")
            srgb = np.asarray(disp_img)[:proof.shape[0], :proof.shape[1]]
            if srgb.shape[:2] != mask.shape:
                mask = mask[:srgb.shape[0], :srgb.shape[1]]
        else:
            srgb = lab_d50_to_srgb_array(proof)
        proof_path = self._work / "proof_preview.tif"
        Image.fromarray(srgb, "RGB").save(proof_path, compression="tiff_lzw")

        # Highlight overlay.
        marked = srgb.copy()
        marked[mask] = np.array(_HIGHLIGHTS.get(self._params.highlight, (128, 128, 128)),
                                dtype=np.uint8)
        hl_path = self._work / "proof_highlight.tif"
        Image.fromarray(marked, "RGB").save(hl_path, compression="tiff_lzw")

        log.info("softproof: OOG=%.1f%% (ΔE>%.1f), preview=%s",
                 oog_percent, self._params.threshold, proof_path)
        return SoftproofResult(
            proof_path=str(proof_path),
            highlight_path=str(hl_path),
            original_path=str(self._input_tif),
            oog_percent=oog_percent,
            source_note=self._source_note + getattr(self, "_display_note", ""),
        )
