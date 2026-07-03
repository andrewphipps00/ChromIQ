"""Build-scanner-profile dialog: the colprof profile-type selector (Knut #98).

The scanner ICC used to be hardcoded to shaper+matrix / medium quality; Knut
asked for Matrix / LUT-medium / LUT-high to be selectable. These guard that the
combo offers exactly those three, maps each to the right colprof (-a, -q) pair,
and that the default keeps the previous output (shaper+matrix, medium)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.settings import DEFAULTS  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self, **overrides):
        self._store = {**DEFAULTS, **overrides}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value


def _dialog(_app):
    from ui.dialogs.scanin_dialog import ScannerProfileDialog
    return ScannerProfileDialog(object(), _FakeSettings())


def test_profile_type_options_and_mapping(_app):
    dlg = _dialog(_app)
    try:
        combo = dlg._ptype
        got = [(combo.itemData(i)) for i in range(combo.count())]
        # Three choices, in order: Matrix, LUT medium, LUT high.
        assert got == [("s", "m"), ("x", "m"), ("x", "h")]
        # Default selection = Matrix (keeps the previous shaper+matrix output).
        assert combo.currentData() == ("s", "m")
    finally:
        dlg.deleteLater()


def test_gridspec_from_cht_it8(_app):
    """GridSpec.from_cht parses a standard IT8 .cht into normalised patch rects
    in the fiducial frame (288 patches; missing fiducials → empty)."""
    from pathlib import Path
    from ui.scan_grid_marquee import GridSpec
    cht = Path("/Applications/Argyll/ref/it8.cht")
    if not cht.is_file():
        import pytest as _pt
        _pt.skip("it8.cht not present")
    g = GridSpec.from_cht(cht.read_text(errors="ignore"))
    assert len(g.rects) == 288
    us = [r[0] for r in g.rects]
    assert min(us) >= -0.02 and max(u + w for u, _, w, _ in g.rects) <= 1.02
    # A non-cht string yields no grid rather than raising.
    assert GridSpec.from_cht("not a cht").rects == []


def test_profile_type_high_selects_xh(_app):
    dlg = _dialog(_app)
    try:
        combo = dlg._ptype
        combo.setCurrentIndex(2)
        assert combo.currentData() == ("x", "h")
    finally:
        dlg.deleteLater()


def _has_it8():
    from pathlib import Path
    return Path("/Applications/Argyll/ref/it8.cht").is_file()


def test_standard_mode_lists_targets_and_loads_grid(_app):
    """Switching to standard-target mode lists Argyll's ref/ targets and
    auto-loads the first one's patch grid."""
    if not _has_it8():
        import pytest as _pt
        _pt.skip("Argyll ref/ not present")
    dlg = _dialog(_app)
    try:
        # Argyll ships 25 targets + the "Other…" entry.
        assert dlg._target_combo.count() >= 2
        dlg._mode_standard.setChecked(True)
        assert dlg._std_grid is not None and len(dlg._std_grid.rects) > 0
        # No reference / scan yet → can't run.
        assert dlg._can_run() is False
    finally:
        dlg.deleteLater()


def test_standard_mode_execute_uses_chosen_cht_and_reference(_app, tmp_path):
    """_execute_standard pairs the chosen .cht with the target's reference file,
    reads the scan, and writes the profile next to the scan."""
    if not _has_it8():
        import pytest as _pt
        _pt.skip("Argyll ref/ not present")
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        dlg._mode_standard.setChecked(True)
        scan = tmp_path / "myscan.tif"
        scan.write_bytes(b"II*\0")                       # placeholder file
        ref = tmp_path / "R123.txt"
        ref.write_text("dummy reference")
        cht = Path("/Applications/Argyll/ref/it8.cht")
        dlg._set_std_target(cht)
        dlg._std_ref = ref
        dlg._cur_shot()["path"] = scan
        dlg._cur_shot()["corners"] = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert dlg._can_run() is True
        captured = []
        dlg._run_job = lambda i: captured.append(i)      # don't actually run
        dlg._execute()
        jobs = dlg._jobs
        assert jobs[0]["kind"] == "scanin"
        p = jobs[0]["params"]
        # At the default 60% sample area the dialog hands scanin a sample-adjusted
        # sibling .cht (BOX_SHRINK rewritten) — never the read-only bundled file —
        # while the reference and scan are untouched.
        assert p.cht == scan.parent / "it8-sample.cht" and p.cht.is_file()
        assert "BOX_SHRINK" in p.cht.read_text()
        assert p.cie == ref and p.scan_tif == scan
        assert jobs[-1]["kind"] == "colprof"
        # Profile base sits next to the scan (→ <scan>-scanner.ti3/.icc).
        assert jobs[-1]["base"] == scan.parent / scan.stem
        assert p.out_ti3.parent == scan.parent
    finally:
        dlg.deleteLater()


def test_multi_scan_averaging_pipeline(_app, tmp_path):
    """Two scans of a page → two scanin jobs + an average job whose output feeds
    colprof (Knut #98, ask 1c). The averaging method flows through."""
    if not _has_it8():
        import pytest as _pt
        _pt.skip("Argyll ref/ not present")
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        dlg._mode_standard.setChecked(True)
        dlg._set_std_target(Path("/Applications/Argyll/ref/it8.cht"))
        dlg._std_ref = tmp_path / "ref.txt"
        dlg._std_ref.write_text("x")
        s1 = tmp_path / "s1.tif"; s1.write_bytes(b"II*\0")
        s2 = tmp_path / "s2.tif"; s2.write_bytes(b"II*\0")
        dlg._cur_shot()["path"] = s1
        dlg._add_shot()
        dlg._cur_shot()["path"] = s2
        # Averaging controls appear once a page has two scans.
        assert dlg._avg_row_w.isVisibleTo(dlg)
        dlg._avg_method.setCurrentIndex(1)               # geomean
        dlg._run_job = lambda i: None
        dlg._execute()
        kinds = [j["kind"] for j in dlg._jobs]
        assert kinds == ["scanin", "scanin", "average", "colprof"]
        avg = next(j for j in dlg._jobs if j["kind"] == "average")
        assert avg["method"] == "geomean" and len(avg["ti3s"]) == 2
        colprof = next(j for j in dlg._jobs if j["kind"] == "colprof")
        assert colprof["ti3s"] == [avg["out"]]           # profile the average
    finally:
        dlg.deleteLater()


def test_multipage_multiscan_pipeline(_app):
    """A multi-page ChromIQ chart with several scans per page: each page reads
    its scans with that page's own .cht, averages *within* the page, then colprof
    combines the per-page averages (Knut #98 — pages × averaging together)."""
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        # Simulate a loaded 2-page engine chart (stay in ChromIQ mode).
        dlg._ti3 = Path("/tmp/proj/mychart.ti3")
        dlg._layout = {"patches": [{"page": 0}, {"page": 1}]}
        dlg._pages = [0, 1]
        for pg in (0, 1):
            shots = dlg._page_shots(pg)
            shots.clear()
            for k in (1, 2):
                shots.append({"path": Path(f"/tmp/proj/p{pg + 1}_scan{k}.tif"),
                              "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
        dlg._run_job = lambda i: None
        dlg._execute()
        kinds = [j["kind"] for j in dlg._jobs]
        # per page: two scanin + one average; then one colprof over both pages.
        assert kinds == ["scanin", "scanin", "average",
                         "scanin", "scanin", "average", "colprof"]
        avgs = [j for j in dlg._jobs if j["kind"] == "average"]
        assert len(avgs) == 2 and all(len(a["ti3s"]) == 2 for a in avgs)
        # Each page uses its own per-page .cht.
        scanins = [j for j in dlg._jobs if j["kind"] == "scanin"]
        assert scanins[0]["params"].cht.name == "mychart_01.cht"
        assert scanins[2]["params"].cht.name == "mychart_02.cht"
        colprof = next(j for j in dlg._jobs if j["kind"] == "colprof")
        assert colprof["ti3s"] == [a["out"] for a in avgs]   # both page averages
    finally:
        dlg.deleteLater()


def test_demo_scan_button_loads_files(_app, tmp_path, monkeypatch):
    """"Try with a demo scan" generates a test scan + reference from the chosen
    target and LOADS them into the scan/reference fields — regression for the
    QPlainTextEdit.append crash (the log has no .append; must be appendPlainText)
    and for the auto-load behaviour that replaced the confusing Finder pop-up."""
    from pathlib import Path
    import workflow.standard_targets as st

    dlg = _dialog(_app)
    dlg._mode_standard.setChecked(True)
    dlg._on_mode_changed()
    dlg._set_std_target(Path("data/scanner_targets/it8Wolf.cht").resolve())

    real = st.make_test_scan            # write into a temp dir, not real ~/ChromIQ
    monkeypatch.setattr(st, "make_test_scan", lambda cht, _out: real(cht, tmp_path))

    dlg._reveal_target_files()          # must not raise

    tif, cie = tmp_path / "it8Wolf-test.tif", tmp_path / "it8Wolf-test.cie"
    assert tif.is_file() and cie.is_file()
    assert dlg._scan_field.text() == str(tif)      # auto-loaded as the scan
    assert dlg._ref_field.text() == str(cie)       # …and the reference
    assert dlg._std_ref == cie
    assert "demo scan" in dlg._log.toPlainText()
