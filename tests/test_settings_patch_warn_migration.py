"""schema-8 migration (#49): a patch-read warning value RAISED above the default
in an earlier beta is now too high (the flag became adaptive, so raising it only
hides genuine misreads). Reset raised values; keep lowered ones."""
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings   # noqa: E402
from core.settings import AppSettings   # noqa: E402


def _settings(tmp_path: Path, value) -> AppSettings:
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s._qs.setValue("settings_schema", 7)      # pre-schema-8
    if value is not None:
        s._qs.setValue("patch_read_warn_de", value)
    return s


def test_raised_value_is_reset(tmp_path):
    s = _settings(tmp_path, 45.0)
    dropped = s.migrate()
    assert s._qs.value("patch_read_warn_de", None) is None      # -> falls back to 20
    assert any("patch_read_warn_de" in d for d in dropped)


def test_lowered_value_is_kept(tmp_path):
    s = _settings(tmp_path, 10.0)
    s.migrate()
    assert float(s._qs.value("patch_read_warn_de")) == 10.0     # user wanted more sensitivity


def test_default_value_untouched(tmp_path):
    s = _settings(tmp_path, 20.0)
    s.migrate()
    assert float(s._qs.value("patch_read_warn_de")) == 20.0


def test_unset_value_stays_unset(tmp_path):
    s = _settings(tmp_path, None)
    s.migrate()
    assert s._qs.value("patch_read_warn_de", None) is None
