"""Editor "Add…" dialog (#46): single colour or generated colour sets."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.dialogs.ti2_relayout_dialog import _AddPatchesDialog, _NewChartDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


def test_default_is_single_colour_mode(qapp):
    dlg = _AddPatchesDialog(_FakeSettings())
    assert dlg._add_mode_single.isChecked()
    # The generate panel is disabled until "Generate colour sets" is picked.
    assert not dlg._gen_panel.isEnabled()


def test_single_colour_returns_one_patch(qapp):
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._single_rgb = (100.0, 0.0, 0.0)
    dlg._on_add()
    assert dlg.result_program == [(100.0, 0.0, 0.0)]


def test_generate_mode_returns_generated_program(qapp):
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    assert dlg._gen_panel.isEnabled()
    # Only the cube, 4 per axis -> 64 patches.
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(4)
    dlg._update_gen_counts()
    dlg._on_add()
    assert dlg.result_program is not None
    assert len(dlg.result_program) == 64


def test_generate_choices_persist_to_settings(qapp):
    s = _FakeSettings()
    dlg = _AddPatchesDialog(s)
    dlg._add_mode_gen.setChecked(True)
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_skin.setChecked(True)
    dlg._on_add()
    saved = s.get("new_chart_gen")
    assert isinstance(saved, dict)
    assert saved["cb"]["skin"] is True and saved["cb"]["cube"] is False
    # A second Add dialog restores those colour-set choices.
    dlg2 = _AddPatchesDialog(s)
    assert dlg2._gen_skin.isChecked() and not dlg2._gen_cube.isChecked()


def test_add_dialog_shares_newchart_gen_state_without_clobbering_chart(qapp):
    """Saving from the Add dialog must not wipe the New-chart dialog's saved
    instrument / paper / layout — only the colour-set sub-state is touched."""
    s = _FakeSettings()
    s.set("new_chart_gen", {"instr": "3p", "paper": "Letter",
                            "cb": {"cube": True}, "sp": {"cube_n": 8}})
    dlg = _AddPatchesDialog(s)
    dlg._add_mode_gen.setChecked(True)
    dlg._gen_cube.setChecked(True)
    dlg._on_add()
    saved = s.get("new_chart_gen")
    assert saved["instr"] == "3p" and saved["paper"] == "Letter"


def test_newchart_generate_program_unaffected_by_refactor(qapp, tmp_path):
    """Regression: the New-chart dialog still builds a program from its panel."""
    dlg = _NewChartDialog(tmp_path, _FakeSettings())
    dlg._mode_generate.setChecked(True)
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(3)
    dlg._update_gen_counts()
    assert len(dlg._build_generated_program()) == 27


def test_add_with_no_chart_open_seeds_a_chart_and_previews(qapp, monkeypatch):
    """#46 follow-up: adding patches with nothing loaded must seed a fresh
    chart (so a preview renders), not silently fill a grid with no spec."""
    from core.argyll_runner import ArgyllRunner
    from core.settings import AppSettings
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QDialog
    import ui.dialogs.ti2_relayout_dialog as M

    settings = AppSettings()
    # IniFormat (not the native Windows registry) so clear() never hits
    # "key marked for deletion" registry warnings on Windows.
    settings._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                             "chromiq-test", "add-patches")
    settings._qs.clear()
    editor = M.Ti2RelayoutDialog(ArgyllRunner(settings), settings)
    assert editor._spec is None

    # Stub the Add dialog to "return" two generated colours, and stub the
    # render so the test doesn't shell out to printtarg.
    class _StubAdd:
        def __init__(self, *a, **k):
            self.result_program = [(0.0, 0.0, 0.0), (100.0, 100.0, 100.0)]

        def exec(self):
            return QDialog.DialogCode.Accepted
    monkeypatch.setattr(M, "_AddPatchesDialog", _StubAdd)
    rendered = []
    monkeypatch.setattr(editor, "_regenerate", lambda **k: rendered.append(k))

    editor._add_patch()
    assert editor._spec is not None          # a chart was seeded
    assert editor._grid.count() == 2         # patches landed in the grid
    assert rendered                          # initial preview was kicked off
