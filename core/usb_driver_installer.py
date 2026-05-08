"""Windows-only: enumerate connected ArgyllCMS-compatible USB devices and
install WinUSB drivers via wdi-simple (libwdi)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path
from typing import NamedTuple

from core.logger import get_logger
from core.resource_path import resource_path

log = get_logger(__name__)

# VID/PID → display name for every device ArgyllCMS supports.
# Source: ArgyllCMS usb/ArgyllCMS_USB_driver/*.inf files.
KNOWN_COLORIMETERS: dict[tuple[str, str], str] = {
    ("0765", "5020"): "X-Rite Eye-One Pro (i1 Pro 1)",
    ("0765", "d020"): "X-Rite Eye-One Pro 2 (i1 Pro 2)",
    ("0765", "d0a0"): "X-Rite i1 Pro 3",
    ("0765", "d0a4"): "X-Rite i1 Pro 3+",
    ("0765", "d094"): "X-Rite ColorMunki Photo/Design",
    ("0765", "d095"): "X-Rite ColorMunki Display",
    ("0765", "d0c0"): "X-Rite i1 Studio",
    ("0765", "d065"): "X-Rite i1 Display Pro / ColorMunki Display (HID)",
    ("085C", "0200"): "Datacolor Spyder 2",
    ("085C", "0300"): "Datacolor Spyder 3",
    ("085C", "0400"): "Datacolor Spyder 4",
    ("085C", "0500"): "Datacolor Spyder 5",
    ("085C", "0a00"): "Datacolor SpyderX",
    ("085C", "0b00"): "Datacolor SpyderX Pro",
    ("085C", "0c00"): "Datacolor Spyder X2",
    ("04DB", "005B"): "Colorvision Spyder 1",
    ("0670", "0001"): "Sequel Chroma 5",
}


class UsbDevice(NamedTuple):
    vid: str        # 4-char hex, lower-case, no 0x prefix
    pid: str
    name: str
    has_winusb: bool   # True if WinUSB is already the active driver


def _wdi_simple_path() -> Path:
    # Single x64 binary — runs on both x64 and ARM64 Windows via the x64 emulation layer.
    return resource_path("assets/wdi_simple.exe")


def enumerate_connected() -> list[UsbDevice]:
    """Return connected USB devices that match the known colorimeter list."""
    if sys.platform != "win32":
        return []
    import winreg
    found: list[UsbDevice] = []
    try:
        base = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB"
        )
    except OSError:
        return []

    i = 0
    while True:
        try:
            combo = winreg.EnumKey(base, i)   # e.g. "VID_0765&PID_5020"
        except OSError:
            break
        i += 1
        parts = combo.upper().split("&")
        if len(parts) < 2:
            continue
        vid = parts[0].replace("VID_", "").lower()
        pid = parts[1].replace("PID_", "").lower()
        name = KNOWN_COLORIMETERS.get((vid, pid))
        if name is None:
            continue

        # Check if any instance already has WinUSB as its service driver.
        has_winusb = False
        try:
            dev_key = winreg.OpenKey(base, combo)
            j = 0
            while True:
                try:
                    inst = winreg.EnumKey(dev_key, j)
                    inst_key = winreg.OpenKey(dev_key, inst)
                    try:
                        svc, _ = winreg.QueryValueEx(inst_key, "Service")
                        if str(svc).lower() == "winusb":
                            has_winusb = True
                    except OSError:
                        pass
                    j += 1
                except OSError:
                    break
        except OSError:
            pass

        found.append(UsbDevice(vid=vid, pid=pid, name=name, has_winusb=has_winusb))

    return found


def install_winusb(device: UsbDevice) -> bool:
    """Install the WinUSB driver for *device* via wdi-simple (elevated UAC).

    Returns True if wdi-simple exits with code 0.
    Returns False if the user cancels the UAC prompt or the install fails.
    """
    wdi = _wdi_simple_path()
    if not wdi.exists() or wdi.stat().st_size == 0:
        log.error("wdi-simple not found or empty at %s", wdi)
        return False

    args = (
        f'--vid 0x{device.vid} --pid 0x{device.pid} '
        f'--name "{device.name}" --driver WinUSB'
    )
    log.info("Installing WinUSB: %s %s", wdi.name, args)

    # ShellExecuteExW with "runas" → UAC elevation for wdi-simple only,
    # without re-launching the full ChromIQ process as admin.
    SEE_MASK_NOCLOSEPROCESS = 0x40
    SW_HIDE = 0

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize",       wt.DWORD),
            ("fMask",        wt.ULONG),
            ("hwnd",         wt.HWND),
            ("lpVerb",       wt.LPCWSTR),
            ("lpFile",       wt.LPCWSTR),
            ("lpParameters", wt.LPCWSTR),
            ("lpDirectory",  wt.LPCWSTR),
            ("nShow",        ctypes.c_int),
            ("hInstApp",     wt.HINSTANCE),
            ("lpIDList",     ctypes.c_void_p),
            ("lpClass",      wt.LPCWSTR),
            ("hkeyClass",    wt.HKEY),
            ("dwHotKey",     wt.DWORD),
            ("hIcon",        wt.HANDLE),
            ("hProcess",     wt.HANDLE),
        ]

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize       = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask        = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb       = "runas"
    sei.lpFile       = str(wdi)
    sei.lpParameters = args
    sei.nShow        = SW_HIDE

    shell32  = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        log.info("wdi-simple: UAC cancelled or ShellExecuteExW failed")
        return False

    kernel32.WaitForSingleObject(sei.hProcess, 60_000)   # 60 s timeout
    code = wt.DWORD()
    kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(sei.hProcess)
    log.info("wdi-simple exit code: %d", code.value)
    return code.value == 0


def launch_zadig() -> bool:
    """Launch the bundled Zadig GUI as a fallback USB driver installer."""
    zadig = resource_path("assets/zadig.exe")
    if not zadig.exists() or zadig.stat().st_size == 0:
        log.error("zadig.exe not found at %s", zadig)
        return False
    import subprocess
    try:
        subprocess.Popen([str(zadig)], close_fds=True)
        return True
    except OSError as exc:
        log.error("Failed to launch zadig.exe: %s", exc)
        return False
