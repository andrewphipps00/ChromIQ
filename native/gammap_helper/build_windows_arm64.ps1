<#
  build_windows_arm64.ps1 — build + smoke-test the bit-exact gamut helper
  (chromiq-gammap.exe) NATIVELY on Windows-on-ARM (arm64).

  Why this script exists: the mac CI builds a universal2 helper, the x64
  Windows CI builds one with mingw-w64, and Linux CI builds one with gcc — but
  Windows-ARM64 has no helper yet, so bit-exact +N gamut mapping falls back to
  the fast Python mapper there. This closes that gap. An arm64 VM is the only
  place that can both BUILD an arm64 exe and RUN it to prove it works.

  Toolchain: LLVM-mingw (clang targeting aarch64-w64-mingw32). It's a drop-in
  mingw-style toolchain, so the existing CMakeLists `if(MINGW)` path (which
  static-links the runtime) applies unchanged — no MSVC, no code edits.

  Run from the repo root:  pwsh native/gammap_helper/build_windows_arm64.ps1
  Idempotent: re-running reuses the downloaded toolchain.
#>

$ErrorActionPreference = 'Stop'
$repo  = (Resolve-Path "$PSScriptRoot\..\..").Path
$tools = Join-Path $repo '.tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null

# --- 1. LLVM-mingw (aarch64 host + target) -----------------------------------
# If a newer release exists and this URL 404s, bump $llvmVer to the latest tag
# from https://github.com/mstorsjo/llvm-mingw/releases (any recent one works).
$llvmVer = '20250430'
$llvmDir = Join-Path $tools "llvm-mingw-$llvmVer-ucrt-aarch64"
if (-not (Test-Path (Join-Path $llvmDir 'bin\aarch64-w64-mingw32-clang.cmd')) -and
    -not (Test-Path (Join-Path $llvmDir 'bin\aarch64-w64-mingw32-clang.exe'))) {
  $zip = Join-Path $tools "llvm-mingw-$llvmVer.zip"
  $url = "https://github.com/mstorsjo/llvm-mingw/releases/download/$llvmVer/llvm-mingw-$llvmVer-ucrt-aarch64.zip"
  Write-Host "Downloading LLVM-mingw $llvmVer (aarch64)…"
  Invoke-WebRequest -Uri $url -OutFile $zip
  Write-Host "Extracting…"
  Expand-Archive -Path $zip -DestinationPath $tools -Force
}
$env:PATH = (Join-Path $llvmDir 'bin') + [IO.Path]::PathSeparator + $env:PATH
Write-Host "clang: $(& aarch64-w64-mingw32-clang --version | Select-Object -First 1)"

# --- 2. Ninja (native arm64) -------------------------------------------------
if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) {
  $ninjaDir = Join-Path $tools 'ninja'
  if (-not (Test-Path (Join-Path $ninjaDir 'ninja.exe'))) {
    $nzip = Join-Path $tools 'ninja.zip'
    # ninja ships a native winarm64 build since v1.12.
    Invoke-WebRequest -Uri 'https://github.com/ninja-build/ninja/releases/latest/download/ninja-winarm64.zip' -OutFile $nzip
    Expand-Archive -Path $nzip -DestinationPath $ninjaDir -Force
  }
  $env:PATH = $ninjaDir + [IO.Path]::PathSeparator + $env:PATH
}
Write-Host "ninja: $(& ninja --version)"

# CMake is assumed present (winget install Kitware.CMake if not).
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
  throw "cmake not found — install it (e.g. `winget install Kitware.CMake`) and re-run."
}

# --- 3. Configure + build ----------------------------------------------------
$build = Join-Path $repo 'build\gammap-arm64'
cmake -S (Join-Path $repo 'native\gammap_helper') -B $build -G Ninja `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER=aarch64-w64-mingw32-clang
cmake --build $build
$exe = Join-Path $build 'chromiq-gammap.exe'
if (-not (Test-Path $exe)) { throw "build produced no exe at $exe" }

# --- 4. Verify it is an ARM64 PE + is self-contained -------------------------
$readobj = Join-Path $llvmDir 'bin\llvm-readobj.exe'
Write-Host "`n--- PE machine type (want IMAGE_FILE_MACHINE_ARM64) ---"
& $readobj --file-headers $exe | Select-String 'Machine'
Write-Host "`n--- DLL dependencies (want only api-ms-win-crt-* / KERNEL32) ---"
& $readobj --coff-imports $exe | Select-String 'Name:' | Sort-Object -Unique

# --- 5. Smoke-run natively (proves the arm64 binary loads + executes) --------
Write-Host "`n--- smoke run (no args -> should print usage, exit non-zero) ---"
$out = & $exe 2>&1
Write-Host $out
if ($out -match 'usage:') { Write-Host "`nSMOKE TEST PASSED — arm64 helper runs natively." }
else { throw "smoke test FAILED — no usage banner; binary may not have executed." }

# --- 6. Stage for bundling ---------------------------------------------------
Copy-Item $exe (Join-Path $repo 'native\chromiq-gammap.exe') -Force
Write-Host "`nStaged native\chromiq-gammap.exe (git-ignored; PyInstaller bundles it if you build the app here)."
Write-Host "Next: functional check with the Python suite if a venv exists —"
Write-Host "  `$env:CHROMIQ_GAMMAP='$repo\native\chromiq-gammap.exe'; pytest tests/test_gammap_helper.py"
