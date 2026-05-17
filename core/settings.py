"""Persistent application settings via QSettings."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSettings

from core.logger import get_logger
from core.platform_paths import default_argyll_bin_dir, is_windows

log = get_logger(__name__)

DEFAULTS: dict[str, Any] = {
    # ArgyllCMS
    "argyll_bin_path":           default_argyll_bin_dir(),
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
    "use_native_print_dialog":   is_windows(),
    "confirm_before_printing":   True,
    # Step 3 — measure
    "measure_disable_bidir":       True,
    "measure_suppress_warnings":   True,
    "measure_extra_args":          "",
    "measure_tolerance_enabled":           True,
    "measure_tolerance_value":             0.7,
    "manual2_chartread_tolerance_enabled": True,
    # Step 4 — profile
    "colprof_algorithm":         "l",
    "colprof_quality":           "m",
    "colprof_extra_args":        "",
    # Step 5 — check & refine
    "profcheck_de_formula":      "k",       # "" / "-c" / "-k"
    "profcheck_intent":          "a",       # "a" = absolute,  "r" = relative
    "profcheck_sort":            True,
    "profcheck_verbosity":       "2",       # "1" = summary,  "2" = per-patch
    "profcheck_fwa_enabled":     False,
    "profcheck_fwa_illum":       "D50",
    "profcheck_illum":           "D50",
    "profcheck_observer":        "1931_2",
    "profcheck_prune_enabled":      False,
    "profcheck_prune_value":        3.0,
    "profcheck_x3dom":              False,
    "profcheck_refine_threshold":   2.0,
    # Step 5 — gamut viewer (iccgamut)
    "gamut_intent":              "a",     # a=absolute, r=relative, p=perceptual, s=saturation
    "gamut_pcs":                 "l",     # l=Lab, j=CIECAM02 Jab
    "gamut_sres":                20.0,    # surface resolution
    "gamut_axes":                True,    # show axes + white/black point
    "gamut_cusps":               False,   # cusp markers
    "gamut_edges":               False,   # edge plot
    "gamut_function":            "f",     # f=forward, b=backward
    "gamut_themed_colors":       True,   # remap vertex colors to app palette
    # Calibration workflow
    "calibration_mode":          False,
    "printcal_smoothing":        1.0,
    "printcal_verbosity":        1,
    "printcal_mode":             "initial",
    "applycal_mode":             "apply",
    "applycal_verbose":          False,
    # UI state
    "window_geometry":           None,
    "active_tab":                0,
    "restore_last_tab":          True,
    "restore_last_session":      False,
    "appearance":                "auto",   # "light" | "dark" | "auto"
    # Session restore paths (only used when restore_last_session is True)
    "session_target_name":       "",
    "session_ti1_path":          "",
    "session_ti3_path":          "",
    "session_icc_path":          "",
    "session_cal_ti3_path":      "",
}


class AppSettings:
    def __init__(self) -> None:
        self._qs = QSettings("ChromIQ", "ChromIQ")
        log.debug("Settings loaded from %s", self._qs.fileName())

    def get(self, key: str, default: Any = None) -> Any:
        # On Windows the OS print dialog is the only path that works (no CUPS,
        # no driver-side colour-management toggle we can reach from PostScript).
        # Force the setting True regardless of stored value so a stale False
        # carried over from an older install can't strand the user on the
        # non-functional bypass UI.
        if key == "use_native_print_dialog" and is_windows():
            return True
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
        elif isinstance(fallback, float) and isinstance(val, str):
            try:
                val = float(val)
            except ValueError:
                val = fallback
        return val

    def set(self, key: str, value: Any) -> None:
        log.debug("settings.set %s = %r", key, value)
        self._qs.setValue(key, value)

    def reset_to_defaults(self) -> None:
        presets = self._qs.value("manual_presets", "")
        self._qs.clear()
        if presets:
            self._qs.setValue("manual_presets", presets)
        log.info("Settings reset to factory defaults")

    def save_tab_defaults(self, prefix: str, values: dict[str, Any]) -> None:
        """Save a dict of key→value under a given prefix."""
        for k, v in values.items():
            self.set(f"{prefix}_{k}", v)
