# `.pwxf` (i1Profiler workflow) format — reverse-engineered mapping

This is the working reference for adding `.pwxf` export to ChromIQ (TI2 editor
"Save As → i1Profiler workflow"). It was derived from **14 genuine i1Profiler
1.1.0 exports** supplied by Nelson (the "ArgyllCMS … ti1-pxf-pwxf" set), all
verified as authentic, unmodified `X-Rite - Prism` CxF3 output. See
`workflow/i1profiler_export.py` for the existing `.pxf` writer this builds on.

## TL;DR

A `.pwxf` **is the same CxF3 file as our `.pxf`**, with two additions:

1. A **fuller `<xrp:CustomAttributes>`** element — adds page geometry, the
   patch grid (`NumberPatchColumns/Rows/Pages`), patch sizes, device, and
   measurement mode. Our `.pxf` only emits a ~12-attribute subset.
2. **Optional per-patch `<cc:TagCollection Name="Location">`** giving each
   patch a `Column`/`Row`/`Page` on the i1Profiler grid.

Everything else (root element, `FileInformation`, the `Object` list, the
`ColorSpecificationCollection`, and the `<ProfileSettings>` block) is byte-for-byte
the structure we already generate.

## What is verified vs. constant

### Patch data — fully verified, lossless
- Objects are emitted in **exact `.ti1` order**. All 9 "576-patch" `.pwxf`
  files and the `.pxf` reproduce the supplied `.ti1`'s 576 patches in order,
  identical RGB. i1Profiler preserves order on import/export.
- RGB conversion: `round(argyll_0..100 / 100 * 255)` → `<cc:R/G/B>` 0..255.
  (Same `_to_255_int` we already use.)

### Header / spec blocks — constant boilerplate (identical across all 14 files)
- `<cc:FileInformation>`: `Creator` = `X-Rite - Prism` (ours says `ChromIQ` —
  keep ChromIQ unless an import test shows i1Profiler rejects it),
  `Description` = `Prism CXF3 file`, plus two tags
  `PrismAppName="i1Profiler"` / `PrismAppVersion="1.1.0"`.
- `<cc:ColorSpecificationCollection>` → `Colorimetric_Reflectance` /
  `UnknownGeometry=Target`. Identical in every file; we already emit this in
  `_color_specification()`.
- `<ProfileSettings>` block — constant; identical to what `_profile_settings()`
  already produces.

## The layout knobs — `<xrp:CustomAttributes>`

Of ~71 attributes, **55 are constant** across all files and 16 vary. Only the
varying ones carry layout/device intent:

| Attribute | Meaning | Observed values | Source in ChromIQ |
|-----------|---------|-----------------|-------------------|
| `MeasurementDevice` | spectro, free string | `i1Pro 2`, `i1Pro 3`, `i1iO 2` | user picks (dropdown) |
| `MeasurementMode` | scan mode | `1` = single scan, `2` = dual scan | user picks |
| `PaperFormat` | page preset | `0` = Custom, `2` = A4 | from chosen paper |
| `PaperOrientation` | `Portrait` / `Landscape` | both seen | from layout |
| `PageWidth` / `PageHeight` | **imageable area, mm** (not always the physical sheet — see gotcha) | e.g. `296.93`×`210.06` (A4), `175`×`130` (custom) | layout / paper |
| `NumberPatchColumns` | grid columns | 12–39 | computed (see grid) |
| `NumberPatchRows` | grid rows | 11–24 | computed (see grid) |
| `NumberPatchPages` | sheets | 1–4 | computed |
| `PatchSizeWidthValue` / `PatchSizeHeightValue` | patch size, **mm** | 6.0–10.0 | layout |
| `PatchSizeWidthPercent` / `PatchSizeHeightPercent` | same as % of cell; **derived, sometimes `0`** | — | derive or 0 |
| `UseLegacyTestChart` | older chart engine | `True`/`False` | set `False` |
| `UsePatchSettingDefaults` | let i1Profiler auto-size patches | `True`/`False` | `True` is safest |
| `ProfileFilename` | output profile path | empty in all | leave empty |

Constant attributes worth knowing: `ColorSpace="RGB"`, `TestChartType="RGB Variable"`,
`ScramblePatches="False"`, `WriteProtected` / `LockWriteProtection` (we set
`WriteProtected="True"` in `.pxf` to stop i1Profiler re-generating patches — keep
that), `InkLimit="300"`, `WorkflowStep="TestChart"`,
`WorkflowType="PrinterProfilePro"`, `numberCorePatches=<N>`,
`TitleString` (free text, shown in i1Profiler).

> Note: our current `.pxf` writes `MeasurementScanningMode="Strip"`, which does
> **not** appear in any real file — i1Profiler uses the integer `MeasurementMode`.
> The `Strip` attribute is almost certainly ignored on import.

## The grid (`Location` tags)

When present, each Object gets:

```xml
<cc:TagCollection Name="Location">
  <cc:Tag Name="Column"     Value="0"/>
  <cc:Tag Name="Page"       Value="1"/>
  <cc:Tag Name="Row"        Value="0"/>
  <cc:Tag Name="SampleID"   Value="-1"/>
  <cc:Tag Name="SampleName" Value=""/>
</cc:TagCollection>
```

- **Fill order is column-major** over the Objects (= `.ti1` order):
  `Column = i // NumberPatchRows`, `Row = i % NumberPatchRows`, `Page` advances
  when a sheet fills. Verified on the 29×20 (i1Pro3) and 39×15 (i1iO single)
  charts.
- `SampleID = -1` and `SampleName = ""` for **every** patch — the layout is
  purely positional, not name-keyed. We don't need to invent IDs.

**Presence is inconsistent** across the sample set (see table below) — it tracks
how far the chart was taken in i1Profiler before saving, not a clean version
flag. The three newest files (laid-out charts) and the older 572-set all carry
Location tags; the May-1 576 files don't.

| File group | Location tags? | `UseLegacyTestChart` | `UsePatchSettingDefaults` |
|------------|----------------|----------------------|----------------------------|
| 572-patch (Apr 27) | yes (572) | mixed | False |
| 576-patch i1Pro1/2 (May 1) | **no** | mixed | False |
| 576-patch i1Pro3 / i1iO2 (May 28) | yes (576) | False | mostly True |

**Open question (needs an i1Profiler round-trip test — only Nelson can run it):**
does i1Profiler **recompute** the grid from page+patch size on load and ignore
our `Column/Row`, or does it honour what we write? This decides whether we:
- (A) emit no Location tags + let i1Profiler auto-lay-out (simplest, matches the
  May-1 files which still printed fine), or
- (B) compute and emit the column-major grid ourselves.

Recommend shipping **(A)** first (omit Location tags, set
`UsePatchSettingDefaults="True"`), and only add (B) if a test shows i1Profiler
needs an explicit grid.

## Patch size & header length (the slider-percent encoding)

When `UsePatchSettingDefaults="False"`, i1Profiler **ignores** `PatchSize*Value`
and reads the size from the slider *percent*:

```
PatchSize*Percent = (mm - lo) / (hi - lo) * 100
```

`(lo, hi)` is the device's slider range — verified per device from workflow files
saved at each slider extreme (`_PWXF_DEVICES` in `ui/dialogs/tools_dialogs.py`):

| Device | W mm | H mm | Mode | Header length? |
|--------|------|------|------|----------------|
| i1Pro 2 | 7–25 | 8–12 | 1/2 | — |
| i1Pro 3 | 6–25 | 6–12 | 1/2 | — |
| i1Pro 3 PLUS | 16–40 | 16–20 | 1 | — |
| i1Pro 3 PLUS M3 | 16–40 | 16–20 | 6 | — |
| i1iO 2 / 3 | 6–20 | 7–20 | 1/2 | — |
| i1iO 3 PLUS | 16–40 | 16–40 | 1 | — |
| i1iO 3 PLUS M3 | 16–40 | 16–40 | 6 | — |
| i1iSis /2/XL/2 XL | 6–20 | 6–20 | 1 | **32–80 mm** |

- Writing `*Percent="0"` makes i1Profiler use the slider **minimum** (the old
  ChromIQ bug → 6 mm + "unscannable" warning). Always emit the real percent.
- **Scan minimum ≠ slider minimum.** i1Profiler warns below a device-specific
  scan minimum (i1Pro 3 = 7, i1iO 3 = 7.5, PLUS/M3 = 20 mm). Defaults sit at/above
  it (`_device_default_size`: 8×7, or 20×20 for PLUS/M3).
- **Measurement mode** is fixed for some devices (PLUS → 1, M3 → 6, i1iSis → 1);
  only i1Pro/i1iO offer the Single (1) / Dual (2) choice.
- **`HeaderEdgeSizePercent`** = the i1iSis "Vorlauf" / **header length** lead-in.
  Same percent encoding, reverse-engineered range **32–80 mm**
  (`(mm-32)/48*100`; verified 32→0, 56→50, 80→100). Non-i1iSis devices write the
  sentinel `-2147483648`. i1Profiler still computes the column/row grid itself
  either way.
  - **NOT user-controllable.** i1Profiler does not *persist* the lead-in: it
    resets to the 32 mm minimum on load regardless of the file's value —
    confirmed by re-saving i1Profiler's own 56 mm workflow and reopening it (also
    32 mm). So ChromIQ writes `0` (= 32 mm) for i1iSis and offers no UI for it.
    The range/formula above are kept only as documented knowledge.

## Gotchas

- **`PageWidth/Height` ≠ physical sheet.** The "A4" May-1 files are
  `296.9 × 240` mm (not 210×297) with `PaperFormat="0"` (Custom) — the user had
  set a custom imageable area. The true-A4 files use `296.93 × 210.06` with
  `PaperFormat="2"`. So width here is the long edge / imaging region; pin down
  exact semantics with an i1Profiler test before trusting it as the sheet size.
- **`*Percent` patch sizes are unreliable** (`0` in the i1iO single-scan file).
  Use the `*Value` (mm) fields; treat percent as derived/optional.
- **Filename ≠ internal device.** Nelson's `i1Pro1` and `i1Pro2` files all carry
  `MeasurementDevice="i1Pro 2"` (i1Profiler 1.1.0 only exposes "i1Pro 2"). Don't
  read the i1Pro generation from a filename; the device string is the truth.

## Implementation sketch

`write_pxf()` in `workflow/i1profiler_export.py` already produces the skeleton.
Add a `write_pwxf()` (or a `workflow=True` path) that:

1. Reuses `_pxf_open` / `_rgb_object` / `_color_specification` /
   `_profile_settings` unchanged.
2. Emits the **full** `<xrp:CustomAttributes>` with the 16 layout/device
   attributes above, fed from the TI2 editor's in-memory layout
   (paper, patch size, columns/rows/pages) + a device/mode dropdown.
3. Phase 1: omit `Location` tags, `UsePatchSettingDefaults="True"`,
   `UseLegacyTestChart="False"`. Phase 2 (if testing requires): emit the
   column-major grid.

Validate output with `xml.etree.ElementTree.parse()` (all 14 references are
well-formed) and diff RGB-in-order against the source `.ti1`.
