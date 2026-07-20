"""schema-8/9 migration (#49): the patch-read warning is now adaptive, and its
default floor moved 20 → 50 ΔE (Nelson's value). A stored echo of the old 20
default, or a value raised above the new 50 floor, is reset so the user gets the
new default; a value the user deliberately lowered (more sensitivity) is kept."""
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings   # noqa: E402
from core.settings import AppSettings   # noqa: E402


def _settings(tmp_path: Path, value) -> AppSettings:
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s._qs.setValue("settings_schema", 7)      # pre-schema-8/9
    if value is not None:
        s._qs.setValue("patch_read_warn_de", value)
    return s


def test_old_default_20_is_reset(tmp_path):
    # 20 was the old default; drop it so it falls through to the new 50.
    s = _settings(tmp_path, 20.0)
    dropped = s.migrate()
    assert s._qs.value("patch_read_warn_de", None) is None
    assert any("patch_read_warn_de" in d for d in dropped)


def test_value_raised_above_new_floor_is_reset(tmp_path):
    s = _settings(tmp_path, 70.0)
    dropped = s.migrate()
    assert s._qs.value("patch_read_warn_de", None) is None      # -> falls back to 50
    assert any("patch_read_warn_de" in d for d in dropped)


def test_value_between_old_and_new_default_is_kept(tmp_path):
    # 30 is more sensitive than the new 50 default — a deliberate choice, kept.
    s = _settings(tmp_path, 30.0)
    s.migrate()
    assert float(s._qs.value("patch_read_warn_de")) == 30.0


def test_lowered_value_is_kept(tmp_path):
    s = _settings(tmp_path, 10.0)
    s.migrate()
    assert float(s._qs.value("patch_read_warn_de")) == 10.0     # user wanted more sensitivity


def test_new_default_value_untouched(tmp_path):
    s = _settings(tmp_path, 50.0)
    s.migrate()
    assert float(s._qs.value("patch_read_warn_de")) == 50.0


def test_unset_value_stays_unset(tmp_path):
    s = _settings(tmp_path, None)
    s.migrate()
    assert s._qs.value("patch_read_warn_de", None) is None
