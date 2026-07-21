"""schema-11 migration (v3.14.0): the ChromIQ chart-reading engine (#126) is now
the default. A stored echo of the old "argyll" default is dropped so it resolves
to "chromiq"; an explicit choice survives; a fresh install gets "chromiq"."""
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSettings   # noqa: E402
from core.settings import AppSettings   # noqa: E402


def _settings(tmp_path: Path, value) -> AppSettings:
    s = AppSettings()
    s._qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s._qs.setValue("settings_schema", 10)     # pre-schema-11
    if value is not None:
        s._qs.setValue("chartread_engine", value)
    return s


def test_fresh_install_defaults_to_chromiq(tmp_path):
    s = _settings(tmp_path, None)
    s.migrate()
    assert s.get("chartread_engine") == "chromiq"


def test_old_argyll_default_flips_to_chromiq(tmp_path):
    s = _settings(tmp_path, "argyll")          # stored echo of the old default
    dropped = s.migrate()
    assert s.get("chartread_engine") == "chromiq"
    assert any("chartread_engine" in d for d in dropped)


def test_explicit_chromiq_kept(tmp_path):
    s = _settings(tmp_path, "chromiq")
    s.migrate()
    assert s.get("chartread_engine") == "chromiq"


def test_migration_runs_once(tmp_path):
    s = _settings(tmp_path, "argyll")
    s.migrate()
    # user later deliberately picks argyll again; a second migrate must NOT undo it
    s._qs.setValue("chartread_engine", "argyll")
    s.migrate()
    assert s.get("chartread_engine") == "argyll"
