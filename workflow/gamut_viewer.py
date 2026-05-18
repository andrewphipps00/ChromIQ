"""Runs iccgamut to compute gamut volume and generate an X3DOM 3D visualization."""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

_VOLUME_RE = re.compile(r"Total volume of gamut is ([\d.]+)")

# JS for the X3D <Background> node only. The colour remap that used to
# live here ran at DOMContentLoaded but X3DOM had already parsed the X3D
# nodes via a body-script-triggered init and cached the GPU buffers, so
# late attribute mutations on <color> / <material> did not propagate. The
# remap now runs in Python at patch time (see _apply_chromiq_colors), so
# X3DOM reads the final colours straight from disk. The background needs
# to remain JS-driven because it reads the CSS body background at runtime
# and has to follow live Light/Dark switches.
_THEMED_JS = """\
(function () {
  function applyBackground() {
    var bgStr = window.getComputedStyle(document.body).backgroundColor;
    var m = bgStr.match(/rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/);
    if (!m) return;
    var sky = (+m[1]/255).toFixed(5) + ' '
            + (+m[2]/255).toFixed(5) + ' '
            + (+m[3]/255).toFixed(5);
    var scene = document.querySelector('scene');
    if (!scene) return;
    var bgNode = scene.querySelector('background');
    if (bgNode) {
      bgNode.setAttribute('skyColor', sky);
      bgNode.setAttribute('groundColor', sky);
    } else {
      // X3D Background has TWO hemispheres (sky + ground). Setting only
      // skyColor leaves groundColor at its default (pure black), giving
      // a half-black horizon split. Set both to the same colour for a
      // uniform background that matches the surrounding panel frame.
      bgNode = document.createElement('background');
      bgNode.setAttribute('skyColor', sky);
      bgNode.setAttribute('groundColor', sky);
      scene.insertBefore(bgNode, scene.firstChild);
    }
  }
  document.addEventListener('DOMContentLoaded', applyBackground);
  document.addEventListener('x3dom-initialized', function() { setTimeout(applyBackground, 50); });
})();
"""


# ChromIQ accent palette — same six hue bands the JS used to define, ported
# to Python so the remap runs at patch time (before X3DOM parses the file)
# instead of as a DOM mutation that X3DOM ignores after init.
# Tuple: (hue_lo, hue_hi, accent_hue, accent_saturation).
_THEME_ACCENTS = (
    (330, 360, 345, 0.995),
    (  0,  30, 345, 0.995),
    ( 30,  80,  39, 0.990),
    ( 80, 165, 158, 0.600),
    (165, 210, 190, 0.630),
    (210, 330, 254, 1.000),
)

# Cap applied to vertex lightness when remapping. The original JS kept L
# unchanged, so vertices at the gamut's white tip (L≈1.0) rendered pure
# white regardless of the accent hue. Capping keeps the bright top
# visibly tinted with the accent colour instead of blowing out to white.
# Low-L (dark tip) is left untouched — pure black there is fine.
# 0.92 balances: tip is still slightly off-white (visible tint) while
# overall surface brightness reads close to the combined-view
# transparency-lifted appearance for parity between alone and combined.
_L_CAP = 0.92


def _rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    l = (mx + mn) / 2
    h = 0.0
    s = 0.0
    if d > 0:
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h = ((g - b) / d + (6 if g < b else 0)) / 6
        elif mx == g:
            h = ((b - r) / d + 2) / 6
        else:
            h = ((r - g) / d + 4) / 6
    return h * 360, s, l


def _hue_helper(p: float, q: float, t: float) -> float:
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 0.5:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[float, float, float]:
    if s == 0:
        return l, l, l
    h_norm = h / 360
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return (
        _hue_helper(p, q, h_norm + 1 / 3),
        _hue_helper(p, q, h_norm),
        _hue_helper(p, q, h_norm - 1 / 3),
    )


def _remap_chromiq_color(rgb_str: str) -> str:
    """Remap a space-separated RGB triplet string to ChromIQ accents.

    Mirrors the JS remapColors() this module used to ship in _THEMED_JS,
    with a max-lightness cap to keep the brightest gamut vertices from
    rendering as pure white.
    """
    parts = rgb_str.split()
    out: list[str] = []
    for i in range(0, len(parts) - 2, 3):
        try:
            r = float(parts[i])
            g = float(parts[i + 1])
            b = float(parts[i + 2])
        except ValueError:
            continue
        h, s, l = _rgb_to_hsl(r, g, b)
        if l > _L_CAP:
            l = _L_CAP
        nh, ns = 0.0, 0.0
        if s >= 0.15:
            for lo, hi, ah, ays in _THEME_ACCENTS:
                if lo <= h < hi:
                    nh, ns = ah, ays
                    break
        nr, ng, nb = _hsl_to_rgb(nh, ns, l)
        out.append(f"{nr:.5f}")
        out.append(f"{ng:.5f}")
        out.append(f"{nb:.5f}")
    return " ".join(out)


# X3D allows both <Color color='r g b r g b ...'> per-vertex arrays and
# <Material diffuseColor='r g b'> per-shape solid colours. iccgamut emits
# both — Color for the gamut surface, Material for axes/labels.
_COLOR_TAG_RE = re.compile(
    r"(<\s*[Cc]olor\b[^>]*?\bcolor\s*=\s*['\"])([^'\"]*)(['\"])",
    re.DOTALL,
)
_MATERIAL_TAG_RE = re.compile(
    r"(<\s*[Mm]aterial\b[^>]*?\b[Dd]iffuse[Cc]olor\s*=\s*['\"])([^'\"]*)(['\"])",
    re.DOTALL,
)


def _apply_chromiq_colors(html_text: str) -> str:
    """Rewrite every X3D Color/Material colour attribute in-place."""
    html_text = _COLOR_TAG_RE.sub(
        lambda m: m.group(1) + _remap_chromiq_color(m.group(2)) + m.group(3),
        html_text,
    )
    html_text = _MATERIAL_TAG_RE.sub(
        lambda m: m.group(1) + _remap_chromiq_color(m.group(2)) + m.group(3),
        html_text,
    )
    return html_text


def _patch_html(html_path: Path, themed: bool = True, bg: str = "#111111") -> None:
    """Inject page background, expand X3D canvas, and optionally apply theme colors."""
    try:
        text = html_path.read_text(encoding="utf-8")
        style = (
            "<style>\n"
            f"  html, body {{ background: {bg}; margin: 0; padding: 0;"
            " overflow: hidden; }\n"
            "  x3d, x3d > canvas, x3d > div {"
            " border: 0 !important; outline: 0 !important;"
            " margin: 0 !important; padding: 0 !important; }\n"
            "  canvas { border: 0 !important; outline: 0 !important; }\n"
            "</style>\n"
        )
        # JS always runs — applyBackground needs to set the X3D scene
        # background in both themed and untinted modes. The colour remap
        # is themed-only and applies in Python below, before write.
        inject = style + "<script>\n" + _THEMED_JS + "</script>\n"
        text = text.replace("</head>", inject + "</head>", 1)
        text = text.replace("height: 70%;", "height: 100vh;", 1)
        text = text.replace("height='70%'", "height='100vh'", 1)
        if themed:
            text = _apply_chromiq_colors(text)
        html_path.write_text(text, encoding="utf-8")
    except OSError:
        pass


_CHROMIQ_BG_RE = re.compile(
    r"(html, body \{ background: )#[0-9A-Fa-f]{3,8}", re.IGNORECASE
)


def repatch_background(html_path: Path, bg: str) -> None:
    """Swap the already-injected page background to ``bg`` (no re-injection).

    Safe to call repeatedly on a previously-patched file; a no-op on files
    where ``_patch_html`` has not run.
    """
    try:
        text = html_path.read_text(encoding="utf-8")
        new_text, n = _CHROMIQ_BG_RE.subn(rf"\g<1>{bg}", text, count=1)
        if n:
            html_path.write_text(new_text, encoding="utf-8")
    except OSError:
        pass


@dataclass
class GamutViewerParams:
    icc_path: Path
    intent: str = "a"       # -i  a=absolute, r=relative, p=perceptual, s=saturation
    pcs: str = "l"          # -p  l=Lab, j=CIECAM02 Jab
    sres: float = 4.0       # -d  surface resolution
    axes: bool = True       # omit -n when True
    cusps: bool = False     # -k
    edges: bool = False     # -e
    function: str = "f"     # -f  f=forward, b=backward


class GamutViewer(QObject):
    """Wraps iccgamut to produce gamut volume + X3DOM HTML in a temp directory."""

    finished = pyqtSignal(float, str, str)   # (volume_cc, html_path, gam_path)
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner    = runner
        self._log_lines: list[str] = []
        self._work_dir: Path | None = None

    def run(
        self,
        params: GamutViewerParams,
        on_line:   Callable[[str], None],
        on_finish: Callable[[int], None],
        themed:    bool = True,
        bg:        str  = "#111111",
    ) -> None:
        if self._runner.is_running:
            self.error.emit("Another process is already running.")
            return

        self._log_lines = []
        self._params    = params
        self._themed    = themed
        self._bg        = bg

        # iccgamut writes output next to the input file — use a temp dir to
        # avoid polluting the profile folder and to get a known output path.
        work_dir = Path(tempfile.mkdtemp(prefix="chromiq_gamut_"))
        self._work_dir = work_dir
        icc_copy = work_dir / params.icc_path.name
        try:
            if params.icc_path.stat().st_size == 0:
                shutil.rmtree(work_dir, ignore_errors=True)
                self.error.emit(f"empty:{params.icc_path}")
                return
            # copyfile, not copy2: macOS system ColorSync profiles carry
            # SIP-protected BSD flags that copystat cannot replicate.
            shutil.copyfile(params.icc_path, icc_copy)
        except OSError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            self.error.emit(f"Cannot copy ICC file: {exc}")
            return

        args = self._build_args(params, icc_copy)
        log.info("iccgamut: %s  [cwd=%s]", " ".join(args), work_dir)

        def _accumulate(line: str) -> None:
            self._log_lines.append(line)
            on_line(line)

        def _done(code: int) -> None:
            self._on_done(code, icc_copy, on_finish)

        self._runner.run("iccgamut", args, work_dir, on_line=_accumulate, on_finish=_done)

    def _on_done(
        self,
        code: int,
        icc_copy: Path,
        on_finish: Callable[[int], None],
    ) -> None:
        full_log = "\n".join(self._log_lines)
        m = _VOLUME_RE.search(full_log)

        if m:
            volume = float(m.group(1))
            stem   = icc_copy.stem
            html   = icc_copy.parent / f"{stem}.x3d.html"
            gam    = icc_copy.parent / f"{stem}.gam"
            html_path = str(html) if html.exists() else ""
            gam_path  = str(gam)  if gam.exists()  else ""
            if not html_path:
                log.warning("iccgamut: HTML not found at %s", html)
            else:
                _patch_html(html, self._themed, self._bg)
            log.info("iccgamut: volume=%.1f cc, html=%s, gam=%s", volume, html_path, gam_path)
            self.finished.emit(volume, html_path, gam_path)
        elif code != 0:
            tool_err = next((l for l in reversed(self._log_lines) if "Error" in l or "error" in l), "")
            suffix = f"\niccgamut reported: {tool_err}" if tool_err else ""
            self.error.emit(f"tool_error:{code}{suffix}")
        else:
            self.error.emit("Could not parse gamut volume from iccgamut output — try running with -v flag.")


        on_finish(code)

    @staticmethod
    def _build_args(p: GamutViewerParams, icc_path: Path) -> list[str]:
        args: list[str] = ["-v", "-w"]  # verbose (prints volume) + X3DOM HTML
        if p.intent and p.intent != "a":
            args.append(f"-i{p.intent}")
        if p.pcs and p.pcs != "l":
            args.append(f"-p{p.pcs}")
        if p.sres != 4.0:
            args += ["-d", f"{p.sres:.1f}"]
        if not p.axes:
            args.append("-n")
        if p.cusps:
            args.append("-k")
        if p.edges:
            args.append("-e")
        if p.function and p.function != "f":
            args.append(f"-f{p.function}")
        args.append(str(icc_path.name))
        return args
