"""Welcome dialog — opens on first launch and via the masthead "?" button.

Two-page QStackedWidget:
  • Page 0 — six clickable WorkflowCard tiles arranged 3x2
  • Page 1 — numbered step instructions for the selected workflow

Theme-aware via set_appearance(mode); persists the "show on startup" choice
through AppSettings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPaintEvent, QPen,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.fade_scroll import FadeScrollArea
from ui.styles import SPEC_MAGENTA, TAB_COLORS
from core.i18n import tr

if TYPE_CHECKING:
    from core.settings import AppSettings


# ---------------------------------------------------------------------------
# Workflow content
# ---------------------------------------------------------------------------

# Each step is (tab_index_1based, text) or (tab_index_1based, text, optional_bool).
# tab_index drives the coloured badge. The displayed number inside the badge is
# the step count, not the tab number — the colour already tells you which tab.
# optional=True renders the badge outlined (rather than filled) and dims the
# text slightly, marking steps that improve quality but aren't required.
WORKFLOWS: list[dict] = [
    {
        "key": "first_profile",
        "title": tr("Build my first ICC profile"),
        "subtitle": tr("The full walk-through from blank chart to finished profile."),
        "steps": [
            (1, tr("On the Create Chart tab, pick which instrument you'll measure "
                "with (e.g. i1Pro) and choose your paper size. Set the number "
                "of pages — more pages means more patches and a more accurate "
                "profile. Two or three A4 pages is a sensible starting point "
                "(around 1000–1500 patches with an i1Pro); raise it for "
                "critical work, or drop it back if you're just experimenting. "
                "Give the chart a descriptive name — it carries through to "
                "every file downstream (.ti2, .ti3 and the final .icc). A "
                "good convention is printer + paper + date, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_2026-05”; avoid spaces and "
                "special characters. Click “Create Chart”. ChromIQ writes a "
                "chart TIFF plus a .ti2 file that records exactly where every "
                "patch sits on the page.")),
            (2, tr("Move to the Print Chart tab and pick your printer and media. "
                "Driver colour management must be OFF — if the driver re-maps "
                "colours the patches won't match their definition and the "
                "profile will be wrong. On macOS ChromIQ disables it "
                "automatically; just confirm nothing in the print dialog has "
                "switched it back on. On Windows and other systems you need "
                "to switch it off yourself in the driver dialog. "
                "Click “Print”.")),
            (3, tr("On the Measure tab, connect and switch on your "
                "spectrophotometer, then place the printed chart on a white "
                "surface (a plain sheet of paper underneath works perfectly) — "
                "a coloured or dark backing can bleed through thin stock and "
                "skew the reading. Before you scan, check the “Disable "
                "bidirectional reading” option: it's ON by default, the safest "
                "setting for any spectro. If you use an i1Pro and you're used "
                "to scanning each strip in one continuous left-and-right "
                "motion, turn this option OFF first — leaving it on while you "
                "sweep bidirectionally is the classic cause of mis-recognised "
                "strips and bad data. Click “Measure Chart” and follow the "
                "strip-by-strip prompts.")),
            (4, tr("On the Build Profile tab the new .ti3 measurement is "
                "already loaded. If you like, fill in the optional metadata "
                "fields (Description, Manufacturer, Copyright) — they get "
                "embedded in the .icc header so colour-management apps can "
                "identify it later. Then click “Build Profile”. When the "
                "build finishes a result popup appears — install the .icc "
                "system-wide from there, or jump to Check & Refine to "
                "verify its accuracy and start guided refinement (the steps "
                "below) for a noticeably more accurate profile.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "two_pass",
        "title": tr("Build a high-quality profile (2-pass)"),
        "subtitle": tr("A pre-conditioning pass produces a sharper second profile."),
        "steps": [
            (1, tr("Start a fresh chart on the Create Chart tab. Pick the "
                "instrument and paper size as normal. For this first pass "
                "you can keep the page count low — one A4 page is plenty. "
                "The pre-conditioning profile is throwaway, its only job is "
                "to tell ChromIQ where your printer is most non-linear so "
                "the second-pass chart can place patches more cleverly. "
                "Save your paper and ink for the second pass. Give the "
                "chart a descriptive name — it carries through to every "
                "file downstream (.ti2, .ti3 and the final .icc). A good "
                "convention is printer + paper + a “_pre” suffix for this "
                "pre-conditioning pass, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_pre_2026-05”; avoid spaces "
                "and special characters. Click “Create Chart”. This first "
                "chart will produce the pre-conditioning profile — not yet "
                "the final one.")),
            (2, tr("Move to the Print Chart tab and pick your printer and media. "
                "Driver colour management must be OFF — if the driver re-maps "
                "colours the patches won't match their definition and the "
                "first-pass profile will be wrong. On macOS ChromIQ disables "
                "it automatically; just confirm nothing in the print dialog "
                "has switched it back on. On Windows and other systems you "
                "need to switch it off yourself in the driver dialog. "
                "Click “Print”.")),
            (3, tr("On the Measure tab, connect and switch on your "
                "spectrophotometer, then place the printed chart on a white "
                "surface (a plain sheet of paper underneath works perfectly) — "
                "a coloured or dark backing can bleed through thin stock and "
                "skew the reading. Before you scan, check the “Disable "
                "bidirectional reading” option: it's ON by default, the safest "
                "setting for any spectro. If you use an i1Pro and you're used "
                "to scanning each strip in one continuous left-and-right "
                "motion, turn this option OFF first — leaving it on while you "
                "sweep bidirectionally is the classic cause of mis-recognised "
                "strips and bad data. Click “Measure Chart” and follow the "
                "strip-by-strip prompts.")),
            (4, tr("On the Build Profile tab click “Build Profile” to "
                "produce the first .icc. Treat this profile as a colour-"
                "space map rather than a finished result. In the result popup "
                "(or in Check & Refine) click “Use as Pre-conditioning "
                "Profile” — ChromIQ jumps back to the Create Chart tab with "
                "the new .icc loaded as the pre-conditioning profile.")),
            (1, tr("Optionally raise the patch count — a second-pass chart "
                "benefits from more patches because they're placed where "
                "the printer is most non-linear. Click “Create Chart” to "
                "generate the high-quality chart.")),
            (2, tr("Print the new chart on the Print Chart tab. Driver colour "
                "management must be OFF — on macOS ChromIQ disables it "
                "automatically; just confirm nothing in the print dialog has "
                "switched it back on. On Windows and other systems you need "
                "to switch it off yourself in the driver dialog. "
                "Click “Print”.")),
            (3, tr("On the Measure tab, connect the spectrophotometer and place "
                "the printed chart on a white surface. Check the “Disable "
                "bidirectional reading” option: it's ON by default, the "
                "safest setting for any spectro. If you use an i1Pro and "
                "you're used to scanning each strip in one continuous "
                "left-and-right motion, turn this option OFF first — "
                "leaving it on while you sweep bidirectionally is the "
                "classic cause of mis-recognised strips and bad data. "
                "Click “Measure Chart” and follow the strip-by-strip "
                "prompts.")),
            (4, tr("Click “Build Profile” one more time. The result is "
                "noticeably more accurate than the first-pass profile "
                "because targen could place patches where they actually "
                "mattered. This is the profile to install.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold. After a clean 2-pass build "
                "the result is often already good enough; the steps below "
                "are for squeezing out the last few outliers."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "improve_existing_profile",
        "title": tr("Improve an existing ICC profile"),
        "subtitle": tr("Seed ChromIQ with a current profile to build a sharper one."),
        "steps": [
            (1, tr("On the Create Chart tab, find the “Refinement (Optional)” "
                "section, tick “Refinement profile”, then click “Select "
                "pre-conditioning profile” and pick the existing .icc for "
                "this printer + paper combination. Choose the instrument "
                "and paper size as usual, and give the chart a descriptive "
                "name with a “_v2” (or similar) suffix, e.g. "
                "“EpsonP900_HahnemuhlePhotoRag_v2_2026-05”. Because the "
                "seed profile tells ChromIQ exactly where your printer is "
                "most non-linear, raise the patch count so those tricky "
                "regions get more samples. Click “Create Chart”.")),
            (2, tr("Move to the Print Chart tab and pick your printer and media. "
                "Driver colour management must be OFF — if the driver re-maps "
                "colours the patches won't match their definition and the "
                "refined profile will be wrong. On macOS ChromIQ disables it "
                "automatically; just confirm nothing in the print dialog has "
                "switched it back on. On Windows and other systems you need "
                "to switch it off yourself in the driver dialog. "
                "Click “Print”.")),
            (3, tr("On the Measure tab, connect and switch on your "
                "spectrophotometer, then place the printed chart on a white "
                "surface (a plain sheet of paper underneath works perfectly) — "
                "a coloured or dark backing can bleed through thin stock and "
                "skew the reading. Before you scan, check the “Disable "
                "bidirectional reading” option: it's ON by default, the safest "
                "setting for any spectro. If you use an i1Pro and you're used "
                "to scanning each strip in one continuous left-and-right "
                "motion, turn this option OFF first — leaving it on while you "
                "sweep bidirectionally is the classic cause of mis-recognised "
                "strips and bad data. Click “Measure Chart” and follow the "
                "strip-by-strip prompts.")),
            (4, tr("On the Build Profile tab the new .ti3 is already loaded. "
                "If you like, fill in the optional metadata fields "
                "(Description, Manufacturer, Copyright) — they get embedded "
                "in the .icc header so colour-management apps can identify "
                "it later. Click “Build Profile”. When the build finishes a "
                "result popup appears — the new .icc is more accurate than "
                "the seed profile because ChromIQ placed patches where they "
                "mattered. Install it from the popup, or jump to Check & "
                "Refine to verify it before installing.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "print_chart",
        "title": tr("Print an existing test chart"),
        "subtitle": tr("You already have a chart on disk and just want to print it."),
        "steps": [
            (2, tr("On the Print Chart tab, click “Load .ti2” and pick the "
                "chart definition file. ChromIQ finds the matching TIFF "
                "pages automatically — you don't pick them by hand.")),
            (2, tr("Choose your printer, paper type and any quality settings "
                "the print dialog exposes. Make sure driver colour "
                "management is OFF, just like a fresh print.")),
            (2, tr("Click “Print”. The TIFF is sent as raw PostScript so no "
                "driver filter alters the patches on the way to the "
                "printer.")),
            (3, tr("Once the print is dry, head to the Measure tab and connect "
                "your spectrophotometer, then place the chart on a white "
                "surface (a plain sheet of paper underneath works). Before "
                "scanning, check the “Disable bidirectional reading” option "
                "— it's ON by default which is safest, but if you use an "
                "i1Pro and you're used to scanning each strip in one "
                "continuous left-and-right motion, turn it OFF first. "
                "Click “Measure Chart” and follow the strip-by-strip "
                "prompts.")),
            (4, tr("On the Build Profile tab the new .ti3 is already loaded. "
                "If you like, fill in the optional metadata fields "
                "(Description, Manufacturer, Copyright) — they get embedded "
                "in the .icc header so colour-management apps can identify "
                "it later. Then click “Build Profile”. When the build "
                "finishes a result popup appears — install the .icc system-"
                "wide from there, or jump to Check & Refine to verify its "
                "accuracy and start guided refinement (the steps below) for "
                "a noticeably more accurate profile.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "measure_existing",
        "title": tr("Measure a chart I already printed"),
        "subtitle": tr("Jump straight to reading patches with your spectrophotometer."),
        "steps": [
            (3, tr("On the Measure tab, click “Load Chart File” and pick the "
                ".ti1 or .ti2 that matches your printed chart. The .ti2 is "
                "preferred where available — it contains the exact patch "
                "positions printtarg used.")),
            (3, tr("Connect and switch on the spectrophotometer. ChromIQ "
                "detects it automatically; a green status pill appears in "
                "the toolbar when it's ready.")),
            (3, tr("Check the “Disable bidirectional reading” option before "
                "you start. It's ON by default — safe for any spectro but "
                "slower. If you use an i1Pro and you're used to scanning "
                "each strip in one continuous left-and-right sweep, turn "
                "it OFF first; leaving it on while you scan bidirectionally "
                "causes mis-recognised strips and bad measurements.")),
            (3, tr("Click “Measure Chart” and follow the strip-by-strip "
                "prompts. Results save as a .ti3 next to the chart, "
                "ready for the Build Profile tab.")),
            (4, tr("On the Build Profile tab the new .ti3 is already loaded. "
                "If you like, fill in the optional metadata fields "
                "(Description, Manufacturer, Copyright) — they get embedded "
                "in the .icc header so colour-management apps can identify "
                "it later. Then click “Build Profile”. When the build "
                "finishes a result popup appears — install the .icc system-"
                "wide from there, or jump to Check & Refine to verify its "
                "accuracy and start guided refinement (the steps below) for "
                "a noticeably more accurate profile.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "build_from_measurement",
        "title": tr("Build a profile from an existing measurement"),
        "subtitle": tr("You have a .ti3 file — turn it into an ICC profile."),
        "steps": [
            (4, tr("On the Build Profile tab, click “Load .ti3” and pick your "
                "existing measurement file. The matching .ti1/.ti2 is "
                "found and loaded automatically.")),
            (4, tr("If you like, fill in the optional metadata fields "
                "(Description, Manufacturer, Copyright). These get embedded "
                "in the .icc header so colour-management apps can identify "
                "the profile later — you can leave them empty if you don't "
                "care.")),
            (4, tr("Click “Build Profile”. The .icc lands next to the .ti3 "
                "in the same folder.")),
            (4, tr("A result popup appears with three actions: install the "
                "profile to your system colour folder, jump to Check & "
                "Refine to inspect its accuracy, or feed it back as a "
                "pre-conditioning profile (workflow 2). You can dismiss "
                "the popup and come back to any of these later.")),
            (5, tr("Optional — On the Check & Refine tab click “Analyse”. "
                "ChromIQ runs profcheck and flags any patches whose ΔE is "
                "above your refinement threshold (2.0 is a sensible starting "
                "point). If outliers are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected strips."),
                True),
            (3, tr("Optional — Re-measure the strips ChromIQ marks. The new "
                "readings are merged into the .ti3; patches that were already "
                "good are kept as they are."),
                True),
            (4, tr("Optional — Click “Build Profile” again. The refined .ti3 "
                "produces a more accurate profile."),
                True),
            (5, tr("Optional — Run “Analyse” one more time to confirm the "
                "worst outliers are now below threshold. Repeat the refine "
                "loop as often as you like — each pass should reduce ΔE "
                "further."),
                True),
        ],
    },
    {
        "key": "refine",
        "title": tr("Refine an existing profile"),
        "subtitle": tr("Re-measure only the strips where ΔE is worst."),
        "steps": [
            (5, tr("On the Check & Refine tab, click “Load .ti3” and open the "
                "measurement of the profile you want to improve. The "
                "matching .icc loads automatically.")),
            (5, tr("Click “Analyse”. ChromIQ runs profcheck and looks for "
                "patches whose ΔE is above your refinement threshold "
                "(configurable in the panel — 2.0 is a sensible "
                "starting point).")),
            (5, tr("If outlier patches are found, ChromIQ offers to send you "
                "back to the Measure tab to re-read only the affected "
                "strips — much faster than reprinting and re-measuring "
                "the whole chart.")),
            (3, tr("Re-measure the strips ChromIQ marks. The new readings "
                "are merged into the .ti3 — old patches are kept where "
                "they were already good.")),
            (4, tr("Click “Build Profile” again. The refined .ti3 produces "
                "a more accurate profile, and you can repeat this cycle "
                "until the worst outliers are below threshold.")),
        ],
    },
    {
        "key": "check_visualise",
        "title": tr("Visualise a profile's gamut"),
        "subtitle": tr("See in 3D what colours a printer can and can't reproduce."),
        "steps": [
            (5, tr("On the Check & Refine tab, load the .icc profile you "
                "want to inspect. A matching .ti3 is helpful but not "
                "required for the gamut viewer.")),
            (5, tr("Open the Gamut Viewer pane. ChromIQ runs iccgamut on the "
                "profile and renders the printer's colour volume as a 3D "
                "mesh that you can rotate, zoom and pan freely.")),
            (5, tr("Optionally overlay a reference gamut (e.g. sRGB or "
                "AdobeRGB) to see at a glance which colours of the "
                "reference space the printer can hit and which it has "
                "to clip.")),
            (5, tr("This workflow is read-only — no files are written, so "
                "you can poke around freely without changing anything.")),
        ],
    },
    {
        "key": "scanner_profile",
        "title": tr("Profile my scanner or camera"),
        "subtitle": tr("Colour-profile a scanner or a camera — from a chart you "
                       "measured, or a standard target you own."),
        "steps": [
            (3, tr("Print and measure a ChromIQ chart as usual, and keep its "
                "recognition files: after measuring, tick “Also save "
                "scanner-profiling files for this chart” in the All Stripes Read "
                "or Profile Quality Assessment window — or run Tools ▸ Create "
                "scanner or camera target on any measured chart. This writes the "
                "chart's .cht + .cie files.")),
            (3, tr("Scan the printed chart on the scanner you want to profile as "
                "a plain RGB TIFF, with the scanner's own auto-correction and "
                "colour management turned OFF. Scan at 600 dpi or more — "
                "1200 dpi is preferred; 300 dpi is too coarse for clean patch "
                "reads.")),
            (3, tr("Open Tools ▸ Build scanner or camera profile. Pick the "
                "measured chart and the scan, drag the four corners over the "
                "patch area until the green grid lines up with the real patches, "
                "and build. ChromIQ runs scanin + colprof and writes an ICC "
                "profile next to the scan. Multi-page charts: place each page's "
                "scan (and, if you like, several scans per page to average), all "
                "combined into one profile.")),
            (3, tr("No ChromIQ chart — or profiling a camera? In Build scanner "
                "or camera profile choose “A standard target I own”, pick your "
                "target type (IT8, X-Rite ColorChecker, LaserSoft…) and load the "
                "reference data file that came with it, then scan the target — or "
                "photograph it for a camera. Everything else is the same. See the "
                "window's ⓘ for how to capture a camera shot."), True),
            (3, tr("For the best quality when you mainly scan your own "
                "colour-managed prints, print a fresh chart through your normal "
                "print workflow, measure THAT sheet, and profile from it — its "
                "colours then match what you actually scan."), True),
        ],
    },
    {
        "key": "printer_from_scan",
        "title": tr("Profile my printer with a flatbed scanner"),
        "subtitle": tr("No spectrophotometer? A profiled scanner can measure "
                       "your chart and build the printer profile."),
        "steps": [
            (3, tr("First profile your scanner — it's about to become your "
                "measuring instrument. Follow the “Profile my scanner or "
                "camera” workflow once (from a measured ChromIQ chart or a "
                "standard target you own); the scanner profile is reused for "
                "every printer profile you build this way.")),
            (1, tr("On the Create Chart tab, create a chart for your printer "
                "and paper. A ChromIQ layout-engine chart is ideal — its patch "
                "geometry travels with the chart, so the reading grid knows "
                "exactly where every patch sits.")),
            (2, tr("Print the chart from the Print Chart tab as usual, with "
                "driver colour management OFF. You do NOT measure it — the "
                "scanner will do that.")),
            (3, tr("Scan every printed page on your profiled scanner as a "
                "plain RGB TIFF, with the scanner's auto-correction and colour "
                "management turned OFF — the same settings you profiled it "
                "with. Scan at 600 dpi or more — 1200 dpi is preferred; "
                "300 dpi is too coarse for clean patch reads.")),
            (3, tr("Open Tools ▸ Build scanner or camera profile and tick "
                "“Profile my printer from this scan”. Pick your scanner "
                "profile, the chart you printed (its .ti2), and each page's "
                "scan; drag the four corners so the grid lines up with the "
                "patches on every page, then build. ChromIQ reads the patches "
                "through the scanner profile and writes a printer ICC "
                "profile.")),
            (3, tr("Save the diagnostic image and take any alignment warning "
                "seriously — a misplaced grid reads the wrong patches and "
                "ruins the profile. And keep expectations honest: a flatbed "
                "is a fine everyday instrument, but not a spectrophotometer."),
             True),
        ],
    },
]


# ---------------------------------------------------------------------------
# Dictionary and terminology (Knut, #108) — every term, phrase and
# abbreviation the app (and printer/scanner profiling generally) throws at a
# newcomer, alphabetical, in plain language. Rendered by its own detail view
# (no numbered steps).
GLOSSARY: list[tuple[str, str]] = [
    (tr(".cht file"),
     tr("ArgyllCMS's recognition file: where every patch sits on a scanned target, so software can find them in the image.")),
    (tr(".cie file"),
     tr("The reference colours of a target — what each patch SHOULD measure — used together with a scan to build a scanner profile.")),
    (tr(".ti1 / .ti2 / .ti3 files"),
     tr("ArgyllCMS's chart pipeline: .ti1 = the designed patch set, .ti2 = the printed layout (which patch sits where), .ti3 = the measurements. colprof turns a .ti3 into a profile.")),
    (tr("Black point"),
     tr("The darkest colour a printer and paper can produce. Everything darker in an image gets squeezed up to this level.")),
    (tr("Calibration"),
     tr("Bringing a device to a fixed, repeatable state (e.g. printer ink limits or a monitor's brightness). Done BEFORE profiling — a profile describes a device, calibration sets it.")),
    (tr("Chart / test chart"),
     tr("A printed page of colour patches with known device values. Measuring what the printer actually made of them is the raw material of a profile. Also called a target.")),
    (tr("chartread"),
     tr("The ArgyllCMS command-line tool that reads a printed chart with a spectrophotometer. ChromIQ runs it on the Measure tab.")),
    (tr("CMYK"),
     tr("Cyan, magenta, yellow and black — printing inks. ChromIQ profiles RGB-driven printers, whose drivers convert to ink internally.")),
    (tr("Colorimeter"),
     tr("A measuring device with a few colour filters — fine for monitors, not suitable for printer profiling. Compare spectrophotometer.")),
    (tr("colprof"),
     tr("The ArgyllCMS tool that turns a measurement file (.ti3) into an ICC profile.")),
    (tr("D50"),
     tr("The standard 'daylight' illuminant of printing: warmish daylight at 5000 K. Profiles and measurements are referenced to it, and prints should be judged under it.")),
    (tr("Delta E (ΔE)"),
     tr("The distance between two colours as a single number. Below about 1 is invisible; 2–4 is visible side by side; above 6 is obvious. Used to judge profile quality.")),
    (tr("Device link"),
     tr("A special profile that converts directly from one device's colours to another's, in one step, without the usual detour through a neutral colour space.")),
    (tr("dpi / ppi"),
     tr("Dots (printer) or pixels (scanner/image) per inch. For scanning charts: 600 dpi is fine, 1200 dpi preferred; the reading software averages each patch anyway.")),
    (tr("Fiducial marks"),
     tr("Small crosses or corners printed outside a target's patch area. Scanning software uses them to locate the patch grid precisely.")),
    (tr("Gamma / TRC"),
     tr("The tone curve relating stored values to brightness. Profiles carry it per channel (the 'shaper' in shaper/matrix profiles).")),
    (tr("Gamut"),
     tr("All the colours a device can reproduce. A printer's gamut is much smaller than what a camera captures or a monitor shows — the profile manages the squeeze.")),
    (tr("Gamut volume"),
     tr("A single number for a gamut's size (in Lab space). Useful for comparing papers or printers; bigger is roomier, not automatically better.")),
    (tr("ICC profile"),
     tr("A standard file (.icc) describing how a device reproduces colour. Colour-managed programs use it to translate between device colours and real-world colours.")),
    (tr("Illuminant"),
     tr("The light a measurement or profile assumes. Printing uses D50; changing the light changes how prints look (see metamerism).")),
    (tr("Ink limit"),
     tr("The maximum ink a paper can take before problems (bleeding, pooling). RGB printer drivers handle this internally.")),
    (tr("Instrument"),
     tr("The measuring device — in ChromIQ usually a spectrophotometer (i1Pro, ColorMunki, SpectroScan) or, with the scanner workflow, a profiled flatbed scanner.")),
    (tr("Lab (CIELAB)"),
     tr("A device-independent colour space built around human vision: L* is lightness, a* red–green, b* yellow–blue. The neutral meeting ground profiles translate through.")),
    (tr("LUT profile"),
     tr("A profile built as a lookup table — flexible enough for a printer's irregular gamut. Compare matrix profile.")),
    (tr("Matrix profile"),
     tr("A compact profile type: one tone curve per channel plus a 3×3 matrix. Great for well-behaved devices (monitors, scanners); ChromIQ's recommended type for scanner profiles.")),
    (tr("Measurement modes (M0/M1/M2)"),
     tr("Standard instrument modes differing in UV content: M0 legacy, M1 includes UV (matches D50), M2 excludes UV ('UV-cut') — matters on OBA-rich papers.")),
    (tr("Metamerism"),
     tr("Two colours matching under one light but not under another. The reason prints are judged under standard light (D50).")),
    (tr("OBA (optical brighteners)"),
     tr("Additives that make paper look whiter under UV-containing light. They can shift measurements and make prints look different across lighting.")),
    (tr("Patch"),
     tr("One coloured rectangle on a test chart. More patches = more measured colours = a potentially more accurate profile.")),
    (tr("Perceptual (rendering intent)"),
     tr("Squeezes the whole image smoothly into the printer's gamut, keeping relationships between colours. Good default for photos.")),
    (tr("printtarg"),
     tr("The ArgyllCMS tool that lays out a patch set onto printable pages (ChromIQ's layout engine is an alternative to it).")),
    (tr("Profile (verb)"),
     tr("To measure how a device reproduces colour and store the result as an ICC profile.")),
    (tr("Quality (profile build)"),
     tr("colprof's -q setting: how finely the profile models the measurements. Higher = slower build, bigger file, usually only marginally better.")),
    (tr("Relative colorimetric (rendering intent)"),
     tr("Reproduces in-gamut colours exactly, clips out-of-gamut ones to the edge. Good for proofing; can flatten saturated areas.")),
    (tr("Rendering intent"),
     tr("The strategy for squeezing colours into a smaller gamut: perceptual, relative colorimetric, saturation, or absolute colorimetric.")),
    (tr("RGB"),
     tr("Red, green, blue — how images, monitors, scanners and (from the computer's side) most photo printers describe colour.")),
    (tr("Saturation (rendering intent)"),
     tr("Keeps colours as vivid as possible at the expense of accuracy — for graphs and signage, not photos.")),
    (tr("scanin"),
     tr("The ArgyllCMS tool that reads patch values out of a SCANNED image of a target, using a .cht file to find the patches.")),
    (tr("Scanner target (IT8 etc.)"),
     tr("An industrially-made chart with known reference values (e.g. Wolf Faust IT8, LaserSoft), used to profile a scanner or camera.")),
    (tr("Soft-proof"),
     tr("Simulating on screen how an image will look when printed through a given profile — including its gamut limits.")),
    (tr("Spacer"),
     tr("A separator strip between patch rows/columns on a chart, helping strip-reading instruments (and scan alignment) stay on track.")),
    (tr("Spectrophotometer"),
     tr("A measuring device that samples the whole visible spectrum of a patch — the standard instrument for printer profiling.")),
    (tr("Strip"),
     tr("A row of patches read in one sweep by instruments like the i1Pro.")),
    (tr("targen"),
     tr("The ArgyllCMS tool that designs a patch SET (which colours to print) before printtarg/the engine lays it out.")),
    (tr("White point"),
     tr("The colour of the paper itself — the lightest 'colour' a print can contain. Profiles measure and account for it.")),
]


# App-workflow terms (Knut: "patch set, chart layout, layout engine, etc. —
# it should make a bit longer list").
GLOSSARY += [
    (tr("Patch set"),
     tr("The list of colours a chart will contain — designed by targen or by "
        "the generators in the chart editor — before anything is laid out on "
        "paper. Stored as a .ti1 file.")),
    (tr("Chart layout"),
     tr("How a patch set is arranged on the page: patch size, margins, "
        "spacers, strips and page splits. The layout decides what the "
        "instrument (or scanner) can read reliably.")),
    (tr("Layout engine"),
     tr("ChromIQ's own chart-layout generator — an alternative to printtarg. "
        "It records exactly where every patch sits, so scans of its charts "
        "can be read with perfect knowledge of the geometry.")),
    (tr("Preset"),
     tr("A saved set of chart options you can reload with one click. ChromIQ "
        "ships built-in presets (marked ★) and stores the ones you save "
        "yourself; both appear in the Presets dropdown.")),
    (tr("Chart recipe"),
     tr("The saved design of a chart's colour set (which generators, how many "
        "patches, in what order) — carried with the chart so the same design "
        "can be reloaded, edited or reused later.")),
    (tr("Preconditioning profile"),
     tr("A quick first-pass profile used to seed a better second chart: "
        "patch colours are chosen where the printer actually needs them. See "
        "the two-pass workflow.")),
    (tr("Refinement (two-pass)"),
     tr("Building a profile in two rounds: a first chart maps the printer "
        "roughly, a second chart — placed using that knowledge — measures "
        "where it matters. The measurements are merged for the final "
        "profile.")),
    (tr("Averaging (measurements)"),
     tr("Reading the same printed chart more than once and averaging the "
        "measurements. Evens out instrument noise and print unevenness; "
        "ChromIQ offers it after the last strip is read.")),
    (tr("Randomised patch order"),
     tr("Scrambling the printed order of patches so neighbouring strips "
        "don't contain similar colours in sequence. Helps strip-reading "
        "instruments notice when a strip was read wrongly.")),
    (tr("Patch sample area"),
     tr("How much of each patch's centre gets read when profiling from a "
        "scan — shown as the green inner square. Reading only the middle "
        "avoids edges, bleed and slight grid misplacement.")),
    (tr("Reading grid (marquee)"),
     tr("The draggable four-corner frame you place over a scanned target so "
        "ChromIQ knows where every patch sits. The misalignment check warns "
        "when it seems off.")),
    (tr("Demo target"),
     tr("A rendered stand-in scan for a standard target, with exact known "
        "colours and realistic softness/noise. Lets you try the scanner "
        "workflow end-to-end without hardware.")),
    (tr("Driver colour management"),
     tr("The printer driver's own colour correction. It MUST be off when "
        "printing charts — if the driver remaps colours, the measurements "
        "describe the driver, not the printer.")),
    (tr("Bit depth (8/16-bit)"),
     tr("How many steps each colour channel has: 8-bit = 256, 16-bit = "
        "65536. Charts print fine as 8-bit; 16-bit matters for smooth "
        "gradients and some editing workflows.")),
    (tr("Verification (profile check)"),
     tr("Printing and measuring a small chart THROUGH the finished profile "
        "to see how close the result lands (in ΔE). The honest way to judge "
        "a profile — better than trusting the build report.")),
]

GLOSSARY_CARD: dict = {
    "key": "glossary",
    "title": tr("Dictionary and terminology"),
    "subtitle": tr("Every term used in ChromIQ and in printer/scanner "
                   "profiling, explained in plain language."),
    "steps": [],
    "kind": "glossary",
}
WORKFLOWS.append(GLOSSARY_CARD)


# ---------------------------------------------------------------------------
# Painted card icon — geometric placeholder per workflow
# ---------------------------------------------------------------------------

class WorkflowIcon(QWidget):
    """96x96 painted icon. Magenta accent + monochrome lines that flip with theme."""

    SIZE = 96

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._mode = "dark"
        self.setFixedSize(QSize(self.SIZE, self.SIZE))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self.update()

    def _fg(self) -> QColor:
        return QColor("#22211f" if self._mode == "light" else "#e6e6e6")

    def paintEvent(self, _ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fg = self._fg()
        accent = QColor(SPEC_MAGENTA)
        stroke = 2.4

        s = self.SIZE
        if self._key == "first_profile":
            # 5 nodes connected in a row, last one filled magenta
            cy = s / 2
            n = 5
            pad = 12
            step = (s - 2 * pad) / (n - 1)
            p.setPen(QPen(fg, stroke))
            for i in range(n - 1):
                x0 = pad + i * step
                x1 = pad + (i + 1) * step
                p.drawLine(int(x0), int(cy), int(x1), int(cy))
            for i in range(n):
                cx = pad + i * step
                r = 7 if i == n - 1 else 5
                if i == n - 1:
                    p.setBrush(accent)
                    p.setPen(Qt.PenStyle.NoPen)
                else:
                    p.setBrush(QColor(0, 0, 0, 0))
                    p.setPen(QPen(fg, stroke))
                p.drawEllipse(int(cx - r), int(cy - r), 2 * r, 2 * r)

        elif self._key == "print_chart":
            # Sheet (rectangle) with a 4x6 patch grid; one accent patch
            margin = 14
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(margin, margin, s - 2 * margin, s - 2 * margin, 4, 4)
            cols, rows = 6, 4
            cell_w = (s - 2 * margin - 8) / cols
            cell_h = (s - 2 * margin - 8) / rows
            ox = margin + 4
            oy = margin + 4
            p.setPen(Qt.PenStyle.NoPen)
            accent_cell = (2, 1)
            for r in range(rows):
                for c in range(cols):
                    x = ox + c * cell_w
                    y = oy + r * cell_h
                    if (r, c) == accent_cell:
                        p.setBrush(accent)
                    else:
                        col = QColor(fg)
                        col.setAlpha(110)
                        p.setBrush(col)
                    p.drawRect(int(x + 1), int(y + 1), int(cell_w - 2), int(cell_h - 2))

        elif self._key == "measure_existing":
            # Spectro head (rounded rectangle with notch) above a strip of patches.
            # Sized to leave a generous gap to the card title below.
            head_w, head_h = 50, 24
            head_x = (s - head_w) / 2
            head_y = 19
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(int(head_x), int(head_y), head_w, head_h, 7, 7)
            # Aperture
            p.setBrush(accent)
            p.setPen(Qt.PenStyle.NoPen)
            ap = 7
            p.drawEllipse(int(s / 2 - ap / 2), int(head_y + head_h - 4), ap, ap)
            # Patches strip — integer dimensions keep every cell and gap uniform.
            n = 6
            cell = 12          # 12 * 6 = 72
            strip_w = n * cell  # 72
            strip_h = 18
            strip_y = 53
            pad = (s - strip_w) // 2  # 12
            patch_w = cell - 2  # 10 — leaves a 2 px gap to the next patch
            for i in range(n):
                if i == 2:
                    p.setBrush(accent)
                else:
                    col = QColor(fg)
                    col.setAlpha(110)
                    p.setBrush(col)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(pad + i * cell + 1, strip_y, patch_w, strip_h)
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(pad, strip_y, strip_w, strip_h)

        elif self._key == "build_from_measurement":
            # Document glyph (folded corner) → arrow → cube. Tightened so the
            # whole composition fits within the 96 canvas with a clear bottom
            # margin (previous version overflowed past the right edge).
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            # Document
            dx, dy, dw, dh = 10, 13, 28, 52
            p.drawLine(dx, dy, dx + dw - 9, dy)
            p.drawLine(dx + dw - 9, dy, dx + dw, dy + 9)
            p.drawLine(dx + dw, dy + 9, dx + dw, dy + dh)
            p.drawLine(dx + dw, dy + dh, dx, dy + dh)
            p.drawLine(dx, dy + dh, dx, dy)
            p.drawLine(dx + dw - 9, dy, dx + dw - 9, dy + 9)
            p.drawLine(dx + dw - 9, dy + 9, dx + dw, dy + 9)
            # Arrow
            ax0 = dx + dw + 4
            ax1 = ax0 + 12
            ay = dy + dh / 2
            p.setPen(QPen(accent, stroke))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube
            p.setPen(QPen(fg, stroke))
            csz = 20
            cx0 = ax1 + 4
            cy0 = int(ay - csz / 2)
            iso = 6
            p.drawRect(cx0, cy0, csz, csz)
            p.drawLine(cx0 + iso, cy0 - iso, cx0 + csz + iso, cy0 - iso)
            p.drawLine(cx0 + csz, cy0, cx0 + csz + iso, cy0 - iso)
            p.drawLine(cx0 + csz + iso, cy0 - iso,
                       cx0 + csz + iso, cy0 + csz - iso)
            p.drawLine(cx0 + csz, cy0 + csz, cx0 + csz + iso, cy0 + csz - iso)

        elif self._key == "refine":
            # Magnifying glass — refinement = inspecting + re-measuring outliers.
            # (Previous circular-arrow attempt had arrowhead-angle issues — this
            # geometry is foolproof and matches the analyse-then-re-measure flow.)
            import math
            lens_r = 22
            cx = s / 2 - 8
            cy = s / 2 - 8
            p.setPen(QPen(fg, stroke + 0.4))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawEllipse(int(cx - lens_r), int(cy - lens_r), 2 * lens_r, 2 * lens_r)
            # Handle — 45° line off the lower-right of the lens
            ang = math.radians(-45)
            hx0 = cx + lens_r * math.cos(ang)
            hy0 = cy - lens_r * math.sin(ang)
            hx1 = hx0 + 18
            hy1 = hy0 + 18
            p.setPen(QPen(fg, stroke + 1.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(hx0), int(hy0), int(hx1), int(hy1))
            # Magenta accent dot inside the lens — the outlier being inspected
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(int(cx - 5), int(cy - 5), 10, 10)

        elif self._key == "two_pass":
            # Two cubes side by side, second one filled magenta — first profile
            # becomes the pre-conditioning base for a higher-quality second one.
            csz = 24
            gap = 10
            x0 = (s - (2 * csz + gap + 10)) / 2
            y0 = (s - csz) / 2 + 3
            # Cube 1 — outline
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(int(x0), int(y0), csz, csz)
            p.drawLine(int(x0 + 5), int(y0 - 5), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz), int(y0), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz + 5), int(y0 - 5),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x0 + csz), int(y0 + csz),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            # Arrow between
            ay = y0 + csz / 2
            ax0 = x0 + csz + 7
            ax1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube 2 — filled magenta
            x1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke))
            p.setBrush(accent)
            p.drawRect(int(x1), int(y0), csz, csz)
            # Iso depth lines for cube 2 — slightly faded
            faded = QColor(accent)
            faded.setAlpha(170)
            p.setPen(QPen(faded, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawLine(int(x1 + 5), int(y0 - 5), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz), int(y0), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz + 5), int(y0 - 5),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x1 + csz), int(y0 + csz),
                       int(x1 + csz + 5), int(y0 + csz - 5))

        elif self._key == "improve_existing_profile":
            # Existing profile (filled grey cube) → arrow → improved profile
            # (outlined cube with magenta "+"). Distinct from two_pass which
            # uses two cubes both being internally produced; here the first
            # cube is the *given* input, not built in-app.
            csz = 24
            gap = 10
            x0 = (s - (2 * csz + gap + 10)) / 2
            y0 = (s - csz) / 2 + 3
            # Cube 1 — filled in fg colour (the seed profile you bring in)
            seed = QColor(fg)
            seed.setAlpha(180)
            p.setPen(QPen(fg, stroke))
            p.setBrush(seed)
            p.drawRect(int(x0), int(y0), csz, csz)
            faded = QColor(fg)
            faded.setAlpha(160)
            p.setPen(QPen(faded, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawLine(int(x0 + 5), int(y0 - 5), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz), int(y0), int(x0 + csz + 5), int(y0 - 5))
            p.drawLine(int(x0 + csz + 5), int(y0 - 5),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x0 + csz), int(y0 + csz),
                       int(x0 + csz + 5), int(y0 + csz - 5))
            # Arrow
            ay = y0 + csz / 2
            ax0 = x0 + csz + 7
            ax1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(ax0), int(ay), int(ax1), int(ay))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay - 4))
            p.drawLine(int(ax1), int(ay), int(ax1 - 4), int(ay + 4))
            # Cube 2 — outlined in accent, with a magenta "+" inside
            x1 = x0 + csz + gap + 6
            p.setPen(QPen(accent, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRect(int(x1), int(y0), csz, csz)
            p.drawLine(int(x1 + 5), int(y0 - 5), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz), int(y0), int(x1 + csz + 5), int(y0 - 5))
            p.drawLine(int(x1 + csz + 5), int(y0 - 5),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            p.drawLine(int(x1 + csz), int(y0 + csz),
                       int(x1 + csz + 5), int(y0 + csz - 5))
            # "+" mark — improvement
            cx2 = x1 + csz / 2
            cy2 = y0 + csz / 2
            arm = 5
            p.setPen(QPen(accent, stroke + 0.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(cx2 - arm), int(cy2), int(cx2 + arm), int(cy2))
            p.drawLine(int(cx2), int(cy2 - arm), int(cx2), int(cy2 + arm))

        elif self._key == "check_visualise":
            # Isometric wireframe cube
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            cx, cy = s / 2, s / 2
            r = 30
            import math
            verts = [
                (cx, cy - r),               # top
                (cx + r * math.cos(math.radians(30)), cy - r * math.sin(math.radians(30))),  # right-top
                (cx + r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))),  # right-bot
                (cx, cy + r),               # bottom
                (cx - r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))),  # left-bot
                (cx - r * math.cos(math.radians(30)), cy - r * math.sin(math.radians(30))),  # left-top
            ]
            # Outer hex outline
            for i in range(6):
                a = verts[i]
                b = verts[(i + 1) % 6]
                p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
            # 3 inner spokes from centre to alternating verts
            for idx in (0, 2, 4):
                v = verts[idx]
                p.drawLine(int(cx), int(cy), int(v[0]), int(v[1]))
            # Accent vertex
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            v = verts[1]
            p.drawEllipse(int(v[0] - 5), int(v[1] - 5), 10, 10)

        elif self._key in ("scanner_profile", "printer_from_scan"):
            # Flatbed scanner: bed rectangle, an accent scan bar, content lines.
            # printer_from_scan: the content is a patch grid instead of lines —
            # the scanner is reading a chart, not a photo.
            margin = 16
            top = margin + 6
            h = s - 2 * margin - 12
            p.setPen(QPen(fg, stroke))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawRoundedRect(margin, top, s - 2 * margin, h, 6, 6)
            inner = margin + 10
            # Accent scan bar (the moving light) near the top of the bed
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(inner, top + 12, s - 2 * inner, 6, 2, 2)
            if self._key == "printer_from_scan":
                # 4x2 patch grid under the bar
                p.setPen(QPen(fg, 1.6))
                p.setBrush(QColor(0, 0, 0, 0))
                gw = (s - 2 * inner - 6) / 4
                for r_ in range(2):
                    for c_ in range(4):
                        p.drawRect(int(inner + c_ * (gw + 2)),
                                   int(top + 28 + r_ * 14), int(gw), 10)
            else:
                # Two content lines below it
                p.setPen(QPen(fg, stroke))
                for k in range(2):
                    y = top + 30 + k * 12
                    x1 = s - inner - (12 if k == 1 else 0)
                    p.drawLine(inner, y, x1, y)

        elif self._key == "glossary":
            # Dictionary: a big "Aa" with an accent underline.
            f = QFont()
            f.setPixelSize(int(s * 0.42))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(fg, stroke))
            p.drawText(0, 0, s, s - 14, Qt.AlignmentFlag.AlignCenter, "Aa")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(int(s * 0.30), s - 22, int(s * 0.40), 5, 2, 2)

        p.end()


# ---------------------------------------------------------------------------
# Card widget (clickable)
# ---------------------------------------------------------------------------

class WorkflowCard(QFrame):
    """Clickable workflow tile — icon, title, one-line subtitle."""

    clicked = pyqtSignal(str)  # emits workflow key

    def __init__(self, workflow: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = workflow["key"]
        self._mode = "dark"
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Height is set uniformly for all cards by _build_menu_page via
        # required_height() — translated titles/subtitles wrap to more lines
        # than the English originals, so a hard-coded box would squeeze them
        # into the icon.
        self.setMinimumWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)

        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        self._icon = WorkflowIcon(self._key, self)
        icon_row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(icon_row)

        self._title = QLabel(workflow["title"], self)
        self._title.setWordWrap(True)
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(f)
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._title)

        self._subtitle = QLabel(workflow["subtitle"], self)
        self._subtitle.setWordWrap(True)
        sf = QFont()
        sf.setPixelSize(11)
        self._subtitle.setFont(sf)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._subtitle)
        layout.addStretch(1)

        self._apply_style()

    def required_height(self, text_width: int) -> int:
        """Height this card needs at `text_width` for its wrapped labels —
        margins + icon + spacings + title + subtitle (+ stretch floor)."""
        m = self.layout().contentsMargins()
        spacing = self.layout().spacing()

        # Font metrics, not heightForWidth(): the latter returns -1 until the
        # widget is polished, which silently collapses the computed height.
        def _wrapped_h(label: QLabel) -> int:
            fm = QFontMetrics(label.font())
            return fm.boundingRect(
                0, 0, text_width, 4000,
                Qt.TextFlag.TextWordWrap, label.text(),
            ).height()

        # minimumHeight, not height()/sizeHint(): before the first layout
        # pass height() is the default widget size, and a plain QWidget's
        # sizeHint() is invalid — setFixedSize() only pins min/max.
        icon_h = self._icon.minimumHeight()
        # +12: QLabel renders wrapped text slightly taller than raw
        # boundingRect metrics (leading / style margins).
        return (m.top() + icon_h + spacing + _wrapped_h(self._title)
                + spacing + _wrapped_h(self._subtitle) + m.bottom() + 12)

    def set_appearance(self, mode: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self._icon.set_appearance(self._mode)
        self._apply_style()

    def _apply_style(self) -> None:
        if self._mode == "light":
            bg = "#ffffff"
            border = "#d0ccc6"
            text = "#22211f"
            sub = "#7a7570"
        else:
            bg = "#1a1a1a"
            border = "#333333"
            text = "#e6e6e6"
            sub = "#8a8a8a"
        hover_border = SPEC_MAGENTA if self._hover else border
        self.setStyleSheet(
            f"""
            WorkflowCard {{
                background: {bg};
                border: 1.5px solid {hover_border};
                border-radius: 10px;
            }}
            WorkflowCard QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        self._title.setStyleSheet(f"color: {text};")
        self._subtitle.setStyleSheet(f"color: {sub};")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Tab number badge
# ---------------------------------------------------------------------------

class StepBadge(QLabel):
    """Step-number chip — the number is the step count; colour = tab.

    Optional steps render outlined (transparent fill + coloured ring) so they
    read as suggestions rather than required steps in the sequence.
    """

    def __init__(
        self,
        step_number: int,
        tab_index_1based: int,
        parent: QWidget | None = None,
        *,
        optional: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText(str(step_number))
        color = TAB_COLORS[(tab_index_1based - 1) % len(TAB_COLORS)]
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.Bold)
        self.setFont(f)
        if optional:
            # Outlined: ring in the tab colour, text in the tab colour,
            # transparent fill — visually quieter than a filled chip.
            self.setStyleSheet(
                f"background: transparent; color: {color}; "
                f"border-radius: 15px; border: 2px solid {color};"
            )
        else:
            self.setStyleSheet(
                f"background: {color}; color: #0a0a0a; "
                f"border-radius: 15px; border: none;"
            )


# ---------------------------------------------------------------------------
# Welcome dialog
# ---------------------------------------------------------------------------

class WelcomeDialog(QDialog):
    """Welcome menu + per-workflow instructions."""

    def __init__(
        self,
        settings: "AppSettings",
        parent: QWidget | None = None,
        initial_mode: str = "dark",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._mode = "light" if initial_mode == "light" else "dark"
        self.setWindowTitle(tr("Welcome to ChromIQ"))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setMinimumSize(870, 660)
        self._cards: list[WorkflowCard] = []
        self._build_ui()
        self.set_appearance(self._mode)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_menu_page())
        self._stack.addWidget(self._build_detail_page())
        self._stack.currentChanged.connect(self._on_page_changed)
        outer.addWidget(self._stack, stretch=1)

        # Work-in-progress disclaimer. Persistent across both pages; small,
        # italic, dimmed text — visible but not noisy.
        self._wip_note = QLabel(
            tr("These guides are still being polished — some details may not "
            "be fully accurate yet. When in doubt, trust what you see in "
            "the app over what you read here."),
            self,
        )
        self._wip_note.setObjectName("welcome_wip_note")
        self._wip_note.setWordWrap(True)
        self._wip_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wip_font = QFont()
        wip_font.setPixelSize(11)
        wip_font.setItalic(True)
        self._wip_note.setFont(wip_font)
        outer.addWidget(self._wip_note)

        # Footer — shared across both pages. Back button only shows on detail.
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self._show_cb = QCheckBox(tr("Show this on startup"), self)
        self._show_cb.setChecked(bool(self._settings.get("show_welcome_dialog", True)))
        self._show_cb.toggled.connect(
            lambda v: self._settings.set("show_welcome_dialog", bool(v))
        )
        footer.addWidget(self._show_cb)
        # Quiet support link — the classic tucked-away spot: only people who
        # open the help find it, so it never feels pushy (Basti). Opens the
        # Ko-fi page in the browser.
        self._support_btn = QPushButton(tr("♥ Support ChromIQ"), self)
        self._support_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._support_btn.setFlat(True)
        self._support_btn.setToolTip(tr(
            "ChromIQ is free and always will be. If it saves you time or "
            "ink, a coffee on Ko-fi is a kind way to say thanks — completely "
            "optional, and the app stays fully featured either way."))
        self._support_btn.clicked.connect(self._open_support_page)
        footer.addSpacing(16)
        footer.addWidget(self._support_btn)
        footer.addStretch(1)
        self._back_btn = QPushButton(tr("← Back"), self)
        self._back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._back_btn.setVisible(False)
        footer.addWidget(self._back_btn)
        self._close_btn = QPushButton(tr("Close"), self)
        self._close_btn.clicked.connect(self.accept)
        footer.addWidget(self._close_btn)
        outer.addLayout(footer)

    def _on_page_changed(self, index: int) -> None:
        self._back_btn.setVisible(index == 1)

    def _open_support_page(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://ko-fi.com/itsab1989"))

    # ------------------------------------------------------------------
    def _build_menu_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # Heading
        heading = self._make_heading()
        v.addWidget(heading)

        self._subtitle = QLabel(tr("What would you like to do?"), page)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sf = QFont()
        sf.setPixelSize(15)
        self._subtitle.setFont(sf)
        v.addWidget(self._subtitle)

        # Card grid — 3 columns. Layout adapts to the workflow count:
        #   • 6 cards: 3+3
        #   • 7 cards: 3+3+1 (last centred)
        #   • 8 cards: 3+3+2 (last row at cols 0 and 2, col 1 empty for
        #     symmetry — mirrors the centred-bottom feel of the 7-card case)
        # Wrapped in a FadeScrollArea so the dialog can be shorter than the
        # full grid height; users scroll to reach lower workflows and the
        # edges fade to dialog bg instead of being cut by a hard line.
        grid_host = QWidget(page)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 8, 12, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        # 3-column grid. Full rows fill left-to-right; a partial final row is
        # centred (1 card → middle column; 2 cards → cols 0 and 2). Works for any
        # card count — a full last row (e.g. 9 cards) is just three clean rows.
        n_cards = len(WORKFLOWS)
        rem = n_cards % 3
        full_count = n_cards - rem
        last_row = full_count // 3
        for i, wf in enumerate(WORKFLOWS):
            card = WorkflowCard(wf, grid_host)
            card.clicked.connect(self._on_card_clicked)
            self._cards.append(card)
            if i < full_count:
                grid.addWidget(card, i // 3, i % 3)
            elif rem == 1:
                grid.addWidget(card, last_row, 1)
            else:  # rem == 2 → cols 0 and 2, leaving the middle empty
                grid.addWidget(card, last_row, 0 if i == full_count else 2)

        # Uniform tile height that fits the tallest translated card at the
        # narrowest card width the minimum dialog size allows (~205px of
        # label width at the 870px minimum dialog size, minus slack).
        text_w = 200
        tile_h = max(190, max(c.required_height(text_w) for c in self._cards))
        for c in self._cards:
            c.setFixedHeight(tile_h)

        self._menu_scroll = FadeScrollArea(page, surface="dialog")
        self._menu_scroll.setWidget(grid_host)
        self._menu_scroll.setWidgetResizable(True)
        self._menu_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._menu_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        v.addWidget(self._menu_scroll, stretch=1)
        return page

    # ------------------------------------------------------------------
    def _build_detail_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(12)

        self._detail_title = QLabel("", page)
        tf = QFont()
        tf.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
        tf.setPixelSize(32)
        tf.setWeight(QFont.Weight.Normal)
        self._detail_title.setFont(tf)
        self._detail_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self._detail_title)

        self._detail_subtitle = QLabel("", page)
        self._detail_subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._detail_subtitle.setWordWrap(True)
        ssf = QFont()
        ssf.setPixelSize(13)
        self._detail_subtitle.setFont(ssf)
        v.addWidget(self._detail_subtitle)

        # Steps in a scroll area
        self._steps_host = QWidget(page)
        self._steps_layout = QVBoxLayout(self._steps_host)
        self._steps_layout.setContentsMargins(20, 16, 20, 16)
        self._steps_layout.setSpacing(14)

        self._detail_scroll = FadeScrollArea(page, surface="dialog")
        self._detail_scroll.setWidget(self._steps_host)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(self._detail_scroll, stretch=1)

        return page

    # ------------------------------------------------------------------
    def _make_heading(self) -> QWidget:
        """Custom-painted 'Welcome to ChromIQ' wordmark with magenta IQ."""

        class _Heading(QWidget):
            def __init__(self, dialog: "WelcomeDialog") -> None:
                super().__init__(dialog)
                self._dialog = dialog
                self.setFixedHeight(72)

            def paintEvent(self, _ev):
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

                font_r = QFont()
                font_r.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
                font_r.setPixelSize(44)
                font_r.setWeight(QFont.Weight.Normal)

                font_i = QFont()
                font_i.setFamilies(["Instrument Serif", "Georgia", "Times New Roman", "serif"])
                font_i.setPixelSize(44)
                font_i.setWeight(QFont.Weight.Bold)
                font_i.setItalic(True)

                fm_r = QFontMetricsF(font_r)
                fm_i = QFontMetricsF(font_i)
                # The trailing "Chrom" stays attached to the painted
                # magenta "IQ" wordmark; only the greeting translates.
                text_pre = tr("Welcome to") + " Chrom"
                text_iq  = "IQ"
                wpre = fm_r.horizontalAdvance(text_pre)
                wiq  = fm_i.horizontalAdvance(text_iq)
                total = wpre + wiq - 1
                x_start = (self.width() - total) / 2
                baseline = (self.height() + fm_r.ascent() - fm_r.descent()) / 2

                fg = "#22211f" if self._dialog._mode == "light" else "#ffffff"
                p.setFont(font_r)
                p.setPen(QColor(fg))
                p.drawText(int(x_start), int(baseline), text_pre)
                p.setFont(font_i)
                p.setPen(QColor(SPEC_MAGENTA))
                p.drawText(int(x_start + wpre - 1), int(baseline), text_iq)
                p.end()

        self._heading = _Heading(self)
        return self._heading

    # ------------------------------------------------------------------
    def _on_card_clicked(self, key: str) -> None:
        wf = next((w for w in WORKFLOWS if w["key"] == key), None)
        if wf is None:
            return
        self._detail_title.setText(wf["title"])
        self._detail_subtitle.setText(wf["subtitle"])
        # Clear previous step rows
        while self._steps_layout.count():
            item = self._steps_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if wf.get("kind") == "glossary":
            # Alphabetical term/definition rows — no step badges (Knut, #108).
            for term, definition in sorted(GLOSSARY,
                                           key=lambda e: e[0].lower()):
                self._steps_layout.addWidget(self._make_glossary_row(
                    term, definition))
        else:
            # Build new rows. Steps are (tab_idx, text) or
            # (tab_idx, text, optional).
            for i, step in enumerate(wf["steps"], start=1):
                tab_idx, text = step[0], step[1]
                optional = bool(step[2]) if len(step) > 2 else False
                row = self._make_step_row(i, tab_idx, text, optional=optional)
                self._steps_layout.addWidget(row)
        self._steps_layout.addStretch(1)
        self._apply_detail_text_colors()
        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    def _make_glossary_row(self, term: str, definition: str) -> QWidget:
        """One dictionary entry: bold term, plain-language definition under it
        (Knut's "Dictionary and terminology" card, #108)."""
        row = QWidget(self._steps_host)
        v = QVBoxLayout(row)
        v.setContentsMargins(0, 0, 0, 10)
        v.setSpacing(2)
        t = QLabel(term, row)
        tf = QFont()
        tf.setPixelSize(13)
        tf.setBold(True)
        t.setFont(tf)
        t.setWordWrap(True)
        t.setObjectName("welcome_step_body")
        v.addWidget(t)
        d = QLabel(definition, row)
        df = QFont()
        df.setPixelSize(13)
        d.setFont(df)
        d.setWordWrap(True)
        d.setObjectName("welcome_step_body")
        v.addWidget(d)
        return row

    # ------------------------------------------------------------------
    def _make_step_row(
        self,
        number: int,
        tab_index: int,
        text: str,
        *,
        optional: bool = False,
    ) -> QWidget:
        row = QWidget(self._steps_host)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        badge = StepBadge(number, tab_index, row, optional=optional)
        h.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        body = QLabel(text, row)
        body.setWordWrap(True)
        bf = QFont()
        bf.setPixelSize(13)
        if optional:
            bf.setItalic(True)
        body.setFont(bf)
        body.setObjectName("welcome_step_body")
        # Tag the label so theme re-tinting can dim optional steps.
        body.setProperty("welcome_optional", optional)
        h.addWidget(body, stretch=1)
        return row

    # ------------------------------------------------------------------
    def set_appearance(self, mode: str) -> None:
        """Re-tint dialog chrome + propagate to children."""
        self._mode = "light" if mode == "light" else "dark"
        if self._mode == "light":
            dialog_bg = "#eeece8"     # match LM_BG_WINDOW
            sub_fg    = "#7a7570"
        else:
            dialog_bg = "#181818"     # match BG_PANEL — dark grey, not pure black
            sub_fg    = "#a8a4a0"
        # Dialog body. Override the global ACCENT (cyan/blue) for the checkbox
        # indicator so it picks up the spectrum-magenta accent of the welcome
        # dialog rather than the app-wide cyan/blue.
        self.setStyleSheet(
            f"""
            QDialog {{ background: {dialog_bg}; }}
            QDialog QLabel {{ background: transparent; }}
            QDialog QCheckBox::indicator:checked {{
                background: {SPEC_MAGENTA};
                border-color: {SPEC_MAGENTA};
            }}
            QDialog QCheckBox::indicator:hover {{
                border-color: {SPEC_MAGENTA};
            }}
            """
        )
        if hasattr(self, "_support_btn"):
            _heart = "#c62b52" if self._mode == "light" else "#ff7aa2"
            self._support_btn.setStyleSheet(
                "QPushButton {"
                f"  color: {_heart}; background: transparent; border: none;"
                "   padding: 2px 6px; font-size: 12px; }"
                "QPushButton:hover { text-decoration: underline; }")
        if hasattr(self, "_subtitle"):
            self._subtitle.setStyleSheet(f"color: {sub_fg};")
        if hasattr(self, "_detail_subtitle"):
            self._detail_subtitle.setStyleSheet(f"color: {sub_fg};")
        if hasattr(self, "_wip_note"):
            self._wip_note.setStyleSheet(f"color: {sub_fg};")
        for card in self._cards:
            card.set_appearance(self._mode)
        if hasattr(self, "_heading"):
            self._heading.update()
        if hasattr(self, "_menu_scroll"):
            self._menu_scroll.set_appearance(self._mode)
        if hasattr(self, "_detail_scroll"):
            self._detail_scroll.set_appearance(self._mode)
        self._apply_detail_text_colors()

    # ------------------------------------------------------------------
    def _apply_detail_text_colors(self) -> None:
        if not hasattr(self, "_steps_host"):
            return
        body_fg     = "#22211f" if self._mode == "light" else "#e6e6e6"
        optional_fg = "#7a7570" if self._mode == "light" else "#9a9a9a"
        title_fg = body_fg
        for lbl in self._steps_host.findChildren(QLabel, "welcome_step_body"):
            fg = optional_fg if bool(lbl.property("welcome_optional")) else body_fg
            lbl.setStyleSheet(f"color: {fg};")
        if hasattr(self, "_detail_title"):
            self._detail_title.setStyleSheet(f"color: {title_fg};")
