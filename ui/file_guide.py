"""The "Where are my files?" folder guide (#125, Knut).

One place that explains every file ChromIQ writes into a project folder: which
feature creates it, when, and what it's for. Shown as its own card in the
Welcome/Help window (Basti: the help lives there, not in the Tools popup); the
same information (in English) is also dropped into each project folder as
``Where are my files.txt`` (see ``core.file_manager``).

The text lives here as a single ``tr()`` catalog key so it is translated like
every other help text.
"""
from __future__ import annotations

from core.i18n import tr


def file_guide_card_title() -> str:
    """Tile title in the Welcome/Help window's card grid."""
    return tr("Where are my files? (folder guide)")


def file_guide_card_subtitle() -> str:
    return tr("Every file a ChromIQ project folder can contain — what "
              "created it, when, and what it's for.")


def file_guide_body() -> str:
    return tr(
        "Every ChromIQ project lives in its own folder (inside ~/ChromIQ, or "
        "your custom output folder from Settings), named after the profile. "
        "As you work through the steps — create a chart, print, measure, "
        "build, refine — files accumulate in it. This card lists every file "
        "you can meet, which feature creates it, when, and what it's for. "
        "{name} stands for your profile's name.\n\n"
        "The files you'll actually want:\n\n"
        "• runs/runN/{name}.icc — your finished ICC profile, built on the "
        "Build Profile tab. This is the file you install or share.\n"
        "• runs/runN/{name}_01.tif, {name}_02.tif … — the printable chart "
        "pages, created when you generate a chart. Print these.\n"
        "• runs/runN/{name}.ti3 — your measurements, written when a chart "
        "reading completes. This is the file the profile is built from — "
        "keep it, and any profile can be rebuilt later.\n\n"
        "The working files of a chart (created when you generate one):\n\n"
        "• {name}.ti1 — the list of patch colours (from targen or the patch "
        "generators). The chart's recipe, before it's laid out on paper.\n"
        "• {name}.ti2 — the laid-out chart: which colour sits at which "
        "position. Measuring needs it, so ChromIQ knows what it's reading.\n"
        "• {name}.channels.json — a small info file that records the chart's "
        "ink channels and (for engine charts) the exact layout and creation "
        "recipe, so reopening the chart later restores everything.\n"
        "• {name}.pdf — the chart as a vector PDF, only when “Also export "
        "PDF” is ticked in the layout options.\n"
        "• {name}.ps — a PostScript copy created for printing (the print "
        "pipeline sends this to the printer, bypassing colour management).\n\n"
        "Hand-off files, so the chart can be used outside ChromIQ (written "
        "with every generated chart, best-effort):\n\n"
        "• {name}-colours.txt — the chart's colours as a plain hex list (RGB "
        "charts only). Can be pasted back into the New-chart dialog.\n"
        "• {name}-i1profiler.txt / .pxf — the patch set in i1Profiler's "
        "formats, for measuring with an i1iSis in i1Profiler.\n"
        "• {name}.cht / {name}.cie — a recognition template + reference "
        "values for scanner/camera measurement. Created by the “Create "
        "scanner or camera target” tool after you have a measurement.\n\n"
        "Files from measuring and refining:\n\n"
        "• reads/read1.ti3, read2.ti3 … — the individual readings when you "
        "use “Read again & average”; they're averaged back into "
        "{name}.ti3 when you finish.\n"
        "• preconditioning.ti3 / preconditioning.icc — copies of a previous "
        "run's measurement and profile, created when you refine a build; "
        "ChromIQ uses them to aim the next chart better.\n"
        "• merged.ti3 / merged.icc — the build-time merge of your new "
        "measurement with the pre-conditioning one (ChromIQ-style "
        "refinement). The installed profile still gets the clean "
        "{name}.icc name.\n"
        "• calibrated.icc — your profile with the calibration curves baked "
        "in (applycal), when the calibration workflow is on.\n\n"
        "Project-level files and folders:\n\n"
        "• project.json — ChromIQ's project manifest: which run is current, "
        "the run history. Created the first time the project is used; "
        "please don't edit it.\n"
        "• meta.json (one per run) — remembers the run's settings (layout "
        "knobs, averaging method, parent run) so reopening restores them.\n"
        "• runs/run1, run2 … — one folder per profile build. Each is "
        "self-contained; the newest is the current one. Old runs are your "
        "history — delete a whole runN folder if you don't need it.\n"
        "• cal/ — the optional calibration target, shared by all runs: "
        "{name}-cal.ti1/.ti2/_NN.tif (the chart), {name}-cal.ti3 (its "
        "measurement), {name}-cal.cal (the curves applycal uses).\n"
        "• exports/ — i1Profiler exports made from the Tools menu.\n"
        "• Where are my files.txt — this guide as a text file, written once "
        "into each project folder. ChromIQ never reads it back; edit or "
        "delete it freely.\n\n"
        "Safe to tidy: everything can in principle be recreated except your "
        "measurements ({name}.ti3, reads/, cal/{name}-cal.ti3) — those "
        "represent real ink on real paper and are worth keeping. The "
        "quickest tidy-up is deleting old runN folders you no longer need."
    )
