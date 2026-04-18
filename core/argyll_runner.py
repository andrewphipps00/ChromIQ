"""QProcess wrapper for ArgyllCMS tool execution."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from core.logger import get_logger

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)


class ArgyllRunner(QObject):
    line_received = pyqtSignal(str)
    finished = pyqtSignal(int)   # exit code

    def __init__(self, settings: "AppSettings", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._process: QProcess | None = None
        self._pending_stdin: bytes | None = None
        self._run_on_finish: Callable[[int], None] | None = None
        self._run_on_line:   Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        tool: str,
        args: list[str],
        cwd: Path,
        on_line: Callable[[str], None] | None = None,
        on_finish: Callable[[int], None] | None = None,
    ) -> None:
        if self.is_running:
            log.warning("ArgyllRunner: already running, ignoring run(%s)", tool)
            return

        bin_path = self._resolve(tool)
        log.info("Run: %s %s  [cwd=%s]", bin_path, " ".join(args), cwd)

        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(cwd))
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )

        self._run_on_finish = on_finish
        self._run_on_line   = on_line

        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.finished.connect(self._on_finished)

        if on_line:
            self.line_received.connect(on_line)

        self._process.start(str(bin_path), args)

    def write_stdin(self, text: str) -> None:
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(text.encode())

    def abort(self) -> None:
        if self._process:
            self._process.kill()
            log.info("ArgyllRunner: process killed")

    @property
    def is_running(self) -> bool:
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_ready_read(self) -> None:
        if not self._process:
            return
        raw = self._process.readAllStandardOutput().data()
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            log.debug("[argyll] %s", line)
            self.line_received.emit(line)

    def _on_finished(self, exit_code: int, _exit_status: object) -> None:
        log.info("ArgyllRunner: finished with code %d", exit_code)
        # Capture per-run callback before it can be overwritten by a chained run()
        on_finish = self._run_on_finish
        self._run_on_finish = None
        self._run_on_line   = None
        try:
            self._process.readyReadStandardOutput.disconnect(self._on_ready_read)
            self._process.finished.disconnect(self._on_finished)
        except RuntimeError:
            pass
        try:
            self.line_received.disconnect()
        except (TypeError, RuntimeError):
            pass
        # Emit public signal for any external observers
        self.finished.emit(exit_code)
        # Call per-run callback directly so chained run() calls (targen→printtarg)
        # can register their own on_finish without it being disconnected here
        if on_finish:
            on_finish(exit_code)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, tool: str) -> Path:
        bin_dir = Path(self._settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        candidate = bin_dir / tool
        if not candidate.exists():
            # Try without path — rely on $PATH
            return Path(tool)
        return candidate
