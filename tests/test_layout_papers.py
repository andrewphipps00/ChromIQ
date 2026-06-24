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
