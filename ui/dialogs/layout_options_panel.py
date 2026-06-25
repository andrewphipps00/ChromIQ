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
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMenu, QToolButton,
    QVBoxLayout, QWidget,
)

from core.i18n import tr
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox
from workflow.layout_engine.presets import LayoutRecipe

# Sheet-text placeholders, filled in at build time by chart.build_chart.
SHEET_TOKENS = (
    ("project", tr("Project / target name")),
    ("date", tr("Build date")),
    ("paper", tr("Paper size code")),
    ("instrument", tr("Instrument")),
    ("patchcount", tr("Total patch count")),
    ("pages", tr("Number of pages")),
    ("seed", tr("Randomisation seed")),
    ("dpi", tr("Resolution")),
)


class LayoutOptionsPanel(QWidget):
    """All layout-engine controls except instrument/paper/mode."""

    changed = pyqtSignal()

    INSTRUMENTS = [("i1", "i1Pro"), ("p3", "i1Pro 3+"),
                   ("CM", "ColorMunki"), ("SS", "SpectroScan")]

    @staticmethod
    def modes_for(inst: str) -> list[tuple[str, str]]:
        if inst in ("i1", "p3"):
            return [("clip", tr("Clip border on")),
                    ("noclip", tr("Clip border off — more patches"))]
        if inst == "CM":
            return [("freehand", tr("Hand-held")), ("high", tr("High density (rig)")),
                    ("extrahigh", tr("Extra-high density"))]
        if inst == "SS":
            return [("flat", tr("Rectangular")), ("hex", tr("Hexagonal — denser"))]
        return [("default", tr("Default"))]

    def __init__(self, parent: QWidget | None = None, *,
                 with_calibration: bool = False, with_selectors: bool = False) -> None:
        super().__init__(parent)
        self._loading = False
        self._with_calibration = with_calibration
        self._with_selectors = with_selectors
        self._inst = "i1"           # last-known instrument / clip state, for
        self._clip = True           # clip-border-width row visibility
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.instr = self.paper = self.mode = None
        if with_selectors:
            sel = QGridLayout()
            # Instrument and Mode each get a full-width row.
            self.instr = NoScrollComboBox(self)
            for k, lbl in self.INSTRUMENTS:
                self.instr.addItem(lbl, k)
            sel.addWidget(QLabel(tr("Instrument:"), self), 0, 0)
            sel.addWidget(self.instr, 0, 1, 1, 3)
            self.mode = NoScrollComboBox(self)
            sel.addWidget(QLabel(tr("Mode:"), self), 1, 0)
            sel.addWidget(self.mode, 1, 1, 1, 3)
            # Paper + Pages share a row; paper gets the stretch (wider).
            self.paper = NoScrollComboBox(self)
            sel.addWidget(QLabel(tr("Paper:"), self), 2, 0)
            sel.addWidget(self.paper, 2, 1)
            sel.addWidget(QLabel(tr("Pages:"), self), 2, 2)
            self.pages = NoScrollSpinBox(self)
            self.pages.setRange(1, 20)
            self.pages.setValue(1)
            self.pages.setMaximumWidth(70)
            self.pages.valueChanged.connect(self._emit)
            sel.addWidget(self.pages, 2, 3)
            # Custom paper W×H (shown only when Paper = "Custom…").
            self._custom_paper_w = QWidget(self)
            _cpl = QHBoxLayout(self._custom_paper_w)
            _cpl.setContentsMargins(0, 0, 0, 0); _cpl.setSpacing(6)
            _cpl.addWidget(QLabel(tr("Custom size (mm):"), self))
            self.custom_w = NoScrollDoubleSpinBox(self)
            self.custom_h = NoScrollDoubleSpinBox(self)
            for _cs in (self.custom_w, self.custom_h):
                _cs.setRange(20, 2000); _cs.setDecimals(0); _cs.setMaximumWidth(80)
                _cs.valueChanged.connect(self._emit)
            self.custom_w.setValue(210); self.custom_h.setValue(297)
            _cpl.addWidget(self.custom_w); _cpl.addWidget(QLabel("×", self))
            _cpl.addWidget(self.custom_h); _cpl.addStretch()
            sel.addWidget(self._custom_paper_w, 3, 0, 1, 4)
            self._custom_paper_w.setVisible(False)
            sel.setColumnStretch(1, 1)        # paper / instrument / mode expand
            v.addLayout(sel)
            # Long paper labels shouldn't force the panel wide; the paper combo
            # gets a roomier minimum (it shares its row only with Pages) while
            # instrument/mode stay capped. The dropdown always shows full text.
            from PyQt6.QtWidgets import QComboBox
            self.paper.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.paper.setMinimumContentsLength(18)
            for _c in (self.instr, self.mode):
                _c.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                _c.setMinimumContentsLength(10)
            self.instr.currentIndexChanged.connect(self._on_instr_changed)
            self.paper.currentIndexChanged.connect(self._on_paper_changed)
            self.mode.currentIndexChanged.connect(self._emit)
            self.mode.currentIndexChanged.connect(self._update_clip_visibility)
            self._on_instr_changed()

        def mm(special_auto: bool = False, top: float = 300.0) -> NoScrollDoubleSpinBox:
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0, top)
            sb.setDecimals(1)
            sb.setSingleStep(0.5)
            sb.setSuffix(" mm")
            sb.setMinimumWidth(96)          # room for "300,0 mm" + buttons
            if special_auto:
                sb.setSpecialValueText(tr("auto"))
            sb.valueChanged.connect(self._emit)
            return sb

        def scale() -> NoScrollDoubleSpinBox:
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0.5, 3.0)
            sb.setDecimals(3)
            sb.setSingleStep(0.05)
            sb.setMinimumWidth(96)
            sb.valueChanged.connect(self._emit)
            return sb

        from PyQt6.QtCore import Qt as _Qt

        def add_row(grid, r, label, control, tip=None):
            """label | control (control fills the column → no clipping)."""
            grid.addWidget(QLabel(label, self), r, 0, _Qt.AlignmentFlag.AlignRight)
            grid.addWidget(control, r, 1)
            if tip is not None:
                grid.addWidget(tip, r, 2)
            grid.setColumnStretch(1, 1)

        def cell(*widgets):
            """A compact left-aligned row of small widgets in one grid cell."""
            box = QHBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(6)
            for w in widgets:
                box.addWidget(w)
            box.addStretch()
            wrap = QWidget(self); wrap.setLayout(box)
            return wrap

        def cell_fill(grow, *fixed):
            """First widget fills the cell; trailing widgets keep their size."""
            box = QHBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(6)
            box.addWidget(grow, 1)
            for w in fixed:
                box.addWidget(w)
            wrap = QWidget(self); wrap.setLayout(box)
            return wrap

        from PyQt6.QtWidgets import QLineEdit, QPushButton

        def small_mm(top: float = 60.0) -> NoScrollDoubleSpinBox:
            sb = NoScrollDoubleSpinBox(self)
            sb.setRange(0, top); sb.setDecimals(1); sb.setSingleStep(0.5)
            sb.setMinimumWidth(84)            # room for "300,0" / "auto" + buttons
            sb.setMaximumWidth(96)            # (suffix lives in the row label)
            sb.valueChanged.connect(self._emit)
            return sb

        # ---- Patches & spacers (2-column: label | control) ----
        ps = QGroupBox(tr("Patches && spacers"), self)
        g = QGridLayout(ps)
        self.pscale = scale()
        self.sscale = scale()
        self.spacer_mode = NoScrollComboBox(self)
        for k, lbl in (("colored", tr("Coloured")), ("bw", tr("Black & white")),
                       ("none", tr("None"))):
            self.spacer_mode.addItem(lbl, k)
        self.spacer_mode.currentIndexChanged.connect(self._emit)
        self.spacer_width = mm(special_auto=True)
        self.patch_x = small_mm(); self.patch_x.setSpecialValueText(tr("auto"))
        self.patch_y = small_mm(); self.patch_y.setSpecialValueText(tr("auto"))
        self.inter_patch = mm()
        self.sig = mm()
        add_row(g, 0, tr("Patch size (mm):"),
                cell(self.patch_x, QLabel("×", self), self.patch_y),
                tip=TooltipButton(
                    tr("Patch size"),
                    tr("Width × height of each patch in millimetres. Leave at "
                       "“auto” (0) to use the instrument's recommended size "
                       "(scaled by Patch scale). A value below ~6 mm can make the "
                       "chart hard to read."), self))
        add_row(g, 1, tr("Patch scale:"), self.pscale)
        add_row(g, 2, tr("Spacers:"), self.spacer_mode)
        add_row(g, 3, tr("Spacer width:"), self.spacer_width)
        add_row(g, 4, tr("Spacer scale:"), self.sscale)
        add_row(g, 5, tr("Inter-patch gap:"), self.inter_patch)
        add_row(g, 6, tr("Strip-indicator gap:"), self.sig)
        v.addWidget(ps)

        # ---- Randomisation ----
        rg = QGroupBox(tr("Randomisation"), self)
        rgg = QGridLayout(rg)
        self.randomize_cb = QCheckBox(tr("Randomise patch order"), self)
        self.randomize_cb.setChecked(True)
        self.randomize_cb.toggled.connect(self._on_randomize_toggled)
        self.fixed_seed_cb = QCheckBox(tr("Use a fixed seed (reproducible)"), self)
        self.fixed_seed_cb.toggled.connect(self._on_fixed_seed_toggled)
        self.seed_spin = NoScrollSpinBox(self)
        self.seed_spin.setRange(0, 2_147_483_647)
        self.seed_spin.setObjectName("compact_input")
        self.seed_spin.valueChanged.connect(self._emit)
        self.new_seed_btn = QPushButton(tr("New seed"), self)
        self.new_seed_btn.setObjectName("compact_input")
        self.new_seed_btn.clicked.connect(self._on_new_seed)
        rgg.addWidget(self.randomize_cb, 0, 1)
        rgg.addWidget(self.fixed_seed_cb, 1, 1)
        rgg.addWidget(QLabel(tr("Seed:"), self), 2, 0, _Qt.AlignmentFlag.AlignRight)
        rgg.addWidget(cell_fill(self.seed_spin, self.new_seed_btn), 2, 1)
        rgg.addWidget(TooltipButton(
            tr("Randomisation"),
            tr("Patches are shuffled across the sheet so a streak of similar "
               "colours can't bias a strip — leave this on. The seed is the "
               "number that drives the shuffle: with a fixed seed the exact same "
               "layout is reproduced every build (handy for re-printing an "
               "identical chart), otherwise a fresh seed is drawn each time. "
               "Press New seed to draw one now; it's saved with the chart so you "
               "can always recreate it."), self), 2, 2)
        v.addWidget(rg)
        self._on_randomize_toggled(True)

        # ---- Strip indicators ----
        si = QGroupBox(tr("Strip indicators"), self)
        sig2 = QGridLayout(si)
        self.show_indicators = QCheckBox(tr("Show strip indicators"), self)
        self.show_indicators.setChecked(True)
        self.show_indicators.toggled.connect(self._on_show_indicators)
        sig2.addWidget(self.show_indicators, 0, 1)
        self.indicator_font = NoScrollComboBox(self)
        self._populate_font_combo(self.indicator_font)
        self.indicator_font.currentIndexChanged.connect(self._emit)
        self.indicator_size = small_mm(top=20.0)
        self.indicator_size.setSpecialValueText(tr("auto"))
        self.ind_bold = QCheckBox(tr("Bold"), self)
        self.ind_bold.toggled.connect(self._emit)
        self.ind_italic = QCheckBox(tr("Italic"), self)
        self.ind_italic.toggled.connect(self._emit)
        self._add_font_rows(sig2, 1, tr("Font:"), self.indicator_font,
                            self.indicator_size, self.ind_bold, self.ind_italic)
        self.underline_mode = NoScrollComboBox(self)
        for k, lbl in (("off", tr("Off")),
                       ("segments", tr("Coloured (5 segments)")),
                       ("cycle", tr("Coloured (per strip)")),
                       ("black", tr("Black"))):
            self.underline_mode.addItem(lbl, k)
        self.underline_mode.currentIndexChanged.connect(self._on_underline_changed)
        self.underline_thickness = small_mm(top=5.0)
        self.underline_gap = small_mm(top=20.0)
        add_row(sig2, 3, tr("Underline:"), self.underline_mode,
                tip=TooltipButton(
                    tr("Underline"),
                    tr("Draws a thin rule under each strip's letter label. "
                       "Coloured (5 segments) splits the rule into the five "
                       "ChromIQ accent colours side by side under every strip; "
                       "Coloured (per strip) instead cycles one accent colour "
                       "per strip so neighbours read apart; Black is a plain "
                       "rule. Use the thickness and distance to taste."),
                    self))
        add_row(sig2, 4, tr("Underline thickness:"), self.underline_thickness)
        add_row(sig2, 5, tr("Underline distance:"), self.underline_gap)
        v.addWidget(si)

        # ---- Page geometry ----
        pg = QGroupBox(tr("Page geometry"), self)
        gg = QGridLayout(pg)
        self.margins = {k: small_mm(top=60.0) for k in ("t", "r", "b", "l")}
        _mlabels = {"t": tr("T"), "r": tr("R"), "b": tr("B"), "l": tr("L")}
        _mbox = QVBoxLayout(); _mbox.setContentsMargins(0, 0, 0, 0); _mbox.setSpacing(4)
        for _pair in (("t", "r"), ("b", "l")):
            _hb = QHBoxLayout(); _hb.setContentsMargins(0, 0, 0, 0); _hb.setSpacing(6)
            for _k in _pair:
                _hb.addWidget(QLabel(_mlabels[_k], self))
                _hb.addWidget(self.margins[_k])
            _hb.addStretch()
            _rw = QWidget(self); _rw.setLayout(_hb)
            _mbox.addWidget(_rw)
        _margins_w = QWidget(self); _margins_w.setLayout(_mbox)
        self.dpi = NoScrollSpinBox(self); self.dpi.setRange(72, 1200)
        self.dpi.setSuffix(" dpi"); self.dpi.valueChanged.connect(self._emit)
        self.nolimit = QCheckBox(tr("Don't cap strip length"), self)
        self.nolimit.toggled.connect(self._emit)
        self.max_strip = mm(special_auto=True)
        self.offx = small_mm(top=300.0)
        self.offy = small_mm(top=300.0)
        self.strip_pat = QLineEdit(self); self.strip_pat.textChanged.connect(self._emit)
        self.patch_pat = QLineEdit(self); self.patch_pat.textChanged.connect(self._emit)
        # Clip-border width (i1/p3, clip mode only) — reserved left zone for the
        # scanner's paper clip; printtarg hard-codes 26 mm, we make it adjustable.
        self.clip_width = small_mm(top=100.0)
        self.clip_width.setMinimum(10.0)
        self.clip_width_label = QLabel(tr("Clip border width:"), self)
        self.clip_width_tip = TooltipButton(
            tr("Clip border width"),
            tr("Width of the blank zone reserved down the left edge for the "
               "clip that holds the sheet against the scanner bed. Make it wider "
               "if your clip covers more of the page; the patches start to its "
               "right. Only applies to the i1Pro / i1Pro 3 in clip-border mode "
               "(printtarg fixes this at 26 mm)."), self)
        add_row(gg, 0, tr("Margins (mm):"), _margins_w)
        gg.addWidget(self.clip_width_label, 1, 0, _Qt.AlignmentFlag.AlignRight)
        gg.addWidget(self.clip_width, 1, 1)
        gg.addWidget(self.clip_width_tip, 1, 2)
        add_row(gg, 2, tr("Resolution:"), self.dpi)
        add_row(gg, 3, tr("Max strip length:"), self.max_strip)
        add_row(gg, 4, tr("Chart offset (mm):"),
                cell(self.offx, QLabel("×", self), self.offy))
        add_row(gg, 5, tr("Strip pattern:"), self.strip_pat)
        add_row(gg, 6, tr("Patch pattern:"), self.patch_pat)
        gg.addWidget(self.nolimit, 7, 1)
        v.addWidget(pg)
        self._update_clip_visibility()

        # ---- Output ----
        og = QGroupBox(tr("Output"), self)
        ogg = QGridLayout(og)
        self.bit_depth = NoScrollComboBox(self)
        self.bit_depth.addItem(tr("8-bit"), 8)
        self.bit_depth.addItem(tr("16-bit"), 16)
        self.bit_depth.currentIndexChanged.connect(self._emit)
        self.compression = NoScrollComboBox(self)
        for k, lbl in (("lzw", "LZW"), ("zlib", "Zlib"), ("none", tr("None"))):
            self.compression.addItem(lbl, k)
        self.compression.currentIndexChanged.connect(self._emit)
        add_row(ogg, 0, tr("Bit depth:"), self.bit_depth)
        add_row(ogg, 1, tr("Compression:"), self.compression)
        v.addWidget(og)

        # ---- Sheet text ----
        st = QGroupBox(tr("Sheet text"), self)
        stg = QGridLayout(st)
        self.chart_text = QLineEdit(self)
        self.chart_text.setPlaceholderText(tr("e.g. {project} — {date}"))
        self.chart_text.textChanged.connect(self._emit)
        self.insert_token_btn = QToolButton(self)
        self.insert_token_btn.setText(tr("Insert ▾"))
        self.insert_token_btn.setObjectName("compact_input")
        self.insert_token_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        _tmenu = QMenu(self.insert_token_btn)
        for _tok, _desc in SHEET_TOKENS:
            _act = _tmenu.addAction(f"{{{_tok}}} — {_desc}")
            _act.triggered.connect(lambda _checked=False, t=_tok: self._insert_token(t))
        self.insert_token_btn.setMenu(_tmenu)
        self.text_preview = QLabel(self)
        self.text_preview.setWordWrap(True)
        self.text_preview.setStyleSheet("color: palette(mid);")
        self.chart_text_font = NoScrollComboBox(self)
        self._populate_font_combo(self.chart_text_font)
        self.chart_text_font.currentIndexChanged.connect(self._emit)
        self.chart_text_size = small_mm(top=20.0)
        self.chart_text_size.setSpecialValueText(tr("auto"))
        self.ct_bold = QCheckBox(tr("Bold"), self)
        self.ct_bold.toggled.connect(self._emit)
        self.ct_italic = QCheckBox(tr("Italic"), self)
        self.ct_italic.toggled.connect(self._emit)
        self.stamp_command = QCheckBox(tr("Stamp layout summary on the sheet"), self)
        self.stamp_command.toggled.connect(self._emit)
        add_row(stg, 0, tr("Custom text:"),
                cell_fill(self.chart_text, self.insert_token_btn),
                tip=TooltipButton(
                    tr("Sheet text"),
                    tr("Optional text printed in the bottom margin of every sheet. "
                       "Use Insert ▾ to drop in a placeholder — it's replaced with "
                       "the real value when the chart is built: {project}, {date}, "
                       "{paper}, {instrument}, {patchcount}, {pages}, {seed}, "
                       "{dpi}. The Preview line shows how it will read."), self))
        add_row(stg, 1, tr("Preview:"), self.text_preview)
        self._add_font_rows(stg, 2, tr("Font:"), self.chart_text_font,
                            self.chart_text_size, self.ct_bold, self.ct_italic)
        stg.addWidget(self.stamp_command, 4, 1)
        v.addWidget(st)
        self._update_text_preview()

        # ---- Clip-border content (i1/p3 clip mode) ----
        from ui.widgets import load_magenta_folder_icon
        self._clip_content_grp = QGroupBox(tr("Clip-border content"), self)
        ccg = QGridLayout(self._clip_content_grp)
        self.clip_content_mode = NoScrollComboBox(self)
        for k, lbl in (("off", tr("Off")), ("text", tr("Custom text")),
                       ("branding", tr("ChromIQ branding")),
                       ("notes", tr("Notes box")), ("image", tr("Imported image"))):
            self.clip_content_mode.addItem(lbl, k)
        self.clip_content_mode.currentIndexChanged.connect(self._on_clip_content_changed)
        self.clip_text = QLineEdit(self)
        self.clip_text.setPlaceholderText(tr("e.g. {project} — {date}"))
        self.clip_text.textChanged.connect(self._emit)
        self.clip_text_font = NoScrollComboBox(self)
        self._populate_font_combo(self.clip_text_font)
        self.clip_text_font.currentIndexChanged.connect(self._emit)
        self.clip_image_path = QLineEdit(self)
        self.clip_image_path.setPlaceholderText(tr("no image selected"))
        self.clip_image_path.textChanged.connect(self._emit)
        self.clip_image_browse = QPushButton(self)
        self.clip_image_browse.setIcon(load_magenta_folder_icon())
        self.clip_image_browse.setToolTip(tr("Browse for an image"))
        self.clip_image_browse.setFlat(True)
        self.clip_image_browse.setFixedSize(30, 26)
        self.clip_image_browse.clicked.connect(self._browse_clip_image)
        self.clip_dims_label = QLabel("", self)
        self.clip_dims_label.setStyleSheet("color: palette(mid);")
        self.clip_preview = QLabel(self)
        self.clip_preview.setFixedHeight(220)
        self.clip_preview.setStyleSheet("border: 1px solid palette(mid);")
        self.clip_export_btn = QPushButton(tr("Export template (PNG + PDF)…"), self)
        self.clip_export_btn.setObjectName("compact_input")
        self.clip_export_btn.clicked.connect(self._export_clip_template)
        add_row(ccg, 0, tr("Content:"), self.clip_content_mode,
                tip=TooltipButton(
                    tr("Clip-border content"),
                    tr("Fills the blank strip down the left edge that the scanner "
                       "clip reserves. Custom text and Notes box accept the same "
                       "{project}/{date}/… tokens as the sheet text; ChromIQ "
                       "branding stamps the wordmark; Imported image places a "
                       "logo. Export template gives you an exact-size PNG and PDF "
                       "to design a graphic in another tool."), self))
        add_row(ccg, 1, tr("Text:"), self.clip_text)
        add_row(ccg, 2, tr("Font:"), self.clip_text_font)
        add_row(ccg, 3, tr("Image:"), cell_fill(self.clip_image_path,
                                                 self.clip_image_browse))
        add_row(ccg, 4, tr("Clip area:"), self.clip_dims_label)
        add_row(ccg, 5, tr("Preview:"), self.clip_preview)
        ccg.addWidget(self.clip_export_btn, 6, 1)
        v.addWidget(self._clip_content_grp)

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
            from ui.widgets import load_magenta_folder_icon
            browse = QPushButton(self)
            browse.setIcon(load_magenta_folder_icon())
            browse.setToolTip(tr("Browse for a .cal file"))
            browse.setFlat(True)
            browse.setFixedSize(30, 26)        # compact, icon-only
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

        # Match the compact input styling used throughout the Manual module
        # (app QSS targets #compact_input for the slim look + white bg).
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit
        for w in self.findChildren((QAbstractSpinBox, QComboBox, QLineEdit)):
            w.setObjectName("compact_input")

        self._sync_clip_content_enabled()
        self._update_clip_visibility()

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
    def _on_instr_changed(self, *_a) -> None:
        from workflow.layout_engine import papers
        if self.instr is None:
            return
        self._loading = True
        inst = self.instr.currentData() or "i1"
        prev_paper = self.paper.currentData()
        self.paper.clear()
        for code, label, _dims in papers.list_papers(inst):
            self.paper.addItem(label, code)
        self.paper.addItem(tr("Custom…"), "__custom__")
        i = self.paper.findData(prev_paper)
        self.paper.setCurrentIndex(i if i >= 0 else 0)
        prev_mode = self.mode.currentData()
        self.mode.clear()
        for k, lbl in self.modes_for(inst):
            self.mode.addItem(lbl, k)
        j = self.mode.findData(prev_mode)
        self.mode.setCurrentIndex(j if j >= 0 else 0)
        self._loading = False
        self._on_paper_changed()

    def _update_clip_visibility(self, *_a) -> None:
        """Show the clip-border width row only for i1/p3 in clip-border mode."""
        if not hasattr(self, "clip_width"):
            return
        if self.instr is not None:
            inst = self.instr.currentData() or "i1"
            clip = inst in ("i1", "p3") and (self.mode.currentData() == "clip")
        else:
            clip = self._clip and self._inst in ("i1", "p3")
        for w in (self.clip_width_label, self.clip_width, self.clip_width_tip):
            w.setVisible(clip)
        if hasattr(self, "_clip_content_grp"):
            self._clip_content_grp.setVisible(clip)
            if clip:
                self._refresh_clip_preview()

    # ---- Clip-border content -------------------------------------------
    def _sync_clip_content_enabled(self) -> None:
        mode = self.clip_content_mode.currentData()
        text_modes = mode in ("text", "branding", "notes")
        self.clip_text.setEnabled(text_modes)
        self.clip_text_font.setEnabled(text_modes)
        self.clip_image_path.setEnabled(mode == "image")
        self.clip_image_browse.setEnabled(mode == "image")

    def _on_clip_content_changed(self, *_a) -> None:
        self._sync_clip_content_enabled()
        self._emit()

    def _browse_clip_image(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select clip-strip image"), "",
            tr("Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)"))
        if path:
            self.clip_image_path.setText(path)

    def _clip_geom_and_height(self):
        """Build the current i1/p3 Geom + paper height for the clip preview."""
        from workflow.layout_engine import instruments, papers
        if self.instr is not None:
            inst, paper, mode = self.selection()
        else:
            inst, paper, mode = self._inst, "A4", ("clip" if self._clip else "noclip")
        if inst not in ("i1", "p3"):
            return None
        try:
            geom = instruments.build(
                inst, border=min(self.margins[k].value() for k in ("t", "r", "b", "l")),
                margins=tuple(self.margins[k].value() for k in ("t", "r", "b", "l")),
                clip_border_width=self.clip_width.value(),
                nolpcbord=(mode != "clip"))
            _w, h_mm = papers.dimensions_mm(paper)
        except Exception:
            return None
        return geom, h_mm

    @staticmethod
    def _pil_to_pixmap(img):
        from PyQt6.QtGui import QImage, QPixmap
        rgb = img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3,
                      QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _refresh_clip_preview(self) -> None:
        if not hasattr(self, "clip_preview"):
            return
        from PyQt6.QtCore import Qt
        from workflow.layout_engine import geometry, raster
        gh = self._clip_geom_and_height()
        area = geometry.clip_area_mm(gh[0], gh[1]) if gh else None
        if area is None:
            self.clip_dims_label.setText(tr("—"))
            self.clip_preview.clear()
            return
        _x, _y, w_mm, h_mm = area
        dpi = int(self.dpi.value())
        wp, hp = round(w_mm * dpi / 25.4), round(h_mm * dpi / 25.4)
        self.clip_dims_label.setText(
            tr("{w:.0f} × {h:.0f} mm  ({wp} × {hp} px @ {dpi} dpi)").format(
                w=w_mm, h=h_mm, wp=wp, hp=hp, dpi=dpi))
        mode = self.clip_content_mode.currentData()
        if mode == "off":
            self.clip_preview.clear()
            return
        pdpi = 96
        pw = max(1, round(w_mm * pdpi / 25.4))
        ph = max(1, round(h_mm * pdpi / 25.4))
        img = raster.render_clip_strip(
            mode, width_px=pw, height_px=ph, dpi=pdpi,
            text=self._resolve_sample(self.clip_text.text()),
            font_family=self.clip_text_font.currentData() or "Inter",
            image_path=self.clip_image_path.text().strip())
        self.clip_preview.setPixmap(
            self._pil_to_pixmap(img).scaledToHeight(216, Qt.TransformationMode.SmoothTransformation))

    def _export_clip_template(self) -> None:
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from workflow.layout_engine import geometry, raster
        gh = self._clip_geom_and_height()
        area = geometry.clip_area_mm(gh[0], gh[1]) if gh else None
        if area is None:
            return
        _x, _y, w_mm, h_mm = area
        dpi = int(self.dpi.value())
        base, _ = QFileDialog.getSaveFileName(
            self, tr("Export clip template"), "clip-template",
            tr("Template base name"))
        if not base:
            return
        paths = raster.export_clip_template(
            base, width_px=round(w_mm * dpi / 25.4), height_px=round(h_mm * dpi / 25.4),
            width_mm=w_mm, height_mm=h_mm, dpi=dpi)
        QMessageBox.information(
            self, tr("Clip template exported"),
            tr("Wrote:\n{files}").format(files="\n".join(str(p) for p in paths)))

    def _sync_seed_enabled(self) -> None:
        on = self.randomize_cb.isChecked()
        self.fixed_seed_cb.setEnabled(on)
        self.new_seed_btn.setEnabled(on)
        self.seed_spin.setEnabled(on and self.fixed_seed_cb.isChecked())

    def _on_randomize_toggled(self, *_a) -> None:
        self._sync_seed_enabled()
        self._emit()

    def _on_fixed_seed_toggled(self, *_a) -> None:
        self._sync_seed_enabled()
        self._emit()

    def _on_new_seed(self) -> None:
        from workflow.layout_engine.permutation import pick_seed
        self.fixed_seed_cb.setChecked(True)   # a drawn seed is a reproducible one
        self.seed_spin.setValue(pick_seed())

    def _insert_token(self, token: str) -> None:
        """Drop ``{token}`` into the sheet-text field at the cursor."""
        self.chart_text.insert("{%s}" % token)
        self.chart_text.setFocus()

    def _resolve_sample(self, text: str) -> str:
        """Fill *text*'s placeholders with representative values for preview."""
        import time
        inst, paper = "i1", "A4"
        if self.instr is not None:
            inst, paper, _ = self.selection()
        ctx = {
            "project": "MyChart", "date": time.strftime("%Y-%m-%d"),
            "paper": paper, "instrument": inst, "patchcount": "600",
            "pages": str(self.get_pages()), "seed": "12345",
            "dpi": str(int(self.dpi.value())),
        }
        try:
            return text.format(**ctx)
        except (KeyError, IndexError, ValueError):
            return text       # unknown token — leave literal, as the builder does

    def _update_text_preview(self) -> None:
        if not hasattr(self, "text_preview"):
            return
        text = self.chart_text.text()
        self.text_preview.setText(self._resolve_sample(text) if text
                                  else tr("(no sheet text)"))

    def _add_font_rows(self, grid, r, label, combo, size, bold, italic) -> None:
        """Font on row *r*; Size + Bold + Italic on row *r+1*."""
        from PyQt6.QtCore import Qt
        grid.addWidget(QLabel(label, self), r, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(combo, r, 1)
        grid.addWidget(QLabel(tr("Size:"), self), r + 1, 0, Qt.AlignmentFlag.AlignRight)
        wrap = QWidget(self)
        box = QHBoxLayout(wrap); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(8)
        box.addWidget(size); box.addWidget(bold); box.addWidget(italic); box.addStretch()
        grid.addWidget(wrap, r + 1, 1)
        grid.setColumnStretch(1, 1)
        combo.currentIndexChanged.connect(
            lambda: self._update_style_enabled(combo, bold, italic))
        self._update_style_enabled(combo, bold, italic)

    def _update_style_enabled(self, combo, bold, italic) -> None:
        """Grey Bold/Italic (box + label) when the chosen font lacks the style.

        Uses the engine's own capability probe so the checkbox can't promise a
        style the renderer won't actually apply.
        """
        from workflow.layout_engine.raster import font_supports
        has_bold, has_italic = font_supports(combo.currentData() or "")
        bold.setEnabled(has_bold)
        italic.setEnabled(has_italic)
        if not has_bold:
            bold.setChecked(False)
        if not has_italic:
            italic.setChecked(False)

    @staticmethod
    def _populate_font_combo(combo) -> None:
        """Bundled fonts on top, then a separator, then all installed families."""
        for fam in ("JetBrains Mono", "Inter", "Instrument Serif"):
            combo.addItem(fam, fam)
        combo.insertSeparator(combo.count())
        try:
            from PyQt6.QtGui import QFontDatabase
            for fam in QFontDatabase.families():
                combo.addItem(fam, fam)
        except Exception:
            pass
        from PyQt6.QtWidgets import QComboBox
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)

    def _on_show_indicators(self, on: bool) -> None:
        self.indicator_font.setEnabled(on)
        self.indicator_size.setEnabled(on)
        if on:
            self._update_style_enabled(self.indicator_font,
                                       self.ind_bold, self.ind_italic)
        else:
            self.ind_bold.setEnabled(False)
            self.ind_italic.setEnabled(False)
        self._sync_underline_enabled()
        self._emit()

    def _sync_underline_enabled(self) -> None:
        on = self.show_indicators.isChecked()
        self.underline_mode.setEnabled(on)
        active = on and self.underline_mode.currentData() != "off"
        self.underline_thickness.setEnabled(active)
        self.underline_gap.setEnabled(active)

    def _on_underline_changed(self, *_a) -> None:
        self._sync_underline_enabled()
        self._emit()

    def _on_paper_changed(self, *_a) -> None:
        if self.paper is not None:
            self._custom_paper_w.setVisible(self.paper.currentData() == "__custom__")
        self._emit()

    def selection(self) -> tuple[str, str, str]:
        """(instrument, paper, mode) from the selectors (when present)."""
        if self.instr is None:
            return "i1", "A4", "default"
        paper = self.paper.currentData() or "A4"
        if paper == "__custom__":
            paper = f"{int(self.custom_w.value())}x{int(self.custom_h.value())}"
        return (self.instr.currentData() or "i1", paper,
                self.mode.currentData() or "default")

    def get_pages(self) -> int:
        return int(self.pages.value()) if self.pages is not None else 1

    def set_pages(self, n: int) -> None:
        if self.pages is not None:
            self.pages.setValue(max(1, int(n)))

    def get_recipe(self, base: LayoutRecipe | None = None) -> LayoutRecipe:
        """Build a complete recipe from the selectors (if any) + the controls."""
        from workflow.layout_engine.presets import default_recipe
        if self.instr is not None:
            inst, paper, mode = self.selection()
            r = default_recipe(inst, paper, mode=mode)
        else:
            r = base if base is not None else LayoutRecipe()
        return self.apply_to_recipe(r)

    def _emit(self, *_a) -> None:
        self._update_text_preview()
        self._refresh_clip_preview()
        if not self._loading:
            self.changed.emit()

    def set_recipe(self, r: LayoutRecipe) -> None:
        self._loading = True
        if self.instr is not None:
            ii = self.instr.findData(r.instrument)
            self.instr.setCurrentIndex(ii if ii >= 0 else 0)
            self._on_instr_changed()
            self._loading = True
            pi = self.paper.findData(r.paper)
            if pi >= 0:
                self.paper.setCurrentIndex(pi)
            else:
                from workflow.layout_engine import papers
                dims = papers.parse_custom(r.paper)
                ci = self.paper.findData("__custom__")
                if dims and ci >= 0:
                    self.paper.setCurrentIndex(ci)
                    self.custom_w.setValue(dims[0])
                    self.custom_h.setValue(dims[1])
            self._custom_paper_w.setVisible(self.paper.currentData() == "__custom__")
            mi = self.mode.findData(r.mode())
            if mi >= 0:
                self.mode.setCurrentIndex(mi)
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
        self.bit_depth.setCurrentIndex(1 if r.bit16 else 0)
        self.show_indicators.setChecked(r.show_strip_indicators)
        _fi = self.indicator_font.findData(r.indicator_font)
        self.indicator_font.setCurrentIndex(_fi if _fi >= 0 else 0)
        self.indicator_size.setValue(r.indicator_size_mm)
        self.ind_bold.setChecked(r.indicator_bold)
        self.ind_italic.setChecked(r.indicator_italic)
        _umkey = "segments" if r.underline_mode == "colored" else r.underline_mode
        _um = self.underline_mode.findData(_umkey)
        self.underline_mode.setCurrentIndex(_um if _um >= 0 else 0)
        self.underline_thickness.setValue(r.underline_thickness_mm)
        self.underline_gap.setValue(r.underline_gap_mm)
        self._sync_underline_enabled()
        self.chart_text.setText(r.chart_text)
        _ctf = self.chart_text_font.findData(r.chart_text_font)
        self.chart_text_font.setCurrentIndex(_ctf if _ctf >= 0 else 0)
        self.chart_text_size.setValue(r.chart_text_size_mm)
        self.ct_bold.setChecked(r.chart_text_bold)
        self.ct_italic.setChecked(r.chart_text_italic)
        self.stamp_command.setChecked(r.stamp_command)
        ci = self.compression.findData(r.compression)
        self.compression.setCurrentIndex(ci if ci >= 0 else 0)
        self.clip_width.setValue(r.clip_border_width_mm or 26.0)
        _cc = self.clip_content_mode.findData(r.clip_content_mode)
        self.clip_content_mode.setCurrentIndex(_cc if _cc >= 0 else 0)
        self.clip_text.setText(r.clip_text)
        _cf = self.clip_text_font.findData(r.clip_text_font)
        self.clip_text_font.setCurrentIndex(_cf if _cf >= 0 else 0)
        self.clip_image_path.setText(r.clip_image_path)
        self._sync_clip_content_enabled()
        self.randomize_cb.setChecked(r.randomize)
        _fixed = r.seed is not None
        self.fixed_seed_cb.setChecked(_fixed)
        if _fixed:
            self.seed_spin.setValue(int(r.seed))
        self._sync_seed_enabled()
        self._inst, self._clip = r.instrument, r.clip_border
        self._update_clip_visibility()
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
        r.bit16 = (self.bit_depth.currentData() == 16)
        r.show_strip_indicators = self.show_indicators.isChecked()
        r.indicator_font = self.indicator_font.currentData() or "JetBrains Mono"
        r.indicator_size_mm = self.indicator_size.value()
        r.indicator_bold = self.ind_bold.isChecked()
        r.indicator_italic = self.ind_italic.isChecked()
        r.underline_mode = self.underline_mode.currentData() or "off"
        r.underline_thickness_mm = self.underline_thickness.value()
        r.underline_gap_mm = self.underline_gap.value()
        r.chart_text = self.chart_text.text()
        r.chart_text_font = self.chart_text_font.currentData() or "Inter"
        r.chart_text_size_mm = self.chart_text_size.value()
        r.chart_text_bold = self.ct_bold.isChecked()
        r.chart_text_italic = self.ct_italic.isChecked()
        r.stamp_command = self.stamp_command.isChecked()
        r.compression = self.compression.currentData() or "lzw"
        r.clip_border_width_mm = self.clip_width.value()
        r.clip_content_mode = self.clip_content_mode.currentData() or "off"
        r.clip_text = self.clip_text.text()
        r.clip_text_font = self.clip_text_font.currentData() or "Inter"
        r.clip_image_path = self.clip_image_path.text().strip()
        r.randomize = self.randomize_cb.isChecked()
        r.seed = (int(self.seed_spin.value())
                  if r.randomize and self.fixed_seed_cb.isChecked() else None)
        return r
