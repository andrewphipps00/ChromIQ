"""PrefixLockedLineEdit / SuffixLockedLineEdit affix-locking behaviour (#68)."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.widgets import PrefixLockedLineEdit, SuffixLockedLineEdit  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_prefix_locks_head_keeps_tail(qapp):
    e = PrefixLockedLineEdit()
    e.set_tail("Baryta")
    e.set_prefix("i1Pro-A4-484p-")
    assert e.text() == "i1Pro-A4-484p-Baryta"
    assert e.tail() == "Baryta"
    # Swapping the prefix preserves the user's tail.
    e.set_prefix("ColorMunki-A3-")
    assert e.text() == "ColorMunki-A3-Baryta"
    assert e.tail() == "Baryta"
    # Clearing the prefix leaves just the tail.
    e.set_prefix("")
    assert e.text() == "Baryta"


def test_prefix_text_not_starting_with_prefix_becomes_tail(qapp):
    # A bare setText (e.g. a preset-loaded name) is treated as the editable tail.
    e = PrefixLockedLineEdit()
    e.set_prefix("i1Pro-")
    e.setText("Some Preset Name")
    assert e.tail() == "Some Preset Name"
    assert e.text() == "i1Pro-Some Preset Name"


def test_prefix_cursor_clamps_into_tail(qapp):
    e = PrefixLockedLineEdit()
    e.set_prefix("i1Pro-")
    e.set_tail("X")
    e.setCursorPosition(0)            # try to put the cursor inside the prefix
    assert e.cursorPosition() >= len("i1Pro-")


def test_suffix_still_locks_tail(qapp):
    # The mirror widget is unchanged and still usable.
    e = SuffixLockedLineEdit()
    e.set_base("Baryta")
    e.set_suffix("-i1Pro-A4")
    assert e.text() == "Baryta-i1Pro-A4"
    assert e.base() == "Baryta"
