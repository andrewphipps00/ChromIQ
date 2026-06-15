"""Target-rename chooser dialog (#52): button labels must not clip."""
import pytest

pytest.importorskip("PyQt6")
from pathlib import Path  # noqa: E402

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from ui.dialogs.target_change_dialog import TargetChangeDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("old,new", [
    ("Glossy", "Matte"),
    ("Canon-PRO-300-Hahnemuehle-PhotoRag-308-Glossy",
     "Canon-PRO-300-Hahnemuehle-FineArt-Baryta-Matte"),     # long names (#52)
])
def test_option_buttons_are_wide_enough_for_their_text(qapp, old, new):
    dlg = TargetChangeDialog(old, new, Path("/u/" + old), Path("/u/" + new))
    dlg.resize(dlg.sizeHint())
    dlg.show()
    qapp.processEvents()
    try:
        # Only the #52 option buttons carry an explicit minimum width
        # (advance + 48); the plain Cancel button uses Qt's own default sizing,
        # which fits "Cancel" without our heuristic (its natural width is
        # narrower than need+36 on Windows, wider on macOS — not a clip).
        option_btns = [b for b in dlg.findChildren(QPushButton)
                       if b.minimumWidth() > 0]
        assert option_btns
        for b in option_btns:
            need = b.fontMetrics().horizontalAdvance(b.text())
            # text + the stylesheet's 18px*2 padding must fit inside the button
            assert b.width() >= need + 36, f"clipped: {b.text()!r}"
    finally:
        dlg.close()
