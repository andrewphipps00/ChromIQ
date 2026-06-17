"""#68 follow-ups: soft-separator name field, editor TD-transfer, paper-name in
the suggested name, and the locked-prefix toggle used by the Save dialogs."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    from PyQt6.QtCore import QSettings
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    return s


def test_soft_separator_no_trailing_dash(qapp):
    from ui.widgets import PrefixLockedLineEdit
    e = PrefixLockedLineEdit()
    e.set_prefix("i1Pro-A4-484p-1page-Portrait")
    assert e.text() == "i1Pro-A4-484p-1page-Portrait"      # no dangling '-'
    e.set_tail("Baryta")
    assert e.text() == "i1Pro-A4-484p-1page-Portrait-Baryta"
    e.set_tail("")
    assert e.text() == "i1Pro-A4-484p-1page-Portrait"      # dash vanishes again
    # A trailing dash on the supplied prefix is tolerated (callers may pass one).
    e.set_prefix("i1Pro-A4-")
    assert e.text() == "i1Pro-A4"


def test_toggle_locked_prefix_keeps_name(qapp):
    # Turning the option off leaves the full name editable (not blank), and back
    # on doesn't duplicate the descriptive head (#68).
    from ui.widgets import PrefixLockedLineEdit
    from ui.dialogs.ti2_relayout_dialog import _toggle_locked_prefix
    e = PrefixLockedLineEdit()
    pfx = "ColorMunki-A3-1196p-1page-Landscape"
    _toggle_locked_prefix(e, True, pfx)
    e.set_tail("v2")
    assert e.text() == pfx + "-v2"
    _toggle_locked_prefix(e, False, pfx)
    assert e.text() == pfx + "-v2"          # not emptied
    _toggle_locked_prefix(e, True, pfx)
    assert e.text() == pfx + "-v2"          # not doubled
    assert e.text().count("ColorMunki") == 1


def test_suggested_name_uses_paper_name_not_mm(qapp, settings):
    # #68 (Knut): A3+ must appear as "A3+", not its "483x329" mm code.
    import workflow.ti2_relayout as R
    from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
    d = Ti2RelayoutDialog(ArgyllRunner(settings), settings)
    spec = R.ChartSpec.new(instrument_flag="CM", paper_flag="483x329")
    spec.paper_mm = (483.0, 329.0)          # landscape A3+
    d._set_chart(spec, [(50.0, 50.0, 50.0)] * 1196, "x")
    name = d._suggest_chart_name()
    assert "A3+" in name and "483x329" not in name
    assert name.startswith("ColorMunki-A3+-1196p")


def test_editor_td_sync_keeps_loaded_scale_margin(qapp, settings):
    # #68 (Knut): loading a triple-density chart must keep its own -a / -m, not
    # clobber them with the TD preset 1.3 / 5.
    import workflow.ti2_relayout as R
    from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
    d = Ti2RelayoutDialog(ArgyllRunner(settings), settings)
    spec = R.ChartSpec.new(instrument_flag="CM", paper_flag="A4")
    d._spec = spec
    d._options = R.LayoutOptions(patch_scale=1.04, margin_mm=6, triple_density=True)
    d._sync_printtarg_widgets()
    assert abs(d._pt_a.value() - 1.04) < 1e-6
    assert d._pt_m.value() == 6
    assert d._pt_td.isChecked()
