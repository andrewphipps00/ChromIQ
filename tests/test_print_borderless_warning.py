"""Borderless detection for the always-on chart-scaling warning.

Borderless physically cannot print at 100% — drivers enlarge the page a few
percent past the paper edges (Epson bakes cupsBorderlessScalingFactor
1.03–1.07 into its borderless PageSizes; Canon scales via its extension
setting). The Print tab therefore warns whenever the selection requests
borderless, in any of the three encodings drivers use.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.tabs.tab_print import _borderless_selected  # noqa: E402


def test_vendor_toggle_detected() -> None:
    assert _borderless_selected({"EPIJ_Brlss": "True"})
    assert _borderless_selected({"CNBorderless": "on"})


def test_synthetic_toggle_detected() -> None:
    assert _borderless_selected({"__BORDERLESS__": "true"})


def test_epson_psrc_borderless_detected() -> None:
    assert _borderless_selected({"EPIJ_PSrc": "3"})


def test_borderless_page_size_variant_detected() -> None:
    assert _borderless_selected({"PageSize": "A4.FullBleed"})
    assert _borderless_selected({"EPIJ_Size": "EPKG.NMgn"})
    assert _borderless_selected({"media": "4x6Borderless"})


def test_bordered_selection_not_flagged() -> None:
    assert not _borderless_selected({
        "PageSize": "A4",
        "EPIJ_PSrc": "1",
        "__BORDERLESS__": "false",
        "CNIJMediaType": "51",
    })
    assert not _borderless_selected({})
