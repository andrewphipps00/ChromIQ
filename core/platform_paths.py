"""Centralised platform-conditional paths and URLs.

This module exists so that every place in ChromIQ that needs to ask
"where do logs go on this OS?" / "where is ArgyllCMS installed?" /
"where do ICC profiles live?" delegates to one set of functions
with explicit branches for ``win32``, ``darwin`` and ``linux``.

Stdlib only — must not import anything from ``core.settings`` or
``core.logger`` to avoid circular imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Platform predicates
# ---------------------------------------------------------------------------

def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# ArgyllCMS
# ---------------------------------------------------------------------------

def default_argyll_bin_dir() -> str:
    """Sensible default Argyll bin directory shown in Preferences."""
    if is_windows():
        return r"C:\Program Files\ArgyllCMS\bin"
    if is_macos():
        return "/Applications/Argyll/bin"
    return "/usr/bin"


def argyll_candidate_dirs() -> list[Path]:
    """Ordered list of directories to probe for Argyll binaries.

    Order is preserved across refactor: PATH-discovered candidates are
    handled by the caller, not this function.  This function returns the
    fixed and dynamically-scanned fallback locations only.
    """
    if is_windows():
        local_app = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        candidates: list[Path] = [
            Path(r"C:\Program Files\ArgyllCMS\bin"),
            Path(r"C:\Program Files (x86)\ArgyllCMS\bin"),
            local_app / "ArgyllCMS" / "bin",
            Path.home() / "ArgyllCMS" / "bin",
        ]
        for search_root in (local_app / "ArgyllCMS",
                            Path(r"C:\Program Files\ArgyllCMS")):
            try:
                versioned = sorted(
                    (d for d in search_root.iterdir()
                     if d.is_dir() and "argyll" in d.name.lower()),
                    reverse=True,
                )
                candidates = [d / "bin" for d in versioned] + candidates
            except (PermissionError, OSError, FileNotFoundError):
                pass
        return candidates

    if is_macos():
        candidates = [
            Path("/Applications/Argyll/bin"),
            Path("/Applications/ArgyllCMS/bin"),
            Path("/opt/homebrew/bin"),       # Homebrew (Apple Silicon)
            Path("/usr/local/bin"),          # Homebrew (Intel) / manual
            Path("/opt/local/bin"),          # MacPorts
            Path.home() / "ArgyllCMS/bin",
            Path.home() / "Applications/Argyll/bin",
            Path.home() / ".local/bin",
        ]
        try:
            versioned = sorted(
                (d for d in Path("/Applications").iterdir()
                 if d.is_dir() and "argyll" in d.name.lower()),
                reverse=True,
            )
            candidates = [d / "bin" for d in versioned] + candidates
        except (PermissionError, OSError, FileNotFoundError):
            pass
        return candidates

    # Linux (and any other Unix that isn't macOS)
    return [
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/opt/argyll/bin"),
        Path("/opt/argyllcms/bin"),
        Path.home() / ".local/bin",
        Path.home() / "Argyll/bin",
        Path.home() / "ArgyllCMS/bin",
    ]


def argyll_download_page() -> str:
    """URL of the ArgyllCMS download page for this OS."""
    if is_windows():
        return "https://www.argyllcms.com/downloadwin.html"
    if is_macos():
        return "https://www.argyllcms.com/downloadmac.html"
    return "https://www.argyllcms.com/downloadlinux.html"


# ---------------------------------------------------------------------------
# Filesystem locations
# ---------------------------------------------------------------------------

def log_dir() -> Path:
    """Directory where ChromIQ writes its rotating log file."""
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChromIQ" / "Logs"
    elif is_macos():
        base = Path.home() / "Library" / "Logs" / "ChromIQ"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        root = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
        base = root / "ChromIQ" / "logs"
    return base


def presets_dir() -> Path:
    """Root directory for user-visible manual-tab preset .json files.

    One subfolder per tab lives under this root. Users can browse, copy
    and share preset files with a normal file manager.
    """
    if is_windows():
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "ChromIQ"
    elif is_macos():
        base = Path.home() / "Library" / "Preferences" / "ChromIQ"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".config") / "ChromIQ"
    return base / "presets"


def icc_install_dir() -> Path:
    """Where ``Install Profile`` writes the freshly built .icc."""
    if is_windows():
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return windir / "System32" / "spool" / "drivers" / "color"
    if is_macos():
        return Path.home() / "Library" / "ColorSync" / "Profiles"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return root / "color" / "icc"


def icc_system_dirs() -> list[Path]:
    """All system ICC/ICM profile directories worth scanning.

    The caller filters by existence — paths that don't exist are simply
    skipped.  Order matters for the gamut viewer file dialog default.
    """
    home = Path.home()
    if is_windows():
        win = os.environ.get("WINDIR", r"C:\Windows")
        return [
            Path(r"C:\Windows\System32\spool\drivers\color"),
            Path(win) / "System32" / "spool" / "drivers" / "color",
        ]
    if is_macos():
        return [
            home / "Library" / "ColorSync" / "Profiles",
            Path("/Library/ColorSync/Profiles"),
            Path("/System/Library/ColorSync/Profiles"),
        ]
    # Linux — XDG + colord-managed system dirs
    xdg_data = os.environ.get("XDG_DATA_HOME")
    user_data = Path(xdg_data) if xdg_data else home / ".local" / "share"
    return [
        user_data / "color" / "icc",
        home / ".color" / "icc",
        Path("/usr/share/color/icc"),
        Path("/usr/local/share/color/icc"),
        Path("/var/lib/colord/icc"),
    ]


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def native_print_supported() -> bool:
    """True iff the OS has a native print panel ChromIQ can drive directly.

    Used only for UI affordances (the macOS ``NSPrintPanel`` checkbox in
    Preferences).  Do not conflate with the *default* value of
    ``use_native_print_dialog``, which is True on both macOS and Windows
    (macOS: the native path now reliably disables colour management; Windows:
    the Qt print dialog is the only sensible way to print).
    """
    return is_macos()
