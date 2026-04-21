"""Dark theme palette and stylesheet for ChromIQ."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette

from core.resource_path import resource_path

_ARROW_DOWN = str(resource_path("assets/arrow_down.svg")).replace("\\", "/")
_ARROW_UP   = str(resource_path("assets/arrow_up.svg")).replace("\\", "/")

# -----------------------------------------------------------------------
# Colour tokens
# -----------------------------------------------------------------------
BG_DARK    = "#1a1a1a"
BG_PANEL   = "#1e1e1e"
BG_WIDGET  = "#2a2a2a"
BG_INPUT   = "#242424"
BG_HEADER  = "#161616"

BORDER     = "#3a3a3a"
BORDER_HI  = "#555555"

TEXT_MAIN  = "#e0e0e0"
TEXT_DIM   = "#909090"
TEXT_MONO  = "#a8e6a8"

ACCENT_BLUE  = "#2979ff"
ACCENT_HOVER = "#448aff"
ACCENT_CYAN  = "#00bcd4"
ACCENT_WARN  = "#ff9800"
ACCENT_ERROR = "#f44336"
ACCENT_OK    = "#4caf50"


def make_dark_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Base,            QColor(BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_WIDGET))
    pal.setColor(QPalette.ColorRole.Text,            QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Button,          QColor(BG_WIDGET))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT_BLUE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link,            QColor(ACCENT_CYAN))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#2d2d2d"))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(TEXT_MAIN))
    # Fusion style uses Light/Midlight/Shadow for frame highlights — keep them
    # near-black so widget borders and the QTabWidget top frame line are dark.
    pal.setColor(QPalette.ColorRole.Light,           QColor("#1c1c1c"))
    pal.setColor(QPalette.ColorRole.Midlight,        QColor("#1e1e1e"))
    pal.setColor(QPalette.ColorRole.Mid,             QColor("#161616"))
    pal.setColor(QPalette.ColorRole.Dark,            QColor("#101010"))
    pal.setColor(QPalette.ColorRole.Shadow,          QColor("#080808"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#505050"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#505050"))
    return pal


APP_STYLESHEET = f"""
/* ---- Base --------------------------------------------------------- */
QWidget {{
    background: {BG_PANEL};
    color: {TEXT_MAIN};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {BG_DARK};
}}

/* ---- Tabs --------------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-top: 2px solid #0a0a0a;   /* dark separator below tab bar */
    background: {BG_PANEL};
}}
QTabWidget {{
    background: {BG_DARK};
    border-top: 1px solid #000000;   /* hide bright frame line at very top */
}}
QTabBar {{
    background: {BG_DARK};
}}
QTabBar::tab {{
    background: #252525;
    color: {TEXT_DIM};
    padding: 9px 20px;
    border: 1px solid {BORDER};
    border-bottom: 1px solid #0a0a0a;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    min-width: 130px;
}}
QTabBar::tab:selected {{
    background: {BG_PANEL};
    color: #ffffff;
    border-bottom: 2px solid {ACCENT_BLUE};
}}
QTabBar::tab:hover:!selected {{
    background: #2d2d2d;
    color: {TEXT_MAIN};
}}
QTabBar::scroller {{
    background: {BG_DARK};
}}
/* Fill the unused area to the right of the last tab */
QTabWidget > QTabBar {{
    background: {BG_DARK};
}}

/* ---- Buttons ------------------------------------------------------ */
QPushButton {{
    background: {BG_WIDGET};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER_HI};
    border-radius: 4px;
    padding: 6px 16px;
    min-height: 28px;
}}
QPushButton:hover {{
    background: #363636;
    border-color: #707070;
}}
QPushButton:pressed {{
    background: #1e1e1e;
}}
QPushButton:disabled {{
    color: #505050;
    border-color: {BORDER};
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
    background: #1a3a6a;
    border-color: #1a3a6a;
    color: #607090;
}}
QPushButton#danger {{
    background: #5a1a1a;
    color: #ff9090;
    border-color: #8a2020;
}}
QPushButton#danger:hover {{
    background: #6a2020;
}}

/* ---- Inputs ------------------------------------------------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_INPUT};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
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
    border-left: 1px solid {BORDER};
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
    background: #323232;
}}
QComboBox::drop-down:hover {{
    background: #404040;
}}
QComboBox::down-arrow {{
    image: url({_ARROW_DOWN});
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background: #2d2d2d;
    border: 1px solid {BORDER_HI};
    selection-background-color: {ACCENT_BLUE};
    outline: none;
}}
/* Spinbox: tall enough to show both buttons */
QSpinBox, QDoubleSpinBox {{
    padding-right: 20px;
    min-height: 28px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    height: 14px;
    border-left: 1px solid {BORDER};
    border-top: 1px solid transparent;
    border-right: 1px solid transparent;
    border-bottom: 1px solid {BORDER};
    border-top-right-radius: 3px;
    background: #323232;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
    background: #444444;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    height: 14px;
    border-left: 1px solid {BORDER};
    border-top: 1px solid {BORDER};
    border-right: 1px solid transparent;
    border-bottom: 1px solid transparent;
    border-bottom-right-radius: 3px;
    background: #323232;
}}
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #444444;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({_ARROW_UP});
    width: 10px;
    height: 6px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({_ARROW_DOWN});
    width: 10px;
    height: 6px;
}}

/* ---- CheckBox ----------------------------------------------------- */
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_HI};
    border-radius: 3px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT_HOVER};
}}

/* ---- Log / terminal output --------------------------------------- */
QPlainTextEdit#log {{
    background: #111111;
    color: {TEXT_MONO};
    font-family: "Menlo", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid {BORDER};
    border-radius: 3px;
}}

/* ---- GroupBox ---------------------------------------------------- */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 4px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    top: 2px;
    color: {TEXT_DIM};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ---- ScrollBar --------------------------------------------------- */
QScrollBar:vertical {{
    background: {BG_DARK};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #404040;
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: #565656; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {BG_DARK};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: #404040;
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background: #565656; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- Splitter ----------------------------------------------------- */
QSplitter::handle {{
    background: {BORDER};
}}

/* ---- Labels ------------------------------------------------------- */
QLabel#warning {{
    background: #3a2a00;
    color: {ACCENT_WARN};
    border: 1px solid {ACCENT_WARN};
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#info {{
    background: #0a2a3a;
    color: {ACCENT_CYAN};
    border: 1px solid {ACCENT_CYAN};
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#error {{
    background: #3a0a0a;
    color: {ACCENT_ERROR};
    border: 1px solid {ACCENT_ERROR};
    border-radius: 4px;
    padding: 6px 10px;
}}
QLabel#patch_count {{
    font-size: 24px;
    font-weight: bold;
    color: {ACCENT_CYAN};
}}
QLabel#section_title {{
    font-size: 14px;
    font-weight: bold;
    color: #ffffff;
}}

/* ---- Browse / file-picker buttons -------------------------------- */
QPushButton#browse {{
    background: #323232;
    color: {TEXT_MAIN};
    border: 1px solid {BORDER_HI};
    border-radius: 3px;
    padding: 4px 8px;
    min-width: 32px;
    font-size: 14px;
}}
QPushButton#browse:hover {{
    background: #444;
}}

/* ---- ToolButton (tooltip icon) ----------------------------------- */
QToolButton#tooltip_btn {{
    background: transparent;
    border: none;
    padding: 0;
}}
QToolButton#tooltip_btn:hover {{
    background: rgba(255,255,255,15);
    border-radius: 10px;
}}
"""
