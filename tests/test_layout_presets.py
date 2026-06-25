"""Tests for LayoutRecipe + PresetStore (round-trip persistence, presets)."""
from workflow.layout_engine.presets import (
    LayoutRecipe, PresetStore, default_recipe,
)


def test_mode_and_preset_key():
    assert LayoutRecipe(instrument="i1", clip_border=True).mode() == "clip"
    assert LayoutRecipe(instrument="i1", clip_border=False).mode() == "noclip"
    # ColorMunki: normal + two high-density levels
    assert LayoutRecipe(instrument="CM", cm_density=1).mode() == "freehand"
    assert LayoutRecipe(instrument="CM", cm_density=2).mode() == "high"
    assert LayoutRecipe(instrument="CM", cm_density=3).mode() == "extrahigh"
    assert LayoutRecipe(instrument="SS", hflag=True).mode() == "hex"
    assert LayoutRecipe(instrument="41").mode() == "default"
    assert LayoutRecipe(instrument="i1", paper="A4", clip_border=True).preset_key() == "i1|A4|clip"


def test_recipe_dict_roundtrip():
    r = LayoutRecipe(instrument="CM", paper="A3", hflag=True, seed=123, pscale=0.9)
    r2 = LayoutRecipe.from_dict(r.to_dict())
    assert r2 == r
    # unknown keys ignored (forward-compat)
    r3 = LayoutRecipe.from_dict({**r.to_dict(), "future_field": 1})
    assert r3 == r


def test_build_kwargs_maps_clip_border():
    assert LayoutRecipe(instrument="i1", clip_border=False).build_kwargs()["nolpcbord"] is True
    assert LayoutRecipe(instrument="i1", clip_border=True).build_kwargs()["nolpcbord"] is False
    # clip_border irrelevant for non-i1 -> never suppresses
    assert LayoutRecipe(instrument="CM", clip_border=False).build_kwargs()["nolpcbord"] is False


def test_store_get_set_default_fallback():
    store = PresetStore()
    # nothing stored -> default
    d = store.get("i1", "A4", "clip")
    assert isinstance(d, LayoutRecipe) and d.instrument == "i1" and d.clip_border is True
    # set then get returns stored values (seed dropped from presets)
    store.set(LayoutRecipe(instrument="i1", paper="A4", clip_border=True, pscale=0.8, seed=99))
    got = store.get("i1", "A4", "clip")
    assert got.pscale == 0.8
    assert got.seed is None


def test_all_fields_persist_through_named_dict():
    """Every engine option must survive the file-backed preset path
    (store.set → as_named_dict → from_named_dict → get) so it saves as a default
    / preset like the printtarg options. Presets drop only the per-chart seed."""
    from dataclasses import fields, replace
    full = LayoutRecipe(
        instrument="i1", paper="A4", clip_border=True, dpi=150, randomize=True,
        cm_density=1, spacer_on=True, spacer_mode="bw", pscale=0.9, sscale=1.1,
        border=8.0, margin_top=10.0, margin_right=8.0, margin_bottom=12.0,
        margin_left=9.0, patch_w_mm=9.0, patch_h_mm=11.0, spacer_width_mm=2.0,
        inter_patch_mm=1.0, max_strip_mm=200.0, strip_indicator_gap_mm=3.0,
        offset_x_mm=4.0, offset_y_mm=5.0, bit16=True, compression="zlib",
        show_strip_indicators=True, indicator_font="Inter", indicator_size_mm=4.0,
        indicator_bold=True, indicator_italic=True, underline_mode="cycle",
        underline_thickness_mm=0.8, underline_gap_mm=1.2, chart_text="{project}",
        chart_text_font="Inter", chart_text_size_mm=3.5, chart_text_bold=True,
        chart_text_italic=True, stamp_command=True, clip_border_width_mm=30.0,
        clip_content_mode="text", clip_text="ID", clip_text_font="Inter",
        clip_image_path="/tmp/logo.png", nolimit=True, strip_pattern="A-Z",
        patch_pattern="1-99")
    store = PresetStore()
    store.set(full)
    reloaded = PresetStore.from_named_dict(store.as_named_dict())
    got = reloaded.get("i1", "A4", "clip")
    # every field except the deliberately-dropped per-chart seed must match
    for f in fields(LayoutRecipe):
        if f.name == "seed":
            assert got.seed is None
            continue
        assert getattr(got, f.name) == getattr(full, f.name), f.name


def test_from_channels_json(tmp_path):
    import json
    rec = LayoutRecipe(instrument="i1", paper="A4", clip_border=True,
                       clip_content_mode="branding", underline_mode="cycle",
                       indicator_bold=True)
    ch = tmp_path / "c.channels.json"
    ch.write_text(json.dumps({"layout": {"engine": "chromiq", "seed": 42,
                                         "recipe": rec.to_dict()}}))
    got = LayoutRecipe.from_channels_json(ch)
    assert got is not None
    assert got.clip_content_mode == "branding"
    assert got.underline_mode == "cycle"
    assert got.indicator_bold is True
    assert got.seed == 42                    # build seed carried for reproduction
    # not an engine chart → None
    nb = tmp_path / "nb.channels.json"
    nb.write_text(json.dumps({"layout": {"strips": []}}))
    assert LayoutRecipe.from_channels_json(nb) is None
    assert LayoutRecipe.from_channels_json(tmp_path / "missing.json") is None


def test_store_save_load(tmp_path):
    store = PresetStore.factory_defaults()
    p = tmp_path / "presets.json"
    store.save(p)
    loaded = PresetStore.load(p)
    assert loaded.keys() == store.keys()
    assert "i1|A4|clip" in loaded.keys()
    assert "CM|A4|high" in loaded.keys()


def test_factory_defaults_have_modes():
    f = PresetStore.factory_defaults()
    keys = f.keys()
    assert "i1|A4|noclip" in keys
    assert "SS|A4|hex" in keys
    # ColorMunki gets normal + two high-density presets per paper
    assert "CM|A4|freehand" in keys
    assert "CM|A4|high" in keys
    assert "CM|A4|extrahigh" in keys


def test_default_recipe_mode_application():
    assert default_recipe("i1", "A4", mode="noclip").clip_border is False
    assert default_recipe("CM", "A4", mode="high").cm_density == 2
    assert default_recipe("CM", "A4", mode="extrahigh").cm_density == 3
