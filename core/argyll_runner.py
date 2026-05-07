"""QProcess wrapper for ArgyllCMS tool execution."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from core.logger import get_logger
from core.resource_path import argyll_binary

if sys.platform != "win32":
    import pty
    import select

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;]*[A-Za-z]|\][^\x07]*\x07|[()][AB012]|[=>])")


class ArgyllRunner(QObject):
    line_received = pyqtSignal(str)
    finished      = pyqtSignal(int)   # exit code
    _pty_done     = pyqtSignal(int)   # internal: PTY reader → main thread

    def __init__(self, settings: "AppSettings", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._process: QProcess | None = None
        self._pending_stdin: bytes | None = None
        self._run_on_finish: Callable[[int], None] | None = None
        self._run_on_line:   Callable[[str], None] | None = None

        # PTY mode state
        self._pty_proc:   subprocess.Popen | None = None
        self._pty_master: int | None = None
        self._pty_thread: threading.Thread | None = None
        self._pty_done.connect(self._on_pty_finished)

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
        use_pty: bool = False,
    ) -> None:
        if self.is_running:
            log.warning("ArgyllRunner: already running, ignoring run(%s)", tool)
            return

        if use_pty:
            self._run_pty(tool, args, cwd, on_line, on_finish)
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
        if self._pty_master is not None:
            try:
                os.write(self._pty_master, text.encode())
            except OSError:
                pass
        elif self._pty_proc is not None and self._pty_proc.stdin:
            try:
                self._pty_proc.stdin.write(text.encode())
                self._pty_proc.stdin.flush()
            except OSError:
                pass
        elif self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(text.encode())

    def abort(self) -> None:
        if self._pty_proc is not None:
            self._pty_proc.kill()
            log.info("ArgyllRunner: PTY process killed")
        elif self._process:
            self._process.kill()
            log.info("ArgyllRunner: process killed")

    def cleanup(self) -> None:
        """Kill any running process and join the PTY thread before app shutdown.

        Must be called from closeEvent before Qt starts destroying objects,
        otherwise the daemon PTY thread can emit signals into already-freed
        C++ objects and cause a segfault (macOS 'quit unexpectedly' dialog).
        """
        # Disconnect all signals so no callbacks fire during teardown.
        for sig in (self.line_received, self.finished, self._pty_done):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass

        # Kill subprocess(es).
        if self._pty_proc is not None and self._pty_proc.poll() is None:
            self._pty_proc.kill()
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(2000)

        # Close the PTY master fd so the reader thread unblocks immediately.
        if self._pty_master is not None:
            try:
                os.close(self._pty_master)
            except OSError:
                pass
            self._pty_master = None

        # Wait for the reader thread to exit so it cannot emit after we return.
        if self._pty_thread is not None and self._pty_thread.is_alive():
            self._pty_thread.join(timeout=2.0)
            self._pty_thread = None

        log.info("ArgyllRunner: cleanup complete")

    @property
    def is_running(self) -> bool:
        if self._pty_proc is not None and self._pty_proc.poll() is None:
            return True
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    # ------------------------------------------------------------------
    # PTY mode (macOS/Linux) / pipe mode (Windows)
    # ------------------------------------------------------------------

    def _run_pty(
        self,
        tool: str,
        args: list[str],
        cwd: Path,
        on_line: Callable[[str], None] | None,
        on_finish: Callable[[int], None] | None,
    ) -> None:
        bin_path = self._resolve(tool)

        if sys.platform == "win32":
            self._run_pipe(bin_path, args, cwd, on_line, on_finish)
            return

        log.info("Run (PTY): %s %s  [cwd=%s]", bin_path, " ".join(args), cwd)
        master_fd, slave_fd = pty.openpty()
        self._pty_proc = subprocess.Popen(
            [str(bin_path)] + args,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=str(cwd),
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        self._pty_master = master_fd

        self._run_on_finish = on_finish
        self._run_on_line   = on_line
        if on_line:
            self.line_received.connect(on_line)

        self._pty_thread = threading.Thread(
            target=self._pty_reader, args=(master_fd,), daemon=True
        )
        self._pty_thread.start()

    def _run_pipe(
        self,
        bin_path: Path,
        args: list[str],
        cwd: Path,
        on_line: Callable[[str], None] | None,
        on_finish: Callable[[int], None] | None,
    ) -> None:
        log.info("Run (pipe): %s %s  [cwd=%s]", bin_path, " ".join(args), cwd)
        self._pty_proc = subprocess.Popen(
            [str(bin_path)] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            creationflags=_CREATE_NO_WINDOW,
        )
        self._pty_master = None

        self._run_on_finish = on_finish
        self._run_on_line   = on_line
        if on_line:
            self.line_received.connect(on_line)

        self._pty_thread = threading.Thread(
            target=self._pipe_reader, daemon=True
        )
        self._pty_thread.start()

    def _pty_reader(self, master_fd: int) -> None:
        buf = b""
        FLUSH_AFTER = 0.15   # emit partial prompt lines after this silence

        # Throttle repeated identical lines so a runaway process (e.g. USB
        # error loop) cannot flood the Qt event queue and freeze the UI.
        _last_line  = ""
        _repeat_cnt = 0
        _MAX_REPEAT = 4  # show a line up to this many times, then suppress

        def _emit(line: str) -> None:
            nonlocal _last_line, _repeat_cnt
            if line == _last_line:
                _repeat_cnt += 1
                if _repeat_cnt == _MAX_REPEAT:
                    self.line_received.emit("[…repeated output suppressed]")
                if _repeat_cnt >= _MAX_REPEAT:
                    return
            else:
                _last_line  = line
                _repeat_cnt = 0
            log.debug("[argyll-pty] %s", line)
            self.line_received.emit(line)

        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], FLUSH_AFTER)
            except (OSError, ValueError):
                break

            if r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\r")
                    if line:
                        _emit(line)
            else:
                # Silence window — flush any partial prompt
                if buf:
                    line = _ANSI_RE.sub("", buf.decode("utf-8", errors="replace")).rstrip("\r")
                    buf = b""
                    if line:
                        _emit(line)

            if self._pty_proc and self._pty_proc.poll() is not None:
                break

        # Flush remainder
        if buf:
            line = _ANSI_RE.sub("", buf.decode("utf-8", errors="replace")).rstrip("\r")
            if line:
                self.line_received.emit(line)

        code = 0
        if self._pty_proc:
            try:
                code = self._pty_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._pty_proc.kill()
                code = self._pty_proc.wait()

        try:
            os.close(master_fd)
        except OSError:
            pass

        self._pty_done.emit(code)

    def _pipe_reader(self) -> None:
        """Read from subprocess stdout pipe (Windows fallback for PTY).

        A helper thread feeds bytes into a queue so the main loop can apply
        the same FLUSH_AFTER silence-window logic as the PTY reader, making
        interactive ArgyllCMS prompts (no trailing newline) visible promptly.
        """
        FLUSH_AFTER = 0.15

        proc = self._pty_proc
        if proc is None or proc.stdout is None:
            self._pty_done.emit(0)
            return

        _last_line  = ""
        _repeat_cnt = 0
        _MAX_REPEAT = 4

        def _emit(line: str) -> None:
            nonlocal _last_line, _repeat_cnt
            if line == _last_line:
                _repeat_cnt += 1
                if _repeat_cnt == _MAX_REPEAT:
                    self.line_received.emit("[…repeated output suppressed]")
                if _repeat_cnt >= _MAX_REPEAT:
                    return
            else:
                _last_line  = line
                _repeat_cnt = 0
            log.debug("[argyll-pipe] %s", line)
            self.line_received.emit(line)

        byte_q: queue.Queue[bytes | None] = queue.Queue()

        def _raw_reader() -> None:
            try:
                while True:
                    b = proc.stdout.read(1)
                    byte_q.put(b if b else None)
                    if not b:
                        break
            except OSError:
                byte_q.put(None)

        threading.Thread(target=_raw_reader, daemon=True).start()

        buf = b""
        while True:
            try:
                byte = byte_q.get(timeout=FLUSH_AFTER)
            except queue.Empty:
                if buf:
                    line = _ANSI_RE.sub(
                        "", buf.decode("utf-8", errors="replace")
                    ).rstrip("\r")
                    buf = b""
                    if line:
                        _emit(line)
                continue

            if byte is None:
                break

            buf += byte
            if byte == b"\n":
                raw = buf.rstrip(b"\r\n")
                buf = b""
                line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))
                if line:
                    _emit(line)

        if buf:
            line = _ANSI_RE.sub(
                "", buf.decode("utf-8", errors="replace")
            ).rstrip("\r")
            if line:
                self.line_received.emit(line)

        code = 0
        if proc:
            try:
                code = proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()

        self._pty_done.emit(code)

    def _on_pty_finished(self, code: int) -> None:
        self._pty_master = None
        self._pty_proc   = None
        on_finish = self._run_on_finish
        self._run_on_finish = None
        self._run_on_line   = None
        try:
            self.line_received.disconnect()
        except (TypeError, RuntimeError):
            pass
        log.info("ArgyllRunner (PTY): finished with code %d", code)
        self.finished.emit(code)
        if on_finish:
            on_finish(code)

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
        # Drain any output still buffered in QProcess before disconnecting.
        # Qt does not guarantee all readyReadStandardOutput events arrive before
        # finished(), so the last chunk of output (e.g. profcheck per-patch lines)
        # can be silently lost without this flush.
        if self._process:
            remaining = self._process.readAllStandardOutput().data()
            if remaining:
                text = remaining.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    log.debug("[argyll] %s", line)
                    self.line_received.emit(line)

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
        candidate = bin_dir / argyll_binary(tool)
        if not candidate.exists():
            return Path(argyll_binary(tool))
        return candidate
