# Developer note — the per-run working-folder layout

ChromIQ stores each profiling project as a folder under
`~/ChromIQ/<target-name>/` (or the custom output path). The structure and all
path construction are owned by `core/file_manager.py` via three classes:
`Project`, `Run`, and `Calibration`.

## The layout

```
<target-name>/                       # = sanitised project name (spaces → hyphens)
  project.json                       # manifest
  cal/                               # optional; one calibration shared by all runs
    <target-name>-cal.ti1 / .ti2 / .cht / .ps
    <target-name>-cal_NN.tif
    <target-name>-cal.ti3            # calibration measurement
    <target-name>-cal.cal            # printcal output
    <target-name>-cal.icc
    <target-name>-cal.channels.json
    meta.json
  exports/                           # external-tool exports
    <target-name>-i1profiler.txt
    <target-name>-i1profiler.pxf
  runs/
    run1/                            # one folder per profile build
      <target-name>.ti1 / .ti2 / .cht / .ps / .channels.json
      <target-name>_NN.tif           # NN = page index (a real ordinal, not state)
      reads/                         # present only when averaging is used
        read1.ti3 / read2.ti3 / …    # role-named (in subfolder)
      <target-name>.ti3              # the measurement (chartread output)
      <target-name>.icc              # the profile (colprof output)
      preconditioning.ti3 / .icc     # role-named; seeded from a parent run
      merged.ti3 / merged.icc        # role-named; build-time refinement merge
      calibrated.icc                 # role-named; applycal output
      meta.json
    run2/ …
```

The **chart's own files** (`.ti1`/`.ti2`/`.ti3`/`.icc`/page TIFFs/`.cht`/`.ps`/
`.channels.json`) carry the sanitised project name as their stem — so
printtarg stamps it on the printed sheet, the built ICC is self-identifying in
ColorSync and Finder, and the install path in the Check tab copies it to the
system folder as `<target-name>.icc`. The **derived/intermediate files** stay
role-named (`reads/readN.ti3`, `preconditioning.*`, `merged.*`,
`calibrated.icc`) — they never go on a printed sheet and the role name is
clearer at a glance than `<target-name>_merged.ti3` would be.

Calibration uses `<target-name>-cal` so its printed sheet shows the project
name (you can tell which project the cal target belongs to) AND is
distinguishable from the profiling chart, which lives in `runs/<id>/` under
the bare project name.

## The one rule

**Role is encoded in the filename within a folder; the folder disambiguates
context.** `runs/run1/chart.ti3` and `runs/run2/chart.ti3` are the same role in
different runs. There are no `pre_`/`cal_` prefixes and no
`_readN`/`_average`/`_merged` suffixes — the folder boundary does that work.

This kills several whole bug classes that the old flat-folder + prefix/suffix
scheme allowed:

- **Cross-run averaging collision** — run 2's `reads/` cannot see run 1's, so
  averaged reads can never be double-counted into a refinement merge.
- **Suffix-stem collisions** — no `set_ti3_path` stripping of `_average`/`_readN`
  to recover a `.ti2`; `chart.ti3` sits next to `chart.ti2`.
- **`.json`-as-CGATS confusion** — pre-conditioning data is a real
  `preconditioning.ti3`, not CGATS hidden under a `.json` name.

## Why Argyll-natural stems (`chart.ti3`, `chart.icc`)

chartread and colprof are **stem-coupled**: reading `chart.ti2` writes
`chart.ti3`; reading `chart.ti3` writes `chart.icc`. Naming the measurement
`measurement.ti3` or the profile `profile.icc` would force a rename after every
tool — reintroducing exactly the stem fragility this layout removes. So the
canonical measurement is `chart.ti3` and the profile is `chart.icc`. The
per-run folder supplies the role context the generic stem lacks.

A refinement merge is the exception that proves the rule: `average -m` writes a
throwaway `merged.ti3`, colprof builds `merged.icc` from it, and
`Run.built_profile_icc()` returns `merged.icc` when present (else `chart.icc`).
Check & Refine always works from the clean `chart.ti3` (Architecture D).

## The API

| Class | Responsibility |
|-------|----------------|
| `Project` | `project.json` manifest; `create` / `load` / `create_or_load`; `current_run()`, `new_run(preconditioning_from=…)`, `all_runs()`; `calibration`, `exports_dir` |
| `Run` | every path in a run folder (`chart_ti1`, `measurement_ti3` → `chart.ti3`, `profile_icc` → `chart.icc`, `merged_ti3/_icc`, `calibrated_icc`, `preconditioning_ti3/_icc`); `reads()`, `next_read_path()`, `promote_measurement_to_read()`, `clear_reads()`, `reset_chart_artefacts()`, `built_profile_icc()` |
| `Calibration` | the `cal/` folder (`cal_path`, `ti1/.ti2/.ti3/.icc`, `chart_tiffs()`, `exists()`, `reset()`) |

Two entry points:

- `FileManager.project()` — the current target's `Project` (created on first
  access, cached until `set_target_name` changes).
- `Run.for_dir(run_dir)` — a project-less `Run` bound to an explicit folder, for
  path operations where threading the whole `Project` through isn't worth it
  (e.g. the Measure tab deriving the run from the chart's `.ti1` parent).

**Never build a working-folder path by string concatenation outside these
classes.** Adding a new artefact means adding a property to `Run` (and, if it
should be wiped on chart regeneration, listing it in `reset_chart_artefacts`).

## Key flows

- **First profile** — `Project.create` makes `run1`; chart_creator runs in
  `runs/run1/` with stem `chart`; chartread writes `chart.ti3`; colprof writes
  `chart.icc`.
- **Calibration target** — `cal_target=True` routes chart_creator to `cal/`
  with stem `calibration`; detection elsewhere is "ti3 lives in `cal/`".
- **Averaging** — "Measure again" moves `chart.ti3` → `reads/readN.ti3`;
  "Average all reads" runs `average reads/*.ti3` back into `chart.ti3`.
- **Pre-conditioning refinement** — "Use as pre-conditioning profile" makes
  tab_chart capture the parent run id; at Generate-click,
  `Project.new_run(preconditioning_from=parent)` copies the parent's
  `chart.ti3`/`chart.icc` into the new run as `preconditioning.ti3`/`.icc` and
  makes it current. Build-time merge → `merged.ti3` → `merged.icc`.
- **External `-c` profile** — `chart_creator._import_external_preconditioning`
  copies a vendor `.icc` (and sibling `.ti3` when refinement is on) into the
  run as `preconditioning.*`.
- **Session restore** — only `session_target_name` is persisted; every artefact
  path is re-derived from `project.current_run()`.

## Migration

There is **no migration** from the old flat-folder layout — the redesign
shipped on a clean break (small early user base). Projects created by older
versions are simply not picked up by session restore (the `project.json`
existence check bails); the user starts fresh.
