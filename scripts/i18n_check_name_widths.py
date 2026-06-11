#!/usr/bin/env python3
"""Check translated parameter names against the manual-mode label column.

ParameterWidget gives every row's label a fixed 190px column. Expert
non-boolean rows use a checkbox in that same column, whose indicator
eats ~25px. A translated name that exceeds its budget is clipped behind
the row's control, so this must pass for every shipped language.

Usage:  QT_QPA_PLATFORM=offscreen python scripts/i18n_check_name_widths.py de
Exit code 1 if any name is over budget (offenders listed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LABEL_BUDGET = 188     # fixed width 190, small slack
CHECKBOX_BUDGET = 163  # same column minus the checkbox indicator


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "de"

    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication, QLabel

    import yaml

    from core import i18n
    from core.resource_path import resource_path

    app = QApplication([])
    for fp in resource_path("assets/fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(fp))

    i18n.set_language(code)
    with open(resource_path("data/parameters.yaml"), encoding="utf-8") as f:
        params = i18n.translate_parameters(yaml.safe_load(f)["parameters"])

    fm = QLabel("x").fontMetrics()
    offenders = []
    for tool, defs in params.items():
        for p in defs:
            name = p["name"]
            expert = bool(p.get("expert_only"))
            boolean = p.get("type") == "boolean"
            if expert and boolean:
                continue  # expert booleans use a free-width checkbox row
            budget = CHECKBOX_BUDGET if expert else LABEL_BUDGET
            w = fm.horizontalAdvance(name + ":")
            if w > budget:
                offenders.append((w, budget, tool, p["flag"], name))

    for w, budget, tool, flag, name in sorted(offenders, reverse=True):
        print(f"OVER {w:4d}px (budget {budget})  {tool:9s} {flag:6s} {name}")
    print(f"[{code}] {len(offenders)} over-budget parameter names")
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
