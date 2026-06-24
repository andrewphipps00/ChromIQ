"""Layout recipe + preset store.

A :class:`LayoutRecipe` is the complete, serialisable set of layout settings
used to build a chart.  It is:

* the **source of truth** persisted with a chart (run ``meta.json``) so the
  Create Chart tab and the Edit/Create Chart editor populate identically when a
  chart moves between them, and so the strip/patch geometry can be regenerated
  for the Measure-tab highlighter;
* the unit a **preset** stores, keyed by *instrument × paper × mode-toggle*
  (i1 clip-border on/off, ColorMunki high-density on/off, SpectroScan hex/flat).

The :class:`PresetStore` is a JSON-backed dict of recipes with export / import /
restore-factory-defaults.  All Qt-free.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from . import permutation

SUPPORTED_INSTRUMENTS = ("i1", "p3", "CM", "41", "51", "SS")


@dataclass
class LayoutRecipe:
    instrument: str = "i1"
    paper: str = "A4"
    dpi: int = 300
    randomize: bool = True
    seed: int | None = None
    hflag: bool = False            # SpectroScan hex (n/a elsewhere)
    cm_density: int = 1            # ColorMunki rows: 1 normal, 2 rig, 3 extra-high
    spacer_on: bool = True
    pscale: float = 1.0
    sscale: float = 1.0
    border: float = 6.0
    clip_border: bool = True       # i1/p3 only — left paper clip border present
    nolimit: bool = False
    strip_pattern: str = permutation.DEFAULT_STRIP_PATTERN
    patch_pattern: str = permutation.DEFAULT_PATCH_PATTERN
    chart_text: str = ""           # custom on-sheet text (per chart, Phase 5)

    # ---- mode / preset identity ----------------------------------------
    CM_MODES = {1: "freehand", 2: "high", 3: "extrahigh"}

    def mode(self) -> str:
        if self.instrument in ("i1", "p3"):
            return "clip" if self.clip_border else "noclip"
        if self.instrument == "CM":
            return self.CM_MODES.get(self.cm_density, "freehand")
        if self.instrument == "SS":
            return "hex" if self.hflag else "flat"
        return "default"

    def preset_key(self) -> str:
        return f"{self.instrument}|{self.paper}|{self.mode()}"

    # ---- serialisation (meta.json round-trip) --------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutRecipe":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    # ---- mapping to the engine build kwargs ----------------------------
    def build_kwargs(self) -> dict:
        """Kwargs for :func:`workflow.layout_engine.chart.build_chart`."""
        return {
            "instrument": self.instrument,
            "paper": self.paper,
            "seed": self.seed,
            "randomize": self.randomize,
            "dpi": self.dpi,
            "hflag": self.hflag,
            "density": self.cm_density,
            "spacer_on": self.spacer_on,
            "pscale": self.pscale,
            "sscale": self.sscale,
            "border": self.border,
            "nolpcbord": (not self.clip_border) if self.instrument in ("i1", "p3") else False,
            "nolimit": self.nolimit,
            "strip_pattern": self.strip_pattern,
            "patch_pattern": self.patch_pattern,
        }


def default_recipe(instrument: str = "i1", paper: str = "A4", *, mode: str | None = None
                   ) -> LayoutRecipe:
    """A sensible default recipe for *instrument*/*paper* (and optional *mode*)."""
    r = LayoutRecipe(instrument=instrument, paper=paper)
    if mode is not None:
        if instrument in ("i1", "p3"):
            r.clip_border = (mode == "clip")
        elif instrument == "CM":
            r.cm_density = {"freehand": 1, "high": 2, "extrahigh": 3}.get(mode, 1)
        elif instrument == "SS":
            r.hflag = (mode == "hex")
    return r


class PresetStore:
    """A keyed collection of :class:`LayoutRecipe` presets (JSON-backed)."""

    VERSION = 1

    def __init__(self, presets: dict[str, LayoutRecipe] | None = None):
        self._presets: dict[str, LayoutRecipe] = dict(presets or {})

    # ---- access --------------------------------------------------------
    def get(self, instrument: str, paper: str, mode: str) -> LayoutRecipe:
        key = f"{instrument}|{paper}|{mode}"
        if key in self._presets:
            return replace(self._presets[key])      # a copy
        return default_recipe(instrument, paper, mode=mode)

    def set(self, recipe: LayoutRecipe) -> None:
        # Presets store layout, not the per-chart seed.
        self._presets[recipe.preset_key()] = replace(recipe, seed=None)

    def delete(self, instrument: str, paper: str, mode: str) -> bool:
        return self._presets.pop(f"{instrument}|{paper}|{mode}", None) is not None

    def keys(self) -> list[str]:
        return sorted(self._presets)

    # ---- (de)serialisation ---------------------------------------------
    def to_dict(self) -> dict:
        return {"version": self.VERSION,
                "presets": {k: v.to_dict() for k, v in self._presets.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "PresetStore":
        raw = d.get("presets", {}) if isinstance(d, dict) else {}
        return cls({k: LayoutRecipe.from_dict(v) for k, v in raw.items()})

    # ---- bridge to core.preset_store's {name: data} file layout -------
    def as_named_dict(self) -> dict[str, dict]:
        """``{preset_key: recipe_dict}`` — one entry per user-browsable file."""
        return {k: v.to_dict() for k, v in self._presets.items()}

    @classmethod
    def from_named_dict(cls, d: dict[str, dict]) -> "PresetStore":
        return cls({k: LayoutRecipe.from_dict(v) for k, v in d.items()})

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "PresetStore":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # export / import are just save / load to a user-chosen path
    export = save
    load_import = load

    @classmethod
    def factory_defaults(cls) -> "PresetStore":
        """The presets the app ships with — one default per instrument/mode."""
        presets: dict[str, LayoutRecipe] = {}
        for inst in SUPPORTED_INSTRUMENTS:
            modes = (["clip", "noclip"] if inst in ("i1", "p3")
                     else ["freehand", "high", "extrahigh"] if inst == "CM"
                     else ["flat", "hex"] if inst == "SS"
                     else ["default"])
            for m in modes:
                r = default_recipe(inst, "A4", mode=m)
                presets[r.preset_key()] = r
        return cls(presets)
