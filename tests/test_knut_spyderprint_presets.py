"""Knut's TC9.18 + Spyderprint-greys built-in presets.

Each of the 17 presets shares ONE bundled 1168-patch .ti1 and differs only in
its printtarg layout. Selecting one seeds the Manual printtarg panel and builds
the chart from the .ti1 (via _preset_ti1_path). These tests pin that the seeded
panel reproduces Knut's commands:

  i1Pro:      printtarg -P -ii1  -T200 -p<paper> -M8 -R<seed> -a<scale> -A0.6  (no -L)
  ColorMunki: printtarg -P -iCM -h -T200 -p<paper> -a<scale> -M6

ChromIQ emits -m<m> -M<m> together (functionally == Knut's lone -M) and keeps
the left clip border (no -L) on the i1 charts.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.resource_path import resource_path  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import (  # noqa: E402
    KNUT_PRESETS,
    KNUT_PRESETS_BY_KEY,
    KNUT_PRESET_KEYS,
    KNUT_TI1_ASSET,
    BUILTIN_PRESET_GROUPS,
    BUILTIN_PRESET_KEYS,
    BUILTIN_PRESET_LABELS,
    TabChart,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "chromiq_test.ini"), QSettings.Format.IniFormat)
    return s


def _make_tab(qapp, settings) -> TabChart:
    t = TabChart(ArgyllRunner(settings), FileManager(settings), settings)
    t._switch_mode("manual")
    return t


def _printtarg_args(tab) -> list[str]:
    """printtarg argv the current panel would run, minus the trailing stem."""
    params = tab._collect_manual()
    return tab._creator._build_printtarg_args(params)[:-1]


def _fmt_scale(v: float) -> str:
    return f"{v:.2f}" if round(v, 2) == round(v, 3) else f"{v:.3f}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_shape():
    # 17 TC9.18+Spyderprint presets (shared .ti1) + 4 "Wide-gamut" presets
    # (#53; each its own .ti1).
    assert len(KNUT_PRESETS) == 21
    assert len(KNUT_PRESET_KEYS) == 21  # all keys unique
    assert sum(1 for p in KNUT_PRESETS if p.slug.startswith("wg_")) == 4
    assert KNUT_PRESET_KEYS <= BUILTIN_PRESET_KEYS
    assert all(p.combo_label in BUILTIN_PRESET_LABELS for p in KNUT_PRESETS)
    # every preset is reachable from a dropdown/overlay group
    grouped = {k for _i, entries in BUILTIN_PRESET_GROUPS for (_c, _o, k) in entries}
    assert KNUT_PRESET_KEYS <= grouped


def test_ti1_asset_present():
    assert resource_path(KNUT_TI1_ASSET).is_file()


def test_keys_are_stable_sentinels():
    # The slug, not the display name, is the identity — guard the format so a
    # rename can't silently change a key and orphan saved selections.
    for p in KNUT_PRESETS:
        assert p.key == f"__chromiq_knut_{p.slug}__"


# ---------------------------------------------------------------------------
# Seeded printtarg command per preset
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(KNUT_PRESET_KEYS))
def test_seeded_command_matches_recipe(qapp, settings, key):
    p = KNUT_PRESETS_BY_KEY[key]
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset(key)
    args = _printtarg_args(tab)

    # Field-driven so it covers both the TC9.18 family and the Wide-gamut one.
    assert f"-i{p.instrument}" in args
    assert f"-p{p.paper}" in args
    if p.tiff_16bit:
        assert "-T200" in args and "-t200" not in args   # 16-bit raster at 200 dpi
    else:
        assert "-t200" in args and "-T200" not in args    # 8-bit raster at 200 dpi
    assert ("-P" in args) == p.no_strip_limit             # don't limit strip length
    assert ("-L" in args) == p.suppress_left_clip         # left clip border
    assert ("-h" in args) == p.double_density             # double density
    assert f"-M{p.margin}" in args
    assert f"-a{_fmt_scale(p.patch_scale)}" in args
    assert "-r" not in args           # charts are randomised
    # -m is emitted alongside -M only when the margin differs from the default 6.
    if p.margin != 6:
        assert f"-m{p.margin}" in args
    else:
        assert f"-m{p.margin}" not in args
    joined = " ".join(args)
    if p.spacer_scale is not None:
        assert f"-A {p.spacer_scale:.2f}" in joined
    else:
        assert not any(a.startswith("-A") for a in args)
    if p.seed is not None:
        assert f"-R {p.seed}" in joined
    else:
        assert not any(a.startswith("-R") for a in args)


def test_widegamut_preset_uses_its_own_ti1_and_count(qapp, settings):
    # #58: a Wide-gamut preset bundles its OWN .ti1 (not the shared TC9.18 set),
    # and its reuse info box reports that preset's patch count, not 1168.
    key = "__chromiq_knut_wg_cm_a3_2016_4p__"
    p = KNUT_PRESETS_BY_KEY[key]
    assert p.ti1_asset != KNUT_TI1_ASSET
    assert p.patches == 2016
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset(key)
    tab._knut_active = True
    tab._knut_active_key = key
    tab._knut_targen_sig = tab._targen_signature()
    tab._refresh_manual_command_preview()
    info = tab._manual_info_lbl.text()
    assert "2016" in info and "1168" not in info
    assert "Wide-gamut" in tab._knut_tooltip(key)


def test_three_decimal_scale_survives(qapp, settings):
    # 0.929 must not be rounded to 0.93 (the whole reason for decimals: 3).
    tab = _make_tab(qapp, settings)
    key = "__chromiq_knut_i1_a4_2p__"
    tab._seed_knut_preset(key)
    assert "-a0.929" in _printtarg_args(tab)


def test_switching_presets_clears_stale_spacer_and_seed(qapp, settings):
    # i1 → CM must drop the i1-only -A / -R rows.
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset("__chromiq_knut_i1_a3_land_1p__")   # sets -A0.6 -R161
    tab._seed_knut_preset("__chromiq_knut_cm_a4_5p__")        # CM has neither
    args = _printtarg_args(tab)
    assert not any(a.startswith("-A") for a in args)
    assert not any(a.startswith("-R") for a in args)


def test_targen_signature_ignores_printtarg_changes(qapp, settings):
    # While a preset is active, changing only printtarg settings must keep the
    # bundled .ti1 as the source (signature unchanged); a targen change opts into
    # a fresh targen chart (signature differs).
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset("__chromiq_knut_i1_a4_2p__")
    sig0 = tab._targen_signature()
    tab._set_manual_value("printtarg", "-a", 1.05)      # printtarg-only edit
    assert tab._targen_signature() == sig0
    tab._set_manual_value("targen", "-f", 500)          # targen edit
    assert tab._targen_signature() != sig0


def test_info_box_announces_ti1_reuse(qapp, settings):
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset("__chromiq_knut_cm_a4_5p__")
    tab._knut_active = True
    tab._knut_targen_sig = tab._targen_signature()
    tab._refresh_manual_command_preview()
    txt = tab._manual_info_lbl.text()
    assert "targen skipped" in txt and "1168" in txt
    # A targen change flips the note back to the normal targen+printtarg view.
    tab._set_manual_value("targen", "-f", 500)
    tab._refresh_manual_command_preview()
    assert "targen skipped" not in tab._manual_info_lbl.text()


def test_leaving_preset_reverts_overrides(qapp, settings):
    # Leaving a preset (for Default / a user preset) must not leak its forced
    # printtarg flags into the next chart — _reset_knut_overrides handles it.
    tab = _make_tab(qapp, settings)
    tab._seed_knut_preset("__chromiq_knut_i1_a3_land_1p__")   # -A0.6 -R161 -P -a0.98 -m8
    tab._knut_active = True
    tab._reset_knut_overrides()
    args = _printtarg_args(tab)
    assert "-P" not in args
    assert not any(a.startswith("-A") for a in args)
    assert not any(a.startswith("-R") for a in args)
    assert "-a0.98" not in args        # back to the default scale (1.0 → no -a)
