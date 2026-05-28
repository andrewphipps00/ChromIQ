"""Interactive chart layout editor (Tools → Edit / create chart layout).

Loads any RGB ``.ti2`` (or starts a new chart), lets the user reorder patches,
recolour patches and spacers, preview the rendered chart, and save a new valid
``.ti2`` + page TIFF(s). All chart logic lives in :mod:`workflow.ti2_relayout`;
this module is purely the Qt front-end driving it.

The editor mutates a *device-value program* (an ordered list of 0..100 RGB
tuples). Reordering permutes it, recolouring a patch replaces an entry — exactly
the core's model. Spacers are handled two ways: a native palette (written into
the regenerated chart so printtarg renders it, contrast-optimised and readable)
and an optional per-spacer paint applied to the rendered TIFF.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPoint, QRect
from PyQt6.QtGui import (
    QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QColorDialog, QDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QRadioButton, QScrollArea, QSlider, QSplitter,
    QVBoxLayout, QWidget,
)

from core.logger import get_logger
from ui.styles import SPEC_AMBER, SPEC_MAGENTA, TAB_COLORS
from ui.widgets import (
    NoScrollComboBox, NoScrollSpinBox, open_dir_dialog, open_file_dialog,
)
from workflow import ti2_relayout as R

log = get_logger(__name__)

_SWATCH = 46  # grid swatch px

# printtarg -i codes the editor offers, with friendly labels.
_INSTRUMENTS = [("i1", "i1Pro / i1Pro2 / i1Pro3(+)"), ("CM", "ColorMunki / i1Studio")]
_PAPERS = ["A4", "A4R", "A3", "A2", "Letter", "LetterR", "Legal", "4x6", "11x17"]


def _qcolor(rgb: tuple[float, float, float]) -> QColor:
    return QColor(*(max(0, min(255, round(v / 100 * 255))) for v in rgb))


def _to100(c: QColor) -> tuple[float, float, float]:
    return (c.red() / 255 * 100, c.green() / 255 * 100, c.blue() / 255 * 100)


def _swatch_icon(rgb: tuple[float, float, float], size: int = _SWATCH) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(_qcolor(rgb))
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Background regeneration (printtarg runs off the GUI thread)
# ---------------------------------------------------------------------------
class _RegenWorker(QThread):
    done = pyqtSignal(object)  # RegenResult | Exception

    def __init__(self, spec, program, out_dir, bin_dir, palette):
        super().__init__()
        self._args = (spec, program, out_dir, bin_dir, palette)

    def run(self) -> None:
        spec, program, out_dir, bin_dir, palette = self._args
        try:
            self.done.emit(R.regenerate(spec, program, out_dir, bin_dir,
                                        spacer_palette=palette))
        except Exception as exc:  # surfaced to the user, not swallowed
            log.exception("relayout regenerate failed")
            self.done.emit(exc)


# ---------------------------------------------------------------------------
# Patch grid — reliable drag-reorder in IconMode
# ---------------------------------------------------------------------------
class _ReorderListWidget(QListWidget):
    """QListWidget that reliably commits IconMode drag-reorders.

    Qt's default IconMode + InternalMove often "snaps back" the dragged items
    instead of moving them. We compute the target row from the drop position
    and rebuild the selection at the new location ourselves; the ``reordered``
    signal fires after every successful reorder so the dialog can renumber.
    """

    reordered = pyqtSignal()

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.source() is not self:
            event.ignore()
            return
        drop = event.position().toPoint()
        target_idx = self.indexAt(drop)
        if target_idx.isValid():
            target_row = target_idx.row()
            # Drop on the right half → insert AFTER that item.
            if drop.x() > self.visualRect(target_idx).center().x():
                target_row += 1
        else:
            target_row = self.count()           # empty space → append

        source_rows = sorted({self.row(it) for it in self.selectedItems()})
        if not source_rows:
            event.ignore()
            return

        # Adjust for the items that will be removed from before the target.
        adjusted = target_row - sum(1 for r in source_rows if r < target_row)

        taken = [self.takeItem(r) for r in reversed(source_rows)][::-1]
        adjusted = max(0, min(adjusted, self.count()))
        self.clearSelection()
        for off, it in enumerate(taken):
            self.insertItem(adjusted + off, it)
            self.item(adjusted + off).setSelected(True)
        event.acceptProposedAction()
        self.reordered.emit()


# ---------------------------------------------------------------------------
# Clickable preview (for per-spacer selection in spacer mode)
# ---------------------------------------------------------------------------
class _PreviewLabel(QLabel):
    """Preview QLabel supporting single click + click-drag marquee.

    ``clicked`` fires on a release where the mouse barely moved (treated as a
    plain click). ``marquee_finished`` fires when the press-and-drag covered
    more than a few pixels (treated as a selection rectangle). Both positions
    are in label coordinates; the dialog maps them to image pixels.
    """

    clicked = pyqtSignal(QPoint, object)            # pos, keyboard modifiers
    marquee_finished = pyqtSignal(QRect, object)    # rect, keyboard modifiers
    _CLICK_PX = 4               # movement under this is still a click

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._press: QPoint | None = None
        self._drag_rect: QRect | None = None
        self._base_pixmap: QPixmap | None = None  # what to repaint each frame

    def set_base_pixmap(self, pm: QPixmap | None) -> None:
        """Stash the pixmap the dialog wants shown; we redraw it + the marquee."""
        self._base_pixmap = pm
        if pm is not None:
            self.setPixmap(pm)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()
            self._drag_rect = None
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._press is not None:
            cur = ev.position().toPoint()
            self._drag_rect = QRect(self._press, cur).normalized()
            self._redraw_with_marquee()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self._press is not None:
            end = ev.position().toPoint()
            mods = ev.modifiers()
            dx = abs(end.x() - self._press.x())
            dy = abs(end.y() - self._press.y())
            if dx <= self._CLICK_PX and dy <= self._CLICK_PX:
                self.clicked.emit(self._press, mods)
            else:
                self.marquee_finished.emit(
                    QRect(self._press, end).normalized(), mods)
            self._press = None
            self._drag_rect = None
            if self._base_pixmap is not None:
                self.setPixmap(self._base_pixmap)
        super().mouseReleaseEvent(ev)

    def _redraw_with_marquee(self) -> None:
        if self._base_pixmap is None or self._drag_rect is None:
            return
        # Build a composite of the base + a translucent marquee rectangle.
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        # Centre the base pixmap in the label area, same as QLabel does.
        bx = (self.width() - self._base_pixmap.width()) // 2
        by = (self.height() - self._base_pixmap.height()) // 2
        p.drawPixmap(bx, by, self._base_pixmap)
        p.setPen(QPen(QColor(255, 230, 0), 1, Qt.PenStyle.DashLine))
        p.setBrush(QColor(255, 230, 0, 50))
        p.drawRect(self._drag_rect)
        p.end()
        self.setPixmap(pm)


# ---------------------------------------------------------------------------
# New-chart setup
# ---------------------------------------------------------------------------
class _NewChartDialog(QDialog):
    """Pick instrument / paper and either a blank canvas or a targen seed."""

    def __init__(self, bin_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New chart")
        self._bin_dir = bin_dir
        self.result_spec: R.ChartSpec | None = None
        self.result_program: list[tuple] | None = None

        lay = QVBoxLayout(self)
        grid = QGridLayout()
        grid.addWidget(QLabel("Instrument:"), 0, 0)
        self._instr = NoScrollComboBox(self)
        for code, label in _INSTRUMENTS:
            self._instr.addItem(label, code)
        grid.addWidget(self._instr, 0, 1)
        grid.addWidget(QLabel("Paper:"), 1, 0)
        self._paper = NoScrollComboBox(self)
        self._paper.addItems(_PAPERS)
        grid.addWidget(self._paper, 1, 1)
        lay.addLayout(grid)

        self._blank = QRadioButton("Blank canvas (add patches by hand)", self)
        self._seed = QRadioButton("Seed from targen (optimised patch set)", self)
        self._seed.setChecked(True)
        lay.addWidget(self._seed)
        seed_row = QHBoxLayout()
        seed_row.addSpacing(22)
        seed_row.addWidget(QLabel("Patches:"))
        self._count = NoScrollSpinBox(self)
        self._count.setRange(8, 4000)
        self._count.setValue(200)
        seed_row.addWidget(self._count)
        seed_row.addStretch(1)
        lay.addLayout(seed_row)
        lay.addWidget(self._blank)
        self._seed.toggled.connect(lambda on: self._count.setEnabled(on))

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Create", self)
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def _on_ok(self) -> None:
        spec = R.ChartSpec.new(self._instr.currentData(),
                               self._paper.currentText())
        program: list[tuple] = []
        if self._seed.isChecked():
            try:
                program = R.seed_from_targen(self._bin_dir, self._count.value())
            except Exception as exc:
                QMessageBox.warning(self, "targen failed", str(exc))
                return
        self.result_spec = spec
        self.result_program = program
        self.accept()


# ---------------------------------------------------------------------------
# Main editor
# ---------------------------------------------------------------------------
class Ti2RelayoutDialog(QDialog):
    def __init__(self, runner, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._bin_dir = Path(settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        self.setWindowTitle("Edit / create chart layout")
        self.resize(1060, 720)
        self.setMinimumSize(880, 560)

        self._spec: R.ChartSpec | None = None
        self._palette: list[tuple] | None = None       # native spacer palette
        self._regen: R.RegenResult | None = None
        self._page = 0                                  # previewed page index
        self._spacers: list = []                        # current-page Spacer list
        self._sel_spacers: set[int] = set()             # current-page selection
        self._paint: dict[tuple[int, int], tuple] = {}  # (page, spacer idx) -> rgb
        self._preview_tmp = tempfile.TemporaryDirectory()
        self._worker: _RegenWorker | None = None
        self._preview_scale = 1.0
        self._preview_pending_save: Path | None = None
        self._swatch_size = _SWATCH                     # current grid icon size
        self._base_pixmap: QPixmap | None = None        # preview without overlay

        self._build_ui()
        self._refresh_enabled()

    # -- UI -----------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # Source row
        src = QHBoxLayout()
        load_btn = QPushButton("Load .ti2…", self)
        load_btn.clicked.connect(self._load_ti2)
        new_btn = QPushButton("New chart…", self)
        new_btn.clicked.connect(self._new_chart)
        src.addWidget(load_btn)
        src.addWidget(new_btn)
        src.addStretch(1)
        self._info = QLabel("No chart loaded.", self)
        src.addWidget(self._info)
        outer.addLayout(src)

        split = QSplitter(Qt.Orientation.Horizontal, self)

        # Left: swatch-size slider + patch grid
        left = QWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(QLabel("Swatch size:"))
        self._size_slider = QSlider(Qt.Orientation.Horizontal, left)
        self._size_slider.setRange(24, 96)
        self._size_slider.setValue(_SWATCH)
        self._size_slider.valueChanged.connect(self._set_swatch_size)
        top.addWidget(self._size_slider, 1)
        lv.addLayout(top)

        self._grid = _ReorderListWidget(left)
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setFlow(QListWidget.Flow.LeftToRight)
        self._grid.setWrapping(True)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        # Static + InternalMove + MoveAction = reorder by shifting items (no
        # free-floating drops on empty space; an empty-area drop appends).
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._grid.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setIconSize(QSize(_SWATCH, _SWATCH))
        self._grid.setSpacing(3)
        # Menlo matches the rest of ChromIQ's small/mono labels (e.g. the TIFF
        # preview overlay, the "Feed the beast" subtext).
        self._grid.setStyleSheet(
            "QListWidget { font-family: Menlo; font-size: 10px; }"
        )
        # keep numeric labels correct after a drag-reorder (explicit signal
        # from the subclass, plus model().rowsMoved for any other code path)
        self._grid.reordered.connect(self._renumber)
        self._grid.model().rowsMoved.connect(lambda *a: self._renumber())
        # arrow-key reorder for the selection (Alt = "move", plain arrows still
        # navigate the cursor as usual)
        for keys, fn in (
            (("Alt+Up", "Alt+Left"),    self._move_up),
            (("Alt+Down", "Alt+Right"), self._move_down),
            (("Alt+Home",),             self._move_front),
            (("Alt+End",),              self._move_back),
        ):
            for k in keys:
                QShortcut(QKeySequence(k), self._grid, activated=fn)
        lv.addWidget(self._grid, 1)
        split.addWidget(left)

        # Middle: preview + page navigation
        mid = QWidget(self)
        midv = QVBoxLayout(mid)
        midv.setContentsMargins(0, 0, 0, 0)
        midv.setSpacing(6)
        self._preview = _PreviewLabel(self)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setText("Preview will appear here.")
        self._preview.clicked.connect(self._on_preview_click)
        self._preview.marquee_finished.connect(self._on_marquee)
        scroll = QScrollArea(mid)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._preview)
        midv.addWidget(scroll, 1)
        self._page_bar = QWidget(mid)
        pbl = QHBoxLayout(self._page_bar)
        pbl.setContentsMargins(0, 0, 0, 0)
        self._prev_btn = QPushButton("◀ Page", self._page_bar)
        self._next_btn = QPushButton("Page ▶", self._page_bar)
        self._page_label = QLabel("", self._page_bar)
        self._prev_btn.clicked.connect(lambda: self._show_page(self._page - 1))
        self._next_btn.clicked.connect(lambda: self._show_page(self._page + 1))
        pbl.addStretch(1)
        pbl.addWidget(self._prev_btn)
        pbl.addWidget(self._page_label)
        pbl.addWidget(self._next_btn)
        pbl.addStretch(1)
        self._page_bar.setVisible(False)
        midv.addWidget(self._page_bar)
        split.addWidget(mid)
        split.setSizes([520, 520])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        # Right: controls — OUTSIDE the splitter so it sits flush at the right
        # edge with no jumpy "phantom" pane between it and the window border.
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(split, 1)
        body.addWidget(self._build_controls(), 0)
        outer.addLayout(body, 1)

        self._status = QLabel("", self)
        self._status.setStyleSheet("color: #888;")
        outer.addWidget(self._status)

    def _build_controls(self) -> QWidget:
        panel = QWidget(self)
        panel.setFixedWidth(230)
        # Container stylesheet — the only reliable way to shrink button height
        # ([[feedback_qt_button_sizing]]: setMinimumHeight on the button itself
        # is overridden by Qt's compound-widget CSS).
        panel.setStyleSheet("""
            QPushButton { padding: 3px 8px; min-height: 22px; font-size: 11px; }
            QGroupBox  { font-size: 12px; }
            QLabel     { font-size: 11px; }
            QRadioButton { font-size: 12px; }
        """)
        v = QVBoxLayout(panel)
        v.setContentsMargins(4, 0, 0, 0)
        v.setSpacing(8)

        # Target mode
        mode_box = QGroupBox("Edit target", panel)
        mb = QVBoxLayout(mode_box)
        self._mode_patches = QRadioButton("Patches", mode_box)
        self._mode_spacers = QRadioButton("Spacers", mode_box)
        self._mode_patches.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._mode_patches)
        self._mode_group.addButton(self._mode_spacers)
        self._mode_patches.toggled.connect(self._on_mode_change)
        mb.addWidget(self._mode_patches)
        mb.addWidget(self._mode_spacers)
        v.addWidget(mode_box)

        # Patch controls
        self._patch_box = QGroupBox("Patches", panel)
        pb = QVBoxLayout(self._patch_box)
        set_col = QPushButton("Set colour of selection…", self._patch_box)
        set_col.clicked.connect(self._set_patch_colour)
        pb.addWidget(set_col)
        tr = QHBoxLayout()
        dark = QPushButton("Darken 10%", self._patch_box)
        light = QPushButton("Lighten 10%", self._patch_box)
        dark.clicked.connect(lambda: self._transform_selection(0.9))
        light.clicked.connect(lambda: self._transform_selection(1.0 / 0.9))
        tr.addWidget(dark)
        tr.addWidget(light)
        pb.addLayout(tr)
        addrem = QHBoxLayout()
        add_b = QPushButton("Add…", self._patch_box)
        add_b.setToolTip("Add a new patch with a chosen colour")
        add_b.clicked.connect(self._add_patch)
        rem_b = QPushButton("Remove", self._patch_box)
        rem_b.setToolTip("Remove the selected patches")
        rem_b.clicked.connect(self._remove_selected_patches)
        addrem.addWidget(add_b)
        addrem.addWidget(rem_b)
        pb.addLayout(addrem)
        reorder_lbl = QLabel("Reorder (drag, Alt+arrows, or):", self._patch_box)
        reorder_lbl.setWordWrap(True)
        pb.addWidget(reorder_lbl)
        order = QGridLayout()
        order.setHorizontalSpacing(6)
        order.setVerticalSpacing(4)
        # 2×2 grid — "FRONT/UP/DOWN/BACK" in caps doesn't fit a 4-wide row at
        # 230 px panel width, so split into two rows of two.
        btns = (("First", self._move_front, 0, 0), ("Up",   self._move_up,    0, 1),
                ("Last",  self._move_back,  1, 0), ("Down", self._move_down,  1, 1))
        for label, fn, r, c in btns:
            b = QPushButton(label, self._patch_box)
            b.clicked.connect(fn)
            order.addWidget(b, r, c)
        pb.addLayout(order)
        v.addWidget(self._patch_box)

        # Spacer controls
        self._spacer_box = QGroupBox("Spacers", panel)
        sb = QVBoxLayout(self._spacer_box)
        pal_lbl = QLabel(
            "Native palette (printtarg picks one per gap for best contrast):",
            self._spacer_box)
        pal_lbl.setWordWrap(True)
        sb.addWidget(pal_lbl)
        self._palette_row = QHBoxLayout()
        sb.addLayout(self._palette_row)
        sb.addSpacing(10)
        reset = QPushButton("Reset palette", self._spacer_box)
        reset.setToolTip("Reset the spacer palette to printtarg's defaults")
        reset.clicked.connect(self._reset_palette)
        sb.addWidget(reset)
        sb.addWidget(self._hline())
        paint_lbl = QLabel(
            "Per-spacer paint: click a spacer (drag for a marquee). "
            "Hold Alt to remove from selection. Selected = yellow outline.",
            self._spacer_box)
        paint_lbl.setWordWrap(True)
        sb.addWidget(paint_lbl)
        paint_row = QHBoxLayout()
        paint = QPushButton("Paint selected…", self._spacer_box)
        paint.setToolTip("Paint the spacers selected in the preview")
        paint.clicked.connect(self._paint_spacers)
        clear = QPushButton("Clear", self._spacer_box)
        clear.setToolTip("Clear the spacer selection")
        clear.clicked.connect(self._clear_spacer_selection)
        paint_row.addWidget(paint)
        paint_row.addWidget(clear)
        sb.addLayout(paint_row)
        self._spacer_box.setVisible(False)
        v.addWidget(self._spacer_box)

        # Actions
        v.addStretch(1)

        # "What a mess!" flourish — same recipe as tab_print.py's "Feed the
        # beast" block (no-title groupbox, Georgia headline with amber italic
        # bang, Menlo subtext, 5-colour bar).
        mess_box = QGroupBox(panel)
        mess_box.setStyleSheet(
            "QGroupBox { margin-top: 0px; padding: 12px 6px 10px 6px; }"
        )
        ml = QVBoxLayout(mess_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        mess_head = QLabel(
            f'What a mess<span style="color: {SPEC_MAGENTA}; '
            f'font-style: italic;">!</span>',
            mess_box,
        )
        mess_head.setTextFormat(Qt.TextFormat.RichText)
        mess_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mess_head.setStyleSheet(
            "background: transparent;"
            " font-family: Georgia; font-size: 22px;"
        )
        ml.addWidget(mess_head)
        mess_sub = QLabel("Time to tidy up.", mess_box)
        mess_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mess_sub.setStyleSheet(
            "color: #808080; background: transparent;"
            " font-family: Menlo; font-size: 9px; font-weight: 300;"
        )
        ml.addWidget(mess_sub)
        mess_bar = QHBoxLayout()
        mess_bar.setContentsMargins(0, 6, 0, 0)
        mess_bar.setSpacing(0)
        mess_bar.addStretch()
        for _color in TAB_COLORS:
            seg = QFrame(mess_box)
            seg.setFixedSize(22, 2)
            seg.setStyleSheet(f"background-color: {_color}; border: none;")
            mess_bar.addWidget(seg)
        mess_bar.addStretch()
        ml.addLayout(mess_bar)
        v.addWidget(mess_box)

        self._preview_btn = QPushButton("Update preview", panel)
        self._preview_btn.clicked.connect(lambda: self._regenerate(save_to=None))
        v.addWidget(self._preview_btn)
        self._save_btn = QPushButton("Save As…", panel)
        self._save_btn.clicked.connect(self._save_as)
        v.addWidget(self._save_btn)
        return panel

    def _pick_color(self, initial: QColor, title: str) -> QColor:
        """Use Qt's non-native colour dialog so the HTML/hex field, RGB / HSV
        spinners and the basic-colour swatches are all available (the macOS
        native picker hides the hex field on older systems)."""
        return QColorDialog.getColor(
            initial, self, title,
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )

    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        return f

    # -- source -------------------------------------------------------------
    def _load_ti2(self) -> None:
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        path = open_file_dialog(self, "Load .ti2 chart",
                                "Argyll chart (*.ti2)", start_dir=start)
        if not path:
            return
        try:
            spec = R.ChartSpec.from_ti2(Path(path))
            program = R.default_program(spec)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load chart", str(exc))
            return
        self._set_chart(spec, program, f"Loaded {Path(path).name}")

    def _new_chart(self) -> None:
        dlg = _NewChartDialog(self._bin_dir, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_spec is None:
            return
        self._set_chart(dlg.result_spec, dlg.result_program or [], "New chart")

    def _set_chart(self, spec: R.ChartSpec, program: list[tuple], note: str) -> None:
        self._spec = spec
        self._palette = None
        self._paint.clear()
        self._sel_spacers.clear()
        self._populate_grid(program)
        self._build_palette_row()
        self._info.setText(
            f"{note} — {len(program)} patches, -i{spec.instrument_flag} "
            f"-p{spec.paper_flag}")
        self._status.setText("Edit, then Update preview / Save As.")
        self._refresh_enabled()

    # -- patch grid ---------------------------------------------------------
    def _populate_grid(self, program: list[tuple]) -> None:
        self._grid.clear()
        for i, rgb in enumerate(program, start=1):
            it = QListWidgetItem(_swatch_icon(rgb, self._swatch_size), str(i))
            it.setData(Qt.ItemDataRole.UserRole, tuple(rgb))
            it.setToolTip(f"#{i}  RGB {tuple(round(v) for v in rgb)}")
            self._grid.addItem(it)

    def _renumber(self) -> None:
        """Refresh #1..#N labels + tooltips (after drag-reorder or add/remove)."""
        for i in range(self._grid.count()):
            it = self._grid.item(i)
            rgb = it.data(Qt.ItemDataRole.UserRole)
            it.setText(str(i + 1))
            it.setToolTip(f"#{i + 1}  RGB {tuple(round(v) for v in rgb)}")

    def _set_swatch_size(self, size: int) -> None:
        """Resize the grid swatches; rebuild icons so they stay crisp."""
        self._swatch_size = size
        self._grid.setIconSize(QSize(size, size))
        for i in range(self._grid.count()):
            it = self._grid.item(i)
            it.setIcon(_swatch_icon(it.data(Qt.ItemDataRole.UserRole), size))

    def _add_patch(self) -> None:
        c = self._pick_color(QColor(128, 128, 128), "Add patch — pick colour")
        if not c.isValid():
            return
        rgb = _to100(c)
        it = QListWidgetItem(_swatch_icon(rgb, self._swatch_size), "")
        it.setData(Qt.ItemDataRole.UserRole, rgb)
        self._grid.addItem(it)
        self._renumber()
        self._status.setText("Patch added. Update preview to apply.")

    def _remove_selected_patches(self) -> None:
        rows = sorted((self._grid.row(it) for it in self._grid.selectedItems()),
                      reverse=True)
        if not rows:
            self._status.setText("Select one or more patches first.")
            return
        for r in rows:
            self._grid.takeItem(r)
        self._renumber()
        self._status.setText(f"Removed {len(rows)} patch(es).")

    def _program_from_grid(self) -> list[tuple]:
        return [self._grid.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._grid.count())]

    def _set_patch_colour(self) -> None:
        items = self._grid.selectedItems()
        if not items:
            self._status.setText("Select one or more patches first.")
            return
        start = _qcolor(items[0].data(Qt.ItemDataRole.UserRole))
        c = self._pick_color(start, "Patch colour")
        if not c.isValid():
            return
        rgb = _to100(c)
        for it in items:
            it.setData(Qt.ItemDataRole.UserRole, rgb)
            it.setIcon(_swatch_icon(rgb))
        self._status.setText(f"Set {len(items)} patch(es). Update preview to apply.")

    def _transform_selection(self, factor: float) -> None:
        items = self._grid.selectedItems()
        if not items:
            self._status.setText("Select one or more patches first.")
            return
        for it in items:
            rgb = it.data(Qt.ItemDataRole.UserRole)
            new = tuple(max(0.0, min(100.0, v * factor)) for v in rgb)
            it.setData(Qt.ItemDataRole.UserRole, new)
            it.setIcon(_swatch_icon(new))

    def _selected_rows(self) -> list[int]:
        return sorted(self._grid.row(it) for it in self._grid.selectedItems())

    def _move(self, rows: list[int], dest: int) -> None:
        if not rows:
            return
        taken = [self._grid.takeItem(r) for r in reversed(rows)][::-1]
        for off, it in enumerate(taken):
            self._grid.insertItem(dest + off, it)
        for off in range(len(taken)):
            self._grid.item(dest + off).setSelected(True)

    def _move_up(self) -> None:
        rows = self._selected_rows()
        if rows and rows[0] > 0:
            self._move(rows, rows[0] - 1)

    def _move_down(self) -> None:
        rows = self._selected_rows()
        if rows and rows[-1] < self._grid.count() - 1:
            self._move(rows, rows[0] + 1)

    def _move_front(self) -> None:
        self._move(self._selected_rows(), 0)

    def _move_back(self) -> None:
        rows = self._selected_rows()
        self._move(rows, self._grid.count() - len(rows))

    # -- mode + spacer palette ---------------------------------------------
    def _on_mode_change(self) -> None:
        patches = self._mode_patches.isChecked()
        self._patch_box.setVisible(patches)
        self._spacer_box.setVisible(not patches)
        # selection outlines only show in Spacers mode
        self._refresh_preview()

    def _build_palette_row(self) -> None:
        while self._palette_row.count():
            w = self._palette_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        pal = self._current_palette()
        # entries 1..6 are the editable colour spacers (0=white, 7=black fixed)
        self._palette_row.setSpacing(4)
        for idx in range(1, 7):
            btn = QPushButton(self._spacer_box)
            # 6×30 + 5×4 = 200 px — fills the spacer groupbox content width
            # (~210 px) without overflowing.
            btn.setFixedSize(30, 30)
            # Defensive: cancel the panel-wide QPushButton padding/min-height
            # so each swatch is exactly 22×22 (else the panel CSS makes them
            # wider than their fixed size and they overlap their neighbours).
            btn.setStyleSheet(
                f"background:{_qcolor(pal[idx]).name()};"
                " border: 1px solid #888; border-radius: 2px;"
                " padding: 0; margin: 0;"
                " min-width: 0; min-height: 0;"
            )
            btn.setToolTip(f"Spacer palette colour #{idx} — click to edit")
            btn.clicked.connect(lambda _=False, i=idx: self._edit_palette(i))
            self._palette_row.addWidget(btn)
        self._palette_row.addStretch(1)

    def _current_palette(self) -> list[tuple]:
        from workflow.i1profiler_import import _DENSITY_EXTREMES
        return list(self._palette) if self._palette else [tuple(c) for c in _DENSITY_EXTREMES]

    def _edit_palette(self, idx: int) -> None:
        pal = self._current_palette()
        c = self._pick_color(_qcolor(pal[idx]), "Spacer palette colour")
        if not c.isValid():
            return
        pal[idx] = _to100(c)
        self._palette = pal
        self._build_palette_row()
        self._status.setText("Palette changed. Update preview to apply.")

    def _reset_palette(self) -> None:
        self._palette = None
        self._build_palette_row()
        self._status.setText("Palette reset to default.")

    # -- regeneration / preview --------------------------------------------
    def _regenerate(self, save_to: Path | None) -> None:
        if self._spec is None or self._grid.count() == 0:
            self._status.setText("Load or create a chart first.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        out_dir = save_to or Path(self._preview_tmp.name)
        # fresh dir for each preview render
        if save_to is None:
            for p in Path(self._preview_tmp.name).glob("*"):
                if p.is_file():
                    p.unlink()
        self._preview_pending_save = save_to
        self._set_busy(True)
        self._status.setText("Rendering with printtarg…")
        self._worker = _RegenWorker(
            self._spec, self._program_from_grid(), out_dir, self._bin_dir,
            tuple(self._palette) if self._palette else None)
        self._worker.done.connect(self._on_regen_done)
        self._worker.start()

    def _on_regen_done(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Render failed", str(result))
            self._status.setText("Render failed.")
            return
        self._regen = result
        if self._page >= len(result.tiffs):
            self._page = 0
        self._show_page(self._page)
        if self._preview_pending_save is not None:
            self._status.setText(f"Saved to {self._preview_pending_save}")
        else:
            pages = len(result.tiffs)
            extra = f" across {pages} pages" if pages > 1 else ""
            self._status.setText(
                f"{len(self._spacers)} spacers on this page{extra}. "
                "Spacers mode → click to select, then Paint.")

    def _show_page(self, page: int) -> None:
        """Switch the preview to ``page``: re-detect its spacers, redraw."""
        if self._regen is None:
            return
        n = len(self._regen.tiffs)
        self._page = max(0, min(page, n - 1))
        try:
            tif = self._regen.tiffs[self._page]
            bw  = self._regen.bw_tiffs[self._page]
            mask = R.spacer_mask(tif, bw)
            # Pass the deliverable array so wide horizontal bands get split
            # into per-strip cells by colour discontinuity (otherwise adjacent
            # strips' spacers in the same row are one connected component and
            # a click selects the whole row).
            ref_arr = R._imread_rgb(tif)
            self._spacers = R.segment_spacers(mask, page=self._page,
                                              ref_arr=ref_arr)
        except Exception:
            self._spacers = []
        self._sel_spacers.clear()
        self._update_page_nav()
        self._apply_paint_and_show()

    def _update_page_nav(self) -> None:
        n = len(self._regen.tiffs) if self._regen else 0
        self._page_bar.setVisible(n > 1)
        if n > 1:
            self._page_label.setText(f"Page {self._page + 1}/{n}")
            self._prev_btn.setEnabled(self._page > 0)
            self._next_btn.setEnabled(self._page < n - 1)

    def _apply_paint_and_show(self) -> None:
        if self._regen is None:
            return
        tif = self._regen.tiffs[self._page]
        show_path = tif
        page_paint = {idx: rgb for (pg, idx), rgb in self._paint.items()
                      if pg == self._page}
        if page_paint and self._spacers:
            from collections import defaultdict
            groups: dict[tuple, list] = defaultdict(list)
            for idx, rgb in page_paint.items():
                if idx < len(self._spacers):
                    groups[tuple(round(v) for v in rgb)].append(self._spacers[idx])
            painted = Path(self._preview_tmp.name) / "_painted.tif"
            src = tif
            for rgb, sps in groups.items():
                R.recolor_spacers(src, sps, rgb, painted)
                src = painted
            show_path = painted
        self._show_image(show_path)

    def _show_image(self, path: Path) -> None:
        pm = QPixmap(str(path))
        if pm.isNull():
            return
        target = self._preview.size()
        scaled = pm.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._preview_scale = scaled.width() / pm.width() if pm.width() else 1.0
        self._preview_orig = pm.size()
        self._base_pixmap = scaled
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Redraw the preview from the cached base pixmap, overlaying yellow
        outlines on currently-selected spacers when in Spacers mode. Hands
        the composited pixmap to the preview label so the marquee can repaint
        on top of it during drag."""
        if self._base_pixmap is None:
            return
        pm = QPixmap(self._base_pixmap)
        if (self._mode_spacers.isChecked()
                and self._sel_spacers and self._spacers):
            p = QPainter(pm)
            p.setPen(QPen(QColor(255, 230, 0), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            s = self._preview_scale
            for i in self._sel_spacers:
                if 0 <= i < len(self._spacers):
                    x0, y0, x1, y1 = self._spacers[i].bbox
                    p.drawRect(int(x0 * s) - 1, int(y0 * s) - 1,
                               int((x1 - x0 + 1) * s) + 2,
                               int((y1 - y0 + 1) * s) + 2)
            p.end()
        self._preview.set_base_pixmap(pm)

    def _label_to_image(self, p: QPoint) -> tuple[float, float] | None:
        """Map a label-coord click to deliverable image pixels (or None)."""
        if self._base_pixmap is None or self._preview_scale <= 0:
            return None
        off_x = (self._preview.width() - self._base_pixmap.width()) / 2
        off_y = (self._preview.height() - self._base_pixmap.height()) / 2
        return (p.x() - off_x) / self._preview_scale, (p.y() - off_y) / self._preview_scale

    def _on_marquee(self, rect, mods) -> None:
        """Add (or, with Alt, subtract) every spacer that intersects the marquee."""
        if not self._mode_spacers.isChecked() or not self._spacers:
            return
        tl = self._label_to_image(rect.topLeft())
        br = self._label_to_image(rect.bottomRight())
        if tl is None or br is None:
            return
        ix0, iy0 = tl
        ix1, iy1 = br
        is_alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        touched: list[int] = []
        for i, sp in enumerate(self._spacers):
            sx0, sy0, sx1, sy1 = sp.bbox
            if sx1 < ix0 or sx0 > ix1 or sy1 < iy0 or sy0 > iy1:
                continue
            touched.append(i)
        if is_alt:
            removed = sum(1 for i in touched if i in self._sel_spacers)
            self._sel_spacers.difference_update(touched)
            self._refresh_preview()
            self._status.setText(
                f"Marquee removed {removed} spacer(s); "
                f"{len(self._sel_spacers)} still selected.")
        else:
            added = sum(1 for i in touched if i not in self._sel_spacers)
            self._sel_spacers.update(touched)
            self._refresh_preview()
            self._status.setText(
                f"Marquee added {added} spacer(s); "
                f"{len(self._sel_spacers)} total selected.")

    def _clear_spacer_selection(self) -> None:
        if not self._sel_spacers:
            return
        self._sel_spacers.clear()
        self._refresh_preview()
        self._status.setText("Spacer selection cleared.")

    def _on_preview_click(self, pos: QPoint, mods) -> None:
        if not self._mode_spacers.isChecked() or not self._spacers:
            return
        mapped = self._label_to_image(pos)
        if mapped is None:
            return
        ix, iy = mapped
        hit = self._spacer_at(ix, iy)
        if hit is None:
            return
        # Plain click toggles; Alt+click explicitly removes (matches macOS
        # subtract-from-selection convention).
        is_alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if is_alt:
            self._sel_spacers.discard(hit)
        elif hit in self._sel_spacers:
            self._sel_spacers.discard(hit)
        else:
            self._sel_spacers.add(hit)
        self._refresh_preview()
        self._status.setText(f"{len(self._sel_spacers)} spacer(s) selected.")

    def _spacer_at(self, ix: float, iy: float) -> int | None:
        for i, sp in enumerate(self._spacers):
            x0, y0, x1, y1 = sp.bbox
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                return i
        return None

    def _paint_spacers(self) -> None:
        if not self._sel_spacers:
            self._status.setText("Click spacers in the preview to select them first.")
            return
        c = self._pick_color(QColor(128, 128, 128), "Spacer colour")
        if not c.isValid():
            return
        rgb = (c.red(), c.green(), c.blue())
        for i in self._sel_spacers:
            self._paint[(self._page, i)] = rgb
        self._apply_paint_and_show()
        self._status.setText(
            f"Painted {len(self._sel_spacers)} spacer(s) on page {self._page + 1}.")

    # -- save ---------------------------------------------------------------
    def _save_as(self) -> None:
        if self._spec is None or self._grid.count() == 0:
            return
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        out_dir = open_dir_dialog(self, "Choose output folder", start_dir=start)
        if not out_dir:
            return
        name, ok = self._ask_name()
        if not ok or not name:
            return
        target = Path(out_dir) / name
        target.mkdir(parents=True, exist_ok=True)
        # regenerate straight into the target, then bake per-spacer paint into pages
        try:
            res = R.regenerate(self._spec, self._program_from_grid(), target,
                               self._bin_dir,
                               spacer_palette=tuple(self._palette) if self._palette else None,
                               basename=name)
            pad = R.assert_data_integrity(self._program_from_grid(), res.ti2)
            self._bake_paint_into_saved(res)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        msg = f"Saved {res.ti2.name} + {len(res.tiffs)} page(s) to {target}"
        if pad:
            msg += f"\nprinttarg added {pad} patch(es) to complete the last strip."
        QMessageBox.information(self, "Saved", msg)
        self._status.setText(msg.splitlines()[0])

    def _bake_paint_into_saved(self, res: R.RegenResult) -> None:
        """Apply per-spacer paint to every saved page in place.

        Spacer indices are deterministic per page (same program + ``-r``), so the
        ``(page, idx)`` keys collected while previewing each page map straight
        onto the freshly regenerated pages here.
        """
        if not self._paint:
            return
        from collections import defaultdict
        for page, (tif, bw) in enumerate(zip(res.tiffs, res.bw_tiffs)):
            page_paint = {idx: rgb for (pg, idx), rgb in self._paint.items()
                          if pg == page}
            if not page_paint:
                continue
            spacers = R.segment_spacers(R.spacer_mask(tif, bw), page=page,
                                        ref_arr=R._imread_rgb(tif))
            groups: dict[tuple, list] = defaultdict(list)
            for idx, rgb in page_paint.items():
                if idx < len(spacers):
                    groups[tuple(round(v) for v in rgb)].append(spacers[idx])
            src = tif
            for rgb, sps in groups.items():
                R.recolor_spacers(src, sps, rgb, tif)
                src = tif

    def _ask_name(self) -> tuple[str, bool]:
        from PyQt6.QtWidgets import QInputDialog
        default = "chart"
        if self._regen is not None:
            default = self._regen.basename
        return QInputDialog.getText(self, "Chart name", "Base name:",
                                    QLineEdit.EchoMode.Normal, default)

    # -- misc ---------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._preview_btn.setEnabled(not busy)
        self._save_btn.setEnabled(not busy)

    def _refresh_enabled(self) -> None:
        has = self._spec is not None
        self._preview_btn.setEnabled(has)
        self._save_btn.setEnabled(has)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        self._preview_tmp.cleanup()
        super().closeEvent(ev)
