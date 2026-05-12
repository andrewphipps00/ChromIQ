"""Runs viewgam to combine two gamut files and compute coverage statistics."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

_INTERSECT_RE   = re.compile(r"Intersecting volume\s*=\s*([\d.]+)")


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

_COVERAGE_RE    = re.compile(r"'[^']+' volume = ([\d.]+) cubic units, intersect = ([\d.]+)%")


_COMPARE_CONTROLS_JS = """\
(function () {
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
  function applyTransparency() {
    var T = String(typeof window._chromiqCompareOpacity !== 'undefined'
                   ? window._chromiqCompareOpacity : 0.5);
    var g = document.getElementById('chromiq-compare');
    if (!g) return;
    g.querySelectorAll('material').forEach(function (m) {
      m.setAttribute('transparency', T);
    });
    g.querySelectorAll('appearance').forEach(function (app) {
      if (!app.querySelector('material')) {
        var m = document.createElement('material');
        m.setAttribute('transparency', T);
        app.appendChild(m);
      }
    });
  }
  function applySaturation() {
    var S = typeof window._chromiqCompareSat !== 'undefined'
            ? window._chromiqCompareSat : 1.0;
    var g = document.getElementById('chromiq-compare');
    if (!g) return;
    g.querySelectorAll('color').forEach(function (c) {
      if (!c._origColors) c._origColors = c.getAttribute('color') || '';
      var parts = c._origColors.trim().split(/\\s+/), out = [];
      for (var i = 0; i + 2 < parts.length; i += 3) {
        var hsl = rgb2hsl(+parts[i], +parts[i+1], +parts[i+2]);
        var rgb = hsl2rgb(hsl[0], Math.min(1, hsl[1] * S), hsl[2]);
        out.push(rgb[0].toFixed(5), rgb[1].toFixed(5), rgb[2].toFixed(5));
      }
      c.setAttribute('color', out.join(' '));
    });
  }
  function applyAll() { applyTransparency(); applySaturation(); }
  window._chromiqApplyCompare = applyAll;
  document.addEventListener('x3dom-initialized', function () {
    setTimeout(applyAll, 150);
  });
})();
"""


def _add_material_transparency(mo: re.Match, value: float = 0.5) -> str:
    """Add or update the transparency attribute on a matched <Material .../> tag."""
    tag = mo.group(0)
    if re.search(r"\btransparency\s*=", tag, re.IGNORECASE):
        return re.sub(
            r'\btransparency\s*=\s*["\'][^"\']*["\']',
            f'transparency="{value}"',
            tag,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s*(/?>)$", rf' transparency="{value}"\1', tag)


def _build_compare_overlay_html(
    primary_html: Path,
    compare_html: Path,
    output_path: Path,
) -> bool:
    """Merge compare gamut scene into primary's colorful HTML as a transparent overlay.

    Profile A keeps its natural per-vertex colors and is fully opaque.
    Profile B keeps its natural colors but is rendered semi-transparent so
    Profile A remains visible beneath it.
    Returns True on success.
    """
    try:
        primary_text = primary_html.read_text(encoding="utf-8")
        compare_text = compare_html.read_text(encoding="utf-8")

        m = re.search(r"<[Ss]cene[^>]*>(.*?)</[Ss]cene>", compare_text, re.DOTALL | re.IGNORECASE)
        if not m:
            return False
        compare_scene = m.group(1)

        # Add transparency to every Material element in the compare scene.
        # Shapes that omit Material (relying on per-vertex Color alone) are
        # handled at runtime by _COMPARE_TRANSPARENCY_JS.
        compare_scene = re.sub(
            r"<[Mm]aterial\b[^>]*/?>",
            _add_material_transparency,
            compare_scene,
        )

        overlay = f'\n<Group id="chromiq-compare">\n{compare_scene}\n</Group>\n'
        result = re.sub(r"</[Ss]cene>", overlay + "</Scene>", primary_text, count=1, flags=re.IGNORECASE)
        if result == primary_text:
            return False

        result = result.replace("</head>", f"<script>\n{_COMPARE_CONTROLS_JS}</script>\n</head>", 1)

        output_path.write_text(result, encoding="utf-8")
        return True
    except OSError:
        return False


def _patch_html(html_path: Path, themed: bool = True) -> None:
    """Inject dark background, expand X3D canvas, and optionally apply theme colors."""
    try:
        text = html_path.read_text(encoding="utf-8")
        style = (
            "<style>\n"
            "  html, body { background: #111111; margin: 0; padding: 0;"
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


@dataclass
class ViewgamResult:
    html_path: str = ""
    intersection_volume: float | None = None
    primary_coverage_pct: float | None = None   # % of primary gamut covered by compare
    compare_coverage_pct: float | None = None   # % of compare gamut covered by primary


class ViewgamRunner(QObject):
    """Wraps viewgam to produce combined X3DOM visualization + coverage statistics."""

    finished = pyqtSignal(object)   # ViewgamResult
    error    = pyqtSignal(str)

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner    = runner
        self._log_lines: list[str] = []

    def run(
        self,
        primary_gam:  Path,
        compare_gam:  Path,
        on_line:      Callable[[str], None],
        on_finish:    Callable[[int], None],
        themed:       bool = True,
        primary_html: Path | None = None,
        compare_html: Path | None = None,
    ) -> None:
        if self._runner.is_running:
            self.error.emit("Another process is already running.")
            return

        self._log_lines   = []
        self._themed      = themed
        self._primary_html = primary_html
        self._compare_html = compare_html
        work_dir    = Path(tempfile.mkdtemp(prefix="chromiq_viewgam_"))
        output_base = str(work_dir / "combined")

        args = [
            "-i",
            "-c", "r", str(primary_gam),
            "-c", "w", str(compare_gam),
            output_base,
        ]
        log.info("viewgam: %s  [cwd=%s]", " ".join(args), work_dir)

        def _accumulate(line: str) -> None:
            self._log_lines.append(line)
            on_line(line)

        def _done(code: int) -> None:
            self._on_done(code, work_dir, on_finish)

        self._runner.run("viewgam", args, work_dir, on_line=_accumulate, on_finish=_done)

    def _on_done(
        self,
        code: int,
        work_dir: Path,
        on_finish: Callable[[int], None],
    ) -> None:
        full_log = "\n".join(self._log_lines)
        result   = ViewgamResult()

        m_int = _INTERSECT_RE.search(full_log)
        if m_int:
            result.intersection_volume = float(m_int.group(1))

        coverages = _COVERAGE_RE.findall(full_log)
        if len(coverages) >= 2:
            result.primary_coverage_pct = float(coverages[0][1])
            result.compare_coverage_pct = float(coverages[1][1])

        # Prefer custom overlay HTML (Profile A keeps its natural colours) over
        # viewgam's flat-colour output.
        custom_html = work_dir / "custom_combined.x3d.html"
        if self._primary_html and self._compare_html and _build_compare_overlay_html(
            self._primary_html, self._compare_html, custom_html
        ):
            result.html_path = str(custom_html)
        else:
            viewgam_html = work_dir / "combined.x3d.html"
            if viewgam_html.exists():
                result.html_path = str(viewgam_html)
                _patch_html(viewgam_html, self._themed)

        if result.html_path or result.intersection_volume is not None:
            log.info(
                "viewgam: intersect=%.0f cc, A→B=%.1f%%, B→A=%.1f%%",
                result.intersection_volume or 0,
                result.primary_coverage_pct or 0,
                result.compare_coverage_pct or 0,
            )
            self.finished.emit(result)
        elif code != 0:
            self.error.emit(f"viewgam exited with code {code}.")
        else:
            self.error.emit("viewgam produced no usable output.")

        on_finish(code)
