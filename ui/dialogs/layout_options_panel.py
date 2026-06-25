"""Reusable ChromIQ layout-engine options panel (issue #93).

The same control set is shown in **Settings → Chart Layout** (as the defaults
editor) and in the **Create Chart → Manual** module (as the per-chart mirror),
so the two can't drift.  The panel edits the layout-specific fields of a
:class:`~workflow.layout_engine.presets.LayoutRecipe`; the host supplies the
instrument / paper / mode (those live in the surrounding selectors).

It is Qt-only UI glue — no engine logic beyond reading/writing the recipe.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from core.i18n import tr
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox
from workflow.layout_engine.presets import LayoutRecipe


class LayoutOptionsPanel(QWidget):
    """All layout-engine controls except instrument/paper/mode."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *,
                 with_calibration: bool = False) -> None:
        super().__init__(parent)
        self._loading = False
        self._with_calibration = with_calibration
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        def mm(special_auto: bool = False, top: float = 300.0) -> NoScrollDoubleSpinBox:
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0, top)
            sb.setDecimals(1)
            sb.setSingleStep(0.5)
            sb.setSuffix(" mm")
            if special_auto:
                sb.setSpecialValueText(tr("auto"))
            sb.valueChanged.connect(self._emit)
            return sb

        def scale() -> NoScrollDoubleSpinBox:
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0.5, 3.0)
            sb.setDecimals(3)
            sb.setSingleStep(0.05)
            sb.valueChanged.connect(self._emit)
            return sb

        # ---- Patches & spacers ----
        ps = QGroupBox(tr("Patches && spacers"), self)
        g = QGridLayout(ps)
        g.addWidget(QLabel(tr("Patch scale:"), self), 0, 0)
        self.pscale = scale(); g.addWidget(self.pscale, 0, 1)
        g.addWidget(QLabel(tr("Spacer scale:"), self), 0, 2)
        self.sscale = scale(); g.addWidget(self.sscale, 0, 3)
        g.addWidget(QLabel(tr("Spacers:"), self), 1, 0)
        self.spacer_mode = NoScrollComboBox(self)
        for k, lbl in (("colored", tr("Coloured")), ("bw", tr("Black & white")),
                       ("none", tr("None"))):
            self.spacer_mode.addItem(lbl, k)
        self.spacer_mode.currentIndexChanged.connect(self._emit)
        g.addWidget(self.spacer_mode, 1, 1)
        g.addWidget(QLabel(tr("Spacer width:"), self), 1, 2)
        self.spacer_width = mm(special_auto=True); g.addWidget(self.spacer_width, 1, 3)
        g.addWidget(QLabel(tr("Patch size:"), self), 2, 0)
        self.patch_x = mm(special_auto=True, top=60); g.addWidget(self.patch_x, 2, 1)
        g.addWidget(QLabel(tr("× height:"), self), 2, 2)
        self.patch_y = mm(special_auto=True, top=60); g.addWidget(self.patch_y, 2, 3)
        g.addWidget(QLabel(tr("Inter-patch gap:"), self), 3, 0)
        self.inter_patch = mm(); g.addWidget(self.inter_patch, 3, 1)
        g.addWidget(QLabel(tr("Strip-indicator gap:"), self), 3, 2)
        self.sig = mm(); g.addWidget(self.sig, 3, 3)
        g.addWidget(TooltipButton(
            tr("Patch size"),
            tr("Width × height of each patch in millimetres. Leave at “auto” (0) "
               "to use the instrument's recommended size (scaled by Patch scale). "
               "A value below ~6 mm can make the chart hard to read."), self), 2, 4)
        v.addWidget(ps)

        # ---- Page geometry ----
        pg = QGroupBox(tr("Page geometry"), self)
        gg = QGridLayout(pg)
        gg.addWidget(QLabel(tr("Margins:"), self), 0, 0)
        mrow = QHBoxLayout()
        self.margins: dict[str, NoScrollDoubleSpinBox] = {}
        for k, lbl in (("t", tr("T")), ("r", tr("R")), ("b", tr("B")), ("l", tr("L"))):
            mrow.addWidget(QLabel(lbl, self))
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0, 60); sb.setDecimals(1); sb.setSingleStep(0.5)
            sb.setSuffix(" mm"); sb.setFixedWidth(78)
            sb.valueChanged.connect(self._emit)
            self.margins[k] = sb
            mrow.addWidget(sb)
        mrow.addStretch()
        mw = QWidget(self); mw.setLayout(mrow)
        gg.addWidget(mw, 0, 1, 1, 4)
        gg.addWidget(QLabel(tr("Resolution:"), self), 1, 0)
        self.dpi = NoScrollSpinBox(self); self.dpi.setRange(72, 1200)
        self.dpi.setSuffix(" dpi"); self.dpi.valueChanged.connect(self._emit)
        gg.addWidget(self.dpi, 1, 1)
        self.nolimit = QCheckBox(tr("Don't cap strip length"), self)
        self.nolimit.toggled.connect(self._emit)
        gg.addWidget(self.nolimit, 1, 2, 1, 2)
        gg.addWidget(QLabel(tr("Max strip length:"), self), 2, 0)
        self.max_strip = mm(special_auto=True); gg.addWidget(self.max_strip, 2, 1)
        gg.addWidget(QLabel(tr("Chart offset:"), self), 2, 2)
        self.offx = mm(); gg.addWidget(self.offx, 2, 3)
        gg.addWidget(QLabel(tr("Strip pattern:"), self), 3, 0)
        from PyQt6.QtWidgets import QLineEdit
        self.strip_pat = QLineEdit(self); self.strip_pat.textChanged.connect(self._emit)
        gg.addWidget(self.strip_pat, 3, 1)
        gg.addWidget(QLabel(tr("Patch pattern:"), self), 3, 2)
        self.patch_pat = QLineEdit(self); self.patch_pat.textChanged.connect(self._emit)
        gg.addWidget(self.patch_pat, 3, 3)
        gg.addWidget(QLabel(tr("× vertical:"), self), 4, 2)
        self.offy = mm(); gg.addWidget(self.offy, 4, 3)
        v.addWidget(pg)

        # ---- Output ----
        og = QGroupBox(tr("Output"), self)
        ogg = QGridLayout(og)
        self.bit16 = QCheckBox(tr("16-bit TIFF"), self)
        self.bit16.toggled.connect(self._emit)
        ogg.addWidget(self.bit16, 0, 0, 1, 2)
        ogg.addWidget(QLabel(tr("Compression:"), self), 0, 2)
        self.compression = NoScrollComboBox(self)
        for k, lbl in (("lzw", "LZW"), ("zlib", "Zlib"), ("none", tr("None"))):
            self.compression.addItem(lbl, k)
        self.compression.currentIndexChanged.connect(self._emit)
        ogg.addWidget(self.compression, 0, 3)
        v.addWidget(og)

        # ---- Calibration (per-chart; engine -K/-I) ----
        self.cal_mode = self.cal_path_edit = None
        if with_calibration:
            from PyQt6.QtWidgets import QLineEdit, QPushButton
            cg = QGroupBox(tr("Printer calibration"), self)
            cgg = QGridLayout(cg)
            cgg.addWidget(QLabel(tr("Mode:"), self), 0, 0)
            self.cal_mode = NoScrollComboBox(self)
            for k, lbl in (("off", tr("None")),
                           ("apply", tr("Apply && embed (-K)")),
                           ("embed", tr("Embed only (-I)"))):
                self.cal_mode.addItem(lbl, k)
            self.cal_mode.currentIndexChanged.connect(self._emit)
            cgg.addWidget(self.cal_mode, 0, 1)
            self.cal_path_edit = QLineEdit(self)
            self.cal_path_edit.setPlaceholderText(tr("no .cal file selected"))
            self.cal_path_edit.textChanged.connect(self._emit)
            cgg.addWidget(self.cal_path_edit, 1, 0, 1, 3)
            browse = QPushButton(tr("Browse…"), self)
            browse.clicked.connect(self._browse_cal)
            cgg.addWidget(browse, 1, 3)
            cgg.addWidget(TooltipButton(
                tr("Printer calibration"),
                tr("Attach an ArgyllCMS .cal linearisation. “Apply && embed (-K)” "
                   "bakes the calibration into the printed patch values and stores "
                   "it in the .ti2; “Embed only (-I)” stores it without changing "
                   "the patches (use this if your printer/RIP already linearises). "
                   "Leave “None” if you don't calibrate."), self), 0, 2)
            v.addWidget(cg)

    def _browse_cal(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select printer calibration"), "",
            tr("ArgyllCMS calibration (*.cal)"))
        if path and self.cal_path_edit is not None:
            self.cal_path_edit.setText(path)

    def cal_settings(self) -> tuple[str | None, bool]:
        """Return ``(cal_path_or_None, apply_cal)`` for the engine."""
        if self.cal_mode is None:
            return None, False
        mode = self.cal_mode.currentData()
        path = (self.cal_path_edit.text().strip() or None) if self.cal_path_edit else None
        if mode == "off" or not path:
            return None, False
        return path, (mode == "apply")

    def set_cal(self, path: str, mode: str) -> None:
        if self.cal_mode is None:
            return
        i = self.cal_mode.findData(mode)
        self.cal_mode.setCurrentIndex(i if i >= 0 else 0)
        if self.cal_path_edit is not None:
            self.cal_path_edit.setText(path or "")

    # ------------------------------------------------------------------
    def _emit(self, *_a) -> None:
        if not self._loading:
            self.changed.emit()

    def set_recipe(self, r: LayoutRecipe) -> None:
        self._loading = True
        self.pscale.setValue(r.pscale)
        self.sscale.setValue(r.sscale)
        i = self.spacer_mode.findData(r.spacer_mode)
        self.spacer_mode.setCurrentIndex(i if i >= 0 else 0)
        self.spacer_width.setValue(r.spacer_width_mm)
        self.patch_x.setValue(r.patch_w_mm)
        self.patch_y.setValue(r.patch_h_mm)
        self.inter_patch.setValue(r.inter_patch_mm)
        self.sig.setValue(r.strip_indicator_gap_mm)
        self.margins["t"].setValue(r.margin_top)
        self.margins["r"].setValue(r.margin_right)
        self.margins["b"].setValue(r.margin_bottom)
        self.margins["l"].setValue(r.margin_left)
        self.dpi.setValue(r.dpi)
        self.nolimit.setChecked(r.nolimit)
        self.max_strip.setValue(r.max_strip_mm)
        self.offx.setValue(r.offset_x_mm)
        self.offy.setValue(r.offset_y_mm)
        self.strip_pat.setText(r.strip_pattern)
        self.patch_pat.setText(r.patch_pattern)
        self.bit16.setChecked(r.bit16)
        ci = self.compression.findData(r.compression)
        self.compression.setCurrentIndex(ci if ci >= 0 else 0)
        self._loading = False

    def apply_to_recipe(self, r: LayoutRecipe) -> LayoutRecipe:
        """Write the panel's values onto *r* (keeps r's instrument/paper/mode)."""
        r.pscale = self.pscale.value()
        r.sscale = self.sscale.value()
        r.spacer_mode = self.spacer_mode.currentData() or "colored"
        r.spacer_on = r.spacer_mode != "none"
        r.spacer_width_mm = self.spacer_width.value()
        r.patch_w_mm = self.patch_x.value()
        r.patch_h_mm = self.patch_y.value()
        r.inter_patch_mm = self.inter_patch.value()
        r.strip_indicator_gap_mm = self.sig.value()
        r.margin_top = self.margins["t"].value()
        r.margin_right = self.margins["r"].value()
        r.margin_bottom = self.margins["b"].value()
        r.margin_left = self.margins["l"].value()
        r.border = min(r.margin_top, r.margin_right, r.margin_bottom, r.margin_left)
        r.dpi = int(self.dpi.value())
        r.nolimit = self.nolimit.isChecked()
        r.max_strip_mm = self.max_strip.value()
        r.offset_x_mm = self.offx.value()
        r.offset_y_mm = self.offy.value()
        r.strip_pattern = self.strip_pat.text() or r.strip_pattern
        r.patch_pattern = self.patch_pat.text() or r.patch_pattern
        r.bit16 = self.bit16.isChecked()
        r.compression = self.compression.currentData() or "lzw"
        return r
