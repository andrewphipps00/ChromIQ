"""Working-folder management for ChromIQ sessions."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_ILLEGAL = re.compile(r"[^\w\-.]+", re.UNICODE)
_TRAIL   = re.compile(r"^[._]+|[._]+$")


class FileManager:
    def __init__(self, settings: "AppSettings") -> None:
        self._settings = settings
        self._target_name: str = ""

    # ------------------------------------------------------------------
    # Target name
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitise(name: str) -> str:
        s = name.strip().replace(" ", "-")
        s = _ILLEGAL.sub("_", s)
        s = _TRAIL.sub("", s)
        return s or "session"

    def set_target_name(self, name: str) -> None:
        if not name.strip():
            self._target_name = self._auto_name()
        else:
            self._target_name = self._sanitise(name)
        log.debug("Target name set to: %s", self._target_name)

    def get_target_name(self) -> str:
        if not self._target_name:
            self._target_name = self._auto_name()
        return self._target_name

    @classmethod
    def default_target_name(
        cls,
        printer: str = "Printer",
        paper: str = "Paper",
        papertype: str = "Type",
        instrument: str = "Instr",
    ) -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        parts = [printer, paper, papertype, instrument, ts]
        return "_".join(cls._sanitise(p) for p in parts)

    def _auto_name(self) -> str:
        return self.default_target_name()

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------

    def root_dir(self) -> Path:
        custom = self._settings.get("custom_output_path", "")
        return Path(custom) if custom else Path.home() / "ChromIQ"

    def working_dir(self) -> Path:
        return self.root_dir() / self.get_target_name()

    def ensure_folder(self) -> Path:
        d = self.working_dir()
        d.mkdir(parents=True, exist_ok=True)
        log.debug("Working dir: %s", d)
        return d

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clean_folder(self, extensions: list[str] | None = None) -> None:
        """Delete files with the given extensions (or all) in working_dir."""
        d = self.working_dir()
        if not d.exists():
            return
        exts = {e.lstrip(".").lower() for e in extensions} if extensions else None
        deleted = 0
        for f in d.iterdir():
            if f.is_file():
                if exts is None or f.suffix.lstrip(".").lower() in exts:
                    try:
                        f.unlink()
                        deleted += 1
                    except OSError as exc:
                        log.warning("Could not delete %s: %s", f, exc)
        log.debug("Cleaned %d file(s) from %s", deleted, d)
