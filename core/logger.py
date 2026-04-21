"""Centralised logging setup for ChromIQ."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def _log_path() -> Path:
    p = Path.home() / "Library" / "Logs" / "ChromIQ"
    p.mkdir(parents=True, exist_ok=True)
    return p / "chromiq.log"


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
