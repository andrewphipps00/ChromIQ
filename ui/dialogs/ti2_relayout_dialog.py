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

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPoint, QRect, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QDialog,
    QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSlider, QSplitter,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from core.logger import get_logger
from core.strip_utils import parse_passes_per_page
from ui.styles import SPEC_AMBER, SPEC_MAGENTA, TAB_COLORS
from ui.widgets import (
    NoScrollComboBox, NoScrollSpinBox, open_dir_dialog, open_file_dialog,
)
from workflow import ti2_relayout as R

log = get_logger(__name__)

_SWATCH = 46  # grid swatch px

# printtarg -i codes the editor offers, with friendly labels.
_INSTRUMENTS = [("i1", "i1Pro / i1Pro2 / i1Pro3(+)"), ("CM", "ColorMunki / i1Studio")]

# Paper sizes the new-chart dropdown offers — matches the Create Chart tab.
from data.patch_db import PAPER_LABELS, PAPER_PRINTTARG_ARG
_PAPER_ORDER = ("A2", "594x420", "329x483", "483x329", "A3", "420x297",
                "11x17", "Legal", "A4", "A4R", "Letter", "LetterR",
                "203x254", "127x178", "4x6")


def _qcolor(rgb: tuple[float, float, float]) -> QColor:
    return QColor(*(max(0, min(255, round(v / 100 * 255))) for v in rgb))


def _to100(c: QColor) -> tuple[float, float, float]:
    return (c.red() / 255 * 100, c.green() / 255 * 100, c.blue() / 255 * 100)


def _swatch_icon(rgb: tuple[float, float, float], size: int = _SWATCH) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(_qcolor(rgb))
    return QIcon(pm)


def _ghost_swatch_icon(rgb: tuple[float, float, float], size: int = _SWATCH) -> QIcon:
    """Faded swatch used while an item is being dragged — the cursor's drag
    pixmap stays crisp, the original slot looks washed-out + dashed so it's
    obvious the patch is in motion."""
    r, g, b = (max(0, min(255, round(v / 100 * 255))) for v in rgb)
    # Blend 70 % white + 30 % colour
    fr = round(255 * 0.7 + r * 0.3)
    fg = round(255 * 0.7 + g * 0.3)
    fb = round(255 * 0.7 + b * 0.3)
    pm = QPixmap(size, size)
    pm.fill(QColor(fr, fg, fb))
    p = QPainter(pm)
    p.setPen(QPen(QColor(128, 128, 128), 1, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Background regeneration (printtarg runs off the GUI thread)
# ---------------------------------------------------------------------------
class _RegenWorker(QThread):
    done = pyqtSignal(object)  # RegenResult | Exception

    def __init__(self, spec, program, out_dir, bin_dir, palette,
                 *, options=None, basename="chart"):
        super().__init__()
        self._args = (spec, program, out_dir, bin_dir, palette, options, basename)

    def run(self) -> None:
        spec, program, out_dir, bin_dir, palette, options, basename = self._args
        try:
            self.done.emit(R.regenerate(spec, program, out_dir, bin_dir,
                                        spacer_palette=palette,
                                        options=options,
                                        basename=basename))
        except Exception as exc:  # surfaced to the user, not swallowed
            log.exception("relayout regenerate failed")
            self.done.emit(exc)


# ---------------------------------------------------------------------------
# Patch grid — ListMode + wrapping flow with an icon-above-label delegate.
#
# We use ListMode (not IconMode) because Qt's reorder via DragDropMode.InternalMove
# is genuinely reliable there — IconMode's drop-target resolution is finicky
# (items would either snap back to their slot or land at a free grid intersection
# depending on movement mode). The custom delegate paints each item with its
# colour swatch on top and the patch number underneath, so we keep the
# IconMode-style visual without giving up reliable drag-reorder.
# ---------------------------------------------------------------------------


class _SwatchDelegate(QStyledItemDelegate):
    """Paint a swatch (icon) above a Menlo-styled patch number, inside the
    grid cell sized by :meth:`sizeHint`."""

    LABEL_H = 16
    H_PAD = 6
    V_PAD = 4

    def __init__(self, parent=None, swatch_size: int = _SWATCH) -> None:
        super().__init__(parent)
        self.swatch_size = swatch_size

    def paint(self, painter, opt, idx) -> None:
        painter.save()
        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(opt.rect, opt.palette.highlight())

        icon = idx.data(Qt.ItemDataRole.DecorationRole)
        text = idx.data(Qt.ItemDataRole.DisplayRole) or ""

        rect = opt.rect
        size = self.swatch_size
        cx = rect.x() + (rect.width() - size) // 2
        cy = rect.y() + self.V_PAD
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(cx, cy, size, size))

        f = QFont("Menlo")
        f.setPixelSize(10)
        painter.setFont(f)
        text_color = (opt.palette.color(opt.palette.ColorRole.HighlightedText)
                      if opt.state & QStyle.StateFlag.State_Selected
                      else opt.palette.color(opt.palette.ColorRole.Text))
        painter.setPen(text_color)
        text_rect = QRect(rect.x(), cy + size + 2,
                          rect.width(), self.LABEL_H)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            text,
        )
        painter.restore()

    def sizeHint(self, opt, idx) -> QSize:
        return QSize(self.swatch_size + 2 * self.H_PAD,
                     self.swatch_size + self.V_PAD + self.LABEL_H + 2)


# ---------------------------------------------------------------------------
class _ReorderListWidget(QListWidget):
    """QListWidget with drag-reorder UX tweaks.

    Drop handling is Qt's default (Snap + InternalMove) — that combo is what
    QListView's reorder logic actually targets, and the previous custom
    dropEvent fought with it (items snapping back was the symptom). All we
    customise here is the visual feedback: while a drag is active, the source
    items get a washed-out / dashed icon so the user sees the slot the patch
    came from and the drag pixmap following the cursor at the same time.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_originals: list[tuple[QListWidgetItem, QIcon]] = []

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        selected = self.selectedItems()
        size = self.iconSize().width() or _SWATCH
        self._drag_originals = [(it, it.icon()) for it in selected]
        for it, _ in self._drag_originals:
            rgb = it.data(Qt.ItemDataRole.UserRole)
            if rgb is not None:
                it.setIcon(_ghost_swatch_icon(rgb, size))
        try:
            super().startDrag(supported_actions)
        finally:
            for it, icon in self._drag_originals:
                it.setIcon(icon)
            self._drag_originals = []


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
    resized = pyqtSignal()                           # geometry change
    _CLICK_PX = 4               # movement under this is still a click

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._press: QPoint | None = None
        self._drag_rect: QRect | None = None

    def set_base_pixmap(self, pm: QPixmap | None) -> None:
        """Show the given pixmap. QLabel draws DPR-aware pixmaps at logical
        size with full retina resolution — no compositing needed here; the
        marquee is painted on top in :meth:`paintEvent`."""
        if pm is not None:
            self.setPixmap(pm)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.resized.emit()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()
            self._drag_rect = None
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._press is not None:
            cur = ev.position().toPoint()
            self._drag_rect = QRect(self._press, cur).normalized()
            self.update()
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
            self.update()
        super().mouseReleaseEvent(ev)

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)        # QLabel renders the pixmap centred
        if self._drag_rect is not None:
            p = QPainter(self)
            p.setPen(QPen(QColor(255, 230, 0), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 230, 0, 60))
            p.drawRect(self._drag_rect)
            p.end()


# ---------------------------------------------------------------------------
# New-chart setup
# ---------------------------------------------------------------------------
class _NewChartDialog(QDialog):
    """New-chart setup: source (blank / targen seed / pasted colours) plus the
    printtarg layout knobs that affect rendering."""

    def __init__(self, bin_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New chart")
        self.setMinimumWidth(540)
        self._bin_dir = bin_dir
        self.result_spec: R.ChartSpec | None = None
        self.result_program: list[tuple] | None = None
        self.result_options: R.LayoutOptions | None = None
        self.result_basename: str = "chart"

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # --- Chart identity --------------------------------------------------
        chart_box = QGroupBox("Chart", self)
        cg = QGridLayout(chart_box)
        cg.addWidget(QLabel("Name:"), 0, 0)
        self._name = QLineEdit("chart", chart_box)
        self._name.setToolTip("Used as the file basename and stamped onto the chart")
        cg.addWidget(self._name, 0, 1, 1, 3)
        cg.addWidget(QLabel("Instrument:"), 1, 0)
        self._instr = NoScrollComboBox(chart_box)
        for code, label in _INSTRUMENTS:
            self._instr.addItem(label, code)
        cg.addWidget(self._instr, 1, 1)
        cg.addWidget(QLabel("Paper:"), 1, 2)
        self._paper = NoScrollComboBox(chart_box)
        for code in _PAPER_ORDER:
            self._paper.addItem(PAPER_LABELS.get(code, code), code)
        # Default to A4 portrait
        ix = self._paper.findData("A4")
        if ix >= 0:
            self._paper.setCurrentIndex(ix)
        cg.addWidget(self._paper, 1, 3)
        lay.addWidget(chart_box)

        # --- Source ---------------------------------------------------------
        src_box = QGroupBox("Patches", self)
        sl = QVBoxLayout(src_box)
        self._mode_blank = QRadioButton("Blank canvas (add patches by hand)", src_box)
        self._mode_seed = QRadioButton("Seed from targen (optimised patch set)", src_box)
        self._mode_paste = QRadioButton("Paste colour values (or load a file)", src_box)
        self._mode_seed.setChecked(True)
        sl.addWidget(self._mode_seed)
        seed_row = QHBoxLayout()
        seed_row.addSpacing(22)
        seed_row.addWidget(QLabel("Patches:"))
        self._count = NoScrollSpinBox(src_box)
        self._count.setRange(8, 4000)
        self._count.setValue(200)
        seed_row.addWidget(self._count)
        seed_row.addStretch(1)
        sl.addLayout(seed_row)
        sl.addWidget(self._mode_blank)
        sl.addWidget(self._mode_paste)
        paste_indent = QVBoxLayout()
        paste_indent.setContentsMargins(22, 0, 0, 0)
        self._paste_edit = QPlainTextEdit(src_box)
        self._paste_edit.setPlaceholderText(
            "One colour per line — hex (#ff00aa or ff00aa) or RGB "
            "(255,0,170 / 1.0 0 0.67). Scale auto-detected."
        )
        self._paste_edit.setFixedHeight(110)
        paste_indent.addWidget(self._paste_edit)
        paste_btns = QHBoxLayout()
        load_btn = QPushButton("Load from file…", src_box)
        load_btn.clicked.connect(self._load_paste_file)
        self._paste_status = QLabel("", src_box)
        self._paste_status.setStyleSheet("color: #888;")
        paste_btns.addWidget(load_btn)
        paste_btns.addStretch(1)
        paste_btns.addWidget(self._paste_status)
        paste_indent.addLayout(paste_btns)
        sl.addLayout(paste_indent)
        self._paste_edit.textChanged.connect(self._update_paste_count)
        # Enable/disable subcontrols by mode
        self._mode_seed.toggled.connect(lambda on: self._count.setEnabled(on))
        for r in (self._mode_blank, self._mode_seed, self._mode_paste):
            r.toggled.connect(self._refresh_source_widgets)
        self._refresh_source_widgets()
        lay.addWidget(src_box)

        # --- Layout options -------------------------------------------------
        opt_box = QGroupBox("Layout options (printtarg)", self)
        og = QGridLayout(opt_box)
        og.addWidget(QLabel("Spacers:"), 0, 0)
        self._sp_colored = QRadioButton("Coloured", opt_box)
        self._sp_bw = QRadioButton("B&&W", opt_box)
        self._sp_none = QRadioButton("None", opt_box)
        self._sp_colored.setChecked(True)
        sp_grp = QButtonGroup(opt_box)
        sp_row = QHBoxLayout()
        for rb in (self._sp_colored, self._sp_bw, self._sp_none):
            sp_grp.addButton(rb)
            sp_row.addWidget(rb)
        sp_row.addStretch(1)
        og.addLayout(sp_row, 0, 1, 1, 3)

        og.addWidget(QLabel("Patch scale (-a):"), 1, 0)
        self._patch_scale = QDoubleSpinBox(opt_box)
        self._patch_scale.setRange(0.3, 3.0)
        self._patch_scale.setSingleStep(0.05)
        self._patch_scale.setValue(1.0)
        og.addWidget(self._patch_scale, 1, 1)
        og.addWidget(QLabel("Spacer scale (-A):"), 1, 2)
        self._spacer_scale = QDoubleSpinBox(opt_box)
        self._spacer_scale.setRange(0.3, 3.0)
        self._spacer_scale.setSingleStep(0.05)
        self._spacer_scale.setValue(1.0)
        og.addWidget(self._spacer_scale, 1, 3)

        self._cb_L = QCheckBox("Suppress left clip border (-L)", opt_box)
        self._cb_L.setToolTip("i1Pro / 3+ only. Frees the strip for patches.")
        self._cb_P = QCheckBox("Don't limit strip length (-P)", opt_box)
        self._cb_h = QCheckBox("Double density / hexagons (-h)", opt_box)
        self._cb_h.setToolTip("ColorMunki: double-density. SpectroScan: hex patches.")
        og.addWidget(self._cb_L, 2, 0, 1, 2)
        og.addWidget(self._cb_P, 2, 2, 1, 2)
        og.addWidget(self._cb_h, 3, 0, 1, 4)
        lay.addWidget(opt_box)

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

    # -- helpers -----------------------------------------------------------
    def _refresh_source_widgets(self) -> None:
        self._count.setEnabled(self._mode_seed.isChecked())
        self._paste_edit.setEnabled(self._mode_paste.isChecked())

    def _update_paste_count(self) -> None:
        parsed = R.parse_color_values(self._paste_edit.toPlainText())
        self._paste_status.setText(f"{len(parsed)} colour(s) parsed"
                                    if parsed else "")

    def _load_paste_file(self) -> None:
        path = open_file_dialog(self, "Load colour values",
                                "Text files (*.txt *.csv *.tsv);;All files (*)",
                                start_dir=str(Path.home()))
        if not path:
            return
        try:
            self._paste_edit.setPlainText(Path(path).read_text(errors="ignore"))
            self._mode_paste.setChecked(True)
        except OSError as exc:
            QMessageBox.warning(self, "Could not read file", str(exc))

    def _on_ok(self) -> None:
        paper_code = self._paper.currentData() or self._paper.currentText()
        spec = R.ChartSpec.new(self._instr.currentData(), paper_code)
        program: list[tuple] = []
        if self._mode_seed.isChecked():
            try:
                program = R.seed_from_targen(self._bin_dir, self._count.value())
            except Exception as exc:
                QMessageBox.warning(self, "targen failed", str(exc))
                return
        elif self._mode_paste.isChecked():
            program = R.parse_color_values(self._paste_edit.toPlainText())
            if not program:
                QMessageBox.warning(self, "No values",
                                    "Couldn't parse any RGB / hex values "
                                    "from the pasted text.")
                return

        if self._sp_bw.isChecked():
            sm = "bw"
        elif self._sp_none.isChecked():
            sm = "none"
        else:
            sm = "colored"
        opts = R.LayoutOptions(
            spacer_mode=sm,
            patch_scale=self._patch_scale.value(),
            spacer_scale=self._spacer_scale.value(),
            suppress_left_clip=self._cb_L.isChecked(),
            no_strip_limit=self._cb_P.isChecked(),
            double_density=self._cb_h.isChecked(),
        )

        name = self._name.text().strip() or "chart"
        self.result_spec = spec
        self.result_program = program
        self.result_options = opts
        self.result_basename = name
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
        self._full_pixmap: QPixmap | None = None        # full-res render (pre-scale)
        self._options = R.LayoutOptions()               # printtarg layout knobs
        self._basename = "chart"                        # used for preview + save
        self._strips_per_page: list[int] = []           # PASSES_IN_STRIPS2 per page

        # Debounced auto-preview: fire 1.8s after the last edit.
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(1800)
        self._auto_timer.timeout.connect(
            lambda: self._regenerate(save_to=None) if self._spec else None
        )

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
        # ListMode + LeftToRight + Wrapping is the canonical Qt pattern for a
        # wrap-list reorder. The custom delegate puts the icon ABOVE the label
        # so the visual still looks IconMode-style.
        self._grid.setViewMode(QListWidget.ViewMode.ListMode)
        self._grid.setFlow(QListWidget.Flow.LeftToRight)
        self._grid.setWrapping(True)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._grid.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._grid.setDragEnabled(True)
        self._grid.setAcceptDrops(True)
        self._grid.setDropIndicatorShown(True)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setIconSize(QSize(_SWATCH, _SWATCH))
        self._grid.setSpacing(3)
        self._delegate = _SwatchDelegate(self._grid, _SWATCH)
        self._grid.setItemDelegate(self._delegate)
        self._grid.setGridSize(self._delegate.sizeHint(None, None))
        # Qt's default InternalMove reorder emits rowsMoved on success.
        # That's our single source of truth for "drag committed" — renumber
        # the labels and schedule an auto-preview.
        def _after_drag(*_a):
            self._renumber()
            self._schedule_auto_refresh()
        self._grid.model().rowsMoved.connect(_after_drag)
        # Keyboard reorder for the selection. Alt + arrows nudge / jump,
        # plain F/L jump to first/last (mnemonic for "front" / "last").
        for keys, fn in (
            (("Alt+Up", "Alt+Left"),    self._move_up),
            (("Alt+Down", "Alt+Right"), self._move_down),
            (("Alt+Home", "F"),         self._move_front),
            (("Alt+End",  "L"),         self._move_back),
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
        # Re-scale the preview from the full-res cache when the label resizes,
        # so the displayed image stays sharp at any pane width.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._rescale_preview)
        self._preview.resized.connect(self._resize_timer.start)
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
        reorder_lbl = QLabel(
            "Reorder (drag, Alt+arrows, F/L, or):", self._patch_box)
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
        paint = QPushButton("Paint…", self._spacer_box)
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
        if dlg.result_options is not None:
            self._options = dlg.result_options
        self._basename = dlg.result_basename or "chart"
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
        self._refresh_enabled()
        # Auto-render the initial preview so the user sees the chart
        # immediately instead of having to click "Update preview" first.
        if program:
            self._status.setText("Rendering initial preview…")
            self._regenerate(save_to=None)
        else:
            self._status.setText("Empty chart — add patches, then preview.")

    def _schedule_auto_refresh(self) -> None:
        """Restart the debounced preview timer (called from user edit hooks)."""
        if self._spec is not None and self._grid.count() > 0:
            self._auto_timer.start()

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
        """Resize the grid swatches; rebuild icons + delegate cell so the
        layout stays crisp and items keep their icon-above-label proportions."""
        self._swatch_size = size
        self._grid.setIconSize(QSize(size, size))
        self._delegate.swatch_size = size
        self._grid.setGridSize(self._delegate.sizeHint(None, None))
        for i in range(self._grid.count()):
            it = self._grid.item(i)
            it.setIcon(_swatch_icon(it.data(Qt.ItemDataRole.UserRole), size))
        self._grid.scheduleDelayedItemsLayout()

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
        self._status.setText(f"Set {len(items)} patch(es).")
        self._schedule_auto_refresh()

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
        self._schedule_auto_refresh()

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
        self._status.setText("Palette changed.")
        self._schedule_auto_refresh()

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
            tuple(self._palette) if self._palette else None,
            options=self._options, basename=self._basename)
        self._worker.done.connect(self._on_regen_done)
        self._worker.start()

    def _on_regen_done(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Render failed", str(result))
            self._status.setText("Render failed.")
            return
        self._regen = result
        # Authoritative per-page strip count from the regenerated .ti2.
        self._strips_per_page = parse_passes_per_page(result.ti2)
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
            ref_arr = R._imread_rgb(tif)
            # Authoritative split by strip count from PASSES_IN_STRIPS2 —
            # this handles the case where two adjacent strips happened to
            # pick the same spacer colour, which colour-jump detection
            # alone can't separate.
            strip_xs = self._compute_strip_xs(ref_arr)
            self._spacers = R.segment_spacers(
                mask, page=self._page,
                ref_arr=ref_arr, strip_xs=strip_xs)
        except Exception:
            self._spacers = []
        self._sel_spacers.clear()
        self._update_page_nav()
        self._apply_paint_and_show()

    def _compute_strip_xs(self, ref_arr) -> list[int] | None:
        """Return the inter-strip x-boundaries on the current page, or None.

        Uses the page's strip count from PASSES_IN_STRIPS2 (parsed in
        :meth:`_on_regen_done`) and the patch-grid bbox to divide the block
        into equal-width strip cells.
        """
        if (self._page >= len(self._strips_per_page)
                or self._strips_per_page[self._page] <= 1):
            return None
        bbox = R._patch_grid_bbox(ref_arr)
        if bbox is None:
            return None
        y0, y1, x0, x1 = bbox
        n = self._strips_per_page[self._page]
        col_w = (x1 - x0 + 1) / n
        return [int(x0 + i * col_w) for i in range(1, n)]

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
        self._full_pixmap = pm
        self._rescale_preview()

    def _rescale_preview(self) -> None:
        """Scale the cached full-resolution pixmap to the current preview size,
        honouring the display's device pixel ratio so retina screens get a
        crisp image (the older code rendered at logical resolution and looked
        soft on retina)."""
        if self._full_pixmap is None:
            return
        dpr = self._preview.devicePixelRatioF() or 1.0
        lw, lh = self._preview.width(), self._preview.height()
        if lw <= 0 or lh <= 0:
            return
        target = QSize(int(lw * dpr), int(lh * dpr))
        scaled = self._full_pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        # Logical scale (label coords -> image px) — divides out the dpr.
        logical_w = scaled.width() / dpr
        self._preview_scale = (logical_w / self._full_pixmap.width()
                               if self._full_pixmap.width() else 1.0)
        self._preview_orig = self._full_pixmap.size()
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
            # Fill + outline (à la ui.scan_highlighter) — a translucent yellow
            # wash makes thin bars visible at a glance, the 2px outline keeps
            # the boundary crisp.
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QColor(255, 230, 0, 120))
            p.setPen(QPen(QColor(255, 200, 0), 2))
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
        """Map a label-coord click to deliverable image pixels (or None).

        ``_base_pixmap`` is DPR-aware; its ``width()`` is physical, so we
        divide by DPR to compare against the label's logical width.
        """
        if self._base_pixmap is None or self._preview_scale <= 0:
            return None
        dpr = self._base_pixmap.devicePixelRatio() or 1.0
        pm_logical_w = self._base_pixmap.width() / dpr
        pm_logical_h = self._base_pixmap.height() / dpr
        off_x = (self._preview.width() - pm_logical_w) / 2
        off_y = (self._preview.height() - pm_logical_h) / 2
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
                               basename=name, options=self._options)
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
