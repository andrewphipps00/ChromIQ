"""3D RGB-cube visualisation of a chart's patch set (Tools → layout editor).

Turns the editor's flat list of 0..100 RGB device tuples into a self-contained
Plotly ``Scatter3d`` page: every patch is a dot placed at its own (R, G, B)
coordinate and painted in its own colour, inside a 0..100 wireframe cube with a
neutral (black→white) axis drawn through it. Rotating / zooming the cube makes
gamut coverage and patch-density clumping obvious at a glance — the thing the
swatch grid and 2D page preview can't show.

Qt-free and unit-testable: the popup in :mod:`ui.dialogs.ti2_relayout_dialog`
just writes the returned HTML to a temp file and loads it in a QWebEngineView.
The heavy ``plotly-gl3d.min.js`` bundle is referenced by ``file://`` URL (not
inlined) so the page stays small enough for any loader and ships from
``assets/`` with the rest of the app.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np


@dataclass
class CubeStats:
    """Coverage / density summary shown alongside the 3D cube."""
    count: int
    nn_mean: float          # mean nearest-neighbour RGB distance (0..100 scale)
    nn_min: float           # closest pair distance — small ⇒ near-duplicates
    occupancy_pct: float    # % of a 10×10×10 cube grid that holds ≥1 patch

    def summary(self) -> str:
        from core.i18n import tr
        if self.count == 0:
            return tr("No patches to analyse.")
        return tr("{count} patches · gamut fill {fill:.0f}% "
                  "· spacing mean {mean:.1f}, closest {closest:.1f} "
                  "(0–100 RGB)").format(count=self.count,
                                        fill=self.occupancy_pct,
                                        mean=self.nn_mean,
                                        closest=self.nn_min)


def _as_array(program: list[tuple[float, ...]]) -> np.ndarray:
    """Coerce the program to an ``[N, 3]`` float array of 0..100 RGB."""
    if not program:
        return np.zeros((0, 3), dtype=float)
    arr = np.asarray([p[:3] for p in program], dtype=float)
    return arr.reshape(-1, 3)


def cube_stats(program: list[tuple[float, ...]]) -> CubeStats:
    """Coverage / density metrics for a patch program.

    * ``occupancy_pct`` — patches binned into a 10×10×10 grid over the 0..100
      cube; the fraction of occupied cells is a cheap, monotone proxy for "how
      much of the colour space is sampled" that doesn't need a convex hull.
    * ``nn_mean`` / ``nn_min`` — nearest-neighbour RGB distances, computed in
      blocks so a few-thousand-patch chart stays within a sane memory budget.
      A tiny ``nn_min`` flags near-duplicate patches; a large ``nn_mean`` with
      low occupancy flags sparse, uneven coverage.
    """
    arr = _as_array(program)
    n = len(arr)
    if n == 0:
        return CubeStats(0, 0.0, 0.0, 0.0)

    # Occupancy over a 10×10×10 grid (clamp to keep 100.0 in the last cell).
    cells = np.clip((arr / 10.0).astype(int), 0, 9)
    keys = cells[:, 0] * 100 + cells[:, 1] * 10 + cells[:, 2]
    occupancy = len(np.unique(keys)) / 1000.0 * 100.0

    if n == 1:
        return CubeStats(1, 0.0, 0.0, occupancy)

    # Block-wise nearest-neighbour distances (avoid an N×N matrix all at once).
    nn = np.empty(n, dtype=float)
    block = 256
    for i in range(0, n, block):
        chunk = arr[i:i + block]                                  # [b, 3]
        d = np.linalg.norm(chunk[:, None, :] - arr[None, :, :], axis=2)  # [b, N]
        # Mask each point's distance to itself.
        for j in range(len(chunk)):
            d[j, i + j] = np.inf
        nn[i:i + block] = d.min(axis=1)
    return CubeStats(n, float(nn.mean()), float(nn.min()), occupancy)


# 8 corners of the 0..100 cube, then the 12 edges as corner-index pairs.
_CUBE_CORNERS = [(x, y, z) for x in (0, 100) for y in (0, 100) for z in (0, 100)]
_CUBE_EDGES = [
    (a, b) for a in range(8) for b in range(a + 1, 8)
    # an edge connects corners differing in exactly one axis
    if sum(_CUBE_CORNERS[a][k] != _CUBE_CORNERS[b][k] for k in range(3)) == 1
]


def _edge_lines() -> tuple[list, list, list]:
    """Build x/y/z arrays tracing all 12 cube edges, ``None``-separated so a
    single Plotly line trace draws the whole wireframe."""
    xs: list = []
    ys: list = []
    zs: list = []
    for a, b in _CUBE_EDGES:
        ca, cb = _CUBE_CORNERS[a], _CUBE_CORNERS[b]
        xs += [ca[0], cb[0], None]
        ys += [ca[1], cb[1], None]
        zs += [ca[2], cb[2], None]
    return xs, ys, zs


def _points_trace(arr: np.ndarray, *, name: str, size: float, opacity: float,
                  hover_prefix: str) -> dict:
    """A ``scatter3d`` marker trace placing each patch at its (R,G,B) and
    painting it its own colour. ``size`` / ``opacity`` let the caller dim the
    already-present patches so the freshly-generated ones read on top."""
    rgb255 = np.clip(np.round(arr / 100.0 * 255.0), 0, 255).astype(int)
    colors = [f"rgb({r},{g},{b})" for r, g, b in rgb255.tolist()]
    hover = [f"{hover_prefix} · RGB {r} {g} {b}" for r, g, b in rgb255.tolist()]
    return {
        "type": "scatter3d", "mode": "markers", "name": name,
        "x": arr[:, 0].tolist(), "y": arr[:, 1].tolist(),
        "z": arr[:, 2].tolist(),
        "marker": {"size": size, "color": colors,
                   "line": {"width": 0}, "opacity": opacity},
        "text": hover, "hoverinfo": "text", "showlegend": False,
    }


def cube_traces(
    new_program: list[tuple[float, ...]],
    existing_program: list[tuple[float, ...]] | None = None,
    *,
    fg: str = "#cccccc",
    grid: str = "#444444",
) -> list[dict]:
    """The Plotly ``data`` list: wireframe + neutral axis + patch markers.

    When ``existing_program`` is given (the editor's Add flow), its patches are
    drawn smaller and semi-transparent as a separate trace and the freshly
    generated ``new_program`` patches are drawn full-size on top — one merged
    cloud in which the about-to-be-added patches still stand out.
    """
    ex_arr = _as_array(existing_program or [])
    new_arr = _as_array(new_program)
    ex, ey, ez = _edge_lines()
    cube = {
        "type": "scatter3d", "mode": "lines", "name": "cube",
        "x": ex, "y": ey, "z": ez,
        "line": {"color": grid, "width": 2}, "hoverinfo": "skip",
        "showlegend": False,
    }
    neutral = {
        "type": "scatter3d", "mode": "lines", "name": "neutral axis",
        "x": [0, 100], "y": [0, 100], "z": [0, 100],
        "line": {"color": fg, "width": 2, "dash": "dot"}, "hoverinfo": "skip",
        "showlegend": False,
    }
    data = [cube, neutral]
    has_existing = len(ex_arr) > 0
    if has_existing:
        data.append(_points_trace(ex_arr, name="existing", size=2.4,
                                  opacity=0.35, hover_prefix="existing"))
    data.append(_points_trace(
        new_arr, name="new" if has_existing else "patches",
        size=3.6, opacity=1.0,
        hover_prefix="new" if has_existing else "patch"))
    return data


def combined_summary(
    new_program: list[tuple[float, ...]],
    existing_program: list[tuple[float, ...]] | None = None,
) -> str:
    """Footer text — a plain coverage line, or an ``N existing + M new`` split
    over the combined set when there are already-present patches."""
    existing = list(existing_program or [])
    if not existing:
        return cube_stats(new_program).summary()
    from core.i18n import tr
    combined = cube_stats(existing + list(new_program))
    return tr("{ex} existing + {new} new · gamut fill {fill:.0f}% "
              "(combined)").format(ex=len(existing), new=len(new_program),
                                   fill=combined.occupancy_pct)


def cube_payload(
    new_program: list[tuple[float, ...]],
    existing_program: list[tuple[float, ...]] | None = None,
    *,
    fg: str = "#cccccc",
    grid: str = "#444444",
) -> dict:
    """JSON-serialisable ``{"data", "stats"}`` for a live in-place update.

    The live preview window feeds this (via ``json.dumps``) to the page's
    ``cqUpdateCube`` function, which calls ``Plotly.react`` — no page reload,
    so the cube redraws smoothly while generator settings change."""
    return {
        "data": cube_traces(new_program, existing_program, fg=fg, grid=grid),
        "stats": combined_summary(new_program, existing_program),
    }


def _cube_layout(bg: str, fg: str, grid: str) -> dict:
    def _axis(title: str) -> dict:
        return {"title": title, "range": [0, 100], "color": fg,
                "gridcolor": grid, "zerolinecolor": grid,
                "backgroundcolor": bg, "showbackground": True}
    return {
        "paper_bgcolor": bg, "plot_bgcolor": bg,
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "showlegend": False,
        "scene": {
            "xaxis": _axis("R"), "yaxis": _axis("G"), "zaxis": _axis("B"),
            "aspectmode": "cube",
            "camera": {"eye": {"x": 1.5, "y": 1.5, "z": 1.3}},
        },
    }


# Reusable JS: middle-button (wheel-click) drag → pan a Plotly 3D plot by id
# (#64), and keep two plots' cameras in sync (#66). Emitted into both the
# single and dual cube pages.
_CUBE_JS_HELPERS = """
function cqInstallPan(plotId) {
  var gd = document.getElementById(plotId);
  var panning = false, lx = 0, ly = 0;
  var sub = function(a,b){return {x:a.x-b.x,y:a.y-b.y,z:a.z-b.z};};
  var add = function(a,b){return {x:a.x+b.x,y:a.y+b.y,z:a.z+b.z};};
  var scl = function(a,s){return {x:a.x*s,y:a.y*s,z:a.z*s};};
  var cross = function(a,b){return {x:a.y*b.z-a.z*b.y,y:a.z*b.x-a.x*b.z,z:a.x*b.y-a.y*b.x};};
  var len = function(a){return Math.hypot(a.x,a.y,a.z)||1;};
  var nrm = function(a){return scl(a,1/len(a));};
  function cam(){
    var c=(gd.layout&&gd.layout.scene&&gd.layout.scene.camera)||{eye:{x:1.5,y:1.5,z:1.3}};
    return {eye:c.eye,center:c.center||{x:0,y:0,z:0},up:c.up||{x:0,y:0,z:1}};
  }
  gd.addEventListener("mousedown", function(e){
    if(e.button===1){panning=true;lx=e.clientX;ly=e.clientY;e.preventDefault();}
  }, true);
  window.addEventListener("mousemove", function(e){
    if(!panning)return;
    var dx=e.clientX-lx, dy=e.clientY-ly; lx=e.clientX; ly=e.clientY;
    var c=cam(), fwd=nrm(sub(c.center,c.eye)), right=nrm(cross(fwd,c.up)), tup=nrm(cross(right,fwd));
    var rect=gd.getBoundingClientRect(), k=len(sub(c.eye,c.center))/Math.max(1,rect.height);
    var t=add(scl(right,-dx*k),scl(tup,dy*k));
    Plotly.relayout(gd,{"scene.camera":{eye:add(c.eye,t),center:add(c.center,t),up:c.up}});
  }, true);
  window.addEventListener("mouseup", function(e){ if(e.button===1) panning=false; }, true);
}
function cqLinkCameras(idA, idB) {
  // Keep both cubes' cameras locked together CONTINUOUSLY while either is being
  // rotated / zoomed / panned. The live camera during a drag lives on the
  // scene's internal object (layout.scene.camera only updates on release), and
  // 'plotly_relayouting' (not 'plotly_relayout') is the event that fires
  // throughout the gesture — using those two is what makes it move in real time.
  var a = document.getElementById(idA), b = document.getElementById(idB);
  // Only the cube the user is actively driving pushes its camera to the other —
  // the passive one ignores its own (programmatic) updates, so the two never
  // feed back into each other.
  var active = null, wheelTimer = null;
  function liveCam(gd) {
    try {
      var s = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
      if (s && s.getCamera) return s.getCamera();
    } catch (e) {}
    return gd.layout && gd.layout.scene && gd.layout.scene.camera;
  }
  function arm(gd) {                          // wheel has no mouseup — auto-disarm
    active = gd;
    if (wheelTimer) clearTimeout(wheelTimer);
    wheelTimer = setTimeout(function() { active = null; }, 300);
  }
  a.addEventListener("mousedown", function() { active = a; }, true);
  b.addEventListener("mousedown", function() { active = b; }, true);
  window.addEventListener("mouseup", function() { active = null; }, true);
  a.addEventListener("wheel", function() { arm(a); }, true);
  b.addEventListener("wheel", function() { arm(b); }, true);
  function link(src, dstId) {
    function sync() {
      if (active !== src) return;            // only the driven cube leads
      var c = liveCam(src);
      if (c) Plotly.relayout(dstId, {"scene.camera": c});
    }
    src.on("plotly_relayouting", sync);      // continuous, during the gesture
    src.on("plotly_relayout", sync);         // and the final committed position
  }
  link(a, idB); link(b, idA);
}
"""


def build_cube_html(
    program: list[tuple[float, ...]],
    plotly_js_url: str,
    *,
    existing_program: list[tuple[float, ...]] | None = None,
    bg: str = "#111111",
    fg: str = "#cccccc",
    grid: str = "#444444",
) -> str:
    """Return a self-contained HTML page plotting ``program`` as a 3D RGB cube.

    ``plotly_js_url`` is the ``file://`` (or http) URL of the bundled
    ``plotly-gl3d.min.js``. Markers sit at each patch's (R, G, B) on the 0..100
    axes and are painted in that patch's own colour; a wireframe cube and a
    black→white neutral axis are drawn for reference. ``bg`` / ``fg`` / ``grid``
    theme the page so it matches the host dialog's light / dark appearance.

    ``existing_program`` (the editor's Add flow) is drawn dimmed underneath the
    freshly generated ``program``. The page also exposes ``window.cqUpdateCube``
    so a host can push a new :func:`cube_payload` via ``Plotly.react`` and have
    the cube redraw in place — no reload — as generator settings change.
    """
    data = cube_traces(program, existing_program, fg=fg, grid=grid)
    stats_text = combined_summary(program, existing_program)
    layout = _cube_layout(bg, fg, grid)
    config = {"displaylogo": False, "responsive": True, "scrollZoom": True}

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="{plotly_js_url}"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; width: 100%;
                overflow: hidden; background: {bg};
                font-family: Menlo, Consolas, "Courier New", monospace; }}
  #plot {{ width: 100%; height: calc(100% - 26px); overflow: hidden; }}
  #stats {{ height: 26px; line-height: 26px; color: {fg}; font-size: 11px;
            padding: 0 10px; background: {bg};
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
</style></head>
<body>
  <div id="plot"></div>
  <div id="stats">{stats_text}</div>
  <script>
    {_CUBE_JS_HELPERS}
    var CQ_LAYOUT = {json.dumps(layout)};
    var CQ_CONFIG = {json.dumps(config)};
    Plotly.newPlot("plot", {json.dumps(data)}, CQ_LAYOUT, CQ_CONFIG);
    cqInstallPan("plot");
    // Live in-place update: the host pushes a {{data, stats}} payload and the
    // cube redraws via Plotly.react without reloading the page or the WebGL
    // context (see workflow.patch_cube.cube_payload).
    window.cqUpdateCube = function(payload) {{
      Plotly.react("plot", payload.data, CQ_LAYOUT, CQ_CONFIG);
      var s = document.getElementById("stats");
      if (s) {{ s.textContent = payload.stats; }}
    }};
  </script>
</body></html>
"""


def build_dual_cube_html(
    program_a: list[tuple[float, ...]],
    label_a: str,
    program_b: list[tuple[float, ...]],
    label_b: str,
    plotly_js_url: str,
    *,
    existing_a: list[tuple[float, ...]] | None = None,
    bg: str = "#111111",
    fg: str = "#cccccc",
    grid: str = "#444444",
) -> str:
    """Two side-by-side 3D RGB cubes (current chart vs. a comparison preset),
    with their cameras locked in sync — rotate/zoom/pan one and the other
    follows, so they can be compared from any angle (#66). Each cube carries its
    name above it and its own stats below."""
    import html as _html
    data_a = cube_traces(program_a, existing_a, fg=fg, grid=grid)
    data_b = cube_traces(program_b, None, fg=fg, grid=grid)
    layout = _cube_layout(bg, fg, grid)
    config = {"displaylogo": False, "responsive": True, "scrollZoom": True}
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="{plotly_js_url}"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; width: 100%;
                overflow: hidden; background: {bg};
                font-family: Menlo, Consolas, "Courier New", monospace; }}
  #row {{ display: flex; width: 100%; height: 100%; }}
  .col {{ flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; }}
  .col + .col {{ border-left: 1px solid {grid}; }}
  .title {{ height: 24px; line-height: 24px; color: {fg}; font-size: 12px;
            font-weight: bold; text-align: center; background: {bg};
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            padding: 0 8px; }}
  .cube {{ flex: 1 1 auto; min-height: 0; }}
  .stats {{ height: 24px; line-height: 24px; color: {fg}; font-size: 11px;
            padding: 0 10px; background: {bg};
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
</style></head>
<body>
  <div id="row">
    <div class="col">
      <div class="title">{_html.escape(label_a)}</div>
      <div id="plotA" class="cube"></div>
      <div class="stats">{combined_summary(program_a, existing_a)}</div>
    </div>
    <div class="col">
      <div class="title">{_html.escape(label_b)}</div>
      <div id="plotB" class="cube"></div>
      <div class="stats">{combined_summary(program_b, None)}</div>
    </div>
  </div>
  <script>
    {_CUBE_JS_HELPERS}
    var CQ_LAYOUT = {json.dumps(layout)};
    var CQ_CONFIG = {json.dumps(config)};
    Promise.all([
      Plotly.newPlot("plotA", {json.dumps(data_a)}, CQ_LAYOUT, CQ_CONFIG),
      Plotly.newPlot("plotB", {json.dumps(data_b)}, CQ_LAYOUT, CQ_CONFIG)
    ]).then(function() {{
      cqInstallPan("plotA"); cqInstallPan("plotB");
      cqLinkCameras("plotA", "plotB");   // rotate/zoom/pan stay in sync
    }});
  </script>
</body></html>
"""
