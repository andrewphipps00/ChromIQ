"""Tests for the Project / Run / Calibration API in core/file_manager.py.

These cover the lifecycle the redesigned workflow depends on:
  - Project.create / load / create_or_load round-trip
  - Run path properties (every artefact name)
  - Averaging via Run.promote_measurement_to_read + reads()
  - Pre-conditioning seed via Project.new_run(preconditioning_from=...)
  - Run.reset_chart_artefacts (what survives, what doesn't)
  - Calibration.exists / reset
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.file_manager import (
    Calibration,
    Project,
    ProjectManifest,
    Run,
    RunMeta,
)


# ---------------------------------------------------------------------------
# ProjectManifest / RunMeta
# ---------------------------------------------------------------------------

def test_project_manifest_fresh_defaults() -> None:
    m = ProjectManifest.fresh("MyChart")
    assert m.schema_version == 1
    assert m.target_name == "MyChart"
    assert m.current_run == "run1"
    assert m.runs == ["run1"]
    assert m.created_at  # ISO timestamp, non-empty


def test_project_manifest_from_dict_ignores_unknown_keys() -> None:
    """Forward compatibility: a future field in project.json must not crash load()."""
    m = ProjectManifest.from_dict({
        "schema_version": 1,
        "target_name": "X",
        "current_run": "run1",
        "runs": ["run1"],
        "created_at": "now",
        "future_field": "ignored",
    })
    assert m.target_name == "X"


def test_run_meta_fresh_and_roundtrip() -> None:
    meta = RunMeta.fresh("run2", parent="run1")
    assert meta.run_id == "run2"
    assert meta.parent_run == "run1"
    assert meta.status == "in_progress"
    # Round-trip through dict
    from dataclasses import asdict
    restored = RunMeta.from_dict(asdict(meta))
    assert restored == meta


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------

def test_project_create_initialises_structure(tmp_path: Path) -> None:
    root = tmp_path / "MyChart"
    proj = Project.create(root, "MyChart")

    assert (root / "project.json").is_file()
    assert (root / "runs" / "run1").is_dir()
    assert (root / "runs" / "run1" / "meta.json").is_file()
    assert proj.current_run().id == "run1"
    assert proj.all_runs() == [proj.run("run1")] or len(proj.all_runs()) == 1


def test_project_load_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "P"
    Project.create(root, "P")
    reloaded = Project.load(root)
    assert reloaded.target_name == "P"
    assert reloaded.current_run().id == "run1"


def test_project_load_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Project.load(tmp_path / "does_not_exist")


def test_project_create_or_load_loads_if_present(tmp_path: Path) -> None:
    root = tmp_path / "P"
    Project.create(root, "P")
    # Mutate the manifest so we can detect that load (not create) ran.
    (root / "project.json").write_text(json.dumps({
        "schema_version": 1, "created_at": "x",
        "target_name": "P", "current_run": "run1", "runs": ["run1"],
    }))
    proj = Project.create_or_load(root, "DIFFERENT")
    assert proj.target_name == "P", "should have loaded existing, not created fresh"


def test_project_create_or_load_creates_if_absent(tmp_path: Path) -> None:
    root = tmp_path / "Fresh"
    proj = Project.create_or_load(root, "Fresh")
    assert (root / "project.json").is_file()
    assert proj.current_run().id == "run1"


# ---------------------------------------------------------------------------
# Run — path properties
# ---------------------------------------------------------------------------

def test_run_path_properties(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()

    expected = {
        r.chart_ti1:              "runs/run1/chart.ti1",
        r.chart_ti2:              "runs/run1/chart.ti2",
        r.chart_cht:              "runs/run1/chart.cht",
        r.chart_ps:               "runs/run1/chart.ps",
        r.chart_channels_json:    "runs/run1/chart.channels.json",
        r.measurement_ti3:        "runs/run1/chart.ti3",
        r.preconditioning_ti3:    "runs/run1/preconditioning.ti3",
        r.preconditioning_icc:    "runs/run1/preconditioning.icc",
        r.merged_ti3:             "runs/run1/merged.ti3",
        r.merged_icc:             "runs/run1/merged.icc",
        r.profile_icc:            "runs/run1/chart.icc",
        r.meta_path:              "runs/run1/meta.json",
        r.reads_dir:              "runs/run1/reads",
    }
    for actual, suffix in expected.items():
        assert actual == proj.root / suffix, f"{actual} != {proj.root / suffix}"


def test_run_chart_tiffs_sorted_and_case_insensitive(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()
    (r.dir / "chart_02.tif").write_text("p2")
    (r.dir / "chart_01.tif").write_text("p1")
    (r.dir / "chart_03.TIF").write_text("p3")
    (r.dir / "chart_04.tiff").write_text("p4")
    # A non-chart tiff must not be picked up.
    (r.dir / "other.tif").write_text("nope")
    tiffs = r.chart_tiffs()
    assert [p.name for p in tiffs] == ["chart_01.tif", "chart_02.tif", "chart_03.TIF", "chart_04.tiff"]


# ---------------------------------------------------------------------------
# Run — averaging
# ---------------------------------------------------------------------------

def test_run_reads_empty_when_no_reads_dir(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    assert proj.current_run().reads() == []
    assert proj.current_run().next_read_index() == 1


def test_run_promote_measurement_to_read_increments(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()

    r.measurement_ti3.write_text("M1")
    p1 = r.promote_measurement_to_read()
    assert p1.name == "read1.ti3"
    assert p1.read_text() == "M1"
    assert not r.measurement_ti3.exists(), "measurement.ti3 must be moved, not copied"

    r.measurement_ti3.write_text("M2")
    p2 = r.promote_measurement_to_read()
    assert p2.name == "read2.ti3"

    assert [p.name for p in r.reads()] == ["read1.ti3", "read2.ti3"]
    assert r.next_read_index() == 3


def test_run_promote_without_measurement_raises(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    with pytest.raises(FileNotFoundError):
        proj.current_run().promote_measurement_to_read()


def test_run_reads_ignores_non_matching_files(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()
    r.reads_dir.mkdir()
    (r.reads_dir / "read1.ti3").write_text("R1")
    (r.reads_dir / "read2.ti3").write_text("R2")
    (r.reads_dir / "garbage.ti3").write_text("nope")
    (r.reads_dir / "readN.ti3").write_text("nope")  # not a number
    assert [p.name for p in r.reads()] == ["read1.ti3", "read2.ti3"]


def test_run_clear_reads_removes_dir(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()
    r.reads_dir.mkdir()
    (r.reads_dir / "read1.ti3").write_text("R1")
    r.clear_reads()
    assert not r.reads_dir.exists()


# ---------------------------------------------------------------------------
# Pre-conditioning seed (the original double-counting scenario)
# ---------------------------------------------------------------------------

def test_new_run_seeds_preconditioning_from_parent(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    parent = proj.current_run()
    parent.measurement_ti3.write_text("PARENT MEASUREMENT")
    parent.profile_icc.write_text("PARENT PROFILE")

    child = proj.new_run(preconditioning_from=parent)

    assert child.id == "run2"
    assert proj.current_run().id == "run2", "new_run must switch current"
    assert proj.all_runs() == [proj.run("run1"), proj.run("run2")] or \
           [r.id for r in proj.all_runs()] == ["run1", "run2"]

    assert child.preconditioning_ti3.read_text() == "PARENT MEASUREMENT"
    assert child.preconditioning_icc.read_text() == "PARENT PROFILE"
    assert child.has_preconditioning()
    assert child.load_meta().parent_run == "run1"
    assert child.load_meta().preconditioning_source_run == "run1"

    # Parent is untouched.
    assert parent.measurement_ti3.read_text() == "PARENT MEASUREMENT"
    assert parent.profile_icc.read_text() == "PARENT PROFILE"


def test_new_run_without_parent_has_no_preconditioning(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r2 = proj.new_run()
    assert r2.id == "run2"
    assert not r2.has_preconditioning()
    assert r2.load_meta().parent_run is None


def test_new_run_requires_parent_artefacts(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    parent = proj.current_run()
    # No measurement / profile written → must raise
    with pytest.raises(FileNotFoundError):
        proj.new_run(preconditioning_from=parent)


def test_run_2_reads_dir_isolated_from_run_1(tmp_path: Path) -> None:
    """The whole point of the redesign: cross-run averaging collision impossible."""
    proj = Project.create(tmp_path / "P", "P")
    r1 = proj.current_run()
    r1.reads_dir.mkdir()
    (r1.reads_dir / "read1.ti3").write_text("V1 R1")
    (r1.reads_dir / "read2.ti3").write_text("V1 R2")
    r1.measurement_ti3.write_text("V1 AVG")
    r1.profile_icc.write_text("V1 ICC")

    r2 = proj.new_run(preconditioning_from=r1)

    # Run 2 starts with NO reads visible from run 1's reads/ directory.
    assert r2.reads() == []
    assert r2.next_read_index() == 1

    # Run 1 reads still exist where they were (preserved for diagnostics).
    assert (r1.reads_dir / "read1.ti3").read_text() == "V1 R1"


# ---------------------------------------------------------------------------
# Run.reset_chart_artefacts
# ---------------------------------------------------------------------------

def test_reset_chart_artefacts_preserves_preconditioning_and_meta(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r1 = proj.current_run()
    r1.measurement_ti3.write_text("M")
    r1.profile_icc.write_text("ICC")
    r2 = proj.new_run(preconditioning_from=r1)
    # Now run 2 has preconditioning.* and meta.json. Add some chart artefacts:
    r2.chart_ti1.write_text("TI1")
    r2.chart_ti2.write_text("TI2")
    (r2.dir / "chart_01.tif").write_text("TIFF")
    r2.chart_channels_json.write_text("{}")
    r2.measurement_ti3.write_text("MEASURED")
    r2.merged_ti3.write_text("MERGED")
    r2.profile_icc.write_text("ICC2")
    r2.reads_dir.mkdir()
    (r2.reads_dir / "read1.ti3").write_text("R1")

    r2.reset_chart_artefacts()

    # Wiped:
    for name in ("chart.ti1", "chart.ti2", "chart_01.tif", "chart.channels.json",
                 "chart.ti3", "merged.ti3", "chart.icc"):
        assert not (r2.dir / name).exists(), f"{name} should be wiped"
    assert not r2.reads_dir.exists()

    # Preserved:
    assert r2.preconditioning_ti3.exists()
    assert r2.preconditioning_icc.exists()
    assert r2.meta_path.exists()


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_paths(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    cal = proj.calibration
    assert cal.dir == proj.root / "cal"
    assert cal.cal_path == proj.root / "cal" / "calibration.cal"
    assert cal.ti3 == proj.root / "cal" / "calibration.ti3"


def test_calibration_exists_false_when_empty(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    assert not proj.calibration.exists()


def test_calibration_exists_true_after_cal_written(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    cal = proj.calibration
    cal.ensure_dir()
    cal.cal_path.write_text("CAL")
    assert cal.exists()


def test_calibration_reset_removes_dir(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    cal = proj.calibration
    cal.ensure_dir()
    cal.cal_path.write_text("CAL")
    cal.ti3.write_text("TI3")
    cal.reset()
    assert not cal.dir.exists()


# ---------------------------------------------------------------------------
# FileManager.project()
# ---------------------------------------------------------------------------

class _FakeSettings:
    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, key: str, default=None):
        if key == "custom_output_path":
            return str(self._root)
        return default


def test_file_manager_project_creates_on_first_access(tmp_path: Path) -> None:
    from core.file_manager import FileManager
    fm = FileManager(_FakeSettings(tmp_path))
    fm.set_target_name("MyChart")
    proj = fm.project()
    assert proj.target_name == "MyChart"
    assert proj.root == tmp_path / "MyChart"
    assert (tmp_path / "MyChart" / "project.json").is_file()


def test_file_manager_project_cached_until_target_name_changes(tmp_path: Path) -> None:
    from core.file_manager import FileManager
    fm = FileManager(_FakeSettings(tmp_path))
    fm.set_target_name("A")
    p1 = fm.project()
    p2 = fm.project()
    assert p1 is p2

    fm.set_target_name("B")
    p3 = fm.project()
    assert p3 is not p1
    assert p3.target_name == "B"
