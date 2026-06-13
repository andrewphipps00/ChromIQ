"""Spreadsheet export / import round-trip for translations.

Verifies that building rows from a shipped language and applying them back
reconstructs the exact catalog (identity), that CSV and XLSX preserve every
row — including the HTML/comma/newline-heavy strings — and that the importer's
validation catches broken placeholders and incomplete choice lists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from workflow import i18n_roundtrip as rt

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _empty_user_dir(tmp_path, monkeypatch):
    """Point the writable override dir at an empty tmp dir so tests read the
    bundled catalogs (and writes never touch the real ~/ChromIQ)."""
    monkeypatch.setattr(rt, "user_i18n_dir", lambda: tmp_path / "i18n")
    return tmp_path / "i18n"


def _tuples(rows):
    return [r.as_tuple() for r in rows]


# ---------------------------------------------------------------------------
# Round-trip through each file format preserves every row exactly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", [".csv", ".xlsx"])
def test_write_read_identity(tmp_path, ext):
    rows = rt.build_rows("de")
    path = tmp_path / f"de{ext}"
    (rt.write_xlsx if ext == ".xlsx" else rt.write_csv)(rows, path)
    back = rt.read_rows(path)
    assert _tuples(back) == _tuples(rows)


@pytest.mark.parametrize("ext", [".csv", ".xlsx"])
def test_html_and_newline_strings_survive(tmp_path, ext):
    rows = [
        rt.Row("ui", "a", "a", "<b>Bold</b>, with comma"),
        rt.Row("ui", "b", "b", "line one\nline two"),
        rt.Row("ui", "c", "c", 'has "quotes" inside'),
    ]
    path = tmp_path / f"x{ext}"
    (rt.write_xlsx if ext == ".xlsx" else rt.write_csv)(rows, path)
    assert _tuples(rt.read_rows(path)) == _tuples(rows)


# ---------------------------------------------------------------------------
# Applying an unchanged export rebuilds the shipped catalogs (identity)
# ---------------------------------------------------------------------------

def test_apply_rebuilds_ui_catalog():
    rows = rt.build_rows("de")
    json_dict, _yaml_dict, report = rt.apply_rows("de", rows)
    bundled = json.loads((ROOT / "data/i18n/de.json").read_text(encoding="utf-8"))
    assert json_dict == bundled
    assert not report.has_errors


def test_apply_rebuilds_parameter_overlay():
    rows = rt.build_rows("de")
    _json_dict, yaml_dict, _report = rt.apply_rows("de", rows)
    bundled = yaml.safe_load(
        (ROOT / "data/i18n/parameters.de.yaml").read_text(encoding="utf-8"))
    assert yaml_dict.get("parameters") == bundled.get("parameters")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_broken_placeholder_is_reported_and_excluded():
    rows = rt.build_rows("de")
    target = next(r for r in rows
                  if r.section == "ui" and "{" in r.english and r.translation)
    # change the placeholder name so it no longer matches the source
    target.translation = "kaputt {nope}"
    json_dict, _yaml, report = rt.apply_rows("de", rows)
    assert target.english in report.placeholder_errors
    assert target.english not in json_dict
    assert report.has_errors


def test_incomplete_label_list_is_reported():
    rows = rt.build_rows("de")
    # drop one label row of targen/-d so the translated list is short
    idx = [i for i, r in enumerate(rows)
           if r.id.startswith("targen/-d/labels/")]
    del rows[idx[-1]]
    _json, yaml_dict, report = rt.apply_rows("de", rows)
    assert "targen/-d/labels" in report.label_errors
    assert "labels" not in yaml_dict["parameters"]["targen"]["-d"]


def test_missing_translations_use_english_not_error():
    rows = rt.build_rows("de")
    blanked = 0
    for r in rows:
        if r.section == "ui":
            r.translation = ""
            blanked += 1
    json_dict, _yaml, report = rt.apply_rows("de", rows)
    assert not report.has_errors
    assert report.missing == blanked
    # nothing written for blanks → tr() will fall back to English
    assert all(k.startswith("@") for k in json_dict)


def test_target_code_mismatch_flagged():
    rows = rt.build_rows("de")  # contains @target_code = "de"
    _json, _yaml, report = rt.apply_rows("fr", rows)
    assert report.code_mismatch == "de"


# ---------------------------------------------------------------------------
# Saving writes into the override dir and is reloadable
# ---------------------------------------------------------------------------

def test_save_translation_writes_user_dir(_empty_user_dir):
    rows = rt.build_rows("de")
    json_dict, yaml_dict, _report = rt.apply_rows("de", rows)
    out = rt.save_translation("zz", json_dict, yaml_dict)
    assert out == _empty_user_dir
    saved = json.loads((out / "zz.json").read_text(encoding="utf-8"))
    assert saved == json_dict
    saved_yaml = yaml.safe_load((out / "parameters.zz.yaml").read_text(encoding="utf-8"))
    assert saved_yaml.get("parameters") == yaml_dict["parameters"]


def test_save_without_params_removes_stale_overlay(_empty_user_dir):
    _empty_user_dir.mkdir(parents=True, exist_ok=True)
    stale = _empty_user_dir / "parameters.zz.yaml"
    stale.write_text("parameters: {}\n", encoding="utf-8")
    rt.save_translation("zz", {"@language_name": "Zz"}, {})
    assert not stale.exists()


def test_new_language_starts_blank():
    rows = rt.build_rows("zz", language_name="Zedish")
    assert all(r.translation == "" for r in rows
               if r.section in ("ui", "param"))
    meta = {r.id: r.translation for r in rows if r.section == "meta"}
    assert meta["@language_name"] == "Zedish"
    assert meta["@target_code"] == "zz"
