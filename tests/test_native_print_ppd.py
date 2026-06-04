"""Tests for the vendor "no colour management" PPD backstop in
``workflow.native_print_macos``.

These exercise only the pure PPD-parsing helpers (no PyObjC / PrintCore), so
they run on any platform.
"""
import textwrap

from workflow.native_print_macos import _vendor_no_cm_setting


def _write_ppd(tmp_path, body: str):
    p = tmp_path / "printer.ppd"
    p.write_text(textwrap.dedent(body).lstrip())
    return str(p)


# Trimmed from the real installed Canon PRO-300 driver PPD (CNIJ587.ppd).  The
# only "no colour management" lever Canon exposes is value 1001 ("No Color
# Correction") on an option whose *name* is "Rendering Intent" — it has no
# "colour" in the option label, which is exactly what used to make the backstop
# miss it.
CANON_PRO300_PPD = """
    *OpenUI *CNIJMediaType/Media Type: PickOne
    *DefaultCNIJMediaType: 50
    *CNIJMediaType 50/Photo Paper Pro Platinum: ""
    *CloseUI: *CNIJMediaType
    *OpenUI *CNIJIntent2/Rendering Intent: PickOne
    *DefaultCNIJIntent2: 5
    *CNIJIntent2 5/Perceptual (Photo): ""
    *CNIJIntent2 1001/No Color Correction: ""
    *CloseUI: *CNIJIntent2
    *OpenUI *CNIJColorPatternCheckBox/View Color Pattern: PickOne
    *DefaultCNIJColorPatternCheckBox: 0
    *CNIJColorPatternCheckBox 0/OFF: ""
    *CNIJColorPatternCheckBox 1/ON: ""
    *CloseUI: *CNIJColorPatternCheckBox
"""


def test_canon_no_color_correction_on_rendering_intent(tmp_path):
    """Canon hangs its no-CM toggle off "Rendering Intent" → "No Color
    Correction"; the value alone must qualify it even though the option name
    has no "colour" in it."""
    ppd = _write_ppd(tmp_path, CANON_PRO300_PPD)
    assert _vendor_no_cm_setting(ppd) == ("CNIJIntent2", "1001")


def test_epson_cmat_still_detected(tmp_path):
    """Regression guard: the Epson path that already worked must keep working —
    "No Color Adjustment" on an "EPSON Color Controls" option."""
    ppd = _write_ppd(tmp_path, """
        *OpenUI *EPIJ_CMat/EPSON Color Controls: PickOne
        *DefaultEPIJ_CMat: 1
        *EPIJ_CMat 1/Color Controls: ""
        *EPIJ_CMat 3/No Color Adjustment: ""
        *CloseUI: *EPIJ_CMat
    """)
    assert _vendor_no_cm_setting(ppd) == ("EPIJ_CMat", "3")


def test_explicit_no_cm_value_preferred_over_generic_off(tmp_path):
    """A prio-0 explicit "no colour management" value beats a prio-1 bare
    "Off" on a colour-management option."""
    ppd = _write_ppd(tmp_path, """
        *OpenUI *ColorMatching/Color Matching: PickOne
        *DefaultColorMatching: Auto
        *ColorMatching Auto/Automatic: ""
        *ColorMatching Off/Off: ""
        *CloseUI: *ColorMatching
        *OpenUI *Intent/Rendering Intent: PickOne
        *DefaultIntent: Photo
        *Intent Photo/Perceptual: ""
        *Intent Raw/No Color Management: ""
        *CloseUI: *Intent
    """)
    assert _vendor_no_cm_setting(ppd) == ("Intent", "Raw")


def test_bare_off_on_non_cm_option_ignored(tmp_path):
    """A bare "Off" only counts on a clearly colour-management option — a
    "Duplex: Off" (or here a non-CM toggle) must not be mistaken for one."""
    ppd = _write_ppd(tmp_path, """
        *OpenUI *Duplex/Two-Sided: PickOne
        *DefaultDuplex: None
        *Duplex None/Off: ""
        *Duplex DuplexNoTumble/Long-Edge: ""
        *CloseUI: *Duplex
    """)
    assert _vendor_no_cm_setting(ppd) is None


def test_no_colour_option_returns_none(tmp_path):
    """A PPD with nothing colour-related yields no backstop setting."""
    ppd = _write_ppd(tmp_path, """
        *OpenUI *PageSize/Page Size: PickOne
        *DefaultPageSize: A4
        *PageSize A4/A4: ""
        *CloseUI: *PageSize
    """)
    assert _vendor_no_cm_setting(ppd) is None
