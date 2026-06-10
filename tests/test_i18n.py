"""Language support: catalog loading, fallback, overlay merge, hygiene.

The German catalog itself is also validated here (completeness against
the tr() call sites, placeholder integrity, parameters overlay coverage)
so a future string change that forgets the translation fails CI instead
of silently showing mixed-language UI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from core import i18n

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from i18n_extract import extract_keys  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_language():
    # i18n state is module-global — never leak a language into other tests.
    yield
    i18n.set_language("en")


# ----------------------------------------------------------------------
# Core behaviour
# ----------------------------------------------------------------------

def test_english_is_passthrough():
    i18n.set_language("en")
    assert i18n.tr("Build Profile") == "Build Profile"
    assert i18n.current_language() == "en"


def test_german_translates_known_string():
    i18n.set_language("de")
    assert i18n.current_language() == "de"
    assert i18n.tr("Cancel") == "Abbrechen"


def test_unknown_string_falls_through_untranslated():
    i18n.set_language("de")
    assert i18n.tr("zz-not-a-real-source-string") == "zz-not-a-real-source-string"


def test_unknown_language_falls_back_to_english():
    i18n.set_language("xx")
    assert i18n.current_language() == "en"
    assert i18n.tr("Cancel") == "Cancel"


def test_available_languages_lists_english_and_german():
    langs = dict(i18n.available_languages())
    assert langs["en"] == "English"
    assert langs["de"] == "Deutsch"


# ----------------------------------------------------------------------
# German catalog hygiene
# ----------------------------------------------------------------------

def _load_de() -> dict[str, str]:
    with open(ROOT / "data" / "i18n" / "de.json", encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("@")}


def test_de_catalog_is_complete():
    missing = sorted(extract_keys() - set(_load_de()))
    assert not missing, f"{len(missing)} untranslated strings, e.g. {missing[:5]}"


def test_de_catalog_has_no_stale_keys():
    stale = sorted(set(_load_de()) - extract_keys())
    assert not stale, f"{len(stale)} stale catalog keys, e.g. {stale[:5]}"


def test_de_placeholders_match_source():
    bad = i18n.check_placeholders(_load_de())
    assert not bad, f"placeholder mismatch in: {bad[:5]}"


def test_de_short_labels_stay_compact():
    """Button/label-sized strings must not balloon in German (clipping).

    Short English strings are the ones that end up on buttons and tab
    labels; allow modest growth plus a small constant, flag the rest.
    """
    offenders = []
    for src, dst in _load_de().items():
        if "\n" in src or len(src) > 24 or "{" in src:
            continue
        if len(dst) > int(len(src) * 1.6) + 6:
            offenders.append((src, dst))
    assert not offenders, f"over-long German for short labels: {offenders}"


# ----------------------------------------------------------------------
# parameters.yaml overlay
# ----------------------------------------------------------------------

def _load_params() -> dict:
    with open(ROOT / "data" / "parameters.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["parameters"]


def test_parameters_overlay_covers_every_parameter():
    overlay = yaml.safe_load(
        (ROOT / "data" / "i18n" / "parameters.de.yaml").read_text(encoding="utf-8")
    )["parameters"]
    problems = []
    for tool, defs in _load_params().items():
        for p in defs:
            entry = overlay.get(tool, {}).get(p["flag"])
            if entry is None:
                problems.append(f"{tool} {p['flag']}: no overlay entry")
                continue
            for field in ("name", "tooltip_title", "tooltip_body"):
                if field in p and field not in entry:
                    problems.append(f"{tool} {p['flag']}: missing {field}")
            if "labels" in p and len(entry.get("labels", [])) != len(p["labels"]):
                problems.append(f"{tool} {p['flag']}: label count mismatch")
    assert not problems, problems


def test_translate_parameters_merges_german():
    i18n.set_language("de")
    params = i18n.translate_parameters(_load_params())
    d = next(p for p in params["targen"] if p["flag"] == "-d")
    assert d["name"] == "Gerätetyp"
    assert len(d["labels"]) == 16


def test_translate_parameters_is_noop_for_english():
    i18n.set_language("en")
    params = _load_params()
    translated = i18n.translate_parameters(params)
    d = next(p for p in translated["targen"] if p["flag"] == "-d")
    assert d["name"] == "Device Type"


def test_label_count_mismatch_keeps_english_labels(tmp_path, monkeypatch):
    """A stale overlay with the wrong number of labels must be ignored
    for that list — labels and choices may never desynchronise."""
    i18n.set_language("de")
    params = {"targen": [{"flag": "-d", "name": "Device Type",
                          "labels": ["a", "b", "c"]}]}
    monkeypatch.setattr(
        i18n, "_load_parameters_overlay",
        lambda code: {"targen": {"-d": {"name": "Gerätetyp",
                                        "labels": ["nur", "zwei"]}}},
    )
    out = i18n.translate_parameters(params)
    assert out["targen"][0]["name"] == "Gerätetyp"
    assert out["targen"][0]["labels"] == ["a", "b", "c"]
