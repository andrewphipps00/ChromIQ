"""Bundled corrected scanner targets (Knut #2): the dropdown prefers ChromIQ's
bundled ``.cht`` over the user's Argyll ``ref/`` copies (which had wrong
fiducials for several targets)."""
from __future__ import annotations

from pathlib import Path

from workflow.standard_targets import (
    bundled_targets_dir, display_name, list_standard_targets)


class _S:
    def __init__(self, **o): self._s = o
    def get(self, k, d=None): return self._s.get(k, d)


def test_bundle_present_and_nonempty():
    d = bundled_targets_dir()
    assert d is not None and d.is_dir()
    chts = list(d.glob("*.cht"))
    assert len(chts) >= 6, "expected the corrected .cht set to be bundled"
    # Licence + attribution ship alongside the GPLv3 files.
    assert (d / "LICENSE").is_file() and (d / "README.md").is_file()


def test_bundle_listed_even_without_argyll_ref(tmp_path):
    # No Argyll ref/ available → the dropdown still lists the bundled targets.
    targets = list_standard_targets(_S(argyll_bin_path=str(tmp_path / "bin")))
    stems = {p.stem for _, p in targets}
    assert {"Hutchcolor", "LaserSoftDCPro", "SpyderChecker24"} <= stems


def test_bundled_cht_preferred_over_argyll_ref(tmp_path):
    # A fake Argyll ref/ with a same-named .cht must be overridden by the bundle.
    ref = tmp_path / "ref"; ref.mkdir()
    (ref / "Hutchcolor.cht").write_text("stale argyll copy")
    (tmp_path / "bin").mkdir()
    targets = dict((p.stem, p) for _, p in
                   list_standard_targets(_S(argyll_bin_path=str(tmp_path / "bin"))))
    hutch = targets["Hutchcolor"]
    assert hutch == bundled_targets_dir() / "Hutchcolor.cht"   # bundle wins
    assert "stale argyll copy" not in hutch.read_text()


def test_bundled_cht_parses_and_registers():
    """The bundled corrected .cht parse cleanly with ChromIQ's own parser."""
    from workflow.cht_parser import parse_cht
    d = bundled_targets_dir()
    for cht in d.glob("*.cht"):
        g = parse_cht(cht.read_text(errors="ignore"))
        assert g.patches and len(g.fiducials) == 4, f"{cht.name} parse looks wrong"
