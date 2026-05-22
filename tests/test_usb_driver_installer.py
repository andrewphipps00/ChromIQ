"""Tests for launch_zadig()'s bundled-vs-download-page fallback.

Regression guard for the forum #148275 driver dialog: when the bundled
zadig.exe isn't present (e.g. running from source rather than a CI build),
launch_zadig() must open the Zadig download page instead of silently failing,
so the Settings dialog can tell the user what happened.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import core.usb_driver_installer as udi


def _stub_resource_path(monkeypatch, target: Path) -> None:
    monkeypatch.setattr(udi, "resource_path", lambda rel: target)


def test_launch_zadig_runs_bundled_exe(monkeypatch, tmp_path: Path) -> None:
    zadig = tmp_path / "zadig.exe"
    zadig.write_bytes(b"MZ")  # non-empty -> treated as a real binary
    _stub_resource_path(monkeypatch, zadig)

    launched: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda args, **kw: launched.append(args) or object(),
    )
    # If the bundled exe runs, we must NOT also open a browser.
    import webbrowser

    def _no_browser(url):
        raise AssertionError("browser opened despite bundled zadig.exe")

    monkeypatch.setattr(webbrowser, "open", _no_browser)

    assert udi.launch_zadig() == "launched"
    assert launched and launched[0][0] == str(zadig)


def test_launch_zadig_opens_download_page_when_missing(monkeypatch, tmp_path: Path) -> None:
    _stub_resource_path(monkeypatch, tmp_path / "does_not_exist.exe")

    opened: list[str] = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    assert udi.launch_zadig() == "download_page"
    assert opened == [udi.ZADIG_URL]


def test_launch_zadig_treats_empty_exe_as_missing(monkeypatch, tmp_path: Path) -> None:
    empty = tmp_path / "zadig.exe"
    empty.write_bytes(b"")  # 0-byte placeholder -> not a usable binary
    _stub_resource_path(monkeypatch, empty)

    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: True)

    assert udi.launch_zadig() == "download_page"


def test_launch_zadig_failed_when_browser_unavailable(monkeypatch, tmp_path: Path) -> None:
    _stub_resource_path(monkeypatch, tmp_path / "nope.exe")

    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: False)

    assert udi.launch_zadig() == "failed"


def test_i1pro_family_known(monkeypatch) -> None:
    """i1 Pro family must be in the allowlist so the driver dialog detects it.

    Regression guard for the forum #148275 report ("i1Pro / i1Pro2 not
    recognized"). Per ArgyllCMS 3.5.0 usb/ArgyllCMS.inf the i1 Pro and i1 Pro 2
    share GretagMacbeth 0971:2000, and the i1 Pro 3 / 3+ use X-Rite 0765:6009.
    Keys are lower-case hex to match enumerate_connected()'s registry reads.
    """
    assert ("0971", "2000") in udi.KNOWN_COLORIMETERS
    assert ("0765", "6009") in udi.KNOWN_COLORIMETERS
    # All keys are lower-case hex (the lookup would silently miss otherwise).
    for vid, pid in udi.KNOWN_COLORIMETERS:
        assert vid == vid.lower() and pid == pid.lower(), (vid, pid)


def test_i1pro_2000_matches_registry_combo() -> None:
    """The 0971:2000 key matches what enumerate_connected() parses from a
    'VID_0971&PID_2000' registry combo (the form Windows stores)."""
    combo = "VID_0971&PID_2000"
    parts = combo.upper().split("&")
    vid = parts[0].replace("VID_", "").lower()
    pid = parts[1].replace("PID_", "").lower()
    assert udi.KNOWN_COLORIMETERS.get((vid, pid)) == "GretagMacbeth i1 Pro / i1 Pro 2"


# Devices Argyll 3.5.0's usb/ArgyllCMS.inf actively binds the libusb driver to.
_EXPECTED_PRESENT = {
    ("0971", "2000"),  # i1 Pro / i1 Pro 2
    ("0971", "2007"),  # ColorMunki Photo/Design
    ("0765", "6009"),  # i1 Pro 3 / 3+
    ("0765", "6008"),  # i1 Studio
    ("0765", "6003"),  # ColorMunki Smile
    ("0765", "d094"),  # DTP94
    ("085c", "0100"),  # Spyder 1
    ("085c", "0a0a"),  # SpyderX2
    ("085c", "0a0b"),  # Spyder 2024
    ("04db", "005b"),  # HCFR V3.1
    ("2457", "4000"),  # Image Engineering EX1
}

# Deliberately excluded: inf-commented (5020/600a), HID-only colorimeters that
# must NOT get WinUSB (d0c0/d065/d095), and the prior table's wrong PIDs.
_EXPECTED_ABSENT = {
    ("0765", "5020"),  # Eye-One Display 3 — commented out in the inf
    ("0765", "600a"),  # D123 — commented out in the inf
    ("0765", "d0c0"),  # i1 Studio native HID — Argyll binds 6008 instead
    ("0765", "d065"),  # i1 Display Pro (HID) — must stay on HID, not WinUSB
    ("0765", "d095"),  # ColorMunki Display (HID)
    ("085c", "0c00"),  # prior table's wrong SpyderX2 PID (real is 0a0a)
    ("085c", "0b00"),  # prior table's invented "SpyderX Pro"
}


def test_table_matches_argyll_inf_inclusions() -> None:
    for key in _EXPECTED_PRESENT:
        assert key in udi.KNOWN_COLORIMETERS, f"{key} missing from allowlist"


def test_table_excludes_hid_and_commented_devices() -> None:
    for key in _EXPECTED_ABSENT:
        assert key not in udi.KNOWN_COLORIMETERS, f"{key} should not be in allowlist"


def _dev(vid: str, pid: str, has_winusb: bool) -> udi.UsbDevice:
    return udi.UsbDevice(vid=vid, pid=pid, name=f"{vid}:{pid}", has_winusb=has_winusb)


def test_unbound_targets_flags_device_that_did_not_bind(monkeypatch) -> None:
    """After install, a target still reporting no driver must be flagged.

    Reproduces the i1Studio case: wdi-simple exits 0 but the device (0765:6008)
    is still driverless, so the dialog must treat it as a failure and offer Zadig.
    """
    target = _dev("0765", "6008", has_winusb=False)
    # Re-enumeration still shows it without a WinUSB/libusb0 driver.
    monkeypatch.setattr(udi, "enumerate_connected", lambda: [_dev("0765", "6008", False)])
    assert udi.unbound_targets([target]) == [_dev("0765", "6008", False)]


def test_unbound_targets_empty_when_bind_succeeded(monkeypatch) -> None:
    target = _dev("0765", "6008", has_winusb=False)
    # Re-enumeration now shows the driver bound.
    monkeypatch.setattr(udi, "enumerate_connected", lambda: [_dev("0765", "6008", True)])
    assert udi.unbound_targets([target]) == []


def test_unbound_targets_only_considers_requested_devices(monkeypatch) -> None:
    target = _dev("0765", "6008", has_winusb=False)
    # An unrelated driverless device must not be reported as a failed target.
    monkeypatch.setattr(
        udi, "enumerate_connected",
        lambda: [_dev("0765", "6008", True), _dev("085c", "0a00", False)],
    )
    assert udi.unbound_targets([target]) == []
