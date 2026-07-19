"""ARGYLL_EXCLUDE_SERIAL_SCAN handling: skip Argyll's slow phantom-serial-port
probe on macOS so a USB spectro connects fast, without ever excluding a real
serial instrument (Basti — 11 s → 0.35 s to the calibration prompt)."""
from __future__ import annotations

import sys

import pytest

from core.argyll_runner import (_phantom_serial_ports,
                                argyll_serial_exclusion_ports,
                                merged_serial_exclusion)


def test_phantom_filter_keeps_real_usb_serial_adapters():
    cands = [
        "/dev/cu.Bluetooth-Incoming-Port",   # phantom → exclude
        "/dev/cu.debug-console",             # phantom → exclude
        "/dev/cu.wlan-debug",                # phantom → exclude
        "/dev/cu.usbserial-1420",            # REAL adapter (e.g. SpectroScan) → keep
        "/dev/cu.usbmodem14201",             # REAL adapter → keep
    ]
    excl = _phantom_serial_ports(cands)
    assert "/dev/cu.Bluetooth-Incoming-Port" in excl
    assert "/dev/cu.debug-console" in excl
    assert "/dev/cu.wlan-debug" in excl
    # A real USB-serial adapter is NEVER excluded → serial instruments still work.
    assert "/dev/cu.usbserial-1420" not in excl
    assert "/dev/cu.usbmodem14201" not in excl
    assert excl == sorted(excl)                       # stable order


def test_merged_value_adds_ours_and_keeps_user_set():
    # No existing value → just our ports.
    assert merged_serial_exclusion(None, ["/dev/cu.a", "/dev/cu.b"]) == \
        "/dev/cu.a;/dev/cu.b"
    # A user-set value is preserved and never dropped.
    assert merged_serial_exclusion("COM9;COM10", ["/dev/cu.a"]) == \
        "COM9;COM10;/dev/cu.a"
    # Commas are accepted as separators too.
    assert merged_serial_exclusion("x,y", ["z"]) == "x;y;z"
    # De-dup: a port already listed isn't repeated.
    assert merged_serial_exclusion("/dev/cu.a", ["/dev/cu.a"]) == "/dev/cu.a"
    # Nothing to exclude → None (callers then leave the environment untouched).
    assert merged_serial_exclusion(None, []) is None
    assert merged_serial_exclusion("", []) is None


def test_no_exclusion_off_macos(monkeypatch):
    """Windows/Linux are left completely unchanged — no ports enumerated."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert argyll_serial_exclusion_ports() == []
    monkeypatch.setattr(sys, "platform", "linux")
    assert argyll_serial_exclusion_ports() == []


class _StubSettings:
    def __init__(self, on): self._on = on
    def get(self, key, default=None):
        return self._on if key == "fast_instrument_connect" else default


def test_setting_gates_the_exclusion(qapp):
    """The Beta 'Faster instrument connection' switch turns the whole feature on
    and off: off ⇒ the environment is never touched, whatever ports exist."""
    from core.argyll_runner import ArgyllRunner
    off = ArgyllRunner(_StubSettings(False))
    assert off._serial_exclusion_value(None) is None
    assert off._serial_exclusion_value("COM9") is None      # user value untouched too
    on = ArgyllRunner(_StubSettings(True))
    # On: a user-set value is always preserved (ours is merged onto it).
    assert on._serial_exclusion_value("COM9") is not None
    assert "COM9" in on._serial_exclusion_value("COM9")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS /dev/cu.* only")
def test_macos_enumeration_returns_paths():
    # On macOS this returns whatever phantom /dev/cu.* ports exist (possibly
    # empty); every entry must be a real device path and never a USB adapter.
    for p in argyll_serial_exclusion_ports():
        assert p.startswith("/dev/cu.")
        assert "usbserial" not in p and "usbmodem" not in p
