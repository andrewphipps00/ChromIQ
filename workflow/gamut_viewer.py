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

_THEMED_JS = """\
(function () {
  var ACCENTS = [
    {lo: 330, hi: 360, h: 345, s: 0.995},
    {lo:   0, hi:  30, h: 345, s: 0.995},
    {lo:  30, hi:  80, h:  39, s: 0.990},
    {lo:  80, hi: 165, h: 158, s: 0.600},
    {lo: 165, hi: 210, h: 190, s: 0.630},
    {lo: 210, hi: 330, h: 254, s: 1.000}
  ];
  function rgb2hsl(r, g, b) {
    var mx = Math.max(r,g,b), mn = Math.min(r,g,b), d = mx-mn;
    var l = (mx+mn)/2, h = 0, s = 0;
    if (d > 0) {
      s = l > 0.5 ? d/(2-mx-mn) : d/(mx+mn);
      if (mx===r)      h = ((g-b)/d + (g<b?6:0))/6;
      else if (mx===g) h = ((b-r)/d + 2)/6;
      else             h = ((r-g)/d + 4)/6;
    }
    return [h*360, s, l];
  }
  function hq(p, q, t) {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1/6) return p+(q-p)*6*t;
    if (t < 0.5) return q;
    if (t < 2/3) return p+(q-p)*(2/3-t)*6;
    return p;
  }
  function hsl2rgb(h, s, l) {
    h /= 360;
    if (s === 0) return [l, l, l];
    var q = l<0.5 ? l*(1+s) : l+s-l*s, p = 2*l-q;
    return [hq(p,q,h+1/3), hq(p,q,h), hq(p,q,h-1/3)];
  }
  function remapColors(str) {
    var v = str.trim().split(/\\s+/), out = [];
    for (var i = 0; i+2 < v.length; i += 3) {
      var hsl = rgb2hsl(+v[i], +v[i+1], +v[i+2]);
      var H = hsl[0], S = hsl[1], L = hsl[2], nh = 0, ns = 0;
      if (S >= 0.15) {
        for (var j = 0; j < ACCENTS.length; j++) {
          if (H >= ACCENTS[j].lo && H < ACCENTS[j].hi) {
            nh = ACCENTS[j].h; ns = ACCENTS[j].s; break;
          }
        }
      }
      var rgb = hsl2rgb(nh, ns, L);
      out.push(rgb[0].toFixed(5), rgb[1].toFixed(5), rgb[2].toFixed(5));
    }
    return out.join(' ');
  }
  function applyTheme() {
    document.querySelectorAll('color[color]').forEach(function(n) {
      n.setAttribute('color', remapColors(n.getAttribute('color')));
    });
    document.querySelectorAll('material[diffusecolor]').forEach(function(n) {
      n.setAttribute('diffusecolor', remapColors(n.getAttribute('diffusecolor')));
    });
  }
  document.addEventListener('DOMContentLoaded', applyTheme);
  document.addEventListener('x3dom-initialized', function() { setTimeout(applyTheme, 50); });
})();
"""


def _patch_html(html_path: Path, themed: bool = True, bg: str = "#111111") -> None:
    """Inject page background, expand X3D canvas, and optionally apply theme colors."""
    try:
        text = html_path.read_text(encoding="utf-8")
        style = (
            "<style>\n"
            f"  html, body {{ background: {bg}; margin: 0; padding: 0;"
            " overflow: hidden; }\n"
            "</style>\n"
        )
        inject = style
        if themed:
            inject += "<script>\n" + _THEMED_JS + "</script>\n"
        text = text.replace("</head>", inject + "</head>", 1)
        text = text.replace("height: 70%;", "height: 100vh;", 1)
        text = text.replace("height='70%'", "height='100vh'", 1)
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
