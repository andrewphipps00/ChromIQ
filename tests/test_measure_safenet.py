"""Opt-in misalignment safety net (#50): the ChromIQ engine's --safenet flag.

The critical property is that it is CONSERVATIVE — a correctly-aligned read must
never trip it (no false alarms on a good print, where vivid patches naturally
differ from the sRGB design). Reproducing a true positive is deliberately hard:
chartread's own recognition already catches gross shifts as a 'wrong strip'
warning, so the safety net only covers the narrow accepted-but-misaligned case.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
sys.path.insert(0, str(Path(__file__).parent / "helpers"))
from replay_tools import HELPER, ReplaySession, write_replay_script  # noqa: E402

ARGYLL = Path("/Applications/Argyll/bin")


def _make_chart(tmp: Path, name: str = "SN") -> Path:
    targen = shutil.which("targen") or str(ARGYLL / "targen")
    if not Path(targen).exists() or not Path(HELPER).exists():
        pytest.skip("Argyll targen or the ChromIQ engine helper is unavailable")
    tmp.mkdir(parents=True, exist_ok=True)
    base = tmp / name
    subprocess.run([targen, "-v0", "-d2", "-G", "-f120", str(base)],
                   check=True, capture_output=True, cwd=tmp)
    from workflow.layout_engine.chart import build_chart
    build_chart(base.with_suffix(".ti1"), base, instrument="i1", paper="A4",
                randomize=True, layout_mode="patch_first")
    return base


def _read_all(base: Path, extra_args):
    replay = base.parent / "replay.txt"
    write_replay_script(base.with_suffix(".ti2"), replay, noise=0.1)   # good read
    s = ReplaySession(base, replay, extra_args=extra_args)
    ev = s.wait_event("session_start")
    cols = [x["strip"] for x in ev["strips"]]
    s.wait_event("strip_ready")
    for _ in range(len(cols)):
        idx = s.event_index()
        s.send(cmd="swipe")
        s.wait_event("strip_read", after=idx)
        s.wait_event("saved", after=idx)
    s.send(cmd="done")
    s.wait_event("done", timeout=8)
    rc = s.finish()
    return s, cols, rc


def test_safenet_flag_accepted_and_quiet_on_good_read(tmp_path):
    base = _make_chart(tmp_path)
    s, cols, rc = _read_all(base, extra_args=["--safenet"])
    assert rc == 0
    # A correctly-aligned read must produce NO misalignment warnings.
    mis = [e for e in s.events if e.get("event") == "strip_misaligned"]
    assert mis == []
    # Every strip was still read and the full measurement saved.
    reads = [e for e in s.events if e.get("event") == "strip_read"]
    assert len(reads) == len(cols)
    assert base.with_suffix(".ti3").exists()


def test_safenet_off_is_identical(tmp_path):
    """Without --safenet the engine behaves exactly as before (no new events)."""
    base = _make_chart(tmp_path)
    s, cols, rc = _read_all(base, extra_args=[])
    assert rc == 0
    assert not any(e.get("event") == "strip_misaligned" for e in s.events)
    assert base.with_suffix(".ti3").exists()
