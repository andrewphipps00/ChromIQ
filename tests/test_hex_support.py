"""SpectroScan hexagonal-chart detection (Knut #126): the scanner/camera CHT
features are blocked for these charts, so detection must be exact."""
from __future__ import annotations

import json

from workflow.hex_support import (chart_is_hexagonal, hex_unsupported_message,
                                  recipe_is_hexagonal)


def test_recipe_is_hexagonal_dict_and_object():
    assert recipe_is_hexagonal({"instrument": "SS", "hflag": True}) is True
    assert recipe_is_hexagonal({"instrument": "SS", "hflag": False}) is False
    # hflag is SpectroScan-only: a stray hflag on another instrument is not hex.
    assert recipe_is_hexagonal({"instrument": "i1", "hflag": True}) is False
    assert recipe_is_hexagonal({}) is False
    assert recipe_is_hexagonal(None) is False

    class _R:
        instrument = "SS"
        hflag = True
    assert recipe_is_hexagonal(_R()) is True


def test_chart_is_hexagonal_reads_channels_json(tmp_path):
    # A SpectroScan hex chart's sidecar.
    (tmp_path / "chart.channels.json").write_text(
        json.dumps({"layout": {"recipe": {"instrument": "SS", "hflag": True}}}))
    (tmp_path / "chart.ti2").write_text("x")
    assert chart_is_hexagonal(tmp_path / "chart.ti2") is True
    assert chart_is_hexagonal(tmp_path / "chart.ti3") is True   # by stem
    assert chart_is_hexagonal(tmp_path / "chart.channels.json") is True

    # A rectangular SpectroScan chart is fine.
    (tmp_path / "flat.channels.json").write_text(
        json.dumps({"layout": {"recipe": {"instrument": "SS", "hflag": False}}}))
    assert chart_is_hexagonal(tmp_path / "flat.ti2") is False

    # Fail open: no sidecar, unreadable, or None → not hex (never blocks blindly).
    assert chart_is_hexagonal(tmp_path / "missing.ti2") is False
    assert chart_is_hexagonal(None) is False


def test_hex_unsupported_message_lists_the_features():
    msg = hex_unsupported_message()
    assert "scanner" in msg.lower()
    assert "Rectangular" in msg
    assert "hexagon" in msg.lower()
