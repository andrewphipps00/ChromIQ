# i1Profiler multi-page layout — results (issue #120)

Follow-up to the single-page probe. A **1500-patch** probe set was loaded from
`probe.pxf` (i1Pro 3, defaults) to force a page break, and the chart saved as
TIFF. All numbers below are recovered from the patch colours alone — each patch
is painted a colour that encodes its own index.

## What i1Profiler did

- **1500 patches → 2 pages.** The page indicator read "Seite 1 von 2".
- **Saved as SEPARATE files**, one per page, named
  `<title>_<page>_<total>.tif`:
  `ChromIQ i1Profiler layo_1_2.tif`, `ChromIQ i1Profiler layo_2_2.tif`
  (the title is truncated). **Not** a multi-frame TIFF.
- Each page is 271 × 210 mm (full A4-landscape height), grid **30 × 25**,
  patch **8.000 × 7.000 mm**, zero gap, origin 15.50 / 24.50 mm — same patch
  metrics as the single-page chart, just 25 rows instead of 20.

## The fill rule (the important part)

i1Profiler does **not** fill page 1 completely and then spill onto page 2.
Instead it builds **one logical grid of 30 columns × 50 rows**
(`cols = floor(usable_width / patch_w) = 240/8 = 30`,
`rows_total = ceil(1500 / 30) = 50`), fills it **column-major**
(`index = col × 50 + row`), and then **splits that grid into pages of 25 rows**:

- **page 1 = the top 25 rows of every column** → indices 0–24, 50–74, 100–124, …
- **page 2 = the bottom 25 rows of every column** → indices 25–49, 75–99, …

Verified against all 1500 patches: `index = col × 50 + (page-1)×25 + row` holds
exactly, zero misses, zero overlap between pages, none missing.

The pages are also **balanced** (25 + 25), not filled-then-remainder: 50 rows
fit in 2 pages of 25, and 25 is the most 7 mm rows that fit A4 height.

## Consequence for ChromIQ

**A page cannot be read in isolation.** On page 1 the patch order jumps by the
full page height at every column (0…24, then 50…74), so `SAMPLE_LOC`/geometry
recovery must reassemble **all pages together** as one column-major grid:

```
global_row = (page - 1) × rows_per_page + row_on_page
index      = col × rows_total + global_row
```

where `rows_total = ceil(n_patches / cols)` and `rows_per_page` is read from any
one page's grid. `layout_from_render.py` must join pages this way, not
concatenate per-page reads.
