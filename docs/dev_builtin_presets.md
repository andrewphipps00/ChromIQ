# Developer note — built-in Create Chart presets

How the built-in "by Pharmacist" presets were implemented, and how to **add**,
**rename**, or **re-file** one quickly. "Built-in" means: it always appears in
the Create Chart → Manual **Presets** dropdown, the user can't delete it, and
selecting it does something fixed.

> **Start here.** The single list that decides which built-ins exist and how
> they're grouped is the `builtins = [ (instrument, label, key, tooltip), … ]`
> literal inside `_populate_preset_combo()` (`ui/tabs/tab_chart.py`). Everything
> else (`*_PRESET_KEY`/`*_PRESET_LABEL` constants, the `MUNKI_TARGEN` /
> `PREBUILT_PRESETS` tables, and `BUILTIN_PRESET_KEYS`/`BUILTIN_PRESET_LABELS`
> derived from them) feeds that list. To add → append a row + its constants; to
> rename → edit a constant; to regroup → edit a row's first field. The
> "[Rename or re-file](#rename-or-re-file-an-existing-preset)" section is the
> checklist.

There are five built-in presets today, of **three different kinds**:

1. **ti1-based** — *i1Pro TC9.18 by Pharmacist*. Loads a bundled `.ti1` and runs
   `printtarg` only (targen skipped), creating the target right after the name
   prompt (see below).
2. **params-based** — *ColorMunki 324 patch standard quality* and *ColorMunki
   648 patch high quality*. Plain parameter sets (normal `targen→printtarg`);
   selecting one seeds the settings and then **auto-generates** by calling
   `_on_generate()` (the preset combo is manual-only, so `_collect_params`
   routes to `_collect_manual`). Both select the ColorMunki and turn on **Triple
   density**, so `printtarg` lays the chart out with the denser i1Pro geometry
   (`-ii1`) and `chart_creator._patch_ti2_instrument` rewrites the `.ti2`
   `TARGET_INSTRUMENT` back to `X-Rite ColorMunki`. The two share one apply
   method (`_apply_colormunki_td_preset`) and a `MUNKI_TARGEN` table that maps
   each key to its `(patches, white, black, grey)` targen counts — add more by
   adding a row, no new method needed.
3. **prebuilt-files** — *i1Pro TC9.24 A4* and *i1Pro TC9.24 Letter by Pharmacist*.
   A **complete, pre-generated target** (`.ti1` + `.ti2` + page `.tif`s) bundled in
   `assets/charts/`. Selecting one prompts for a name, **copies** the bundled files
   into a fresh `~/ChromIQ/<name>/` folder (renamed to `<name>…`) and loads the
   copied TIFFs — **neither targen nor printtarg runs**, so the param panels are
   greyed out while the preset is active. The two share `_apply_prebuilt_preset` /
   `_create_prebuilt_target` and a `PREBUILT_PRESETS` table mapping each key to its
   `(asset_stem, default_name)` — add more by adding a row.

In the dropdown the order is **Default → user presets → built-ins** (built-ins
pinned at the bottom). The built-ins are **grouped by instrument**, the groups
ordered by instrument name, with a **divider line** before the built-in block
(separating it from the user presets) and before each new instrument group. The
grouping/sort lives in `_populate_preset_combo` (a `(instrument, label, key,
tooltip)` list, stable-sorted by instrument, `insertSeparator()` on each group
change). The default combo separator is nearly invisible on the dark theme and
QSS `::separator` isn't honoured for combo views, so `_ComboSeparatorDelegate`
(module-level, set via `setItemDelegate`) paints the line itself in a
palette-derived colour; separator rows are non-selectable and an extra guard at
the top of `_on_preset_selected` ignores any that somehow become current.

All of this lives in `ui/tabs/tab_chart.py` unless noted.

## Concept

A normal preset is one `.json` file under `presets_dir()` (see
`core/preset_store.py`) holding captured parameter values. A built-in preset is
**not** a file — it's a hard-coded combo entry identified by a sentinel
`userData` value, so the disk-backed save/load/delete code never touches it.
All built-in keys live in `BUILTIN_PRESET_KEYS` (labels in
`BUILTIN_PRESET_LABELS`); `_is_deletable_preset()`, the disk-shadow filter, and
`_add_builtin_preset_item()` (bold + tooltip) all key off those sets.

The ti1-based TC9.18 preset additionally pins its patch set to a **bundled
`.ti1`** and runs `printtarg` only (skipping `targen`), because its OFPS patch
set can't be recreated reliably by re-running `targen`.

### User presets: "generate on select" (auto-run)

Separate from built-ins, a **user** preset can opt into the same generate-on-
select behavior via a checkbox in the Save dialog (`_on_preset_save`). The flag
is stored as `data["auto_run"]` *inside the preset's own `.json`*, so it travels
with a shared preset — `preset_store` round-trips the whole `data` dict, and the
restore loop ignores keys that don't match a `{tool}_{flag}` widget.

- Combo: auto-run user presets get a `▶ ` prefix in `_populate_preset_combo`
  (`userData` stays the bare name).
- Identity-by-text was therefore replaced with identity-by-`userData`:
  `_populate_preset_combo` reselect uses `findData`, `_on_preset_delete` uses
  `currentData()`. (Restore already used `currentData()`.)
- Selection (`_on_preset_selected`, end of method): the values are restored
  normally first, *then* if `auto_run` it shows `_prompt_target_name` and, on
  confirm, sets the target name and calls `_on_generate()`. **Cancel keeps the
  preset selected with its values loaded but doesn't generate** — deliberately
  different from a built-in's full revert, so an auto-run user preset stays
  selectable for delete / re-save.

### User presets: attached `.ti1` (build from a bundled patch set)

A user preset can also **bundle its own `.ti1`**, mirroring the ti1-based
built-in but with a user-supplied patch set. The Save dialog (`_on_preset_save`)
shows a *"Build from the currently loaded patch set (attach its .ti1)"* checkbox,
enabled only when `self._current_ti1_path` points at a real file (set in
`_on_generate_finished` from `<work_dir>/<stem>.ti1` after any generate/load).

- On save: the loaded `.ti1` is copied next to the preset's `.json` via
  `preset_store.sidecar_path("create_chart", name, ".ti1")` (same filename stem,
  so the pair travels together when shared), and `data["attached_ti1"] = True` is
  stored. Turning the option off deletes a stale sidecar; `_on_preset_delete`
  removes it too. (Note: `save_presets` only rewrites `*.json`, so non-JSON
  sidecars are managed by hand in these two methods.)
- On selection: `_on_preset_selected` sets `self._preset_ti1_path` to the sidecar
  if `attached_ti1` is set (else `None`). `_on_generate` checks this **before** the
  TC9.18 path and routes to `_generate_from_ti1(self._preset_ti1_path)` — targen
  skipped, printtarg lays it out. Works with `auto_run` too (prompt → generate).

## Moving parts (TC9.18)

Module-level constants (top of `tab_chart.py`):

| Constant | Purpose |
|----------|---------|
| `TC918_PRESET_KEY` | Sentinel `userData` that identifies the combo item (never matched by text). |
| `TC918_PRESET_LABEL` | Display text (`★ … · built-in`). |
| `TC918_TI1_ASSET` | Path of the bundled `.ti1` relative to the repo/bundle root. |
| `TC918_TARGET_NAME` | Default output base name. |
| `TC918_PRINTTARG` | `{flag: value}` map of the fixed printtarg layout (the "recipe"). |

State (set in `__init__`): `self._tc918_active`, `self._tc918_targen_sig`.

Methods:

- `_populate_preset_combo()` — inserts the built-in item just under "Default"
  (bold via `FontRole`, tooltip via `ToolTipRole`); skips any disk preset that
  would shadow it.
- `_is_deletable_preset(index)` — gates the − button; returns `False` for
  "Default" (data `None`) and the built-in (data == `TC918_PRESET_KEY`). Also
  guards `_on_preset_delete()`.
- `_on_preset_selected(index)` — for any `data in BUILTIN_PRESET_KEYS`, first
  prompts for a target name (`_prompt_target_name`, default from
  `_builtin_default_name`). **Cancel** → `_revert_preset_combo()` (restore the
  dropdown to `_last_preset_index`, apply/generate nothing). **Confirm** → apply
  the preset with that name and generate. Non-built-in selections run the normal
  restore. `_last_preset_index` is updated on every committed selection (and in
  `_populate_preset_combo`) so a cancel can revert to it.
- `_apply_tc918_preset()` — turns off Triple density / Auto patch count / Auto
  neutrals, seeds `TC918_PRINTTARG` via `_set_manual_value()`, writes the targen
  "description" fields, snapshots `_tc918_targen_signature()`, sets
  `_tc918_active`, refreshes the command preview, then `_generate_from_ti1()`.
- `_generate_from_ti1(ti1_path)` — emits `target_started`, sets the target name,
  collects params, and calls `chart_creator.load_ti1_and_generate_preview()`
  (printtarg-only). Shared by the initial creation and later regenerations.
- `_tc918_targen_signature()` — snapshot of **targen-only** controls. Equality
  between snapshot and current state means "reproduce the bundled chart"; any
  difference means the user changed the patch set.
- `_reset_tc918_overrides()` — reverts every `TC918_PRINTTARG` flag to its YAML
  default via `ParameterWidget.reset_to_default()` when leaving the preset.
- `_on_generate()` — before the normal path: if `_tc918_active` and the targen
  signature is unchanged, regenerate from the bundled `.ti1` and return; if it
  changed, clear `_tc918_active` and fall through to a normal `targen→printtarg`.
- `_on_load_ti1()` — clears `_tc918_active` (a different patch set is now loaded).
- `_refresh_manual_command_preview()` — shows a "targen skipped" note while the
  preset reproduces the bundled chart.

Supporting code:

- `ui/parameter_widget.py::reset_to_default()` — restores the YAML default and
  **unticks the expert enable-checkbox**. Needed because expert non-boolean rows
  (e.g. `-A`, `-m`) keep emitting their flag while ticked even after the value
  reverts; expert booleans (`-c`, `-r`, `-P`) are handled by `set_value` alone.
- `workflow/chart_creator.py::load_ti1_and_generate_preview()` /
  `_build_printtarg_args()` — the printtarg-only run and the arg builder the
  recipe must satisfy.
- `assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/` — the
  bundled patch sets, filed by that taxonomy (e.g.
  `pharmacist/rgb/i1pro/a4/tc918/`, `…/a4/tc924/`, `…/letter/tc924/`); file
  stems are the bare `<target>` (`tc924.ti1`, not `tc924_a4.ti1`). See
  `assets/charts/README.md` for the provenance + recipe table. Anything under
  `assets/` is shipped automatically via `('assets','assets')` in `ChromIQ.spec`;
  resolve a file at runtime with `core.resource_path.resource_path(
  "assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/<file>")`.

## Gotchas learned

- **Expert enable-checkbox.** A flag only reaches the command if its enable-
  checkbox is ticked. `_set_manual_value()` ticks it for any value it sets;
  `reset_to_default()` unticks it. Forgetting the unset side is exactly the bug
  that left `-A`/`-c` active after switching back to Default.
- **`-m` drives both `-m` and `-M`.** One UI widget; `_build_printtarg_args`
  emits `-m` only when ≠ 6 but always emits `-M`.
- **`-t<dpi>` is always present** (printtarg needs an output resolution); it's not
  part of the recipe map but will show in the built command.
- **Mutually exclusive flags.** `-c` (colored spacers) vs `-b` (B&W spacers) —
  pick one. Verify the real `printtarg` help before trusting a copied command.
- **Reproducibility.** Don't try to recreate an OFPS chart by guessing `targen`
  flags; bundle the `.ti1` and run printtarg on it.
- **Triple density ordering (params-based).** Triple density is gated to the
  ColorMunki: set the instrument to `CM` *first*, then tick `_manual_td_check`.
  `_on_manual_td_toggled` then seeds `-a1.3 / -m5 / -P / -L` and stashes the
  prior values, and `chart_creator` rewrites the `.ti2` to ColorMunki at
  generate time (because `p.triple_density` is True). Enabling TD on a non-CM
  instrument is force-unchecked by `_update_manual_lb_visibility`.

## Recipe — add another built-in preset

**Another ColorMunki variant (easiest):** add one row to `MUNKI_TARGEN`
(`key -> (patches, white, black, grey)`), a `*_PRESET_KEY` + `*_PRESET_LABEL`
constant, include the label in `BUILTIN_PRESET_LABELS`, and add one
`_add_builtin_preset_item(LABEL, KEY, self._munki_tooltip(*MUNKI_TARGEN[KEY]))`
call. `_on_preset_selected` already dispatches anything in `MUNKI_TARGEN` to the
shared `_apply_colormunki_td_preset`, and `BUILTIN_PRESET_KEYS` is derived from
`MUNKI_TARGEN`, so no new branch or method is needed.

**A different kind of preset:**

1. Add `FOO_PRESET_KEY` + `FOO_PRESET_LABEL` and include them in
   `BUILTIN_PRESET_KEYS` / `BUILTIN_PRESET_LABELS`.
2. Add a `_add_builtin_preset_item(...)` call in `_populate_preset_combo()`.
3. Branch on the key in `_on_preset_selected()` and call its `_apply_foo_preset`.
   `_is_deletable_preset()` and the disk-shadow filter pick it up automatically.

- **ti1-based** (clone TC9.18): add `_TI1_ASSET` / `_TARGET_NAME` / `_PRINTTARG`
  constants, drop the `.ti1` into its
  `assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/` leaf, and have
  `_apply_foo_preset` seed the layout + call `_generate_from_ti1`. Wire
  `_on_generate()` reproduce routing, the preview note, and `_reset_*_overrides()`
  like TC9.18.
- **params-based** (clone `_apply_colormunki_td_preset`): call
  `_set_manual_value(...)` per flag (turn Auto patches/neutrals off first), set
  pages / bit-depth / target name, then call `self._on_generate()` to create the
  target immediately. No ti1, no reproduce routing — the normal targen→printtarg
  flow runs. (Guard `self._runner.is_running` first.)
- **prebuilt-files** (easiest — just a `PREBUILT_PRESETS` row): drop the bundle
  (`<target>.ti1`, `<target>.ti2`, `<target>_NN.tif`) into its
  `assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/` leaf,
  add a row `KEY: (".../<paper>/<target>/<target>", "<default-name>")`, a `*_PRESET_KEY` +
  `*_PRESET_LABEL` (include the label in `BUILTIN_PRESET_LABELS`), and one
  `_add_builtin_preset_item(LABEL, KEY, self._prebuilt_tooltip("<paper>"))` call.
  `_on_preset_selected` already dispatches anything in `PREBUILT_PRESETS` to
  `_apply_prebuilt_preset`, and `BUILTIN_PRESET_KEYS` is derived from it.
  `_create_prebuilt_target` copies the files (renamed to the chosen name), greys
  the panels via `_set_manual_params_enabled(False)`, pins the instrument to `i1`,
  and routes through `_on_generate_finished` so Print/Measure get the files. The
  panels are re-enabled by `_leave_prebuilt()` on any other selection or
  `_on_load_ti1`. `_on_generate` re-copies to the current Output name while active.

The three kinds have one dispatch branch each in `_on_preset_selected`; only add a
fourth branch for a genuinely new *kind*, not for more variants of an existing one.

## Rename or re-file an existing preset

Renaming changes what the user *sees* or *where its files live* — never what the
preset *does*. Work through only the rows that apply:

- **`*_PRESET_KEY` — leave it alone.** It's the runtime identity matched by
  `BUILTIN_PRESET_KEYS`, the dispatch in `_on_preset_selected`, and
  `_is_deletable_preset`. It's never shown to the user and never written to disk,
  so a rename has no reason to touch it. (If you truly must, change it in the
  constant, any `MUNKI_TARGEN`/`PREBUILT_PRESETS` table key, **and**
  `BUILTIN_PRESET_KEYS` in the same commit, or the item silently becomes
  deletable / undispatched.)
- **Visible name** → edit `*_PRESET_LABEL` only. It feeds both the combo text and
  the bold built-in styling (via `BUILTIN_PRESET_LABELS`) automatically. Keep the
  `★  …  ·  built-in` shape. The label text does **not** drive grouping, so reword
  freely.
- **Group / order** → the only source of truth is the `builtins` list in
  `_populate_preset_combo`. Edit a row's **first field** (`instrument`) to move it
  to another group; reorder rows to change order within a group (stable sort, so
  list order is kept). Don't infer the group from the label.
- **Default output name** → `*_TARGET_NAME` (ti1-based) or the 2nd tuple element
  of the `PREBUILT_PRESETS` row (prebuilt). The shared prompt default comes from
  `_builtin_default_name`.
- **Re-file the bundled charts** (different `creator/colorspace/instrument/paper/
  target` leaf, or a new `<target>` stem):
  1. `git mv` the files into the new leaf. Keep each file's stem equal to the
     `<target>` folder name (`tc924.ti1`, **not** `tc924_a4.ti1` — the path
     carries the metadata). Delete the emptied old dirs (`rmdir`).
  2. Update the path: `TC918_TI1_ASSET` (ti1-based) or the **1st** tuple element
     (asset stem) of the `PREBUILT_PRESETS` row. The stem is the path with **no**
     extension and **no** `_NN`; `_create_prebuilt_target` appends `.ti1`/`.ti2`
     and globs `<stem>_*.tif`.
  3. Update the table in `assets/charts/README.md`.
  4. No `.gitignore` change needed — `!assets/charts/**/*.tif{,f}` is recursive.

**Verify after any asset move or re-file** (from the repo root, venv active):

```bash
# 1. every bundled chart path still resolves
python -c "
from core.resource_path import resource_path
from ui.tabs.tab_chart import TC918_TI1_ASSET, PREBUILT_PRESETS
import os, glob
assert os.path.isfile(resource_path(TC918_TI1_ASSET)), TC918_TI1_ASSET
for stem, _ in PREBUILT_PRESETS.values():
    rp = str(resource_path(stem))
    for ext in ('.ti1', '.ti2'):
        assert os.path.isfile(rp + ext), rp + ext
    assert glob.glob(rp + '_*.tif'), rp + '_*.tif'
print('all bundled chart paths OK')
"
# 2. moved TIFFs are still tracked, not gitignored (prints nothing == good)
git check-ignore assets/charts/**/*.tif
# 3. nothing else still points at an old path
grep -rn "charts/" --include="*.py" --include="*.md" . | grep -v ".venv/"
# 4. suite
QT_QPA_PLATFORM=offscreen pytest -q
```
