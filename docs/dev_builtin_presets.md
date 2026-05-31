# Adding a built-in (non-deletable) Create Chart preset

ChromIQ ships a handful of **built-in presets** in the Create Chart → Manual
**Presets** dropdown that the user can't delete. This guide explains how the
mechanism works end-to-end and how to add, rename, or re-file one.

> **History.** Earlier builds had three kinds of built-in (a *ti1-based* preset
> that ran `printtarg` on a bundled `.ti1`, *targen-based* parameter presets,
> and *prebuilt-files*). Those were all replaced by the four prebuilt-files
> charts below; the ti1-based / targen-based machinery was removed (see the
> "remove dead TC9.18/ColorMunki preset code" commit). If you need to revive a
> run-`printtarg`-at-selection preset, recover the old `_apply_tc918_preset` /
> `_generate_from_ti1` wiring from git history — `_generate_from_ti1` itself is
> still present (it backs the *user* preset "attach a .ti1" feature).

All current built-ins are one kind: **prebuilt-files**. A complete,
pre-generated target (`.ti1` + `.ti2` + page `.tif`s) is bundled in `assets/`.
Selecting one prompts for a name, copies the bundled files into a fresh
`~/ChromIQ/<name>/runs/<current>/` folder under the canonical `chart` stem, and
loads the TIFFs — **no `targen` or `printtarg` runs**, so the parameter panels
are greyed out while the preset is active.

The four shipped presets (all RGB, A4):

| Label (in the dropdown)                          | Instrument | Asset leaf |
|--------------------------------------------------|------------|------------|
| ★ i1Pro TC9.24 by Pharmacist                     | i1Pro      | `i1pro/a4/tc924` |
| ★ i1Pro 1110 ABW-optimized by Pharmacist         | i1Pro      | `i1pro/a4/abw1110` |
| ★ ColorMunki TC3.00 by Pharmacist                | ColorMunki | `colormunki/a4/tc300` |
| ★ ColorMunki 702 ABW-optimized by Pharmacist     | ColorMunki | `colormunki/a4/abw702` |

---

## File / function map (`ui/tabs/tab_chart.py`)

**Constants (module level)**

- `TC924_PRESET_KEY` / `TC924_PRESET_LABEL`, `ABW1110_*`, `TC300_*`, `ABW702_*`
  — one sentinel `userData` key + one display label per preset. The combo entry
  is matched by its **key**, never its text.
- `PREBUILT_PRESETS: dict[key -> (asset_stem, default_name)]` — the registry.
  `asset_stem` is the path *without* extension under `assets/`; it locates
  `<stem>.ti1`, `<stem>.ti2` and the `<stem>_NN.tif` pages in that leaf folder.
- `DISABLED_BUILTIN_PRESET_KEYS` — keys shown greyed-out and non-selectable
  (park a preset here pending a fix instead of deleting it). Currently empty.
- `BUILTIN_PRESET_KEYS = frozenset(PREBUILT_PRESETS)` and
  `BUILTIN_PRESET_LABELS` — protect built-ins from the delete button and stop a
  user `.json` from shadowing one.

**Combo population**

- `_populate_preset_combo` — adds "Default", then the user presets, then the
  built-ins grouped by instrument with separators (sorted by instrument name,
  curated order preserved within a group via stable sort). Guided mode shows
  only the recommended starter (i1Pro TC9.24).
- `_add_builtin_preset_item` — appends a bold, tooltipped, pinned entry;
  `disabled=True` greys it out and blocks selection.
- `_prebuilt_tooltip(paper)` — the tooltip body for a prebuilt preset.
- `_builtin_default_name(key)` — the name suggested in the prompt
  (`PREBUILT_PRESETS[key][1]`, else `"chart"`).

**Selection → creation**

- `_on_preset_selected` — for any `BUILTIN_PRESET_KEYS` entry: guard against a
  running process, prompt for a target name (Cancel reverts the dropdown), then
  route straight to `_apply_prebuilt_preset(key, name)`.
- `_apply_prebuilt_preset` — sets `_prebuilt_active`, pins the instrument to
  `-i i1` (so downstream routing treats it as a normal strip-read chart), greys
  the param panels, then calls `_create_prebuilt_target`.
- `_create_prebuilt_target` — copies `<stem>.ti1`/`.ti2` and the `<stem>_NN.tif`
  pages into the run as `chart.ti1` / `chart.ti2` / `chart_NN.tif`, clears
  `_last_params`, and hands the TIFF list to `_on_generate_finished` (same path
  a generated chart takes). `resource_path()` resolves the asset in both dev and
  frozen builds.
- `_leave_prebuilt` — clears the prebuilt state and re-enables the panels (run
  when the user picks Default / a user preset / loads a different patch set).

---

## Asset layout

Charts are filed by **creator / colorspace / instrument / paper / target**:

```
assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/
    <stem>.ti1
    <stem>.ti2
    <stem>_01.tif
    <stem>_02.tif      # only multi-page charts
```

e.g. `assets/charts/pharmacist/rgb/i1pro/a4/tc924/tc924.{ti1,ti2}` +
`tc924_01.tif` / `tc924_02.tif`. The stem inside the leaf folder is the
`asset_stem`'s last path component; `PREBUILT_PRESETS` stores the stem path
without extension, so `_create_prebuilt_target` can find every file by globbing
`<stem>_*.tif` next to `<stem>.ti1`.

---

## Recipe: add another prebuilt-files preset

1. **Bundle the files.** Drop `<stem>.ti1`, `<stem>.ti2` and the page TIFFs
   (`<stem>_01.tif`, …) into
   `assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/`. Name
   the TIFFs `<stem>_NN.tif` (zero-padded, 1-based) — a single-page chart still
   uses `<stem>_01.tif`.
2. **Add the constants.** Define `FOO_PRESET_KEY = "__chromiq_foo_builtin__"`
   (unique sentinel) and `FOO_PRESET_LABEL = "★  <name> by <author>  ·  built-in"`.
3. **Register it.** Add `FOO_PRESET_KEY: ("assets/charts/.../<stem>", "<default-name>")`
   to `PREBUILT_PRESETS`. `BUILTIN_PRESET_KEYS` derives from the dict
   automatically; add the label to `BUILTIN_PRESET_LABELS`.
4. **Show it.** Add `(instrument, FOO_PRESET_LABEL, FOO_PRESET_KEY, self._prebuilt_tooltip("<paper>"))`
   to the `builtins` list in `_populate_preset_combo`.
5. **Verify** (see snippet below): the asset files resolve, the key is in the
   registry, and the suite passes.

### Rename or re-file an existing preset

- **Rename (label only):** change `*_PRESET_LABEL` and update
  `BUILTIN_PRESET_LABELS`. The `*_PRESET_KEY` is the stable identity — leave it
  alone so saved selections still resolve.
- **Re-file (move assets):** move the leaf folder and update the `asset_stem` in
  `PREBUILT_PRESETS`. Nothing else references the path.
- **Park temporarily:** add the key to `DISABLED_BUILTIN_PRESET_KEYS` — it stays
  visible but greyed-out and unselectable; remove it to re-enable.

### Gotchas

- The **key**, not the label, is the identity. Renaming a label must never
  change the key, or existing projects/selections lose their preset.
- TIFFs **must** be `<stem>_NN.tif`. `_create_prebuilt_target` globs
  `<stem>_*.tif`; a bare `<stem>.tif` is not picked up.
- The preset's instrument is pinned to i1Pro layout routing (`-i i1`) for the
  downstream hand-off; the *actual* instrument the chart was laid out for is
  read from the bundled `.ti2` (`TARGET_INSTRUMENT`) during measurement.
- The bundled `.ti2` should be **randomised** (carry `RANDOM_START`). A
  fixed-order chart (`CHART_ID`) can make chartread misrecognise strips — see
  the `analyze_randomisation` gate.
- Don't let a user `.json` preset share a built-in's key or label;
  `_populate_preset_combo` already filters those out, but keep new keys unique.

---

## Verify snippet

```python
# QT_QPA_PLATFORM=offscreen python - <<'PY'
from ui.tabs.tab_chart import PREBUILT_PRESETS, BUILTIN_PRESET_KEYS, BUILTIN_PRESET_LABELS
from core.resource_path import resource_path

assert set(PREBUILT_PRESETS) == BUILTIN_PRESET_KEYS
for key, (stem, default) in PREBUILT_PRESETS.items():
    ti1 = resource_path(f"{stem}.ti1")
    ti2 = resource_path(f"{stem}.ti2")
    tiffs = sorted(ti1.parent.glob(f"{ti1.stem}_*.tif"))
    assert ti1.is_file() and ti2.is_file() and tiffs, f"{key}: missing assets"
    print(f"OK  {key}  ({len(tiffs)} page[s], default name {default!r})")
print("labels:", len(BUILTIN_PRESET_LABELS))
# PY
```

Run the full suite (`QT_QPA_PLATFORM=offscreen pytest`) after any change here —
`tests/test_pharmacist_builtin_chart.py`, `tests/test_tc924_prebuilt.py` and
`tests/test_chart_tab.py` cover the registry, the asset files, and the
copy-into-run flow for both instruments.
