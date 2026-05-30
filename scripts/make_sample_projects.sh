#!/usr/bin/env bash
# Generate nicely-named sample profiling projects for documentation screenshots.
# Output goes to .sample_data/ (gitignored). Requires ArgyllCMS + a reference
# profile to simulate measurements off of.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${ARGYLL_BIN:-/Applications/Argyll/bin}"
REF_SRC="${BIN%/bin}/ref/ClayRGB1998.icm"
# Generate INSIDE the ChromIQ working folder so the app treats them as native
# projects — this avoids the modal "Copy Chart Files" relocation prompt that
# fires when a chart is loaded from outside the working tree (and which would
# freeze the headless capture run).
OUT="${CHROMIQ_HOME:-$HOME/ChromIQ}"
mkdir -p "$OUT"

make_project() {
  local stem="$1" patches="$2" simsrc="$3"
  local dir="$OUT/$stem"
  mkdir -p "$dir"; cd "$dir"
  echo "== $stem =="
  "$BIN/targen"   -v -d2 -G -e4 -f"$patches" "$stem"            >/dev/null 2>&1
  "$BIN/printtarg" -v -ii1 -pA4 -t300 -L "$stem"               >/dev/null 2>&1
  "$BIN/fakeread"  -v "$simsrc" "$stem"                         >/dev/null 2>&1
  "$BIN/colprof"   -v -qh -S "$REF_SRC" -cmt -dpp "$stem"       >/dev/null 2>&1
  # Drop a project manifest so ChromIQ treats this folder as a native project
  # and never shows the modal "Copy Chart Files" relocation prompt.
  printf '{"schema_version": 1, "current_run": "run1", "runs": ["run1"]}\n' > "$dir/project.json"
  echo "   -> $(ls "$stem".ti1 "$stem".ti2 "$stem".ti3 "$stem".icc "$stem"*.tif 2>/dev/null | wc -l | tr -d ' ') files + project.json"
}

# Primary project (simulated off a real ChromIQ measurement profile if present,
# else off the reference itself).
SIM="$HOME/ChromIQ/test/test.icc"
[ -f "$SIM" ] || SIM="$REF_SRC"
make_project "Epson-P700_PhotoRag_A4_i1Pro" 420 "$SIM"
# Comparison project for the gamut-compare screenshot.
make_project "Canon-PRO1000_Glossy_A4_i1Pro" 300 "$REF_SRC"

echo "Sample data ready in $OUT"
