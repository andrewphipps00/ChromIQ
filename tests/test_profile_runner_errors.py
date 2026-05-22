"""Parser tests for every Argyll-tool runner ChromIQ wraps — confirms each
runner captures the right structured error/warning when fed sample lines.

Fixture lines are copied from Argyll 3.5.0 with the printf format strings
concretised:
- colprof.c, printcal.c, applycal.c        (Build Profile / Calibration tab)
- targen.c, printtarg.c                    (Chart tab — via ChartCreator)
- profcheck.c                              (Check & Refine tab)
- iccgamut.c, viewgam.c                    (Check & Refine — gamut panel)
"""
from __future__ import annotations

import sys

import pytest

from PyQt6.QtCore import QCoreApplication

from workflow.applycal_runner import ApplycalRunner
from workflow.chart_creator import ChartCreator
from workflow.gamut_viewer import GamutViewer
from workflow.printcal_runner import PrintcalRunner
from workflow.profcheck_runner import ProfcheckRunner
from workflow.profile_builder import ProfileBuilder
from workflow.viewgam_runner import ViewgamRunner


# QApplication is required for the QObject-derived GamutViewer / ViewgamRunner.
@pytest.fixture(scope="module", autouse=True)
def _qapp():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    yield app


class _StubRunner:
    """Stand-in for ArgyllRunner — runner only calls run(); we never reach it."""
    def run(self, *a, **k):
        pass


class _StubSettings:
    def get(self, *a, **k):
        return None


class _StubFileManager:
    def ensure_folder(self, *a, **k):
        return None

    def clean_folder(self, *a, **k):
        return None


# ---------------------------------------------------------------------------
# ProfileBuilder (colprof)
# ---------------------------------------------------------------------------

def _make_builder() -> ProfileBuilder:
    return ProfileBuilder(_StubRunner())


@pytest.mark.parametrize("line,expected_key", [
    ("Target illuminant 'D50x' is wrong measurement type", "illum_type"),
    ("CIE illuminant 'D65y' is wrong measurement type", "illum_type"),
    ("FWA compensation only works when viewer and/or illuminant selected", "fwa_needs_illum"),
    ("CGATS file read error : Permission denied", "ti3_read"),
    ("Neither CIE nor spectral data found in file '/tmp/chart.ti3'", "ti3_empty"),
    ("Note: instrument doesn't have an FWA illuminent", "fwa_no_uv"),
])
def test_colprof_errors_classified(line: str, expected_key: str):
    b = _make_builder()
    b._scan_line(line)
    failure = b.primary_failure()
    assert failure is not None, f"no failure captured for {line!r}"
    assert failure[0] == expected_key


def test_colprof_first_error_wins():
    b = _make_builder()
    b._scan_line("CGATS file read error : Permission denied")
    b._scan_line("Neither CIE nor spectral data found in file '/tmp/x.ti3'")
    # primary_failure returns the FIRST match — the earliest reason printed.
    assert b.primary_failure()[0] == "ti3_read"


@pytest.mark.parametrize("line,expected_key", [
    ("-t perceptual intent override only works if -s srcprof or -S srcprof is used", "perc_intent_orphan"),
    ("-T saturation intent override only works if -S srcprof is used", "sat_intent_orphan"),
    ("-i illuminant ignored for emissive reference type", "illum_ignored_emissive"),
    ("-f FWA compensation ignored for emissive reference type", "fwa_ignored_emissive"),
    ("Ink limit is greater than original chart! (85% > 80%)", "ink_limit_over"),
    ("Black ink limit greater than original chart! (95% > 90%)", "kink_limit_over"),
    ("-aX not applicable to input profile, using -ax", "input_profile_alg"),
])
def test_colprof_warnings_classified(line: str, expected_key: str):
    b = _make_builder()
    b._scan_line(line)
    keys = [k for k, _ in b.captured_warnings()]
    assert expected_key in keys


def test_colprof_warnings_surface_in_sanity_check(tmp_path):
    icc = tmp_path / "fake.icc"
    icc.write_bytes(b"x" * 2000)
    b = _make_builder()
    b._scan_line("Ink limit is greater than original chart! (85% > 80%)")
    issues = b.sanity_check(icc, log_output="")
    assert any("85% > 80%" in i or "ink limit" in i.lower() for i in issues)


# ---------------------------------------------------------------------------
# PrintcalRunner
# ---------------------------------------------------------------------------

def _make_printcal() -> PrintcalRunner:
    return PrintcalRunner(_StubRunner(), _StubSettings())


@pytest.mark.parametrize("line,expected_key", [
    ("One of -i, -r -e or -I must be set", "no_mode"),
    ("CGATS file read error : Bad magic", "ti3_read"),
    ("Input file doesn't contain keyword COLOR_REPS", "ti3_no_color_reps"),
    ("COLOR_REP 'RGBW' invalid", "color_rep_invalid"),
    ("COLOR_REP 'XYZ' invalid (Neither XYZ nor LAB)", "color_rep_invalid"),
    ("No cal target '/tmp/x.cal' found for re-calibrate (file not found)", "no_prev_cal"),
    ("Reading cal target '/tmp/y.cal' failed", "prev_cal_read"),
    ("Target '/tmp/y.cal' colorspace 'CMYK' doesn't match '/tmp/x.ti3' colorspace 'RGB'", "colorspace_mismatch"),
    ("Can't find field LAB_L in '/tmp/x.cal' table 3", "cal_field_missing"),
    ("Input file doesn't contain field RGB_R", "input_field_missing"),
    ("Can't find even one white patch in '/tmp/x.ti3'", "no_white_patch"),
    ("Couldn't open '/tmp/out.cal' for writing", "write_failed"),
])
def test_printcal_errors_classified(line: str, expected_key: str):
    r = _make_printcal()
    r._scan_line(line)
    failure = r.primary_failure()
    assert failure is not None, f"no failure captured for {line!r}"
    assert failure[0] == expected_key


@pytest.mark.parametrize("line,expected_key", [
    ("COLOR_REP 'GRAY' is probably not suitable for print calibration!", "wrong_color_rep"),
    ("Command line calibration target paramers ignored on re-calibrate, verify and imitate!", "targets_ignored"),
])
def test_printcal_warnings_classified(line: str, expected_key: str):
    r = _make_printcal()
    r._scan_line(line)
    keys = [k for k, _ in r.captured_warnings()]
    assert expected_key in keys


# ---------------------------------------------------------------------------
# ApplycalRunner
# ---------------------------------------------------------------------------

def _make_applycal() -> ApplycalRunner:
    return ApplycalRunner(_StubRunner(), _StubSettings())


@pytest.mark.parametrize("line,expected_key", [
    ("Calibration space CMYK doesn't match profile RGB", "space_mismatch"),
    ("Can't apply calibration to profile of class icSigAbstractClass", "wrong_class"),
    ("Unable to read all tags: 5, malformed tag table", "icc_read"),
    ("Can't find icSigProfileDescriptionTag in profile", "no_desc_tag"),
    ("new_xcal failed", "cal_read"),
])
def test_applycal_errors_classified(line: str, expected_key: str):
    r = _make_applycal()
    r._scan_line(line)
    failure = r.primary_failure()
    assert failure is not None, f"no failure captured for {line!r}"
    assert failure[0] == expected_key


def test_applycal_space_mismatch_message_uses_both_groups():
    r = _make_applycal()
    r._scan_line("Calibration space CMYK doesn't match profile RGB")
    _, body = r.primary_failure()
    assert "'CMYK'" in body and "'RGB'" in body


def test_applycal_warning_non_input_to_input():
    r = _make_applycal()
    r._scan_line("Non-input calibration being applied to an input profile")
    keys = [k for k, _ in r.captured_warnings()]
    assert "non_input_to_input" in keys


# ---------------------------------------------------------------------------
# ChartCreator — targen + printtarg (Chart tab)
# ---------------------------------------------------------------------------

def _make_chart_creator() -> ChartCreator:
    return ChartCreator(_StubRunner(), _StubFileManager(), _StubSettings())


@pytest.mark.parametrize("line,expected_key", [
    ("ICC profile doesn't match device!", "icc_profile_mismatch"),
    ("MPP profile doesn't match device!", "mpp_profile_mismatch"),
    ("calibration curve is non-invertable", "cal_noninvertable"),
    ("Composite grey wedges aren't appropriate for RGB device", "grey_wedges_wrong_device"),
    ("Don't know how to deal with inverted colorant combination 0xff", "unknown_inverted_colorants"),
    ("N-channel must be 16 or less than channels", "too_many_channels"),
    ("Write error : Disk full", "write_error"),
])
def test_targen_errors_classified(line: str, expected_key: str):
    cc = _make_chart_creator()
    cc._scan_line("targen", line)
    failure = cc.primary_failure()
    assert failure is not None
    tool, key, _ = failure
    assert tool == "targen"
    assert key == expected_key


def test_targen_warning_profile_unchecked():
    cc = _make_chart_creator()
    cc._scan_line(
        "targen",
        "Profile '/tmp/p.icc' no. channels match, but colorant types have not been checked",
    )
    keys = [k for _, k, _ in cc.captured_warnings()]
    assert "profile_unchecked_colorants" in keys


@pytest.mark.parametrize("line,expected_key", [
    ("Paper size not long enough for target identification row (need 280.0 mm, got 210.0 mm)!", "paper_too_short_tid"),
    ("Paper size not long enough for a single patch per row!", "paper_too_short_row"),
    ("Not enough width for even one row!", "paper_too_narrow"),
    ("Unsupported instrument type", "unsupported_instrument"),
    ("Device white encoding not appropriate!", "device_encoding"),
    ("Device black encoding not appropriate!", "device_encoding"),
    ("Device CMY encoding not appropriate!", "device_encoding"),
])
def test_printtarg_errors_classified(line: str, expected_key: str):
    cc = _make_chart_creator()
    cc._scan_line("printtarg", line)
    failure = cc.primary_failure()
    assert failure is not None
    tool, key, _ = failure
    assert tool == "printtarg"
    assert key == expected_key


def test_chart_creator_distinguishes_tool_per_match():
    cc = _make_chart_creator()
    cc._scan_line("targen", "ICC profile doesn't match device!")
    cc._scan_line("printtarg", "Not enough width for even one row!")
    # primary_failure is the FIRST error captured (targen happens before printtarg)
    assert cc.primary_failure()[0] == "targen"


# ---------------------------------------------------------------------------
# ProfcheckRunner (Check & Refine tab)
# ---------------------------------------------------------------------------

def _make_profcheck() -> ProfcheckRunner:
    return ProfcheckRunner(_StubRunner())


@pytest.mark.parametrize("line,expected_key", [
    ("Target illuminant 'D50x' is wrong measurement type", "illum_type"),
    ("CIE illuminant 'D65y' is wrong measurement type", "illum_type"),
    ("CGATS file read error on '/tmp/x.ti3': bad magic", "ti3_read"),
    ("Input file '/tmp/x.ti3' doesn't contain keyword COLOR_REPS", "ti3_no_color_reps"),
    ("Device input file '/tmp/x.ti3' has unhandled color representation 'XYZW'", "unhandled_colorrep"),
    ("Input file '/tmp/x.ti3' has no sets of data", "ti3_empty"),
    ("Input file '/tmp/x.ti3' doesn't contain field SAMPLE_ID", "field_missing"),
    ("Input file doesn't contain field GRAY_W", "field_missing"),
    ("Input file '/tmp/x.ti3' field RGB_R is wrong type - expect float", "field_missing"),
])
def test_profcheck_errors_classified(line: str, expected_key: str):
    pc = _make_profcheck()
    pc._scan_line(line)
    failure = pc.primary_failure()
    assert failure is not None
    assert failure[0] == expected_key


@pytest.mark.parametrize("line,expected_key", [
    ("-i illuminant ignored for emissive reference type", "illum_ignored_emissive"),
    ("-f FWA compensation ignored for emissive reference type", "fwa_ignored_emissive"),
])
def test_profcheck_warnings_classified(line: str, expected_key: str):
    pc = _make_profcheck()
    pc._scan_line(line)
    keys = [k for k, _ in pc.captured_warnings()]
    assert expected_key in keys


# ---------------------------------------------------------------------------
# GamutViewer (iccgamut)
# ---------------------------------------------------------------------------

def _make_gamut_viewer() -> GamutViewer:
    return GamutViewer(_StubRunner())


@pytest.mark.parametrize("line,expected_key", [
    ("new_vrml faile for file '/tmp/x.x3d'", "vrml_write"),
    ("Error closing output file '/tmp/x.x3d'", "vrml_close"),
    ("Error: Profile signature wrong", "icc_read"),
])
def test_iccgamut_errors_classified(line: str, expected_key: str):
    gv = _make_gamut_viewer()
    gv._scan_line(line)
    failure = gv.primary_failure()
    assert failure is not None
    assert failure[0] == expected_key


# ---------------------------------------------------------------------------
# ViewgamRunner
# ---------------------------------------------------------------------------

def _make_viewgam() -> ViewgamRunner:
    return ViewgamRunner(_StubRunner())


@pytest.mark.parametrize("line,expected_key", [
    ("Gamuts are not compatible! (Colorspace, gamut center ?)", "gamuts_incompatible"),
    ("Input file '/tmp/a.gam' error : bad header", "gam_read"),
    ("Input file '/tmp/a.gam' read failed", "gam_read_generic"),
    ("Input file isn't a GAMUT format file", "wrong_filetype"),
    ("Input file doesn't contain field LAB_L", "gam_corrupt"),
    ("No vertices", "gam_corrupt"),
])
def test_viewgam_errors_classified(line: str, expected_key: str):
    vr = _make_viewgam()
    vr._scan_line(line)
    failure = vr.primary_failure()
    assert failure is not None
    assert failure[0] == expected_key


def test_viewgam_incompatible_message_is_friendly():
    vr = _make_viewgam()
    vr._scan_line("Gamuts are not compatible! (Colorspace, gamut center ?)")
    _, body = vr.primary_failure()
    assert "different colour spaces" in body
