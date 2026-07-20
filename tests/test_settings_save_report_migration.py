"""schema-10 migration: 'Save a measurement report after each measurement' is now
on by default. A stored echo of the old off default flips on; an explicit choice
(True, or a deliberately-set value) survives. Booleans persist as the string
'false', which the float-based _SUPERSEDED_DEFAULTS can't match — hence the
dedicated migration."""
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings   # noqa: E402
from core.settings import AppSettings   # noqa: E402


def _settings(tmp_path: Path, value) -> AppSettings:
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s._qs.setValue("settings_schema", 9)      # pre-schema-10
    if value is not None:
        s._qs.setValue("save_measurement_report", value)
    return s


def test_old_off_default_flips_on(tmp_path):
    s = _settings(tmp_path, False)
    dropped = s.migrate()
    assert s.get("save_measurement_report") is True
    assert any("save_measurement_report" in d for d in dropped)


def test_explicit_on_kept(tmp_path):
    s = _settings(tmp_path, True)
    s.migrate()
    assert s.get("save_measurement_report") is True


def test_unset_is_on(tmp_path):
    s = _settings(tmp_path, None)
    s.migrate()
    assert s.get("save_measurement_report") is True


def test_string_false_flips_on(tmp_path):
    # QSettings INI persists booleans as the string "false".
    s = _settings(tmp_path, "false")
    s.migrate()
    assert s.get("save_measurement_report") is True
