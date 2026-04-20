"""Auto-detect the ArgyllCMS binary directory on macOS."""
from __future__ import annotations

from pathlib import Path
from shutil import which

from core.logger import get_logger

log = get_logger(__name__)

_REQUIRED = ("targen", "printtarg", "chartread", "colprof")


def all_tools_present(bin_dir: Path) -> bool:
    return all((bin_dir / t).exists() for t in _REQUIRED)


def find_argyll_bin_path() -> Path | None:
    """Return the first directory that contains all required ArgyllCMS tools, or None."""

    # 1. Check the system PATH first — covers Homebrew, MacPorts, manual installs
    for tool in _REQUIRED:
        found = which(tool)
        if found:
            candidate = Path(found).parent
            if all_tools_present(candidate):
                log.info("ArgyllCMS found in PATH at %s", candidate)
                return candidate

    # 2. Fixed well-known locations
    candidates: list[Path] = [
        Path("/Applications/Argyll/bin"),
        Path("/Applications/ArgyllCMS/bin"),
        Path("/opt/homebrew/bin"),       # Homebrew (Apple Silicon)
        Path("/usr/local/bin"),          # Homebrew (Intel) / manual
        Path("/opt/local/bin"),          # MacPorts
        Path.home() / "ArgyllCMS/bin",
        Path.home() / "Applications/Argyll/bin",
        Path.home() / ".local/bin",
    ]

    # 3. Scan /Applications for versioned Argyll directories (e.g. Argyll_V3.5.0)
    try:
        versioned = sorted(
            (d for d in Path("/Applications").iterdir()
             if d.is_dir() and "argyll" in d.name.lower()),
            reverse=True,  # prefer the highest version number
        )
        candidates = [d / "bin" for d in versioned] + candidates
    except (PermissionError, OSError):
        pass

    for candidate in candidates:
        if all_tools_present(candidate):
            log.info("ArgyllCMS auto-detected at %s", candidate)
            return candidate

    log.warning("ArgyllCMS binaries not found in any known location")
    return None
