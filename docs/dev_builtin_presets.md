# Developer note — built-in Create Chart presets

How the built-in "by Pharmacist" presets were implemented, and how to add
another quickly. "Built-in" means: it always appears in the Create Chart →
Manual **Presets** dropdown, the user can't delete it, and selecting it does
something fixed.

There are three built-in presets today, of **two different kinds**:

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

In the dropdown the order is **Default → user presets → built-ins** (built-ins
pinned at the bottom).

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
- `assets/charts/tc918.ti1` — the bundled patch set. Anything under
  `assets/` is shipped automatically via `('assets','assets')` in `ChromIQ.spec`;
  resolve it at runtime with `core.resource_path.resource_path(...)`.

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
  constants, drop the `.ti1` into `assets/charts/`, and have `_apply_foo_preset`
  seed the layout + call `_generate_from_ti1`. Wire `_on_generate()` reproduce
  routing, the preview note, and `_reset_*_overrides()` like TC9.18.
- **params-based** (clone `_apply_colormunki_td_preset`): call
  `_set_manual_value(...)` per flag (turn Auto patches/neutrals off first), set
  pages / bit-depth / target name, then call `self._on_generate()` to create the
  target immediately. No ti1, no reproduce routing — the normal targen→printtarg
  flow runs. (Guard `self._runner.is_running` first.)

The ti1-based and params-based kinds still have one `if` branch each in
`_on_preset_selected`; only add a third branch for a genuinely new *kind*, not
for more variants of an existing one.
