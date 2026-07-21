"""Capture Measurement Report screenshots for the forum showcase.

Runs the real app ONSCREEN (the report body is a QWebEngineView, which only
composites into a real window), loads the 15-measurement demo set, and grabs the
window region off the screen (a screen grab captures the WebEngine surface that
widget.grab() can miss). Output → ~/Desktop/chromiq_report_shots/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)   # force a real (onscreen) platform
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from scripts.capture_screens import build_app, pump  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from ui.theme import apply_appearance  # noqa: E402

OUT = Path.home() / "Desktop" / "chromiq_report_shots"
DEMO = Path.home() / "Desktop" / "i1Profiler-15-measurements"


def _grab_window(win, path: Path) -> None:
    """Screen-grab the window's on-screen rectangle (captures WebEngine content)."""
    scr = QApplication.primaryScreen()
    fg = win.frameGeometry()
    pm = scr.grabWindow(0, fg.x(), fg.y(), fg.width(), fg.height())
    pm.save(str(path))
    print("  saved", path.name, pm.width(), "x", pm.height())


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = build_app()
    settings = AppSettings()
    apply_appearance(app, None, "light")     # clean light shot for the forum

    from ui.dialogs.measurement_report_dialog import MeasurementReportDialog
    dlg = MeasurementReportDialog(settings)
    dlg.resize(1120, 1480)
    dlg.show(); dlg.raise_(); dlg.activateWindow()
    pump(900)

    files = sorted(DEMO.glob("*.txt"))
    print(f"loading {len(files)} demo measurements…")
    for f in files:
        try:
            dlg._append_source(dlg._as_ti3(f), origin=f)
        except Exception as e:  # noqa: BLE001
            print("  load failed", f.name, e)
    if dlg._sources:
        dlg._report = dlg._sources[0]["runs"][-1]
        dlg._rebuild_from_sources()
    pump(2200)                                # let the WebEngine body + trend paint

    _grab_window(dlg, OUT / "report_full.png")               # hero shot
    dlg._trend_de.grab().save(str(OUT / "report_trend.png"))  # trend chart alone
    print("  saved report_trend.png")

    # Export the PDF, bypassing the save dialog + the auto-open.
    pdf_path = OUT / "measurement_report.pdf"
    QFileDialog.getSaveFileName = staticmethod(  # type: ignore[assignment]
        lambda *a, **k: (str(pdf_path), "PDF (*.pdf)"))
    try:
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl = staticmethod(lambda *a, **k: True)  # type: ignore
        dlg._export_pdf()
        print("  saved", pdf_path.name)
    except Exception as e:  # noqa: BLE001
        print("  pdf export failed:", e)

    print("DONE →", OUT)
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
