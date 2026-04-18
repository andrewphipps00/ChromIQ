"""Persistent application settings via QSettings."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSettings

from core.logger import get_logger

log = get_logger(__name__)

DEFAULTS: dict[str, Any] = {
    # ArgyllCMS
    "argyll_bin_path":           "/Applications/Argyll/bin",
    # Preferences
    "preferred_instrument":      "i1",
    "preferred_paper_size":      "A4",
    "custom_output_path":        "",       # "" = ~/ChromIQ/
    # Step 1 — chart creation
    "chart_mode":                "guided",
    "chart_instrument":          "i1",
    "chart_paper":               "A4",
    "chart_pages":               1,
    "chart_double_density":      False,
    "chart_disable_left_border": False,
    "targen_device_type":        "2",      # Print RGB
    "targen_patches":            0,        # 0 = auto-computed
    "targen_white_patches":      4,
    "targen_black_patches":      4,
    "targen_good_mode":          True,
    "targen_extra_args":         "",
    "printtarg_dpi":             300,
    "printtarg_extra_args":      "",
    # Step 2 — print
    "last_printer":              "",
    "print_input_slot":          "",
    "print_media":               "",
    "print_media_type":          "",
    "print_quality":             "",
    # Step 3 — measure
    "measure_disable_bidir":     True,
    "measure_suppress_warnings": True,
    "measure_extra_args":        "",
    # Step 4 — profile
    "colprof_algorithm":         "l",
    "colprof_quality":           "m",
    "colprof_extra_args":        "",
    # UI state
    "window_geometry":           None,
    "active_tab":                0,
}


class AppSettings:
    def __init__(self) -> None:
        self._qs = QSettings("ChromIQ", "ChromIQ")
        log.debug("Settings loaded from %s", self._qs.fileName())

    def get(self, key: str, default: Any = None) -> Any:
        fallback = DEFAULTS.get(key, default)
        val = self._qs.value(key, fallback)
        # QSettings can return strings for booleans — coerce
        if isinstance(fallback, bool) and isinstance(val, str):
            val = val.lower() in ("true", "1", "yes")
        elif isinstance(fallback, int) and isinstance(val, str):
            try:
                val = int(val)
            except ValueError:
                val = fallback
        return val

    def set(self, key: str, value: Any) -> None:
        log.debug("settings.set %s = %r", key, value)
        self._qs.setValue(key, value)

    def reset_to_defaults(self) -> None:
        self._qs.clear()
        log.info("Settings reset to factory defaults")

    def save_tab_defaults(self, prefix: str, values: dict[str, Any]) -> None:
        """Save a dict of key→value under a given prefix."""
        for k, v in values.items():
            self.set(f"{prefix}_{k}", v)
