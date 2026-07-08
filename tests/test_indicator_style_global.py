"""Strip-indicator styling is app-global (Settings → Chart Layout) and wins
over every recipe source — saved defaults, stored per-combo presets, live
changes — via the read-time overlay in TabChart._current_layout_recipe.

Regression for the beta.143-era report: the Settings styling controls had no
effect because a "Save as Defaults" snapshot (or a stored preset) restored its
own frozen styling verbatim, and nothing re-applied the Settings values."""
import pytest

pytest.importorskip("PyQt6")
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings, INDICATOR_STYLE_KEYS  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


STYLE = {
    "strip_indicator_font": "Baskerville",
    "strip_indicator_bold": True,
    "strip_indicator_rotation": 90,
    "strip_underline_mode": "segments",
    "strip_label_offset_mm": 2.5,
}


def _tab(tmp_path, **prefs):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    s.set("custom_output_path", str(tmp_path / "out"))
    s.set("use_chromiq_layout_engine", True)
    for k, v in prefs.items():
        s.set(k, v)
    tab = TabChart(ArgyllRunner(s), FileManager(s), s)
    tab._manual_btn.setChecked(True)
    return tab, s


def _style_of(recipe) -> dict:
    return {f: getattr(recipe, f) for f in INDICATOR_STYLE_KEYS}


def test_settings_style_overrides_saved_defaults_snapshot(qapp, tmp_path):
    """A recipe restored from "Save as Defaults" (manual_engine_recipe) gets the
    Settings styling — the snapshot's frozen styling is inert history."""
    import core.preset_store as ps
    from workflow.layout_engine.presets import default_recipe
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path / "p")):
        stale = default_recipe("i1", "A4")     # factory styling, e.g. rotation 0
        tab, s = _tab(tmp_path, manual_engine_recipe=stale.to_dict(), **STYLE)
        r = tab._current_layout_recipe()
    assert r.indicator_font == "Baskerville"
    assert r.indicator_bold is True
    assert r.indicator_rotation == 90
    assert r.underline_mode == "segments"
    assert r.strip_label_offset_mm == 2.5


def test_settings_style_overrides_stored_preset(qapp, tmp_path):
    """Loading a stored per-combo layout preset keeps its layout values but the
    strip-indicator styling still comes from Settings."""
    import core.preset_store as ps
    from core.preset_store import save_presets
    from dataclasses import replace
    from workflow.layout_engine.presets import PresetStore, default_recipe
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path / "p")):
        preset = replace(default_recipe("i1", "A4"),
                         dpi=600, indicator_font="Courier New",
                         indicator_rotation=180)
        store = PresetStore()
        store.set(preset)
        save_presets("chart_layout", store.as_named_dict())

        tab, s = _tab(tmp_path, **STYLE)
        tab._reset_manual_to_preset()
        r = tab._current_layout_recipe()
    assert r.dpi == 600                            # preset layout kept
    assert r.indicator_font == "Baskerville"       # styling from Settings
    assert r.indicator_rotation == 90


def test_style_change_applies_live_without_restart(qapp, tmp_path):
    """Changing the Settings styling mid-session shows up on the very next
    recipe read — no restart, no re-seeding."""
    import core.preset_store as ps
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path / "p")):
        tab, s = _tab(tmp_path)
        before = tab._current_layout_recipe()
        assert before.indicator_bold is False
        sig_before = tab._layout_signature()
        s.set("strip_indicator_bold", True)
        s.set("strip_indicator_font", "Baskerville")
        after = tab._current_layout_recipe()
        assert after.indicator_bold is True
        assert after.indicator_font == "Baskerville"
        # The auto-preview signature must move too, so the preview re-renders.
        assert tab._layout_signature() != sig_before


def test_build_params_carry_settings_style(qapp, tmp_path):
    """The recipe handed to the chart build (ChartParams.layout_recipe) carries
    the Settings styling, not the panel's inert carrier values."""
    import core.preset_store as ps
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path / "p")):
        tab, s = _tab(tmp_path, **STYLE)
        p = tab._collect_manual()
    assert p.layout_recipe is not None
    assert p.layout_recipe.indicator_font == "Baskerville"
    assert p.layout_recipe.underline_mode == "segments"


def test_styling_never_counts_as_preset_modification(qapp, tmp_path):
    """A stored preset whose frozen styling differs from the Settings values is
    NOT shown as "modified" — styling is global, not part of preset identity."""
    import core.preset_store as ps
    from core.preset_store import save_presets
    from dataclasses import replace
    from workflow.layout_engine.presets import PresetStore
    with mock.patch.object(ps, "presets_dir", lambda: Path(tmp_path / "p")):
        tab, s = _tab(tmp_path, **STYLE)
        # Store a preset identical to the live recipe except for its styling.
        preset = replace(tab._current_layout_recipe(),
                         indicator_font="Courier New", indicator_rotation=180)
        store = PresetStore()
        store.set(preset)
        save_presets("chart_layout", store.as_named_dict())
        status = tab._layout_preset_status()
    assert status is not None
    _summary, modified = status
    assert modified is False
