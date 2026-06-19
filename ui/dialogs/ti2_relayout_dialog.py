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

import copy
import sys
import tempfile
from dataclasses import astuple, dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPoint, QRect, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog,
    QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QSplitter,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from core.logger import get_logger
from core.strip_utils import parse_passes_per_page
from ui.styles import SPEC_AMBER, SPEC_MAGENTA, TAB_COLORS
from ui.gradient_overlay import GradientOverlay
from ui.tab_header import SpectrumStripe as _SpectrumStripe, TabHeader
from ui.tooltip_button import TooltipButton
from ui.widgets import (
    NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox,
    PrefixLockedLineEdit, open_dir_dialog, open_file_dialog, save_file_dialog,
)


def _magenta_tip(title: str, body: str, parent: QWidget | None = None,
                 min_width: int = 480) -> TooltipButton:
    """A TooltipButton drawn in the editor's magenta accent."""
    return TooltipButton(title, body, parent, min_width=min_width, color=SPEC_MAGENTA)


def _toggle_locked_prefix(edit: "PrefixLockedLineEdit", on: bool, prefix: str) -> None:
    """Flip a name field between locked-prefix and free modes (#68, Knut's
    model). ON: descriptive head locked + greyed with a trailing '-' and an
    empty editable tail. OFF: the descriptive name shown as a plain, fully
    editable field (no dash, no lock). Mirrors tab_chart's ``_toggle_name_prefix``."""
    if on:
        edit.set_prefix("")
        edit.setText("")
        edit.set_prefix(prefix)
    else:
        edit.set_prefix("")
        edit.setText(prefix)


# The "Generate colour sets" help, shared verbatim by the New-chart dialog's ⓘ
# and the Add-patches dialog's ⓘ (#66 follow-up: the generator info must be in
# the Add window too). Kept as one constant so the two tips can't drift; a test
# asserts it equals the matching paragraphs of the New-chart tooltip. The leading
# string passed to tr() is a module constant, which i18n_extract resolves like a
# literal — so this is a single catalog key reused in both places.
_GEN_SETS_HELP = (
    "About “Generate colour sets”:\n\n"
    "Tick any combination of these five sets and ChromIQ lays them down "
    "one after another. Each set shows how many patches it adds, and a "
    "running total appears underneath, so you always know how big the "
    "chart will get before you create it:\n\n"
    "• 3D RGB cube — an even grid of colours across the whole range. You "
    "pick how many steps each of red, green and blue is split into, and "
    "the chart then holds every combination (for example 6 steps makes "
    "6×6×6 = 216 patches). A solid, neutral foundation for almost any "
    "profile.\n\n"
    "• Skin tones (Fitzpatrick) — lifelike skin colours running light to "
    "dark through each of the six Fitzpatrick skin types, now reaching "
    "from porcelain-pale highlights down to very deep, faintly cool "
    "shadows. 'Per type' sets how many shades each type gets from light "
    "to dark; 'Ranges' adds that many parallel ramps, each nudged a "
    "little in hue, so a single skin type is covered by a small spread of "
    "tones rather than one straight line — handy because real faces vary. "
    "Worth adding whenever faces and portraits matter most.\n\n"
    "• Oceans (blues) — extra colours packed into the green-turquoise "
    "to deep-blue part of the range, where wide-gamut papers and inks "
    "reach furthest (it now dips into the greenish turquoise too). "
    "'Per layer' is how many patches each sheet holds and 'Layers' is how "
    "many sheets, so the two multiply (24 per layer × 3 layers = 72). The "
    "sheets are gently angled rather than one flat blanket, so the whole "
    "turquoise corner is filled in depth. Helpful for skies, water and "
    "deep blues.\n\n"
    "• Foliage (greens) — a spread of forest, jungle and leaf greens, for "
    "landscapes and nature shots where the greens carry the picture. As "
    "with the blues, 'Per layer' × 'Layers' patches are spread across "
    "angled sheets so the green part of the range is covered with more "
    "depth.\n\n"
    "• Near-neutral greys — a grey ramp from black to white and, at each "
    "step, a ring of gentle tints just off neutral. This is what helps "
    "greys print cleanly without an unwanted colour cast — the most "
    "important region for a clean profile. You set how many grey steps "
    "there are, how far the tints stray from neutral ('offset'), and how "
    "many rings to circle each grey with ('rings'): one ring is six "
    "tints, and each extra ring adds a wider, denser one (12, then 18 "
    "tints) for fuller near-neutral coverage when you want it. Set rings "
    "to 0 for a plain neutral ramp with no tints at all — a "
    "black-and-white wedge — in which case 'offset' has no effect.\n\n"
    "• Saturated edges — the most vivid colours the printer can manage. "
    "'Per edge' traces the twelve edges of the colour cube — the gamut "
    "wireframe (black up to each pure colour and on to white, plus the "
    "colourful edges between); 'per face' goes further and also fills the "
    "six cube faces — the full gamut surface — with that many patches per "
    "side, or leave it at 0 for edges only. This outer boundary is exactly "
    "where profiles tend to go wrong, so it pays to sample it well.\n\n"
    "• Highlights & shadows — extra detail at the two ends where printers "
    "struggle most: pale tints just below paper white, and deep tones just "
    "above black, spread across every hue. These ends often band or block "
    "up, and the cube alone samples them thinly. 'Per end' is how many "
    "patches go at each end (so the set adds twice that many), and 'depth' "
    "is how far in from white and black the tones reach.\n\n"
    "• Pastels — soft, muted colours all around the hue wheel: dusty blues, "
    "sages, soft pinks, taupes. This is where a great deal of real "
    "photography actually lives — the gentle region between the clean greys "
    "and the vivid sets. Each of the 'layers' is a chroma shell — from "
    "barely-tinted near-greys out to fuller pastels — of 'per layer' "
    "patches, so the two multiply.\n\n"
    "• From image — load one of your own photos and ChromIQ finds its most "
    "representative colours and adds them to the chart, so the profile is "
    "tuned to the kind of pictures you really print. Click 'Load image…', "
    "pick a file, and set how many 'Colours' to pull out. Lovely combined "
    "with a cube for all-round coverage plus your image's own palette on "
    "top.\n\n"
    "• Fill remaining gaps — a tidy-up that comes last. After the sets you "
    "picked are laid down, it scatters extra patches into the empty parts "
    "of colour space — evenly and without repeating — until the chart "
    "reaches the size you ask for. 'Fill to' is that target total, so the "
    "whole chart lands on a round number with nothing left clumped or "
    "bare.\n\n"
    "Mix them freely — say a 3D cube for overall coverage plus "
    "near-neutral greys for clean neutrals, or skin tones plus greens for "
    "portraits out in nature.\n\n"
    "• Ensure unique colours — when this is ticked and your sets happen to "
    "share a colour (for example a 3D cube and a grey ramp both include "
    "black and white), ChromIQ keeps one and nudges the duplicates apart "
    "by a tiny amount, so no colour is printed and measured twice. The "
    "patch total stays the same. Leave it on unless you have a reason not "
    "to."
)

# Short intro for the Add-patches dialog's ⓘ, ahead of the shared generator help.
_ADD_TIP_INTRO = (
    "Add more patches to the chart you're editing. Two ways:\n\n"
    "• Add a single colour — dial in one colour and append it as a single "
    "patch.\n\n"
    "• Generate colour sets — lay down one or more of the ready-made colour "
    "spreads described below; a running total shows how many patches you'll "
    "add, and “Fill remaining gaps” tops the whole chart up to a target size "
    "rather than adding that many.\n\n"
    "The new patches are appended after the chart's existing ones — rearrange "
    "them however you like back in the editor."
)


class _AutoHideLabel(QLabel):
    """Status line that clears itself a few seconds after its last message, so
    the bottom bar is normally empty instead of holding stale text. Every
    ``setText`` (re)starts the clear timer; setting empty text cancels it."""

    HIDE_MS = 4000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        # The status line lives in the left pane of a horizontal QSplitter. A
        # plain QLabel reports its full (unwrapped) text width as its minimum,
        # so a long message would force the splitter to widen this pane and
        # shrink the preview — then snap back when the text auto-clears 4 s
        # later (a Windows-only jitter, where the system font renders the text
        # wider than the pane's share). Ignored horizontal policy means the
        # text never dictates the pane width; the label just takes whatever the
        # layout gives it and clips an over-long message.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(self.HIDE_MS)
        # Clear via the base setText so the timeout doesn't re-arm itself.
        self._clear_timer.timeout.connect(lambda: QLabel.setText(self, ""))

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        if text:
            self._clear_timer.start()
        else:
            self._clear_timer.stop()


def _uniform_button_width(buttons, *, pad: int = 0) -> None:
    """Give every button in ``buttons`` one minimum width = the widest
    ``sizeHint`` + ``pad``, so their labels never clip (macOS's Fusion style
    under-sizes button hints by a few px) and the buttons line up. Derived from
    the rendered sizeHints, so it adapts to the active language."""
    buttons = [b for b in buttons if b is not None]
    if not buttons:
        return
    w = max(b.sizeHint().width() for b in buttons) + pad
    for b in buttons:
        b.setMinimumWidth(w)


def _as_compact(*widgets) -> None:
    """Mark inputs as ``#compact_input`` so the app-wide stylesheets
    (ui/styles.py + ui/light_styles.py) apply the short / small-arrow
    rules to them. Used in this dialog so the spinboxes don't display
    the bulky default native arrows."""
    for w in widgets:
        w.setObjectName("compact_input")


def _wire_spacer_mutex(boxes: tuple) -> None:
    """Make a set of QCheckBoxes behave like a radio group that also
    permits the all-off state.

    Clicking a box that's already on leaves it on (no-op uncheck-then-
    -check loop); clicking a different box switches the active selection;
    the user may explicitly uncheck the active one to land in the all-off
    state. Mirrors the spacer-mode picker behaviour the editor uses in
    both the printtarg section and the New-chart dialog.
    """
    def _make(cb_idx: int):
        def _on_toggled(on: bool) -> None:
            if not on:
                return
            for j, other in enumerate(boxes):
                if j == cb_idx or not other.isChecked():
                    continue
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
        return _on_toggled
    for i, cb in enumerate(boxes):
        cb.toggled.connect(_make(i))
from workflow import ti2_relayout as R
from workflow import patch_generators as G

log = get_logger(__name__)


def _patches_label(n: int) -> str:
    """Count-bearing patch label with explicit singular / plural (no "(s)")."""
    return (tr("{n} patch") if n == 1 else tr("{n} patches")).format(n=n)


_SWATCH = 46  # grid swatch px

# Minimum spacing (device units, 0..100) that "Ensure unique colours" keeps
# between any two patches when sets are combined. A touch above the old 0.5 grid
# so generators that land near a 3D-cube dot are nudged a little clearer of it,
# not just de-duplicated when they coincide exactly (Knut, #78).
_GEN_MIN_DIST = 2.0

# On-screen preview render resolution (#44). The preview never needs print DPI;
# rendering it low-res makes printtarg far faster and shrinks every image
# read / diff / segmentation. The saved chart still uses the user's full DPI.
_PREVIEW_DPI = 100

_IS_MAC = sys.platform == "darwin"

# printtarg -i codes the editor offers, with friendly labels. The codes are
# passed straight through to printtarg (see workflow.ti2_relayout.regenerate),
# so they must be printtarg's own -i values: "i1" (i1Pro family), "3p"
# (i1Pro 3 Plus — larger aperture, far fewer patches per sheet, so its strip
# layout differs), "CM" (ColorMunki / i1Studio).
_INSTRUMENTS = [
    ("i1", "i1Pro / i1Pro2 / i1Pro3"),
    ("3p", "i1Pro3 Plus"),
    ("CM", "ColorMunki / i1Studio"),
]

# Strip readers (i1Pro family + 3 Plus) — the instruments for which printtarg's
# -L (suppress left clip) and -P (no per-strip patch limit) apply.
_STRIP_INSTRUMENTS = frozenset({"i1", "3p"})

# Paper sizes the new-chart dropdown offers — matches the Create Chart tab.
from data.patch_db import PAPER_LABELS, PAPER_PRINTTARG_ARG, paper_name_token
from core.i18n import tr


def _mod_keys() -> dict[str, str]:
    """Platform- and language-aware modifier-key names for the editor's
    selection hints (#45): macOS shows ⌘/⌥, every other platform the localized
    Ctrl / Alt. Built at call time so the language (restart-applied) is set."""
    shift = tr("Shift")
    if _IS_MAC:
        return {"ext": "⌘", "add": f"⌘/{shift}", "remove": "⌥"}
    ctrl = tr("Ctrl")
    return {"ext": ctrl, "add": f"{ctrl}/{shift}", "remove": tr("Alt")}
_PAPER_ORDER = ("A2", "594x420", "329x483", "483x329", "A3", "420x297",
                "11x17", "Legal", "A4", "A4R", "Letter", "LetterR",
                "203x254", "127x178", "4x6", "custom")
_PAPER_LABELS_WITH_CUSTOM = {**PAPER_LABELS,
                              "custom": "Custom (enter dimensions)"}


def _paper_code_known(code: str) -> bool:
    """True iff *code* is one of the printtarg named paper sizes — i.e. not
    a custom ``WxH`` form. Used by the editor UI to decide whether to fall
    back to the "custom" combo entry + W/H spinboxes when syncing a loaded
    chart whose paper_flag was emitted as ``WxH`` by
    :func:`workflow.ti2_relayout.paper_to_flag`.
    """
    return code in PAPER_LABELS


def _qcolor(rgb: tuple[float, float, float]) -> QColor:
    return QColor(*(max(0, min(255, round(v / 100 * 255))) for v in rgb))


def _to100(c: QColor) -> tuple[float, float, float]:
    return (c.red() / 255 * 100, c.green() / 255 * 100, c.blue() / 255 * 100)


def _swatch_icon(rgb: tuple[float, float, float], size: int = _SWATCH) -> QIcon:
    """Colour-filled swatch with a 1-px luminance-adaptive border so dark
    patches stay visible against the dark grid background and light patches
    still get a subtle frame."""
    pm = QPixmap(size, size)
    qc = _qcolor(rgb)
    pm.fill(qc)
    # BT.601 luminance — light grey border on dark swatches, darker grey on
    # light ones. Both are visible against the dark grid background.
    y = 0.30 * qc.red() + 0.59 * qc.green() + 0.11 * qc.blue()
    border = QColor(160, 160, 160) if y < 90 else QColor(90, 90, 90)
    p = QPainter(pm)
    p.setPen(QPen(border, 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
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
                 *, options=None, basename="chart", with_twin=True,
                 dpi_override=None):
        super().__init__()
        self._args = (spec, program, out_dir, bin_dir, palette, options,
                      basename, with_twin, dpi_override)

    def run(self) -> None:
        (spec, program, out_dir, bin_dir, palette, options, basename,
         with_twin, dpi_override) = self._args
        try:
            self.done.emit(R.regenerate(spec, program, out_dir, bin_dir,
                                        spacer_palette=palette,
                                        options=options,
                                        basename=basename,
                                        with_twin=with_twin,
                                        dpi_override=dpi_override))
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
            painter.fillRect(opt.rect, QColor(255, 69, 115, 110))

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
    dropEvent fought with it (items snapping back was the symptom).

    Two visual tweaks on top of Qt's behaviour:

    1. While a drag is active, the source items get a washed-out / dashed
       icon so the source slot stays visible alongside the drag pixmap.
    2. The drop indicator is painted by us at the **gap midpoint** between
       the two items around the cursor — Qt's built-in indicator otherwise
       snaps to either side of the gap depending on cursor position, which
       reads as visual flicker even though the resulting reorder is the
       same. We hide the built-in one and draw a single mid-gap line.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_originals: list[tuple[QListWidgetItem, QIcon]] = []
        self._drop_line: tuple[int, int, int] | None = None  # (x, y0, y1)

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
            self._drop_line = None
            self.viewport().update()

    def dragMoveEvent(self, ev) -> None:  # noqa: N802
        """Compute the gap midpoint between the items around the cursor.

        Qt's hit logic gives us the item *under* the cursor; we then decide
        whether the drop goes *before* or *after* that item by the cursor's
        position within its rect, and pin the indicator to the centre of the
        gap to the chosen neighbour. The actual insert position is left to
        Qt's dropEvent so the InternalMove reorder still does the right
        thing.
        """
        super().dragMoveEvent(ev)
        pos = ev.position().toPoint()
        idx = self.indexAt(pos)
        if not idx.isValid():
            self._drop_line = None
            self.viewport().update()
            return
        rect = self.visualRect(idx)
        # Drop-before-this-item if cursor is in its left half, else drop-after.
        before = pos.x() < rect.center().x()
        if before:
            other_idx = self.model().index(idx.row() - 1, 0) if idx.row() > 0 else None
            if other_idx is not None and other_idx.isValid():
                left_rect  = self.visualRect(other_idx)
                right_rect = rect
                # Only stack horizontally when items are on the same row.
                if abs(left_rect.center().y() - right_rect.center().y()) < rect.height() / 2:
                    x = (left_rect.right() + right_rect.left()) // 2
                    y0 = min(left_rect.top(), right_rect.top())
                    y1 = max(left_rect.bottom(), right_rect.bottom())
                    self._drop_line = (x, y0, y1)
                else:
                    self._drop_line = (rect.left() - 2, rect.top(), rect.bottom())
            else:
                self._drop_line = (rect.left() - 2, rect.top(), rect.bottom())
        else:
            other_idx = self.model().index(idx.row() + 1, 0)
            if other_idx is not None and other_idx.isValid():
                left_rect  = rect
                right_rect = self.visualRect(other_idx)
                if abs(left_rect.center().y() - right_rect.center().y()) < rect.height() / 2:
                    x = (left_rect.right() + right_rect.left()) // 2
                    y0 = min(left_rect.top(), right_rect.top())
                    y1 = max(left_rect.bottom(), right_rect.bottom())
                    self._drop_line = (x, y0, y1)
                else:
                    self._drop_line = (rect.right() + 2, rect.top(), rect.bottom())
            else:
                self._drop_line = (rect.right() + 2, rect.top(), rect.bottom())
        self.viewport().update()

    def dragLeaveEvent(self, ev) -> None:  # noqa: N802
        self._drop_line = None
        self.viewport().update()
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev) -> None:  # noqa: N802
        self._drop_line = None
        super().dropEvent(ev)
        self.viewport().update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)
        if self._drop_line is None:
            return
        x, y0, y1 = self._drop_line
        p = QPainter(self.viewport())
        # Magenta accent — matches the app's drag/active highlight family.
        p.setPen(QPen(QColor(SPEC_MAGENTA), 2))
        p.drawLine(x, y0, x, y1)
        p.end()


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
            p.setPen(QPen(QColor(SPEC_MAGENTA), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 69, 115, 60))
            p.drawRect(self._drag_rect)
            p.end()


# ---------------------------------------------------------------------------
# Right-panel scroll area with a top/bottom fade gradient.
# ---------------------------------------------------------------------------
class _FadeScrollArea(QScrollArea):
    """QScrollArea that paints translucent fade strips at the top + bottom
    edges so the user can see content continues out of view.

    The fades only render when there's actually content above/below — at
    the top they vanish when scrolled to the top, etc. The dialog colour
    bleeds the fade into the surrounding panel.

    We install an event filter on the viewport to draw AFTER the child
    widget paints (QScrollArea's own paintEvent fires only on frame
    repaints, not on scroll). The vertical scroll bar's valueChanged
    triggers a viewport update so the fades refresh as the user scrolls.
    """

    _FADE_PX = 18

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.verticalScrollBar().valueChanged.connect(
            lambda _v: self.viewport().update()
        )
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.viewport() and ev.type() == ev.Type.Paint:
            # Let the viewport draw its child widget first, then overlay
            # the fade strips on top.
            res = super().eventFilter(obj, ev)
            self._paint_fades(self.viewport())
            return res
        return super().eventFilter(obj, ev)

    def _paint_fades(self, vp) -> None:
        bar = self.verticalScrollBar()
        if bar is None or bar.maximum() == 0:
            return
        from PyQt6.QtGui import QLinearGradient
        w, h = vp.width(), vp.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(vp)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Sample the dialog background (one level up) so the fade matches
        # whatever theme is active.
        bg = (self.parentWidget() or self).palette().color(
            self.backgroundRole())
        clear  = QColor(bg.red(), bg.green(), bg.blue(), 0)
        opaque = QColor(bg.red(), bg.green(), bg.blue(), 235)
        if bar.value() > 0:
            grad = QLinearGradient(0, 0, 0, self._FADE_PX)
            grad.setColorAt(0.0, opaque)
            grad.setColorAt(1.0, clear)
            p.fillRect(0, 0, w, self._FADE_PX, grad)
        if bar.value() < bar.maximum():
            grad = QLinearGradient(0, h - self._FADE_PX, 0, h)
            grad.setColorAt(0.0, clear)
            grad.setColorAt(1.0, opaque)
            p.fillRect(0, h - self._FADE_PX, w, self._FADE_PX, grad)
        p.end()


# ---------------------------------------------------------------------------
# New-chart setup
# ---------------------------------------------------------------------------
class _NewChartDialog(QDialog):
    """New-chart setup: source (blank / targen seed / pasted colours) plus the
    printtarg layout knobs that affect rendering."""

    def __init__(self, bin_dir: Path, settings=None,
                 parent: QWidget | None = None,
                 initial_recipe: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New chart"))
        self.setMinimumWidth(620)
        self._bin_dir = bin_dir
        self._settings = settings
        # The chart's stored creation recipe, if reopened from a chart that has
        # one — applied instead of the app-wide last-used state (see the restore
        # in __init__ below). The window reports its own recipe back via
        # result_recipe so the editor can persist it.
        self._initial_recipe = initial_recipe
        self.result_recipe: dict | None = None
        self.result_spec: R.ChartSpec | None = None
        self.result_program: list[tuple] | None = None
        self.result_options: R.LayoutOptions | None = None
        self.result_basename: str = "chart"
        # Magenta accents on checked / focused state — same scoped rules
        # as the parent dialog so the New-chart dialog matches it instead
        # of falling back to the app-wide cyan.
        self._install_magenta_accents()

        # Content lives in a scroll area so the dialog fits small screens even
        # with every colour set expanded (the panel can get tall).
        content = QWidget(self)
        lay = QVBoxLayout(content)
        lay.setSpacing(10)

        head = QHBoxLayout()
        # Tab-style heading (uppercase eyebrow + large serif title), matching
        # the main-window tab headers, in the editor's magenta accent. Lives at
        # the top of the dialog above a full-width spectrum stripe (added to
        # ``outer`` below), so the 3D-cube preview starts beneath it.
        head.setContentsMargins(16, 12, 16, 0)
        head.addWidget(TabHeader(
            tr("NEW CHART · SETUP"), tr("Set up your chart"),
            SPEC_MAGENTA, self), 0, Qt.AlignmentFlag.AlignVCenter)
        GradientOverlay(SPEC_MAGENTA, parent=self, alpha=15, height=95, on_top=False)
        head.addStretch(1)
        head.addWidget(_magenta_tip(
            tr("New chart"),
            tr("Let's start a brand-new chart. You only need to make a few quick "
            "choices here — once you're done, the chart opens in the editor where "
            "you can arrange and fine-tune everything.\n\n"
            "What each choice means:\n\n"
            "• Instrument & Paper — which measuring device you'll use and what "
            "paper you'll print on. ChromIQ uses these to lay the patches out in a "
            "way your device can read, at the right page size.\n\n"
            "• Patches — how to fill the chart to begin with. There are four ways:\n"
            "    – Blank canvas — start empty and add every colour by hand in the "
            "editor.\n"
            "    – Seed from targen — enter a number and let ChromIQ spread that "
            "many colours evenly across the whole colour range. A great all-round "
            "starting point you can then rearrange.\n"
            "    – Paste colour values — paste, or load from a file, your own list "
            "of hex or RGB colours.\n"
            "    – Generate colour sets — build the chart from one or more "
            "ready-made colour spreads (described below).\n\n"
            "• Layout options — the finer print settings (spacer squares, sizing, "
            "page margin, resolution). The defaults are sensible, so feel free to "
            "leave these alone until you need them.\n\n"
            "About “Generate colour sets”:\n\n"
            "Tick any combination of these five sets and ChromIQ lays them down "
            "one after another. Each set shows how many patches it adds, and a "
            "running total appears underneath, so you always know how big the "
            "chart will get before you create it:\n\n"
            "• 3D RGB cube — an even grid of colours across the whole range. You "
            "pick how many steps each of red, green and blue is split into, and "
            "the chart then holds every combination (for example 6 steps makes "
            "6×6×6 = 216 patches). A solid, neutral foundation for almost any "
            "profile.\n\n"
            "• Skin tones (Fitzpatrick) — lifelike skin colours running light to "
            "dark through each of the six Fitzpatrick skin types, now reaching "
            "from porcelain-pale highlights down to very deep, faintly cool "
            "shadows. 'Per type' sets how many shades each type gets from light "
            "to dark; 'Ranges' adds that many parallel ramps, each nudged a "
            "little in hue, so a single skin type is covered by a small spread of "
            "tones rather than one straight line — handy because real faces vary. "
            "Worth adding whenever faces and portraits matter most.\n\n"
            "• Oceans (blues) — extra colours packed into the green-turquoise "
            "to deep-blue part of the range, where wide-gamut papers and inks "
            "reach furthest (it now dips into the greenish turquoise too). "
            "'Per layer' is how many patches each sheet holds and 'Layers' is how "
            "many sheets, so the two multiply (24 per layer × 3 layers = 72). The "
            "sheets are gently angled rather than one flat blanket, so the whole "
            "turquoise corner is filled in depth. Helpful for skies, water and "
            "deep blues.\n\n"
            "• Foliage (greens) — a spread of forest, jungle and leaf greens, for "
            "landscapes and nature shots where the greens carry the picture. As "
            "with the blues, 'Per layer' × 'Layers' patches are spread across "
            "angled sheets so the green part of the range is covered with more "
            "depth.\n\n"
            "• Near-neutral greys — a grey ramp from black to white and, at each "
            "step, a ring of gentle tints just off neutral. This is what helps "
            "greys print cleanly without an unwanted colour cast — the most "
            "important region for a clean profile. You set how many grey steps "
            "there are, how far the tints stray from neutral ('offset'), and how "
            "many rings to circle each grey with ('rings'): one ring is six "
            "tints, and each extra ring adds a wider, denser one (12, then 18 "
            "tints) for fuller near-neutral coverage when you want it. Set rings "
            "to 0 for a plain neutral ramp with no tints at all — a "
            "black-and-white wedge — in which case 'offset' has no effect.\n\n"
            "• Saturated edges — the most vivid colours the printer can manage. "
            "'Per edge' traces the twelve edges of the colour cube — the gamut "
            "wireframe (black up to each pure colour and on to white, plus the "
            "colourful edges between); 'per face' goes further and also fills the "
            "six cube faces — the full gamut surface — with that many patches per "
            "side, or leave it at 0 for edges only. This outer boundary is exactly "
            "where profiles tend to go wrong, so it pays to sample it well.\n\n"
            "• Highlights & shadows — extra detail at the two ends where printers "
            "struggle most: pale tints just below paper white, and deep tones just "
            "above black, spread across every hue. These ends often band or block "
            "up, and the cube alone samples them thinly. 'Per end' is how many "
            "patches go at each end (so the set adds twice that many), and 'depth' "
            "is how far in from white and black the tones reach.\n\n"
            "• Pastels — soft, muted colours all around the hue wheel: dusty blues, "
            "sages, soft pinks, taupes. This is where a great deal of real "
            "photography actually lives — the gentle region between the clean greys "
            "and the vivid sets. Each of the 'layers' is a chroma shell — from "
            "barely-tinted near-greys out to fuller pastels — of 'per layer' "
            "patches, so the two multiply.\n\n"
            "• From image — load one of your own photos and ChromIQ finds its most "
            "representative colours and adds them to the chart, so the profile is "
            "tuned to the kind of pictures you really print. Click 'Load image…', "
            "pick a file, and set how many 'Colours' to pull out. Lovely combined "
            "with a cube for all-round coverage plus your image's own palette on "
            "top.\n\n"
            "• Fill remaining gaps — a tidy-up that comes last. After the sets you "
            "picked are laid down, it scatters extra patches into the empty parts "
            "of colour space — evenly and without repeating — until the chart "
            "reaches the size you ask for. 'Fill to' is that target total, so the "
            "whole chart lands on a round number with nothing left clumped or "
            "bare.\n\n"
            "Mix them freely — say a 3D cube for overall coverage plus "
            "near-neutral greys for clean neutrals, or skin tones plus greens for "
            "portraits out in nature.\n\n"
            "• Ensure unique colours — when this is ticked and your sets happen to "
            "share a colour (for example a 3D cube and a grey ramp both include "
            "black and white), ChromIQ keeps one and nudges the duplicates apart "
            "by a tiny amount, so no colour is printed and measured twice. The "
            "patch total stays the same. Leave it on unless you have a reason not "
            "to.\n\n"
            "When you confirm, your new chart opens in the editor — there you can "
            "drag patches around, recolour them, add or remove some, and save when "
            "it's ready."),
            self, min_width=520))
        # NB: ``head`` is added to the dialog's ``outer`` layout (above the
        # full-width spectrum stripe), not to the scrolled content, so the
        # heading spans the window and the cube preview sits below it.

        # --- Load setup from preset (#55) ------------------------------------
        # Create Chart presets that were saved carrying a creation recipe
        # (Set B) can be reloaded here in one go — colour sets, instrument /
        # paper and layout. The row is ALWAYS shown (even with no qualifying
        # presets) so the layout stays consistent and users know it's there
        # (Knut's request); the list is just "None" until a preset with a saved
        # setup exists. Selecting one applies its recipe to the whole window.
        self._preset_recipes = self._available_preset_recipes()
        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel(tr("Load setup from preset:")))
        self._preset_setup_combo = NoScrollComboBox(self)
        self._preset_setup_combo.addItem(tr("None"), None)
        for pname in self._preset_recipes:
            self._preset_setup_combo.addItem(pname, pname)
        _as_compact(self._preset_setup_combo)
        self._preset_setup_combo.activated.connect(
            self._on_preset_setup_selected)
        pr_row.addWidget(self._preset_setup_combo, 1)
        pr_row.addWidget(_magenta_tip(
            tr("Load setup from preset"),
            tr("Load the full New-chart setup — colour sets, instrument, "
               "paper and layout — that was saved with a preset, so you can "
               "reuse or tweak an existing design instead of setting "
               "everything by hand.\n\nOnly presets saved with such a setup "
               "show up here, so the list stays empty (just \"None\") until "
               "you save one.")))
        lay.addLayout(pr_row)

        # --- Chart identity --------------------------------------------------
        # The chart name is no longer asked for here — it's chosen later when
        # the chart is saved (Save & apply), which is what becomes the folder
        # and file names. The basename stays a neutral "chart" placeholder
        # until then (see _on_ok / result_basename).
        chart_box = QGroupBox(tr("Chart"), self)
        cg = QGridLayout(chart_box)
        cg.addWidget(QLabel(tr("Instrument:")), 0, 0)
        self._instr = NoScrollComboBox(chart_box)
        for code, label in _INSTRUMENTS:
            self._instr.addItem(label, code)
        cg.addWidget(self._instr, 0, 1)
        # Breathing room between the instrument combo and the Paper label so the
        # two controls don't read as one run-on field.
        _paper_lbl = QLabel(tr("Paper:"))
        _paper_lbl.setContentsMargins(20, 0, 0, 0)
        cg.addWidget(_paper_lbl, 0, 2)
        self._paper = NoScrollComboBox(chart_box)
        for code in _PAPER_ORDER:
            self._paper.addItem(
                _PAPER_LABELS_WITH_CUSTOM.get(code, code), code)
        _as_compact(self._instr, self._paper)
        # The compact closed combo (max-height 22px) otherwise yields cramped
        # dropdown rows; give each popup view a comfortable row height so the
        # open list is easy to read. Scoped to these two combos' views only.
        for _cb in (self._instr, self._paper):
            _cb.view().setStyleSheet(
                "QAbstractItemView::item { min-height: 28px; padding: 3px 8px; }")
        # Default to A4 portrait
        ix = self._paper.findData("A4")
        if ix >= 0:
            self._paper.setCurrentIndex(ix)
        cg.addWidget(self._paper, 0, 3)
        # Custom W/H row appears under the paper combo when "Custom" is
        # selected — same UX as the Create Chart tab.
        self._paper_custom_row = QWidget(chart_box)
        cust_l = QHBoxLayout(self._paper_custom_row)
        cust_l.setContentsMargins(0, 0, 0, 0)
        cust_l.setSpacing(6)
        cust_l.addWidget(QLabel(tr("W (mm):")))
        self._paper_w = NoScrollSpinBox(self._paper_custom_row)
        self._paper_w.setRange(10, 9999)
        self._paper_w.setValue(210)
        cust_l.addWidget(self._paper_w)
        cust_l.addWidget(QLabel(tr("H (mm):")))
        self._paper_h = NoScrollSpinBox(self._paper_custom_row)
        self._paper_h.setRange(10, 9999)
        self._paper_h.setValue(297)
        cust_l.addWidget(self._paper_h)
        cust_l.addStretch(1)
        _as_compact(self._paper_w, self._paper_h)
        self._paper_custom_row.setVisible(False)
        # Sit the W/H row directly under the Paper combo it belongs to (cols 2–3)
        # instead of stranded on the far left under "Instrument".
        cg.addWidget(self._paper_custom_row, 1, 2, 1, 2)
        # Keep the two control columns balanced so the Instrument/Paper combos
        # get equal width and the row doesn't crowd the label gap.
        cg.setColumnStretch(1, 1)
        cg.setColumnStretch(3, 1)
        self._paper.currentIndexChanged.connect(self._on_paper_changed)
        lay.addWidget(chart_box)

        # --- Source ---------------------------------------------------------
        src_box = QGroupBox(tr("Patches"), self)
        sl = QVBoxLayout(src_box)
        self._mode_blank = QRadioButton(tr("Blank canvas (add patches by hand)"), src_box)
        self._mode_seed = QRadioButton(tr("Seed from targen (optimised patch set)"), src_box)
        self._mode_paste = QRadioButton(tr("Paste colour values (or load a file)"), src_box)
        self._mode_seed.setChecked(True)
        sl.addWidget(self._mode_seed)
        seed_row = QHBoxLayout()
        seed_row.addSpacing(22)
        seed_row.addWidget(QLabel(tr("Patches:")))
        self._count = NoScrollSpinBox(src_box)
        self._count.setRange(8, 4000)
        self._count.setValue(200)
        self._count.setObjectName("compact_input")
        seed_row.addWidget(self._count)
        seed_row.addStretch(1)
        sl.addLayout(seed_row)
        sl.addWidget(self._mode_blank)
        sl.addWidget(self._mode_paste)
        paste_indent = QVBoxLayout()
        paste_indent.setContentsMargins(22, 0, 0, 0)
        self._paste_edit = QPlainTextEdit(src_box)
        self._paste_edit.setPlaceholderText(
            tr("One colour per line — hex (#ff00aa or ff00aa) or RGB "
            "(255,0,170 / 1.0 0 0.67). Scale auto-detected.")
        )
        self._paste_edit.setFixedHeight(110)
        paste_indent.addWidget(self._paste_edit)
        paste_btns = QHBoxLayout()
        load_btn = QPushButton(tr("Load from file…"), src_box)
        load_btn.setObjectName("compact_input")  # match the compact "Load image…" button
        load_btn.clicked.connect(self._load_paste_file)
        self._paste_status = QLabel("", src_box)
        self._paste_status.setStyleSheet("color: #888;")
        paste_btns.addWidget(load_btn)
        paste_btns.addStretch(1)
        paste_btns.addWidget(self._paste_status)
        paste_indent.addLayout(paste_btns)
        sl.addLayout(paste_indent)
        self._paste_edit.textChanged.connect(self._update_paste_count)

        # Generate colour sets — combinable generators (#37). Each ticked set
        # contributes its patches, concatenated top-to-bottom into the program.
        self._mode_generate = QRadioButton(tr("Generate colour sets"), src_box)
        sl.addWidget(self._mode_generate)
        sl.addLayout(self._build_generate_panel(src_box))

        # Enable/disable subcontrols by mode
        self._mode_seed.toggled.connect(lambda on: self._count.setEnabled(on))
        for r in (self._mode_blank, self._mode_seed, self._mode_paste,
                  self._mode_generate):
            r.toggled.connect(self._refresh_source_widgets)
        self._refresh_source_widgets()
        lay.addWidget(src_box)

        # --- Layout options -------------------------------------------------
        opt_box = QGroupBox(tr("Layout options (printtarg)"), self)
        og = QGridLayout(opt_box)
        # Spacer-mode checkboxes wired as a mutex group, with the all-off
        # state permitted. "None" disables Spacer scale (-A) since there
        # are no spacers to scale.
        og.addWidget(QLabel(tr("Spacers:")), 0, 0)
        self._sp_colored = QCheckBox(tr("Coloured"), opt_box)
        self._sp_bw      = QCheckBox(tr("B&&W"), opt_box)
        self._sp_none    = QCheckBox(tr("None"), opt_box)
        self._sp_colored.setChecked(True)
        _wire_spacer_mutex((self._sp_colored, self._sp_bw, self._sp_none))
        sp_row = QHBoxLayout()
        for cb in (self._sp_colored, self._sp_bw, self._sp_none):
            sp_row.addWidget(cb)
        sp_row.addStretch(1)
        og.addLayout(sp_row, 0, 1, 1, 3)
        self._sp_none.toggled.connect(self._refresh_spacer_scale_enabled)

        og.addWidget(QLabel(tr("Patch scale (-a):")), 1, 0)
        self._patch_scale = NoScrollDoubleSpinBox(opt_box)
        self._patch_scale.setRange(0.3, 3.0)
        self._patch_scale.setSingleStep(0.05)
        self._patch_scale.setValue(1.0)
        og.addWidget(self._patch_scale, 1, 1)
        og.addWidget(QLabel(tr("Spacer scale (-A):")), 1, 2)
        self._spacer_scale = NoScrollDoubleSpinBox(opt_box)
        self._spacer_scale.setRange(0.3, 3.0)
        self._spacer_scale.setSingleStep(0.05)
        self._spacer_scale.setValue(1.0)
        og.addWidget(self._spacer_scale, 1, 3)

        # Margin / DPI / bit depth — match the Create Chart tab so the
        # editor offers the same printtarg knobs.
        og.addWidget(QLabel(tr("Margin (-m / -M, mm):")), 2, 0)
        self._margin = NoScrollSpinBox(opt_box)
        self._margin.setRange(0, 50)
        self._margin.setValue(6)
        self._margin.setToolTip(tr("Inter-strip and outer page margin in mm. "
                                "printtarg's default is 6."))
        og.addWidget(self._margin, 2, 1)
        og.addWidget(QLabel(tr("DPI:")), 2, 2)
        self._dpi = NoScrollSpinBox(opt_box)
        self._dpi.setRange(72, 1200)
        self._dpi.setSingleStep(50)
        self._dpi.setValue(300)
        og.addWidget(self._dpi, 2, 3)
        _as_compact(self._patch_scale, self._spacer_scale,
                    self._margin, self._dpi)

        og.addWidget(QLabel(tr("Bit depth:")), 3, 0)
        self._bd_8 = QRadioButton(tr("8-bit"), opt_box)
        self._bd_16 = QRadioButton(tr("16-bit"), opt_box)
        self._bd_8.setChecked(True)
        bd_grp = QButtonGroup(opt_box)
        bd_grp.addButton(self._bd_8)
        bd_grp.addButton(self._bd_16)
        bd_row = QHBoxLayout()
        bd_row.addWidget(self._bd_8)
        bd_row.addWidget(self._bd_16)
        bd_row.addStretch(1)
        og.addLayout(bd_row, 3, 1, 1, 3)

        # Instrument-conditional knobs — laid out in a self-contained 2-col
        # grid below the always-visible options. We toggle the *whole row*
        # visibility from the instrument signal so the dialog stays compact.
        self._cb_L = QCheckBox(tr("Suppress left clip border (-L)"), opt_box)
        self._cb_L.setToolTip(tr("i1Pro / 3+ only. Frees the strip for patches."))
        self._cb_P = QCheckBox(tr("Don't limit strip length (-P)"), opt_box)
        self._cb_P.setToolTip(tr("i1Pro / 3+ only. Lets a long strip span multiple "
                              "physical strokes for very tall charts."))
        self._cb_h = QCheckBox(tr("Double density (-h)"), opt_box)
        self._cb_h.setToolTip(tr("ColorMunki only. Tighter strip layout for the "
                              "ColorMunki rig. Mutually exclusive with Triple."))
        self._cb_td = QCheckBox(tr("Triple density (i1Pro layout emulation)"), opt_box)
        self._cb_td.setToolTip(
            tr("ColorMunki + rig only. Renders the chart with the i1Pro strip "
            "layout (printtarg -ii1) at the tuned scale (1.3) / margin (5) / "
            "strip-limit-off / left-border-suppressed preset, then patches "
            "TARGET_INSTRUMENT back to ColorMunki so chartread still drives "
            "your meter. Mutually exclusive with Double density.")
        )
        og.addWidget(self._cb_L,  4, 0, 1, 2)
        og.addWidget(self._cb_P,  4, 2, 1, 2)
        og.addWidget(self._cb_h,  5, 0, 1, 2)
        og.addWidget(self._cb_td, 5, 2, 1, 2)
        # Triple ↔ Double mutual exclusion + triple-density preset apply
        self._cb_td.toggled.connect(self._on_td_toggled)
        self._cb_h.toggled.connect(self._on_dd_toggled)
        # Initial visibility for the conditional rows
        self._instr.currentIndexChanged.connect(self._refresh_instr_widgets)
        self._refresh_instr_widgets()
        lay.addWidget(opt_box)

        btns = QHBoxLayout()
        restore = QPushButton(tr("Restore defaults"), self)
        restore.setToolTip(tr("Reset every setting on this window — instrument, "
                           "paper, layout options and colour sets — back to "
                           "their defaults."))
        restore.clicked.connect(self._restore_factory_defaults)
        btns.addWidget(restore)
        btns.addWidget(self._make_fold_button())
        btns.addStretch(1)
        ok = QPushButton(tr("Create"), self)
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        cancel = QPushButton(tr("Cancel"), self)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        # Controls on the left (kept at their natural width so the options are
        # never clipped), the foldable live 3D cube on the right.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(head)
        outer.addWidget(_SpectrumStripe(self))
        outer.addLayout(self._build_body(scroll, content.sizeHint().width()), 1)
        btns.setContentsMargins(12, 4, 12, 10)
        outer.addLayout(btns)
        # Height fits common laptop screens; width follows the fold state.
        self._init_fold_state(760)

        # Prefer the chart's own creation recipe (reopened to tweak/recreate it);
        # otherwise restore the app-wide last-used state so creating another chart
        # doesn't start from scratch.
        if isinstance(self._initial_recipe, dict):
            self._apply_gen_state(self._initial_recipe)
        else:
            self._restore_gen_state()
        self._do_push_live_preview()   # seed the cube with the restored state

    # -- last-used settings persistence -----------------------------------
    # The widget suffixes whose checked/value state is remembered between
    # New-chart sessions (attribute = "_gen_<name>").
    _GEN_CHECKS = ("cube", "skin", "blues", "greens", "sunrises", "flamingos",
                   "greys", "edges", "hs", "pastel", "image", "whiteblack",
                   "fill", "unique")
    _GEN_SPINS = ("cube_n", "skin_n", "skin_ranges", "blues_n", "blues_layers",
                  "greens_n", "greens_layers", "sunrises_n", "sunrises_layers",
                  "flamingos_n", "flamingos_layers",
                  "greys_n", "greys_off",
                  "greys_rings", "edges_n", "edges_faces", "hs_n", "hs_reach",
                  "pastel_n", "pastel_layers", "image_n", "whiteblack_n",
                  "fill_to")
    # Factory defaults — what "Restore defaults" resets the whole window to
    # (also the fresh-install state). Must mirror the inline widget defaults.
    # Covers the source mode, chart instrument/paper, the seed count, every
    # Layout-options knob and the colour sets, so one click puts every
    # parameter back to a known-good baseline.
    _GEN_FACTORY = {
        "mode": "seed",
        "instr": "i1",
        "paper": "A4",
        "paper_w": 210, "paper_h": 297,
        "count": 200,
        "layout": {"spacer_mode": "colored", "patch_scale": 1.0,
                   "spacer_scale": 1.0, "margin": 6, "dpi": 300,
                   "bit16": False, "L": False, "P": False, "h": False,
                   "td": False},
        "cb": {"cube": True, "skin": True, "blues": True, "greens": True,
               "sunrises": True, "flamingos": True, "greys": True,
               "edges": False, "hs": False,
               "pastel": False, "image": False, "whiteblack": False,
               "fill": False, "unique": True},
        "sp": {"cube_n": 8, "skin_n": 8, "skin_ranges": 3, "blues_n": 64,
               "blues_layers": 3, "greens_n": 64, "greens_layers": 3,
               "sunrises_n": 64, "sunrises_layers": 3,
               "flamingos_n": 64, "flamingos_layers": 3,
               "greys_n": 16, "greys_off": 4, "greys_rings": 1, "edges_n": 1,
               "edges_faces": 0, "hs_n": 24, "hs_reach": 16, "pastel_n": 24,
               "pastel_layers": 2, "image_n": 24, "whiteblack_n": 1,
               "fill_to": 1000},
    }

    def _spacer_mode(self) -> str:
        """Current spacer-mode mutex selection (matches _on_ok's mapping)."""
        if self._sp_bw.isChecked():
            return "bw"
        if self._sp_none.isChecked():
            return "none"
        return "colored"

    def _collect_gen_sets(self) -> dict:
        """The colour-set checkboxes + size spins as a {"cb":…, "sp":…} dict.
        Shared by the New-chart persistence and the editor's Add dialog."""
        return {
            "cb": {n: getattr(self, f"_gen_{n}").isChecked()
                   for n in self._GEN_CHECKS},
            "sp": {n: getattr(self, f"_gen_{n}").value()
                   for n in self._GEN_SPINS},
        }

    def _apply_gen_sets(self, st: dict) -> None:
        """Set the colour-set checkboxes + size spins from a {"cb":…, "sp":…}
        dict (missing/unknown keys skipped). Does not touch the source mode or
        any chart/layout widget, so subclasses without those can reuse it.

        Older saved states may carry an ``edges_auto`` flag from the retired
        edges↔cube auto-coupling; it is simply ignored now (the 'between' control
        keys off the cube directly)."""
        for n, val in (st.get("sp") or {}).items():
            w = getattr(self, f"_gen_{n}", None)
            if w is not None:
                try:
                    w.setValue(int(val))
                except (TypeError, ValueError):
                    pass
        for n, val in (st.get("cb") or {}).items():
            w = getattr(self, f"_gen_{n}", None)
            if w is not None:
                w.setChecked(bool(val))

    def _collect_gen_state(self) -> dict:
        mode = ("generate" if self._mode_generate.isChecked() else
                "paste" if self._mode_paste.isChecked() else
                "blank" if self._mode_blank.isChecked() else "seed")
        return {
            "mode": mode,
            **self._collect_gen_sets(),
            "instr": self._instr.currentData(),
            "paper": self._paper.currentData(),
            "paper_w": self._paper_w.value(),
            "paper_h": self._paper_h.value(),
            "count": self._count.value(),
            "layout": {
                "spacer_mode": self._spacer_mode(),
                "patch_scale": self._patch_scale.value(),
                "spacer_scale": self._spacer_scale.value(),
                "margin": self._margin.value(),
                "dpi": self._dpi.value(),
                "bit16": self._bd_16.isChecked(),
                "L": self._cb_L.isChecked(),
                "P": self._cb_P.isChecked(),
                "h": self._cb_h.isChecked(),
                "td": self._cb_td.isChecked(),
            },
        }

    def _apply_gen_state(self, st: dict) -> None:
        """Set every window widget from a saved/factory state dict. Unknown /
        missing keys are skipped, so old saved states load forward-compatibly.

        Order matters: the instrument combo is set first (its change handler
        hides + clears the strip-only -L / -P knobs for non-strip readers), and
        within the layout block the density toggles run before the values they
        overwrite, so a restored triple-density preset doesn't get clobbered."""
        # Chart identity — instrument + paper (+ custom dimensions).
        instr = st.get("instr")
        if instr is not None:
            ix = self._instr.findData(instr)
            if ix >= 0:
                self._instr.setCurrentIndex(ix)
        paper = st.get("paper")
        if paper is not None:
            ix = self._paper.findData(paper)
            if ix >= 0:
                self._paper.setCurrentIndex(ix)
        for key, w in (("paper_w", self._paper_w), ("paper_h", self._paper_h),
                       ("count", self._count)):
            if key in st:
                try:
                    w.setValue(int(st[key]))
                except (TypeError, ValueError):
                    pass

        # Layout options. Spacer mode + density toggles first; their signal
        # handlers (mutex, triple-density preset) settle before we restore the
        # exact scale / margin / -L / -P values the user had saved.
        lo = st.get("layout") or {}
        sm = lo.get("spacer_mode")
        if sm == "bw":
            self._sp_bw.setChecked(True)
        elif sm == "none":
            self._sp_none.setChecked(True)
        elif sm == "colored":
            self._sp_colored.setChecked(True)
        if "h" in lo:
            self._cb_h.setChecked(bool(lo["h"]))
        if "td" in lo:
            self._cb_td.setChecked(bool(lo["td"]))
        if "L" in lo:
            self._cb_L.setChecked(bool(lo["L"]))
        if "P" in lo:
            self._cb_P.setChecked(bool(lo["P"]))
        if "patch_scale" in lo:
            self._patch_scale.setValue(float(lo["patch_scale"]))
        if "spacer_scale" in lo:
            self._spacer_scale.setValue(float(lo["spacer_scale"]))
        if "margin" in lo:
            self._margin.setValue(int(lo["margin"]))
        if "dpi" in lo:
            self._dpi.setValue(int(lo["dpi"]))
        if "bit16" in lo:
            (self._bd_16 if lo["bit16"] else self._bd_8).setChecked(True)

        self._apply_gen_sets(st)
        radio = {"generate": self._mode_generate, "paste": self._mode_paste,
                 "blank": self._mode_blank, "seed": self._mode_seed}.get(
                     st.get("mode"))
        if radio is not None:
            radio.setChecked(True)
        self._refresh_source_widgets()

    # -- "Load setup from preset" (#55) -----------------------------------
    def _available_preset_recipes(self) -> dict:
        """Presets that carry a populated creation recipe (Set B), as
        ``{display_name: recipe_dict}``.

        Built-in Wide-gamut presets (bundled recipes, shown with a ★) come first,
        then the user's Create Chart presets. Name collisions (#55): a custom
        preset identical to a built-in of the same name is dropped (the built-in
        already represents it); a custom that differs keeps its own name (the ★
        distinguishes the built-in)."""
        import json
        out: dict = {}
        builtin: dict = {}
        try:
            from core.resource_path import resource_path
            p = resource_path("assets/charts/knut/rgb/widegamut/recipes.json")
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
                builtin = {k: v for k, v in raw.items()
                           if isinstance(v, dict) and v}
        except Exception:  # noqa: BLE001 — never block opening the window
            pass
        for name, rec in builtin.items():
            out[f"★ {name}"] = rec
        if self._settings is None:
            return out
        try:
            from core.preset_store import load_presets
            for name, payload in load_presets("create_chart", self._settings).items():
                rec = payload.get("editor_recipe") if isinstance(payload, dict) else None
                if not (isinstance(rec, dict) and rec):
                    continue
                if builtin.get(name) == rec:
                    continue  # identical to a built-in → already listed
                disp, n = name, 2
                while disp in out:        # keep every distinct recipe visible
                    disp, n = f"{name} ({n})", n + 1
                out[disp] = rec
        except Exception:  # noqa: BLE001
            pass
        return out

    def _on_preset_setup_selected(self, idx: int) -> None:
        name = self._preset_setup_combo.itemData(idx)
        rec = self._preset_recipes.get(name) if name else None
        if isinstance(rec, dict):
            self._apply_gen_state(rec)
            self._do_push_live_preview()

    def _save_gen_state(self) -> None:
        if self._settings is not None:
            self._settings.set("new_chart_gen", self._collect_gen_state())

    def _restore_gen_state(self) -> None:
        st = self._settings.get("new_chart_gen", None) if self._settings else None
        if isinstance(st, dict):
            self._apply_gen_state(st)

    def _restore_factory_defaults(self) -> None:
        """Reset every parameter in the window — source mode, instrument,
        paper, seed count, Layout options and the colour sets — back to its
        factory default."""
        self._apply_gen_state(self._GEN_FACTORY)

    # -- generate-colour-sets panel ---------------------------------------
    def _build_generate_panel(self, parent: QWidget) -> QVBoxLayout:
        """Build the combinable colour-set generators (#37) under the
        "Generate colour sets" radio: a checkbox + size control(s) + live
        count per generator, and a running total."""
        # Patches already on the chart we're adding to (empty for New-chart);
        # "Fill remaining gaps" counts these toward its target so it tops the
        # whole chart up rather than appending that many patches (#51).
        if not hasattr(self, "_existing_patches"):
            self._existing_patches = []
        indent = QVBoxLayout()
        indent.setContentsMargins(22, 0, 0, 0)
        self._gen_panel = QWidget(parent)
        gg = QGridLayout(self._gen_panel)
        gg.setContentsMargins(0, 0, 0, 0)
        gg.setHorizontalSpacing(8)
        gg.setVerticalSpacing(4)

        def _spin(lo: int, hi: int, val: int) -> NoScrollSpinBox:
            s = NoScrollSpinBox(self._gen_panel)
            s.setRange(lo, hi)
            s.setValue(val)
            s.setObjectName("compact_input")
            s.valueChanged.connect(self._update_gen_counts)
            return s

        def _count_label() -> QLabel:
            lb = QLabel("", self._gen_panel)
            lb.setStyleSheet("color: #888;")
            return lb

        # 3D RGB cube — N per axis ⇒ N³ patches.
        self._gen_cube = QCheckBox(tr("3D RGB cube"), self._gen_panel)
        self._gen_cube.setChecked(True)
        self._gen_cube.setToolTip(tr("An even N×N×N grid across the whole RGB "
                                  "range. N is the steps per axis."))
        self._gen_cube_n = _spin(2, 30, 8)
        self._gen_cube_count = _count_label()
        gg.addWidget(self._gen_cube, 0, 0)
        gg.addWidget(QLabel(tr("per axis:")), 0, 1)
        gg.addWidget(self._gen_cube_n, 0, 2)
        gg.addWidget(self._gen_cube_count, 0, 7)

        # Fitzpatrick skin tones — per-type ramp × parallel hue ranges.
        self._gen_skin = QCheckBox(tr("Skin tones (Fitzpatrick)"), self._gen_panel)
        self._gen_skin.setChecked(True)
        self._gen_skin.setToolTip(tr("Light→dark ramps through each of the six "
                                  "Fitzpatrick skin phototypes. 'Ranges' adds "
                                  "parallel ramps offset in hue for broader "
                                  "coverage."))
        self._gen_skin_n = _spin(1, 36, 8)
        self._gen_skin_ranges = _spin(1, 5, 3)
        self._gen_skin_count = _count_label()
        gg.addWidget(self._gen_skin, 2, 0)
        gg.addWidget(QLabel(tr("per type:")), 2, 1)
        gg.addWidget(self._gen_skin_n, 2, 2)
        gg.addWidget(QLabel(tr("ranges:")), 2, 3)
        gg.addWidget(self._gen_skin_ranges, 2, 4)
        gg.addWidget(self._gen_skin_count, 2, 7)

        # Enhanced blues / turquoise.
        self._gen_blues = QCheckBox(tr("Oceans (blues)"), self._gen_panel)
        self._gen_blues.setChecked(True)
        self._gen_blues.setToolTip(tr("Denser sampling of the green-turquoise→blue "
                                   "band wide-gamut spaces stretch furthest. "
                                   "Each of the 'layers' is a non-parallel sheet "
                                   "of 'per layer' patches, so the two multiply."))
        self._gen_blues_n = _spin(1, 200, 64)
        self._gen_blues_layers = _spin(1, 10, 3)
        self._gen_blues_count = _count_label()
        gg.addWidget(self._gen_blues, 3, 0)
        gg.addWidget(QLabel(tr("per layer:")), 3, 1)
        gg.addWidget(self._gen_blues_n, 3, 2)
        gg.addWidget(QLabel(tr("layers:")), 3, 3)
        gg.addWidget(self._gen_blues_layers, 3, 4)
        gg.addWidget(self._gen_blues_count, 3, 7)

        # Enhanced greens (foliage).
        self._gen_greens = QCheckBox(tr("Foliage (greens)"), self._gen_panel)
        self._gen_greens.setChecked(True)
        self._gen_greens.setToolTip(tr("Forest, jungle and foliage greens for "
                                    "nature images. Each of the 'layers' is a "
                                    "non-parallel sheet of 'per layer' patches, "
                                    "so the two multiply."))
        self._gen_greens_n = _spin(1, 200, 64)
        self._gen_greens_layers = _spin(1, 10, 3)
        self._gen_greens_count = _count_label()
        gg.addWidget(self._gen_greens, 4, 0)
        gg.addWidget(QLabel(tr("per layer:")), 4, 1)
        gg.addWidget(self._gen_greens_n, 4, 2)
        gg.addWidget(QLabel(tr("layers:")), 4, 3)
        gg.addWidget(self._gen_greens_layers, 4, 4)
        gg.addWidget(self._gen_greens_count, 4, 7)

        # Sunrises — the warm band (yellows, oranges, reds, pinks).
        self._gen_sunrises = QCheckBox(tr("Sunrises (warm)"), self._gen_panel)
        self._gen_sunrises.setChecked(True)
        self._gen_sunrises.setToolTip(tr("Golden yellows, oranges, reds and pinks "
                                      "— the warm 'sunrise' side of the gamut the "
                                      "blues and greens sets leave out, for skies, "
                                      "flowers and skin highlights. Each of the "
                                      "'layers' is a non-parallel sheet of 'per "
                                      "layer' patches, so the two multiply."))
        self._gen_sunrises_n = _spin(1, 200, 64)
        self._gen_sunrises_layers = _spin(1, 10, 3)
        self._gen_sunrises_count = _count_label()
        gg.addWidget(self._gen_sunrises, 5, 0)
        gg.addWidget(QLabel(tr("per layer:")), 5, 1)
        gg.addWidget(self._gen_sunrises_n, 5, 2)
        gg.addWidget(QLabel(tr("layers:")), 5, 3)
        gg.addWidget(self._gen_sunrises_layers, 5, 4)
        gg.addWidget(self._gen_sunrises_count, 5, 7)

        # Flamingos — the pink / magenta / indigo band the other bands leave out.
        self._gen_flamingos = QCheckBox(tr("Flamingos (pinks)"), self._gen_panel)
        self._gen_flamingos.setChecked(True)
        self._gen_flamingos.setToolTip(tr("Pinks, magentas and indigos — the band "
                                       "between where Oceans (blues) ends and "
                                       "Sunrises begins, the big gap the other "
                                       "colour-band sets leave in the middle. Great "
                                       "for flowers, fabrics, sunsets and skin. Each "
                                       "of the 'layers' is a non-parallel sheet of "
                                       "'per layer' patches, so the two multiply."))
        self._gen_flamingos_n = _spin(1, 200, 64)
        self._gen_flamingos_layers = _spin(1, 10, 3)
        self._gen_flamingos_count = _count_label()
        gg.addWidget(self._gen_flamingos, 6, 0)
        gg.addWidget(QLabel(tr("per layer:")), 6, 1)
        gg.addWidget(self._gen_flamingos_n, 6, 2)
        gg.addWidget(QLabel(tr("layers:")), 6, 3)
        gg.addWidget(self._gen_flamingos_layers, 6, 4)
        gg.addWidget(self._gen_flamingos_count, 6, 7)

        # Near-neutral greys — neutral ramp + 6 hue rings per step.
        self._gen_greys = QCheckBox(tr("Near-neutral greys"), self._gen_panel)
        self._gen_greys.setChecked(True)
        self._gen_greys.setToolTip(tr("A neutral grey ramp plus, at each step, "
                                   "rings of small hue tints around the neutral "
                                   "axis. 'Rings' is how many rings circle each "
                                   "grey (6, 12, 18 tints) for a denser "
                                   "near-neutral cluster — set it to 0 for a "
                                   "plain neutral ramp with no tints (a "
                                   "black-and-white wedge). 'Offset' sets the "
                                   "first ring's distance, and has no effect when "
                                   "rings is 0."))
        self._gen_greys_n = _spin(1, 64, 16)
        self._gen_greys_rings = _spin(0, 3, 1)
        self._gen_greys_off = _spin(1, 50, 4)
        self._gen_greys_count = _count_label()
        # Kept as a field so it can be greyed out alongside the offset spin when
        # rings is 0 (offset then has no effect).
        self._gen_greys_off_label = QLabel(tr("offset:"))
        gg.addWidget(self._gen_greys, 7, 0)
        gg.addWidget(QLabel(tr("steps:")), 7, 1)
        gg.addWidget(self._gen_greys_n, 7, 2)
        gg.addWidget(QLabel(tr("rings:")), 7, 3)
        gg.addWidget(self._gen_greys_rings, 7, 4)
        gg.addWidget(self._gen_greys_off_label, 7, 5)
        gg.addWidget(self._gen_greys_off, 7, 6)
        gg.addWidget(self._gen_greys_count, 7, 7)

        # Saturated edges — the gamut boundary, locked to the 3D cube's grid so
        # the infill stays even at any density (Knut, #78). Placed directly under
        # the 3D cube (grid row 1) since the two are interdependent — the widgets
        # are built here but the grid row puts the controls right below the cube.
        self._gen_edges = QCheckBox(tr("Saturated edges"), self._gen_panel)
        self._gen_edges.setToolTip(tr("The most saturated colours the printer can "
                                   "reach — the boundary of the RGB cube, where "
                                   "profiles err most. This set works hand in hand "
                                   "with the 3D cube: 'between' drops that many "
                                   "patches evenly between each pair of neighbouring "
                                   "cube dots along the 12 cube edges (the gamut "
                                   "wireframe), and 'faces' does the same inside each "
                                   "square of the cube's 6 faces (the gamut surface), "
                                   "or 0 for edges only. Because the spacing is tied "
                                   "to the cube, the fill stays even at any setting "
                                   "(1 is one patch midway between each cube dot)."))
        self._gen_edges_n = _spin(0, 5, 1)
        self._gen_edges_faces = _spin(0, 5, 0)
        self._gen_edges_count = _count_label()
        gg.addWidget(self._gen_edges, 1, 0)
        gg.addWidget(QLabel(tr("between:")), 1, 1)
        gg.addWidget(self._gen_edges_n, 1, 2)
        gg.addWidget(QLabel(tr("faces:")), 1, 3)
        gg.addWidget(self._gen_edges_faces, 1, 4)
        gg.addWidget(self._gen_edges_count, 1, 7)

        # Highlights & shadows — detail at the two tonal ends. The label's "&"
        # is doubled so Qt shows it literally instead of eating it as a mnemonic;
        # the tr() key stays the plain text (and the fix covers translations
        # whose names also contain "&", e.g. de "Lichter & Schatten").
        self._gen_hs = QCheckBox(tr("Highlights & shadows").replace("&", "&&"),
                                 self._gen_panel)
        self._gen_hs.setToolTip(tr("Extra detail at the two ends where printers "
                                "struggle: pale tints just below paper white and "
                                "deep tones just above black, spread across every "
                                "hue. 'Per end' is the patches at each end (so the "
                                "total is twice that); 'depth' is how far in from "
                                "white and black the tones reach. The two ends are "
                                "built as mirror images, so they match. This set "
                                "works together with 'Near-neutral greys': when "
                                "that set is on, these patches stay just outside "
                                "its grey rings so no colour is printed twice; "
                                "when it is off, they also fill in the "
                                "near-neutral light and dark tones themselves."))
        self._gen_hs_n = _spin(1, 200, 24)
        self._gen_hs_reach = _spin(2, 45, 16)
        self._gen_hs_count = _count_label()
        gg.addWidget(self._gen_hs, 8, 0)
        gg.addWidget(QLabel(tr("per end:")), 8, 1)
        gg.addWidget(self._gen_hs_n, 8, 2)
        gg.addWidget(QLabel(tr("depth:")), 8, 3)
        gg.addWidget(self._gen_hs_reach, 8, 4)
        gg.addWidget(self._gen_hs_count, 8, 7)

        # Pastels — low-chroma midtones.
        self._gen_pastel = QCheckBox(tr("Pastels"), self._gen_panel)
        self._gen_pastel.setToolTip(tr("Soft, muted colours across every hue — "
                                    "dusty blues, sages, soft pinks and taupes. "
                                    "This is where most photos actually live, "
                                    "between the near-neutral greys and the vivid "
                                    "sets. Each of the 'layers' is a chroma shell "
                                    "of 'per layer' patches, so the two multiply."))
        self._gen_pastel_n = _spin(1, 200, 24)
        self._gen_pastel_layers = _spin(1, 4, 2)
        self._gen_pastel_count = _count_label()
        gg.addWidget(self._gen_pastel, 9, 0)
        gg.addWidget(QLabel(tr("per layer:")), 9, 1)
        gg.addWidget(self._gen_pastel_n, 9, 2)
        gg.addWidget(QLabel(tr("layers:")), 9, 3)
        gg.addWidget(self._gen_pastel_layers, 9, 4)
        gg.addWidget(self._gen_pastel_count, 9, 7)

        # From image — the most representative colours of a chosen photo.
        self._gen_image = QCheckBox(tr("From image"), self._gen_panel)
        self._gen_image.setToolTip(tr("Load a photo and ChromIQ picks out its most "
                                   "representative colours and adds them, so the "
                                   "profile is tuned to the kind of images you "
                                   "actually print. 'Colours' is how many to "
                                   "extract."))
        self._gen_image_px = None        # decoded (N,3) pixels, or None
        self._gen_image_name = ""
        self._gen_image_btn = QPushButton(tr("Load image…"), self._gen_panel)
        self._gen_image_btn.setObjectName("compact_input")
        self._gen_image_btn.clicked.connect(self._load_gen_image)
        self._gen_image_n = _spin(1, 500, 24)
        self._gen_image_count = _count_label()
        gg.addWidget(self._gen_image, 10, 0)
        gg.addWidget(self._gen_image_btn, 10, 1, 1, 2)
        gg.addWidget(QLabel(tr("colours:")), 10, 3)
        gg.addWidget(self._gen_image_n, 10, 4)
        gg.addWidget(self._gen_image_count, 10, 7)

        # Pure white & black — the two tonal anchors, N of each, kept verbatim.
        self._gen_whiteblack = QCheckBox(
            tr("Pure white & black").replace("&", "&&"), self._gen_panel)
        self._gen_whiteblack.setToolTip(tr("Adds pure paper white and the "
                                        "deepest black the printer can lay — two "
                                        "anchors that matter for a good profile. "
                                        "'Each' is how many of white and of black "
                                        "to include; they're kept even when "
                                        "'Ensure unique colours' is on, which is "
                                        "handy for averaging repeats. The 3D cube "
                                        "and near-neutral greys already include one "
                                        "of each, and any they provide counts "
                                        "toward your number."))
        self._gen_whiteblack_n = _spin(1, 50, 1)
        self._gen_whiteblack_count = _count_label()
        gg.addWidget(self._gen_whiteblack, 11, 0)
        gg.addWidget(QLabel(tr("each:")), 11, 1)
        gg.addWidget(self._gen_whiteblack_n, 11, 2)
        gg.addWidget(self._gen_whiteblack_count, 11, 7)

        # Fill remaining gaps — blue-noise top-up of whatever's left sparse.
        # Special: its count depends on the combined total of the sets above.
        self._gen_fill = QCheckBox(tr("Fill remaining gaps"), self._gen_panel)
        self._gen_fill.setToolTip(tr("After the sets above, scatter extra patches "
                                  "into the empty parts of colour space until the "
                                  "chart reaches the size you set, evenly and "
                                  "without repeating. 'Fill to' is the target "
                                  "total patch count."))
        self._gen_fill_to = _spin(1, 30000, 1000)
        self._gen_fill_count = _count_label()
        gg.addWidget(self._gen_fill, 12, 0)
        gg.addWidget(QLabel(tr("fill to:")), 12, 1)
        gg.addWidget(self._gen_fill_to, 12, 2)
        gg.addWidget(self._gen_fill_count, 12, 7)

        # A per-set ⓘ icon (col 8) opens the set's explanation in its own little
        # window — more discoverable than a hover tooltip. The body reuses each
        # checkbox's tooltip (already written + translated), the title is the set
        # name, so this adds no new strings. Titles use the plain name (the "&"
        # in "Highlights & shadows" is fine here — the icon has no mnemonic).
        row_tips = (
            (0, self._gen_cube,   tr("3D RGB cube")),
            (1, self._gen_edges,  tr("Saturated edges")),
            (2, self._gen_skin,   tr("Skin tones (Fitzpatrick)")),
            (3, self._gen_blues,  tr("Oceans (blues)")),
            (4, self._gen_greens, tr("Foliage (greens)")),
            (5, self._gen_sunrises, tr("Sunrises (warm)")),
            (6, self._gen_flamingos, tr("Flamingos (pinks)")),
            (7, self._gen_greys,  tr("Near-neutral greys")),
            (8, self._gen_hs,     tr("Highlights & shadows")),
            (9, self._gen_pastel, tr("Pastels")),
            (10, self._gen_image,  tr("From image")),
            (11, self._gen_whiteblack, tr("Pure white & black")),
            (12, self._gen_fill,  tr("Fill remaining gaps")),
        )
        for row, cb, title in row_tips:
            gg.addWidget(
                _magenta_tip(title, cb.toolTip(), self._gen_panel, min_width=360),
                row, 8, Qt.AlignmentFlag.AlignCenter)

        # The counts all live in one column (7), so they stay left-aligned in a
        # tidy column for every set. Greys' extra "offset:" control sits in cols
        # 5/6 *before* the count (those columns are empty — but reserved at full
        # width by the grid — for the other rows, so every count lines up). Per-set
        # ⓘ icons sit in col 8; a stretchy trailing column (9) soaks up spare width.
        gg.setColumnMinimumWidth(7, 124)
        gg.setColumnStretch(9, 1)

        # Cross-set de-duplication — keep combined patches unique (#37 follow-up).
        self._gen_unique = QCheckBox(tr("Ensure unique colours"), self._gen_panel)
        self._gen_unique.setChecked(True)
        self._gen_unique.setToolTip(tr("When sets are combined, nudge any "
                                    "repeated colours apart by a small offset "
                                    "so no patch is printed twice."))
        self._gen_unique.toggled.connect(self._update_gen_counts)
        gg.addWidget(self._gen_unique, 13, 0, 1, 8)

        self._gen_total = QLabel("", self._gen_panel)
        self._gen_total.setStyleSheet("font-weight: bold;")
        gg.addWidget(self._gen_total, 14, 0, 1, 8)

        # In the Add dialog (a chart already has patches), also show the chart's
        # resulting size — existing patches + the additions — since this is the
        # Add flow, not a fresh layout (#60, Knut). Hidden when there are no
        # existing patches (the New-chart dialog), where it would just repeat.
        self._gen_after_total = QLabel("", self._gen_panel)
        self._gen_after_total.setStyleSheet("color: #909090;")
        self._gen_after_total.setVisible(bool(self._existing_patches))
        gg.addWidget(self._gen_after_total, 15, 0, 1, 8)

        for cb in (self._gen_cube, self._gen_skin, self._gen_blues,
                   self._gen_greens, self._gen_sunrises, self._gen_flamingos,
                   self._gen_greys, self._gen_edges, self._gen_hs,
                   self._gen_pastel, self._gen_image, self._gen_whiteblack,
                   self._gen_fill):
            cb.toggled.connect(self._update_gen_counts)

        # Saturated edges no longer needs to track the cube via a coupled value:
        # its 'between' control places patches *between* the cube's own steps, so
        # it reads the cube's steps-per-axis directly at build time and stays even
        # at any setting (Knut, #78). The old edges↔cube auto-sync is gone (the
        # cube spin already refreshes the counts, which now drive the edges total).
        indent.addWidget(self._gen_panel)
        return indent

    def _edges_cube_n(self) -> int:
        """Steps-per-axis the saturated-edges 'between' fill is keyed to: the 3D
        cube's value when that set is on, else 2 (fill between the corners only),
        so edges/faces stay even and locked to the cube it sits with (Knut, #78)."""
        return self._gen_cube_n.value() if self._gen_cube.isChecked() else 2

    # The generators in fixed concatenation order: (checkbox, builder, counter).
    def _gen_specs(self):
        return (
            (self._gen_cube,
             lambda: G.rgb_cube(self._gen_cube_n.value()),
             lambda: G.rgb_cube_count(self._gen_cube_n.value()),
             self._gen_cube_count),
            # Saturated edges sits right after the cube — matching its panel row
            # (#78) — so the de-dup, which walks this list top-to-bottom keeping
            # earlier patches and nudging later ones clear, processes the sets in
            # the order the user sees them.
            (self._gen_edges,
             lambda: (G.gamut_edges_between(self._edges_cube_n(),
                                            self._gen_edges_n.value())
                      + G.gamut_faces_between(self._edges_cube_n(),
                                              self._gen_edges_faces.value())),
             lambda: (G.gamut_edges_between_count(self._edges_cube_n(),
                                                  self._gen_edges_n.value())
                      + G.gamut_faces_between_count(self._edges_cube_n(),
                                                    self._gen_edges_faces.value())),
             self._gen_edges_count),
            (self._gen_skin,
             lambda: G.skin_tones(self._gen_skin_n.value(),
                                  self._gen_skin_ranges.value()),
             lambda: G.skin_tones_count(self._gen_skin_n.value(),
                                        self._gen_skin_ranges.value()),
             self._gen_skin_count),
            (self._gen_blues,
             lambda: G.blues(self._gen_blues_n.value()
                             * self._gen_blues_layers.value(),
                             self._gen_blues_layers.value()),
             lambda: G.blues_count(self._gen_blues_n.value()
                                   * self._gen_blues_layers.value()),
             self._gen_blues_count),
            (self._gen_greens,
             lambda: G.greens(self._gen_greens_n.value()
                              * self._gen_greens_layers.value(),
                              self._gen_greens_layers.value()),
             lambda: G.greens_count(self._gen_greens_n.value()
                                    * self._gen_greens_layers.value()),
             self._gen_greens_count),
            (self._gen_sunrises,
             lambda: G.sunrises(self._gen_sunrises_n.value()
                                * self._gen_sunrises_layers.value(),
                                self._gen_sunrises_layers.value()),
             lambda: G.sunrises_count(self._gen_sunrises_n.value()
                                      * self._gen_sunrises_layers.value()),
             self._gen_sunrises_count),
            (self._gen_flamingos,
             lambda: G.flamingos(self._gen_flamingos_n.value()
                                 * self._gen_flamingos_layers.value(),
                                 self._gen_flamingos_layers.value()),
             lambda: G.flamingos_count(self._gen_flamingos_n.value()
                                       * self._gen_flamingos_layers.value()),
             self._gen_flamingos_count),
            (self._gen_greys,
             lambda: G.near_neutral_greys(self._gen_greys_n.value(),
                                          float(self._gen_greys_off.value()),
                                          self._gen_greys_rings.value()),
             lambda: G.near_neutral_greys_count(self._gen_greys_n.value(),
                                                self._gen_greys_rings.value()),
             self._gen_greys_count),
            (self._gen_hs,
             # Highlights & shadows interlocks with Near-neutral greys: when that
             # set is on, H&S stays just outside its rings (no colour printed
             # twice); when off, H&S also fills the near-neutral light/dark tones.
             lambda: G.highlight_shadow_detail(
                 self._gen_hs_n.value(),
                 float(self._gen_hs_reach.value()),
                 greys_enabled=self._gen_greys.isChecked(),
                 greys_steps=self._gen_greys_n.value(),
                 greys_offset=float(self._gen_greys_off.value()),
                 greys_rings=self._gen_greys_rings.value()),
             lambda: G.highlight_shadow_detail_count(self._gen_hs_n.value()),
             self._gen_hs_count),
            (self._gen_pastel,
             lambda: G.pastels(self._gen_pastel_n.value()
                               * self._gen_pastel_layers.value(),
                               self._gen_pastel_layers.value()),
             lambda: G.pastels_count(self._gen_pastel_n.value()
                                     * self._gen_pastel_layers.value()),
             self._gen_pastel_count),
            (self._gen_image,
             lambda: (G.image_palette(self._gen_image_px, self._gen_image_n.value())
                      if self._gen_image_px is not None else []),
             lambda: G.image_palette_count(self._gen_image_n.value(),
                                           self._gen_image_px is not None),
             self._gen_image_count),
        )
        # NB: "Pure white & black" is deliberately *not* a _gen_specs entry — it
        # tops up to N of each *after* de-duplication (so its repeats survive) and
        # counts whatever the other sets already contributed. Built/counted in
        # _build_generated_program / _update_gen_counts instead.

    def _install_magenta_accents(self) -> None:
        """Scope the editor's magenta accent onto this dialog's checked /
        focused controls (shared by the New-chart dialog and the Add dialog)
        so they don't fall back to the app-wide cyan."""
        self.setStyleSheet(f"""
            QCheckBox::indicator:checked {{
                background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
            }}
            QCheckBox::indicator:hover {{ border-color: {SPEC_MAGENTA}; }}
            QRadioButton::indicator:checked {{
                background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
            }}
            /* A ticked-but-disabled box must read as off — without this the
               magenta :checked fill wins over Qt's disabled greying, so an
               unselected panel (e.g. "Generate colour sets") still showed bright
               ticks. The two-state selector outranks the single :checked rule. */
            QCheckBox::indicator:checked:disabled,
            QRadioButton::indicator:checked:disabled {{
                background: #4a4a4a; border-color: #4a4a4a;
            }}
            QLineEdit:focus, QComboBox:focus,
            QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {SPEC_MAGENTA};
            }}
            /* The dropdown's hovered/selected row defaulted to the app-wide
               cyan; tint it magenta to match the rest of the dialog. */
            QComboBox QAbstractItemView {{
                selection-background-color: {SPEC_MAGENTA};
                selection-color: white;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {SPEC_MAGENTA}; color: white;
            }}
        """)

    def _gen_sets_active(self) -> bool:
        """Whether the generate-colour-sets panel is the active source.

        In the New-chart dialog that's the "Generate colour sets" radio;
        subclasses that reuse the panel under a different control (e.g. the
        editor's Add dialog) override this."""
        return self._mode_generate.isChecked()

    def _update_gen_counts(self, *_a) -> None:
        """Refresh each generator's patch count + the running total, and gate
        the per-row spin boxes on their checkbox."""
        on = self._gen_sets_active()
        self._gen_panel.setEnabled(on)
        # Grey each row's size control(s) when its set is unticked.
        for cb, spins in (
            (self._gen_cube, (self._gen_cube_n,)),
            (self._gen_skin, (self._gen_skin_n, self._gen_skin_ranges)),
            (self._gen_blues, (self._gen_blues_n, self._gen_blues_layers)),
            (self._gen_greens, (self._gen_greens_n, self._gen_greens_layers)),
            (self._gen_sunrises, (self._gen_sunrises_n,
                                  self._gen_sunrises_layers)),
            (self._gen_flamingos, (self._gen_flamingos_n,
                                   self._gen_flamingos_layers)),
            (self._gen_greys, (self._gen_greys_n, self._gen_greys_off,
                               self._gen_greys_rings)),
            (self._gen_edges, (self._gen_edges_n, self._gen_edges_faces)),
            (self._gen_hs, (self._gen_hs_n, self._gen_hs_reach)),
            (self._gen_pastel, (self._gen_pastel_n, self._gen_pastel_layers)),
            (self._gen_image, (self._gen_image_btn, self._gen_image_n)),
            (self._gen_whiteblack, (self._gen_whiteblack_n,)),
            (self._gen_fill, (self._gen_fill_to,)),
        ):
            for s in spins:
                s.setEnabled(cb.isChecked())
        # Near-neutral greys: offset has no effect with 0 rings, so grey out the
        # offset spin + its label when rings is 0 (or the set itself is off).
        off_on = self._gen_greys.isChecked() and self._gen_greys_rings.value() > 0
        self._gen_greys_off.setEnabled(off_on)
        self._gen_greys_off_label.setEnabled(off_on)
        total = 0
        for cb, _build, count, label in self._gen_specs():
            n = count()
            label.setText(_patches_label(n))
            if cb.isChecked():
                total += n
        # "From image" shows a hint until a photo is loaded.
        if self._gen_image.isChecked() and self._gen_image_px is None:
            self._gen_image_count.setText(tr("load an image"))
        # Pure white & black is part of the chart *before* fill, so it's counted
        # before the fill top-up. These are deliberate anchor patches (e.g. extra
        # paper-white / max-black readings to average), so they're added on top
        # of whatever the chart already holds — only the corners the *other
        # ticked sets* contribute in this same batch count toward N, never the
        # existing chart's own white/black (#76, Knut). One of each from those
        # corner-bearing sets (the 3D cube, and greys with ≥2 steps) if any are on
        # (collapsed by de-dup), else one per such set. Saturated edges no longer
        # counts here: its 'between' fill sits strictly between the cube steps, so
        # it never lands on the black or white corner (Knut, #78).
        corner = ((1 if self._gen_cube.isChecked() else 0)
                  + (1 if (self._gen_greys.isChecked()
                           and self._gen_greys_n.value() >= 2) else 0))
        sets_have = (1 if corner else 0) if self._gen_unique.isChecked() else corner
        wb_n = G.white_black_count(self._gen_whiteblack_n.value(),
                                   sets_have, sets_have)
        self._gen_whiteblack_count.setText(_patches_label(wb_n))
        if self._gen_whiteblack.isChecked():
            total += wb_n
        # Fill remaining gaps tops the whole chart (existing + sets + white/black)
        # up to its target, so it's counted last.
        fill_n = G.fill_gaps_count(total + len(self._existing_patches),
                                   self._gen_fill_to.value())
        self._gen_fill_count.setText(_patches_label(fill_n))
        if self._gen_fill.isChecked():
            total += fill_n
        # Total = the patches the current set selection produces (the additions:
        # the ticked sets + white/black + fill) — NOT the existing chart, and
        # shown even when the master Generate toggle is off, exactly like the
        # per-set counts beside each option (#60, Knut's clarification). This
        # estimate shows instantly; _do_push_live_preview refreshes it from the
        # real built program ~300 ms later (which catches any cross-set de-dup
        # the per-set estimate can't see).
        self._gen_total.setText(tr("Total: {label}").format(
            label=_patches_label(total)))
        if self._existing_patches:
            self._gen_after_total.setText(tr("Chart after adding: {label}").format(
                label=_patches_label(len(self._existing_patches) + total)))
        # Keep the embedded live cube in step with the colour-set controls.
        self._push_live_preview()

    def _build_generated_program(self) -> list[tuple]:
        """Concatenate every ticked generator's patches, in panel order,
        de-duplicating across sets when 'Ensure unique colours' is on."""
        program: list[tuple] = []
        for cb, build, _count, _label in self._gen_specs():
            if not cb.isChecked():
                continue
            # Saturated edges keys its even infill straight to the 3D cube's grid
            # (see _edges_cube_n / gamut_edges_between), so it needs no seed of the
            # patches already placed — its build() is the same as every other set.
            program.extend(build())
        if self._gen_unique.isChecked():
            # Assure a real minimum spacing, walking the sets top-to-bottom (the
            # order above) so each set's patches are spaced against the ones above
            # — not just landed on distinct grid cells (Knut, #78).
            program = G.enforce_min_distance(program, _GEN_MIN_DIST)
        # Pure white & black goes in *after* de-dup (so its deliberate repeats
        # survive) but *before* fill, so it's part of the chart fill tops up to —
        # not stacked on top of it. These are deliberate anchor patches, so they
        # add N of each on top of the existing chart; only the corners the other
        # sets contribute in *this* batch count toward N (#76, Knut).
        if self._gen_whiteblack.isChecked():
            have_w, have_b = G.count_white_black(program)
            program.extend(G.white_black(
                self._gen_whiteblack_n.value(), have_w, have_b))
        # Fill runs last so it tops the *whole* chart (patches already on it, the
        # chosen sets and the white/black anchors) up to the target, placed where
        # it's sparse and avoiding everything already chosen (#51).
        if self._gen_fill.isChecked():
            seed = self._existing_patches + program
            program.extend(G.fill_gaps(seed, self._gen_fill_to.value()))
        return program

    # -- live 3D-cube preview (embedded panel) ----------------------------
    # An always-visible cube panel docked beside the colour-set controls that
    # mirrors the generator in real time: it shows exactly what the ticked sets
    # would produce (plus the chart's existing patches, dimmed, in the Add flow),
    # redrawing whenever a setting changes. Embedded — not a separate window —
    # so it can't fight the dialog's modal session (which on macOS broke Create/
    # Add and left the editor unclosable). Shared by New-chart and Add dialogs.
    # Extra dialog width the cube adds when unfolded (controls keep their own
    # natural width; the cube takes this much beside them).
    _CUBE_WIDTH = 460

    def _make_cube_panel(self) -> QWidget:
        from ui.patch_cube_panel import PatchCubePanel
        from ui.theme import resolve_mode
        mode = resolve_mode(self._settings.get("appearance", "auto")
                            if self._settings else "auto")
        self._cube_panel = PatchCubePanel(mode=mode, parent=self)
        self._cube_panel.setMinimumWidth(360)
        self._cube_shown = True
        # Coalesce bursts of control changes into one redraw ~300 ms after the
        # last change — a full Plotly.react on every spinbox tick would thrash.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._do_push_live_preview)
        return self._cube_panel

    def _build_body(self, scroll: QScrollArea, content_w: int) -> "QHBoxLayout":
        """Lay the controls (`scroll`, kept at their natural width) beside the
        embedded cube (which takes the remaining space). Records the widths the
        fold toggle needs."""
        # +room for the vertical scrollbar so the options are never clipped.
        self._controls_w = content_w + 22
        scroll.setMinimumWidth(self._controls_w)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(scroll, 0)              # controls: keep their width
        body.addWidget(self._make_cube_panel(), 1)  # cube: take the rest
        return body

    def _make_fold_button(self) -> QPushButton:
        btn = QPushButton(self)
        btn.setCheckable(True)
        btn.setToolTip(tr("Show or hide the live 3D-cube preview."))
        btn.toggled.connect(self._on_fold_toggled)
        self._fold_btn = btn
        return btn

    def _init_fold_state(self, base_height: int) -> None:
        """Apply the remembered fold state and size the dialog accordingly.
        The cube starts folded away by default — opt in per session."""
        shown = (bool(self._settings.get("new_chart_show_cube", False))
                 if self._settings else False)
        self.resize(self._controls_w, base_height)   # set the height once
        self._fold_btn.blockSignals(True)
        self._fold_btn.setChecked(shown)
        self._fold_btn.blockSignals(False)
        self._on_fold_toggled(shown)

    def _on_fold_toggled(self, shown: bool) -> None:
        self._cube_shown = shown
        # Capture the centre *first*: when unfolding, raising the minimum width
        # below makes Qt resize the window immediately (and rightward, top-left
        # fixed), so a centre read after that already reflects the grown, shifted
        # geometry. Remembering it up front lets us recentre symmetrically.
        was_visible = self.isVisible()
        center = self.frameGeometry().center()
        self._cube_panel.setVisible(shown)
        # Cube sits on the right: hiding collapses it inward (◂), showing
        # expands it outward (▸).
        self._fold_btn.setText(tr("◂ Hide 3D preview") if shown
                               else tr("Show 3D preview ▸"))
        if self._settings is not None:
            self._settings.set("new_chart_show_cube", shown)
        # Clamp the minimum width to fit both panes when shown, so the window
        # can't be dragged narrow enough for the cube to overrun the options.
        cube_min = self._cube_panel.minimumWidth() if shown else 0
        self.setMinimumWidth(self._controls_w + cube_min)
        new_w = self._controls_w + (self._CUBE_WIDTH if shown else 0)
        self.resize(new_w, self.height())
        if was_visible:
            # Grow / shrink symmetrically about the pre-toggle centre so the
            # dialog extends to both sides equally instead of only to the right.
            fg = self.frameGeometry()
            fg.moveCenter(center)
            self.move(fg.topLeft())
        # (Not shown yet → construction; showEvent centres it over the parent.)
        if shown:
            self._do_push_live_preview()   # refresh after being hidden

    def exec(self) -> int:  # noqa: A003 - intentional QDialog.exec override
        """Enter the modal loop, but first realize the cube's web view while the
        dialog is still non-modal — even when it opens folded.

        Creating a ``QWebEngineView``'s native child surface *inside* an
        application-modal dialog wedges the modal grab on Windows and freezes
        the whole app (issue #38 follow-up). The startup warm-up keeps Chromium
        alive but each view still spawns its own surface. So we show the dialog
        non-modally, build the view off the modal path, let the surface settle,
        then enter the modal loop with it already in place — whether the cube
        starts unfolded (the view stays visible) or folded (a later unfold just
        re-shows the existing surface instead of creating one while modal). A
        folded-open cube must be momentarily shown to realize the surface; we
        hide that behind window-opacity 0 so there's no flash."""
        panel = getattr(self, "_cube_panel", None)
        if panel is not None:
            folded = not getattr(self, "_cube_shown", False)
            if folded:
                self.setWindowOpacity(0.0)   # hide the brief realize-unfold
            self.show()                      # non-modal: realize the dialog
            if folded:
                panel.setVisible(True)       # must be visible to realize the surface
            panel.ensure_view()              # build the web view off the grab
            QApplication.processEvents()     # let the native surface settle
            if folded:
                panel.setVisible(False)      # restore folded; the surface persists
                self.setWindowOpacity(1.0)
        self._fit_content_min_height()
        return super().exec()

    def _fit_content_min_height(self) -> None:
        """Pin a minimum height that fully shows the scrolled left column.

        The controls live in a ``QScrollArea`` whose own sizeHint is small, so
        the dialog could open (or be dragged) shorter than the column needs and
        clip it. The column's realized height is only known once shown, so do
        this from exec() after the window has settled: needed dialog height =
        the content's natural height + the chrome around the scroll viewport
        (masthead, buttons, margins). Capped at 92 % of the screen."""
        if not getattr(self, "_fit_content_height", False):
            return
        sc = getattr(self, "_scroll", None)
        if sc is None or sc.widget() is None:
            return
        self.layout().activate()
        QApplication.processEvents()
        viewport_h = sc.viewport().height()
        if viewport_h <= 0:
            return  # geometry not settled — leave the initial sizing in place
        chrome = self.height() - viewport_h
        need = sc.widget().sizeHint().height() + chrome
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            need = min(need, int(screen.availableGeometry().height() * 0.92))
        self.setMinimumHeight(need)
        if self.height() < need:
            self.resize(self.width(), need)

    def _push_live_preview(self) -> None:
        """Schedule a debounced rebuild — refreshes the exact total (and the cube
        if it's unfolded). Runs even while folded so the total stays correct."""
        if getattr(self, "_preview_timer", None) is not None:
            self._preview_timer.start()

    def _do_push_live_preview(self) -> None:
        if getattr(self, "_gen_total", None) is None:
            return
        # The generated additions for the current set selection. Built even when
        # the master Generate toggle is off, so the Total previews them like the
        # per-set counts do; using the real built program also catches the
        # white/black de-dup against existing patches the estimate can't see (the
        # original 921-vs-924 drift in #60).
        additions = self._build_generated_program()
        # Total = the additions only (not the existing chart), shown always (#60).
        self._gen_total.setText(tr("Total: {label}").format(
            label=_patches_label(len(additions))))
        # In the Add flow, also the chart's resulting size (existing + additions).
        if self._existing_patches:
            self._gen_after_total.setText(tr("Chart after adding: {label}").format(
                label=_patches_label(len(self._existing_patches) + len(additions))))
        # The cube shows what's actually being added — nothing when generate is
        # off (you're adding no sets), the additions otherwise.
        program = additions if self._gen_sets_active() else []
        if getattr(self, "_cube_panel", None) is not None and self._cube_shown:
            self._cube_panel.set_program(program, self._existing_patches)

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        # Centre over the parent window on first show. The dialog used to settle
        # its size early because the cube's QWebEngineView forced a layout pass
        # in __init__; now that the cube is built lazily, exec() can drop the
        # window at the screen's top-left, so recentre once the size is known.
        if getattr(self, "_centred_once", False):
            return
        self._centred_once = True
        par = self.parentWidget()
        ref = (par.window().frameGeometry()
               if par is not None and par.window() is not None
               else self.screen().availableGeometry())
        fg = self.frameGeometry()
        fg.moveCenter(ref.center())
        self.move(fg.topLeft())

    def done(self, result: int) -> None:  # noqa: N802
        # done() is the chokepoint for accept (Create / Add), reject (Cancel)
        # and the window's X — drain the embedded cube's web view while the loop
        # is still alive (issue #38) before the dialog goes away.
        if getattr(self, "_cube_panel", None) is not None:
            self._cube_panel.teardown()
        super().done(result)

    def _load_gen_image(self) -> None:
        """Load + decode an image for the 'From image' colour set.

        Decoding (Pillow) and down-sampling happen here; the pure
        :func:`patch_generators.image_palette` does the clustering. The image is
        shrunk to a small thumbnail first — plenty for finding representative
        colours and keeps k-means fast.
        """
        from PyQt6.QtWidgets import QFileDialog
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Load image"), start,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)")
        if not path:
            return
        try:
            from PIL import Image
            import numpy as np
            im = Image.open(path).convert("RGB")
            im.thumbnail((200, 200))
            self._gen_image_px = np.asarray(im).reshape(-1, 3)
        except Exception as exc:  # noqa: BLE001 — surface any decode failure
            QMessageBox.warning(self, tr("Load failed"), str(exc))
            return
        self._gen_image_name = Path(path).name
        self._gen_image_btn.setText(self._gen_image_name)
        self._gen_image.setChecked(True)
        self._update_gen_counts()

    # -- helpers -----------------------------------------------------------
    def _refresh_source_widgets(self) -> None:
        self._count.setEnabled(self._mode_seed.isChecked())
        self._paste_edit.setEnabled(self._mode_paste.isChecked())
        self._update_gen_counts()

    def _refresh_spacer_scale_enabled(self, *_a) -> None:
        """Disable Spacer scale (-A) when "None" is the spacer choice —
        there's nothing for the scale to apply to in that case."""
        self._spacer_scale.setEnabled(not self._sp_none.isChecked())

    def _on_paper_changed(self) -> None:
        """Show the custom W/H row only when "Custom" is the selection."""
        self._paper_custom_row.setVisible(
            self._paper.currentData() == "custom")

    def _refresh_instr_widgets(self) -> None:
        """Show/hide instrument-conditional options.

        Strip readers (i1Pro family + i1Pro 3 Plus): ``-L`` (suppress left
        clip) + ``-P`` (no strip limit) — both layout flags Argyll documents
        as strip-reader-only.
        ColorMunki: ``-h`` (double density) + Triple density — the rig-only
        layout tweaks. Hidden controls also reset to off so a hidden value
        can't leak through into the printtarg command on Create.
        """
        code = self._instr.currentData()
        is_strip = code in _STRIP_INSTRUMENTS
        is_cm = code == "CM"
        self._cb_L.setVisible(is_strip)
        self._cb_P.setVisible(is_strip)
        self._cb_h.setVisible(is_cm)
        self._cb_td.setVisible(is_cm)
        if not is_strip:
            self._cb_L.setChecked(False)
            self._cb_P.setChecked(False)
        if not is_cm:
            self._cb_h.setChecked(False)
            self._cb_td.setChecked(False)

    def _on_dd_toggled(self, on: bool) -> None:
        """Toggling double density off triple density (mutual exclusion)."""
        if on and self._cb_td.isChecked():
            self._cb_td.setChecked(False)

    def _on_td_toggled(self, on: bool) -> None:
        """Apply / undo the triple-density preset on the layout widgets.

        Mirrors the manual triple-density toggle in
        ``ui/tabs/tab_chart.py`` (``_on_manual_td_toggled``): when enabled,
        stash the user's -a / -m / -L / -P values and overwrite them with the
        preset (1.3 / 5 / on / on); restore on untoggle. Also keeps Double
        density mutually exclusive.
        """
        if on and self._cb_h.isChecked():
            self._cb_h.setChecked(False)
        self._cb_h.setEnabled(not on)
        if on:
            self._td_stash = {
                "a": self._patch_scale.value(),
                "m": self._margin.value(),
                "L": self._cb_L.isChecked(),
                "P": self._cb_P.isChecked(),
            }
            self._patch_scale.setValue(1.3)
            self._margin.setValue(5)
            self._cb_L.setChecked(True)
            self._cb_P.setChecked(True)
        else:
            stash = getattr(self, "_td_stash", None) or {}
            if "a" in stash:
                self._patch_scale.setValue(stash["a"])
            if "m" in stash:
                self._margin.setValue(stash["m"])
            if "L" in stash:
                self._cb_L.setChecked(bool(stash["L"]))
            if "P" in stash:
                self._cb_P.setChecked(bool(stash["P"]))
            self._td_stash = None

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
            QMessageBox.warning(self, tr("Could not read file"), str(exc))

    def _on_ok(self) -> None:
        paper_code = self._paper.currentData() or self._paper.currentText()
        if paper_code == "custom":
            paper_code = f"{self._paper_w.value()}x{self._paper_h.value()}"
        spec = R.ChartSpec.new(self._instr.currentData(), paper_code)
        # ChartSpec.new only knows the named-paper inverse map; patch
        # paper_mm explicitly for custom sizes so the editor knows the
        # canvas dimensions for downstream code (preview scaling etc.).
        if self._paper.currentData() == "custom":
            spec.paper_mm = (float(self._paper_w.value()),
                              float(self._paper_h.value()))
        program: list[tuple] = []
        if self._mode_seed.isChecked():
            try:
                program = R.seed_from_targen(self._bin_dir, self._count.value())
            except Exception as exc:
                QMessageBox.warning(self, tr("targen failed"), str(exc))
                return
        elif self._mode_paste.isChecked():
            program = R.parse_color_values(self._paste_edit.toPlainText())
            if not program:
                QMessageBox.warning(self, tr("No values"),
                                    tr("Couldn't parse any RGB / hex values "
                                    "from the pasted text."))
                return
        elif self._mode_generate.isChecked():
            program = self._build_generated_program()
            if not program:
                QMessageBox.warning(self, tr("No colour sets"),
                                    tr("Tick at least one colour set to "
                                    "generate patches from."))
                return

        # Mutex-checkbox group: at most one is on; all-off falls through
        # to printtarg's coloured default.
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
            margin_mm=self._margin.value(),
            suppress_left_clip=self._cb_L.isChecked(),
            no_strip_limit=self._cb_P.isChecked(),
            double_density=self._cb_h.isChecked(),
            triple_density=self._cb_td.isChecked(),
            tiff_16bit=self._bd_16.isChecked(),
            dpi=self._dpi.value(),
        )

        self.result_spec = spec
        self.result_program = program
        self.result_options = opts
        # The full creation recipe, so the editor can persist it on the chart
        # (reloaded into New chart / Add later to tweak/recreate the design).
        self.result_recipe = self._collect_gen_state()
        # No name is asked for here any more — the real name is chosen at
        # Save & apply time. Keep the neutral "chart" placeholder basename.
        self.result_basename = "chart"
        self._save_gen_state()   # remember these choices for next time
        self.accept()


class _AddPatchesDialog(_NewChartDialog):
    """The editor's "Add…" dialog: add a single chosen colour, or generate one
    or more colour sets (the New-chart generate panel — 3D cube, skin tones,
    blues, greens, sunrises, greys, edges, highlights/shadows, pastels, from
    image, fill) to append to the loaded chart.

    Reuses :class:`_NewChartDialog`'s generate panel + program builder, but the
    surrounding chrome is different (no chart-identity / layout frames, no
    source-mode radios), so it deliberately bypasses the full New-chart
    ``__init__`` and builds only what it needs. On accept, ``result_program``
    holds the RGB triples to splice into the grid.
    """

    def __init__(self, settings=None, parent: QWidget | None = None,
                 existing_patches=None, initial_recipe=None) -> None:
        QDialog.__init__(self, parent)
        self.setWindowTitle(tr("Add patches"))
        self.setMinimumWidth(620)
        self._settings = settings
        # The chart's creation recipe, if reopened from a chart that has one —
        # its colour-set choices pre-fill the generate panel instead of the
        # app-wide last-used ones (Add only has the generate panel, so just the
        # cb/sp portion applies).
        self._initial_recipe = initial_recipe
        self.result_program: list[tuple] | None = None
        # State the inherited generate-panel methods expect. The chart's current
        # patches let "Fill remaining gaps" top the whole chart up to its target
        # instead of appending that many (#51); set before _build_generate_panel.
        self._existing_patches = list(existing_patches or [])
        self._gen_image_px = None
        self._gen_image_name = ""
        self._single_rgb: tuple[float, float, float] = (50.0, 50.0, 50.0)
        self._install_magenta_accents()

        content = QWidget(self)
        lay = QVBoxLayout(content)
        lay.setSpacing(10)

        # NB: the tab-style heading + full-width spectrum stripe are added to the
        # dialog's ``outer`` layout (above the body), not here, so they span the
        # window and the 3D-cube preview sits below them.

        # Mode: a single colour, or generated colour sets.
        self._add_mode_single = QRadioButton(
            tr("Add a single colour"), self)
        self._add_mode_gen = QRadioButton(tr("Generate colour sets"), self)
        self._add_mode_single.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._add_mode_single)
        grp.addButton(self._add_mode_gen)
        lay.addWidget(self._add_mode_single)

        # Single-colour row: a swatch + colour picker.
        single_row = QHBoxLayout()
        single_row.setContentsMargins(22, 0, 0, 0)
        self._single_swatch = QLabel(self)
        self._single_swatch.setFixedSize(28, 22)
        self._single_swatch.setFrameShape(QFrame.Shape.StyledPanel)
        self._paint_single_swatch()
        single_btn = QPushButton(tr("Choose colour…"), self)
        single_btn.setObjectName("compact_input")
        single_btn.clicked.connect(self._pick_single_colour)
        single_row.addWidget(self._single_swatch)
        single_row.addWidget(single_btn)
        single_row.addStretch(1)
        lay.addLayout(single_row)

        lay.addWidget(self._add_mode_gen)
        lay.addLayout(self._build_generate_panel(content))
        self._add_mode_single.toggled.connect(self._refresh_add_mode)
        self._refresh_add_mode()
        # Soak up any extra height at the bottom so the controls stay packed at
        # the top instead of drifting apart when the dialog is taller than its
        # content (single-colour mode is short).
        lay.addStretch(1)

        btns = QHBoxLayout()
        restore = QPushButton(tr("Restore defaults"), self)
        restore.setToolTip(tr("Reset the colour sets back to their defaults."))
        restore.clicked.connect(lambda: self._apply_gen_sets_and_refresh(
            self._GEN_FACTORY))
        btns.addWidget(restore)
        btns.addWidget(self._make_fold_button())
        btns.addStretch(1)
        ok = QPushButton(tr("Add to chart"), self)
        ok.setDefault(True)
        ok.clicked.connect(self._on_add)
        cancel = QPushButton(tr("Cancel"), self)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        # Kept for exec()'s content-fit pass: the left column lives in this
        # scroll area, and its realized height is only known once shown.
        self._scroll = scroll
        self._fit_content_height = True
        # Controls on the left (kept at their natural width), the foldable live
        # 3D cube on the right.
        hint = content.sizeHint()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 16, 0)
        head.addWidget(TabHeader(
            tr("EXTEND THE CHART"), tr("Add patches"), SPEC_MAGENTA, self),
            0, Qt.AlignmentFlag.AlignVCenter)
        GradientOverlay(SPEC_MAGENTA, parent=self, alpha=15, height=95, on_top=False)
        head.addStretch(1)
        # ⓘ with the same generator-sets help the New-chart dialog carries, so
        # the Add window explains the colour sets too (#66 follow-up).
        head.addWidget(_magenta_tip(
            tr("Add patches"),
            tr(_ADD_TIP_INTRO) + "\n\n" + tr(_GEN_SETS_HELP),
            self, min_width=520))
        outer.addLayout(head)
        outer.addWidget(_SpectrumStripe(self))
        outer.addLayout(self._build_body(scroll, hint.width()), 1)
        btns.setContentsMargins(12, 4, 12, 10)
        outer.addLayout(btns)
        # Height tracks the content (capped) so the dialog isn't mostly empty
        # space; width follows the fold state. exec() pins a proper minimum
        # height once the left column's realized size is known (see
        # _fit_content_height / the exec override).
        self._init_fold_state(min(760, hint.height() + 72))

        # Prefer the chart's own recipe (reopened to extend that design);
        # otherwise share the New-chart dialog's last-used colour-set choices.
        st = self._initial_recipe if isinstance(self._initial_recipe, dict) else (
            self._settings.get("new_chart_gen", None) if self._settings else None)
        if isinstance(st, dict):
            self._apply_gen_sets(st)
        self._update_gen_counts()
        self._do_push_live_preview()   # seed the cube (existing patches + sets)

    # The generate panel is the active source only in "Generate colour sets"
    # mode — drives the panel-enable in the inherited _update_gen_counts.
    def _gen_sets_active(self) -> bool:
        return self._add_mode_gen.isChecked()

    def _refresh_add_mode(self, *_a) -> None:
        self._update_gen_counts()

    def _paint_single_swatch(self) -> None:
        r, g, b = (max(0, min(255, round(c / 100 * 255)))
                   for c in self._single_rgb)
        self._single_swatch.setStyleSheet(
            f"background:#{r:02x}{g:02x}{b:02x}; border:1px solid #888;")

    def _pick_single_colour(self) -> None:
        r, g, b = (max(0, min(255, round(c / 100 * 255)))
                   for c in self._single_rgb)
        # Non-native picker (same as the editor's _pick_color) so the hex field
        # + RGB/HSV spinners are available, matching the rest of the editor.
        c = QColorDialog.getColor(
            QColor(r, g, b), self, tr("Pick colour"),
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if not c.isValid():
            return
        self._single_rgb = (c.red() / 255 * 100, c.green() / 255 * 100,
                            c.blue() / 255 * 100)
        self._paint_single_swatch()
        self._add_mode_single.setChecked(True)

    def _apply_gen_sets_and_refresh(self, st: dict) -> None:
        self._apply_gen_sets(st)
        self._update_gen_counts()

    def _on_add(self) -> None:
        if self._add_mode_gen.isChecked():
            program = self._build_generated_program()
            if not program:
                QMessageBox.warning(self, tr("No colour sets"),
                                    tr("Tick at least one colour set to "
                                    "generate patches from."))
                return
            # Remember the colour-set choices for next time (shared with the
            # New-chart dialog), without disturbing its chart/layout state.
            if self._settings is not None:
                cur = self._settings.get("new_chart_gen", None)
                cur = dict(cur) if isinstance(cur, dict) else {}
                cur.update(self._collect_gen_sets())
                self._settings.set("new_chart_gen", cur)
        else:
            program = [self._single_rgb]
        self.result_program = program
        self.accept()


# ---------------------------------------------------------------------------
# Main editor
# ---------------------------------------------------------------------------
# How many editing steps the undo/redo history keeps. Edits here are coarse
# (recolour a selection, reorder, add/remove a batch, paint spacers, one layout
# tweak), not fine brush strokes, so 20 is generous. Each snapshot is a cheap
# in-memory copy of the editable state (a few hundred KB for a huge chart, tens
# of KB typically) — the history never touches disk and is dropped when the
# dialog closes.
_UNDO_DEPTH = 20


@dataclass
class _EditorSnapshot:
    """A point-in-time copy of everything the user can edit in one chart: the
    patch program (colours + order), the spacer palette + per-spacer paint, the
    printtarg layout knobs, and the instrument/paper (which live on the spec).

    Captured by :meth:`Ti2RelayoutDialog._make_snapshot` and re-applied by
    :meth:`Ti2RelayoutDialog._restore_snapshot`. ``key`` is a hashable digest
    used only to skip capturing a step that didn't actually change anything."""
    program: list = field(default_factory=list)
    palette: "list | None" = None
    paint: dict = field(default_factory=dict)
    options: "R.LayoutOptions | None" = None
    instr: str = ""
    paper: str = ""
    paper_mm: tuple = (0.0, 0.0)

    @property
    def key(self) -> tuple:
        return (
            tuple(self.program),
            tuple(self.palette) if self.palette else None,
            tuple(sorted(self.paint.items())),
            astuple(self.options) if self.options is not None else None,
            self.instr, self.paper, tuple(self.paper_mm),
        )


class Ti2RelayoutDialog(QDialog):
    def __init__(self, runner, settings, parent: QWidget | None = None,
                 on_apply: "Callable[[Path, str], bool | None] | None" = None,
                 initial_chart: "Path | None" = None) -> None:
        super().__init__(parent)
        self._settings = settings
        # Callback that hands a freshly-saved chart folder to the Create Chart
        # tab (set by the main window). When present, the action footer offers
        # "Save & apply" instead of the plain colour-export button.
        self._on_apply = on_apply
        # Chart to pre-load when the editor opens — the Create Chart tab's
        # current generated chart, so it's ready to edit (#45). Loaded after
        # the UI is built (end of __init__).
        self._initial_chart = initial_chart
        self._bin_dir = Path(settings.get("argyll_bin_path", "/Applications/Argyll/bin"))
        self.setWindowTitle(tr("Edit / create chart layout"))
        # Wider default so the printtarg-options column doesn't clip its
        # row labels ("Margin (mm):", "Spacer -A:") or its combo content
        # ("A4 (210 × 297 mm) Portrait") on first open.
        self.resize(1280, 820)
        self.setMinimumSize(1000, 620)

        self._spec: R.ChartSpec | None = None
        # The chart's creation recipe (New chart / Add window state) when known —
        # threaded from the New chart dialog, or loaded from meta.json — so it
        # can be re-persisted on save and reloaded into New chart / Add.
        self._chart_recipe: dict | None = None
        # Snapshot of the chart's content (patches + spacers + layout knobs) as
        # last saved / loaded-from-disk. Compared against the live signature to
        # tell whether there are unsaved edits to warn about on Close (#49). A
        # fresh sentinel means "never saved" (a created chart is dirty at once).
        self._saved_sig: object = object()
        self._palette: list[tuple] | None = None       # native spacer palette
        self._regen: R.RegenResult | None = None
        self._page = 0                                  # previewed page index
        self._spacers: list = []                        # current-page Spacer list
        # Per-page spacer segmentation cache (page -> Spacer list). Filled
        # lazily on first visit to a page and reused on every later visit, so
        # flipping back and forth between pages doesn't re-run the (expensive)
        # twin-diff + segmentation each time. Cleared on every fresh regen.
        self._spacer_cache: dict[int, list] = {}
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
        # Per-page {sample_id: (x0,y0,x1,y1)} pixel boxes, computed once
        # after each regen and used by the "highlight selected patches"
        # overlay + the preview's patch-click/marquee handlers.
        # Per-page patch geometry (sample-id → pixel bbox), computed lazily for
        # the visited page only and cached; cleared on each fresh render (#44).
        self._patch_geom_cache: dict[int, dict[int, tuple[int, int, int, int]]] = {}

        # Debounced auto-preview: fire 1.8s after the last edit.
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(1800)
        self._auto_timer.timeout.connect(
            lambda: self._regenerate(save_to=None) if self._spec else None
        )

        # Undo/redo history (in-memory only; see _UNDO_DEPTH). _undo_stack holds
        # _EditorSnapshots with the live state at _undo_index as its tail-or-
        # earlier entry; the baseline (index 0) is the chart as loaded/created.
        # _suppress_undo blocks capture while we ourselves repopulate the grid
        # (load / restore), so programmatic changes never land on the stack.
        self._undo_stack: list[_EditorSnapshot] = []
        self._undo_index: int = -1
        self._suppress_undo: bool = False
        # Capture is debounced so spinbox scrubbing or a multi-patch recolour
        # coalesce into one undo step rather than dozens.
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(500)
        self._undo_timer.timeout.connect(self._capture_undo)

        self._build_ui()
        self._refresh_enabled()

        # Pre-load the Create Chart tab's current chart, ready to edit (#45).
        # Deferred to the event loop so the window is shown first and the
        # initial preview render doesn't block the open. is_saved=True (set in
        # _load_chart_from) means a clean open — closing without edits won't warn.
        if self._initial_chart is not None and Path(self._initial_chart).is_file():
            QTimer.singleShot(0, lambda: self._load_chart_from(Path(self._initial_chart)))

    # -- UI -----------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        # Zero side margins so the spectrum stripe under the source row can run
        # edge-to-edge (full window width), like the masthead stripe. The three
        # real content rows re-add the 16 px side inset themselves.
        outer.setContentsMargins(0, 14, 0, 12)
        outer.setSpacing(10)

        # Source row
        src = QHBoxLayout()
        src.setContentsMargins(16, 0, 16, 0)
        # Tab-style heading at the far left, mirroring the main-window tab
        # headers (uppercase eyebrow + large serif title), in the editor's
        # magenta accent.
        src.addWidget(TabHeader(
            tr("CHART LAYOUT · EDITOR"), tr("Design your chart"),
            SPEC_MAGENTA, self), 0, Qt.AlignmentFlag.AlignVCenter)
        GradientOverlay(SPEC_MAGENTA, parent=self, alpha=15, height=95, on_top=False)
        src.addSpacing(16)
        load_btn = QPushButton(tr("Load chart…"), self)
        load_btn.setToolTip(tr("Load a chart from a .ti2 file."))
        load_btn.clicked.connect(self._load_ti2)
        new_btn = QPushButton(tr("New chart…"), self)
        new_btn.clicked.connect(self._new_chart)
        src.addWidget(new_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        src.addWidget(load_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        src.addStretch(1)
        # Undo / redo, centred between the source buttons and the info readout.
        self._undo_btn = QPushButton(tr("↶ Undo"), self)
        self._undo_btn.setToolTip(tr("Undo the last edit (Ctrl+Z)."))
        self._undo_btn.clicked.connect(self._undo)
        self._redo_btn = QPushButton(tr("Redo ↷"), self)
        self._redo_btn.setToolTip(tr("Redo the last undone edit (Ctrl+Shift+Z)."))
        self._redo_btn.clicked.connect(self._redo)
        src.addWidget(self._undo_btn)
        src.addWidget(self._redo_btn)
        src.addStretch(1)
        # Give each pair of source-row buttons one uniform width sized to its
        # widest label plus padding, so the text never clips — macOS's Fusion
        # button metrics under-size the hint by a few px, which cut "New chart…"
        # on both sides (Knut). Derived from the rendered sizeHints, so it holds
        # in any language; uniform widths also line the buttons up neatly.
        _uniform_button_width((new_btn, load_btn), pad=24)
        _uniform_button_width((self._undo_btn, self._redo_btn), pad=18)
        # Ctrl+Z / Ctrl+Shift+Z (and Ctrl+Y) work anywhere in the dialog.
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self._redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._redo)
        self._info = QLabel(tr("No chart loaded."), self)
        # A long chart name makes this readout's text long; with the default
        # Preferred policy its minimumSizeHint forced the whole source row wider
        # than the window, which clipped the New chart / Load .ti2 buttons.
        # Ignored horizontal policy lets the label shrink (clipping its own text)
        # so it can never dictate the row width — the buttons keep their natural
        # size. The full readout stays available as a tooltip (see _refresh_info).
        self._info.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        src.addWidget(self._info)
        src.addWidget(_magenta_tip(
            "Chart layout editor",
            "Welcome! This is where you design the test chart you'll print and "
            "then measure. A chart is just a page full of little colour squares "
            "(we call each one a \"patch\"). You can start a brand-new chart or "
            "open one you already have, rearrange and recolour the patches however "
            "you like, and save a ready-to-print chart when you're happy.\n\n"
            "Don't worry — you can't break anything here. Nothing is printed or "
            "measured until you choose to, and you can preview every change first.\n\n"
            "There are three areas to know about:\n\n"
            "• The patch grid on the left shows every colour as a small square. "
            "This is your workbench: drag squares around to reorder them, click to "
            "select, and recolour or add and remove patches. The order you see "
            "here is the order they'll be printed in.\n\n"
            "• The preview in the middle is a live picture of the actual printed "
            "page. It redraws as you edit, so you always see what will really come "
            "out of the printer — strips, spacers, margins and all. If the chart "
            "needs more than one page, little Page ◀ ▶ buttons appear.\n\n"
            "• The controls on the right let you pick whether you're editing "
            "patches or the spacer squares between them, and adjust how the page is "
            "laid out (which instrument and paper, spacer style, sizing, margin, "
            "resolution). This is also where you save.\n\n"
            "A typical session goes: load a chart or start a new one, arrange and "
            "recolour the patches, glance at the preview, then Save. The file you "
            "save is exactly what you print and then read in on the Measure tab.\n\n"
            "One handy thing happens automatically: when you save, ChromIQ checks "
            "whether your colours are well mixed and, if they are, marks the chart "
            "so your instrument may read each strip in either direction. You only "
            "have to get involved for tricky, structured layouts — see the "
            "\"Force randomised tag\" option in the controls for more.",
            self, min_width=560))
        outer.addLayout(src)

        # Full-width spectrum stripe — a visual separator between the source /
        # undo-redo controls and the editing area, mirroring the masthead.
        outer.addWidget(_SpectrumStripe(self))

        split = QSplitter(Qt.Orientation.Horizontal, self)

        # Left: swatch-size slider + patch grid
        left = QWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Swatch size:")))
        top.addSpacing(20)
        self._size_slider = QSlider(Qt.Orientation.Horizontal, left)
        self._size_slider.setRange(24, 96)
        self._size_slider.setValue(_SWATCH)
        self._size_slider.valueChanged.connect(self._set_swatch_size)
        # Same recipe as the Gamut viewer's opacity / saturation sliders
        # (slim groove, filled sub-page, round handle) but with the magenta
        # accent so it harmonises with the rest of this dialog's accent.
        from ui.theme import resolve_mode
        _mode = resolve_mode(self._settings.get("appearance", "auto"))
        _groove = "#1c1b18" if _mode == "light" else "#333333"
        self._size_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {_groove};"
            " border-radius: 2px; }"
            f"QSlider::handle:horizontal {{ background: {SPEC_MAGENTA};"
            " border: none; width: 12px; height: 12px; margin: -4px 0;"
            " border-radius: 6px; }"
            f"QSlider::sub-page:horizontal {{ background: {SPEC_MAGENTA};"
            " border-radius: 2px; }"
        )
        self._size_slider.setFixedWidth(220)
        top.addWidget(self._size_slider)
        top.addStretch(1)
        top.addWidget(_magenta_tip(
            "Patch grid",
            "This is your main workspace. Every little square is one colour patch "
            "from your chart, and the order you see — reading left to right, top to "
            "bottom — is exactly the order they'll be printed in.\n\n"
            "Here's everything you can do:\n\n"
            "• Move patches around. Just drag a square (or several at once) to a "
            "new spot — a magenta line shows where it will land when you let go. "
            "Prefer the keyboard? Select some patches and nudge them with Alt and "
            "the arrow keys, or press F to send them to the very front and L to the "
            "very end.\n\n"
            "• Pick patches. Click a square to select it, hold Shift or Ctrl to "
            "select more, or drag a box around several. If you turn on \"Highlight "
            "selected in preview\", the patches you pick here light up in the "
            "preview too (and the other way round), so it's easy to see where each "
            "one sits on the page.\n\n"
            "• Change a colour. Select one or more patches, then use \"Set colour "
            "of selection…\" over in the Patches controls to give them a new "
            "colour.\n\n"
            "• Add or remove. The Patches controls also let you add fresh patches "
            "or delete the ones you've selected.\n\n"
            "And don't worry about the \"Swatch size\" slider next to this button — "
            "it only changes how big the squares look on your screen. It makes no "
            "difference at all to the printed chart.",
            self, min_width=520))
        top.addSpacing(10)
        lv.addLayout(top)
        # It isn't obvious the swatches can be rearranged, so spell it out right
        # above the grid (Knut's suggestion) where it's always visible — the full
        # story stays in the ⓘ. (Below the grid it gets squeezed by the list.)
        grid_hint = QLabel(
            tr("Tip: drag a swatch to move it. Shift- or {ext}-click to pick "
               "several, then drag — or use the First / Up / Down / Last buttons."
               ).format(ext=_mod_keys()["ext"]),
            left)
        grid_hint.setWordWrap(True)
        # palette(mid) was nearly invisible on the dark grid background; use an
        # explicit readable grey with a little breathing room.
        grid_hint.setStyleSheet(
            "color: #b0b0b0; font-size: 11px; font-style: italic; padding: 2px 0;")
        lv.addWidget(grid_hint, 0)

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
        # Built-in indicator hidden — _ReorderListWidget paints a mid-gap
        # magenta line in dragMoveEvent / paintEvent instead.
        self._grid.setDropIndicatorShown(False)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setIconSize(QSize(_SWATCH, _SWATCH))
        self._grid.setSpacing(3)
        self._delegate = _SwatchDelegate(self._grid, _SWATCH)
        self._grid.setItemDelegate(self._delegate)
        self._grid.setGridSize(self._delegate.sizeHint(None, None))
        # Qt's default InternalMove reorder may emit either rowsMoved (when
        # the model implements moveRows) or rowsRemoved/rowsInserted (the
        # remove-then-insert fallback path). Connect to both so the drag is
        # always picked up. _schedule_auto_refresh is debounced, so double
        # firing during a single drop is harmless.
        def _after_drag(*_a):
            self._renumber()
            self._schedule_auto_refresh()
        self._grid.model().rowsMoved.connect(_after_drag)
        self._grid.model().rowsRemoved.connect(_after_drag)
        # Re-render the preview overlay when the grid selection changes —
        # only matters when "Highlight selected in preview" is on, but the
        # connection is harmless either way (_refresh_preview no-ops if the
        # toggle is off).
        self._grid.itemSelectionChanged.connect(self._on_grid_selection_changed)
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
        # Status / info line sits under the patch grid (left column only), not
        # spanning the whole window. Auto-hides a few seconds after each message.
        self._status = _AutoHideLabel(left)
        self._status.setStyleSheet("color: #888;")
        lv.addWidget(self._status)
        split.addWidget(left)

        # Middle: preview + page navigation
        mid = QWidget(self)
        midv = QVBoxLayout(mid)
        midv.setContentsMargins(0, 0, 0, 0)
        midv.setSpacing(6)
        self._preview = _PreviewLabel(self)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setText(tr("Preview will appear here."))
        self._preview.clicked.connect(self._on_preview_click)
        self._preview.marquee_finished.connect(self._on_marquee)
        # Re-scale the preview from the full-res cache when the label resizes,
        # so the displayed image stays sharp at any pane width.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._rescale_preview)
        self._preview.resized.connect(self._resize_timer.start)
        # Plain layout (no QScrollArea): the canvas-with-white-border
        # pattern in _rescale_preview already paints the visible chart on a
        # white background. We give the label a small minimum size so it
        # always shows the border even with no chart loaded.
        self._preview.setMinimumSize(200, 200)
        self._preview.setSizePolicy(
            self._preview.sizePolicy().Policy.Expanding,
            self._preview.sizePolicy().Policy.Expanding,
        )
        midv.addWidget(self._preview, 1)
        self._page_bar = QWidget(mid)
        pbl = QHBoxLayout(self._page_bar)
        pbl.setContentsMargins(0, 0, 0, 0)
        self._prev_btn = QPushButton(tr("◀ Page"), self._page_bar)
        self._next_btn = QPushButton(tr("Page ▶"), self._page_bar)
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
        # The controls panel is wrapped in a scroll area so a stack of
        # printtarg knobs + Patches/Spacers + What-a-mess + actions never
        # falls off the bottom on smaller window heights. _FadeScroll paints
        # a top/bottom fade so the user can tell content continues above /
        # below the visible band.
        body = QHBoxLayout()
        # 16 px side inset (outer layout now has zero side margins so the
        # spectrum stripe can be full-bleed).
        body.setContentsMargins(16, 0, 16, 0)
        body.setSpacing(8)
        body.addWidget(split, 1)
        controls = self._build_controls()
        ctrl_scroll = _FadeScrollArea(self)
        ctrl_scroll.setWidget(controls)
        ctrl_scroll.setWidgetResizable(True)
        ctrl_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ctrl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ctrl_scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            " QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        # Right side: scrollable controls on top, fixed action footer below.
        # Only the controls scroll; "Update preview / Export / Save As" stay
        # pinned so they're always reachable on short windows.
        right = QWidget(self)
        # Match the panel's fixed width (+ scrollbar gutter) so the column
        # sits flush at the window edge without overflow.
        right.setFixedWidth(controls.width() + 14)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(ctrl_scroll, 1)
        rv.addWidget(self._build_action_bar(right), 0)
        body.addWidget(right, 0)
        outer.addLayout(body, 1)

    def _build_controls(self) -> QWidget:
        panel = QWidget(self)
        # Widened so the printtarg paper combo ("A4 (210 × 297 mm)
        # Portrait") and the per-locale spinboxes don't clip on first
        # open. Bump beyond 320 also gives custom-paper W/H spinboxes
        # room to breathe.
        panel.setFixedWidth(360)
        # Container stylesheet — the only reliable way to shrink button
        # height ([[feedback_qt_button_sizing]]: setMinimumHeight on the
        # button itself is overridden by Qt's compound-widget CSS). Magenta
        # accents on checked/focused state to match the dialog's magenta
        # drop-indicator + "What a mess!" bang, scoped to the dialog so
        # the app-wide cyan accent isn't touched.
        panel.setStyleSheet(f"""
            QPushButton {{ padding: 4px 8px; min-height: 26px; font-size: 11px; }}
            QGroupBox  {{ font-size: 12px; }}
            QLabel     {{ font-size: 11px; }}
            QRadioButton {{ font-size: 12px; }}
            QCheckBox  {{ font-size: 11px; }}
            QCheckBox::indicator:checked {{
                background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
            }}
            QCheckBox::indicator:hover {{ border-color: {SPEC_MAGENTA}; }}
            QRadioButton::indicator:checked {{
                background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
            }}
            /* A ticked-but-disabled box must read as off — without this the
               magenta :checked fill wins over Qt's disabled greying, so an
               unselected panel (e.g. "Generate colour sets") still showed bright
               ticks. The two-state selector outranks the single :checked rule. */
            QCheckBox::indicator:checked:disabled,
            QRadioButton::indicator:checked:disabled {{
                background: #4a4a4a; border-color: #4a4a4a;
            }}
            QLineEdit:focus, QComboBox:focus,
            QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {SPEC_MAGENTA};
            }}
            /* The dropdown's hovered/selected row defaulted to the app-wide
               cyan; tint it magenta to match the rest of the dialog. */
            QComboBox QAbstractItemView {{
                selection-background-color: {SPEC_MAGENTA};
                selection-color: white;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {SPEC_MAGENTA}; color: white;
            }}
        """)
        v = QVBoxLayout(panel)
        v.setContentsMargins(4, 0, 0, 0)
        v.setSpacing(8)

        # Target mode
        mode_box = QGroupBox(tr("Edit chart"), panel)
        mb = QVBoxLayout(mode_box)
        self._mode_patches = QRadioButton(tr("Patches"), mode_box)
        self._mode_spacers = QRadioButton(tr("Spacers"), mode_box)
        self._mode_patches.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._mode_patches)
        self._mode_group.addButton(self._mode_spacers)
        self._mode_patches.toggled.connect(self._on_mode_change)
        mb.addWidget(self._mode_patches)
        mb.addWidget(self._mode_spacers)
        v.addWidget(mode_box)

        # printtarg options — all the knobs the New chart dialog exposes,
        # editable on an already-loaded chart. Mirrors LayoutOptions; each
        # widget pushes back to self._options + schedules an auto-preview.
        pt_box = QGroupBox(tr("printtarg"), panel)
        ptg = QGridLayout(pt_box)
        ptg.setHorizontalSpacing(4)
        ptg.setVerticalSpacing(4)
        # Instrument + Paper rows — affect ChartSpec, not LayoutOptions
        # (printtarg reads them as -i / -p), but they live here so the
        # editor has the same coverage as the New chart dialog.
        ptg.addWidget(QLabel(tr("Instrument:")), 0, 0)
        self._pt_instr = NoScrollComboBox(pt_box)
        for code, label in _INSTRUMENTS:
            self._pt_instr.addItem(label, code)
        self._pt_instr.currentIndexChanged.connect(self._on_pt_instr_changed)
        ptg.addWidget(self._pt_instr, 0, 1, 1, 3)
        ptg.addWidget(QLabel(tr("Paper:")), 1, 0)
        # Vertical container: paper combo on top, custom W/H row below
        # (hidden unless "Custom" is the selection). Keeps the rest of
        # the grid's row indices untouched.
        paper_container = QWidget(pt_box)
        paper_v = QVBoxLayout(paper_container)
        paper_v.setContentsMargins(0, 0, 0, 0)
        paper_v.setSpacing(4)
        self._pt_paper = NoScrollComboBox(paper_container)
        for code in _PAPER_ORDER:
            self._pt_paper.addItem(
                _PAPER_LABELS_WITH_CUSTOM.get(code, code), code)
        self._pt_paper.currentIndexChanged.connect(self._on_pt_paper_changed)
        paper_v.addWidget(self._pt_paper)
        self._pt_paper_custom_row = QWidget(paper_container)
        cust_l = QHBoxLayout(self._pt_paper_custom_row)
        cust_l.setContentsMargins(0, 0, 0, 0)
        cust_l.setSpacing(6)
        cust_l.addWidget(QLabel(tr("W (mm):")))
        self._pt_paper_w = NoScrollSpinBox(self._pt_paper_custom_row)
        self._pt_paper_w.setRange(10, 9999)
        self._pt_paper_w.setValue(210)
        self._pt_paper_w.setMinimumWidth(76)
        cust_l.addWidget(self._pt_paper_w)
        cust_l.addWidget(QLabel(tr("H (mm):")))
        self._pt_paper_h = NoScrollSpinBox(self._pt_paper_custom_row)
        self._pt_paper_h.setRange(10, 9999)
        self._pt_paper_h.setValue(297)
        self._pt_paper_h.setMinimumWidth(76)
        cust_l.addWidget(self._pt_paper_h)
        cust_l.addStretch(1)
        self._pt_paper_w.valueChanged.connect(self._on_pt_paper_custom_changed)
        self._pt_paper_h.valueChanged.connect(self._on_pt_paper_custom_changed)
        _as_compact(self._pt_paper_w, self._pt_paper_h)
        self._pt_paper_custom_row.setVisible(False)
        paper_v.addWidget(self._pt_paper_custom_row)
        ptg.addWidget(paper_container, 1, 1, 1, 3)
        _as_compact(self._pt_instr, self._pt_paper)
        # Spacers row — checkboxes wired as a mutex group, with the
        # all-off state permitted (printtarg's default = coloured, so an
        # all-off picker just falls back to that). Picking "None" also
        # disables the Spacer -A field since there are no spacers to
        # scale.
        ptg.addWidget(QLabel(tr("Spacers:")), 2, 0)
        sp_row = QHBoxLayout()
        sp_row.setSpacing(6)
        self._pt_sp_col  = QCheckBox(tr("Coloured"), pt_box)
        self._pt_sp_bw   = QCheckBox(tr("B&&W"), pt_box)
        self._pt_sp_none = QCheckBox(tr("None"), pt_box)
        self._pt_sp_col.setChecked(True)
        _wire_spacer_mutex(
            (self._pt_sp_col, self._pt_sp_bw, self._pt_sp_none)
        )
        for cb in (self._pt_sp_col, self._pt_sp_bw, self._pt_sp_none):
            cb.toggled.connect(self._on_printtarg_changed)
            sp_row.addWidget(cb)
        sp_row.addStretch(1)
        ptg.addLayout(sp_row, 2, 1, 1, 3)
        # Scales row. Min-width on every spinbox so 3-digit / decimal
        # values don't truncate to "30" / "1.0" inside the panel.
        SPIN_W = 76
        ptg.addWidget(QLabel(tr("Patch -a:")), 3, 0)
        self._pt_a = NoScrollDoubleSpinBox(pt_box)
        self._pt_a.setRange(0.3, 3.0)
        self._pt_a.setSingleStep(0.05)
        self._pt_a.setValue(1.0)
        self._pt_a.setMinimumWidth(SPIN_W)
        self._pt_a.valueChanged.connect(self._on_printtarg_changed)
        ptg.addWidget(self._pt_a, 3, 1)
        ptg.addWidget(QLabel(tr("Spacer -A:")), 3, 2)
        self._pt_A = NoScrollDoubleSpinBox(pt_box)
        self._pt_A.setRange(0.3, 3.0)
        self._pt_A.setSingleStep(0.05)
        self._pt_A.setValue(1.0)
        self._pt_A.setMinimumWidth(SPIN_W)
        self._pt_A.valueChanged.connect(self._on_printtarg_changed)
        ptg.addWidget(self._pt_A, 3, 3)
        # Margin + DPI row
        ptg.addWidget(QLabel(tr("Margin (mm):")), 4, 0)
        self._pt_m = NoScrollSpinBox(pt_box)
        self._pt_m.setRange(0, 50)
        self._pt_m.setValue(6)
        self._pt_m.setMinimumWidth(SPIN_W)
        self._pt_m.valueChanged.connect(self._on_printtarg_changed)
        ptg.addWidget(self._pt_m, 4, 1)
        ptg.addWidget(QLabel(tr("DPI:")), 4, 2)
        self._pt_dpi = NoScrollSpinBox(pt_box)
        self._pt_dpi.setRange(72, 1200)
        self._pt_dpi.setSingleStep(50)
        self._pt_dpi.setValue(300)
        self._pt_dpi.setMinimumWidth(SPIN_W)
        self._pt_dpi.valueChanged.connect(self._on_printtarg_changed)
        ptg.addWidget(self._pt_dpi, 4, 3)
        _as_compact(self._pt_a, self._pt_A, self._pt_m, self._pt_dpi)
        # Bit depth row
        ptg.addWidget(QLabel(tr("Bit depth:")), 5, 0)
        bd_row = QHBoxLayout()
        self._pt_bd8  = QRadioButton(tr("8-bit"), pt_box)
        self._pt_bd16 = QRadioButton(tr("16-bit"), pt_box)
        self._pt_bd8.setChecked(True)
        bd_grp = QButtonGroup(pt_box)
        bd_grp.addButton(self._pt_bd8)
        bd_grp.addButton(self._pt_bd16)
        self._pt_bd8.toggled.connect(self._on_printtarg_changed)
        self._pt_bd16.toggled.connect(self._on_printtarg_changed)
        bd_row.addWidget(self._pt_bd8)
        bd_row.addWidget(self._pt_bd16)
        bd_row.addStretch(1)
        ptg.addLayout(bd_row, 5, 1, 1, 3)
        # Instrument-conditional checkboxes (visibility flipped from
        # self._spec.instrument_flag in _sync_printtarg_widgets).
        self._pt_L = QCheckBox(tr("Suppress left clip border (-L)"), pt_box)
        self._pt_L.setToolTip(tr("i1Pro / 3+ only. Frees the strip for patches."))
        self._pt_L.toggled.connect(self._on_printtarg_changed)
        self._pt_P = QCheckBox(tr("Don't limit strip length (-P)"), pt_box)
        self._pt_P.setToolTip(tr("i1Pro / 3+ only. Lets a long strip span "
                              "multiple strokes."))
        self._pt_P.toggled.connect(self._on_printtarg_changed)
        self._pt_dd = QCheckBox(tr("Double density (-h)"), pt_box)
        self._pt_dd.setToolTip(tr("ColorMunki only. Tighter strip layout for "
                               "the ColorMunki rig. Mutually exclusive "
                               "with Triple."))
        self._pt_dd.toggled.connect(self._on_dd_toggled)
        self._pt_td = QCheckBox(tr("Triple density (i1Pro layout)"), pt_box)
        self._pt_td.setToolTip(
            tr("ColorMunki + rig only. Renders with the i1Pro strip layout at "
            "scale 1.3 / margin 5 / strip-limit off / left-border suppressed, "
            "then patches TARGET_INSTRUMENT back to ColorMunki so chartread "
            "still drives your meter. Mutually exclusive with Double."))
        self._pt_td.toggled.connect(self._on_td_toggled)
        self._pt_force_tag = QCheckBox(tr("Force “randomised” tag"), pt_box)
        self._pt_force_tag.setChecked(bool(
            self._settings.get("ti2_editor_force_tag", False)))
        self._pt_force_tag.setEnabled(False)   # enabled only on an unsafe layout
        self._pt_force_tag.setToolTip(
            tr("Only needed for risky layouts (click the ⓘ for details). Charts that "
            "are already well mixed get tagged as randomised automatically."))
        self._pt_force_tag.toggled.connect(self._on_force_tag_toggled)
        ptg.addWidget(self._pt_L,  6, 0, 1, 4)
        ptg.addWidget(self._pt_P,  7, 0, 1, 4)
        ptg.addWidget(self._pt_dd, 8, 0, 1, 4)
        ptg.addWidget(self._pt_td, 9, 0, 1, 4)
        tag_row = QHBoxLayout()
        tag_row.setContentsMargins(0, 0, 0, 0)
        tag_row.addWidget(self._pt_force_tag)
        tag_row.addStretch(1)
        tag_row.addWidget(_magenta_tip(
            "Force “randomised” tag",
            "Good news first: you usually don't need to touch this. It stays "
            "greyed-out and only wakes up when a chart needs your attention.\n\n"
            "Here's the background. When you measure a chart, you slide your "
            "instrument along each row of patches (a row is called a \"strip\"). "
            "Many instruments can read a strip in either direction — but only when "
            "the chart is marked as having its colours in a shuffled order. So when "
            "you save, ChromIQ takes a quick look at your layout:\n\n"
            "• If the colours are nicely mixed up, it adds that \"randomised\" mark "
            "for you automatically. Nothing to do — bidirectional measuring just "
            "works, and this checkbox stays greyed-out.\n\n"
            "• If the colours sit in a tidy pattern instead — a smooth fade from "
            "light to dark, or a neat colour grid, especially on a big chart — then "
            "neighbouring strips look too alike. Marking such a chart as randomised "
            "is risky: your instrument can mix the strips up and quietly record the "
            "wrong readings, giving you a profile with colour casts and no obvious "
            "warning. So ChromIQ does NOT mark it automatically, and instead "
            "switches this checkbox on so the choice is yours.\n\n"
            "Ticking it then says \"I understand the risk — mark it as randomised "
            "anyway.\" That's only sensible if you happen to know the order is "
            "genuinely fine, or you'd rather re-shuffle the chart. Either way, "
            "nothing on the printed page moves — only a note inside the file "
            "changes.\n\n"
            "If you're unsure, leave it unticked and the chart is simply measured "
            "in one direction, which is always safe.",
            pt_box, min_width=560))
        ptg.addLayout(tag_row, 10, 0, 1, 4)
        # NOTE: pt_box is added to the panel layout *after* the Patches and
        # Spacers boxes below, so the on-paper order is Patches → Spacers →
        # printtarg (the chart-content controls users edit most often stay
        # at the top; printtarg's render knobs are set-once-then-forget).
        self._pt_box = pt_box

        # Patch controls
        self._patch_box = QGroupBox(tr("Patches"), panel)
        pb = QVBoxLayout(self._patch_box)
        self._hl_patches = QCheckBox(
            tr("Highlight selected in preview"), self._patch_box)
        self._hl_patches.setToolTip(
            tr("Two-way link between the swatch grid and the preview:\n"
            "• selecting patches on the left highlights them in the preview\n"
            "• clicking or marquee-dragging on the preview selects them on "
            "the left"))
        self._hl_patches.toggled.connect(self._on_patch_highlight_toggled)
        pb.addWidget(self._hl_patches)
        set_col = QPushButton(tr("Set colour of selection…"), self._patch_box)
        set_col.clicked.connect(self._set_patch_colour)
        pb.addWidget(set_col)
        tone_row = QHBoxLayout()
        dark = QPushButton(tr("Darken 10%"), self._patch_box)
        light = QPushButton(tr("Lighten 10%"), self._patch_box)
        dark.clicked.connect(lambda: self._transform_selection(0.9))
        light.clicked.connect(lambda: self._transform_selection(1.0 / 0.9))
        tone_row.addWidget(dark)
        tone_row.addWidget(light)
        pb.addLayout(tone_row)
        addrem = QHBoxLayout()
        add_b = QPushButton(tr("Add…"), self._patch_box)
        add_b.setToolTip(tr("Add a single chosen colour, or generate colour "
                            "sets (3D cube, skin tones, blues, greens, …) and "
                            "add them to the chart"))
        add_b.clicked.connect(self._add_patch)
        rem_b = QPushButton(tr("Remove"), self._patch_box)
        rem_b.setToolTip(tr("Remove the selected patches"))
        rem_b.clicked.connect(self._remove_selected_patches)
        addrem.addWidget(add_b)
        addrem.addWidget(rem_b)
        pb.addLayout(addrem)
        combine = QHBoxLayout()
        append_b = QPushButton(tr("Append from file…"), self._patch_box)
        append_b.setToolTip(
            tr("Load another patch set (.ti2 / .ti1 / .ti3 / CGATS .txt / "
            "i1Profiler .pxf / .pwxf) and add its colours to the start or end "
            "of this chart — an easy way to combine sets. RGB only."))
        append_b.clicked.connect(self._append_from_file)
        combine.addWidget(append_b)
        view3d_b = QPushButton(tr("3D distribution…"), self._patch_box)
        view3d_b.setToolTip(
            tr("Show the patch set as a rotatable 3D RGB cube so you can see how "
            "evenly the colours cover the gamut and where they bunch up."))
        view3d_b.clicked.connect(self._show_3d_distribution)
        combine.addWidget(view3d_b)
        pb.addLayout(combine)
        reorder_lbl = QLabel(
            tr("Reorder (drag, Alt+arrows, F/L, or):"), self._patch_box)
        reorder_lbl.setWordWrap(True)
        pb.addWidget(reorder_lbl)
        order = QGridLayout()
        order.setHorizontalSpacing(6)
        order.setVerticalSpacing(4)
        # 2×2 grid — "FRONT/UP/DOWN/BACK" in caps doesn't fit a 4-wide row at
        # 230 px panel width, so split into two rows of two.
        btns = ((tr("First"), self._move_front, 0, 0), (tr("Up"),   self._move_up,    0, 1),
                (tr("Last"),  self._move_back,  1, 0), (tr("Down"), self._move_down,  1, 1))
        for label, fn, r, c in btns:
            b = QPushButton(label, self._patch_box)
            b.clicked.connect(fn)
            order.addWidget(b, r, c)
        pb.addLayout(order)
        v.addWidget(self._patch_box)

        # Spacer controls
        self._spacer_box = QGroupBox(tr("Spacers"), panel)
        sb = QVBoxLayout(self._spacer_box)
        pal_lbl = QLabel(
            tr("Spacer palette — the colours printtarg can put in the gaps "
            "between strips (it auto-picks the best-contrast one for each gap). "
            "Click a swatch to change a palette colour:"),
            self._spacer_box)
        pal_lbl.setWordWrap(True)
        sb.addWidget(pal_lbl)
        self._palette_row = QHBoxLayout()
        sb.addLayout(self._palette_row)
        sb.addSpacing(10)
        reset = QPushButton(tr("Reset palette"), self._spacer_box)
        reset.setToolTip(tr("Reset the spacer palette to printtarg's defaults"))
        reset.clicked.connect(self._reset_palette)
        sb.addWidget(reset)
        sb.addWidget(self._hline())
        paint_lbl = QLabel(
            tr("Recolour individual spacers (overrides the palette for those "
            "gaps): on the page preview in the centre, click a spacer to "
            "select it, or drag a box to select several. {add} adds to the "
            "selection, {remove} removes. Selected spacers get a magenta outline "
            "— then click “Paint…”.").format(**{k: _mod_keys()[k]
                                                 for k in ("add", "remove")}),
            self._spacer_box)
        paint_lbl.setWordWrap(True)
        sb.addWidget(paint_lbl)
        paint_row = QHBoxLayout()
        paint = QPushButton(tr("Paint…"), self._spacer_box)
        paint.setToolTip(tr("Recolour the spacers you selected on the page "
                            "preview in the centre."))
        paint.clicked.connect(self._paint_spacers)
        clear = QPushButton(tr("Clear"), self._spacer_box)
        clear.setToolTip(tr("Clear the spacer selection"))
        clear.clicked.connect(self._clear_spacer_selection)
        paint_row.addWidget(paint)
        paint_row.addWidget(clear)
        sb.addLayout(paint_row)
        self._spacer_box.setVisible(False)
        v.addWidget(self._spacer_box)

        # printtarg knobs section sits below Patches + Spacers — it's
        # always visible (the chart's printtarg config applies regardless
        # of the "Patches / Spacers" target mode above).
        v.addWidget(self._pt_box)

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
            tr("What a mess<span style=\"color: {SPEC_MAGENTA}; font-style: italic;\">!</span>").format(SPEC_MAGENTA=SPEC_MAGENTA),
            mess_box,
        )
        mess_head.setTextFormat(Qt.TextFormat.RichText)
        mess_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mess_head.setStyleSheet(
            "background: transparent;"
            " font-family: Georgia; font-size: 22px;"
        )
        ml.addWidget(mess_head)
        mess_sub = QLabel(tr("Time to tidy up."), mess_box)
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

        # NOTE: the action buttons (Update preview / Export / Save As) used to
        # live here at the bottom of the scrollable panel, which meant they
        # scrolled off-screen on short windows. They now sit in a fixed
        # footer built by _build_action_bar() and pinned below the scroll
        # area in _build_ui(), so they're always reachable.
        return panel

    def _build_action_bar(self, parent: QWidget) -> QWidget:
        """The always-visible action footer (Update preview / Export / Save).

        Built outside the scroll area so these primary actions never scroll
        out of view. Carries the same compact-button stylesheet the controls
        panel uses (per [[feedback_qt_button_sizing]] a container stylesheet is
        the reliable way to shrink button height)."""
        bar = QWidget(parent)
        bar.setStyleSheet(
            "QPushButton { padding: 4px 8px; min-height: 26px; font-size: 11px; }"
        )
        bv = QVBoxLayout(bar)
        bv.setContentsMargins(4, 4, 0, 0)
        bv.setSpacing(8)
        # "Update preview" shares its row with "Shuffle" — each takes half the
        # width so the preview button is a little smaller and the randomiser
        # sits right beside it.
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._preview_btn = QPushButton(tr("Update preview"), bar)
        self._preview_btn.clicked.connect(lambda: self._regenerate(save_to=None))
        top_row.addWidget(self._preview_btn, 1)
        self._shuffle_btn = QPushButton(tr("Shuffle"), bar)
        self._shuffle_btn.setToolTip(
            tr("Randomise the patch order. Mixing up a structured set (a smooth "
            "ramp or an RGB grid) so neighbouring strips read differently is "
            "what lets a chart be safely read in either direction — it also "
            "unlocks the “tag as randomised” option on Save."))
        self._shuffle_btn.clicked.connect(self._randomise_patches)
        top_row.addWidget(self._shuffle_btn, 1)
        bv.addLayout(top_row)
        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        # "Apply / Save" is the headline action (#70, Knut). It opens a small
        # window offering to *overwrite* the chart currently loaded in the Create
        # Chart tab with this layout, or to *Save As* (export the full deliverable
        # to a folder you pick) — folding the old standalone "Save As" button in.
        # ".replace" doubles the ampersand so Qt shows a literal "&" instead of
        # eating it as a mnemonic; the tr() key stays plain (translations carry a
        # single "&"). Styled in the scheme's magenta to mark it the lead action.
        self._apply_btn = QPushButton(tr("Apply / Save…").replace("&", "&&"), bar)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{ background: {SPEC_MAGENTA}; color: white; "
            f"border: none; border-radius: 4px; padding: 4px 10px; "
            f"min-height: 26px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #ff6690; }}"
            f"QPushButton:disabled {{ background: #4a4a4a; color: #9a9a9a; }}"
        )
        self._apply_btn.setToolTip(
            tr("Overwrite the chart currently loaded in the Create Chart tab with "
            "this layout — or Save As to export the full chart to a folder you "
            "pick, without leaving the editor."))
        self._apply_btn.clicked.connect(self._save_and_apply)
        save_row.addWidget(self._apply_btn)
        self._close_btn = QPushButton(tr("Close"), bar)
        self._close_btn.setToolTip(
            tr("Close the editor without saving. If the layout has unsaved "
            "changes you'll be asked to confirm first; “Apply / Save…” "
            "keeps your work."))
        self._close_btn.clicked.connect(self._on_close_clicked)
        save_row.addWidget(self._close_btn)
        bv.addLayout(save_row)
        return bar

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
        path = open_file_dialog(self, "Load chart",
                                "Argyll chart (*.ti2)", start_dir=start)
        if not path:
            return
        self._load_chart_from(Path(path))

    def _load_chart_from(self, path: Path) -> bool:
        """Load a ``.ti2`` (+ its sibling ``meta.json`` layout knobs, if any)
        into the editor. Shared by the Load .ti2 button and the open-time
        pre-load from the Create Chart tab (#45). Returns True on success."""
        try:
            spec = R.ChartSpec.from_ti2(path)
            program = R.default_program(spec)
        except Exception as exc:  # noqa: BLE001 — surface the parser's message
            QMessageBox.warning(self, tr("Could not load chart"), str(exc))
            return False
        # Restore the printtarg layout knobs from the chart folder's meta.json
        # if this chart was saved by ChromIQ (the .ti2 itself only carries
        # instrument / paper / spacer palette). Foreign charts have no editor
        # meta: reset to defaults and take the basename from the file stem.
        saved = R.load_editor_meta(path)
        if saved is not None:
            self._options, self._basename = saved
            note = f"Loaded {path.name} (restored saved settings)"
        else:
            self._options = R.LayoutOptions()
            self._basename = path.stem or "chart"
            note = f"Loaded {path.name}"
        # The chart's creation recipe (if it carries one) — so New chart / Add
        # reopen with this design rather than the app-wide last-used state.
        self._chart_recipe = R.load_editor_recipe(path)
        self._set_chart(spec, program, note, is_saved=True)
        return True

    def _new_chart(self) -> None:
        # Pre-load the current chart's recipe (if any) so the design can be
        # tweaked/recreated; capture the recipe the dialog reports back.
        dlg = _NewChartDialog(self._bin_dir, self._settings, self,
                              initial_recipe=self._chart_recipe)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_spec is None:
            return
        if dlg.result_options is not None:
            self._options = dlg.result_options
        self._basename = dlg.result_basename or "chart"
        self._chart_recipe = dlg.result_recipe
        self._set_chart(dlg.result_spec, dlg.result_program or [], "New chart")

    def _set_chart(self, spec: R.ChartSpec, program: list[tuple], note: str,
                   *, is_saved: bool = False) -> None:
        # Loading/creating a chart replaces the document: suppress capture while
        # we repopulate, then start a fresh history with this chart as baseline.
        self._suppress_undo = True
        self._undo_timer.stop()
        self._spec = spec
        # Seed the spacer palette from the source chart's own .ti1 (if its
        # sibling was found by ChartSpec.from_ti2), so loaded charts render
        # with the palette they were originally made with instead of
        # snapping to printtarg's defaults.
        self._palette = (list(spec.density_extremes)
                         if spec.density_extremes else None)
        self._paint.clear()
        self._sel_spacers.clear()
        self._populate_grid(program)
        self._build_palette_row()
        # Mirror the live printtarg-options widgets to the chart we just
        # loaded (so loading after a new-chart create reflects whatever the
        # user picked, and loading a file resets to defaults).
        self._sync_printtarg_widgets()
        self._chart_note = note
        # Baseline for unsaved-change detection (#49): a chart loaded from disk
        # starts clean; a freshly created / generated one is dirty until saved
        # (the sentinel _saved_sig never matches the live signature).
        if is_saved:
            self._saved_sig = self._current_signature()
        self._refresh_info()
        self._refresh_enabled()
        self._suppress_undo = False
        self._reset_undo()
        # Auto-render the initial preview so the user sees the chart
        # immediately instead of having to click "Update preview" first.
        if program:
            self._status.setText(tr("Rendering initial preview…"))
            self._regenerate(save_to=None)
        else:
            self._status.setText(tr("Empty chart — add patches, then preview."))

    # -- unsaved-change tracking (#49) -------------------------------------
    def _current_signature(self) -> tuple:
        """A hashable snapshot of everything the user can edit: the patch
        program (colours + order), the spacer palette + per-spacer paint, and
        the printtarg layout knobs. Compared against _saved_sig to detect
        unsaved edits — so any change (re-colour, reorder, add/remove, spacer
        paint, layout tweak) flips the dialog dirty without per-edit hooks."""
        return (
            tuple(self._program_from_grid()),
            tuple(self._palette) if self._palette else None,
            tuple(sorted(self._paint.items())),
            astuple(self._options) if self._options is not None else None,
        )

    def _is_dirty(self) -> bool:
        """True when a chart is open and differs from its last saved state."""
        return (self._spec is not None
                and self._current_signature() != self._saved_sig)

    def _mark_saved(self) -> None:
        """Record the current content as the saved baseline (clean)."""
        self._saved_sig = self._current_signature()

    # -- undo / redo --------------------------------------------------------
    def _make_snapshot(self) -> _EditorSnapshot:
        """Deep-copy the current editable state into a history snapshot."""
        return _EditorSnapshot(
            program=self._program_from_grid(),       # tuples → immutable items
            palette=list(self._palette) if self._palette else None,
            paint=dict(self._paint),
            options=copy.deepcopy(self._options),
            instr=self._spec.instrument_flag if self._spec else "",
            paper=self._spec.paper_flag if self._spec else "",
            paper_mm=tuple(self._spec.paper_mm) if self._spec else (0.0, 0.0),
        )

    def _reset_undo(self) -> None:
        """Start a fresh history with the current chart as the baseline. Called
        whenever a different chart is loaded/created — undo never crosses charts."""
        if self._spec is None:
            self._undo_stack = []
            self._undo_index = -1
        else:
            self._undo_stack = [self._make_snapshot()]
            self._undo_index = 0
        self._refresh_undo_enabled()

    def _clear_undo_history(self) -> None:
        """Drop the entire in-memory history (on close). Nothing is persisted —
        this just frees the snapshots a touch sooner than garbage collection."""
        self._undo_timer.stop()
        self._undo_stack = []
        self._undo_index = -1

    def _note_edit(self) -> None:
        """Mark that the user changed something; (re)arm the debounced capture.
        No-op while we're repopulating the grid ourselves (load / undo / redo)."""
        if self._suppress_undo or self._spec is None:
            return
        self._undo_timer.start()

    def _capture_undo(self) -> None:
        """Push the current state as a new history step, unless it matches the
        step we're already sitting on. Pushing past an undone branch discards
        the redo tail, and the stack is capped at _UNDO_DEPTH steps."""
        if self._suppress_undo or self._spec is None:
            return
        snap = self._make_snapshot()
        if (0 <= self._undo_index < len(self._undo_stack)
                and snap.key == self._undo_stack[self._undo_index].key):
            return  # nothing actually changed
        del self._undo_stack[self._undo_index + 1:]   # drop the redo branch
        self._undo_stack.append(snap)
        # Keep baseline + _UNDO_DEPTH steps; drop the oldest beyond that.
        excess = len(self._undo_stack) - (_UNDO_DEPTH + 1)
        if excess > 0:
            del self._undo_stack[:excess]
        self._undo_index = len(self._undo_stack) - 1
        self._refresh_undo_enabled()

    def _undo(self) -> None:
        # A pending capture would otherwise fire mid-undo and corrupt the index.
        if self._undo_timer.isActive():
            self._undo_timer.stop()
            self._capture_undo()
        if self._undo_index <= 0:
            return
        self._undo_index -= 1
        self._restore_snapshot(self._undo_stack[self._undo_index])
        self._status.setText(tr("Undid one step."))
        self._refresh_undo_enabled()

    def _redo(self) -> None:
        if self._undo_index >= len(self._undo_stack) - 1:
            return
        self._undo_index += 1
        self._restore_snapshot(self._undo_stack[self._undo_index])
        self._status.setText(tr("Redid one step."))
        self._refresh_undo_enabled()

    def _restore_snapshot(self, snap: _EditorSnapshot) -> None:
        """Re-apply a history snapshot to the live editor, then re-render."""
        if self._spec is None:
            return
        self._suppress_undo = True
        try:
            self._spec.instrument_flag = snap.instr
            self._spec.paper_flag = snap.paper
            self._spec.paper_mm = tuple(snap.paper_mm)
            self._options = copy.deepcopy(snap.options) if snap.options else R.LayoutOptions()
            self._palette = list(snap.palette) if snap.palette else None
            self._paint = dict(snap.paint)
            self._sel_spacers.clear()
            self._populate_grid(snap.program)
            self._renumber()
            self._build_palette_row()
            self._sync_printtarg_widgets()
            self._refresh_info()
            self._refresh_enabled()
        finally:
            self._suppress_undo = False
        if snap.program:
            self._regenerate(save_to=None)

    def _refresh_undo_enabled(self) -> None:
        self._undo_btn.setEnabled(self._undo_index > 0)
        self._redo_btn.setEnabled(
            0 <= self._undo_index < len(self._undo_stack) - 1)

    def _confirm_discard(self) -> bool:
        """Ask before throwing away unsaved edits. Returns True to proceed
        (close), False to stay. No prompt when there's nothing unsaved."""
        if not self._is_dirty():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Discard changes?"))
        box.setText(tr("This chart has unsaved changes."))
        box.setInformativeText(
            tr("If you close now they'll be lost. Use “Save As…” or "
               "“Save & apply…” first to keep them."))
        discard = box.addButton(tr("Discard changes"),
                                QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton(tr("Keep editing"),
                             QMessageBox.ButtonRole.RejectRole)
        # The app's button stylesheet keeps buttons short, which clips these
        # longer labels; give each room for its full text plus the stylesheet's
        # padding + Fusion frame (same fix as the Append-from-file prompt).
        for b in (discard, keep):
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 64)
        box.setDefaultButton(keep)
        box.exec()
        return box.clickedButton() is discard

    def _on_close_clicked(self) -> None:
        # Just close — the confirmation lives in closeEvent so the Close button
        # and the window-corner X share one prompt. (Confirming here *and*
        # letting closeEvent re-confirm asked twice — Knut's #49 report.)
        self.close()

    def _schedule_auto_refresh(self) -> None:
        """Restart the debounced preview timer (called from user edit hooks)."""
        if self._spec is not None and self._grid.count() > 0:
            self._auto_timer.start()
        # Nearly every edit funnels through here (reorder/remove via _after_drag,
        # add/recolour/options/paper/palette directly), so it's the natural place
        # to note an undo step. Spacer paint and palette-reset bypass it and call
        # _note_edit themselves.
        self._note_edit()

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
        # Keep the top-right "… N patches …" readout in step with the grid:
        # _renumber runs after every add / remove / append / reorder.
        self._refresh_info()

    def _refresh_info(self) -> None:
        """Rewrite the header readout from the live grid count + chart flags."""
        if self._spec is None:
            return
        note = getattr(self, "_chart_note", "")
        text = tr("{note} — {n} patches, -i{instr} -p{paper}").format(
            note=note, n=self._grid.count(),
            instr=self._spec.instrument_flag,
            paper=self._spec.paper_flag)
        self._info.setText(text)
        # The label may be clipped (Ignored width policy keeps long names from
        # pushing the source-row buttons off-screen), so keep the full readout
        # reachable on hover.
        self._info.setToolTip(text)

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

    def _grid_item(self, rgb: tuple) -> QListWidgetItem:
        """Build a grid item for one RGB patch (icon + UserRole payload)."""
        it = QListWidgetItem(_swatch_icon(rgb, self._swatch_size), "")
        it.setData(Qt.ItemDataRole.UserRole, tuple(rgb))
        return it

    def _add_patch(self) -> None:
        """Open the Add dialog — a single chosen colour, or one or more
        generated colour sets (3D cube, skin tones, blues, greens, greys, …) —
        and splice the result into the chart."""
        dlg = _AddPatchesDialog(self._settings, self,
                                existing_patches=self._program_from_grid(),
                                initial_recipe=self._chart_recipe)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_program:
            return
        extra = dlg.result_program
        if self._spec is None:
            # Nothing loaded yet — seed a fresh blank chart from the added
            # patches (there's nothing to append to). _set_chart renders the
            # initial preview; plain grid edits don't, since _schedule_auto_
            # refresh needs a spec.
            self._set_chart(R.ChartSpec.new("i1", "A4"), list(extra),
                            tr("New chart"))
            return
        if len(extra) == 1:
            # A single hand-picked colour goes straight on the end (no prompt).
            self._grid.addItem(self._grid_item(extra[0]))
            self._renumber()
            self._status.setText(tr("Patch added. Updating preview…"))
            self._schedule_auto_refresh()
            return
        extra = self._resolve_existing_overlap(extra)
        if extra is None:
            return
        if not extra:
            self._status.setText(
                tr("Those colours are all already in the chart, or too close to "
                   "ones in it — nothing new to add."))
            return
        self._place_patches_into_grid(
            extra, tr("{n} colours are ready to add.").format(n=len(extra)))

    def _resolve_existing_overlap(self, extra: list[tuple]) -> list[tuple] | None:
        """If any generated colour already sits in the loaded chart, explain it
        in plain language and let the user nudge the repeats unique, add only the
        new ones, add everything as-is, or cancel. Returns the patches to add
        (possibly relocated or filtered), or ``None`` to cancel. No overlap ⇒
        returns ``extra`` unchanged."""
        existing = self._program_from_grid()
        dup = G.count_too_close(existing, extra, _GEN_MIN_DIST)
        if dup == 0:
            return extra
        box = QMessageBox(self)
        box.setWindowTitle(tr("Some of these colours are too close to ones "
                              "already in your chart"))
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(
            tr("1 of the {total} colours you're about to add lands on — or right "
               "next to — a colour already in this chart.").format(
                   total=len(extra)) if dup == 1
            else tr("{dup} of the {total} colours you're about to add land on — "
                    "or right next to — colours already in this chart.").format(
                        dup=dup, total=len(extra)))
        box.setInformativeText(tr(
            "Two patches that are almost the same colour measure almost the same "
            "thing, so the second one mostly wastes paper, ink and measuring "
            "time without making your profile any more accurate.\n\n"
            "Here's what each choice does:\n\n"
            "• Make them unique (recommended) — keeps every colour you're "
            "adding, but gently nudges each crowded one to the nearest free spot "
            "so it keeps a small gap from the colours already in your chart. You "
            "still get the full number of patches, just none sitting on top of an "
            "existing one.\n\n"
            "• Add only the new ones — adds just the colours that are clear of "
            "the chart and drops the ones that would land on or crowd a colour "
            "already in it, so nothing is printed almost twice.\n\n"
            "• Add anyway — adds them exactly as generated, crowding and all. "
            "Pick this only if you deliberately want near-identical colours, for "
            "example to average several readings of the same patch.\n\n"
            "• Cancel — go back without adding anything, so you can adjust the "
            "generator options first."))
        unique_btn = box.addButton(tr("Make them unique"),
                                   QMessageBox.ButtonRole.AcceptRole)
        onlynew_btn = box.addButton(tr("Add only the new ones"),
                                    QMessageBox.ButtonRole.AcceptRole)
        anyway_btn = box.addButton(tr("Add anyway"),
                                   QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        # The app's button stylesheet keeps buttons short, so longer labels clip
        # at the default width — give each room for its full label plus padding.
        for b in (unique_btn, onlynew_btn, anyway_btn, cancel_btn):
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 64)
        box.setDefaultButton(unique_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is unique_btn:
            return G.enforce_min_distance(extra, _GEN_MIN_DIST, existing=existing)
        if clicked is onlynew_btn:
            return G.drop_too_close(existing, extra, _GEN_MIN_DIST)
        if clicked is anyway_btn:
            return extra
        return None

    def _append_from_file(self) -> None:
        """Combine another patch set into this chart (start or end).

        Parses any supported RGB patch file via
        :func:`workflow.ti2_relayout.load_rgb_program`, asks whether to place
        the new colours at the start or the end, splices them into the grid,
        and re-previews. RGB-only — non-RGB sources surface the parser's error.
        """
        if self._spec is None:
            self._status.setText(tr("Load or create a chart first."))
            return
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        patterns = " ".join(f"*{s}" for s in R.LOADABLE_PATCH_SUFFIXES)
        path = open_file_dialog(
            self, "Append patches from file",
            f"Patch sets ({patterns});;All files (*)", start_dir=start)
        if not path:
            return
        try:
            extra = R.load_rgb_program(Path(path))
        except Exception as exc:  # noqa: BLE001 — show the parser's message
            QMessageBox.warning(self, tr("Could not load patches"), str(exc))
            return
        if not extra:
            QMessageBox.warning(self, tr("No patches"),
                                tr("No RGB patches found in {name}.").format(
                                    name=Path(path).name))
            return

        n = len(extra)
        if n == 1:
            ready = tr("1 colour from “{name}” is ready to add."
                       ).format(name=Path(path).name)
        else:
            ready = tr("{n} colours from “{name}” are ready to add."
                       ).format(n=n, name=Path(path).name)
        self._place_patches_into_grid(extra, ready)

    def _place_patches_into_grid(self, extra: list[tuple], ready: str) -> None:
        """Ask whether to add *extra* RGB patches at the start or the end of the
        chart, splice them in, and re-preview. ``ready`` is the lead sentence of
        the prompt (it names where the colours came from). Shared by "Add…"
        (generated sets) and "Append from file…"."""
        box = QMessageBox(self)
        box.setWindowTitle(tr("Add the new colours"))
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(tr("Where would you like the new colours?"))
        box.setInformativeText(
            ready + " "
            + tr("You can place them right at the beginning of the chart or "
                 "tack them on at the end — whichever makes the combined set "
                 "easier to work with."))
        start_btn = box.addButton(tr("Add to the beginning"),
                                   QMessageBox.ButtonRole.AcceptRole)
        end_btn = box.addButton(tr("Add to the end"),
                                QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        # The app's button stylesheet keeps buttons short, so the longer
        # "Add to the beginning"/"end" labels clip at the default width. Give
        # each button room for its full label plus the stylesheet's own
        # horizontal padding + Fusion frame (this also widens the dialog).
        for b in (start_btn, end_btn, cancel_btn):
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 64)
        box.setDefaultButton(end_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is start_btn:
            for i, rgb in enumerate(extra):
                self._grid.insertItem(i, self._grid_item(rgb))
            where = "start"
        elif clicked is end_btn:
            for rgb in extra:
                self._grid.addItem(self._grid_item(rgb))
            where = "end"
        else:
            return
        self._renumber()
        if where == "start":
            added = tr("Added {n} patches at the beginning.").format(n=len(extra))
        else:
            added = tr("Added {n} patches at the end.").format(n=len(extra))
        self._status.setText(added + " " + tr("Updating preview…"))
        self._schedule_auto_refresh()

    def _show_3d_distribution(self) -> None:
        """Open the rotatable 3D RGB-cube view of the current patch set."""
        program = self._program_from_grid()
        if not program:
            self._status.setText(tr("Add patches first to see their distribution."))
            return
        from ui.dialogs.patch_cube_dialog import PatchCubeDialog
        from ui.theme import resolve_mode
        from ui.tabs.tab_chart import comparable_presets
        mode = resolve_mode(self._settings.get("appearance", "auto"))
        # Same "Compare with profile" dropdown as the Tools 3D viewer (#66);
        # rebuilt each open so newly saved / deleted presets track automatically.
        try:
            presets = comparable_presets(self._settings)
        except Exception as exc:  # noqa: BLE001 — never block the viewer on this
            log.warning("comparable_presets failed: %s", exc)
            presets = []
        # No target_name: self._basename is the printer-profile name, which post-#70
        # doesn't describe the layout — show the neutral "Current chart" label
        # rather than a misleading profile name (Knut, #70 follow-up).
        PatchCubeDialog(program, mode=mode,
                        compare_presets=presets, numbered=True,  # patch # in hover (#67)
                        parent=self).exec()

    def _randomise_patches(self) -> None:
        """Shuffle the patch order into a random permutation, then re-preview.

        A pure reorder — no colour changes — so it's lossless: every patch
        stays, only its position moves. Useful for breaking up a structured
        set so each strip reads distinctly (see the "tag as randomised" gate).
        """
        import random
        program = self._program_from_grid()
        if len(program) < 2:
            self._status.setText(tr("Need at least two patches to shuffle."))
            return
        random.shuffle(program)
        self._populate_grid(program)
        self._renumber()
        self._status.setText(
            tr("Shuffled {n} patches.").format(n=len(program))
            + " " + tr("Updating preview…"))
        self._schedule_auto_refresh()

    def _remove_selected_patches(self) -> None:
        rows = sorted((self._grid.row(it) for it in self._grid.selectedItems()),
                      reverse=True)
        if not rows:
            self._status.setText(tr("Select one or more patches first."))
            return
        for r in rows:
            self._grid.takeItem(r)
        self._renumber()
        n_removed = len(rows)
        self._status.setText(
            tr("Removed 1 patch.") if n_removed == 1
            else tr("Removed {n} patches.").format(n=n_removed))

    def _program_from_grid(self) -> list[tuple]:
        return [self._grid.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._grid.count())]

    def _export_patch_colours(self) -> None:
        """Save the current patch program as a text file (hex or 0..255 RGB).

        Format mirrors what :func:`workflow.ti2_relayout.parse_color_values`
        accepts so the file round-trips through the New chart dialog's
        "Paste colour values" mode.
        """
        if self._grid.count() == 0:
            self._status.setText(tr("No patches to export."))
            return
        from PyQt6.QtWidgets import QInputDialog
        fmt, ok = QInputDialog.getItem(
            self, tr("Export patch colours"), tr("Format:"),
            ["Hex (#rrggbb)", "RGB 0..255 (R G B)",
             "i1Profiler (.txt + .pxf)"], 0, False,
        )
        if not ok:
            return
        if fmt.startswith("i1Profiler"):
            self._export_i1profiler()
            return
        as_hex = fmt.startswith("Hex")
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        default_name = f"{self._basename or 'chart'}-colours.txt"
        path = save_file_dialog(
            self, "Save patch colours",
            "Text files (*.txt)",
            start_path=str(Path(start) / default_name),
        )
        if not path:
            return
        out_path = Path(path)
        if not out_path.suffix:
            out_path = out_path.with_suffix(".txt")
        lines: list[str] = []
        for r100, g100, b100 in self._program_from_grid():
            r = max(0, min(255, round(r100 / 100 * 255)))
            g = max(0, min(255, round(g100 / 100 * 255)))
            b = max(0, min(255, round(b100 / 100 * 255)))
            lines.append(f"#{r:02x}{g:02x}{b:02x}" if as_hex
                          else f"{r} {g} {b}")
        try:
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, tr("Save failed"), str(exc))
            return
        n_exp = len(lines)
        self._status.setText(
            tr("Exported 1 colour to {name}.").format(name=out_path.name)
            if n_exp == 1 else
            tr("Exported {n} colours to {name}.").format(n=n_exp, name=out_path.name))

    def _export_i1profiler(self) -> None:
        """Export the current patch program as i1Profiler-ready files.

        Writes the same ``<base>.txt`` (CGATS) + ``<base>.pxf`` (CxF3) pair the
        Create Chart tab produces, so the layout the user designed here can be
        handed straight to i1Profiler. RGB only (matching the editor and the
        i1Profiler exporter); the program goes via a temp .ti1 the exporter
        reads.
        """
        import tempfile
        from workflow.i1profiler_import import RgbPatch, write_ti1 as _write_ti1
        from workflow import i1profiler_export as X

        prog = self._program_from_grid()
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        default_name = f"{self._basename or 'chart'}-i1profiler"
        path = save_file_dialog(
            self, "Export i1Profiler files",
            "i1Profiler (*.pxf)",
            start_path=str(Path(start) / default_name),
        )
        if not path:
            return
        out_path = Path(path)
        base = out_path.stem or "i1profiler"
        out_dir = out_path.parent
        try:
            with tempfile.TemporaryDirectory() as td:
                ti1 = Path(td) / f"{base}.ti1"
                _write_ti1([RgbPatch(*rgb) for rgb in prog], ti1)
                txt_out, pxf_out = X.export_from_ti1(
                    ti1, out_dir, base_name=base, descriptor=base)
        except Exception as exc:  # noqa: BLE001 — surface any writer failure
            QMessageBox.warning(self, tr("Save failed"), str(exc))
            return
        name = (f"{txt_out.name} + {pxf_out.name}" if txt_out else pxf_out.name)
        n_exp = len(prog)
        self._status.setText(
            tr("Exported 1 colour to {name}.").format(name=name)
            if n_exp == 1 else
            tr("Exported {n} colours to {name}.").format(n=n_exp, name=name))

    def _set_patch_colour(self) -> None:
        items = self._grid.selectedItems()
        if not items:
            self._status.setText(tr("Select one or more patches first."))
            return
        start = _qcolor(items[0].data(Qt.ItemDataRole.UserRole))
        c = self._pick_color(start, "Patch colour")
        if not c.isValid():
            return
        rgb = _to100(c)
        for it in items:
            it.setData(Qt.ItemDataRole.UserRole, rgb)
            it.setIcon(_swatch_icon(rgb))
        n_set = len(items)
        self._status.setText(
            tr("Set 1 patch.") if n_set == 1
            else tr("Set {n} patches.").format(n=n_set))
        self._schedule_auto_refresh()

    def _transform_selection(self, factor: float) -> None:
        items = self._grid.selectedItems()
        if not items:
            self._status.setText(tr("Select one or more patches first."))
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
        # Entering Spacers mode needs the B&W spacer twin (for the per-spacer
        # masks), which the Patches-mode fast preview skips (#44). Render it now
        # if it's missing; otherwise just redraw (selection outlines only show
        # in Spacers mode).
        if (not patches and self._needs_twin() and self._regen is not None
                and not any(self._regen.bw_tiffs)):
            self._regenerate(save_to=None)
            return
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
            btn.setToolTip(tr("Spacer palette colour #{idx} — click to edit").format(idx=idx))
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
        self._status.setText(tr("Palette changed."))
        self._schedule_auto_refresh()

    def _reset_palette(self) -> None:
        self._palette = None
        self._build_palette_row()
        self._note_edit()
        self._status.setText(tr("Palette reset to default."))

    # -- printtarg-options panel -------------------------------------------
    def _on_printtarg_changed(self, *_a) -> None:
        """Pull the printtarg panel widgets into self._options and schedule
        a re-render. The mutual-exclusion + triple-density preset live in
        _on_dd_toggled / _on_td_toggled (which delegate back here)."""
        if getattr(self, "_pt_syncing", False):
            return
        # Spacer mode — checkbox group: at most one is on. All-off falls
        # through to printtarg's coloured default.
        if self._pt_sp_bw.isChecked():
            mode = "bw"
        elif self._pt_sp_none.isChecked():
            mode = "none"
        else:
            mode = "colored"
        # "None" means no spacers — disable the spacer-scale spinbox so
        # the user can't dial a -A that has nothing to apply to.
        self._pt_A.setEnabled(mode != "none")
        new = R.LayoutOptions(
            spacer_mode=mode,
            patch_scale=self._pt_a.value(),
            spacer_scale=self._pt_A.value(),
            margin_mm=self._pt_m.value(),
            suppress_left_clip=self._pt_L.isChecked(),
            no_strip_limit=self._pt_P.isChecked(),
            double_density=self._pt_dd.isChecked(),
            triple_density=self._pt_td.isChecked(),
            tiff_16bit=self._pt_bd16.isChecked(),
            dpi=self._pt_dpi.value(),
        )
        if new == self._options:
            return
        self._options = new
        self._schedule_auto_refresh()

    def _on_dd_toggled(self, on: bool) -> None:
        if on and self._pt_td.isChecked():
            self._pt_td.setChecked(False)
        self._on_printtarg_changed()

    def _on_force_tag_toggled(self, on: bool) -> None:
        # Affects only the saved .ti2's keyword, not the render — no re-preview.
        self._settings.set("ti2_editor_force_tag", bool(on))

    def _update_force_tag_state(self) -> None:
        """Enable the 'Force randomised tag' checkbox only when the current
        layout is judged unsafe to tag automatically.

        A well-mixed chart is tagged as randomised for us on save, so forcing is
        pointless and the box is greyed out; a structured one is left untagged
        unless the user forces it, so the box is offered. Driven by analysing the
        latest preview .ti2 (see _on_regen_done)."""
        safe = True
        if self._regen is not None:
            safe = R.analyze_randomisation(self._regen.ti2).safe
        self._pt_force_tag.setEnabled(not safe)
        self._pt_force_tag.setToolTip(
            "This chart is already well mixed, so it's tagged as randomised "
            "automatically — nothing to force here."
            if safe else
            "This chart's layout looks structured. Tick to mark it as randomised "
            "anyway (risky — click the ⓘ for details).")

    def _on_td_toggled(self, on: bool) -> None:
        """Triple-density mutual exclusion + preset application — mirrors
        the manual triple-density toggle in tab_chart."""
        if on and self._pt_dd.isChecked():
            self._pt_dd.setChecked(False)
        self._pt_dd.setEnabled(not on)
        # When this fires while syncing a freshly-loaded chart's options, that
        # chart already carries its own -a / -m — applying the TD preset here
        # would clobber them with 1.3 / 5 (Knut's #68 "editor shows 1.30/5"
        # bug, where a 1.04 / 6 triple-density preset came back wrong). Leave
        # the loaded values untouched; the sync sets the TD checkbox last.
        if self._pt_syncing:
            return
        # Block while applying the preset so we coalesce one re-render at
        # the end of this method instead of one per widget edit.
        self._pt_syncing = True
        try:
            if on:
                self._td_stash = {
                    "a": self._pt_a.value(),
                    "m": self._pt_m.value(),
                    "L": self._pt_L.isChecked(),
                    "P": self._pt_P.isChecked(),
                }
                self._pt_a.setValue(1.3)
                self._pt_m.setValue(5)
                self._pt_L.setChecked(True)
                self._pt_P.setChecked(True)
            else:
                stash = getattr(self, "_td_stash", None) or {}
                if "a" in stash:
                    self._pt_a.setValue(stash["a"])
                if "m" in stash:
                    self._pt_m.setValue(stash["m"])
                if "L" in stash:
                    self._pt_L.setChecked(bool(stash["L"]))
                if "P" in stash:
                    self._pt_P.setChecked(bool(stash["P"]))
                self._td_stash = None
        finally:
            self._pt_syncing = False
        self._on_printtarg_changed()

    def _sync_printtarg_widgets(self) -> None:
        """Push self._options + spec into the printtarg-section widgets.

        Called when a new chart is loaded / created so the widgets reflect
        whatever options came with it. Visibility of instrument-conditional
        rows is flipped from the loaded chart's instrument flag.
        """
        self._pt_syncing = True
        try:
            o = self._options
            # Instrument + Paper come from spec; everything else from
            # LayoutOptions.
            if self._spec is not None:
                ix = self._pt_instr.findData(self._spec.instrument_flag)
                if ix >= 0:
                    self._pt_instr.setCurrentIndex(ix)
                if _paper_code_known(self._spec.paper_flag):
                    ix = self._pt_paper.findData(self._spec.paper_flag)
                    if ix >= 0:
                        self._pt_paper.setCurrentIndex(ix)
                    self._pt_paper_custom_row.setVisible(False)
                else:
                    # paper_flag is a "WxH" custom size — point the combo
                    # at "Custom" and seed the W/H spinboxes from paper_mm.
                    ix = self._pt_paper.findData("custom")
                    if ix >= 0:
                        self._pt_paper.setCurrentIndex(ix)
                    w, h = self._spec.paper_mm
                    self._pt_paper_w.setValue(int(round(w)))
                    self._pt_paper_h.setValue(int(round(h)))
                    self._pt_paper_custom_row.setVisible(True)
            self._pt_sp_col.setChecked(o.spacer_mode == "colored")
            self._pt_sp_bw.setChecked(o.spacer_mode == "bw")
            self._pt_sp_none.setChecked(o.spacer_mode == "none")
            self._pt_A.setEnabled(o.spacer_mode != "none")
            self._pt_a.setValue(o.patch_scale)
            self._pt_A.setValue(o.spacer_scale)
            self._pt_m.setValue(o.margin_mm)
            self._pt_dpi.setValue(o.dpi)
            self._pt_bd8.setChecked(not o.tiff_16bit)
            self._pt_bd16.setChecked(o.tiff_16bit)
            self._pt_L.setChecked(o.suppress_left_clip)
            self._pt_P.setChecked(o.no_strip_limit)
            self._pt_dd.setChecked(o.double_density)
            self._pt_td.setChecked(o.triple_density)
            self._pt_dd.setEnabled(not o.triple_density)
            self._refresh_pt_instr_visibility()
        finally:
            self._pt_syncing = False

    def _refresh_pt_instr_visibility(self) -> None:
        """Show/hide -L / -P / -h / triple-density rows based on the
        currently-selected instrument — same rule as the New chart dialog."""
        code = self._pt_instr.currentData()
        is_strip = code in _STRIP_INSTRUMENTS
        is_cm = code == "CM"
        self._pt_L.setVisible(is_strip)
        self._pt_P.setVisible(is_strip)
        self._pt_dd.setVisible(is_cm)
        self._pt_td.setVisible(is_cm)
        # Hidden controls reset to off so they can't leak into the
        # printtarg flag list after an instrument switch. We block their
        # toggled signals here so the per-widget callbacks don't each
        # kick off a re-render — _on_pt_instr_changed schedules one at
        # the end.
        for w, on in ((self._pt_L, is_strip), (self._pt_P, is_strip),
                       (self._pt_dd, is_cm), (self._pt_td, is_cm)):
            if not on and w.isChecked():
                w.blockSignals(True)
                w.setChecked(False)
                w.blockSignals(False)

    def _on_pt_instr_changed(self) -> None:
        """User flipped the instrument combo. Visibility always follows
        the combo (so the conditional rows track the user's pick even
        before a chart is loaded). Spec + re-render only fire when a
        chart is open."""
        if getattr(self, "_pt_syncing", False):
            return
        self._refresh_pt_instr_visibility()
        if self._spec is None:
            return
        code = self._pt_instr.currentData()
        if code is None or code == self._spec.instrument_flag:
            return
        self._spec.instrument_flag = code
        self._on_printtarg_changed()
        self._refresh_info()   # keep the -i/-p readout in step (Knut)

    def _on_pt_paper_changed(self) -> None:
        """User flipped the paper combo — update spec.paper_flag + paper_mm
        + re-render. The paper change rewires printtarg's -p so we need a
        full regenerate, not just a redraw. "Custom" reveals W/H spinboxes
        and the actual paper_flag comes from those (see
        :meth:`_on_pt_paper_custom_changed`)."""
        is_custom = self._pt_paper.currentData() == "custom"
        self._pt_paper_custom_row.setVisible(is_custom)
        if getattr(self, "_pt_syncing", False) or self._spec is None:
            return
        if is_custom:
            self._on_pt_paper_custom_changed()
            return
        code = self._pt_paper.currentData()
        if code is None or code == self._spec.paper_flag:
            return
        self._spec.paper_flag = code
        # Update paper_mm too, looking up the named-papers reverse map.
        from workflow.ti2_relayout import _NAMED_PAPERS
        inv = {v: k for k, v in _NAMED_PAPERS.items()}
        self._spec.paper_mm = inv.get(code, self._spec.paper_mm)
        self._schedule_auto_refresh()
        self._refresh_info()   # keep the -i/-p readout in step (Knut)

    def _on_pt_paper_custom_changed(self) -> None:
        """Apply the W/H spinbox values to spec.paper_flag + paper_mm and
        schedule a re-render. Only meaningful when the paper combo is set
        to "Custom"."""
        if getattr(self, "_pt_syncing", False) or self._spec is None:
            return
        if self._pt_paper.currentData() != "custom":
            return
        w, h = self._pt_paper_w.value(), self._pt_paper_h.value()
        flag = f"{w}x{h}"
        if flag == self._spec.paper_flag:
            return
        self._spec.paper_flag = flag
        self._spec.paper_mm = (float(w), float(h))
        self._schedule_auto_refresh()
        self._refresh_info()   # keep the -i/-p readout in step (Knut)


    # -- regeneration / preview --------------------------------------------
    def _regenerate(self, save_to: Path | None) -> None:
        if self._spec is None or self._grid.count() == 0:
            self._status.setText(tr("Load or create a chart first."))
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
        self._status.setText(tr("Rendering with printtarg…"))
        # Fast preview (#44): low DPI, and only render the B&W spacer twin when
        # we're actually in Spacers mode (it exists only for spacer selection).
        full_dpi = self._options.dpi if self._options else 300
        self._worker = _RegenWorker(
            self._spec, self._program_from_grid(), out_dir, self._bin_dir,
            tuple(self._palette) if self._palette else None,
            options=self._options, basename=self._basename,
            with_twin=self._needs_twin(),
            dpi_override=min(full_dpi, _PREVIEW_DPI))
        self._worker.done.connect(self._on_regen_done)
        self._worker.start()

    def _needs_twin(self) -> bool:
        """Whether to render the B&W twin (a second printtarg run, #44).

        Needed when something requires the pixel masks it provides:
          • "Highlight selected in preview" is on — patch geometry (the outline
            overlay + click-a-patch-to-select) is derived from the twin diff and
            is empty without it (would otherwise silently do nothing — #48 note);
          • Spacers mode — the per-spacer selection masks;
          • painted spacers — so those colours still show in the preview.
        The common Patches-mode browse (highlight off, unpainted) skips it — the
        big speed win."""
        if self._hl_patches.isChecked():
            return True
        if self._options is None or self._options.spacer_mode == "none":
            return bool(self._paint)
        return self._mode_spacers.isChecked() or bool(self._paint)

    def _on_regen_done(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr("Render failed"), str(result))
            self._status.setText(tr("Render failed."))
            return
        self._regen = result
        # New render → previous per-page spacer segmentations + patch geometry
        # are stale. Both are now recomputed lazily for the visited page (#44).
        self._spacer_cache.clear()
        self._patch_geom_cache.clear()
        # Authoritative per-page strip count from the regenerated .ti2.
        self._strips_per_page = parse_passes_per_page(result.ti2)
        if self._page >= len(result.tiffs):
            self._page = 0
        self._show_page(self._page)
        # Refresh the 'Force randomised tag' affordance for this fresh layout.
        self._update_force_tag_state()
        if self._preview_pending_save is not None:
            self._status.setText(
                tr("Saved to {path}").format(path=self._preview_pending_save))
        else:
            pages = len(result.tiffs)
            extra = (tr(" across {pages} pages").format(pages=pages)
                     if pages > 1 else "")
            self._status.setText(
                tr("{n} spacers on this page{extra}.").format(
                    n=len(self._spacers), extra=extra)
                + " " + tr("Spacers mode → click a spacer on the page preview "
                           "to select it, then “Paint…”."))

    def _show_page(self, page: int) -> None:
        """Switch the preview to ``page``: detect its spacers (cached), redraw."""
        if self._regen is None:
            return
        n = len(self._regen.tiffs)
        self._page = max(0, min(page, n - 1))
        self._spacers = self._spacers_for_page(self._page)
        self._sel_spacers.clear()
        self._update_page_nav()
        self._apply_paint_and_show()

    def _spacers_for_page(self, page: int) -> list:
        """Spacer list for ``page`` — computed once and cached.

        The twin-diff + segmentation is the slow part of switching pages, so we
        memoise it per page. Cache is cleared on every fresh regen
        (:meth:`_on_regen_done`)."""
        cached = self._spacer_cache.get(page)
        if cached is not None:
            return cached
        spacers: list = []
        try:
            tif = self._regen.tiffs[page]
            bw  = self._regen.bw_tiffs[page]
            if bw is None:
                # No twin page for this deliverable page (page-break skew) —
                # render it without per-spacer selection rather than erroring.
                raise ValueError("no B&W twin for this page")
            mask = R.spacer_mask(tif, bw)
            ref_arr = R._imread_rgb(tif)
            # Authoritative split by strip count from PASSES_IN_STRIPS2 —
            # this handles the case where two adjacent strips happened to
            # pick the same spacer colour, which colour-jump detection
            # alone can't separate.
            strip_xs = self._compute_strip_xs(ref_arr, page)
            spacers = R.segment_spacers(
                mask, page=page, ref_arr=ref_arr, strip_xs=strip_xs)
        except Exception:
            spacers = []
        self._spacer_cache[page] = spacers
        return spacers

    def _compute_strip_xs(self, ref_arr, page: int) -> list[int] | None:
        """Return the inter-strip x-boundaries on ``page``, or None.

        Uses the page's strip count from PASSES_IN_STRIPS2 (parsed in
        :meth:`_on_regen_done`) and the patch-grid bbox to divide the block
        into equal-width strip cells.
        """
        if (page >= len(self._strips_per_page)
                or self._strips_per_page[page] <= 1):
            return None
        bbox = R._patch_grid_bbox(ref_arr)
        if bbox is None:
            return None
        y0, y1, x0, x1 = bbox
        n = self._strips_per_page[page]
        col_w = (x1 - x0 + 1) / n
        return [int(x0 + i * col_w) for i in range(1, n)]

    def _update_page_nav(self) -> None:
        n = len(self._regen.tiffs) if self._regen else 0
        self._page_bar.setVisible(n > 1)
        if n > 1:
            self._page_label.setText(
                tr("Page {page}/{total}").format(page=self._page + 1, total=n))
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
        """Scale + compose the cached full-res pixmap onto a white canvas
        with a 15-px margin, the same canvas-with-white-border pattern
        ui.tiff_preview uses (see ``_repaint_label``). Painting the margin
        INTO the pixmap (instead of via QLabel QSS) sidesteps the
        sizeHint feedback loop that grows the label every refresh."""
        if self._full_pixmap is None:
            return
        dpr = self._preview.devicePixelRatioF() or 1.0
        lw, lh = self._preview.width(), self._preview.height()
        if lw <= 0 or lh <= 0:
            return
        B = 15  # white display border, all sides (logical px)
        avail = QSize(
            max(1, int((lw - 2 * B) * dpr)),
            max(1, int((lh - 2 * B) * dpr)),
        )
        scaled = self._full_pixmap.scaled(
            avail,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        canvas = QPixmap(scaled.width() + int(2 * B * dpr),
                         scaled.height() + int(2 * B * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.GlobalColor.white)
        p = QPainter(canvas)
        p.drawPixmap(B, B, scaled)
        p.end()
        # Logical scale (label coords → image px) — divides out the dpr.
        logical_w = scaled.width() / dpr
        self._preview_scale = (logical_w / self._full_pixmap.width()
                               if self._full_pixmap.width() else 1.0)
        # Origin of the patch raster inside the canvas, in label-logical
        # coords (used by _label_to_image when mapping clicks).
        self._preview_border = B
        self._preview_orig = self._full_pixmap.size()
        self._base_pixmap = canvas
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Redraw the preview from the cached base pixmap, overlaying yellow
        outlines on the currently-selected spacers (Spacers mode) or patches
        (Patches mode + Highlight toggle on). Hands the composited pixmap to
        the preview label so the marquee can repaint on top of it during
        drag.

        Overlay coordinates are shifted by ``_preview_border`` (the white
        margin baked into the canvas by :meth:`_rescale_preview`) so the
        rects align with the chart pixels inside the border.
        """
        if self._base_pixmap is None:
            return
        pm = QPixmap(self._base_pixmap)
        B = getattr(self, "_preview_border", 0)
        dpr = pm.devicePixelRatio() or 1.0
        # Painter coords are logical, but the canvas was created at device
        # pixels with DPR baked in — so logical scale = self._preview_scale
        # and the border offset is just B (logical px).
        if (self._mode_spacers.isChecked()
                and self._sel_spacers and self._spacers):
            # Fill + outline (à la ui.scan_highlighter) — a translucent yellow
            # wash makes thin bars visible at a glance, the 2px outline keeps
            # the boundary crisp.
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QColor(255, 69, 115, 120))
            p.setPen(QPen(QColor(SPEC_MAGENTA), 2))
            s = self._preview_scale
            for i in self._sel_spacers:
                if 0 <= i < len(self._spacers):
                    x0, y0, x1, y1 = self._spacers[i].bbox
                    p.drawRect(int(x0 * s) + B - 1, int(y0 * s) + B - 1,
                               int((x1 - x0 + 1) * s) + 2,
                               int((y1 - y0 + 1) * s) + 2)
            p.end()
        elif (self._mode_patches.isChecked()
                and self._hl_patches.isChecked()
                and self._regen is not None):
            geom = self._patch_geom_for_page(self._page)
            sel = {self._grid.row(it) + 1 for it in self._grid.selectedItems()}
            if sel and geom:
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.setBrush(QColor(255, 69, 115, 120))
                p.setPen(QPen(QColor(SPEC_MAGENTA), 2))
                s = self._preview_scale
                for sid in sel:
                    box = geom.get(sid)
                    if box is None:
                        continue
                    x0, y0, x1, y1 = box
                    p.drawRect(int(x0 * s) + B - 1, int(y0 * s) + B - 1,
                               int((x1 - x0 + 1) * s) + 2,
                               int((y1 - y0 + 1) * s) + 2)
                p.end()
        self._preview.set_base_pixmap(pm)

    def _on_patch_highlight_toggled(self, on: bool) -> None:
        # Turning highlight on needs the B&W twin for patch geometry, which the
        # fast Patches-mode preview skips (#44); render it now if it's missing.
        # Otherwise just redraw to show / clear the overlay.
        if (on and self._regen is not None and not any(self._regen.bw_tiffs)):
            self._regenerate(save_to=None)
            return
        self._refresh_preview()

    def _on_grid_selection_changed(self) -> None:
        # Cheap no-op unless we're showing the patch overlay; otherwise just
        # repaint with the new selection.
        if (self._mode_patches.isChecked()
                and self._hl_patches.isChecked()
                and self._regen is not None):
            self._refresh_preview()

    def _patch_geom_for_page(self, page: int) -> dict:
        """Patch sample-id → pixel bbox for a page, computed once and cached.

        Lazy (visited page only) so a fresh render doesn't scan every page up
        front (#44). The B&W twin refines the y-bands when present; without it
        (Patches-mode fast preview) geometry falls back to the coarser bbox,
        which patch_geometry_for_page tolerates."""
        if self._regen is None or page >= len(self._regen.tiffs):
            return {}
        cached = self._patch_geom_cache.get(page)
        if cached is not None:
            return cached
        bw = (self._regen.bw_tiffs[page]
              if page < len(self._regen.bw_tiffs) else None)
        geom = R.patch_geometry_for_page(
            self._regen.ti2, self._regen.tiffs[page], page, bw_tif_path=bw)
        self._patch_geom_cache[page] = geom
        return geom

    def _patch_at(self, ix: float, iy: float) -> int | None:
        """Return the SAMPLE_ID under (ix, iy) on the current page, or None."""
        for sid, (x0, y0, x1, y1) in self._patch_geom_for_page(self._page).items():
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                return sid
        return None

    def _select_patches_by_ids(
        self, sids: list[int], *, extend: bool, remove: bool = False,
    ) -> None:
        """Select grid rows for the given SAMPLE_IDs (1-based; row = id - 1).

        ``extend`` keeps the existing selection (Ctrl-style additive). ``remove``
        removes the given ids from the selection (Alt-style subtractive).
        """
        if not extend and not remove:
            self._grid.clearSelection()
        for sid in sids:
            row = sid - 1
            if 0 <= row < self._grid.count():
                self._grid.item(row).setSelected(not remove)

    def _label_to_image(self, p: QPoint) -> tuple[float, float] | None:
        """Map a label-coord click to deliverable image pixels (or None).

        ``_base_pixmap`` is DPR-aware; its ``width()`` is physical, so we
        divide by DPR to compare against the label's logical width. The
        white border baked into the canvas (``_preview_border``) is also
        subtracted before scaling back to original-image coordinates.
        """
        if self._base_pixmap is None or self._preview_scale <= 0:
            return None
        dpr = self._base_pixmap.devicePixelRatio() or 1.0
        pm_logical_w = self._base_pixmap.width() / dpr
        pm_logical_h = self._base_pixmap.height() / dpr
        off_x = (self._preview.width() - pm_logical_w) / 2
        off_y = (self._preview.height() - pm_logical_h) / 2
        B = getattr(self, "_preview_border", 0)
        return ((p.x() - off_x - B) / self._preview_scale,
                (p.y() - off_y - B) / self._preview_scale)

    def _on_marquee(self, rect, mods) -> None:
        """Marquee selection — same semantics as :meth:`_on_preview_click`.

            * plain marquee  → replace (everything hit by the rectangle)
            * Shift+marquee  → add hits to existing selection
            * Alt+marquee    → remove hits from selection
            * marquee hitting nothing + no modifiers → clear
        """
        tl = self._label_to_image(rect.topLeft())
        br = self._label_to_image(rect.bottomRight())
        if tl is None or br is None:
            return
        ix0, iy0 = tl
        ix1, iy1 = br
        is_alt   = bool(mods & Qt.KeyboardModifier.AltModifier)
        is_shift = bool(mods & (Qt.KeyboardModifier.ShiftModifier
                                | Qt.KeyboardModifier.ControlModifier
                                | Qt.KeyboardModifier.MetaModifier))

        if self._mode_spacers.isChecked():
            if not self._spacers:
                return
            touched = [
                i for i, sp in enumerate(self._spacers)
                if not (sp.bbox[2] < ix0 or sp.bbox[0] > ix1
                        or sp.bbox[3] < iy0 or sp.bbox[1] > iy1)
            ]
            if is_alt:
                self._sel_spacers.difference_update(touched)
            elif is_shift:
                self._sel_spacers.update(touched)
            else:
                # Empty marquee clears (no modifiers means a replace, so
                # selecting nothing should mean "clear").
                self._sel_spacers = set(touched)
            self._refresh_preview()
            n_sp = len(self._sel_spacers)
            self._status.setText(
                tr("1 spacer selected.") if n_sp == 1
                else tr("{n} spacers selected.").format(n=n_sp))
            return

        # Patches mode (+ highlight): marquee picks patches into the grid.
        if not (self._hl_patches.isChecked() and self._regen is not None):
            return
        geom = self._patch_geom_for_page(self._page)
        if not geom:
            return
        touched_p = [
            sid for sid, (x0, y0, x1, y1) in geom.items()
            if not (x1 < ix0 or x0 > ix1 or y1 < iy0 or y0 > iy1)
        ]
        if is_alt:
            if touched_p:
                self._select_patches_by_ids(touched_p, extend=True, remove=True)
        elif is_shift:
            if touched_p:
                self._select_patches_by_ids(touched_p, extend=True)
        else:
            # Plain marquee replaces — clear first so an empty marquee is a
            # clear, and a non-empty one becomes exactly the touched set.
            self._grid.clearSelection()
            if touched_p:
                self._select_patches_by_ids(touched_p, extend=True)
        if touched_p and not is_alt:
            row = min(touched_p) - 1
            if 0 <= row < self._grid.count():
                self._grid.scrollToItem(self._grid.item(row))
        n_pat = len(self._grid.selectedItems())
        self._status.setText(
            tr("1 patch selected.") if n_pat == 1
            else tr("{n} patches selected.").format(n=n_pat))

    def _clear_spacer_selection(self) -> None:
        if not self._sel_spacers:
            return
        self._sel_spacers.clear()
        self._refresh_preview()
        self._status.setText(tr("Spacer selection cleared."))

    def _on_preview_click(self, pos: QPoint, mods) -> None:
        """Click in the preview — standard select semantics for both modes.

            * plain click on a target  → replace selection (clear + add hit)
            * plain click on empty     → clear selection
            * Shift+click              → add hit
            * Alt+click                → remove hit
        """
        mapped = self._label_to_image(pos)
        if mapped is None:
            return
        ix, iy = mapped
        is_alt   = bool(mods & Qt.KeyboardModifier.AltModifier)
        is_shift = bool(mods & (Qt.KeyboardModifier.ShiftModifier
                                | Qt.KeyboardModifier.ControlModifier
                                | Qt.KeyboardModifier.MetaModifier))

        if self._mode_spacers.isChecked():
            if not self._spacers:
                return
            hit = self._spacer_at(ix, iy)
            if hit is None:
                # Empty area + no modifiers clears; with modifiers it's a
                # no-op (a missed Shift-click shouldn't drop the selection).
                if not (is_alt or is_shift):
                    self._sel_spacers.clear()
                    self._refresh_preview()
                    self._status.setText(tr("Spacer selection cleared."))
                return
            if is_alt:
                self._sel_spacers.discard(hit)
            elif is_shift:
                self._sel_spacers.add(hit)
            else:
                self._sel_spacers = {hit}
            self._refresh_preview()
            n_sp = len(self._sel_spacers)
            self._status.setText(
                tr("1 spacer selected.") if n_sp == 1
                else tr("{n} spacers selected.").format(n=n_sp))
            return

        # Patches mode (+ highlight): click maps to the grid selection.
        if not (self._hl_patches.isChecked() and self._regen is not None):
            return
        sid = self._patch_at(ix, iy)
        if sid is None:
            if not (is_alt or is_shift):
                self._grid.clearSelection()
                self._status.setText(tr("Patch selection cleared."))
            return
        row = sid - 1
        if not (0 <= row < self._grid.count()):
            return
        if is_alt:
            self._select_patches_by_ids([sid], extend=True, remove=True)
        elif is_shift:
            self._select_patches_by_ids([sid], extend=True)
        else:
            self._select_patches_by_ids([sid], extend=False)
        if not is_alt:
            self._grid.scrollToItem(self._grid.item(row))
        n_pat = len(self._grid.selectedItems())
        self._status.setText(
            tr("1 patch selected.") if n_pat == 1
            else tr("{n} patches selected.").format(n=n_pat))

    def _spacer_at(self, ix: float, iy: float) -> int | None:
        for i, sp in enumerate(self._spacers):
            x0, y0, x1, y1 = sp.bbox
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                return i
        return None

    def _paint_spacers(self) -> None:
        if not self._sel_spacers:
            self._status.setText(tr("Click spacers in the preview to select them first."))
            return
        c = self._pick_color(QColor(128, 128, 128), "Spacer colour")
        if not c.isValid():
            return
        rgb = (c.red(), c.green(), c.blue())
        for i in self._sel_spacers:
            self._paint[(self._page, i)] = rgb
        self._apply_paint_and_show()
        self._note_edit()
        n_painted = len(self._sel_spacers)
        self._status.setText(
            tr("Painted 1 spacer on page {page}.").format(page=self._page + 1)
            if n_painted == 1 else
            tr("Painted {n} spacers on page {page}.").format(
                n=n_painted, page=self._page + 1))

    # -- save ---------------------------------------------------------------
    def _save_as(self) -> None:
        if self._spec is None or self._grid.count() == 0:
            return
        res = self._prompt_save_as_name()
        if res is None:
            return
        name, location = res
        target = Path(location) / name
        try:
            msg = self._write_chart_into(target, name)
        except Exception as exc:  # noqa: BLE001 — surface any writer failure
            QMessageBox.warning(self, tr("Save failed"), str(exc))
            return
        QMessageBox.information(self, tr("Saved"), msg)
        self._status.setText(msg.splitlines()[0])
        self._mark_saved()   # Save As clears the unsaved-changes flag (#49)

    def _prompt_save_as_name(self) -> "tuple[str, str] | None":
        """Custom Save-As prompt: a locked descriptive-prefix name field + the
        "Add a descriptive prefix" checkbox (same as Save & apply, #68) plus a
        location row. Returns ``(name, parent_dir)`` or None if cancelled. The
        chart is written as ``<parent_dir>/<name>/<name>.*``."""
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Save chart as…"))
        dlg.setMinimumWidth(580)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        heading = QLabel(tr("Save this chart to a folder"), dlg)
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(heading)
        lay.addWidget(QLabel(
            tr("The name becomes both the folder and the chart's file names."), dlg))

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("Chart name:"), dlg))
        name_edit = PrefixLockedLineEdit(dlg)
        name_row.addWidget(name_edit, 1)
        lay.addLayout(name_row)
        prefix_cb = QCheckBox(tr("Add a descriptive prefix"), dlg)
        prefix_cb.setChecked(bool(self._settings.get("create_chart_auto_suffix", True)))
        prefix_cb.toggled.connect(
            lambda on: _toggle_locked_prefix(name_edit, on, self._dialog_name_prefix()))
        lay.addWidget(prefix_cb)
        self._seed_save_name(name_edit, prefix_cb.isChecked())

        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel(tr("Location:"), dlg))
        start = (self._settings.get("custom_output_path", "")
                 or str(Path.home() / "ChromIQ"))
        loc_edit = QLineEdit(start, dlg)
        loc_row.addWidget(loc_edit, 1)
        browse = QPushButton(tr("Browse…"), dlg)
        browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        browse.clicked.connect(lambda: (
            (lambda d: loc_edit.setText(d) if d else None)(
                open_dir_dialog(self, tr("Choose a folder"),
                                start_dir=loc_edit.text() or start))))
        loc_row.addWidget(browse)
        lay.addLayout(loc_row)

        bb = QDialogButtonBox(dlg)
        ok_btn = bb.addButton(tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = bb.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        import re
        raw = name_edit.text().strip()
        clean = re.sub(r"\s+", "-", raw)
        clean = re.sub(r"[^\w\-.]", "_", clean).strip("._-") or "chart"
        location = loc_edit.text().strip() or start
        return clean, location

    def _write_chart_into(self, target: Path, name: str) -> str:
        """Write the complete chart deliverable into ``target`` and return a note.

        Shared by "Save As" and "Save & apply". Produces the same set of files a
        Create Chart build leaves in a run folder, plus the editor extras:
        ``<name>.ti1`` / ``.ti2`` / ``<name>_NN.tif`` (+ B&W spacer twin),
        ``meta.json`` (full layout), ``<name>-i1profiler.txt/.pxf`` and
        ``<name>-colours.txt`` (the colour list the old Export button wrote).
        Raises on a hard failure (regenerate / integrity); the extras are
        best-effort and never abort the save.
        """
        target.mkdir(parents=True, exist_ok=True)
        # regenerate straight into the target, then bake per-spacer paint into pages
        res = R.regenerate(self._spec, self._program_from_grid(), target,
                           self._bin_dir,
                           spacer_palette=tuple(self._palette) if self._palette else None,
                           basename=name, options=self._options)
        pad = R.assert_data_integrity(self._program_from_grid(), res.ti2)
        self._bake_paint_into_saved(res)
        # Write meta.json (the same RunMeta the main app uses) into the chart
        # folder so reopening restores the printtarg knobs exactly as saved, and
        # the folder reads like a main-app chart.
        R.save_editor_meta(res.ti2, self._spec, self._options, name,
                           recipe=self._chart_recipe)
        # Colour list (<name>-colours.txt) — what the old Export button wrote, so
        # the design can be pasted back into the New chart dialog later.
        colour_note = ""
        try:
            cpath = target / f"{name}-colours.txt"
            self._write_colour_values_file(cpath)
            colour_note = f"Colour list: {cpath.name}"
        except OSError as exc:
            log.warning("colour-list export during save failed: %s", exc)
        # i1Profiler-ready pair (<name>-i1profiler.txt/.pxf) so the saved chart
        # can be handed straight to i1Profiler (best-effort).
        i1_note = ""
        try:
            from workflow import i1profiler_export as X
            ti1 = res.ti2.with_suffix(".ti1")
            if ti1.exists():
                _txt, pxf = X.export_from_ti1(
                    ti1, target, base_name=f"{name}-i1profiler", descriptor=name)
                i1_note = f"i1Profiler files: {pxf.stem}.txt/.pxf"
        except Exception as exc:  # noqa: BLE001
            log.warning("i1Profiler export during save failed: %s", exc)
        tag_note = self._maybe_tag_randomised(res.ti2)
        msg = f"Saved {res.ti2.name} + {len(res.tiffs)} page(s) to {target}"
        if colour_note:
            msg += f"\n{colour_note}"
        if i1_note:
            msg += f"\n{i1_note}"
        if pad:
            msg += f"\nprinttarg added {pad} patch(es) to complete the last strip."
        if tag_note:
            msg += "\n" + tag_note
        return msg

    def _write_colour_values_file(self, path: Path, as_hex: bool = True) -> None:
        """Write the current patch program as a colour list (hex by default).

        Same format :func:`workflow.ti2_relayout.parse_color_values` accepts, so
        the file round-trips through the New chart dialog's "Paste colour values".
        """
        lines: list[str] = []
        for r100, g100, b100 in self._program_from_grid():
            r = max(0, min(255, round(r100 / 100 * 255)))
            g = max(0, min(255, round(g100 / 100 * 255)))
            b = max(0, min(255, round(b100 / 100 * 255)))
            lines.append(f"#{r:02x}{g:02x}{b:02x}" if as_hex else f"{r} {g} {b}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _save_and_apply(self) -> None:
        """The "Apply / Save" action (#70, Knut's model).

        Offers three choices: **Overwrite** the chart currently loaded in the
        Create Chart tab with this layout (the profile name there is *not*
        changed); **Save As** to export the full deliverable to a folder you
        pick; or **Cancel** back to the editor. Falls back to a plain Save As
        when opened without a host to apply into.
        """
        if self._spec is None or self._grid.count() == 0:
            return
        if self._on_apply is None:
            # No host to apply into (e.g. dialog opened standalone) — save only.
            self._save_as()
            return
        action = self._prompt_apply_action()
        if action == "cancel":
            return                       # Cancel → back to the editor
        if action == "saveas":
            self._save_as()              # export to a folder, editor stays open
            return
        # Overwrite: the layout's own descriptive name is only the staging file
        # stem; the host imports it into the *current* Create Chart profile under
        # that profile's name, leaving the profile name untouched.
        name = self._default_apply_name()
        import tempfile
        staging = Path(tempfile.mkdtemp(prefix="chromiq_apply_"))
        try:
            self._write_chart_into(staging, name)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("Could not prepare chart"), str(exc))
            return
        self._status.setText(
            tr("Applying this chart to the Create Chart tab…"))
        try:
            applied = self._on_apply(staging, name)
        except Exception as exc:  # noqa: BLE001
            log.exception("apply callback failed")
            QMessageBox.warning(self, tr("Could not apply chart"), str(exc))
            return
        # The host returns False when the user backed out of a prompt — keep the
        # editor open so they can try again.
        if applied is False:
            self._status.setText(tr("Apply cancelled — the editor is still open."))
            return
        self._mark_saved()   # applied + saved — closing must not warn (#49)
        self._clear_undo_history()
        self.accept()

    def _suggest_chart_name(self) -> str:
        """A descriptive default name from the chart's printtarg settings, e.g.
        ``i1Pro-A4-480p-2pages-Landscape`` (#62). Used when no target name was
        carried into the editor. Tokens are kept as fixed ASCII identifiers (not
        translated) so the suggested name is a stable, filename-safe handle."""
        if self._spec is None:
            return "chart"
        instr = {"i1": "i1Pro", "CM": "ColorMunki", "3p": "i1Pro3Plus",
                 "SS": "SpectroScan"}.get(self._spec.instrument_flag,
                                          self._spec.instrument_flag or "chart")
        # Named papers contribute their NAME (e.g. "A3+"), not their millimetre
        # code ("483x329"); only a truly custom size falls back to the W×H code
        # (#68, Knut). PAPER_LABELS keys both named sizes and the WxH aliases for
        # named ones, so a known code resolves to its short label here.
        paper = paper_name_token(self._spec.paper_flag or "paper")
        parts = [instr, paper, f"{self._grid.count()}p"]
        pages = len(self._regen.tiffs) if self._regen is not None else 0
        if pages:
            parts.append("1page" if pages == 1 else f"{pages}pages")
        w, h = self._spec.paper_mm
        if w and h:
            parts.append("Landscape" if w > h else "Portrait")
        return "-".join(parts)

    def _dialog_name_prefix(self) -> str:
        """The locked descriptive prefix for the Save dialogs — the suggested
        name, or empty when no real chart is loaded (so the bare ``chart``
        placeholder never becomes a prefix)."""
        return self._suggest_chart_name() if self._spec is not None else ""

    def _seed_save_name(self, name_edit: "PrefixLockedLineEdit", on: bool) -> None:
        """Initial contents of a Save dialog's name field (#68, Knut's model).
        ON shows the locked suggested name + ``-`` with an empty editable tail
        (cursor ready to type); OFF shows the suggested name as a plain, fully
        editable field (no dash, no lock)."""
        _toggle_locked_prefix(name_edit, on, self._dialog_name_prefix())

    def _default_apply_name(self) -> str:
        """The pre-filled Save & apply name: the target name carried through the
        editor when there is one, else a suggestion from the settings (#62)."""
        base = (self._basename or "").strip()
        if base and base != "chart":
            return base
        return self._suggest_chart_name()

    def _prompt_apply_action(self) -> str:
        """The "Apply / Save" window (#70, Knut's model).

        Returns ``"overwrite"`` (replace the chart currently loaded in the Create
        Chart tab with this layout), ``"saveas"`` (export the full chart to a
        folder you pick), or ``"cancel"`` (back to the editor). No name is asked
        for — the profile name lives in the Create Chart tab and is never changed
        from here.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Apply or save this chart layout"))
        dlg.setMinimumWidth(580)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)

        heading = QLabel(
            tr("What would you like to do with this chart layout?"), dlg)
        heading.setWordWrap(True)
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(heading)

        body = QLabel(
            tr("  •  Overwrite — replace the chart currently loaded in the "
               "Create Chart tab with this layout. The patch recipe and page "
               "layout there are updated and locked; your printer profile name "
               "and any measurements you've already taken are kept.\n"
               "  •  Save As — export the full chart (the patch list, the "
               "layout and the printable pages, plus the i1Profiler files and a "
               "colour list) to a folder you pick, without leaving the editor.\n"
               "  •  Cancel — go back to the editor and change nothing."), dlg)
        body.setWordWrap(True)
        lay.addWidget(body)

        bb = QDialogButtonBox(dlg)
        overwrite_btn = bb.addButton(tr("Overwrite"),
                                     QDialogButtonBox.ButtonRole.AcceptRole)
        saveas_btn = bb.addButton(tr("Save As…"),
                                  QDialogButtonBox.ButtonRole.ActionRole)
        cancel_btn = bb.addButton(tr("Cancel"),
                                  QDialogButtonBox.ButtonRole.RejectRole)
        overwrite_btn.setDefault(True)
        lay.addWidget(bb)

        result = {"v": "cancel"}
        overwrite_btn.clicked.connect(lambda: (result.__setitem__("v", "overwrite"), dlg.accept()))
        saveas_btn.clicked.connect(lambda: (result.__setitem__("v", "saveas"), dlg.accept()))
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()
        return result["v"]

    def _maybe_tag_randomised(self, ti2: Path) -> str:
        """Decide how the just-saved .ti2 is tagged as randomised, and do it.

        - Well-mixed layout → tagged automatically (CHART_ID → RANDOM_START).
        - Structured layout + 'Force' ticked → tagged after a risk warning the
          user can suppress ('never show again'), or silently if suppressed.
        - Structured layout + 'Force' unticked → left untagged.

        Returns a short note for the save confirmation (empty if nothing to say).
        """
        report = R.analyze_randomisation(ti2)
        if report.safe:
            R.tag_ti2_randomised(ti2)
            return "Tagged as randomised — bidirectional measuring is available."
        if not self._pt_force_tag.isChecked():
            return ("Left untagged — the layout looks structured, so it will be "
                    "measured one direction only. Tick “Force randomised tag” to "
                    "override.")
        if not self._confirm_force_tag(report):
            return ("Left untagged — the layout looks structured, so it will be "
                    "measured one direction only.")
        R.tag_ti2_randomised(ti2)
        return ("Tagged as randomised (forced) — take care: this layout may be "
                "read unreliably (see the warning).")

    def _confirm_force_tag(self, report: "R.RandomisationReport") -> bool:
        """Risk warning shown when the user forces the tag on an unsafe layout.

        Returns True to go ahead and tag. Honours a 'never show this again'
        preference, after which forcing tags without prompting."""
        if bool(self._settings.get("ti2_editor_force_tag_hide_warning", False)):
            return True

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Force “randomised” tag?"))
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)

        heading = QLabel(
            tr("You're about to mark a structured chart as randomised. Please read "
            "this first."), dlg)
        heading.setWordWrap(True)
        heading.setStyleSheet("font-weight: 600;")
        lay.addWidget(heading)

        body = QLabel(
            tr("When you measure a chart, your instrument has to work out which strip "
               "it's looking at and which way round you scanned it. It does that by "
               "matching the colours it reads against what it expects — and that only "
               "works reliably when the colours are well shuffled, so every strip has "
               "its own distinctive look.\n\n"
               "ChromIQ checked this chart and it looks structured instead: "
               "{reason} On a layout like this "
               "the strips can look alike, so the instrument may lock onto the wrong "
               "strip or read it backwards. The frustrating part is that you usually "
               "get no error at all — just measurements that are quietly wrong, which "
               "then build a profile with colour casts.\n\n"
               "Marking it as randomised anyway tells your instrument it's free to "
               "read strips in either direction, which is exactly where this goes "
               "wrong. It's only a sensible choice if you happen to know the order is "
               "genuinely well mixed despite how it looks.\n\n"
               "The safer alternatives: leave it untagged and simply scan every strip "
               "the same way, or rebuild the chart so its colours are shuffled.\n\n"
               "Would you like to mark it as randomised anyway?").format(
                reason=report.reason[0].lower() + report.reason[1:]), dlg)
        body.setWordWrap(True)
        lay.addWidget(body)

        hide_cb = QCheckBox(tr("Don't show this again"), dlg)
        lay.addWidget(hide_cb)

        bb = QDialogButtonBox(dlg)
        tag_btn = bb.addButton(tr("Tag anyway"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = bb.addButton(tr("Leave untagged"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setDefault(True)
        tag_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        lay.addWidget(bb)

        proceed = dlg.exec() == QDialog.DialogCode.Accepted
        if hide_cb.isChecked():
            self._settings.set("ti2_editor_force_tag_hide_warning", True)
        return proceed

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
            if not page_paint or bw is None:
                # bw is None when the twin render skipped this page (page-break
                # skew) — no spacer mask, so this page's paint can't be located.
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

    # -- misc ---------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._preview_btn.setEnabled(not busy)
        self._shuffle_btn.setEnabled(not busy)
        self._apply_btn.setEnabled(not busy)

    def _refresh_enabled(self) -> None:
        has = self._spec is not None
        self._preview_btn.setEnabled(has)
        self._shuffle_btn.setEnabled(has)
        self._apply_btn.setEnabled(has)
        self._refresh_undo_enabled()
        # Initial pass for the conditional checkboxes — without this the
        # default Qt state shows ALL four (L, P, double, triple) at
        # startup before any chart is loaded.
        self._refresh_pt_instr_visibility()

    def closeEvent(self, ev) -> None:  # noqa: N802
        # The window-corner X gets the same unsaved-changes guard as the
        # Close button (#49). Saves clear the flag first, so closing right
        # after Save As / Save & apply doesn't prompt.
        if not self._confirm_discard():
            ev.ignore()
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        self._clear_undo_history()
        self._preview_tmp.cleanup()
        super().closeEvent(ev)
