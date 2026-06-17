#!/usr/bin/env bash
# Run the ChromIQ test suite robustly.
#
# Why this exists: the tests embed QWebEngineView (gamut viewer, patch cube,
# soft-proof). When a run is killed mid-flight (Ctrl-C, or back-to-back
# kill-and-rerun cycles), it can orphan a `QtWebEngineProcess` helper. A fresh
# pytest then wedges at startup waiting on that orphan — the "tests hang for
# minutes" symptom. Clearing the orphans first prevents it; pytest.ini's
# faulthandler_timeout dumps a traceback if a genuine test hangs.
#
# Usage:  ./scripts/run_tests.sh [pytest args]      e.g. -k softproof
set -u
cd "$(dirname "$0")/.."

pkill -9 -f "bin/pytest"        2>/dev/null || true
pkill -9 -f "QtWebEngineProcess" 2>/dev/null || true
sleep 1

# shellcheck disable=SC1091
source .venv/bin/activate
exec env QT_QPA_PLATFORM=offscreen pytest -p no:cacheprovider "$@"
