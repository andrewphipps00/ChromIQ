"""Scanner built-in presets (#100) — Knut's flatbed-scanner printer-profiling
charts as engine-built built-ins in their own "Scanner" preset group.

Each bundles a fixed .ti1 (3430p A4 / 3250p Letter) plus a full ChromIQ
layout-engine recipe; selecting one turns the engine on, seeds the layout
panel from the recipe, and builds from the bundled patch set.
"""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.resource_path import resource_path  # noqa: E402
from ui.tabs.tab_chart import (  # noqa: E402
    BUILTIN_PRESET_GROUPS, BUILTIN_PRESET_KEYS, BUILTIN_PRESET_LABELS,
    KNUT_PRESETS, KNUT_PRESETS_BY_KEY, TabChart,
)

_SCANNER = [p for p in KNUT_PRESETS if p.group == "Scanner"]
_A4_KEY = "__chromiq_knut_scanner_a4_3430p_1page_landscape__"
_LETTER_KEY = "__chromiq_knut_scanner_letter_3250p_1page_landscape__"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_tab(qapp, tmp_path):
    from core.argyll_runner import ArgyllRunner
    from core.file_manager import FileManager
    from core.settings import AppSettings
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "projects"))
    tab = TabChart(ArgyllRunner(s), FileManager(s), s)
    tab._switch_mode("manual")
    return tab, s


# ---------------------------------------------------------------------------
# Registry + assets
# ---------------------------------------------------------------------------

def test_scanner_family_registered():
    assert {p.key for p in _SCANNER} == {_A4_KEY, _LETTER_KEY}
    assert all(p.layout_recipe is not None for p in _SCANNER)
    assert all(p.key in BUILTIN_PRESET_KEYS for p in _SCANNER)
    assert all(p.combo_label in BUILTIN_PRESET_LABELS for p in _SCANNER)
    # Own "Scanner" group in the dropdown/overlay registry, holding exactly
    # the two charts.
    groups = dict(BUILTIN_PRESET_GROUPS)
    assert "Scanner" in groups
    assert [k for (_c, _o, k) in groups["Scanner"]] == [_A4_KEY, _LETTER_KEY]


def test_scanner_assets_match_declared_counts():
    for p in _SCANNER:
        ti1 = resource_path(p.ti1_asset)
        assert ti1.is_file(), f"missing {p.ti1_asset}"
        txt = ti1.read_text(encoding="latin-1", errors="ignore")
        m = re.search(r"NUMBER_OF_SETS\s+(\d+)", txt)
        assert m and int(m.group(1)) == p.patches
        # printtarg needs all three tables of a targen .ti1 (see the
        # i1Profiler-import lesson) — guard the bundled files' completeness.
        assert txt.count("NUMBER_OF_SETS") == 3


def test_scanner_recipes_reproduce_one_page():
    """The bundled recipe + .ti1 must lay out to exactly the advertised single
    page — a threshold/margin regression in the engine would silently spill."""
    from workflow.layout_engine import chart as le_chart
    from workflow.layout_engine.presets import LayoutRecipe
    import tempfile
    from pathlib import Path
    for p in _SCANNER:
        rec = LayoutRecipe.from_dict(p.layout_recipe)
        assert rec.instrument == "SS"
        assert rec.randomize is False       # keep Knut's printed layout
        with tempfile.TemporaryDirectory() as tmp:
            res = le_chart.build_chart(str(resource_path(p.ti1_asset)),
                                       Path(tmp) / "chart", project="t",
                                       **rec.build_kwargs())
            assert len(res.tiff_paths) == p.pages == 1


# ---------------------------------------------------------------------------
# Selection behaviour
# ---------------------------------------------------------------------------

def test_seed_scanner_preset_seeds_engine_panel(qapp, tmp_path):
    tab, s = _make_tab(qapp, tmp_path)
    s.set("use_chromiq_layout_engine", True)   # _on_preset_selected does this
    tab._seed_knut_preset(_A4_KEY)
    rec = tab._manual_layout_panel.get_recipe()
    assert rec.instrument == "SS"
    assert rec.paper == "A4R"
    assert rec.layout_mode == "area_first"
    assert rec.area_min_patch_mm == 4.0
    assert tab._manual_layout_panel.get_pages() == 1
    # Descriptive targen values reflect the bundled set.
    letter = KNUT_PRESETS_BY_KEY[_LETTER_KEY]
    tab._seed_knut_preset(_LETTER_KEY)
    assert tab._manual_layout_panel.get_recipe().paper == "LetterR"
    assert letter.patches == 3250


def test_selecting_scanner_preset_turns_engine_on(qapp, tmp_path, monkeypatch):
    """The real dropdown handler flips the engine to match the preset kind: ON
    for the Scanner family (and seeds the layout panel + builds from the
    bundled .ti1), OFF again for a printtarg-era built-in (#100)."""
    tab, s = _make_tab(qapp, tmp_path)
    built = []
    monkeypatch.setattr(tab, "_generate_from_ti1",
                        lambda p: built.append(p))     # stub the process edge
    idx = tab._preset_combo.findData(_A4_KEY)
    assert idx > 0
    tab._preset_combo.setCurrentIndex(idx)             # fires _on_preset_selected
    assert bool(s.get("use_chromiq_layout_engine", False)) is True
    assert tab._manual_layout_panel.get_recipe().instrument == "SS"
    assert built and built[-1].name == "chart.ti1"     # the bundled patch set
    # A printtarg-era built-in switches the engine back off.
    fls = tab._preset_combo.findData(
        "__chromiq_knut_fls_i1pro_a4_484p_1page_portrait__")
    assert fls > 0
    tab._preset_combo.setCurrentIndex(fls)
    assert bool(s.get("use_chromiq_layout_engine", False)) is False


def test_scanner_tooltip_mentions_scan_workflow(qapp, tmp_path):
    tab, _s = _make_tab(qapp, tmp_path)
    tip = tab._knut_tooltip(_A4_KEY)
    assert "scan" in tip.lower()
    assert "3430" in tip
    assert "printtarg" not in tip            # engine preset, no printtarg line
