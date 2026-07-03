"""Tests for the human-readable paper-size helper (reuses data.patch_db)."""
from workflow.layout_engine import papers


def test_dimensions_named():
    assert papers.dimensions_mm("A4") == (210.0, 297.0)
    assert papers.dimensions_mm("A4R") == (297.0, 210.0)


def test_dimensions_custom():
    assert papers.dimensions_mm("594x420") == (594.0, 420.0)
    assert papers.parse_custom("100x150") == (100.0, 150.0)
    assert papers.parse_custom("nope") is None


def test_label_is_human_readable():
    assert "210" in papers.label("A4")
    assert "Portrait" in papers.label("A4")
    # custom size falls back to the code
    assert papers.label("100x150") == "100x150"


def test_list_papers_uses_app_order_and_exclusions():
    all_codes = [c for c, _, _ in papers.list_papers()]
    assert "A4" in all_codes
    # i1 hides A2 portrait (EXCLUDED_PAPERS), like the dropdown elsewhere.
    i1_codes = [c for c, _, _ in papers.list_papers("i1")]
    assert "A2" not in i1_codes
    assert "A4" in i1_codes


def test_engine_offers_portrait_a2_a3_a3plus_on_strip_readers():
    """The layout engine lays patches out itself, so it offers portrait
    A2 / A3 / A3+ on i1 / p3 where printtarg's capacity preference hid them,
    while printtarg's own list still excludes them (#93)."""
    portrait = {"A2", "A3", "329x483"}  # A3+ portrait == 329x483
    for inst in ("i1", "p3", "SS", "CM"):
        eng = {c for c, _, _ in papers.list_papers(inst, for_engine=True)}
        assert portrait <= eng, (inst, portrait - eng)
    # printtarg default still hides them for the strip readers
    pt = {c for c, _, _ in papers.list_papers("i1")}
    assert not (portrait & pt)
    # genuinely physical / quality limits still apply to the engine
    ss_eng = {c for c, _, _ in papers.list_papers("SS", for_engine=True)}
    assert "594x420" not in ss_eng                      # SpectroScan bed limit
    p3_eng = {c for c, _, _ in papers.list_papers("p3", for_engine=True)}
    assert "4x6" not in p3_eng and "127x178" not in p3_eng  # too few patches


def test_engine_portrait_dimensions_are_portrait():
    for code in ("A2", "A3", "329x483"):
        w, h = papers.dimensions_mm(code)
        assert h > w, (code, w, h)
