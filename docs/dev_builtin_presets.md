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

Built-ins now come in **two kinds**:

1. **prebuilt-files** (the eight "by Pharmacist" charts) — a complete, ready-made
   target is bundled and just copied into the run; **no targen/printtarg**.
2. **ti1 → printtarg** (the 17 "TC9.18+Spyderprint Grays" charts, see the
   dedicated section below) — one shared `.ti1` is bundled and **printtarg is run
   at selection** with a fixed per-preset layout. **targen is greyed** (the patch
   set is fixed) but **printtarg stays editable** so you can re-layout it; only one
   small `.ti1` ships instead of 17 sets of page TIFFs. An "Edit patch recipe"
   override checkbox can unlock targen (→ a fresh, different patch set).

Both kinds grey one or both parameter panels while active, with override
checkboxes to unlock them — see **Panel locks + override checkboxes** below.

The prebuilt-files kind is described first.

A complete,
pre-generated target (`.ti1` + `.ti2` + page `.tif`s) is bundled in `assets/`.
Selecting one prompts for a name, copies the bundled files into a fresh
`~/ChromIQ/<name>/runs/<current>/` folder under the canonical `chart` stem, and
loads the TIFFs — **no `targen` or `printtarg` runs**, so the parameter panels
are greyed out while the preset is active.

The eight shipped presets (all RGB). Labels follow the same
`Instrument · Paper-NNNNp-Mpages Name by Pharmacist` convention as the
ti1→printtarg presets below (patch width / orientation omitted — not stored for
these pre-rendered charts):

| Label (in the dropdown)                                       | Instrument | Asset leaf |
|---------------------------------------------------------------|------------|------------|
| ★ i1Pro · A4-924p-2pages TC9.24 by Pharmacist                 | i1Pro      | `i1pro/a4/tc924` |
| ★ i1Pro · A4-1110p-2pages ABW-optimized by Pharmacist         | i1Pro      | `i1pro/a4/abw1110` |
| ★ i1Pro · A4-1160p-2pages TC9.18 extended greys by Pharmacist | i1Pro      | `i1pro/a4/tc918eg` |
| ★ i1Pro · Letter-1160p-2pages TC9.18 extended greys by Pharmacist | i1Pro  | `i1pro/letter/tc918eg` |
| ★ ColorMunki · A4-300p-1page TC3.00 by Pharmacist             | ColorMunki | `colormunki/a4/tc300` |
| ★ ColorMunki · A4-702p-2pages ABW-optimized by Pharmacist     | ColorMunki | `colormunki/a4/abw702` |
| ★ ColorMunki · A3-924p-1page TC9.24 by Pharmacist             | ColorMunki | `colormunki/a3/tc924` |
| ★ ColorMunki · A3+-1160p-1page TC9.18 extended greys by Pharmacist | ColorMunki | `colormunki/a3plus/tc918eg` |

The `tc918eg` pair is the same patch set in two page sizes; the page size lives
in the label (and is read back from the asset path by `_prebuilt_paper` for the
tooltip), so the two entries are distinguishable in both the dropdown and the
overlay.

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
- `BUILTIN_PRESET_GROUPS` — the single source of truth for *which* built-ins
  exist and *how they group by instrument*, as
  `[(instrument, [(combo_label, overlay_label, key), …]), …]`. Both the dropdown
  and the overlay read it, so they can't drift apart.

**Combo population**

- `_populate_preset_combo` — adds "none", then the user presets, then the
  built-ins grouped by instrument with separators (built from
  `BUILTIN_PRESET_GROUPS`, sorted by instrument name, curated order preserved
  within a group via stable sort). Guided mode shows only the recommended
  starter (i1Pro TC9.24).
- `_add_builtin_preset_item` — appends a bold, tooltipped, pinned entry;
  `disabled=True` greys it out and blocks selection.
- `_prebuilt_tooltip(paper)` — the tooltip body for a prebuilt preset.
- `_builtin_default_name(key)` — the name suggested in the prompt
  (`PREBUILT_PRESETS[key][1]`, else `"chart"`).

**Built-in presets overlay** (`ui/builtin_preset_popup.py`)

- A star button (`BuiltinPresetButton`) sits at the right edge of the
  GUIDED / MANUAL switch row. Clicking it opens `BuiltinPresetPopup` — a
  speech-bubble (same look as the masthead Tools popup) listing the built-ins
  under instrument headers, built from `BUILTIN_PRESET_GROUPS`.
- `_open_builtin_preset_overlay` shows it; `_activate_builtin_preset(key)` wires
  a pick back through the dropdown: switch to Manual, then select the matching
  combo entry (or re-call `_on_preset_selected` if it's already current). So the
  overlay and the dropdown share the *exact* same name-prompt + generate flow.

**Selection → creation**

- `_on_preset_selected` — for any `BUILTIN_PRESET_KEYS` entry: guard against a
  running process, prompt for a target name (Cancel reverts the dropdown), then
  route straight to `_apply_prebuilt_preset(key, name)`.
- `_apply_prebuilt_preset` — sets `_prebuilt_active`, seeds the instrument and
  paper the bundle was made for (`_prebuilt_instrument` / `_prebuilt_paper_code`,
  both parsed from the asset path), snapshots the targen + printtarg signatures
  as the change-detection baseline, greys both panels (locked), then calls
  `_create_prebuilt_target`.
- `_create_prebuilt_target` — copies `<stem>.ti1`/`.ti2` and the `<stem>_NN.tif`
  pages into the run as `chart.ti1` / `chart.ti2` / `chart_NN.tif`, clears
  `_last_params`, and hands the TIFF list to `_on_generate_finished` (same path
  a generated chart takes). `resource_path()` resolves the asset in both dev and
  frozen builds.
- `_leave_prebuilt` — clears the prebuilt state, unticks the override boxes, and
  re-enables the panels (run when the user picks Default / a user preset / loads
  a different patch set).

**Panel locks + override checkboxes** (shared by both kinds)

Selecting a preset that supplies a fixed patch set or layout greys the matching
panel; an override checkbox above each panel (built once per tool in
`_make_manual_panel`, hidden by default) lets the user unlock it.

- `_update_preset_locks` — the single authority. A **ti1 preset**
  (`_ti1_preset_active()`: TC9.18, Knut, or a user preset with an attached `.ti1`)
  greys only **targen**; a **prebuilt** preset greys **both**. Each panel's inner
  content (`_manual_targen_content` / `_manual_printtarg_content` = its basic +
  expert sub-groups) is disabled, never the outer group — so the override row,
  which sits outside that content, stays clickable. Called from every apply /
  leave path and after every Default/user-preset selection.
- `_reset_override_checks` — unticks both boxes (signals blocked, no pop-up) on
  every selection so a freshly picked preset starts locked.
- `_on_override_clicked` — fires only on a real user click (not programmatic
  `setChecked`); shows the warning `InfoDialog` when the box is ticked on.
- **Generate-time routing** keys off the snapshots: for a prebuilt preset,
  `_on_generate` compares the live targen / printtarg signatures against the
  baselines — targen changed → fresh `targen→printtarg`; else printtarg changed →
  re-lay-out the bundled `.ti1` via `_generate_from_ti1` (same patches, new
  layout); else copy the bundled files. User-`.ti1` presets gate the same way on
  `_preset_ti1_targen_sig`.

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
4. **Show it.** Add `(FOO_PRESET_LABEL, "<short overlay label>", FOO_PRESET_KEY)`
   to the right instrument group in `BUILTIN_PRESET_GROUPS` (add a new
   `(instrument, [...])` group if the instrument is new). This single registry
   feeds **both** the Manual presets dropdown (`_populate_preset_combo` derives
   its `builtins` list from it) **and** the Built-in presets overlay
   (`BuiltinPresetPopup`) — no other UI edit is needed. The overlay groups by
   instrument, so the short label should omit the instrument (e.g. just
   `"TC9.24 by Pharmacist"`).
5. **Verify** (see snippet below): the asset files resolve, the key is in the
   registry, and the suite passes.

---

## The ti1 → printtarg kind (TC9.18 + Spyderprint-greys)

These 17 presets (`_Ti1Preset` dataclass + `KNUT_PRESETS` registry in
`ui/tabs/tab_chart.py`) all share **one** bundled `.ti1`
(`assets/charts/knut/rgb/tc918-spyderprint-1168p/1168p.ti1`, 1168 patches) and
differ only in their printtarg layout (instrument, page size, `-a`, margin, `-A`
spacer scale, `-R` seed). Selecting one:

- `_apply_knut_preset(key, name)` → `_seed_knut_preset(key)` seeds every
  printtarg control the recipe touches (and **resets** the optional `-A`/`-R`
  rows when the preset doesn't use them, so values can't leak between presets),
  sets `_knut_active=True`, snapshots the targen controls into `_knut_targen_sig`
  (via the shared `_targen_signature()`), then calls `_generate_from_ti1`.
- **ti1 reuse, signature-gated** (same as the TC9.18 built-in): on later
  **Generate** clicks, `_on_generate` re-lays-out the bundled `.ti1` with
  printtarg only **as long as the targen settings are untouched** (`_knut_active`
  and `_targen_signature() == _knut_targen_sig`). Change a *targen* setting and it
  falls through to a fresh targen run (the OFPS patch set can't be recreated by
  re-running targen, so this is opt-in). The Manual info box says which mode it's
  in. Changing only *printtarg* settings keeps the `.ti1` and just re-lays it out.
- **targen is greyed** (printtarg stays editable). To change the patch set the
  user ticks the "Edit patch recipe" override box — which, once a targen value is
  changed, drops `_knut_active` and falls through to a fresh targen run as above.
  Leaving a preset reverts its forced printtarg flags (`_reset_knut_overrides`),
  unticks the override box, and clears the flags.

### Wide-gamut family (#53) — per-preset `.ti1`

Four more `_Ti1Preset` rows (slugs `wg_*`, label suffix `KNUT_WG_SUFFIX = " · Wide-gamut"`)
are Knut's multi-colour-set charts. They use the **same** ti1→printtarg machinery
but each carries its **own** bundled `.ti1` (different patch sets: 924 / 1196 /
1575 / 2016) under `assets/charts/knut/rgb/widegamut/<leaf>/<stem>.ti1`. The
`_Ti1Preset` dataclass grew optional fields whose **defaults reproduce the shared
TC9.18 presets byte-for-byte**, so only the new family sets them: `ti1_asset`
(per-preset `.ti1`), `patches`/`white`/`black` (descriptive targen display),
`no_strip_limit` (`-P`), `suppress_left_clip` (`-L`), `tiff_16bit` (8- vs 16-bit
`-T`/`-t`), and `suffix` (so `default_target_name` strips the right tail).
`_seed_knut_preset` reads those fields instead of the old hard-coded constants, and
`_apply_knut_preset` resolves `p.ti1_asset` rather than the shared one. To add
another, append a row — no other wiring changes. `test_knut_spyderprint_presets.py`
is field-driven, so it pins both families.

`_Ti1Preset.key` is `__chromiq_knut_<slug>__`; the **slug**, not the display
name, is the stable identity — renaming a `name` must not change a slug.
`KNUT_PRESET_KEYS` folds into `BUILTIN_PRESET_KEYS`, the combo labels into
`BUILTIN_PRESET_LABELS`, and the entries merge into `BUILTIN_PRESET_GROUPS`
(by instrument), so the dropdown + overlay pick them up with no extra wiring.
`_builtin_tooltip(key)` routes these to `_knut_tooltip` (which shows the
printtarg line) instead of `_prebuilt_tooltip`.

> **3-decimal `-a`.** Several recipes are tuned to 3 places (0.929, 1.125, …) to
> land on an exact page count. The printtarg `-a` param carries `decimals: 3` in
> `parameters.yaml` (read by `ParameterWidget`), and `_build_printtarg_args`
> keeps a 3rd decimal only when the value needs it (`-a1.30`/`-a0.95` are
> unchanged). To add another such preset, append a `_Ti1Preset(...)` row — no
> other code change is needed. `tests/test_knut_spyderprint_presets.py` pins the
> seeded command against each recipe.

> **ChromIQ vs. Knut's literal flags.** Knut's commands use a lone `-M8`; ChromIQ
> emits `-m8 -M8` together, which is *functionally identical* (printtarg's `-m`
> and `-M` write the same margin; `-M` only also includes it in the TIFF). The
> i1 charts keep the left clip border (no `-L`, seeded off) to match Knut.

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
from ui.tabs.tab_chart import (
    PREBUILT_PRESETS, KNUT_PRESET_KEYS, BUILTIN_PRESET_KEYS, BUILTIN_PRESET_LABELS,
)
from core.resource_path import resource_path

# Built-ins = prebuilt-files + ti1→printtarg presets.
assert set(PREBUILT_PRESETS) | KNUT_PRESET_KEYS == BUILTIN_PRESET_KEYS
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
