"""Make freetype-py find its native library on Windows/ARM64 (#72).

freetype-py ships prebuilt wheels with a bundled ``libfreetype.dll`` for Windows
x64, macOS and Linux — but **not** for Windows on ARM. There, pip installs the
sdist with no native library, so ``import freetype`` raises
``RuntimeError('Freetype library not found')`` and the vector-PDF export is
silently unavailable (it's imported lazily, only when "Also export a PDF" is on).

To close that gap we vendor a self-contained ARM64 build of FreeType under
``vendor/freetype/win-arm64/freetype.dll`` (FreeType 2.14.3, from
ubawurinna/freetype-windows-binaries — statically linked, needs only the system
VC++ runtime). On that platform, and only there, we add its directory to the DLL
search path so freetype-py's ``ctypes.util.find_library('freetype')`` resolves
it. Everywhere else this is a no-op — the wheel's own bundled library is used,
untouched.

Call :func:`ensure_freetype_library` once, early, **before** anything imports
``freetype`` (from ``main.py`` for the app and ``tests/conftest.py`` for the
suite). Idempotent and never raises.
"""
from __future__ import annotations

import os
import platform
import sys

_done = False


def ensure_freetype_library() -> None:
    global _done
    if _done:
        return
    _done = True
    if sys.platform != "win32" or platform.machine().upper() not in ("ARM64", "AARCH64"):
        return
    try:
        from core.resource_path import resource_path
        d = str(resource_path("vendor/freetype/win-arm64"))
        if not os.path.isfile(os.path.join(d, "freetype.dll")):
            return
        try:
            os.add_dll_directory(d)          # resolve the DLL + any siblings
        except (OSError, AttributeError):
            pass
        # freetype-py resolves via ctypes.util.find_library('freetype'), which
        # appends .dll and searches PATH — so put our directory on it.
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        # Never let a missing/odd vendor layout stop the app from starting; the
        # only cost is that PDF export stays unavailable, as before.
        pass
