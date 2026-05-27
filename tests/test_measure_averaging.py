"""Read-again-&-average routing in TabMeasure (docs/dev_averaging.md).

Exercises the file-promotion + action helpers that the "All Stripes Read"
averaging dialog and the manual completion dialog both feed into:
  * _promote_completed_read — moves a read into reads/readN.ti3 while a set is
    active, leaves a standalone read in place otherwise;
  * _apply_completion_action — routes build / again / use_last correctly.

Under the per-run folder layout the canonical measurement is chart.ti3 and the
per-read snapshots live in reads/readN.ti3 inside the same run folder.

The "average" action is not exercised here (it spawns the `average` QProcess and
is covered by tests/test_average_runner.py); we stub _start_averaging_read so the
"again" path doesn't relaunch chartread.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.argyll_runner import ArgyllRunner  # noqa: E402
from core.settings import DEFAULTS  # noqa: E402
from ui.tabs.tab_measure import TabMeasure  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _StubSettings:
    """Dict-backed AppSettings stand-in (avoids touching real QSettings)."""

    def __init__(self, **overrides):
        self._d = dict(DEFAULTS)
        self._d.update(overrides)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


@pytest.fixture
def tab():
    settings = _StubSettings(averaging_enabled=True)
    t = TabMeasure(ArgyllRunner(settings), settings)
    # Never relaunch chartread during a unit test.
    t._start_averaging_read = lambda: None
    return t


def _fresh(tmp_path, name="chart"):
    p = tmp_path / f"{name}.ti3"
    p.write_text("CTI3\nBEGIN_DATA\nEND_DATA\n")
    return p


def test_build_proceeds_with_standalone_read(tab, tmp_path):
    sig = {}
    tab.measure_finished.connect(lambda p: sig.__setitem__("finished", p))
    tab.proceed_to_profile.connect(lambda: sig.__setitem__("proceed", True))

    ti3 = _fresh(tmp_path)
    tab._averaging_active = False
    current, reads = tab._promote_completed_read(ti3)
    assert current == ti3 and reads == []

    tab._apply_completion_action(ti3, current, reads, "build", "mean")
    assert sig.get("finished") == ti3
    assert sig.get("proceed") is True
    assert not tab._averaging_active


def test_again_on_first_read_starts_a_set(tab, tmp_path):
    ti3 = _fresh(tmp_path)
    tab._averaging_active = False
    current, reads = tab._promote_completed_read(ti3)

    tab._apply_completion_action(ti3, current, reads, "again", "mean")
    assert tab._averaging_active is True
    assert (tmp_path / "reads" / "read1.ti3").exists()
    assert not ti3.exists(), "canonical chart.ti3 should have moved to reads/read1.ti3"


def test_promote_while_active_accumulates_variants(tab, tmp_path):
    # Pretend read1 already exists in reads/ and a set is active.
    reads_dir = tmp_path / "reads"
    reads_dir.mkdir()
    (reads_dir / "read1.ti3").write_text("CTI3\nBEGIN_DATA\nEND_DATA\n")
    tab._averaging_active = True

    ti3 = _fresh(tmp_path)            # the just-finished canonical read
    current, reads = tab._promote_completed_read(ti3)
    assert current.name == "read2.ti3"
    assert [r.name for r in reads] == ["read1.ti3", "read2.ti3"]


def test_use_last_proceeds_with_latest_variant(tab, tmp_path):
    reads_dir = tmp_path / "reads"
    reads_dir.mkdir()
    (reads_dir / "read1.ti3").write_text("CTI3\nBEGIN_DATA\nEND_DATA\n")
    tab._averaging_active = True
    ti3 = _fresh(tmp_path)
    current, reads = tab._promote_completed_read(ti3)

    sig = {}
    tab.measure_finished.connect(lambda p: sig.__setitem__("finished", p))
    tab.proceed_to_profile.connect(lambda: sig.__setitem__("proceed", True))

    tab._apply_completion_action(ti3, current, reads, "use_last", "mean")
    assert sig.get("finished") == current      # the latest read, not the canonical
    assert sig.get("proceed") is True
    assert tab._averaging_active is False
