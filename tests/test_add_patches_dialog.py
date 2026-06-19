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


def test_greys_zero_rings_greys_out_offset_and_counts_steps(qapp):
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_greys.setChecked(True)
    dlg._gen_greys_n.setValue(12)

    # rings > 0: offset is live, count includes the tint rings.
    dlg._gen_greys_rings.setValue(1)
    dlg._update_gen_counts()
    assert dlg._gen_greys_off.isEnabled()
    assert dlg._gen_greys_off_label.isEnabled()

    # rings == 0: pure neutral ramp, offset disabled, count == steps.
    dlg._gen_greys_rings.setValue(0)
    dlg._update_gen_counts()
    assert not dlg._gen_greys_off.isEnabled()
    assert not dlg._gen_greys_off_label.isEnabled()
    dlg._on_add()
    assert dlg.result_program is not None
    assert len(dlg.result_program) == 12
    for r, g, b in dlg.result_program:
        assert r == g == b


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


def test_total_matches_built_program_with_foreign_existing(qapp):
    # #60: the Total is the patches the current set selection produces (the
    # additions: ticked sets + white/black + fill), NOT the existing chart. The
    # built program is used rather than the per-set estimate, so white/black
    # de-dup against the chart's existing patches (which came from elsewhere —
    # a preset's .ti1) doesn't drift (the original 921-vs-924).
    import re
    import workflow.patch_generators as G
    existing = G.rgb_cube(9)  # a "foreign" chart, not built by the recipe
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_cube.setChecked(True)
    dlg._gen_whiteblack.setChecked(True)
    dlg._gen_fill.setChecked(True)
    dlg._gen_fill_to.setValue(900)
    dlg._update_gen_counts()
    dlg._do_push_live_preview()           # the debounced exact rebuild
    shown = int(re.search(r"(\d+)", dlg._gen_total.text()).group(1))
    assert shown == len(dlg._build_generated_program())   # additions only


def test_add_dialog_has_info_button_with_generator_help(qapp):
    # #66 follow-up (Knut): the Add window must carry an ⓘ, and its body must
    # include the same generator-sets help the New-chart dialog has.
    from ui.tooltip_button import TooltipButton
    from ui.dialogs.ti2_relayout_dialog import _GEN_SETS_HELP
    dlg = _AddPatchesDialog(_FakeSettings())
    tips = dlg.findChildren(TooltipButton)
    assert tips, "Add dialog has no ⓘ button"
    assert any(_GEN_SETS_HELP in t._body for t in tips), \
        "no ⓘ carries the generator-sets help"


def test_gen_sets_help_matches_new_chart_tooltip():
    # The shared generator help must stay byte-identical to the matching
    # paragraphs of the New-chart tooltip, so the two ⓘ can't drift (#66).
    from scripts.i18n_extract import extract_keys
    from ui.dialogs.ti2_relayout_dialog import _GEN_SETS_HELP
    nc = next(k for k in extract_keys()
              if k.startswith("Let's start a brand-new chart"))
    assert _GEN_SETS_HELP == "\n\n".join(nc.split("\n\n")[5:19])


def test_white_black_added_over_existing_chart(qapp):
    """#76: pure white & black are deliberate anchors — with each=2 they add
    2 white + 2 black even when the existing chart already holds white/black,
    instead of de-duping to 0."""
    existing = [(100.0, 100.0, 100.0), (100.0, 100.0, 100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (50.0, 50.0, 50.0)]
    dlg = _AddPatchesDialog(_FakeSettings(), existing_patches=existing)
    for cb in (dlg._gen_cube, dlg._gen_skin, dlg._gen_blues, dlg._gen_greens,
               dlg._gen_sunrises, dlg._gen_flamingos, dlg._gen_greys,
               dlg._gen_edges, dlg._gen_hs, dlg._gen_pastel, dlg._gen_image,
               dlg._gen_fill):
        cb.setChecked(False)
    dlg._gen_whiteblack.setChecked(True)
    dlg._gen_whiteblack_n.setValue(2)
    dlg._update_gen_counts()
    assert "4" in dlg._gen_whiteblack_count.text()
    prog = dlg._build_generated_program()
    assert prog.count((100.0, 100.0, 100.0)) == 2
    assert prog.count((0.0, 0.0, 0.0)) == 2


def test_flamingos_and_edges_between_in_program(qapp):
    """Flamingos is a real set in the program, and Saturated edges' 'between'
    control fills evenly relative to the cube's steps (Knut, #78)."""
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_unique.setChecked(False)                  # isolate raw counts
    # cube 4 → 64; edges between=2 with cube_n=4 → 12·2·3 = 72; flamingos 10×1.
    dlg._gen_cube.setChecked(True)
    dlg._gen_cube_n.setValue(4)
    dlg._gen_edges.setChecked(True)
    dlg._gen_edges_n.setValue(2)
    dlg._gen_edges_faces.setValue(0)
    dlg._gen_flamingos.setChecked(True)
    dlg._gen_flamingos_n.setValue(10)
    dlg._gen_flamingos_layers.setValue(1)
    dlg._update_gen_counts()
    assert "72" in dlg._gen_edges_count.text()
    assert "10" in dlg._gen_flamingos_count.text()
    assert len(dlg._build_generated_program()) == 64 + 72 + 10

    # Edges keys to the cube: turn the cube off and it falls back to 2 steps
    # (one gap per edge), so between=2 → 12·2·1 = 24.
    dlg._gen_cube.setChecked(False)
    dlg._update_gen_counts()
    assert "24" in dlg._gen_edges_count.text()


def test_flamingos_persists_and_restores_to_default(qapp):
    """New generator values save on commit and 'Restore defaults' brings them
    back (the persistence rule for every new set)."""
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._gen_flamingos.setChecked(False)
    dlg._gen_flamingos_n.setValue(33)
    st = dlg._collect_gen_sets()
    assert st["cb"]["flamingos"] is False
    assert st["sp"]["flamingos_n"] == 33
    # Restoring the factory baseline re-ticks it and resets the value.
    dlg._apply_gen_sets({"cb": dlg._GEN_FACTORY["cb"],
                         "sp": dlg._GEN_FACTORY["sp"]})
    assert dlg._gen_flamingos.isChecked() is True
    assert dlg._gen_flamingos_n.value() == 64


def test_gamut_corners_in_program_and_persists(qapp):
    """Gamut-corner emphasis adds 8×per_corner patches, and its values save and
    restore (incl. its spread) like every other set."""
    dlg = _AddPatchesDialog(_FakeSettings())
    dlg._add_mode_gen.setChecked(True)
    dlg._refresh_add_mode()
    for n in dlg._GEN_CHECKS:
        getattr(dlg, f"_gen_{n}").setChecked(False)
    dlg._gen_unique.setChecked(False)
    dlg._gen_corners.setChecked(True)
    dlg._gen_corners_n.setValue(5)        # 8 × 5 = 40
    dlg._gen_corners_spread.setValue(12)
    dlg._update_gen_counts()
    assert "40" in dlg._gen_corners_count.text()
    assert len(dlg._build_generated_program()) == 40

    st = dlg._collect_gen_sets()
    assert st["cb"]["corners"] is True
    assert st["sp"]["corners_n"] == 5 and st["sp"]["corners_spread"] == 12
    # Factory baseline: off, per-corner 6, spread 15.
    dlg._apply_gen_sets({"cb": dlg._GEN_FACTORY["cb"],
                         "sp": dlg._GEN_FACTORY["sp"]})
    assert dlg._gen_corners.isChecked() is False
    assert dlg._gen_corners_n.value() == 6
    assert dlg._gen_corners_spread.value() == 15
