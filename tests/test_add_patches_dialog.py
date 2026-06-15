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


def test_fill_counts_existing_chart_patches(qapp):
    """#51: in the Add dialog, "Fill remaining gaps: N" tops the *whole* chart
    up to N — patches already on the chart count, rather than adding N more."""
    existing = [(float(i % 100), 0.0, 0.0) for i in range(60)]
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_fill.setChecked(True)
    dlg._gen_fill_to.setValue(100)
    dlg._update_gen_counts()
    assert len(dlg._build_generated_program()) == 40        # 60 there + 40 = 100


def test_fill_over_target_adds_nothing(qapp):
    existing = [(float(i % 100), 1.0, 2.0) for i in range(150)]
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_fill.setChecked(True)
    dlg._gen_fill_to.setValue(100)              # already past it
    dlg._update_gen_counts()
    assert dlg._build_generated_program() == []


def test_fill_without_existing_chart_unchanged(qapp):
    """New-chart-style use (no existing patches) still fills to the target."""
    dlg = _AddPatchesDialog(_FakeSettings())    # existing_patches defaults to []
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(3)                 # 27
    dlg._gen_fill.setChecked(True)
    dlg._gen_fill_to.setValue(100)
    dlg._update_gen_counts()
    assert len(dlg._build_generated_program()) == 100


class _StubPanel:
    """Captures what the dialog pushes to the embedded cube panel."""
    def __init__(self):
        self.pushed = None
        self.torn_down = 0

    def set_program(self, program, existing_program=None):
        self.pushed = (list(program), list(existing_program or []))

    def teardown(self):
        self.torn_down += 1


def test_cube_panel_is_embedded(qapp):
    """Both dialogs build an always-present embedded cube panel."""
    assert _AddPatchesDialog(_FakeSettings())._cube_panel is not None


def test_cube_folded_by_default_and_toggles(qapp):
    """The cube starts folded away; the toggle reveals it and remembers it."""
    s = _FakeSettings()
    dlg = _AddPatchesDialog(s)
    assert dlg._cube_shown is False
    assert dlg._fold_btn.isChecked() is False
    dlg._fold_btn.setChecked(True)                    # user reveals the cube
    assert dlg._cube_shown is True
    assert s.get("new_chart_show_cube") is True       # remembered

    # A second dialog opens with the cube already shown.
    assert _AddPatchesDialog(s)._cube_shown is True


@pytest.mark.parametrize("show_cube", [False, True])
def test_exec_builds_cube_view_before_going_modal(qapp, monkeypatch, show_cube):
    """Regression (issue #38 / app freeze): the cube's QWebEngineView must be
    realized while the dialog is still non-modal — for BOTH a folded and an
    unfolded open. Creating its native surface inside the application-modal
    dialog wedges the modal grab and freezes the whole app on Windows. So
    exec() must call panel.ensure_view() before delegating to QDialog's modal
    loop; a folded open still pre-builds it (hidden) so a later unfold reuses
    the surface instead of spawning one while modal."""
    from PyQt6.QtWidgets import QApplication, QDialog

    s = _FakeSettings()
    s.set("new_chart_show_cube", show_cube)
    dlg = _AddPatchesDialog(s)

    order = []

    class _Panel:
        def __init__(self): self._v = show_cube
        def ensure_view(self): order.append("ensure_view")
        def isVisible(self): return self._v
        def setVisible(self, v): self._v = v
        def minimumWidth(self): return 360
    dlg._cube_panel = _Panel()

    # Replace the real show / event pump / modal loop so the test neither opens
    # a window nor blocks; just record the call order.
    monkeypatch.setattr(dlg, "show", lambda: order.append("show"))
    monkeypatch.setattr(QApplication, "processEvents",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QDialog, "exec",
        lambda self: order.append("modal") or QDialog.DialogCode.Rejected.value)

    dlg.exec()
    assert "ensure_view" in order, order
    assert order.index("ensure_view") < order.index("modal"), order


def test_live_preview_pushes_existing_plus_new(qapp):
    """In generate mode the panel gets the generated program *and* the chart's
    existing patches (the merged view from the Add flow)."""
    existing = [(0.0, 0.0, 0.0), (100.0, 100.0, 100.0)]
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(3)                       # 27

    dlg._cube_panel = _StubPanel()
    dlg._cube_shown = True                            # preview unfolded
    dlg._do_push_live_preview()
    program, pushed_existing = dlg._cube_panel.pushed
    assert len(program) == 27
    assert pushed_existing == existing


def test_live_preview_shows_existing_only_outside_generate_mode(qapp):
    """In single-colour mode the panel shows just the existing patches (empty
    generated set), not a stale generated view."""
    existing = [(10.0, 10.0, 10.0)]
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    dlg._add_mode_single.setChecked(True)             # not generating

    dlg._cube_panel = _StubPanel()
    dlg._cube_shown = True                            # preview unfolded
    dlg._do_push_live_preview()
    program, pushed_existing = dlg._cube_panel.pushed
    assert program == []
    assert pushed_existing == existing


def test_done_tears_down_cube_panel(qapp):
    """Closing the dialog (any path) drains the embedded web view once."""
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._cube_panel = _StubPanel()
    dlg.reject()                                       # Cancel routes via done()
    assert dlg._cube_panel.torn_down == 1


def test_fill_counts_white_black_not_on_top(qapp):
    """Pure white & black must count toward the fill target, not stack on top:
    3 of each + fill-to-50 yields 50 total (with all 6 anchors present)."""
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_whiteblack.setChecked(True)
    dlg._gen_whiteblack_n.setValue(3)            # 3 white + 3 black
    dlg._gen_fill.setChecked(True)
    dlg._gen_fill_to.setValue(50)
    dlg._update_gen_counts()
    prog = dlg._build_generated_program()
    assert len(prog) == 50                       # counted within, not 56
    anchors = sum(1 for p in prog
                  if tuple(p) in {(100.0, 100.0, 100.0), (0.0, 0.0, 0.0)})
    assert anchors == 6                          # the repeats are kept verbatim
