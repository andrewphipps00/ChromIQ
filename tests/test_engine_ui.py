"""UI-side tests for the chart-reading engine (#126): preview click-to-jump,
session-map handling, split-patch overlay plumbing, guided goto."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    yield QApplication.instance() or QApplication([])


class _Settings:
    def __init__(self, extra=None):
        self._d = {"appearance": "dark"}
        self._d.update(extra or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


# ---------------------------------------------------------------------------
# TiffPreview: click-to-jump hit testing through the real paint geometry
# ---------------------------------------------------------------------------

def _make_preview(qapp=None):
    from ui.tiff_preview import TiffPreview
    pv = TiffPreview()
    pv.resize(400, 500)
    pm = QPixmap(200, 250)
    pm.fill(QColor("white"))
    pv._pixmap = pm
    pv._pages = [(Path("/nonexistent.tif"), 0)]
    pv._repaint_label()          # establishes _paint_geom
    return pv


def test_preview_click_emits_stripe_for_hit_and_nothing_for_miss():
    pv = _make_preview()
    rects = [QRect(10, 10, 180, 40), QRect(10, 60, 180, 40)]
    pv.set_stripe_rects(rects)
    pv.set_stripe_click_enabled(True, {0: True, 1: False})
    hits: list[tuple[int, int]] = []
    pv.stripe_clicked.connect(lambda pg, i: hits.append((pg, i)))

    s, ox, oy = pv._paint_geom
    # centre of stripe 1 in image px → widget coords
    cx = int(ox + (10 + 90) * s)
    cy = int(oy + (60 + 20) * s)
    pos_in_label = QPoint(cx, cy)
    pos = pv._img_label.mapTo(pv, pos_in_label)
    QTest.mouseClick(pv, Qt.MouseButton.LeftButton, pos=pos)
    assert hits == [(0, 1)]

    # a click well outside any stripe emits nothing
    QTest.mouseClick(pv, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert hits == [(0, 1)]

    # disabled → no emission even on a hit
    pv.set_stripe_click_enabled(False)
    QTest.mouseClick(pv, Qt.MouseButton.LeftButton, pos=pos)
    assert hits == [(0, 1)]


def test_patch_overlay_accumulates_and_clears():
    pv = _make_preview()
    items = [(QRect(10, 10, 20, 20), QColor("red"), QColor("blue"), False)]
    pv.set_patch_overlay(0, items)
    pv.set_patch_overlay(0, items)
    assert len(pv._patch_overlay[0]) == 2
    assert pv.has_patch_overlay()
    pv.clear_patch_overlay()
    assert not pv.has_patch_overlay()
    # painting with an overlay present must not raise
    pv.set_patch_overlay(0, items)
    pv._repaint_label()


# ---------------------------------------------------------------------------
# TabMeasure: engine handlers drive combo, read-map and overlay
# ---------------------------------------------------------------------------

def _make_tab(engine="chromiq"):
    from core.argyll_runner import ArgyllRunner
    from ui.tabs.tab_measure import TabMeasure
    s = _Settings({"chartread_engine": engine})
    return TabMeasure(ArgyllRunner(s), s)


def test_session_map_enables_click_jump_and_read_map(monkeypatch):
    tab = _make_tab()
    tab._page_stripe_rects = [[QRect(0, 0, 100, 20), QRect(0, 30, 100, 20)]]
    tab._strips_per_page = [2]
    # manual mode so the engine extras appear
    monkeypatch.setattr(tab, "_current_mode", lambda: "manual")
    tab._on_session_map([
        {"strip": "A", "sheet": 1, "read": True, "verifiable": True},
        {"strip": "B", "sheet": 1, "read": False, "verifiable": True},
    ])
    # No go-to-strip combo any more — clicking a strip is the only jump UI.
    assert not hasattr(tab, "_m_goto_combo")
    assert tab._preview._stripe_click_enabled
    assert tab._preview._stripe_read_map == {0: True, 1: False}


def test_strip_measured_splits_only_real_patch_boxes(monkeypatch):
    """The overlay must land on each patch's OWN box (looked up by loc), and
    draw nothing when the chart exposes no per-patch geometry."""
    tab = _make_tab()
    tab._page_stripe_rects = [[QRect(0, 0, 210, 20), QRect(0, 30, 210, 20)]]
    tab._strips_per_page = [2]
    tab._engine_strips = [{"strip": "A"}, {"strip": "B"}]
    ev = {
        "strip": "B", "worst_de": 2.0, "reversed": False, "verifiable": True,
        "patches": [
            {"id": str(i), "loc": f"B{i}", "xyz": [50, 50, 50],
             "exyz": [50, 50, 50], "de": 0.1}
            for i in range(1, 8)
        ],
    }
    # No geometry → overlay suppressed (never a misaligned block).
    tab._patch_boxes = [dict()]
    tab._on_strip_measured(ev)
    assert tab._preview._patch_overlay.get(0) is None
    assert tab._engine_read["B"] is True

    # With real per-patch boxes → each split lands on its own box.
    boxes = {f"B{i}": QRect(5 * i, 30, 4, 18) for i in range(1, 8)}
    tab._patch_boxes = [boxes]
    tab._on_strip_measured(ev)
    items = tab._preview._patch_overlay.get(0)
    assert items and len(items) == 7
    assert items[0][0] == QRect(5, 30, 4, 18)      # B1's exact box
    assert items[3][0] == QRect(20, 30, 4, 18)     # B4's exact box


def test_preview_click_maps_page_index_to_letter_and_sends_goto(monkeypatch):
    tab = _make_tab()
    tab._strips_per_page = [2, 2]
    sent: list[str] = []
    monkeypatch.setattr(tab._manager, "goto_strip", lambda s: sent.append(s))
    monkeypatch.setattr(type(tab._manager), "engine_active",
                        property(lambda self: True))
    tab._on_preview_strip_clicked(1, 1)     # page 2, second strip → "D"
    assert sent == ["D"]


def test_engine_params_attach_and_fallbacks(monkeypatch, tmp_path):
    from workflow.measure_manager import MeasureParams
    from workflow import chartread_engine

    tab = _make_tab()
    tab._ti1_path = tmp_path / "c.ti2"

    p = MeasureParams(ti1_path=tab._ti1_path)
    helper = chartread_engine.helper_path()  # dev build exists in this repo
    out = tab._apply_engine_params(p)
    assert out.engine_helper == helper

    # patch-by-patch → stock chartread
    p2 = MeasureParams(ti1_path=tab._ti1_path, patch_by_patch=True)
    assert tab._apply_engine_params(p2).engine_helper is None

    # setting off → untouched
    tab2 = _make_tab(engine="argyll")
    tab2._ti1_path = tab._ti1_path
    p3 = MeasureParams(ti1_path=tab._ti1_path)
    assert tab2._apply_engine_params(p3).engine_helper is None


def test_guided_navigation_uses_goto_when_engine_active():
    """The guided module must jump directly instead of stepping f/b."""
    from workflow.measure_manager import MeasureManager

    class _StubRunner:
        def __init__(self):
            self.writes = []

        def write_stdin(self, text):
            self.writes.append(text)

    r = _StubRunner()
    mgr = MeasureManager(r)
    mgr._engine_active = True
    mgr._navigate_toward("A", "K")
    assert r.writes and '"cmd": "goto"' in r.writes[0] and '"K"' in r.writes[0]

    mgr._engine_active = False
    mgr._navigate_toward("A", "K")
    assert r.writes[-1] == "f"              # stock path unchanged


def test_engine_line_decoder_feeds_existing_signals():
    """strip_ready/error events must drive the same signals the regex path
    drives, so every dialog keeps working."""
    from workflow.measure_manager import MeasureManager

    class _StubRunner:
        def write_stdin(self, text):
            pass

    mgr = MeasureManager(_StubRunner())
    mgr._guided_state = "disabled"      # same as the parser-test harness
    got: dict[str, list] = {}
    for name in ("stripe_changed", "all_stripes_done", "strip_error",
                 "wrong_strip", "unexpected_response", "strip_measured",
                 "readings_saved", "session_map", "unread_confirm"):
        got[name] = []
        getattr(mgr, name).connect(
            lambda *a, _n=name: got[_n].append(a))

    lines = [
        '{"event":"session_start","strips":[{"strip":"A","read":false}]}',
        '{"event":"strip_ready","strip":"A","read":false,"all_done":false}',
        '{"event":"strip_read","strip":"A","worst_de":1.0,"patches":[]}',
        '{"event":"saved","path":"x.ti3","read_patches":21}',
        '{"event":"strip_warning","kind":"wrong_strip","read":"B","expected":"A"}',
        '{"event":"strip_warning","kind":"unexpected_response","worst_de":97.5}',
        '{"event":"error","kind":"coms"}',
        '{"event":"unread_confirm","id":"7","loc":"A7"}',
        '{"event":"strip_ready","strip":"B","read":true,"all_done":true}',
        "plain console prose is passed through",
    ]
    prose: list[str] = []
    for ln in lines:
        mgr._handle_engine_line(ln, prose.append)

    assert [a[0] for a in got["stripe_changed"]] == ["A", "B"]
    assert len(got["all_stripes_done"]) == 1
    assert got["strip_error"] == [("communication problem",)]
    assert got["wrong_strip"] == [("B", "A")]
    assert got["unexpected_response"] == [("97.50",)]
    assert len(got["strip_measured"]) == 1
    assert got["readings_saved"] == [("x.ti3", 21)]
    assert got["unread_confirm"] == [("7, A7",)]
    assert "plain console prose is passed through" in prose
