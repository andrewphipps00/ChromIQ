"""Tests for LayoutRecipe + PresetStore (round-trip persistence, presets)."""
from workflow.layout_engine.presets import (
    LayoutRecipe, PresetStore, default_recipe,
)


def test_mode_and_preset_key():
    assert LayoutRecipe(instrument="i1", clip_border=True).mode() == "clip"
    assert LayoutRecipe(instrument="i1", clip_border=False).mode() == "noclip"
    assert LayoutRecipe(instrument="CM", hflag=True).mode() == "rig"
    assert LayoutRecipe(instrument="CM", hflag=False).mode() == "freehand"
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


def test_store_save_load(tmp_path):
    store = PresetStore.factory_defaults()
    p = tmp_path / "presets.json"
    store.save(p)
    loaded = PresetStore.load(p)
    assert loaded.keys() == store.keys()
    assert "i1|A4|clip" in loaded.keys()
    assert "CM|A4|rig" in loaded.keys()


def test_factory_defaults_have_modes():
    f = PresetStore.factory_defaults()
    keys = f.keys()
    assert "i1|A4|noclip" in keys
    assert "SS|A4|hex" in keys


def test_default_recipe_mode_application():
    assert default_recipe("i1", "A4", mode="noclip").clip_border is False
    assert default_recipe("CM", "A4", mode="rig").hflag is True
