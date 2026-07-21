"""The "Keyboard shortcuts" Help card (Knut/Sebastian keyboard-accessibility
pass).

A single source of truth for the shortcut list shown in the Welcome/Help window,
kept in sync with the bindings installed in ``ui.main_window._install_shortcuts``
and the chart layout editor. Rows are listed alphabetically by what they do, as
Knut asked.

Symbols use the macOS convention (⌘ = Command, ⇧ = Shift, ⏎ = Return). Every
shortcut carries a modifier or is an F-key on purpose: during a measurement the
Measure tab claims the bare keys (Space, ← / →, Enter, Esc) to drive the
instrument, so nothing here can be stolen out from under chartread.
"""
from __future__ import annotations

import html

from core.i18n import tr


def keyboard_card_title() -> str:
    return tr("Keyboard shortcuts")


def keyboard_card_subtitle() -> str:
    return tr("Every keyboard shortcut in ChromIQ, listed alphabetically.")


def _shortcuts() -> list[tuple[str, str]]:
    """(keys, what-it-does) — the second field is what the list is sorted by."""
    return [
        ("⌘1 … ⌘5",
         tr("Go to a tab (1 Create Chart · 2 Print Chart · 3 Measure · "
            "4 Build Profile · 5 Check & Refine)")),
        ("F1  ·  ⌘?", tr("Open Help (this window)")),
        ("⌘,", tr("Open Preferences (Settings)")),
        ("⌘T", tr("Open the Tools menu")),
        ("⌘⇧Z  ·  ⌘Y", tr("Redo — in the chart layout editor")),
        ("⌘⏎",
         tr("Run the current tab's main action (Generate · Print · "
            "Measure · Build · Check)")),
        ("⌘Z", tr("Undo — in the chart layout editor")),
    ]


def _measurement_note() -> str:
    return tr(
        "While you are measuring a chart, the keyboard drives the instrument "
        "instead: Space reads the next patch, ← and → move between strips, "
        "Enter confirms and Esc stops. The shortcuts above pause until the "
        "measurement is finished.")


def keyboard_shortcuts_html() -> str:
    """Rich-text (HTML) table for the Welcome/Help card, mirroring the folder
    guide's theme-neutral styling (grey header row, body inherits the label's
    themed text colour)."""
    def esc(s: str) -> str:
        return html.escape(s)

    parts = [
        f"<p>{esc(tr('Shortcuts for getting around ChromIQ with the keyboard. '))}"
        f"{esc(tr('On macOS ⌘ is the Command key.'))}</p>",
        "<p style='font-size:5px; margin:0'>&nbsp;</p>",
        "<table cellspacing='0' cellpadding='4' width='100%' "
        "style='border-collapse:collapse'>",
        "<tr style='color:#888'>"
        f"<th align='left'>{esc(tr('Shortcut'))}</th>"
        f"<th align='left'>{esc(tr('What it does'))}</th></tr>",
    ]
    for keys, action in sorted(_shortcuts(), key=lambda r: r[1].lower()):
        parts.append(
            "<tr>"
            f"<td valign='top'><code>{esc(keys)}</code></td>"
            f"<td valign='top'>{esc(action)}</td></tr>")
    parts.append("</table>")
    parts.append(f"<p style='margin-top:16px'>{esc(_measurement_note())}</p>")
    return "".join(parts)
