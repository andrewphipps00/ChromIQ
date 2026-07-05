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


def test_gapped_grid_uses_rectarg_edges(_app):
    """A gapped grid (Hutchcolor, 528 of a 29×22 grid) now reports its col/row
    structure so the interior can be placed on rectarg's integer edges — and
    rectarg_align_cht widens the first columns (leftover pixels), matching a
    rounded rectarg image the same way the marquee does."""
    from pathlib import Path
    from ui.scan_grid_marquee import GridSpec, rectarg_align_cht
    from workflow.cht_parser import parse_cht
    txt = Path("data/scanner_targets/Hutchcolor.cht").read_text()
    g = GridSpec.from_cht(txt)
    assert g.ncols == 29 and g.nrows == 22 and g.cells is not None
    assert len(g.cells) == len(g.rects)
    ga = parse_cht(rectarg_align_cht(txt, 2237.0, 1688.0))
    xs = sorted({round(b.x1, 3) for b in ga.patches})
    assert (xs[1] - xs[0]) > (xs[10] - xs[9])          # first columns wider
    assert rectarg_align_cht(txt, 5, 5) == txt          # too small → unchanged
    assert rectarg_align_cht("not a cht", 2000, 2000) == "not a cht"


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


# ---------------------------------------------------------------------------
# #102 — one diagnostic image PER SCAN, not just the first
# ---------------------------------------------------------------------------

def test_every_scan_gets_its_own_diag_image(_app):
    """Five scans of one page with the diagnostic option on → five scanin jobs,
    each writing its own <scan>-diag.tif (Knut got only one, #102). Exactly the
    per-scan alignment check averaging is for."""
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        dlg._ti3 = Path("/tmp/proj/mychart.ti3")
        dlg._layout = {"patches": [{"page": 0}]}
        dlg._pages = [0]
        shots = dlg._page_shots(0)
        shots.clear()
        for k in range(5):
            shots.append({"path": Path(f"/tmp/proj/scan{k + 1}.tif"),
                          "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
        dlg._diag.setChecked(True)
        dlg._run_job = lambda i: None
        dlg._execute()
        scanins = [j for j in dlg._jobs if j["kind"] == "scanin"]
        assert len(scanins) == 5
        diags = [j["params"].diag for j in scanins]
        assert all(d is not None for d in diags)
        assert len(set(diags)) == 5                      # one per scan, distinct
        assert diags[2].name == "scan3-diag.tif"         # named after its scan
    finally:
        dlg.deleteLater()


def test_diag_off_writes_none(_app):
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        dlg._ti3 = Path("/tmp/proj/mychart.ti3")
        dlg._layout = {"patches": [{"page": 0}]}
        dlg._pages = [0]
        shots = dlg._page_shots(0)
        shots.clear()
        for k in range(2):
            shots.append({"path": Path(f"/tmp/proj/scan{k + 1}.tif"),
                          "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
        dlg._diag.setChecked(False)
        dlg._run_job = lambda i: None
        dlg._execute()
        assert all(j["params"].diag is None
                   for j in dlg._jobs if j["kind"] == "scanin")
    finally:
        dlg.deleteLater()


def test_printer_mode_diag_on_every_page(_app, tmp_path):
    """Printer mode, two pages, diag on → both pages' scanin jobs write a diag
    (only page 1 got one before, #102)."""
    from pathlib import Path
    dlg = _dialog(_app)
    try:
        base = tmp_path / "mychart"
        (tmp_path / "mychart.ti2").write_text("CTI2\n")
        dlg._ti3 = tmp_path / "mychart.ti2"
        dlg._layout = {"patches": [{"page": 0}, {"page": 1}]}
        dlg._pages = [0, 1]
        dlg._printer_cb.setChecked(True)
        dlg._printer_scan_profile = tmp_path / "scanner.icc"
        for pg in (0, 1):
            shots = dlg._page_shots(pg)
            shots.clear()
            shots.append({"path": tmp_path / f"page{pg + 1}.tif",
                          "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
            (tmp_path / f"page{pg + 1}.tif").write_bytes(b"II*\0")
        dlg._diag.setChecked(True)
        dlg._run_job = lambda i: None
        dlg._execute_printer(base, 0.9)
        scanins = [j for j in dlg._jobs if j["kind"] == "scanin"]
        assert len(scanins) == 2
        assert all(j["params"].diag is not None for j in scanins)
        assert scanins[0]["params"].diag.name == "page1-diag.tif"
        assert scanins[1]["params"].diag.name == "page2-diag.tif"
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# #101 — rejected chart picks must be loud, and the Browse hint mode-aware
# ---------------------------------------------------------------------------

def _engine_channels(path, locs, dpi=300, paper=(210.0, 297.0), patch_px=118):
    """Write a minimal but valid engine channels.json for *locs*."""
    import json
    patches = [{"loc": loc, "page": 0, "x": 100 + i * (patch_px + 10),
                "y": 100, "w": patch_px, "h": patch_px}
               for i, loc in enumerate(locs)]
    path.write_text(json.dumps({"layout": {
        "engine": "chromiq", "engine_version": 1, "dpi": dpi,
        "paper_mm": list(paper), "patches": patches}}))


def _tiny_ti2(path, locs):
    rows = "\n".join(
        f'{i + 1} "{loc}" {v:.1f} {v:.1f} {v:.1f} {v * 0.9:.2f} {v * 0.95:.2f} {v * 0.8:.2f}'
        for i, (loc, v) in enumerate(zip(locs, (0.0, 50.0, 100.0, 25.0))))
    path.write_text(f"""CTI2

DESCRIPTOR "t"
TARGET_INSTRUMENT "X-Rite ColorMunki"
COLOR_REP "iRGB"

NUMBER_OF_FIELDS 8
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS {len(locs)}
BEGIN_DATA
{rows}
END_DATA
""")


def test_chart_without_sidecar_is_rejected_loudly(_app, tmp_path):
    """Picking a chart with no .channels.json anywhere → the reason lands in
    the chart note AND the status log, and the scan Browse repeats it instead
    of the old generic hint (#101). (In printer mode a sidecar-less .ti2 now
    enters the BYO-.cht flow instead — see the #105 tests — so this drives the
    scanner-profile mode, where it stays a hard reject.)"""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "mychart.ti2", locs)
    (tmp_path / "mychart.ti3").write_text("CTI3\n")   # "measured", no sidecar
    dlg = _dialog(_app)
    try:
        assert not dlg._printer_cb.isChecked()        # scanner-profile mode
        dlg._set_chart(tmp_path / "mychart.ti3")
        assert dlg._layout is None
        assert dlg._chart_reject_reason
        assert ".channels.json" in dlg._chart_note.text()
        assert ".channels.json" in dlg._log.toPlainText()
        n = len(dlg._log.toPlainText())
        dlg._pick_scan()                       # dead Browse repeats the reason
        tail = dlg._log.toPlainText()[n:]
        assert "can't be used" in tail and ".channels.json" in tail
    finally:
        dlg.deleteLater()


def test_sidecar_found_by_folder_fallback(_app, tmp_path):
    """A .ti2 whose sidecar doesn't share its stem (files copied/renamed) still
    loads when the folder holds exactly one usable .channels.json (#101)."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "renamed-copy.ti2", locs)
    _engine_channels(tmp_path / "original.channels.json", locs)
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        dlg._set_chart(tmp_path / "renamed-copy.ti2")
        assert dlg._layout is not None
        assert dlg._chart_reject_reason is None
        assert "4 patches" in dlg._chart_note.text()
    finally:
        dlg.deleteLater()


def test_matching_sidecar_still_wins(_app, tmp_path):
    """The exact-stem sidecar is preferred; the fallback only fires when it's
    missing (#101)."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "mychart.ti2", locs)
    _engine_channels(tmp_path / "mychart.channels.json", locs)
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        dlg._set_chart(tmp_path / "mychart.ti2")
        assert dlg._layout is not None and dlg._chart_reject_reason is None
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Nelson — name the profile yourself + install it for the user
# ---------------------------------------------------------------------------

def test_profile_name_renames_ti3_and_description(_app, tmp_path):
    """A chosen profile name drives the .icc file name (via the .ti3 stem
    colprof names it after) and the embedded description."""
    dlg = _dialog(_app)
    try:
        src = tmp_path / "Moab_Satin_240-p1s1-scanner.ti3"
        src.write_text("CTI3\n")
        dlg._prof_name.setText("Epson ET-8550 scanner")
        ti3, desc = dlg._apply_profile_name(src)
        assert ti3.name == "Epson ET-8550 scanner.ti3"
        assert ti3.is_file()
        assert desc == "Epson ET-8550 scanner"
        # Empty field → untouched, defaults kept.
        dlg._prof_name.setText("")
        ti3, desc = dlg._apply_profile_name(src)
        assert ti3 == src and desc is None
        # Filesystem-hostile characters are stripped from the stem only.
        dlg._prof_name.setText('bad/name: "scanner?"')
        ti3, desc = dlg._apply_profile_name(src)
        assert "/" not in ti3.name and ":" not in ti3.name
        assert desc == 'bad/name: "scanner?"'
    finally:
        dlg.deleteLater()


def test_install_profile_copies_into_user_dir(_app, tmp_path, monkeypatch):
    import ui.dialogs.scanin_dialog as M
    dest_dir = tmp_path / "ColorSync" / "Profiles"
    monkeypatch.setattr(M, "_user_profile_dir", lambda: dest_dir)
    dlg = _dialog(_app)
    try:
        icc = tmp_path / "Epson ET-8550 scanner.icc"
        icc.write_bytes(b"\0" * 2048)
        dlg._last_profile = icc
        dlg._install_profile()
        assert (dest_dir / icc.name).is_file()
        assert "installed" in dlg._log.toPlainText().lower()
    finally:
        dlg.deleteLater()


def test_explicit_ti2_pick_is_not_swapped_for_sibling_ti3(_app, tmp_path):
    """Knut picked Printer.ti2 but the field showed Printer.ti3 — an unrelated
    scanner .ti3 sharing the folder was silently preferred (#101). The picked
    file wins; the sibling only backs a '-verify' style indirect pick."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "Printer.ti2", locs)
    (tmp_path / "Printer.ti3").write_text("CTI3\n")   # stale plain-scanin ti3
    _engine_channels(tmp_path / "Printer.channels.json", locs)
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        dlg._set_chart(tmp_path / "Printer.ti2")
        assert dlg._ti3 == tmp_path / "Printer.ti2"   # the pick, not the swap
        assert "Printer.ti2" in dlg._ti3_field.text()
        assert dlg._chart_measured is False
        assert dlg._layout is not None                # loads fine off the .ti2
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# #105 — printer mode accepts a chart's own printtarg .cht files
# ---------------------------------------------------------------------------

def _tiny_cht(path, locs, origin_x=10.0):
    """A minimal printtarg-style .cht page holding an X box per loc."""
    xlines = "\n".join(
        f"X {loc} {origin_x + i * 12:.1f} 10.0 10.0 10.0"
        for i, loc in enumerate(locs))
    path.write_text(f"""BOXES {len(locs)}
F _ _ 0.0 0.0 100.0 0.0 100.0 40.0 0.0 40.0
{xlines}

BOX_SHRINK 2.0
""")


def test_byo_cht_flow_loads_chart_without_sidecar(_app, tmp_path):
    """Printer mode + a chart made outside ChromIQ (no .channels.json): the
    pick enters the awaiting state instead of rejecting, and supplying the
    per-page .cht files loads the layout, pages and grid (#105)."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "Printer.ti2", locs)
    _tiny_cht(tmp_path / "Printer_01.cht", locs[:2])
    _tiny_cht(tmp_path / "Printer_02.cht", locs[2:])
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        assert dlg._byo_row_w.isVisibleTo(dlg)
        dlg._set_chart(tmp_path / "Printer.ti2")
        assert dlg._byo_awaiting and dlg._layout is None
        assert ".channels.json" in dlg._chart_note.text()
        # Dead scan-Browse points at the .cht row, not a generic hint.
        n = len(dlg._log.toPlainText())
        dlg._pick_scan()
        assert "Chart geometry" in dlg._log.toPlainText()[n:]
        # Supply the pages (stub only the file dialog).
        from PyQt6.QtWidgets import QFileDialog
        chts = [str(tmp_path / "Printer_01.cht"), str(tmp_path / "Printer_02.cht")]
        orig = QFileDialog.getOpenFileNames
        QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: (chts, ""))
        try:
            dlg._pick_byo_cht()
        finally:
            QFileDialog.getOpenFileNames = orig
        assert not dlg._byo_awaiting
        assert dlg._layout["engine"] == "printtarg"
        assert dlg._pages == [0, 1]
        assert dlg._chart_reject_reason is None
        # The per-page .cht + .cie were written next to the chart for scanin.
        assert (tmp_path / "Printer.cie").is_file()
        assert (tmp_path / "Printer_01.cht").is_file()
        # And the printer run wires each page to its own written .cht.
        for pg in (0, 1):
            shots = dlg._page_shots(pg)
            shots.clear()
            shots.append({"path": tmp_path / f"scan{pg + 1}.tif",
                          "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
            (tmp_path / f"scan{pg + 1}.tif").write_bytes(b"II*\0")
        dlg._printer_scan_profile = tmp_path / "scanner.icc"
        dlg._run_job = lambda i: None
        dlg._execute_printer(tmp_path / "Printer", 0.9)
        scanins = [j for j in dlg._jobs if j["kind"] == "scanin"]
        assert len(scanins) == 2
    finally:
        dlg.deleteLater()


def test_byo_cht_wrong_pages_rejected_but_retryable(_app, tmp_path):
    """A wrong/missing .cht page fails loudly with the coverage error and the
    awaiting state survives, so a corrected pick can still succeed (#105)."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "Printer.ti2", locs)
    _tiny_cht(tmp_path / "Printer_01.cht", locs[:2])   # page 2 missing
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        dlg._set_chart(tmp_path / "Printer.ti2")
        from PyQt6.QtWidgets import QFileDialog
        orig = QFileDialog.getOpenFileNames
        QFileDialog.getOpenFileNames = staticmethod(
            lambda *a, **k: ([str(tmp_path / "Printer_01.cht")], ""))
        try:
            dlg._pick_byo_cht()
        finally:
            QFileDialog.getOpenFileNames = orig
        assert dlg._layout is None
        assert dlg._byo_awaiting                      # retry stays possible
        assert "cover" in dlg._chart_reject_reason
    finally:
        dlg.deleteLater()


def test_byo_state_resets_on_new_chart_pick(_app, tmp_path):
    """Picking a proper ChromIQ chart after a BYO one clears the awaiting state
    and the .cht field (#105)."""
    locs = ["A1", "A2", "A3", "A4"]
    _tiny_ti2(tmp_path / "outside.ti2", locs)
    _tiny_ti2(tmp_path / "chromiq.ti2", locs)
    _engine_channels(tmp_path / "chromiq.channels.json", locs)
    dlg = _dialog(_app)
    try:
        dlg._printer_cb.setChecked(True)
        dlg._set_chart(tmp_path / "outside.ti2")
        # note: the single usable sidecar in the folder belongs to chromiq —
        # the #101 folder fallback adopts it only when the locs align, which
        # they do here, so use a folder without any sidecar for the BYO leg.
        sub = tmp_path / "loose"; sub.mkdir()
        _tiny_ti2(sub / "outside.ti2", locs)
        dlg._set_chart(sub / "outside.ti2")
        assert dlg._byo_awaiting
        dlg._set_chart(tmp_path / "chromiq.ti2")
        assert not dlg._byo_awaiting
        assert dlg._layout is not None
        assert dlg._byo_field.text() == ""
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# #108 — scan previews must survive real scanner output
# ---------------------------------------------------------------------------

def test_load_scan_qimage_survives_allocation_limit(_app, tmp_path):
    """Qt silently nulls images whose decode exceeds its allocation limit —
    Knut's 16-bit A4 scans at 600 dpi. The loader lifts the limit (#108)."""
    from PyQt6.QtGui import QImage, QImageReader
    import ui.dialogs.scanin_dialog as M
    img = QImage(800, 600, QImage.Format.Format_RGBX64)   # 3.8 MB decoded
    img.fill(0xFFFF0000)
    p = tmp_path / "scan16.tif"
    assert img.save(str(p))
    old = QImageReader.allocationLimit()
    QImageReader.setAllocationLimit(1)          # force the failure mode
    try:
        assert QImage(str(p)).isNull()          # the old code path: empty marquee
        loaded = M._load_scan_qimage(p)
        assert not loaded.isNull()
        assert loaded.width() == 800
    finally:
        QImageReader.setAllocationLimit(old)


def test_page_hint_counts_picked_scans(_app, tmp_path):
    """Multi-page charts show "one scan per page — k of n picked" so a missing
    page is obvious before Build (#108)."""
    from pathlib import Path as P
    dlg = _dialog(_app)
    try:
        dlg._ti3 = P("/tmp/proj/mychart.ti3")
        dlg._layout = {"patches": [{"page": 0}, {"page": 1}]}
        dlg._pages = [0, 1]
        dlg._refresh_shot_bar()
        assert "0 of 2" in dlg._page_hint.text()
        dlg._page_shots(0).clear()
        dlg._page_shots(0).append({"path": P("/tmp/proj/s1.tif"),
                                   "corners": None})
        dlg._refresh_shot_bar()
        assert "1 of 2" in dlg._page_hint.text()
    finally:
        dlg.deleteLater()


def test_printer_checkbox_sits_above_chart_row(_app):
    """The printer-mode switch changes the labels/fields below it, so it must
    come first in the ChromIQ box (Knut, #108)."""
    dlg = _dialog(_app)
    try:
        v = dlg._chromiq_box.layout()
        def _index_of(w):
            for i in range(v.count()):
                item = v.itemAt(i)
                if item.widget() is w:
                    return i
                lay = item.layout()
                if lay is not None:
                    for j in range(lay.count()):
                        if lay.itemAt(j).widget() is w:
                            return i
            return -1
        assert _index_of(dlg._printer_cb) < _index_of(dlg._chart_label)
        # The page selector moved out to the shared form, above the scan field.
        assert _index_of(dlg._page_widget) == -1
        assert dlg._page_widget.parent() is not None
    finally:
        dlg.deleteLater()


def test_printer_mode_hides_averaging_and_says_first_scan_wins(_app, tmp_path):
    """Printer mode reads one scan per page — the averaging affordances hide
    (extra shots were silently ignored before), and a run with leftover extra
    shots says so in the log (Knut's question)."""
    from pathlib import Path as P
    dlg = _dialog(_app)
    try:
        dlg._ti3 = tmp_path / "mychart.ti2"
        (tmp_path / "mychart.ti2").write_text("CTI2\n")
        dlg._layout = {"patches": [{"page": 0}]}
        dlg._pages = [0]
        shots = dlg._page_shots(0)
        shots.clear()
        for k in (1, 2):
            (tmp_path / f"s{k}.tif").write_bytes(b"II*\0")
            shots.append({"path": tmp_path / f"s{k}.tif",
                          "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
        dlg._printer_cb.setChecked(True)
        assert not dlg._add_shot_btn.isVisibleTo(dlg)
        assert not dlg._avg_row_w.isVisibleTo(dlg)
        dlg._printer_scan_profile = tmp_path / "scanner.icc"
        dlg._run_job = lambda i: None
        dlg._execute_printer(tmp_path / "mychart", 0.9)
        assert "first scan of each page" in dlg._log.toPlainText()
        # Scanner mode gets the button back.
        dlg._printer_cb.setChecked(False)
        assert dlg._add_shot_btn.isVisibleTo(dlg)
    finally:
        dlg.deleteLater()


def test_busy_bar_shows_steps_and_stops_on_finish(_app, tmp_path):
    """The busy bar (Knut: 'nothing moves for a long time') names the running
    step and disappears when the run ends."""
    from pathlib import Path as P
    dlg = _dialog(_app)
    try:
        dlg._ti3 = P("/tmp/proj/mychart.ti3")
        dlg._layout = {"patches": [{"page": 0}]}
        dlg._pages = [0]
        shots = dlg._page_shots(0)
        shots.clear()
        shots.append({"path": P("/tmp/proj/s1.tif"),
                      "corners": [(0, 0), (9, 0), (9, 9), (0, 9)]})
        dlg._set_busy(True)
        assert dlg._busy_bar.isVisibleTo(dlg)
        scan_runs = []
        dlg._scanin.run = lambda params, on_line, on_finish: scan_runs.append(params)
        dlg._execute()
        assert "Step 1 of 2" in dlg._busy_bar._label
        dlg._finish(True)
        assert not dlg._busy_bar.isVisibleTo(dlg)
        assert not dlg._busy_tick.isActive()
    finally:
        dlg.deleteLater()
