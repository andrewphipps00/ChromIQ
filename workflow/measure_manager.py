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
from core.i18n import tr

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
# chartread.c 3.5.0 L1671/L2238: a comms failure mid-strip. Unlike the misread
# and unexpected-error variants it prints no "(reason)" in parentheses, so
# _STRIP_ERROR_RE never matches it — hence this dedicated pattern. The prompt
# ("any other key to retry") returns to the strip menu just like a misread, so
# it routes through the same strip_error signal / dialog (Retry/Skip/Save).
_STRIP_COMS_FAIL_RE    = re.compile(r"Strip\s+read\s+failed\s+due\s+to\s+communication\s+problem", re.IGNORECASE)
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

# --- A. Mid-measurement recovery prompts ---------------------------------
# chartread.c 3.5.0 L1608: user hit the instrument switch / Ctrl-C mid-strip.
_STRIP_INTERRUPTED_RE  = re.compile(r"Strip read stopped at user request",      re.IGNORECASE)
# chartread.c 3.5.0 L1593: user pressed 'd' while patches are still unread.
# Captures the "id, loc" payload so we can show the user which patch is missing.
_UNREAD_CONFIRM_RE     = re.compile(r"Done\s*\?\s*-\s*At least one unread patch \(([^)]+)\)", re.IGNORECASE)
# chartread.c 3.5.0 L396: generic ierror() — transient instrument error outside the strip-read fast path.
_GENERIC_IERROR_RE     = re.compile(r"Got\s+'([^']+)'\s*\(([^)]+)\)\s+error\.", re.IGNORECASE)

# --- B. Startup / config failure messages --------------------------------
_INIT_COMS_FAIL_RE     = re.compile(r"Establishing communications with instrument failed with message\s+'([^']+)'", re.IGNORECASE)
_INIT_INST_FAIL_RE     = re.compile(r"Initialising instrument failed with message\s+'([^']+)'", re.IGNORECASE)
_CAPABILITY_FAIL_RE    = re.compile(r"Need (reflection|transmission|emissive)\s[^\n]*?reading capability", re.IGNORECASE)
_CCMX_FAIL_RE          = re.compile(
    r"Setting Colorimeter Correction Matrix failed"
    r"|Reading CCMX/CCSS File\s+'[^']+' failed"
    r"|Instrument doesn't have Colorimeter Correction Matrix capability"
    r"|Instrument doesn't have Colorimeter Calibration Spectral Sample capability",
    re.IGNORECASE,
)
_MODE_SET_FAIL_RE      = re.compile(r"Setting instrument mode failed with error\s*:?\s*'([^']+)'", re.IGNORECASE)

# --- B-status. Informational lines surfaced as status-bar messages -------
_INFO_CHART_INST_MISMATCH_RE = re.compile(r"Warning:\s*chart is for\s+(\S+),\s*using instrument\s+(\S+)", re.IGNORECASE)
# Battery level fires at the start of every chartread session on i1Pro and
# Spectro2 — surfacing it would be noisy. Logged via the normal log line, not
# flashed as a status message.
_INFO_BATTERY_RE             = re.compile(r"(?!x)x", re.IGNORECASE)   # disabled
_INFO_NO_SPECTRAL_RE         = re.compile(r"Instrument isn't capable of spectral measurement", re.IGNORECASE)
_INFO_HIGHRES_IGNORED_RE     = re.compile(r"high resolution ignored", re.IGNORECASE)
_INFO_UV_IGNORED_RE          = re.compile(r"UV measurement mode requested, but instrument doesn't support", re.IGNORECASE)
_INFO_SCAN_TOL_IGNORED_RE    = re.compile(r"Modified patch consistency tolerance ignored", re.IGNORECASE)

# --- D. Spot / XY mode defensive handlers --------------------------------
_XY_PLACE_SHEET_RE     = re.compile(r"Please place sheet\s+(\d+)\s+of\s+(\d+)\s+on table", re.IGNORECASE)
_XY_SHEET_OK_RE        = re.compile(r"Sheet\s+(\d+)\s+of\s+(\d+)\s+read OK", re.IGNORECASE)
_SPOT_READY_RE         = re.compile(r"Ready to read patch\s+'([^']+)'", re.IGNORECASE)
_ABORT_CONFIRM_RE      = re.compile(r"Abort\s*\?\s*-\s*Are you sure\s*\?\s*\[y/n\]", re.IGNORECASE)
_PATCH_NOT_FOUND_RE    = re.compile(r"Patch\s+'([^']+)'\s+not found", re.IGNORECASE)


@dataclass
class MeasureParams:
    ti1_path: Path
    instrument: str = "1"
    disable_bidir: bool = False
    force_bidir: bool = False
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

    # A. Mid-measurement recovery prompts
    strip_interrupted          = pyqtSignal()       # chartread reports the strip read was interrupted by user
    unread_confirm             = pyqtSignal(str)    # user pressed 'd' with unread patches; carries "id, loc"
    generic_instrument_error   = pyqtSignal(str, str)  # (friendly_msg, technical_detail) from ierror()

    # B. Startup / config failures (terminal — chartread exits)
    coms_init_failed           = pyqtSignal(str)    # serial/USB init failed
    inst_init_failed           = pyqtSignal(str)    # init_inst() failed
    instrument_wrong_type      = pyqtSignal(str)    # instrument can't do reflection/transmission/emissive as needed
    ccmx_load_failed           = pyqtSignal(str)    # CCMX/CCSS load failed
    mode_set_failed            = pyqtSignal(str)    # setting instrument mode failed

    # B-status. Non-blocking informational messages
    info_message               = pyqtSignal(str, str)  # (category, text)

    # D. Spot / XY mode (defensive coverage — won't fire in strip mode)
    xy_place_sheet             = pyqtSignal(int, int)  # (sheet_n, total_sheets)
    spot_ready                 = pyqtSignal(str)       # patch id
    abort_confirm              = pyqtSignal()

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
        # -B (disable) and -b (force enable) are mutually exclusive; -B wins
        # if both are somehow set.
        if p.disable_bidir:
            args.append("-B")
        elif p.force_bidir:
            args.append("-b")
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
        # IMPORTANT: handle the user-initiated "unread patch" prompt BEFORE the
        # generic _ARE_YOU_SURE_RE auto-answer below, otherwise that branch
        # resets _save_partial_state to None and the gate here would let the
        # dialog fire even when our Save-Partial flow is in control.
        m = _UNREAD_CONFIRM_RE.search(line)
        if m and self._save_partial_state is None:
            self.unread_confirm.emit(m.group(1).strip())
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
        elif _STRIP_COMS_FAIL_RE.search(line):
            self.strip_error.emit("communication problem")
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

        # A. Mid-measurement recovery prompts ------------------------------
        # (note: _UNREAD_CONFIRM_RE is handled above, before _ARE_YOU_SURE_RE,
        # so the Save-Partial state machine and the user-driven dialog don't
        # race each other when the prompt arrives.)
        if _STRIP_INTERRUPTED_RE.search(line):
            self.strip_interrupted.emit()
        m = _GENERIC_IERROR_RE.search(line)
        if m:
            self.generic_instrument_error.emit(m.group(1).strip(), m.group(2).strip())

        # B. Startup / config failures -------------------------------------
        m = _INIT_COMS_FAIL_RE.search(line)
        if m:
            self.coms_init_failed.emit(m.group(1).strip())
        m = _INIT_INST_FAIL_RE.search(line)
        if m:
            self.inst_init_failed.emit(m.group(1).strip())
        m = _CAPABILITY_FAIL_RE.search(line)
        if m:
            self.instrument_wrong_type.emit(m.group(1).lower())
        if _CCMX_FAIL_RE.search(line):
            self.ccmx_load_failed.emit(line.strip())
        m = _MODE_SET_FAIL_RE.search(line)
        if m:
            self.mode_set_failed.emit(m.group(1).strip())

        # B-status. Informational ------------------------------------------
        m = _INFO_CHART_INST_MISMATCH_RE.search(line)
        if m:
            self.info_message.emit(
                "chart_instrument_mismatch",
                f"Note: chart was generated for {m.group(1)}; reading with {m.group(2)} anyway.",
            )
        m = _INFO_BATTERY_RE.search(line)
        if m:
            try:
                pct = round(float(m.group(1)))
                self.info_message.emit("battery", tr("Instrument battery: {pct}%").format(pct=pct))
            except ValueError:
                pass
        if _INFO_NO_SPECTRAL_RE.search(line):
            self.info_message.emit(
                "no_spectral",
                "Spectral measurement not available on this instrument — colorimetric only.",
            )
        if _INFO_HIGHRES_IGNORED_RE.search(line):
            self.info_message.emit(
                "highres_ignored",
                "High-resolution mode requested but not supported — using normal resolution.",
            )
        if _INFO_UV_IGNORED_RE.search(line):
            self.info_message.emit(
                "uv_ignored",
                "UV mode requested but not supported on this instrument.",
            )
        if _INFO_SCAN_TOL_IGNORED_RE.search(line):
            self.info_message.emit(
                "scan_tol_ignored",
                "Patch consistency tolerance setting ignored — instrument doesn't support it.",
            )

        # D. Spot / XY mode ------------------------------------------------
        m = _XY_PLACE_SHEET_RE.search(line)
        if m:
            self.xy_place_sheet.emit(int(m.group(1)), int(m.group(2)))
        m = _XY_SHEET_OK_RE.search(line)
        if m:
            self.info_message.emit(
                "xy_sheet_ok",
                f"Sheet {m.group(1)} of {m.group(2)} read successfully.",
            )
        m = _SPOT_READY_RE.search(line)
        if m:
            self.spot_ready.emit(m.group(1))
        if _ABORT_CONFIRM_RE.search(line):
            self.abort_confirm.emit()
        m = _PATCH_NOT_FOUND_RE.search(line)
        if m:
            self.info_message.emit("patch_not_found", tr("Patch '{name}' not found.").format(name=m.group(1)))

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
