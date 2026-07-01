"""Building a scanner target (.cht + .cie) from an engine chart's geometry +
its measured .ti3 (#97)."""
import json

import pytest

from workflow import scanin_target as ST


def _channels(tmp_path, patches, dpi=100, paper=(210.0, 297.0), engine="chromiq"):
    doc = {"layout": {"engine": engine, "dpi": dpi, "paper_mm": list(paper),
                      "patches": patches}}
    p = tmp_path / "chart.channels.json"
    p.write_text(json.dumps(doc))
    return p


def _ti3(tmp_path, rows):
    """rows = list of (loc, R,G,B, X,Y,Z) — written in the given order."""
    body = "".join(
        f'{i + 1} "{loc}" {r} {g} {b} {x} {y} {z}\n'
        for i, (loc, r, g, b, x, y, z) in enumerate(rows))
    txt = (
        "CTI3\n\n"
        'KEYWORD "SAMPLE_LOC"\n'
        "NUMBER_OF_FIELDS 8\nBEGIN_DATA_FORMAT\n"
        "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
        f"NUMBER_OF_SETS {len(rows)}\nBEGIN_DATA\n{body}END_DATA\n")
    p = tmp_path / "chart.ti3"
    p.write_text(txt)
    return p


def _patch(page, slot, loc, x, y, w=100, h=100):
    return {"page": page, "slot": slot, "loc": loc, "x": x, "y": y, "w": w, "h": h}


def test_single_page_writes_pair_with_measured_expected(tmp_path):
    patches = [_patch(0, 0, "A01", 0, 0), _patch(0, 1, "A02", 0, 120)]
    ch = _channels(tmp_path, patches)
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 95.0, 100.0, 108.0),
                          ("A02", 100, 0, 0, 41.2, 21.3, 1.9)])
    res = ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")

    assert res.n_pages == 1 and res.n_patches == 2
    assert len(res.cht_paths) == 1 and res.cht_paths[0].name == "chart.cht"
    cht = res.cht_paths[0].read_text()
    assert "  F _ _ " in cht                       # fiducial line present
    # EXPECTED carries the *measured* XYZ, keyed by loc
    assert "  A01 95.000000 100.000000 108.000000" in cht
    cie = res.cie_path.read_text()
    assert "A02 41.200000 21.300000 1.900000" in cie


def test_measurement_order_irrelevant(tmp_path):
    """A randomised chart measures in a different order — matching is by loc, so
    the reference values must still land on the right patch."""
    patches = [_patch(0, 0, "A01", 0, 0), _patch(0, 1, "A02", 0, 120)]
    ch = _channels(tmp_path, patches)
    ti3 = _ti3(tmp_path, [("A02", 100, 0, 0, 41.2, 21.3, 1.9),   # reversed order
                          ("A01", 100, 100, 100, 95.0, 100.0, 108.0)])
    res = ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")
    cie = res.cie_path.read_text()
    assert "A01 95.000000 100.000000 108.000000" in cie
    assert "A02 41.200000 21.300000 1.900000" in cie


def test_multi_page_one_cht_per_page_one_cie(tmp_path):
    patches = [_patch(0, 0, "A01", 0, 0), _patch(0, 1, "A02", 0, 120),
               _patch(1, 2, "B01", 0, 0), _patch(1, 3, "B02", 0, 120)]
    ch = _channels(tmp_path, patches)
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 95, 100, 108),
                          ("A02", 100, 0, 0, 41, 21, 2),
                          ("B01", 0, 100, 0, 36, 71, 12),
                          ("B02", 0, 0, 100, 18, 7, 95)])
    res = ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")

    assert res.n_pages == 2
    names = sorted(p.name for p in res.cht_paths)
    assert names == ["chart_01.cht", "chart_02.cht"]
    # page 1 cht holds only its own patches
    p1 = (tmp_path / "chart_01.cht").read_text()
    assert " A01 A01 " in p1 and " B01 B01 " not in p1
    # one whole-chart cie covering all four patches
    cie = res.cie_path.read_text()
    assert "NUMBER_OF_SETS 4" in cie
    for loc in ("A01", "A02", "B01", "B02"):
        assert f"{loc} " in cie


def test_missing_measurement_raises(tmp_path):
    patches = [_patch(0, 0, "A01", 0, 0), _patch(0, 1, "A02", 0, 120)]
    ch = _channels(tmp_path, patches)
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 95, 100, 108)])  # A02 unmeasured
    with pytest.raises(ST.GeometryMismatch):
        ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")


def test_non_engine_chart_raises(tmp_path):
    ch = _channels(tmp_path, [_patch(0, 0, "A01", 0, 0)], engine="printtarg")
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 95, 100, 108)])
    with pytest.raises(ST.NotAnEngineChart):
        ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")


def test_missing_channels_raises(tmp_path):
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 95, 100, 108)])
    with pytest.raises(ST.NotAnEngineChart):
        ST.build_scanin_target_from_paths(tmp_path / "nope.json", ti3,
                                          tmp_path / "chart")


def test_real_engine_geometry_end_to_end(tmp_path):
    """Drive the orchestrator off a *real* engine build: its channels.json
    geometry + the chart's own SAMPLE_LOCs (via the .ti2 standing in for a
    measurement). Proves the synthetic patch dicts above match production."""
    import random
    import shutil

    from workflow.layout_engine import chart as le_chart
    import workflow.ti2_relayout as R

    random.seed(5)
    prog = [(random.random() * 100, random.random() * 100, random.random() * 100)
            for _ in range(60)]
    R.write_ti1(R.ChartSpec.new("i1", "A4"), prog, tmp_path / "s.ti1")
    le_chart.build_chart(str(tmp_path / "s.ti1"), tmp_path / "chart",
                         instrument="i1", paper="A4", dpi=120, randomize=False)

    # Fold strips.json into channels.json exactly like _embed_layout_geometry.
    strips = json.loads((tmp_path / "chart.strips.json").read_text())
    strips["engine"] = "chromiq"
    (tmp_path / "chart.channels.json").write_text(json.dumps({"layout": strips}))

    # The .ti2 carries RGB + aim XYZ + SAMPLE_LOC — a valid stand-in .ti3 whose
    # locs are the *real* engine locs, so alignment must hold end to end.
    ti3 = tmp_path / "chart.ti3"
    shutil.copy(tmp_path / "chart.ti2", ti3)

    res = ST.build_scanin_target_from_paths(
        tmp_path / "chart.channels.json", ti3, tmp_path / "chart")
    assert res.n_patches == len({p["loc"] for p in strips["patches"]})
    assert all(p.is_file() for p in res.cht_paths) and res.cie_path.is_file()
    assert "  F _ _ " in res.cht_paths[0].read_text()


def test_is_engine_geometry_gate(tmp_path):
    """The offer-gate: True for an engine chart (even before any .ti3 exists),
    False for non-engine / missing sidecars. Never raises."""
    (tmp_path / "e").mkdir()
    (tmp_path / "p").mkdir()
    eng = _channels(tmp_path / "e", [_patch(0, 0, "A01", 0, 0)])
    non = _channels(tmp_path / "p", [_patch(0, 0, "A01", 0, 0)], engine="printtarg")
    assert ST.is_engine_geometry(eng) is True
    assert ST.is_engine_geometry(non) is False
    assert ST.is_engine_geometry(tmp_path / "missing.channels.json") is False


def test_overwrite_reflects_latest_measurement(tmp_path):
    """Re-measuring and rebuilding must overwrite the old .cie (never stale)."""
    patches = [_patch(0, 0, "A01", 0, 0)]
    ch = _channels(tmp_path, patches)
    ti3 = _ti3(tmp_path, [("A01", 100, 100, 100, 50.0, 50.0, 50.0)])
    ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")
    # a better read of the same patch
    ti3.write_text(ti3.read_text().replace("50.0 50.0 50.0", "95.0 100.0 108.0"))
    res = ST.build_scanin_target_from_paths(ch, ti3, tmp_path / "chart")
    cie = res.cie_path.read_text()
    assert "95.000000 100.000000 108.000000" in cie
    assert "50.000000 50.000000 50.000000" not in cie
