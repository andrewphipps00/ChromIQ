# Bundled reference charts

These patch sets ship inside the app (everything under `assets/` is bundled by
`('assets','assets')` in `ChromIQ.spec`) and back the **built-in Create Chart
presets**. They are the *only* charts committed to the repo — every other
`.tif`/`.ti1`/`.ti2` is a workflow output and is gitignored. The `.gitignore`
re-includes these with `!assets/charts/**/*.tif{,f}`.

## Layout

Charts are filed by a fixed taxonomy, one folder level each:

```
assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/<files>
```

- **creator** — who made the patch set (e.g. `pharmacist`)
- **colorspace** — device colorspace of the patches (`rgb`, `cmyk`, …)
- **instrument** — measuring instrument the layout targets (`i1pro`, …)
- **paper** — page size the TIFFs are laid out for (`a4`, `letter`, …)
- **target** — the chart itself (`tc918`, `tc924`, …); files inside use this stem

The path carries the descriptive metadata, so file stems stay short (`tc924.ti1`,
not `tc924_a4.ti1`). Resolve a file at runtime with
`core.resource_path.resource_path("assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/<file>")`.

| Path | Preset (Create Chart → Manual) | Kind | Files |
|------|--------------------------------|------|-------|
| `pharmacist/rgb/i1pro/a4/tc918/` | ★ i1Pro TC9.18 by Pharmacist | ti1-based | `tc918.ti1` |
| `pharmacist/rgb/i1pro/a4/tc924/` | ★ i1Pro TC9.24 A4 by Pharmacist | prebuilt-files | `tc924.ti1` `tc924.ti2` `tc924_01.tif` `tc924_02.tif` |
| `pharmacist/rgb/i1pro/letter/tc924/` | ★ i1Pro TC9.24 Letter by Pharmacist | prebuilt-files | `tc924.ti1` `tc924.ti2` `tc924_01.tif` `tc924_02.tif` |

## How each kind is used

- **ti1-based** (`…/a4/tc918/`) — only the `.ti1` is bundled. Selecting the preset
  prompts for a name, **copies the bundled `.ti1` into a fresh `~/ChromIQ/<name>/`
  (renamed to the chosen target name)**, then runs **printtarg only** on that copy
  (targen skipped) with the fixed Pharmacist layout
  `printtarg -ii1 -pA4 -t300 -L -m12 -M12 -b` to produce the `.ti2` + page TIFFs.
  The OFPS patch order can't be reliably recreated by re-running targen, so the
  `.ti1` is the source of truth.

- **prebuilt-files** (`…/tc924/`) — a complete, pre-generated target (`.ti1` +
  `.ti2` + page TIFFs). Selecting the preset prompts for a name and **copies all
  the files verbatim** into a fresh `~/ChromIQ/<name>/` (renamed to the chosen
  target name); no targen and no printtarg are run. The `_NN.tif` pages are
  located by globbing `<stem>_*.tif` next to the `.ti1`.

Either way the resulting `~/ChromIQ/<name>/` folder is self-contained: it holds
a `<name>.ti1` plus the generated/copied `<name>.ti2` and `<name>_NN.tif` pages.

## Adding another set

Drop the files in the matching `assets/charts/<creator>/<colorspace>/<instrument>/<paper>/<target>/`
leaf (create the levels that don't exist yet; keep the `<target>` stem on the
files) and wire the preset in `ui/tabs/tab_chart.py` — see
`docs/dev_builtin_presets.md` for the full recipe. Source charts in this batch
came from Pharmacist.
