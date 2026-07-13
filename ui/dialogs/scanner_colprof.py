"""Scanner / camera colprof settings for the profile-build dialog (#121, Knut).

The "Build scanner or camera profile" window exposes the most-used colprof
options directly (profile type, colour space, quality, description) and the rest
behind an **Advanced…** button, using the same ParameterWidget method as tab
"4 Build profile" and the same ``(-flag)`` label convention. All values are
remembered between runs (stored in QSettings, so *Restore factory defaults* in
Preferences clears them), and the window shows the exact colprof command the
current settings produce.

This module holds the Advanced-dialog parameter definitions, the mapping from UI
values to :class:`~workflow.profile_builder.ProfileParams`, and the persistence
keys. The main-window controls live in ``scanin_dialog.py`` next to the existing
profile-type row.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QScrollArea, QVBoxLayout,
                             QWidget)

from core.i18n import tr
from ui.parameter_widget import ParameterWidget

# QSettings prefix for the remembered scanner colprof configuration.
SETTINGS_PREFIX = "scanner_colprof"

# Main-window profile type = colprof's -a algorithm directly (data = the -a
# letter). The XYZ vs Lab distinction is how a cLUT stores colour internally, so
# it belongs to the profile type, not a separate "colour space" control — a
# scanner/camera *input* profile has no working-space or rendering-intent choice
# (those are output/printer-profile concepts). (Knut, #121)
PTYPE_CHOICES = [
    ("s", tr("Shaper + matrix (recommended)")),
    ("m", tr("Matrix only")),
    ("x", tr("cLUT — XYZ table")),
    ("l", tr("cLUT — Lab table")),
]
QUALITY_CHOICES = [
    ("l", tr("Low")), ("m", tr("Medium")), ("h", tr("High")), ("u", tr("Ultra")),
]
CLUT_ALGOS = ("x", "l")            # the -a letters for which -q quality applies


# Advanced (less-common) colprof options, rendered with ParameterWidget so the
# method and label style match tab "4 Build profile". Every tooltip is written
# beginner-first (friendly, concrete, current behaviour only).
def advanced_params() -> list[dict[str, Any]]:
    return [
        {
            "flag": "-r", "name": tr("Average deviation (-r)"), "type": "float",
            "min": 0.0, "max": 5.0, "default": 0.5, "step": 0.1,
            "tooltip_title": tr("Average deviation (-r)"),
            "tooltip_body": tr(
                "How much the profile smooths over measurement noise, as a "
                "percentage of ΔE. A scanner reads the same patch a little "
                "differently each time; a higher value tells colprof to trust the "
                "overall trend more than any single patch.\n\n"
                "• 0.5 % — clean, repeatable scans on a good scanner (default).\n"
                "• 1–2 % — noisier scans, textured paper, or a camera shot.\n"
                "• 3–5 % — very noisy; smooths hard, at the cost of fine accuracy.\n\n"
                "Leave it at 0.5 % unless your scans are visibly noisy."),
        },
        {
            "flag": "-ni", "name": tr("No input curves (-ni)"), "type": "boolean",
            "default": False,
            "tooltip_title": tr("No input curves (-ni)"),
            "tooltip_body": tr(
                "Builds the profile without the per-channel input curves that "
                "colprof normally fits before the matrix or table.\n\n"
                "Those curves let the profile follow the scanner's tone response, "
                "so leaving this OFF (the default) is almost always what you want. "
                "Turn it ON only to force a plain, straight-line model — useful for "
                "debugging, or for a device you know is already linear."),
        },
        {
            "flag": "-A", "name": tr("Manufacturer (-A)"), "type": "string",
            "default": "",
            "tooltip_title": tr("Manufacturer (-A)"),
            "tooltip_body": tr(
                "An optional maker name stored inside the profile — for example "
                "“Epson” or “Canon”. It's only metadata: colour-managed apps may "
                "show it, but it doesn't change how the profile converts colour. "
                "Leave it blank if you don't need it."),
        },
        {
            "flag": "-C", "name": tr("Copyright (-C)"), "type": "string",
            "default": "",
            "tooltip_title": tr("Copyright (-C)"),
            "tooltip_body": tr(
                "An optional copyright line stored inside the profile, e.g. "
                "“© 2026 Your Studio”. Metadata only — it doesn't affect the "
                "colour conversion. Leave it blank if you don't need it."),
        },
    ]


# Keys of the values dict this module round-trips (main + advanced).
MAIN_KEYS = ("ptype", "quality")
ADVANCED_FLAGS = ("-r", "-ni", "-A", "-C")
EXTRA_ARGS_KEY = "extra_args"


def make_profile_params(ti3, description: str, main_vals: dict[str, Any],
                        adv_vals: dict[str, Any]):
    """Build the :class:`ProfileParams` colprof runs from the UI's main +
    advanced values. Used both for the real build and the command preview, so
    the preview shown is exactly what runs."""
    from workflow.profile_builder import ProfileParams
    algo = main_vals.get("ptype", "s")          # ptype data IS the colprof -a letter
    try:
        smoothing = float(adv_vals.get("-r", 0.5))
    except (TypeError, ValueError):
        smoothing = 0.5
    return ProfileParams(
        ti3_path=ti3, algorithm=algo, quality=main_vals.get("quality", "m"),
        description=description, model=description,
        manufacturer=str(adv_vals.get("-A", "") or ""),
        copyright=str(adv_vals.get("-C", "") or ""),
        smoothing=smoothing,
        no_input_shaper=bool(adv_vals.get("-ni", False)),
        extra_args=str(adv_vals.get(EXTRA_ARGS_KEY, "") or ""),
        verbose=True)


class ScannerAdvancedDialog(QDialog):
    """Modal Advanced-settings editor: ParameterWidget rows for the less-common
    colprof options, seeded from *values* and returned via :meth:`values`."""

    def __init__(self, values: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Advanced profile settings"))
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(10)

        from ui.parameter_widget import ParameterWidget as _PW  # local, keeps import tidy
        self._widgets: dict[str, _PW] = {}
        for param in advanced_params():
            w = ParameterWidget(param, body)
            if param["flag"] in values:
                w.set_value(values[param["flag"]])
            self._widgets[param["flag"]] = w
            v.addWidget(w)

        # Free-form extra colprof arguments (power users).
        from ui.parameter_widget import ParameterWidget as _PW2  # noqa: F401
        self._extra = ParameterWidget({
            "flag": EXTRA_ARGS_KEY, "name": tr("Extra colprof arguments"),
            "type": "string", "default": "",
            "tooltip_title": tr("Extra colprof arguments"),
            "tooltip_body": tr(
                "Any additional colprof options, typed exactly as on the command "
                "line (for example “-U 1.0”). They're appended after everything "
                "above, so you can reach options this window doesn't show. Leave "
                "it blank unless you know the flag you need — a wrong option here "
                "can make colprof refuse to build."),
        }, body)
        if values.get(EXTRA_ARGS_KEY):
            self._extra.set_value(values[EXTRA_ARGS_KEY])
        v.addWidget(self._extra)
        v.addStretch(1)

        # Recolour the ParameterWidget ⓘ icons to the window's green accent
        # (ParameterWidget uses the app's default accent, which is magenta).
        from ui.styles import SPEC_GREEN
        from ui.tooltip_button import TooltipButton
        for tb in body.findChildren(TooltipButton):
            tb.set_color(SPEC_GREEN)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        bb = QDialogButtonBox(self)
        self._restore_btn = bb.addButton(tr("Restore defaults"),
                                         QDialogButtonBox.ButtonRole.ResetRole)
        bb.addButton(QDialogButtonBox.StandardButton.Ok)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self._restore_btn.clicked.connect(self._restore_defaults)
        outer.addWidget(bb)

    def _restore_defaults(self) -> None:
        for param in advanced_params():
            self._widgets[param["flag"]].reset_to_default()
        self._extra.set_value("")

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for flag, w in self._widgets.items():
            out[flag] = w.get_raw_value()
        out[EXTRA_ARGS_KEY] = self._extra.get_raw_value()
        return out
