# Developer note — built-in Create Chart presets

How the **TC9.18 by Pharmacist** preset was implemented, and how to add another
built-in preset quickly. "Built-in" means: it always appears in the Create Chart
→ Manual **Presets** dropdown, the user can't delete it, and selecting it does
something fixed (here: load a bundled `.ti1` and create the target immediately).

All of this lives in `ui/tabs/tab_chart.py` unless noted.

## Concept

A normal preset is one `.json` file under `presets_dir()` (see
`core/preset_store.py`) holding captured parameter values. A built-in preset is
**not** a file — it's a hard-coded combo entry identified by a sentinel
`userData` value, so the disk-backed save/load/delete code never touches it.

The TC9.18 preset additionally pins its patch set to a **bundled `.ti1`** and
runs `printtarg` only (skipping `targen`), because its OFPS patch set can't be
recreated reliably by re-running `targen`.

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
- `_on_preset_selected(index)` — if `itemData == TC918_PRESET_KEY`, calls
  `_apply_tc918_preset()` and returns. Otherwise, if we were on the built-in,
  calls `_reset_tc918_overrides()` first, then runs the normal restore.
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
- `assets/charts/tc918-grays.ti1` — the bundled patch set. Anything under
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

## Recipe — add another bundled-chart preset

1. Drop the `.ti1` into `assets/charts/`.
2. Add a parallel set of constants (`FOO_PRESET_KEY`, `_LABEL`, `_TI1_ASSET`,
   `_TARGET_NAME`, `_PRINTTARG`).
3. Add the combo item + styling in `_populate_preset_combo()`, and exclude its
   key/label from disk presets.
4. Branch on its key in `_on_preset_selected()` and `_is_deletable_preset()`.
5. Add a `_apply_foo_preset()` (clone `_apply_tc918_preset`), and route
   `_on_generate()` / the preview note / `_reset_*_overrides()` the same way.

If you end up with **more than two** built-in presets, refactor first: replace
the per-preset constants and `if key == …` branches with a list of small spec
objects (key, label, ti1 asset, printtarg map) and loop over it. The single
hard-coded preset above is deliberately the simplest thing that works for one.
