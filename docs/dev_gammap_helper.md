# The bit-exact gamut-mapping helper (`chromiq-gammap`)

ChromIQ's profile engine can build the perceptual/saturation **B2A** tables two
ways, chosen in Settings → *Gamut mapping*:

- **Fast (built-in)** — the pure-Python port of Argyll's gamut mapper
  (`workflow/profile_engine/gammap_port/`). No external binary; a few seconds.
- **Bit-exact (Argyll's engine)** — a small native helper, `chromiq-gammap`,
  that runs ArgyllCMS's **actual** gamut-mapping code (`new_gammap` / `domap`).
  A literal match to Argyll, including for **CMY+N** devices that colprof's ICC
  ink separation refuses (`"Output device input file has unhandled color
  representation 'CMYKOG_XYZ'"`). A little slower.

Both handle RGB, CMYK and 6+ ink. If the helper binary isn't bundled, the engine
silently falls back to the fast mapper — a build never fails for a missing
helper.

## Why a helper (and the AGPL boundary)

Argyll's gamut mapper is an internal library, not a CLI tool, and colprof — the
only stock tool that writes device B2A tables — refuses >4 inks before the
mapper ever runs. So the only way to reach Argyll's real gamut mapper for CMY+N
is to compile it ourselves.

ArgyllCMS is **AGPL-3.0**. The helper is therefore a *separate program*, invoked
arm's-length via `subprocess` exactly like colprof/iccgamut, and it plus its
vendored Argyll sources are AGPL (see `native/argyll/LICENSE` and
`native/argyll/PROVENANCE.md`). ChromIQ's Python application stays GPLv3. The
AGPL source-availability obligation is met by the vendored sources living in the
public repository.

## Layout

```
native/
  argyll/            vendored, unmodified ArgyllCMS 3.5.0 subset (AGPL-3.0)
    h/ numlib/ icc/ cgats/ rspl/ gamut/ xicc/ spectro/ plot/
    LICENSE  PROVENANCE.md
  gammap_helper/
    gammap_helper.c  the helper (CLI over Argyll's new_gammap/domap)
    CMakeLists.txt   self-contained build (no Jam)
```

The vendored subset is exactly the 45 translation units named by the upstream
Jamfile library lists (`libnum`, `libicc`, `libcgats`, `librspl`, `libgamut` +
`libgammap`, `libxicc`, plus `spectro/conv` and `plot/vrml`) and their header
closure. `xutils` (libtiff) and `iccjpeg` (libjpeg) are intentionally omitted —
unreferenced by the gamut-mapping path. How the list was recovered from the
Jamfiles and verified byte-identical to the original proof is recorded in
`workflow/profile_engine/gammap_port/portmap.md`.

## Building

```bash
cmake -S native/gammap_helper -B build/gammap -DCMAKE_BUILD_TYPE=Release
cmake --build build/gammap -j
# → build/gammap/chromiq-gammap
```

macOS universal2 (what CI ships): add
`-DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"`. The CMake builds the Argyll units as
a **static library** and links the helper against it, so the final link pulls
only referenced objects (the omitted-but-vendored `ccmx`/`ccss`/`mpp`/`conv`
units, which carry externals to instrument code we don't vendor, are simply
never pulled). Per-platform legs: mac (CoreFoundation/Foundation/IOKit/
CoreServices + objc), linux (pthread/rt/dl), windows (winmm, `NT`).

In CI (`.github/workflows/build-release.yml`) the helper is built before
PyInstaller and copied to `native/chromiq-gammap`; `ChromIQ.spec` bundles it
under `native/` when present, and the ad-hoc codesign pass signs it inside the
`.app`. The compiled binary is git-ignored.

## CLI contract

```
chromiq-gammap --src SRC.gam
               (--dst-gam DST.gam | --dst-cloud CLOUD.txt)
               --wp L a b --bp L a b
               --intent p|s --mapres N
               --query QUERY.txt --out OUT.txt
```

Everything is CIECAM02 **Jab** (the appearance space colprof maps in).
`--dst-gam` is used for ≤4-ink devices — the iccgamut `.gam` colprof itself
would feed the mapper (byte-identical). `--dst-cloud` is the CMY+N route: the
destination shell is built from a boundary point cloud via Argyll's own
`gamut->expand` (hand-built meshes fail `vector_isect`). Intent parameters come
from `xicc_enum_gmapintent(icxPerceptual/icxSaturationGMIntent)` — the exact
call colprof makes. `--mapres` and the iccgamut `-d` detail follow colprof's
per-quality table (`{u:(7,49), h:(8,39), m:(10,29), l:(12,19)}`, see
`profout.c`).

## Python integration

- `workflow/profile_engine/gammap_helper.py` — locates the binary
  (`resource_path("native/…")`, or `$CHROMIQ_GAMMAP` for dev), runs it, parses
  the mapped lattice. `HelperUnavailable` on any failure.
- `workflow/profile_engine/gammap_port/wire.py` — `fit_gammap_argyll_mappers`
  builds the source/dest gamuts and returns `ArgyllHelperMapper` objects for
  B2A0 (`p`) and B2A2 (`s`). `fit_gammap_port_mappers` routes here when
  `settings.gammap_mode == "argyll"` and falls through to the fast mapper on
  `HelperUnavailable`.
- Setting: `gammap_mode` (`"fast"` | `"argyll"`, default `"fast"`); schema-6
  migration retires the old `gammap_exact_geometry` boolean.

For local end-to-end testing, point `$CHROMIQ_GAMMAP` at a freshly built binary;
`tests/test_gammap_helper.py` then also exercises the real round-trip.

## Bumping Argyll

Re-copy the same file list from the new Argyll release into `native/argyll/`,
rebuild, and confirm the helper still maps a known query identically (the
byte-identical check in `portmap.md`). No source edits are made to the vendored
tree.
