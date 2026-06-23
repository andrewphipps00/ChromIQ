"""Settings round-trip + violation logic for the margin inspector."""
from __future__ import annotations

import pytest

from core.settings import AppSettings, default_margin_thresholds, margin_combo_key
from workflow.margin_inspector import MarginReport, Violation, check_violations


def _report(L=20.0, R=20.0, T=20.0, B=20.0) -> MarginReport:
    return MarginReport(left_mm=L, right_mm=R, top_mm=T, bottom_mm=B,
                        strip_width_mm=11.0, page_w_mm=210.0, page_h_mm=297.0)


# --- combo key + seeds -----------------------------------------------------

def test_combo_key_format():
    assert margin_combo_key("i1Pro", "A4", "landscape") == "i1Pro|A4 Landscape"
    assert margin_combo_key("ColorMunki", "A3", "Portrait") == "ColorMunki|A3 Portrait"
    assert margin_combo_key("i1Pro", "A2", "") == "i1Pro|A2"


def test_seed_table_matches_knut_values():
    seeds = default_margin_thresholds()
    # i1Pro: 10 mm sides/bottom, 24 mm on the Top (label) edge (#82).
    assert seeds["i1Pro|A4 Portrait"] == {"L": 10, "R": 10, "T": 24, "B": 10,
                                          "desc": "i1Pro ruler / jig"}
    assert seeds["i1Pro|A3 Landscape"]["T"] == 24
    # ColorMunki: 6 mm sides/bottom, 24 mm on Top.
    assert seeds["ColorMunki|A4 Portrait"]["L"] == 6
    assert seeds["ColorMunki|Tabloid Landscape"]["T"] == 24
    # A fresh call returns an independent copy (no shared mutation).
    seeds["i1Pro|A4 Portrait"]["L"] = 999
    assert default_margin_thresholds()["i1Pro|A4 Portrait"]["L"] == 10


# --- settings round-trip ---------------------------------------------------

def _isolated_settings(tmp_path) -> AppSettings:
    from PyQt6.QtCore import QSettings
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    return s


def test_thresholds_round_trip(tmp_path):
    s = _isolated_settings(tmp_path)
    # Empty store → seed defaults.
    assert s.get_margin_thresholds()["i1Pro|A4 Portrait"]["L"] == 10
    table = {"i1Pro|A4 Portrait": {"L": 12.5, "R": 30, "T": 11, "B": 11,
                                   "desc": "my rig"}}
    s.set_margin_thresholds(table)
    got = s.get_margin_thresholds()
    assert got["i1Pro|A4 Portrait"]["R"] == 30
    assert got["i1Pro|A4 Portrait"]["desc"] == "my rig"


def test_corrupt_blob_falls_back_to_seeds(tmp_path):
    s = _isolated_settings(tmp_path)
    s.set("margin_thresholds", "{not json")
    assert s.get_margin_thresholds() == default_margin_thresholds()


# --- violation logic -------------------------------------------------------

def test_no_thresholds_no_violations():
    assert check_violations(_report(), None) == []
    assert check_violations(_report(), {}) == []


def test_below_threshold_flags_edge():
    v = check_violations(_report(L=8.0), {"L": 11, "R": 11, "T": 11, "B": 11})
    assert v == [Violation("Left", 8.0, 11.0)]


def test_equal_threshold_is_ok():
    assert check_violations(_report(L=11.0), {"L": 11}) == []


def test_multiple_edges_and_missing_keys():
    v = check_violations(_report(L=5.0, T=2.0),
                         {"L": 11, "T": 11})   # R/B unset → unchecked
    edges = {x.edge for x in v}
    assert edges == {"Left", "Top"}


def test_blank_string_threshold_ignored():
    assert check_violations(_report(L=1.0), {"L": "", "R": 11}) == []
