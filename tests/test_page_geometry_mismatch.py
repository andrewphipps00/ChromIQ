"""Paper-size mismatch warning (Confirm Print Settings)."""
from workflow.page_geometry import check_size_mismatch

_PT = 72.0 / 25.4   # mm → points


def _pt(mm):
    return mm * _PT


# A4 sheet and a typical laser printer's smaller imageable area.
A4 = (_pt(210), _pt(297))
A4_IMAGEABLE = (_pt(200), _pt(287))
A3 = (_pt(297), _pt(420))


def test_full_sheet_chart_on_matching_paper_no_warning():
    """#84: a full-bleed (-M) 210×297 chart on A4 paper must NOT warn, even
    though it exceeds the printer's smaller imageable area."""
    assert check_size_mismatch(*A4, *A4, imageable_pt=A4_IMAGEABLE) is None


def test_chart_cropped_to_imageable_no_warning():
    """An -m chart rasterised to the printable area also matches."""
    assert check_size_mismatch(*A4_IMAGEABLE, *A4, imageable_pt=A4_IMAGEABLE) is None


def test_rotated_chart_no_warning():
    """A landscape chart on portrait paper fits after auto-rotation."""
    assert check_size_mismatch(A4[1], A4[0], *A4, imageable_pt=A4_IMAGEABLE) is None


def test_genuine_wrong_paper_warns():
    """An A3 chart on A4 paper matches neither the sheet nor the imageable area."""
    msg = check_size_mismatch(*A3, *A4, imageable_pt=A4_IMAGEABLE)
    assert msg is not None
    assert "mismatch" in msg.lower()


def test_no_ppd_falls_back_to_sheet():
    """Without an imageable area, the full sheet is the reference."""
    assert check_size_mismatch(*A4, *A4) is None
    assert check_size_mismatch(*A3, *A4) is not None
