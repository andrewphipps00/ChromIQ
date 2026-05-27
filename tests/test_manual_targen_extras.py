"""End-to-end checks that the new Manual-mode targen options actually reach
the targen command line.

These tests instantiate ``TabChart`` headlessly, set values on the new widgets
(ink limit, distribution selector, OFPS adaptation, multi-dim cube steps), then
call ``_collect_manual`` and verify the chosen value appears in the assembled
``ChartParams.extra_targen_args``. They guard against the failure mode of
"the YAML row exists and the widget shows up but the value never makes it into
the actual targen invocation" — which is exactly what we'd hit if we forgot to
add a new flag to the ``_collect_manual`` extras loop.
"""
from __future__ import annotations

import os
import shlex

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.file_manager import FileManager  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.tabs.tab_chart import TabChart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def tab(qapp):
    settings = AppSettings()
    fm = FileManager(settings)
    runner = ArgyllRunner(settings)
    t = TabChart(runner, fm, settings)
    # Force Manual mode so _collect_manual is the path under test.
    t._switch_mode("manual")
    return t


def _set_manual(tab, flag: str, value):
    """Set the Manual-mode widget for ``flag`` to ``value`` (enabling expert
    rows whose enable-checkbox would otherwise gate them off)."""
    for pw in tab._manual_widgets.get("targen", []):
        if pw.flag == flag:
            pw.set_value(value)
            if pw._enable_check is not None and pw._control is not None:
                pw._enable_check.setChecked(True)
            return
    raise AssertionError(f"No targen widget for flag {flag!r}")


def _extra_args(tab) -> list[str]:
    return shlex.split(tab._collect_manual().extra_targen_args)


# ---------------------------------------------------------------------------
# -l total ink limit
# ---------------------------------------------------------------------------

def test_ink_limit_emits_flag(tab):
    _set_manual(tab, "-l", 280)
    args = _extra_args(tab)
    assert "-l" in args
    assert args[args.index("-l") + 1] == "280"


def test_ink_limit_disabled_emits_nothing(tab):
    # Expert checkbox unticked by default — nothing should appear.
    args = _extra_args(tab)
    assert "-l" not in args


# ---------------------------------------------------------------------------
# Mutex distribution selector (flag_choice)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("choice", ["-t", "-r", "-R", "-q", "-Q", "-i", "-I"])
def test_distribution_selector_emits_chosen_flag(tab, choice):
    _set_manual(tab, "__targen_distribution__", choice)
    args = _extra_args(tab)
    # The chosen flag appears as a standalone token (no value follows).
    assert choice in args, f"{choice} missing from {args}"
    # And only that one distribution flag — no leakage of others.
    others = {"-t", "-r", "-R", "-q", "-Q", "-i", "-I"} - {choice}
    assert not (others & set(args)), f"unexpected sibling flag in {args}"


def test_distribution_selector_default_is_ofps_with_no_token(tab):
    # Default choice is OFPS (empty string) — no distribution flag emitted.
    _set_manual(tab, "__targen_distribution__", "")
    args = _extra_args(tab)
    for f in ("-t", "-r", "-R", "-q", "-Q", "-i", "-I"):
        assert f not in args, f"unexpected {f} in default OFPS args: {args}"


# ---------------------------------------------------------------------------
# -A OFPS adaptation
# ---------------------------------------------------------------------------

def test_ofps_adaptation_emits_flag(tab):
    _set_manual(tab, "-A", 0.75)
    args = _extra_args(tab)
    assert "-A" in args
    assert args[args.index("-A") + 1] == "0.75"


# ---------------------------------------------------------------------------
# -m / -M / -b multidim step counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag, value", [("-m", 4), ("-M", 6), ("-b", 3)])
def test_multidim_steps_emit_flag(tab, flag, value):
    _set_manual(tab, flag, value)
    args = _extra_args(tab)
    assert flag in args
    assert args[args.index(flag) + 1] == str(value)


# ---------------------------------------------------------------------------
# Default state: with no expert checkboxes ticked, no new flags appear at all.
# ---------------------------------------------------------------------------

def test_no_new_flags_when_all_expert_checkboxes_off(tab):
    args = _extra_args(tab)
    for f in ("-l", "-A", "-m", "-M", "-b", "-t", "-r", "-R", "-q", "-Q", "-i", "-I"):
        assert f not in args, f"unexpected {f} in default args: {args}"
