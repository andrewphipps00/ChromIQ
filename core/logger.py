"""Centralised logging setup for ChromIQ."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def _log_path() -> Path:
    import sys
    if sys.platform == "win32":
        import os
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChromIQ" / "Logs"
    else:
        base = Path.home() / "Library" / "Logs" / "ChromIQ"
    base.mkdir(parents=True, exist_ok=True)
    return base / "chromiq.log"


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        _log_path(), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
