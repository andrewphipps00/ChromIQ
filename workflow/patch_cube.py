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
        if self.count == 0:
            return "No patches to analyse."
        return (f"{self.count} patches · gamut fill {self.occupancy_pct:.0f}% "
                f"· spacing mean {self.nn_mean:.1f}, closest {self.nn_min:.1f} "
                "(0–100 RGB)")


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


def build_cube_html(
    program: list[tuple[float, ...]],
    plotly_js_url: str,
    *,
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
    """
    arr = _as_array(program)
    # 0..100 → 0..255 for CSS rgb() marker colours + readable hover text.
    rgb255 = np.clip(np.round(arr / 100.0 * 255.0), 0, 255).astype(int)
    colors = [f"rgb({r},{g},{b})" for r, g, b in rgb255.tolist()]
    hover = [f"#{i + 1} · RGB {r} {g} {b}"
             for i, (r, g, b) in enumerate(rgb255.tolist())]
    stats = cube_stats(program)

    ex, ey, ez = _edge_lines()
    points = {
        "type": "scatter3d", "mode": "markers", "name": "patches",
        "x": arr[:, 0].tolist(), "y": arr[:, 1].tolist(), "z": arr[:, 2].tolist(),
        "marker": {"size": 3.5, "color": colors,
                   "line": {"width": 0}, "opacity": 1.0},
        "text": hover, "hoverinfo": "text",
    }
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
    data = [cube, neutral, points]

    def _axis(title: str) -> dict:
        return {"title": title, "range": [0, 100], "color": fg,
                "gridcolor": grid, "zerolinecolor": grid,
                "backgroundcolor": bg, "showbackground": True}

    layout = {
        "paper_bgcolor": bg, "plot_bgcolor": bg,
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "showlegend": False,
        "scene": {
            "xaxis": _axis("R"), "yaxis": _axis("G"), "zaxis": _axis("B"),
            "aspectmode": "cube",
            "camera": {"eye": {"x": 1.5, "y": 1.5, "z": 1.3}},
        },
    }
    config = {"displaylogo": False, "responsive": True,
              "scrollZoom": True}

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="{plotly_js_url}"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; background: {bg};
                font-family: Menlo, Consolas, "Courier New", monospace; }}
  #plot {{ width: 100%; height: calc(100% - 26px); }}
  #stats {{ height: 26px; line-height: 26px; color: {fg}; font-size: 11px;
            padding: 0 10px; background: {bg};
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
</style></head>
<body>
  <div id="plot"></div>
  <div id="stats">{stats.summary()}</div>
  <script>
    Plotly.newPlot("plot", {json.dumps(data)}, {json.dumps(layout)},
                   {json.dumps(config)});
  </script>
</body></html>
"""
