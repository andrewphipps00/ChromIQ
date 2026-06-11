"""Pop the real Strip-Read-Failed dialog on screen so you can see it live.

Drives the genuine TabMeasure._on_strip_error code path. Close one dialog and
the next appears. Run normally (NOT offscreen):

    python scripts/shot_strip_error.py            # dark theme (default)
    python scripts/shot_strip_error.py light      # light theme
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.capture_screens import build_app, set_theme, pump, show_tab
from core.settings import AppSettings
from ui.theme import apply_appearance
from ui.main_window import MainWindow


def main() -> int:
    theme = sys.argv[1] if len(sys.argv) > 1 else "dark"
    app = build_app()
    settings = AppSettings()
    settings.set("show_welcome_dialog", False)
    apply_appearance(app, None, theme)
    win = MainWindow(settings)
    win.resize(1400, 900)
    win.show()
    set_theme(app, win, theme)
    show_tab(win, "measure")
    pump(400)

    m = win._tab_measure
    # Neutralise the post-exec side effects (key send + watchdog) so dismissing
    # the dialog can't kick off chartread control flow.
    m._manager.send_key = lambda *a, **k: None
    m._manager.send_post_retry_key = lambda *a, **k: None
    m._manager.send_save_partial_and_quit = lambda *a, **k: None
    m._arm_key_watchdog = lambda *a, **k: None

    # Each _on_strip_error call blocks on its own modal exec(); closing one
    # returns here and shows the next.
    m._on_strip_error("communication problem")
    m._on_strip_error("Insufficient delta E")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
