# i1Profiler layout probe — results (issue #120)

These are the real charts i1Profiler produced from the ChromIQ layout probe,
run by Basti on a machine with i1Profiler installed (no measurement device, no
profiling licence — neither is needed for this). They answer, from pixels alone,
how i1Profiler lays out a chart, which is what issue #120 turns on.

The probe paints every patch a colour that encodes its own index
(`R,G,B = (v%16, v//16%16, v//256%16) * 17`, `v = index+1`), so each patch is a
distinct 8-bit-exact colour. Recover the geometry with:

```bash
python scripts/decode_i1profiler_probe.py docs/i1profiler_probe/results/test1-autolayout.tif
```

## Files

| File | Loaded into i1Profiler | Purpose |
|------|------------------------|---------|
| `test1-autolayout.tif`   | `probe.pxf` (600 patches) | how does i1Profiler lay out a plain patch set? |
| `test2a-nolocations.tif` | `probe-A-autolayout.pwxf` | workflow **without** per-patch Location tags |
| `test2b-reversed.tif`    | `probe-B-reversed.pwxf`   | same patches, Location tags **reversed** |
| `i1profiler-testchart.png` | — | screenshot of i1Profiler's Testchart panel for test 1 |
| `settings.txt` | — | what i1Profiler reported on screen |

All three TIFFs decode to **600/600 probe colours**, byte-exact — i1Profiler
applies no colour transform when it saves the chart as a TIFF.

## Findings

**1. i1Profiler ignores per-patch Location tags.** `test2a` and `test2b` carry
the *reverse* of each other's grid positions, yet **600/600 patches land in the
identical cell**. So i1Profiler always recomputes the grid itself;
`emit_locations=True` in a `.pwxf` cannot steer it.

**2. But it saves the chart as a TIFF**, at real resolution with exact colours —
so ChromIQ can derive the geometry from the render (`layout_from_render.py`).
That is what unblocks the scanner/camera-target path in #120.

**3. Exact geometry recovered** (101.6 dpi = 4 px/mm):

| | test1 (`.pxf`) | test2a/b (`.pwxf`) |
|---|---|---|
| page (chart image) | 271.00 × 175.00 mm | 271.00 × 125.00 mm |
| grid | **30 cols × 20 rows** | 40 cols × 15 rows |
| patch size | **8.000 × 7.000 mm** | 6.000 × 6.000 mm |
| gap between patches | **0.000 mm** | 0.000 mm |
| patch-area origin | 15.50, 24.50 mm | 15.50, 24.50 mm |
| fill order | **column-major** (down each column) | column-major |

The usable patch-area width is 240 mm in both, so `cols = floor(240 / patch_w)`
(30 at 8 mm, 40 at 6 mm) and the rows follow. Beyond the patches, i1Profiler
adds black registration/edge marks and one mid-grey element (`(210,210,210)`).

**4. A ChromIQ bug this exposed (now fixed).** The `.pwxf` charts came out
**6 × 6 mm** — exactly the i1Pro 3 slider minimum — where the `.pxf` gave
i1Profiler's real 8 × 7 mm default. Cause: our workflow export wrote
`PatchSizeWidthPercent="0"` together with `UsePatchSettingDefaults="True"`.
i1Profiler sizes patches from the slider **percent**, and percent 0 is the
minimum, not "auto". No genuine X-Rite workflow file writes that combination.
Fixed in commit `7c04511`.
