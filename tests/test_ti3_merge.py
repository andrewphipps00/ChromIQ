"""Pre-conditioning measurement merge (ChromIQ-style refinement).

Covers workflow.ti3_merge: combining a pre-conditioning profile's measurement
data into a freshly measured .ti3 so colprof builds from the larger set.

Invariants exercised here:
  * fresh patches stay first as SAMPLE_ID 1..N with their SAMPLE_LOC intact;
  * pre patches are appended, renumbered, and have SAMPLE_LOC neutralised to "0"
    so they cannot masquerade as a strip in Check & Refine;
  * NUMBER_OF_SETS is corrected and CHROMIQ_FRESH_COUNT records N;
  * a colour-space / data-format mismatch is refused, not silently merged.
"""
from __future__ import annotations

import pytest

from workflow.ti3_merge import (
    FRESH_COUNT_KEYWORD,
    Ti3MergeError,
    merge_preconditioning,
    read_fresh_count,
)


def _ti3(color_rep: str, fmt: str, rows: list[str]) -> str:
    body = "\n".join(rows)
    return (
        "CTI3\n\n"
        'DESCRIPTOR "test"\n'
        'ORIGINATOR "test"\n'
        f'COLOR_REP "{color_rep}"\n'
        "\n"
        f"NUMBER_OF_FIELDS {len(fmt.split())}\n"
        "BEGIN_DATA_FORMAT\n"
        f"{fmt}\n"
        "END_DATA_FORMAT\n"
        "\n"
        f"NUMBER_OF_SETS {len(rows)}\n"
        "BEGIN_DATA\n"
        f"{body}\n"
        "END_DATA\n"
    )


_FMT = "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z"


def _write(tmp_path, name, color_rep, rows):
    p = tmp_path / name
    p.write_text(_ti3(color_rep, _FMT, rows))
    return p


def _data_rows(start, count, loc_prefix):
    return [
        f'{i} "{loc_prefix}{i}" 10 20 30 11.0 12.0 13.0'
        for i in range(start, start + count)
    ]


def _read_data(path):
    lines = path.read_text().splitlines()
    b = lines.index("BEGIN_DATA")
    e = lines.index("END_DATA")
    return [ln for ln in lines[b + 1:e] if ln.strip()]


def test_merge_combines_and_renumbers(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 3, "A"))
    pre = _write(tmp_path, "pre.json", "iRGB_XYZ", _data_rows(1, 2, "B"))
    out = tmp_path / "merged.ti3"

    total = merge_preconditioning(fresh, pre, out)
    assert total == 5

    rows = _read_data(out)
    assert len(rows) == 5
    # SAMPLE_IDs run 1..5 with no gaps or repeats
    assert [r.split()[0] for r in rows] == ["1", "2", "3", "4", "5"]
    # NUMBER_OF_SETS header matches
    assert any(ln.strip() == "NUMBER_OF_SETS 5" for ln in out.read_text().splitlines())


def test_fresh_locs_kept_pre_locs_neutralised(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 2, "A"))
    pre = _write(tmp_path, "pre.json", "iRGB_XYZ", _data_rows(1, 2, "B"))
    out = tmp_path / "merged.ti3"
    merge_preconditioning(fresh, pre, out)

    rows = _read_data(out)
    locs = [r.split()[1].strip('"') for r in rows]
    # first 2 = fresh, keep their A-labels; last 2 = pre, neutralised
    assert locs[0].startswith("A") and locs[1].startswith("A")
    assert locs[2] == "0" and locs[3] == "0"


def test_fresh_count_marker_roundtrip(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 4, "A"))
    pre = _write(tmp_path, "pre.json", "iRGB_XYZ", _data_rows(1, 3, "B"))
    out = tmp_path / "merged.ti3"
    merge_preconditioning(fresh, pre, out)

    assert FRESH_COUNT_KEYWORD in out.read_text()
    assert read_fresh_count(out) == 4
    # an ordinary, un-merged file has no marker
    assert read_fresh_count(fresh) is None


def test_color_rep_mismatch_refused(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 2, "A"))
    pre = _write(tmp_path, "pre.json", "iRGB_LAB", _data_rows(1, 2, "B"))
    out = tmp_path / "merged.ti3"
    with pytest.raises(Ti3MergeError):
        merge_preconditioning(fresh, pre, out)
    assert not out.exists()


def test_data_format_mismatch_refused(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 2, "A"))
    # pre file with a different (spectral-ish) format
    pre = tmp_path / "pre.json"
    pre.write_text(_ti3("iRGB_XYZ", _FMT + " SPEC_380", ['1 "B1" 10 20 30 11 12 13 0.5']))
    out = tmp_path / "merged.ti3"
    with pytest.raises(Ti3MergeError):
        merge_preconditioning(fresh, pre, out)


def test_missing_data_block_refused(tmp_path):
    fresh = _write(tmp_path, "fresh.ti3", "iRGB_XYZ", _data_rows(1, 2, "A"))
    bad = tmp_path / "bad.json"
    bad.write_text("CTI3\nnot a real measurement file\n")
    out = tmp_path / "merged.ti3"
    with pytest.raises(Ti3MergeError):
        merge_preconditioning(fresh, bad, out)
