# Bundled reference charts

These patch sets ship inside the app (everything under `assets/` is bundled by
`('assets','assets')` in `ChromIQ.spec`) and back the **built-in Create Chart
presets**. They are the *only* charts committed to the repo — every other
`.tif`/`.ti1`/`.ti2` is a workflow output and is gitignored. The `.gitignore`
re-includes these with `!assets/charts/**/*.tif{,f}`.

One subfolder per chart set; filenames keep the set name as a prefix so a file
is self-describing even when opened on its own. Resolve a file at runtime with
`core.resource_path.resource_path("assets/charts/<set>/<file>")`.

| Folder | Preset (Create Chart → Manual) | Kind | Files |
|--------|--------------------------------|------|-------|
| `tc918/` | ★ i1Pro TC9.18 by Pharmacist | ti1-based | `tc918.ti1` |
| `tc924_a4/` | ★ i1Pro TC9.24 A4 by Pharmacist | prebuilt-files | `.ti1` `.ti2` `_01.tif` `_02.tif` |
| `tc924_letter/` | ★ i1Pro TC9.24 Letter by Pharmacist | prebuilt-files | `.ti1` `.ti2` `_01.tif` `_02.tif` |

## How each kind is used

- **ti1-based** (`tc918/`) — only the `.ti1` is bundled. Selecting the preset
  prompts for a name, **copies the bundled `.ti1` into a fresh `~/ChromIQ/<name>/`
  (renamed to the chosen target name)**, then runs **printtarg only** on that copy
  (targen skipped) with the fixed Pharmacist layout
  `printtarg -ii1 -pA4 -t300 -L -m12 -M12 -b` to produce the `.ti2` + page TIFFs.
  The OFPS patch order can't be reliably recreated by re-running targen, so the
  `.ti1` is the source of truth.

- **prebuilt-files** (`tc924_*/`) — a complete, pre-generated target (`.ti1` +
  `.ti2` + page TIFFs). Selecting the preset prompts for a name and **copies all
  the files verbatim** into a fresh `~/ChromIQ/<name>/` (renamed to the chosen
  target name); no targen and no printtarg are run. The `_NN.tif` pages are
  located by globbing `<stem>_*.tif` next to the `.ti1`.

Either way the resulting `~/ChromIQ/<name>/` folder is self-contained: it holds
a `<name>.ti1` plus the generated/copied `<name>.ti2` and `<name>_NN.tif` pages.

## Adding another set

Drop the files in a new `assets/charts/<set>/` subfolder (keep the set-name
prefix) and wire the preset in `ui/tabs/tab_chart.py` — see
`docs/dev_builtin_presets.md` for the full recipe. Source charts in this batch
came from Pharmacist.
