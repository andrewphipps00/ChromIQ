"""Light mode palette and stylesheet for ChromIQ.

Counterpart to ui/styles.py (dark). Selected at runtime by ui/theme.py based
on the user's appearance setting ("light" / "dark" / "auto").
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

from core.resource_path import resource_path

_ARROW_DOWN_DARK = str(resource_path("assets/arrow_down_dark.svg")).replace("\\", "/")
_ARROW_UP_DARK   = str(resource_path("assets/arrow_up_dark.svg")).replace("\\", "/")

# -----------------------------------------------------------------------
# Colour tokens — v2 (fully-light theme, including header & terminal)
# -----------------------------------------------------------------------

# Backgrounds
LM_BG_WINDOW   = "#eeece8"   # window background, tab-bar fill
LM_BG_PANEL    = "#ffffff"   # main content panels (tab pane)
LM_BG_SURFACE  = "#f7f4ef"   # GroupBox fill, footer strips, patches card
LM_BG_WIDGET   = "#edebe6"   # QPushButton default bg, ComboBox drop-down
LM_BG_INPUT    = "#ffffff"   # QLineEdit / QSpinBox / QComboBox bg
LM_BG_VIEWER   = "#efebe6"   # TIFF preview / 3D gamut viewer fill

# Tab bar (per design spec)
LM_TAB_INACTIVE_BG     = "#e5e2dd"
LM_TAB_INACTIVE_TEXT   = "#989490"
LM_TAB_ACTIVE_BG       = "#ffffff"
LM_TAB_ACTIVE_TEXT     = "#22211f"

# Borders
LM_BORDER      = "#d0ccc6"   # standard border
LM_BORDER_HI   = "#b0aba4"   # hover / focus border

# Text
LM_TEXT_MAIN   = "#22211f"   # primary text (matches design spec)
LM_TEXT_DIM    = "#7a7570"   # secondary labels, GroupBox titles
LM_TEXT_FAINT  = "#a8a4a0"   # placeholder, disabled text

# Mode buttons (segmented switch — Guided / Manual / Expert)
LM_MODE_BG     = "#eeeae5"
LM_MODE_BORDER = "#ccc9c3"
LM_MODE_TEXT   = "#989490"

# Info-box (magenta-themed — matches the magenta accent of dark mode)
LM_INFO_BG     = "#fff0f4"
LM_INFO_TEXT   = "#c2185b"
LM_INFO_BORDER = "#ff4573"

# Semantic accents — darkened for legibility on white backgrounds
LM_ACCENT_WARN  = "#e65100"
LM_ACCENT_ERROR = "#c62828"
LM_ACCENT_OK    = "#2e7d32"

# Warning / info-box tokens (shared so the print-tab airprintInfoBox can
# match the global QLabel#warning chrome).
LM_WARN_BG       = "#fff8e1"   # pale cream
LM_WARN_BORDER   = "#f9c940"   # amber
LM_WARN_TEXT     = "#2a2000"   # very dark olive — primary body text
LM_WARN_TITLE    = "#8a6500"   # darker amber — bold titles inside warning boxes

# Unchanged accents
ACCENT_BLUE  = "#2979ff"
ACCENT_HOVER = "#448aff"
ACCENT_CYAN  = "#00bcd4"

# v2 terminal (light)
LM_LOG_BG    = "#f4f8f5"
LM_LOG_TEXT  = "#2a6e2a"
LM_LOG_BORDER = "#d8e0d8"


# -----------------------------------------------------------------------
# QPalette
# -----------------------------------------------------------------------

def make_light_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(LM_BG_WINDOW))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(LM_TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Base,            QColor(LM_BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(LM_BG_SURFACE))
    pal.setColor(QPalette.ColorRole.Text,            QColor(LM_TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor("#000000"))
    pal.setColor(QPalette.ColorRole.Button,          QColor(LM_BG_WIDGET))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(LM_TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT_BLUE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link,            QColor(ACCENT_CYAN))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#fffbe6"))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(LM_TEXT_MAIN))
    # Fusion uses Light/Midlight/Mid/Dark/Shadow for frame highlights.
    # Keep them in the warm-gray range so borders and splitter handles
    # stay subtle rather than mapping to near-black (dark-mode behaviour).
    pal.setColor(QPalette.ColorRole.Light,           QColor("#f8f5f0"))
    pal.setColor(QPalette.ColorRole.Midlight,        QColor("#f0ece6"))
    pal.setColor(QPalette.ColorRole.Mid,             QColor("#d8d4ce"))
    pal.setColor(QPalette.ColorRole.Dark,            QColor("#c0bcb6"))
    pal.setColor(QPalette.ColorRole.Shadow,          QColor("#a0a09a"))
    pal.setColor(QPalette.ColorGroup.Disabled,
                 QPalette.ColorRole.Text,       QColor(LM_TEXT_FAINT))
    pal.setColor(QPalette.ColorGroup.Disabled,
                 QPalette.ColorRole.ButtonText, QColor(LM_TEXT_FAINT))
    return pal


# -----------------------------------------------------------------------
# QSS stylesheet
# -----------------------------------------------------------------------

LIGHT_STYLESHEET = f"""
/* -- Base ---------------------------------------------------------- */
/* No `background` on QWidget — that would paint over each GroupBox's
 * surface color when its children draw. Children stay transparent and
 * inherit visually from whichever container they sit in (tab pane,
 * GroupBox, etc.). The top-level containers below set explicit bgs. */
QWidget {{
    color: {LM_TEXT_MAIN};
    font-family: "Inter";
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {LM_BG_WINDOW};
}}
QDialog QLabel {{ background: transparent; }}

/* -- Tabs ---------------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {LM_BORDER};
    border-top: 1px solid {LM_BORDER};
    background: {LM_BG_PANEL};
}}
QTabWidget {{
    background: {LM_BG_WINDOW};
    border-top: none;
}}
QTabBar {{
    background: {LM_BG_WINDOW};
}}
QTabBar::tab {{
    background: {LM_TAB_INACTIVE_BG};
    color: {LM_TAB_INACTIVE_TEXT};
    padding: 9px 20px;
    border: 1px solid {LM_BORDER};
    border-bottom: 2px solid transparent;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    min-width: 130px;
}}
QTabBar::tab:selected {{
    background: {LM_TAB_ACTIVE_BG};
    color: {LM_TAB_ACTIVE_TEXT};
}}
QTabBar::tab:hover:!selected {{
    background: #dedad4;
    color: {LM_TAB_ACTIVE_TEXT};
}}
QTabBar::scroller {{
    background: {LM_BG_WINDOW};
}}

/* -- Buttons ------------------------------------------------------- */
QPushButton {{
    background: {LM_BG_WIDGET};
    color: {LM_TEXT_MAIN};
    border: 1px solid {LM_BORDER_HI};
    border-radius: 4px;
    padding: 6px 16px;
    min-height: 28px;
}}
QPushButton:hover {{
    background: #e4e0da;
    border-color: #a0a09a;
}}
QPushButton:pressed {{
    background: #d8d4ce;
}}
QPushButton:disabled {{
    color: {LM_TEXT_FAINT};
    border-color: {LM_BORDER};
}}
QPushButton#primary {{
    background: {ACCENT_BLUE};
    color: #ffffff;
    border: 1px solid {ACCENT_BLUE};
    font-weight: bold;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background: #b8cef8;
    border-color: #b8cef8;
    color: #7890c0;
}}
QPushButton#danger {{
    background: #fde8e8;
    color: {LM_ACCENT_ERROR};
    border-color: #f0b0b0;
}}
QPushButton#danger:hover {{
    background: #fbd4d4;
}}

/* -- Inputs -------------------------------------------------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {LM_BG_INPUT};
    color: {LM_TEXT_MAIN};
    border: 1px solid {LM_BORDER};
    border-radius: 3px;
    padding: 4px 6px;
    min-height: 26px;
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {ACCENT_BLUE};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border-left: 1px solid {LM_BORDER};
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
    background: {LM_BG_WIDGET};
}}
QComboBox::drop-down:hover {{
    background: #e4e0da;
}}
QComboBox::down-arrow {{
    image: url({_ARROW_DOWN_DARK});
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background: {LM_BG_PANEL};
    border: 1px solid {LM_BORDER_HI};
    selection-background-color: {ACCENT_BLUE};
    selection-color: #ffffff;
    outline: none;
}}
QSpinBox, QDoubleSpinBox {{
    padding-right: 20px;
    min-height: 28px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    height: 14px;
    border-left: 1px solid {LM_BORDER};
    border-top: 1px solid transparent;
    border-right: 1px solid transparent;
    border-bottom: 1px solid {LM_BORDER};
    border-top-right-radius: 3px;
    background: {LM_BG_WIDGET};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
    background: #e4e0da;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    height: 14px;
    border-left: 1px solid {LM_BORDER};
    border-top: 1px solid {LM_BORDER};
    border-right: 1px solid transparent;
    border-bottom: 1px solid transparent;
    border-bottom-right-radius: 3px;
    background: {LM_BG_WIDGET};
}}
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #e4e0da;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({_ARROW_UP_DARK});
    width: 10px;
    height: 6px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({_ARROW_DOWN_DARK});
    width: 10px;
    height: 6px;
}}

/* -- CheckBox ------------------------------------------------------ */
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {LM_BORDER_HI};
    border-radius: 3px;
    background: {LM_BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT_HOVER};
}}

/* -- Log / terminal output — v2 light variant --------------------- */
QPlainTextEdit#log {{
    background: {LM_LOG_BG};
    color: {LM_LOG_TEXT};
    font-family: "Menlo", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid {LM_LOG_BORDER};
    border-radius: 3px;
}}

/* -- GroupBox ------------------------------------------------------ */
QGroupBox {{
    border: 1px solid {LM_BORDER};
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 4px;
    background: {LM_BG_SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    top: 2px;
    color: {LM_TEXT_FAINT};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* -- ScrollBar ----------------------------------------------------- */
QScrollBar:vertical {{
    background: {LM_BG_WINDOW};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {LM_BORDER_HI};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: #908d88; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {LM_BG_WINDOW};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {LM_BORDER_HI};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background: #908d88; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* -- Splitter ------------------------------------------------------ */
QSplitter::handle {{
    background: {LM_BORDER};
}}

/* -- Labels -------------------------------------------------------- */
QLabel#warning {{
    background: {LM_WARN_BG};
    color: {LM_WARN_TEXT};
    border: 1px solid {LM_WARN_BORDER};
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#info {{
    background: {LM_INFO_BG};
    color: {LM_INFO_TEXT};
    border: 1px solid {LM_INFO_BORDER};
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#error {{
    background: #ffebee;
    color: {LM_ACCENT_ERROR};
    border: 1px solid #ef9a9a;
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#patch_count {{
    font-size: 24px;
    font-weight: bold;
    color: {LM_TEXT_MAIN};
}}
QLabel#section_title {{
    font-size: 14px;
    font-weight: bold;
    color: {LM_TEXT_MAIN};
}}

/* -- Mode buttons (Guided / Manual / Expert) --------------------- */
/* Default appearance. The per-tab QSS injection in main_window also
 * targets QPushButton#mode_btn and re-tints the :checked state with
 * the active tab's spectrum color. */
QPushButton#mode_btn {{
    background: {LM_MODE_BG};
    border: 1px solid {LM_MODE_BORDER};
    color: {LM_MODE_TEXT};
    font-size: 13px;
    font-weight: 700;
    padding: 6px 22px;
}}
QPushButton#mode_btn:hover {{
    background: #e4e0da;
    border-color: {LM_BORDER_HI};
    color: {LM_TEXT_MAIN};
}}

/* -- Browse / file-picker buttons --------------------------------- */
QPushButton#browse {{
    background: {LM_BG_WIDGET};
    color: {LM_TEXT_MAIN};
    border: 1px solid {LM_BORDER_HI};
    border-radius: 3px;
    padding: 4px 8px;
    min-width: 32px;
    font-size: 14px;
}}
QPushButton#browse:hover {{
    background: #e4e0da;
}}

/* -- Icon-only square buttons ------------------------------------- */
QPushButton#icon_btn {{
    padding: 0;
    min-height: 0;
    min-width: 0;
}}

/* -- ToolButton (tooltip icon) ------------------------------------ */
QToolButton#tooltip_btn {{
    background: transparent;
    border: none;
    padding: 0;
}}
QToolButton#tooltip_btn:hover {{
    background: rgba(0,0,0,8);
    border-radius: 10px;
}}

/* -- Compact inputs (Measure tab: Additional Options) ------------- */
QSpinBox#compact_input, QDoubleSpinBox#compact_input, QComboBox#compact_input {{
    min-height: 0;
    max-height: 22px;
    padding: 1px 6px;
}}
QSpinBox#compact_input, QDoubleSpinBox#compact_input {{
    padding-right: 20px;
}}
QSpinBox#compact_input::up-button, QDoubleSpinBox#compact_input::up-button {{
    height: 10px;
}}
QSpinBox#compact_input::down-button, QDoubleSpinBox#compact_input::down-button {{
    height: 10px;
}}
QComboBox#compact_input {{
    padding-right: 28px;
}}
"""
