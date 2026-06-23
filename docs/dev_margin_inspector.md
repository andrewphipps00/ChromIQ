# Margin inspector & thresholds

Measures the realised page margins of a generated chart preview and warns when
they fall below the minimum a measuring ruler / jig needs. Requested by Knut to
address profiles that read badly because the chart sat too close to a sheet edge
for the i1Pro jig.

## Why measure, not set

printtarg exposes **one uniform `-m`/`-M` margin** (`ma[0..3] = marg`), plus a
fixed **26 mm i1Pro left clip border** (suppressed by `-L`). The realised
per-side margins are therefore an *output* of the whole layout — patch scale
(`-a`), spacer size, `-m`/`-M`, `-L`, strip-length limit (`-P`), paper and
orientation all move them. So the inspector **measures the rendered page**; the
user can't dial a left/right margin directly.

`-M` (what ChromIQ ships) keeps the margin inside the TIFF, so the TIFF spans the
full sheet and margins read straight against the paper edge. Plain `-m` crops the
margin out; `measure_margins` adds the trimmed `(paper − tiff)/2` back when the
true paper size is supplied.

## The "patch area"

The edge measured to is **where bare paper meets the first patch or spacer** —
spacers (between/at strip ends) count as patch area; strip labels (A B C…), the
rotated right-margin title and page labels are text and are **excluded**.

`workflow/margin_inspector.py` reuses `ti2_relayout._patch_grid_bbox` for the
horizontal extent (it already drops the title) and derives the vertical extent
from a row-fill scan inside that x-band (the grid's own y-range is title-
contaminated — the editor only ever uses its x-range). No B&W twin is needed.

## Orientation (the subtle bit)

Margins are reported in **printtarg / TIFF orientation** (what the preview
shows). The jig is rotated 90°: a sheet placed landscape in the jig is laid out
portrait by printtarg. The scanner runs **along the strips**, so the white
run-up is needed on the scan-direction edges:

| printtarg orientation | scan-direction (run-up) edges | cross-scan edges |
|-----------------------|-------------------------------|------------------|
| Portrait              | Top, Bottom                   | Left, Right      |
| Landscape             | Left, Right                   | Top, Bottom      |

Seed thresholds put the run-up only on the scan-direction edges; cross-scan
sides stay unset (0 = unchecked) so a shipped chart's small cross-scan margin
(e.g. the i1Pro A4 portrait preset's 8 mm left/right) can't false-alarm.

## Data model

- `core/settings.py`: `margin_thresholds` is one JSON blob,
  `{"<instrument>|<paper> <Orientation>": {"L","R","T","B","desc"}}`.
  `default_margin_thresholds()` is the seed table; `margin_combo_key()` builds
  the key; `parse/serialize_margin_thresholds()` round-trip the blob.
- Thresholds are **minimums to meet-or-exceed**; no upper bound. A missing combo
  → no check (inspector still shows the measured numbers).
- Two behaviour flags: `margin_inspector_show` (frame visibility),
  `margin_violation_notify` (warning; greyed out while the frame is hidden),
  plus `margin_guides_show` (the preview guide-line toggle, stored from the
  in-tab checkbox).

## UI

- **Settings → Margin Thresholds tab** (`settings_dialog.py`): instrument +
  paper(+orientation) pulldowns select a combo; a Description field and an
  L/R/T/B mm table edit that combo. Edits commit to an in-memory table, saved as
  the blob on OK.
- **Create Chart** (`ui/margin_inspector_panel.py`): the "Measured from Preview"
  frame under the preview shows L/R/T/B + reading-direction patch size (mm &
  inch), a large green `Margins: OK` / red violation status, and the dotted
  guide-line checkbox. `tab_chart._update_margin_inspector` measures every page,
  derives the combo key and surfaces the worst (most-violated) page.
- **Preview guides** (`tiff_preview.set_margin_guides`): dotted lines at each
  threshold position — white-halo black dash normally, red on the violated edge.
  Drawn on the display only, never into the TIFF.

## Seeds

`scripts/derive_margin_seeds.py` renders the shipped ColorMunki presets and
measures them; the seed values are rounded just below the smallest known-good
margin so those (practically-tested) presets read OK out of the box. i1Pro
seeds use Knut's 11 mm scan run-up. These are *editable starting points*, not
physical minima — rulers vary.
