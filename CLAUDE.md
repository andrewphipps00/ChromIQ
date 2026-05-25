# CLAUDE.md — ChromIQ

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python main.py
```

## Test

```bash
source .venv/bin/activate
QT_QPA_PLATFORM=offscreen pytest      # ~1-2s, 284 tests
```

`pytest.ini` scopes collection to `tests/` (via `testpaths`). Without it a bare
`pytest` recurses into `.venv/` and — with `pytest-qt` active — collection
appears to hang for many minutes. If a run takes more than a few seconds,
something is wrong (a test opening a modal dialog `.exec()`, or `.venv` being
scanned again), not just "slow tests".

## Build distributable

```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller ChromIQ.spec
# Result: dist/ChromIQ.app
```

## Architecture

ChromIQ is a PyQt6 GUI for RGB printer ICC profiling with ArgyllCMS 3.5.0.

**Workflow**: `targen → printtarg → [print] → chartread → colprof`

### Module map

| Directory | Purpose |
|-----------|---------|
| `core/` | Settings, ArgyllRunner (QProcess), FileManager, logging, resource_path |
| `data/` | Patch capacity database, parameters.yaml (all CLI flags + tooltips) |
| `ui/` | All Qt widgets — main window, 4 tabs, shared TIFF preview, settings dialog |
| `workflow/` | Business logic — chart creation, PS generation, CUPS printing, measure, profile |

### Data flow

`parameters.yaml` → `ParameterWidget` rows in panels → `workflow/*.py` builds CLI args
→ `ArgyllRunner.run()` → `QProcess` → line_received signal → LogWidget + stripe detection

### Key patterns

**ArgyllRunner** is a singleton `QObject` injected into all workflow classes.
Only one process runs at a time — `is_running` guard checked before each operation.

**resource_path()** in `core/resource_path.py` resolves asset paths for both
development and PyInstaller frozen bundles.

**parameters.yaml** drives `ParameterWidget` creation automatically — add a new
parameter there and it appears in the UI without code changes.

**Patch capacity DB** in `data/patch_db.py` — empirical values from Argyll 3.1/3.5
measured with `printtarg -i<instr> -p<paper> -t300 -L`.  Unknown combos fall back
to binary search in `workflow/chart_creator.py`.

### Printing pipeline

TIFF → `PostScriptGenerator` (hex RGB, PS Level 2, exact PageSize, no scaling)
→ tempfile → `lp -d <printer> -o raw` — bypasses ColorSync and CUPS filters.

### ArgyllCMS binaries

Default path: `/Applications/Argyll/bin`  
Configurable in Settings dialog. The app shows a statusbar warning if binaries are missing.

### Adding a parameter

1. Add entry to `data/parameters.yaml` with `tool`, `flag`, `type`, `default`, `tooltip_title`, `tooltip_body`.
2. Set `no_space: true` if value must be appended directly to flag (e.g. `-il` not `-i l`).
3. Set `expert_only: true` to hide in collapsed "Expert" section.
4. No code changes needed — `ParameterWidget` picks it up automatically.

### Adding a built-in (non-deletable) Create Chart preset

The Create Chart → Manual **Presets** dropdown can host hard-coded presets that
the user can't delete (e.g. "TC9.18 by Pharmacist", which loads a bundled `.ti1`
and runs printtarg only). The full mechanism, file/function map, gotchas, and a
step-by-step recipe are in **`docs/dev_builtin_presets.md`** — read it before
adding another.
