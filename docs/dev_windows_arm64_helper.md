# TASK (for a Claude Code agent on a Windows-on-ARM machine)

**Goal:** build the bit-exact gamut helper (`chromiq-gammap.exe`) **natively for
Windows arm64**, prove it runs, then wire it into CI so arm64 users get bit-exact
gamut mapping instead of the fast-mapper fallback.

This is the one remaining platform gap. macOS (universal2), Linux (x86_64 +
aarch64) and Windows **x64** already build the helper in CI; Windows **arm64**
does not, because no CI runner + toolchain combo was proven for it. An arm64
Windows VM is the only place that can both **build** an arm64 exe and **run** it.
See [[project_bitexact_gammap_helper]] and `native/gammap_helper/CMakeLists.txt`.

## Background you need

- The helper compiles a vendored ArgyllCMS subset (`native/argyll/`) via CMake.
  It is pure numeric/colour C — no GUI, no instrument I/O. The one platform-
  fragile unit (`spectro/conv.c`) is **already excluded** from the build, which
  is what makes it portable.
- The CMake `if(MINGW)` branch static-links the runtime, so a mingw-style
  toolchain yields a **self-contained** exe (deps = only `api-ms-win-crt-*` +
  `KERNEL32`). We use **LLVM-mingw** (clang → `aarch64-w64-mingw32`), which CMake
  treats as MINGW — no MSVC, **no source edits**.
- `native/chromiq-gammap.exe` is git-ignored (built artifact). The `.spec` files
  bundle it only `if os.path.exists(...)` — so producing it is all that's needed
  for PyInstaller to pick it up; its absence is a safe fallback.

## Step 1 — build + verify (one command)

From the repo root:

```powershell
pwsh native/gammap_helper/build_windows_arm64.ps1
```

The script downloads LLVM-mingw (aarch64) + ninja, builds, and checks:
- PE machine type is `IMAGE_FILE_MACHINE_ARM64`,
- imports are only `api-ms-win-crt-*` / `KERNEL32` (self-contained),
- running it with no args prints the `usage:` banner (**proves it executes
  natively on arm64** — the whole point).

CMake must be installed (`winget install Kitware.CMake` if not). If the
LLVM-mingw URL 404s, bump `$llvmVer` to the latest tag at
https://github.com/mstorsjo/llvm-mingw/releases.

**Acceptance for Step 1:** all three checks pass. If the build errors, capture
the failing compile/link line and report it — do **not** start editing vendored
Argyll sources; the fix (if any) belongs in `CMakeLists.txt` flags.

## Step 2 — functional check (if a Python venv is available here)

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
$env:CHROMIQ_GAMMAP = "$(Resolve-Path native\chromiq-gammap.exe)"
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_gammap_helper.py -q
```

Green = the arm64 helper produces correct mappings, not just a running binary.
(Skip if setting up the full env here is impractical — Step 1 is the load-bearing
proof; Step 2 is the bonus only an arm64 box can give.)

## Step 3 — wire arm64 into CI

In `.github/workflows/build-windows.yml`, the helper step is currently gated to
x64 (`if: matrix.arch == 'x64'`, using `choco install mingw`). Add an **arm64**
path that uses LLVM-mingw. Either add a second step guarded by
`if: matrix.arch == 'arm64'`, or generalise the existing one. A working shape:

```yaml
      - name: Build bit-exact gamut helper (arm64, LLVM-mingw)
        if: matrix.arch == 'arm64'
        shell: pwsh
        run: |
          $llvmVer = '20250430'   # bump to latest llvm-mingw release if needed
          $url = "https://github.com/mstorsjo/llvm-mingw/releases/download/$llvmVer/llvm-mingw-$llvmVer-ucrt-aarch64.zip"
          Invoke-WebRequest $url -OutFile llvm.zip; Expand-Archive llvm.zip -DestinationPath tools -Force
          $env:PATH = "$PWD\tools\llvm-mingw-$llvmVer-ucrt-aarch64\bin;$env:PATH"
          choco install ninja -y --no-progress
          cmake -S native/gammap_helper -B build/gammap -G Ninja `
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=aarch64-w64-mingw32-clang
          cmake --build build/gammap
          Copy-Item build/gammap/chromiq-gammap.exe native/chromiq-gammap.exe
```

The `.spec` already bundles the exe when present, so no spec change is needed.
**Verify the exact runner label** (`windows-11-arm` in the matrix) and that
`choco`/`Invoke-WebRequest` work there; adapt if not. Confirm the YAML parses.

## Step 4 — commit + push

Branch: `feature/72-nchannel-patch-data` (do not tag; owner tags from master).
Commit author email must be `itsab1989@users.noreply.github.com`.
End the commit message with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

Then delete the `## Pending platform task` pointer from `CLAUDE.md` and this
file's obsolescence is fine to note in the commit. Report back: PE arch, the
import list, whether the smoke run + pytest passed, and the CI change made.
