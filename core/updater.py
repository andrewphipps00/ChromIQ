"""Background update checker — polls GitHub releases API, emits Qt signals."""
from __future__ import annotations

import json
import ssl
import threading
import urllib.request
from urllib.error import URLError

import certifi

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger
from core.version import APP_VERSION

log = get_logger(__name__)

_RELEASES_API = "https://api.github.com/repos/itsab1989/ChromIQ/releases/latest"
_RELEASES_PAGE = "https://github.com/itsab1989/ChromIQ/releases"


def _parse_version(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


class UpdateChecker(QObject):
    update_available = pyqtSignal(str)   # latest version tag, e.g. "v1.5.0"
    up_to_date       = pyqtSignal()
    check_failed     = pyqtSignal(str)   # error description

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def check_async(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            req = urllib.request.Request(
                _RELEASES_API,
                headers={"User-Agent": "ChromIQ-update-check"},
            )
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read())
            latest = data.get("tag_name", "")
            if not latest:
                self.check_failed.emit("No release tag found.")
                return
            if _parse_version(latest) > _parse_version(APP_VERSION):
                self.update_available.emit(latest)
            else:
                self.up_to_date.emit()
        except URLError as exc:
            log.debug("Update check failed: %s", exc)
            self.check_failed.emit(str(exc.reason))
        except Exception as exc:
            log.debug("Update check failed: %s", exc)
            self.check_failed.emit(str(exc))
