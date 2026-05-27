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


def test_project_create_writes_user_readme(tmp_path: Path) -> None:
    """`Where are my files.txt` lands at the project root and mentions the
    project name so the paths in the example lines are concrete."""
    proj = Project.create(tmp_path / "MyChart", "MyChart")
    readme = proj.root / "Where are my files.txt"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    # Concrete project name substituted into the example paths.
    assert "MyChart.icc" in text
    assert "MyChart-cal.cal" in text
    assert "MyChart-i1profiler.pxf" in text
    # Both sections present.
    assert "Where to find things you might want" in text
    assert "Other files and folders you may see" in text


def test_project_load_backfills_readme_when_missing(tmp_path: Path) -> None:
    """Older projects (created before the README shipped) get one on next load."""
    proj = Project.create(tmp_path / "P", "P")
    proj.readme_path.unlink()
    assert not proj.readme_path.exists()

    Project.load(tmp_path / "P")
    assert proj.readme_path.is_file()


def test_project_load_does_not_overwrite_edited_readme(tmp_path: Path) -> None:
    """A README the user edited is left alone on load."""
    proj = Project.create(tmp_path / "P", "P")
    proj.readme_path.write_text("MY OWN NOTES — please leave alone\n")

    Project.load(tmp_path / "P")
    assert proj.readme_path.read_text() == "MY OWN NOTES — please leave alone\n"


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
    # Chart stem is the (sanitised) project folder name — "P" in this test.
    assert r.stem == "P"

    expected = {
        r.chart_ti1:              "runs/run1/P.ti1",
        r.chart_ti2:              "runs/run1/P.ti2",
        r.chart_cht:              "runs/run1/P.cht",
        r.chart_ps:               "runs/run1/P.ps",
        r.chart_channels_json:    "runs/run1/P.channels.json",
        r.measurement_ti3:        "runs/run1/P.ti3",
        r.preconditioning_ti3:    "runs/run1/preconditioning.ti3",
        r.preconditioning_icc:    "runs/run1/preconditioning.icc",
        r.merged_ti3:             "runs/run1/merged.ti3",
        r.merged_icc:             "runs/run1/merged.icc",
        r.profile_icc:            "runs/run1/P.icc",
        r.meta_path:              "runs/run1/meta.json",
        r.reads_dir:              "runs/run1/reads",
    }
    for actual, suffix in expected.items():
        assert actual == proj.root / suffix, f"{actual} != {proj.root / suffix}"


def test_run_chart_tiffs_sorted_and_case_insensitive(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()
    (r.dir / "P_02.tif").write_text("p2")
    (r.dir / "P_01.tif").write_text("p1")
    (r.dir / "P_03.TIF").write_text("p3")
    (r.dir / "P_04.tiff").write_text("p4")
    # A non-chart tiff must not be picked up.
    (r.dir / "other.tif").write_text("nope")
    tiffs = r.chart_tiffs()
    assert [p.name for p in tiffs] == ["P_01.tif", "P_02.tif", "P_03.TIF", "P_04.tiff"]


def test_run_chart_tiffs_finds_single_page_no_suffix(tmp_path: Path) -> None:
    """printtarg writes `<stem>.tif` (no _NN) for a one-page chart — it must be
    found, not silently skipped by a `<stem>_*.tif` (underscore) glob."""
    proj = Project.create(tmp_path / "P", "P")
    r = proj.current_run()
    (r.dir / "P.tif").write_text("single page")
    assert [p.name for p in r.chart_tiffs()] == ["P.tif"]


def test_run_stem_matches_project_folder(tmp_path: Path) -> None:
    """Stem derives from the project folder, so Run.for_dir works in isolation
    and stays consistent with the (sanitised) target name."""
    from core.file_manager import Run
    proj = Project.create(tmp_path / "printer-test-file", "printer-test-file")
    assert proj.current_run().stem == "printer-test-file"
    # Same answer for a project-less Run bound to the same directory.
    standalone = Run.for_dir(proj.current_run().dir)
    assert standalone.stem == "printer-test-file"
    assert standalone.chart_ti2.name == "printer-test-file.ti2"


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


def test_averaged_run1_to_refined_run2_no_double_count(tmp_path: Path) -> None:
    """End-to-end of the original double-counting bug — now impossible.

    Run 1 with averaging produces reads/ + an averaged chart.ti3 + profile.
    Promoting to run 2 seeds preconditioning.* from run 1's *averaged* outputs.
    Run 2's own averaging set lands in a fresh reads/ that never sees run 1's
    reads, so a later merge with preconditioning.ti3 cannot re-include run 1's
    individual reads.
    """
    proj = Project.create(tmp_path / "P", "P")

    # --- Run 1: averaged ---
    r1 = proj.current_run()
    r1.reads_dir.mkdir()
    (r1.reads_dir / "read1.ti3").write_text("V1 R1")
    (r1.reads_dir / "read2.ti3").write_text("V1 R2")
    r1.measurement_ti3.write_text("V1 AVERAGED")       # chart.ti3 = mean of reads
    r1.profile_icc.write_text("V1 PROFILE")

    # --- Promote to run 2 (refinement) ---
    r2 = proj.new_run(preconditioning_from=r1)
    # Pre-conditioning seed is run 1's AVERAGED measurement, not its raw reads.
    assert r2.preconditioning_ti3.read_text() == "V1 AVERAGED"

    # --- Run 2: its own averaging set ---
    r2.reads_dir.mkdir()
    (r2.reads_dir / "read1.ti3").write_text("V2 R1")
    (r2.reads_dir / "read2.ti3").write_text("V2 R2")
    # Run 2 only ever sees its own reads.
    assert [p.read_text() for p in r2.reads()] == ["V2 R1", "V2 R2"]
    # The merge inputs would be r2.measurement_ti3 + r2.preconditioning_ti3 —
    # exactly one copy of run 1's data (the average), never the raw reads.
    assert r1.reads_dir != r2.reads_dir


# ---------------------------------------------------------------------------
# Run.reset_chart_artefacts
# ---------------------------------------------------------------------------

def test_reset_chart_artefacts_preserves_preconditioning_and_meta(tmp_path: Path) -> None:
    proj = Project.create(tmp_path / "P", "P")
    r1 = proj.current_run()
    r1.measurement_ti3.write_text("M")
    r1.profile_icc.write_text("ICC")
    r2 = proj.new_run(preconditioning_from=r1)
    # Now run 2 has preconditioning.* and meta.json. Add some chart artefacts
    # under the project-name stem ("P" here).
    r2.chart_ti1.write_text("TI1")
    r2.chart_ti2.write_text("TI2")
    (r2.dir / "P_01.tif").write_text("TIFF")
    r2.chart_channels_json.write_text("{}")
    r2.measurement_ti3.write_text("MEASURED")
    r2.merged_ti3.write_text("MERGED")
    r2.profile_icc.write_text("ICC2")
    r2.reads_dir.mkdir()
    (r2.reads_dir / "read1.ti3").write_text("R1")

    r2.reset_chart_artefacts()

    # Wiped:
    for name in ("P.ti1", "P.ti2", "P_01.tif", "P.channels.json",
                 "P.ti3", "merged.ti3", "P.icc"):
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
    # Calibration stem = "<project>-cal" so the printed sheet is named after
    # the project but is distinguishable from the profiling chart.
    assert cal.stem == "P-cal"
    assert cal.dir == proj.root / "cal"
    assert cal.cal_path == proj.root / "cal" / "P-cal.cal"
    assert cal.ti3 == proj.root / "cal" / "P-cal.ti3"


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
