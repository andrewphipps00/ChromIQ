#!/usr/bin/env python3
"""Wrap user-facing string literals in tr() — one-shot retrofit tool.

Walks a file's AST, finds string arguments at known text positions of
known UI calls (QLabel(...), setText(...), QMessageBox.warning(...), …)
and wraps them in tr(...).  Edits are applied bottom-up on exact source
spans, so formatting elsewhere is untouched.  Implicitly concatenated
literal groups are wrapped as a whole (the AST sees them as one node).

f-strings are converted to  tr("… {name} …").format(name=name)  only
when every interpolation is a plain variable name; anything richer is
left alone and listed in the report for a manual pass.

Usage:
    python scripts/i18n_wrap.py [--check] FILE [FILE …]

--check prints the planned rewrites without touching the files.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Constructor calls whose positional string args are display text.
# value = set of positions, or "any" = every positional str arg.
CTOR_TEXT_ARGS: dict[str, object] = {
    "QLabel": {0},
    "QPushButton": "any",      # QPushButton("text") / (icon, "text")
    "QToolButton": {0},
    "QCheckBox": {0},
    "QRadioButton": {0},
    "QGroupBox": {0},
    "QMenu": {0},
    "QAction": "any",          # ("text", parent) / (icon, "text", parent)
    "QProgressDialog": {0, 1},  # (labelText, cancelText, …)
    "TooltipButton": {0, 1},   # (title, body, parent, …)
    "WelcomeButton": "any",
    "QTreeWidgetItem": set(),  # handled via setText / list ctor — skip
    # ChromIQ helpers with (parent, text, …) signatures
    "make_browse_button": {1},
    "open_dir_dialog": {1},
}

# Method names (called on any object) whose arg at the given positions
# is display text.
METHOD_TEXT_ARGS: dict[str, object] = {
    "setText": {0},
    "setToolTip": {0},
    "setTitle": {0},
    "setWindowTitle": {0},
    "setPlaceholderText": {0},
    "setStatusTip": {0},
    "setWhatsThis": {0},
    "setLabelText": {0},
    "setInformativeText": {0},
    "setDetailedText": {0},
    "setTabText": {1},
    "setTabToolTip": {1},
    "addTab": "any",
    "insertTab": "any",
    "addAction": {0},
    "showMessage": {0},
    "setCancelButtonText": {0},
    "setButtonText": {1},
}

# Methods whose first arg is a list of display strings.
METHOD_TEXT_LIST_ARGS = {"addItems", "setHeaderLabels"}

# addItem("label", userData) — label only, never the data value.
METHOD_FIRST_ONLY = {"addItem"}

# Static/class calls:  QMessageBox.warning(parent, title, text, …)
STATIC_TEXT_ARGS: dict[tuple[str, str], object] = {
    ("QMessageBox", "information"): {1, 2},
    ("QMessageBox", "warning"): {1, 2},
    ("QMessageBox", "critical"): {1, 2},
    ("QMessageBox", "question"): {1, 2},
    ("QMessageBox", "about"): {1, 2},
    ("QInputDialog", "getText"): {1, 2},
    ("QInputDialog", "getItem"): {1, 2},
    ("QFileDialog", "getSaveFileName"): {1, 3},
    ("QFileDialog", "getOpenFileName"): {1, 3},
    ("QFileDialog", "getExistingDirectory"): {1},
}


def _is_text(value: str) -> bool:
    """Worth translating?  Needs at least one letter."""
    return any(c.isalpha() for c in value)


class _Collector(ast.NodeVisitor):
    def __init__(self, src: str):
        self.src = src
        self.raw = src.encode("utf-8")   # ast col offsets are UTF-8 bytes
        self.edits: list[tuple[int, int, bytes]] = []  # (start, end, replacement)
        self.skipped: list[str] = []                   # report lines
        self.lines_off: list[int] = [0]
        for line in self.raw.splitlines(keepends=True):
            self.lines_off.append(self.lines_off[-1] + len(line))

    def _span(self, node: ast.AST) -> tuple[int, int]:
        s = self.lines_off[node.lineno - 1] + node.col_offset
        e = self.lines_off[node.end_lineno - 1] + node.end_col_offset
        return s, e

    # ------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        positions = None
        func = node.func
        if isinstance(func, ast.Name):
            positions = CTOR_TEXT_ARGS.get(func.id)
            if func.id == "tr":          # already wrapped — don't recurse into it
                return
        elif isinstance(func, ast.Attribute):
            if (isinstance(func.value, ast.Name)
                    and (func.value.id, func.attr) in STATIC_TEXT_ARGS):
                positions = STATIC_TEXT_ARGS[(func.value.id, func.attr)]
            elif func.attr in METHOD_TEXT_LIST_ARGS:
                if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    for elt in node.args[0].elts:
                        self._wrap_text_node(elt, func.attr)
                self.generic_visit(node)
                return
            elif func.attr in METHOD_FIRST_ONLY:
                if node.args:
                    self._wrap_text_node(node.args[0], func.attr)
                self.generic_visit(node)
                return
            else:
                positions = METHOD_TEXT_ARGS.get(func.attr)

        if positions is not None:
            for i, arg in enumerate(node.args):
                if positions == "any" or i in positions:  # type: ignore[comparison-overlap]
                    self._wrap_text_node(arg, _func_label(func))
        self.generic_visit(node)

    # ------------------------------------------------------------------
    def _wrap_text_node(self, node: ast.AST, ctx: str) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not _is_text(node.value):
                return
            s, e = self._span(node)
            self.edits.append((s, e, b"tr(" + self.raw[s:e] + b")"))
        elif isinstance(node, ast.JoinedStr):
            self._convert_fstring(node, ctx)
        # Name / call / attribute args: text decided elsewhere — manual pass.

    def _convert_fstring(self, node: ast.JoinedStr, ctx: str) -> None:
        parts: list[str] = []
        kwargs: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value.replace("{", "{{").replace("}", "}}"))
            elif isinstance(v, ast.FormattedValue):
                if (isinstance(v.value, ast.Name) and v.conversion == -1
                        and v.format_spec is None):
                    name = v.value.id
                    parts.append("{" + name + "}")
                    if f"{name}={name}" not in kwargs:
                        kwargs.append(f"{name}={name}")
                else:
                    self.skipped.append(
                        f"line {node.lineno}: complex f-string in {ctx}: "
                        f"{ast.get_source_segment(self.src, node)!r:.90}")
                    return
            else:
                self.skipped.append(f"line {node.lineno}: odd f-string part in {ctx}")
                return
        text = "".join(parts)
        if not _is_text(text):
            return
        if "\n" in text:
            self.skipped.append(
                f"line {node.lineno}: multi-line f-string in {ctx} — manual")
            return
        s, e = self._span(node)
        lit = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        repl = f"tr({lit})"
        if kwargs:
            repl += f".format({', '.join(kwargs)})"
        self.edits.append((s, e, repl.encode("utf-8")))


def _func_label(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return "?"


def process(path: Path, check: bool) -> tuple[int, list[str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    col = _Collector(src)
    col.visit(tree)
    if not col.edits:
        return 0, col.skipped

    raw = col.raw
    for s, e, repl in sorted(col.edits, reverse=True):
        raw = raw[:s] + repl + raw[e:]
    new = raw.decode("utf-8")

    import re
    if not re.search(r"^from core\.i18n import .*\btr\b", new, re.MULTILINE):
        # Insert after the last top-level import (end_lineno covers
        # multi-line parenthesized imports).
        new_tree = ast.parse(new)
        last_import_end = 0
        for stmt in new_tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                last_import_end = stmt.end_lineno or stmt.lineno
        lines = new.splitlines(keepends=True)
        lines.insert(last_import_end, "from core.i18n import tr\n")
        new = "".join(lines)

    ast.parse(new)  # syntax sanity before writing
    if not check:
        path.write_text(new, encoding="utf-8")
    return len(col.edits), col.skipped


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    files = [Path(a) for a in args if a != "--check"]
    total = 0
    for f in files:
        n, skipped = process(f, check)
        total += n
        print(f"{f}: {n} wrapped, {len(skipped)} skipped")
        for s in skipped:
            print(f"    SKIP {s}")
    print(f"TOTAL {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
