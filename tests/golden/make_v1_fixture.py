"""Generate the frozen schema-1 project fixture for migration tests (#127).

Run ONCE against the pre-#127 code (schema_version 1, flat run folders) and
commit the result; the migration tests replay it against the new code forever.

    python tests/golden/make_v1_fixture.py

The tree is built with the *real* writers of the old layout wherever they are
pure-Python (Project/Run API, quality-report / refine-strips writers,
measurement report, sidecar exports); binary artefacts (.tif/.icc) are stand-in
bytes — migration only moves files, it never parses these.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

FIXTURE = Path(__file__).parent / "project_v1" / "Golden-Printer"


def main() -> None:
    from core.file_manager import Project

    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)

    proj = Project.create(FIXTURE, "Golden-Printer")
    name = "Golden-Printer"

    # ---- run1: a full profile build with every file family of the flat layout
    run1 = proj.current_run()
    d = run1.dir
    for ext in (".ti1", ".ti2", ".cht", ".cie", ".ps"):
        (d / f"{name}{ext}").write_text(f"stand-in {ext}\n", encoding="utf-8")
    (d / f"{name}.channels.json").write_text('{"channels": ["R", "G", "B"]}\n',
                                             encoding="utf-8")
    (d / f"{name}.strips.json").write_text('{"strips": []}\n', encoding="utf-8")
    (d / f"{name}_01.tif").write_bytes(b"TIFF-STAND-IN-1")
    (d / f"{name}_02.tif").write_bytes(b"TIFF-STAND-IN-2")
    (d / f"{name}.pdf").write_bytes(b"%PDF-STAND-IN")
    (d / f"{name}.ti3").write_text("CTI3 stand-in measurement\n", encoding="utf-8")
    (d / f"{name}.icc").write_bytes(b"ICC-STAND-IN")
    (d / "preconditioning.ti3").write_text("CTI3 precond\n", encoding="utf-8")
    (d / "preconditioning.icc").write_bytes(b"ICC-PRECOND")
    (d / "merged.ti3").write_text("CTI3 merged\n", encoding="utf-8")
    (d / "merged.icc").write_bytes(b"ICC-MERGED")
    (d / "calibrated.icc").write_bytes(b"ICC-CALIBRATED")

    # sidecar exports via the real writer (flat: lands at the run root in v1)
    from workflow.chart_exports import write_sidecars
    ti1 = d / f"{name}.ti1"
    ti1.write_text(
        "CTI1\n\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B\n"
        "END_DATA_FORMAT\nBEGIN_DATA\n1 100.0 0.0 0.0\n2 0.0 100.0 0.0\n"
        "END_DATA\n", encoding="utf-8")
    write_sidecars(ti1, d, name)

    # quality reports + refine strips via the real writers
    from workflow.profcheck_runner import write_quality_report, write_refine_strips
    write_quality_report(d, name, "Assessment: good", "profcheck raw log 1")
    write_quality_report(d, name, "Assessment: better", "profcheck raw log 2")
    write_refine_strips(d, name, [("A", 3.21), ("F", 2.75)])

    # measurement report via the real writer (already reports/ in v1)
    from workflow.measurement_report import save_report
    save_report({"created": "2026-07-18T10:00:00", "mean": 0.4}, d)

    # averaging reads
    run1.reads_dir.mkdir(parents=True, exist_ok=True)
    (run1.reads_dir / "read1.ti3").write_text("CTI3 read1\n", encoding="utf-8")
    (run1.reads_dir / "read2.ti3").write_text("CTI3 read2\n", encoding="utf-8")

    # scanner debris of the flat layout (both current and legacy naming)
    for n in (f"{name}-patchbox.cht", f"{name}-patchbox-sample.cht",
              f"{name}_01-sample.cht", f"{name}-aligned.cht",
              f"{name}-aligned-patchbox.cht"):
        (d / n).write_text("BOXES stand-in\n", encoding="utf-8")
    (d / "scan-of-page1-diag.tif").write_bytes(b"TIFF-DIAG")

    # user files that must NEVER be touched by migration
    (d / "my own notes.txt").write_text("user notes — hands off\n", encoding="utf-8")
    (d / f"{name}-notes.txt").write_text("user stem-named notes\n", encoding="utf-8")

    # ---- run2: sparse (chart only, no measurement) — migration must cope
    run2 = proj.new_run()
    d2 = run2.dir
    (d2 / f"{name}.ti1").write_text("CTI1 run2\n", encoding="utf-8")
    (d2 / f"{name}.ti2").write_text("CTI2 run2\n", encoding="utf-8")
    (d2 / f"Quality_Check_1_{name}.txt").write_text("run2 check\n", encoding="utf-8")

    # ---- cal/: calibration set including its sidecar exports (flat in v1)
    cal = proj.calibration
    cal.ensure_dir()
    cs = cal.stem
    for ext in (".ti1", ".ti2", ".ti3", ".cal", ".icc", ".cht", ".ps"):
        (cal.dir / f"{cs}{ext}").write_text(f"cal stand-in {ext}\n", encoding="utf-8")
    (cal.dir / f"{cs}_01.tif").write_bytes(b"TIFF-CAL")
    (cal.dir / f"{cs}-colours.txt").write_text("#ff0000\n", encoding="utf-8")
    (cal.dir / f"{cs}-i1profiler.txt").write_text("i1p txt\n", encoding="utf-8")
    (cal.dir / f"{cs}-i1profiler.pxf").write_text("i1p pxf\n", encoding="utf-8")
    (cal.dir / "meta.json").write_text("{}\n", encoding="utf-8")

    # ---- project-level exports/ (Tools menu) — must be untouched
    proj.ensure_exports_dir()
    (proj.exports_dir / f"{name}-i1profiler.txt").write_text("tools export\n",
                                                            encoding="utf-8")

    # sanity: fixture must be schema 1
    import json
    m = json.loads((FIXTURE / "project.json").read_text(encoding="utf-8"))
    assert m["schema_version"] == 1, m
    print(f"fixture written: {FIXTURE}  (schema_version=1)")
    for p in sorted(FIXTURE.rglob("*")):
        if p.is_file():
            print(" ", p.relative_to(FIXTURE))


if __name__ == "__main__":
    main()
