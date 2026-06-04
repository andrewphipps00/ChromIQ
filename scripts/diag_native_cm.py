"""No-ink diagnostic for the macOS native-print colour-management-off path.

Sets up an ``NSPrintInfo`` for a chosen printer and runs ChromIQ's
``_lock_no_color_management`` + ``_apply_session_no_color_management`` exactly as
``workflow.native_print_macos.print_frames`` does — but *stops before*
``NSPrintOperation.runOperation``, so nothing is spooled, no paper/ink is used,
and no print dialog opens.

It prints the new ``[default-intent]`` output-intent profile name(s) and the
colour-management verification result, so you can confirm on the Canon that:

  * the print system resolves the default output intent to the **printer's own
    device profile** (→ identity transform = real colour-off), not sRGB / a
    working space, and
  * ``AP_ColorMatchingMode`` verifies as ``AP_ApplicationColorMatching``.

Usage:
    source .venv/bin/activate
    python scripts/diag_native_cm.py                 # default: Canon PRO-300 series
    python scripts/diag_native_cm.py "EPSON ET-8550" # any NSPrinter display name
    python scripts/diag_native_cm.py --list          # list printer display names
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_PRINTER = "Canon PRO-300 series"


def _setup_logging() -> None:
    """Surface the module's INFO logs (it logs the output-intent profile names)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)


def main(argv: list[str]) -> int:
    import AppKit

    # A shared application is needed before touching NSPrintInfo / NSPrinter.
    AppKit.NSApplication.sharedApplication()

    names = list(AppKit.NSPrinter.printerNames())
    if "--list" in argv:
        print("Printer display names:")
        for n in names:
            print(f"  {n}")
        return 0

    _setup_logging()

    target = next((a for a in argv if not a.startswith("-")), DEFAULT_PRINTER)
    if target not in names:
        print(f"ERROR: printer {target!r} not found. Available: {names}", file=sys.stderr)
        print("Run with --list to see the exact display names.", file=sys.stderr)
        return 2

    printer = AppKit.NSPrinter.printerWithName_(target)
    if printer is None:
        print(f"ERROR: could not create NSPrinter for {target!r}", file=sys.stderr)
        return 2

    print_info = AppKit.NSPrintInfo.sharedPrintInfo().copy()
    print_info.setPrinter_(printer)

    from workflow import native_print_macos as npm

    print(f"\n=== Diagnostic for printer: {target} ===")
    print(f"PRINTCORE_OK={npm._PRINTCORE_OK}  PM_SESSION_OK={npm._PM_SESSION_OK}")
    print(f"locked settings = {npm._locked_settings_for(print_info)}\n")

    # The same two calls print_frames() makes before showing the dialog. We
    # deliberately do NOT show the panel or call NSPrintOperation, so nothing
    # spools.
    print("--- applying lock + session no-colour-management ---")
    npm._lock_no_color_management(print_info)
    npm._apply_session_no_color_management(print_info)

    print("\n--- verifying ---")
    mismatches = npm._verify_color_management(print_info, "diagnostic")

    print("\n=== RESULT ===")
    if mismatches:
        print("FAILED — these keys did not hold the colour-off values:")
        for k, (got, want) in mismatches.items():
            print(f"  {k}: got {got!r}, expected {want!r}")
        return 1
    print("OK — colour-management keys verified OFF (no job was spooled).")
    print("Check the '[default-intent] PMSessionRGBOutputIntent -> ...' line above:")
    print("it should name the Canon's own device profile, not sRGB / a working space.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
