"""The reusable LayoutOptionsPanel round-trips a LayoutRecipe."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from workflow.layout_engine.presets import LayoutRecipe


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_recipe_roundtrip(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel()
    r = LayoutRecipe(
        instrument="i1", paper="A4", spacer_mode="bw", pscale=0.9, sscale=1.1,
        spacer_width_mm=2.0, patch_w_mm=9.0, patch_h_mm=11.0, inter_patch_mm=1.0,
        strip_indicator_gap_mm=3.0, margin_top=10, margin_right=8, margin_bottom=12,
        margin_left=15, dpi=150, nolimit=True, max_strip_mm=200, offset_x_mm=4,
        offset_y_mm=5, bit16=True, compression="zlib")
    panel.set_recipe(r)
    out = panel.apply_to_recipe(LayoutRecipe(instrument="i1", paper="A4"))
    for f in ("spacer_mode", "pscale", "sscale", "spacer_width_mm", "patch_w_mm",
              "patch_h_mm", "inter_patch_mm", "strip_indicator_gap_mm", "margin_top",
              "margin_right", "margin_bottom", "margin_left", "dpi", "nolimit",
              "max_strip_mm", "offset_x_mm", "offset_y_mm", "bit16", "compression"):
        assert getattr(out, f) == getattr(r, f), f


def test_changed_signal(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    panel = LayoutOptionsPanel()
    fired = []
    panel.changed.connect(lambda: fired.append(1))
    panel.pscale.setValue(1.5)
    assert fired


def test_calibration_gated(app):
    from ui.dialogs.layout_options_panel import LayoutOptionsPanel
    assert LayoutOptionsPanel().cal_settings() == (None, False)        # no cal group
    p = LayoutOptionsPanel(with_calibration=True)
    assert p.cal_settings() == (None, False)                          # nothing chosen
    p.set_cal("/tmp/x.cal", "apply")
    assert p.cal_settings() == ("/tmp/x.cal", True)
    p.set_cal("/tmp/x.cal", "embed")
    assert p.cal_settings() == ("/tmp/x.cal", False)
