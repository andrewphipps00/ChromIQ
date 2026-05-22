"""Reusable parameter row: label + typed control + tooltip button."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox, make_browse_button, open_file_dialog

log = get_logger(__name__)


class ParameterWidget(QWidget):
    """One parameter row driven by a parameter definition dict from parameters.yaml."""

    value_changed = pyqtSignal()

    def __init__(
        self,
        param: dict[str, Any],
        parent: QWidget | None = None,
        browse_icon: str = "folder",
    ) -> None:
        super().__init__(parent)
        self._param = param
        self._browse_icon = browse_icon
        self._control: QWidget | None = None
        self._label: QLabel | None = None
        self._browse_btn: QPushButton | None = None
        self._enable_check: QCheckBox | None = None
        self._tooltip_btn: TooltipButton | None = None
        self._custom_combo: NoScrollComboBox | None = None
        self._custom_w_spin: NoScrollSpinBox | None = None
        self._custom_h_spin: NoScrollSpinBox | None = None
        self._custom_dim_row: QWidget | None = None
        self._build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def flag(self) -> str:
        return self._param.get("flag", "")

    @property
    def affects_preview(self) -> bool:
        return bool(self._param.get("affects_preview", False))

    @property
    def no_space(self) -> bool:
        return bool(self._param.get("no_space", False))

    @property
    def expert_only(self) -> bool:
        return bool(self._param.get("expert_only", False))

    def get_value(self) -> str:
        """Return CLI-ready value string, or '' to skip this flag."""
        if self._control is None and self._enable_check is not None:
            return "__flag__" if self._enable_check.isChecked() else ""
        c = self._control
        if c is None:
            return ""
        t = self._param.get("type", "string")
        if t == "boolean":
            return "" if not c.isChecked() else "__flag__"
        if t == "choice":
            if self._custom_combo is not None:
                data = self._custom_combo.currentData()
                if data == "custom" and self._custom_w_spin is not None and self._custom_h_spin is not None:
                    return f"{self._custom_w_spin.value()}x{self._custom_h_spin.value()}"
                return data or ""
            return c.currentData() or ""
        if t == "int":
            return str(c.value()) if c.value() != self._param.get("default", 0) else str(c.value())
        if t == "float":
            # QDoubleSpinBox stores the value as a binary double, so stepping
            # 1.0 → 1.1 → 1.2 lands on 1.2000000000000002 even though
            # setDecimals(2) renders it as "1.20" in the spinbox itself.
            # str(c.value()) would otherwise leak the noisy form into the
            # live command preview (extras like -A go through build_args
            # rather than the explicitly-formatted f"-a{...:.2f}" path).
            decimals = c.decimals() if hasattr(c, "decimals") else 2
            return f"{c.value():.{decimals}f}"
        if t in ("string", "file_path"):
            return c.text().strip()
        return ""

    def get_raw_value(self) -> Any:
        """Return Python-native value (bool, int, float, str)."""
        if self._control is None and self._enable_check is not None:
            return self._enable_check.isChecked()
        c = self._control
        if c is None:
            return None
        t = self._param.get("type", "string")
        if t == "boolean":
            return c.isChecked()
        if t == "choice":
            if self._custom_combo is not None:
                data = self._custom_combo.currentData()
                if data == "custom" and self._custom_w_spin is not None and self._custom_h_spin is not None:
                    return f"{self._custom_w_spin.value()}x{self._custom_h_spin.value()}"
                return data
            return c.currentData()
        if t == "int":
            return c.value()
        if t == "float":
            return c.value()
        if t in ("string", "file_path"):
            return c.text().strip()
        return None

    def set_value(self, v: Any) -> None:
        if self._control is None and self._enable_check is not None:
            self._enable_check.setChecked(bool(v))
            return
        c = self._control
        if c is None:
            return
        t = self._param.get("type", "string")
        try:
            if t == "boolean":
                c.setChecked(bool(v))
            elif t == "choice":
                combo = self._custom_combo if self._custom_combo is not None else c
                idx = combo.findData(str(v))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                elif self._custom_combo is not None and "x" in str(v):
                    parts = str(v).split("x", 1)
                    if len(parts) == 2:
                        try:
                            w, h = int(parts[0]), int(parts[1])
                            ci = self._custom_combo.findData("custom")
                            if ci >= 0:
                                self._custom_combo.setCurrentIndex(ci)
                            if self._custom_w_spin:
                                self._custom_w_spin.setValue(w)
                            if self._custom_h_spin:
                                self._custom_h_spin.setValue(h)
                        except ValueError:
                            pass
            elif t == "int":
                c.setValue(int(v))
            elif t == "float":
                c.setValue(float(v))
            elif t in ("string", "file_path"):
                c.setText(str(v))
                if t == "file_path" and self._enable_check is not None and str(v).strip():
                    self._enable_check.setChecked(True)
        except Exception as exc:
            log.warning("set_value(%s, %r): %s", self.flag, v, exc)

    @property
    def is_enabled_by_user(self) -> bool:
        """False when this is an expert param whose enable-checkbox is unchecked."""
        if self._enable_check is not None:
            return self._enable_check.isChecked()
        return True

    def set_user_enabled(self, checked: bool) -> None:
        """Programmatically set the expert enable-checkbox state."""
        if self._enable_check is not None:
            self._enable_check.setChecked(checked)

    def make_compact(self) -> None:
        """Apply compact_input styling to any spinbox, combobox, or line edit in this widget."""
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit
        for w in (self._control, self._custom_combo,
                  self._custom_w_spin, self._custom_h_spin):
            if w is not None and isinstance(w, (QAbstractSpinBox, QComboBox, QLineEdit)):
                w.setObjectName("compact_input")
                w.style().unpolish(w)
                w.style().polish(w)
        if self._browse_btn is not None:
            self._browse_btn.setObjectName("browse_compact")
            self._browse_btn.style().unpolish(self._browse_btn)
            self._browse_btn.style().polish(self._browse_btn)
            from PyQt6.QtCore import QSize
            self._browse_btn.setIconSize(QSize(14, 14))
            self._browse_btn.setFixedHeight(22)

    def build_args(self) -> list[str]:
        """Build the list of CLI tokens for this parameter."""
        if not self.is_enabled_by_user:
            return []
        val = self.get_value()
        if not val:
            return []
        flag = self.flag
        if val == "__flag__":
            return [flag]
        if self.no_space:
            return [flag + val]
        return [flag, val]

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        name = self._param.get("name", self.flag)
        t    = self._param.get("type", "string")

        # Expert boolean: single checkbox serves as both enable-flag and control
        if self.expert_only and t == "boolean":
            self._enable_check = QCheckBox(name, self)
            self._enable_check.setChecked(False)
            self._enable_check.setObjectName("param_label")
            self._enable_check.toggled.connect(self.value_changed)
            layout.addWidget(self._enable_check)
            # No separate control widget; tooltip button only
            tt_title = self._param.get("tooltip_title", name)
            tt_body  = self._param.get("tooltip_body", "No description available.")
            layout.addStretch()
            layout.addWidget(TooltipButton(tt_title, tt_body, self))
            return  # early exit — no _control needed

        # Expert non-boolean: enable-checkbox replaces label
        if self.expert_only:
            self._enable_check = QCheckBox(name + ":", self)
            self._enable_check.setChecked(False)
            self._enable_check.setFixedWidth(190)
            self._enable_check.setObjectName("param_label")
            layout.addWidget(self._enable_check)
        else:
            lbl = QLabel(name + ":", self)
            lbl.setFixedWidth(190)
            lbl.setWordWrap(False)
            lbl.setObjectName("param_label")
            self._label = lbl
            layout.addWidget(lbl)

        # Control
        self._control = self._make_control()
        layout.addWidget(self._control, stretch=1)

        # Browse button for file_path
        if self._param.get("type") == "file_path":
            self._browse_btn = make_browse_button(self, "Browse…", icon=self._browse_icon)
            self._browse_btn.clicked.connect(self._browse)
            layout.addWidget(self._browse_btn)

        # Tooltip button — honour optional `tooltip_min_width:` from YAML so a
        # parameter with extensive prose can request a wider info dialog.
        tt_title = self._param.get("tooltip_title", name)
        tt_body  = self._param.get("tooltip_body", "No description available.")
        tt_min_w = int(self._param.get("tooltip_min_width", 420))
        btn = TooltipButton(tt_title, tt_body, self, min_width=tt_min_w)
        self._tooltip_btn = btn
        layout.addWidget(btn)

        # Wire enable-checkbox to control enabled state
        if self._enable_check is not None:
            self.set_control_enabled(False)
            self._enable_check.toggled.connect(self._on_enable_toggled)

    def set_display_text(self, label: str, tooltip_title: str | None = None,
                          tooltip_body: str | None = None,
                          tooltip_min_width: int | None = None) -> None:
        """Repurpose the row's visible label and tooltip without rebuilding.

        Used when one CLI flag has different meanings per context (e.g. -h
        is "Double density" for ColorMunki but "Hexagon patches" for
        SpectroScan). Leaves the underlying control widget untouched.
        """
        if self._label is not None:
            self._label.setText(label + ":")
        elif self._enable_check is not None:
            suffix = ":" if not (self.expert_only and self._param.get("type") == "boolean") else ""
            self._enable_check.setText(label + suffix)
        if self._tooltip_btn is not None and tooltip_title is not None:
            self._tooltip_btn._title = tooltip_title
            if tooltip_body is not None:
                self._tooltip_btn._body = tooltip_body.strip()
            if tooltip_min_width is not None:
                self._tooltip_btn._min_width = tooltip_min_width
            self._tooltip_btn.setToolTip(f"{tooltip_title}\n\nClick for details")

    def set_control_enabled(self, enabled: bool,
                            include_label: bool = True) -> None:
        """Enable/disable the control + browse button (+ label by default).

        The label tracks the control so the ``:disabled`` QSS selector
        greys it out in both light and dark themes. Pass
        ``include_label=False`` to leave the label at full strength — used
        by the Auto checkboxes, where the row name should stay readable
        while only the input is greyed out.
        """
        if self._control is not None:
            self._control.setEnabled(enabled)
        if self._browse_btn is not None:
            self._browse_btn.setEnabled(enabled)
        if include_label and self._label is not None:
            self._label.setEnabled(enabled)

    def _on_enable_toggled(self, checked: bool) -> None:
        self.set_control_enabled(checked)
        self.value_changed.emit()

    def _make_control(self) -> QWidget:
        t       = self._param.get("type", "string")
        default = self._param.get("default")

        if t == "boolean":
            cb = QCheckBox(self)
            cb.setChecked(bool(default))
            cb.toggled.connect(self.value_changed)
            return cb

        if t == "choice":
            if self._param.get("custom_dimensions"):
                return self._make_custom_dim_control()
            combo = NoScrollComboBox(self)
            combo.setSizeAdjustPolicy(combo.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choices = self._param.get("choices", [])
            labels  = self._param.get("labels", choices)
            for ch, lb in zip(choices, labels):
                combo.addItem(str(lb), str(ch))
            if default is not None:
                idx = combo.findData(str(default))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self.value_changed)
            return combo

        if t == "int":
            sb = NoScrollSpinBox(self)
            sb.setRange(
                self._param.get("min", 0),
                self._param.get("max", 9999),
            )
            sb.setSingleStep(self._param.get("step", 1))
            if default is not None:
                sb.setValue(int(default))
            sb.valueChanged.connect(self.value_changed)
            return sb

        if t == "float":
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(
                self._param.get("min", 0.0),
                self._param.get("max", 999.9),
            )
            sb.setSingleStep(self._param.get("step", 0.1))
            sb.setDecimals(2)
            if default is not None:
                sb.setValue(float(default))
            sb.valueChanged.connect(self.value_changed)
            return sb

        # string / file_path
        le = QLineEdit(self)
        if default:
            le.setText(str(default))
        le.textChanged.connect(self.value_changed)
        return le

    def _browse(self) -> None:
        filt = self._param.get("filter", "")
        extra_paths: tuple | list = ()
        if self._param.get("icc_sidebar"):
            from ui.widgets import icc_profile_paths
            extra_paths = icc_profile_paths()
        path = open_file_dialog(self, "Select file", filt, extra_paths=extra_paths)
        if path and isinstance(self._control, QLineEdit):
            self._control.setText(path)

    def _make_custom_dim_control(self) -> QWidget:
        container = QWidget(self)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        combo = NoScrollComboBox(container)
        choices = self._param.get("choices", [])
        labels  = self._param.get("labels", choices)
        for ch, lb in zip(choices, labels):
            combo.addItem(str(lb), str(ch))
        default = self._param.get("default")
        if default is not None:
            idx = combo.findData(str(default))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self._custom_combo = combo
        combo.currentIndexChanged.connect(self.value_changed)
        combo.currentIndexChanged.connect(self._on_custom_dim_changed)
        vbox.addWidget(combo)

        dim_row = QWidget(container)
        hbox = QHBoxLayout(dim_row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(6)

        lbl_w = QLabel("W (mm):", dim_row)
        lbl_w.setObjectName("param_label")
        hbox.addWidget(lbl_w)

        w_spin = NoScrollSpinBox(dim_row)
        w_spin.setRange(10, 9999)
        w_spin.setValue(210)
        w_spin.valueChanged.connect(self.value_changed)
        self._custom_w_spin = w_spin
        hbox.addWidget(w_spin)

        lbl_h = QLabel("H (mm):", dim_row)
        lbl_h.setObjectName("param_label")
        hbox.addWidget(lbl_h)

        h_spin = NoScrollSpinBox(dim_row)
        h_spin.setRange(10, 9999)
        h_spin.setValue(297)
        h_spin.valueChanged.connect(self.value_changed)
        self._custom_h_spin = h_spin
        hbox.addWidget(h_spin)
        hbox.addStretch()

        dim_row.hide()
        self._custom_dim_row = dim_row
        vbox.addWidget(dim_row)

        return container

    def _on_custom_dim_changed(self) -> None:
        if self._custom_dim_row is None or self._custom_combo is None:
            return
        self._custom_dim_row.setVisible(self._custom_combo.currentData() == "custom")
