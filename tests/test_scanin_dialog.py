"""Build-scanner-profile dialog: the colprof profile-type selector (Knut #98).

The scanner ICC used to be hardcoded to shaper+matrix / medium quality; Knut
asked for Matrix / LUT-medium / LUT-high to be selectable. These guard that the
combo offers exactly those three, maps each to the right colprof (-a, -q) pair,
and that the default keeps the previous output (shaper+matrix, medium)."""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.settings import DEFAULTS  # noqa: E402
from tests.argyll_env import argyll_ref_dir  # noqa: E402


def _it8():
    """Path to Argyll's bundled it8.cht (cross-platform), or None."""
    ref = argyll_ref_dir()
    return (ref / "it8.cht") if ref else None


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
    from ui.scan_grid_marquee import GridSpec
    cht = _it8()
    if cht is None or not cht.is_file():
        import pytest as _pt
        _pt.skip("it8.cht not present")
    g = GridSpec.from_cht(cht.read_text(errors="ignore"))
    assert len(g.rects) == 288
    us = [r[0] for r in g.rects]
    assert min(us) >= -0.02 and max(u + w for u, _, w, _ in g.rects) <= 1.02
    # A non-cht string yields no grid rather than raising.
    assert GridSpec.from_cht("not a cht").rects == []


def test_gridspec_carries_fiducial_frame(_app):
    """The consolidated geometry: from_cht returns the grid AND the fiducial frame
    in one normalised space (extends outside [0,1] since fiducials wrap the
    patches) — driving the on-screen frame and the scanin -F from one source."""
    from pathlib import Path
    from ui.scan_grid_marquee import GridSpec
    g = GridSpec.from_cht(Path("data/scanner_targets/CMP_Digital_Target-4.cht").read_text())
    assert g.fiducial_rect is not None
    u0, v0, u1, v1 = g.fiducial_rect
    assert u0 < 0 and v0 < 0 and u1 > 1 and v1 > 1
    assert GridSpec.from_cht("not a cht").fiducial_rect is None


def test_extrapolate_to_fiducials_derives_marks_from_patch_quad(_app):
    """The unified fix: the marquee is aligned to the patch bbox; ON derives the
    scanin -F by extrapolating that quad out to the fiducial frame (so it lands on
    the marks without the user placing them). Grows outward by the exact ratio."""
    from pathlib import Path
    from ui.scan_grid_marquee import extrapolate_to_fiducials, fiducial_frame
    from workflow.cht_parser import parse_cht
    txt = Path("data/scanner_targets/ISO12641_2_1.cht").read_text()
    g = parse_cht(txt); fr = fiducial_frame(txt)              # left,right,top,bottom
    xs = [b.x1 for b in g.patches] + [b.x2 for b in g.patches]
    ys = [b.y1 for b in g.patches] + [b.y2 for b in g.patches]
    px0, px1, py0, py1 = min(xs), max(xs), min(ys), max(ys)
    quad = [(px0*2, py0*2), (px1*2, py0*2), (px1*2, py1*2), (px0*2, py1*2)]  # patch @2x
    out = extrapolate_to_fiducials(quad, txt)
    assert out is not None
    # extrapolated corners = the fiducial frame at the same 2x mapping
    assert abs(out[0][0] - fr[0]*2) < 1 and abs(out[0][1] - fr[2]*2) < 1
    assert abs(out[2][0] - fr[1]*2) < 1 and abs(out[2][1] - fr[3]*2) < 1
    # …and it grew outward past the patch quad (fiducials sit outside the patches)
    assert out[0][0] < quad[0][0] and out[2][0] > quad[2][0]
    assert extrapolate_to_fiducials(quad, "not a cht") is None
    assert extrapolate_to_fiducials([(0, 0)], txt) is None    # need four corners


def test_profile_type_high_selects_xh(_app):
    dlg = _dialog(_app)
    try:
        combo = dlg._ptype
        combo.setCurrentIndex(2)
        assert combo.currentData() == ("x", "h")
    finally:
        dlg.deleteLater()


def _has_it8():
    cht = _it8()
    return cht is not None and cht.is_file()


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
    dlg = _dialog(_app)
    try:
        dlg._mode_standard.setChecked(True)
        scan = tmp_path / "myscan.tif"
        scan.write_bytes(b"II*\0")                       # placeholder file
        ref = tmp_path / "R123.txt"
        ref.write_text("dummy reference")
        cht = _it8()
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
        # while the reference and scan are untouched. With "Use fiducial marks" off
        # the F line is first rewritten to the patch bbox (…-patchbox…).
        assert p.cht.parent == scan.parent and p.cht.name.endswith("-sample.cht")
        assert p.cht.is_file() and "BOX_SHRINK" in p.cht.read_text()
        assert re.search(r"(?m)^\s*F .*$", p.cht.read_text())   # patch-bbox F line
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
    dlg = _dialog(_app)
    try:
        dlg._mode_standard.setChecked(True)
        dlg._set_std_target(_it8())
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


def test_sanitize_ti3_zeros_stdev_and_drops_bad_reads():
    """A bad STDEV (nan/inf, incl. Windows 1.#IND) is zeroed; a bad *value*
    (RGB/XYZ) drops the whole patch (so it can't become a false 'reads as black'
    point) and NUMBER_OF_SETS is updated. Regression for the Windows
    'Field STDEV_B … non-quoted char string' crash."""
    from workflow.scanin_runner import sanitize_ti3
    ti3 = ("CGATS.17\nNUMBER_OF_FIELDS 4\nBEGIN_DATA_FORMAT\n"
           "SAMPLE_ID RGB_R STDEV_G STDEV_B\nEND_DATA_FORMAT\nNUMBER_OF_SETS 4\n"
           "BEGIN_DATA\n"
           "1 50.1 0.3 nan\n"          # bad STDEV -> zeroed
           "2 20.0 1.#IND00 0.2\n"     # bad STDEV (Windows) -> zeroed
           "3 nan 0.1 0.2\n"           # bad VALUE (RGB_R) -> patch dropped
           "4 80.0 0.2 0.4\n"
           "END_DATA\n")
    clean, zeroed, dropped = sanitize_ti3(ti3)
    assert (zeroed, dropped) == (2, 1)
    data = [ln for ln in clean.splitlines() if ln[:1].isdigit() and "DATA" not in ln]
    ids = [ln.split()[0] for ln in data]
    assert ids == ["1", "2", "4"]              # patch 3 dropped
    assert "NUMBER_OF_SETS 3" in clean         # count updated
    for ln in data:
        for tok in ln.split()[1:]:
            float(tok)                         # every real column parses
    assert sanitize_ti3(clean) == (clean, 0, 0)   # idempotent
