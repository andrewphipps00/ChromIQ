"""The "Where are my files?" folder guide (#125/#126, Knut).

One place that explains every file ChromIQ writes into a project folder: which
feature creates it, when, and what it's for. Shown as its own card in the
Welcome/Help window (rendered as a table — Knut); the same information (in
English) is also dropped into each project folder as ``Where are my files.txt``
(see ``core.file_manager``), rendered as an aligned plain-text table.

Each row's cells are individual ``tr()`` strings so they translate like every
other help text. ``{name}`` stands for the profile's name.
"""
from __future__ import annotations

import html

from core.i18n import tr


def file_guide_card_title() -> str:
    """Tile title in the Welcome/Help window's card grid."""
    return tr("Where are my files? (folder guide)")


def file_guide_card_subtitle() -> str:
    return tr("Every file a ChromIQ project folder can contain — what "
              "created it, when, and what it's for.")


def _intro() -> str:
    return tr(
        "Every ChromIQ project lives in its own folder (inside ~/ChromIQ, or "
        "your custom output folder from Settings), named after the profile. As "
        "you work through the steps — create a chart, print, measure, build, "
        "refine — files accumulate in it. The table below lists every file you "
        "can meet: its folder, what it's for, and which feature creates it. "
        "“{name}” stands for your profile's name.")


def _outro() -> str:
    return tr(
        "Safe to tidy: everything can in principle be recreated except your "
        "measurements ({name}.ti3, reads/, cal/{name}-cal.ti3) — those "
        "represent real ink on real paper and are worth keeping. The scanner / "
        "camera tool in particular leaves a lot of intermediate .cht files and "
        "a diagnostic image behind (see the last group); those are safe to "
        "delete, and clutter multi-page charts the most. The quickest tidy-up "
        "is deleting old runN folders you no longer need.")


def _rows():
    """Groups of (file, folder, description, origin) rows. Lazily built so the
    tr() calls run after the language is set."""
    return [
        (tr("The files you'll actually want"), [
            ("{name}.icc", "runs/runN", tr("Your finished ICC profile — the file you install or share."), tr("Build Profile tab")),
            ("{name}_01.tif, {name}_02.tif …", "runs/runN", tr("The printable chart pages. Print these."), tr("Create Chart")),
            ("{name}.ti3", "runs/runN", tr("Your measurements — the file the profile is built from. Keep it; any profile can be rebuilt later."), tr("Measure tab (on completion)")),
        ]),
        (tr("The working files of a chart"), [
            ("{name}.ti1", "runs/runN", tr("The list of patch colours (the recipe, before it's laid out on paper)."), tr("Create Chart (targen / generators)")),
            ("{name}.ti2", "runs/runN", tr("The laid-out chart: which colour sits where. Measuring needs it."), tr("Create Chart")),
            ("{name}.channels.json", "runs/runN", tr("Records the ink channels and (for engine charts) the exact layout + recipe, so reopening restores everything."), tr("Create Chart")),
            ("{name}.strips.json", "runs/runN", tr("Exact per-strip and per-patch pixel positions, used by the Measure preview (arrow, click-to-jump, split patches)."), tr("Create Chart (engine)")),
            ("{name}.pdf", "runs/runN", tr("The chart as a vector PDF — only when “Also export PDF” is ticked."), tr("Create Chart")),
            ("{name}.ps", "runs/runN", tr("A PostScript copy for printing (bypasses colour management)."), tr("Print Chart")),
        ]),
        (tr("Hand-off files (use the chart outside ChromIQ)"), [
            ("{name}-colours.txt", "runs/runN", tr("The chart's colours as a plain hex list (RGB charts). Can be pasted back into the New-chart dialog."), tr("Create Chart (best-effort)")),
            ("{name}-i1profiler.txt / .pxf", "runs/runN", tr("The patch set in i1Profiler's formats, for measuring with an i1iSis in i1Profiler."), tr("Create Chart (best-effort)")),
        ]),
        (tr("Files from measuring and refining"), [
            ("reads/read1.ti3, read2.ti3 …", "runs/runN/reads", tr("Individual readings when you use “Read again & average”; averaged back into {name}.ti3 when you finish."), tr("Measure tab")),
            ("preconditioning.ti3 / .icc", "runs/runN", tr("Copies of a previous run's measurement + profile, used to aim the next chart better."), tr("Refine")),
            ("merged.ti3 / merged.icc", "runs/runN", tr("The build-time merge of your new measurement with the pre-conditioning one. The installed profile still gets the clean {name}.icc name."), tr("Build Profile (refinement)")),
            ("calibrated.icc", "runs/runN", tr("Your profile with calibration curves baked in (applycal), when the calibration workflow is on."), tr("Build Profile")),
            ("reports/report_*.json", "runs/runN/reports", tr("Dated measurement reports (accuracy & drift), when “Save a measurement report” is on. Compared over time in the Measurement report window."), tr("Measure tab")),
        ]),
        (tr("Files from the quality check"), [
            ("Quality_Check_1_{name}.txt", "runs/runN", tr("A readable quality report — grade, explanation, worst strips, full output. Numbered so checks don't overwrite each other. Safe to delete."), tr("Check & Refine")),
            ("Refine_Strips_{name}.txt", "runs/runN", tr("The list of strips to re-measure after a check; the guided refinement reads it back."), tr("Check & Refine")),
        ]),
        (tr("Scanner / camera profiling (Build profile with scanner or camera)"), [
            ("<target>.cht / .cie", "runs/runN", tr("The recognition template + reference values for reading a scanned or photographed target."), tr("Create scanner/camera target")),
            ("patchbox.cht, patchbox-sample.cht", "runs/runN", tr("Intermediate patch-box templates the scanner tool builds while it locates the target on your scan. Safe to delete."), tr("Scanner/camera profiling")),
            ("*-aligned.cht, *-aligned-patchbox*.cht, printer-p2-*.cht, p2s1-*.cht", "runs/runN", tr("Per-page alignment templates produced while matching each scanned page to the chart. They pile up with multi-page charts — safe to delete."), tr("Scanner/camera profiling")),
            ("*-diag.tif", "runs/runN", tr("A diagnostic image showing where the tool found each patch — handy if recognition went wrong, otherwise safe to delete."), tr("Scanner/camera profiling")),
        ]),
        (tr("Project-level files and folders"), [
            ("project.json", "(project root)", tr("ChromIQ's manifest: current run + run history. Please don't edit."), tr("Created on first use")),
            ("meta.json", "runs/runN", tr("Remembers the run's settings so reopening restores them."), tr("Each run")),
            ("runs/run1, run2 …", "(project root)", tr("One folder per profile build; the newest is current. Old runs are your history — delete a whole runN if not needed."), tr("Each build")),
            ("cal/", "(project root)", tr("The optional calibration target shared by all runs: {name}-cal.ti1/.ti2/_NN.tif, -cal.ti3, -cal.cal."), tr("Calibration workflow")),
            ("exports/", "(project root)", tr("i1Profiler exports made from the Tools menu."), tr("Tools menu")),
            ("Where are my files.txt", "(project root)", tr("This guide as a text file. ChromIQ never reads it back; edit or delete freely."), tr("Created on first use")),
        ]),
    ]


def file_guide_html() -> str:
    """Rich-text (HTML) table for the Welcome/Help card (Knut)."""
    def esc(s: str) -> str:
        return html.escape(s)

    parts = [f"<p>{esc(_intro())}</p>"]
    for title, rows in _rows():
        parts.append(f"<p style='margin:14px 0 4px'><b>{esc(title)}</b></p>")
        parts.append("<table cellspacing='0' cellpadding='4' width='100%' "
                     "style='border-collapse:collapse'>")
        parts.append(
            "<tr style='color:#888'>"
            f"<th align='left'>{esc(tr('File'))}</th>"
            f"<th align='left'>{esc(tr('Folder'))}</th>"
            f"<th align='left'>{esc(tr('What it is'))}</th>"
            f"<th align='left'>{esc(tr('Created by'))}</th></tr>")
        for f, folder, desc, origin in rows:
            parts.append(
                "<tr>"
                f"<td valign='top'><code>{esc(f)}</code></td>"
                f"<td valign='top'>{esc(folder)}</td>"
                f"<td valign='top'>{esc(desc)}</td>"
                f"<td valign='top'>{esc(origin)}</td></tr>")
        parts.append("</table>")
    parts.append(f"<p style='margin-top:14px'>{esc(_outro())}</p>")
    return "".join(parts)


def file_guide_body() -> str:
    """Plain-text version for the ``Where are my files.txt`` sidecar."""
    lines = [_intro(), ""]
    for title, rows in _rows():
        lines.append(title.upper())
        for f, folder, desc, origin in rows:
            lines.append(f"  • {f}  [{folder}]")
            lines.append(f"      {desc}")
            lines.append(f"      Created by: {origin}")
        lines.append("")
    lines.append(_outro())
    return "\n".join(lines)
