"""Orchestrates chartread for interactive measurement."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger
from core.strip_utils import letter_to_idx

if TYPE_CHECKING:
    from core.argyll_runner import ArgyllRunner

log = get_logger(__name__)

# Regex to detect which strip chartread is currently asking for.
# Handles formats:
#   "Ready to read strip pass A"   (Argyll 3.x default)
#   "Scanning strip 'A01'"
#   "Strip ID: B"
_STRIP_RE = re.compile(
    r"[Ss]trip\s+(?:pass\s+|ID:\s*'?|'?)([A-Za-z]{1,3}\d*)(?:')?(?![A-Za-z0-9])"
)

_ALL_DONE_RE           = re.compile(r"ALL\s+ROWS\s+READ",                        re.IGNORECASE)
_CALIBRATION_RE        = re.compile(r"Calibration\s+complete",                   re.IGNORECASE)
_CALIBRATION_PROMPT_RE = re.compile(r"Set\s+instrument\s+sensor\s+to\s+calibration\s+position", re.IGNORECASE)
_STRIP_ERROR_RE        = re.compile(r"Strip\s+read\s+failed[^(]*\(([^)]+)\)",   re.IGNORECASE)
_USB_ERROR_RE          = re.compile(r"ReadPipeAsync\s+failed",                   re.IGNORECASE)
_DEVICE_BUSY_RE        = re.compile(r"Device being used",                        re.IGNORECASE)
_NO_INSTRUMENT_RE      = re.compile(r"no instrument detected|no suitable instruments|no instruments connected", re.IGNORECASE)
_WRONG_STRIP_RE        = re.compile(r"Seem to have read strip pass (\w+) rather than (\w+)", re.IGNORECASE)
_UNEXPECTED_RESP_RE    = re.compile(r"unexpected response.*\(DeltaE\s*([\d.]+)\)",            re.IGNORECASE)
_STRIP_OK_RE           = re.compile(r"strip\s+read\s+ok",                                    re.IGNORECASE)
_SENSOR_POSITION_RE    = re.compile(r"sensor.*wrong\s+position|sensor should be in surface", re.IGNORECASE)
_USB_VM_RE             = re.compile(r"Failed to get piif for USB device",                    re.IGNORECASE)
# chartread asks this when 'd' (done) is pressed with unread patches remaining;
# answering 'y' writes the partial .ti3, 'n' returns to the strip menu.
_ARE_YOU_SURE_RE       = re.compile(r"Are\s+you\s+sure\s+\[y/n\]",                          re.IGNORECASE)


@dataclass
class MeasureParams:
    ti1_path: Path
    instrument: str = "1"
    disable_bidir: bool = False
    suppress_warnings: bool = True
    disable_initial_cal: bool = False
    patch_by_patch: bool = False
    high_res: bool = False
    resume: bool = False
    extra_args: str = ""


class MeasureManager(QObject):
    stripe_changed         = pyqtSignal(str)  # emits strip ID string e.g. "A01"
    all_stripes_done       = pyqtSignal()    # emitted when chartread reports all rows read
    calibration_prompt     = pyqtSignal()    # emitted when chartread asks user to position instrument
    calibration_done       = pyqtSignal()    # emitted when instrument calibration completes
    strip_error            = pyqtSignal(str) # emitted on strip read failure; carries the reason string
    instrument_disconnected = pyqtSignal()   # emitted on USB communication failure
    device_busy             = pyqtSignal()   # emitted when instrument is held by another process
    no_instrument           = pyqtSignal()     # emitted when no instrument is detected at startup
    wrong_strip             = pyqtSignal(str, str)  # (read_strip, expected_strip)
    unexpected_response     = pyqtSignal(str)       # carries the DeltaE value string
    sensor_wrong_position   = pyqtSignal()          # emitted when instrument is in calibration position during scan
    usb_claimed_by_vm       = pyqtSignal()          # emitted when USB device is held exclusively by a VM

    def __init__(self, runner: "ArgyllRunner", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner         = runner
        self._is_resume:     bool = False
        self._guided_strips: list[str] = []
        self._guided_idx:    int  = 0
        self._guided_state:  str  = "idle"   # "idle" | "navigating" | "waiting"
        self._guided_on_line: "Callable[[str], None] | None" = None
        # Queued key dispatched once chartread returns to the strip menu after
        # a misread retry — see send_post_retry_key().
        self._pending_post_retry_key: str | None = None
        # Two-step state for "Save Partial & Quit" from the misread dialog:
        #   None             — idle
        #   "wait_strip_menu" — waiting for the strip-menu prompt to send 'd'
        #   "wait_sure"       — waiting for "Are you sure [y/n]" to send 'y'
        self._save_partial_state: str | None = None

    # ------------------------------------------------------------------

    def start(
        self,
        params: MeasureParams,
        on_line: Callable[[str], None],
        on_finish: Callable[[int], None],
    ) -> None:
        args = self._build_args(params)
        cwd  = params.ti1_path.parent
        log.info("chartread: %s  [cwd=%s]", " ".join(args), cwd)
        self._is_resume      = params.resume
        self._guided_on_line = on_line
        # Reset guided state for this run
        self._guided_idx   = 0
        self._guided_state = "idle" if self._guided_strips else "disabled"

        def _on_finish(code: int) -> None:
            self._pending_post_retry_key = None
            self._save_partial_state = None
            on_finish(code)

        self._runner.run(
            "chartread",
            args,
            cwd,
            on_line=lambda line: self._handle_line(line, on_line),
            on_finish=_on_finish,
            use_pty=True,
        )

    def set_guided_strips(self, strips: list[str]) -> None:
        """Configure strips to auto-navigate during the next measurement run."""
        self._guided_strips = list(strips)
        self._guided_idx    = 0
        self._guided_state  = "idle" if strips else "disabled"

    def send_key(self, key: str) -> None:
        """Send a keystroke to the running chartread process."""
        self._runner.write_stdin(key)

    def send_post_retry_key(self, key: str) -> None:
        """Acknowledge a misread (any-key = retry) and queue ``key`` for the
        strip menu that chartread shows next. Needed because the misread
        prompt only accepts retry or Esc — f/b/n/d are accepted only at the
        subsequent "Press 'f' to move forward…" prompt."""
        self._pending_post_retry_key = key
        self._runner.write_stdin("\r")

    def send_save_partial_and_quit(self) -> None:
        """Save what's been scanned so far and exit chartread cleanly.

        chartread only writes the .ti3 on 'd' (done). With unread patches it
        first prompts "Are you sure [y/n]" — we answer 'y' automatically.
        The full chain from the misread prompt is: any-key → strip-menu → 'd'
        → ("Are you sure" → 'y') → exit. Esc/q at any of these prompts would
        discard the readings, which is why the misread dialog no longer
        offers a destructive path."""
        self._save_partial_state = "wait_strip_menu"
        self._runner.write_stdin("\r")

    def abort(self) -> None:
        self._runner.abort()

    # ------------------------------------------------------------------

    def _build_args(self, p: MeasureParams) -> list[str]:
        args: list[str] = ["-c", p.instrument]
        if p.disable_bidir:
            args.append("-B")
        if p.suppress_warnings:
            args.append("-S")
        if p.disable_initial_cal:
            args.append("-N")
        if p.patch_by_patch:
            args.append("-p")
        if p.high_res:
            args.append("-H")
        if p.resume:
            args.append("-r")
        if p.extra_args:
            args += shlex.split(p.extra_args)
        # Base name without extension
        args.append(str(p.ti1_path.with_suffix("")))
        return args

    def _handle_line(self, line: str, on_line: Callable[[str], None]) -> None:
        on_line(line)
        matches = _STRIP_RE.findall(line)
        if matches:
            current = matches[-1]
            self.stripe_changed.emit(current)
            if self._save_partial_state == "wait_strip_menu":
                self._save_partial_state = "wait_sure"
                self._runner.write_stdin("d")
            elif self._pending_post_retry_key is not None:
                key = self._pending_post_retry_key
                self._pending_post_retry_key = None
                self._runner.write_stdin(key)
            elif self._guided_state not in ("idle_done", "disabled"):
                self._guided_step(current, on_line)
        if _ARE_YOU_SURE_RE.search(line) and self._save_partial_state == "wait_sure":
            self._save_partial_state = None
            self._runner.write_stdin("y")
        if _STRIP_OK_RE.search(line) and self._guided_state == "waiting":
            self._advance_guided_strip(on_line)
        if _ALL_DONE_RE.search(line) and not (self._is_resume and _STRIP_RE.search(line)):
            self.all_stripes_done.emit()
        if _CALIBRATION_PROMPT_RE.search(line):
            self.calibration_prompt.emit()
        if _CALIBRATION_RE.search(line):
            self.calibration_done.emit()
        m = _STRIP_ERROR_RE.search(line)
        if m:
            self.strip_error.emit(m.group(1).strip())
        if _USB_ERROR_RE.search(line):
            self.instrument_disconnected.emit()
        if _DEVICE_BUSY_RE.search(line):
            self.device_busy.emit()
        if _NO_INSTRUMENT_RE.search(line):
            self.no_instrument.emit()
        m = _WRONG_STRIP_RE.search(line)
        if m:
            self.wrong_strip.emit(m.group(1).upper(), m.group(2).upper())
        m = _UNEXPECTED_RESP_RE.search(line)
        if m:
            self.unexpected_response.emit(m.group(1))
        if _SENSOR_POSITION_RE.search(line):
            self.sensor_wrong_position.emit()
        if _USB_VM_RE.search(line):
            self.usb_claimed_by_vm.emit()

    # ------------------------------------------------------------------
    # Guided strip navigation
    # ------------------------------------------------------------------

    def _advance_guided_strip(self, on_line: Callable[[str], None]) -> None:
        """Called when 'Strip read OK' is detected while in guided-waiting state."""
        target = self._guided_strips[self._guided_idx]
        self._guided_idx += 1
        if self._guided_idx >= len(self._guided_strips):
            self._guided_state = "idle_done"
            on_line("[Guided Refinement] All target strips measured.")
            self.all_stripes_done.emit()
        else:
            next_target = self._guided_strips[self._guided_idx]
            self._guided_state = "navigating"
            on_line(
                f"[Guided Refinement] Strip {target} done. "
                f"Moving to strip {next_target}\u2026"
            )
            # Navigation is triggered by the next stripe_changed event —
            # chartread re-announces the current strip after Strip read OK
            # in resume mode, which fires stripe_changed and drives navigation.

    def _guided_step(self, current: str, on_line: Callable[[str], None]) -> None:
        letter = "".join(c for c in current if c.isalpha()).upper()
        if not letter:
            return

        if self._guided_state == "idle":
            target = self._guided_strips[0]
            self._guided_state = "navigating"
            strips_str = ", ".join(self._guided_strips)
            on_line(
                f"[Guided Refinement] Starting auto-navigation to "
                f"{len(self._guided_strips)} strip(s): {strips_str} — worst \u0394E first."
            )
            on_line("[Guided Refinement] The app will press 'f'/'b' for you. Do not touch the keyboard.")
            on_line(f"[Guided Refinement] Moving to strip {target}\u2026")
            self._navigate_toward(letter, target)
            return

        if self._guided_state == "navigating":
            target = self._guided_strips[self._guided_idx]
            if letter == target:
                self._guided_state = "waiting"
                on_line(f"[Guided Refinement] Arrived at strip {target} \u2014 please scan now.")
            else:
                self._navigate_toward(letter, target)

        elif self._guided_state == "waiting":
            target = self._guided_strips[self._guided_idx]
            if letter != target:
                # chartread moved to a new strip — previous one was accepted
                self._guided_idx += 1
                if self._guided_idx >= len(self._guided_strips):
                    self._guided_state = "idle_done"
                    on_line(
                        "[Guided Refinement] All target strips measured. "
                        "You may press 'n' or 'd' to finish."
                    )
                else:
                    next_target = self._guided_strips[self._guided_idx]
                    self._guided_state = "navigating"
                    on_line(
                        f"[Guided Refinement] Strip {target} done. "
                        f"Moving to strip {next_target}\u2026"
                    )
                    self._navigate_toward(letter, next_target)

    def _navigate_toward(self, current: str, target: str) -> None:
        ci = letter_to_idx(current)
        ti = letter_to_idx(target)
        key = "f" if ti > ci else "b"
        self._runner.write_stdin(key)
