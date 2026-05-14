# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for ChromIQ — Linux (x86_64 / aarch64).

Build command (run from the repo root with venv active):
    pyinstaller ChromIQLinux.spec

Result: dist/ChromIQ/ChromIQ  (one-dir bundle)

Notes:
- No ``BUNDLE()`` — that construct is macOS-only.
- ``pyobjc`` is excluded via requirements.txt's ``sys_platform == "darwin"``
  marker, so we do not even attempt to collect it here.
- ``pycups`` is the standard Linux CUPS binding; keep it as a hidden import.
- The runtime ``QIcon`` loads ``assets/app_icon.png``; PyInstaller's Linux
  EXE does not embed an icon (Linux desktop integration uses ``.desktop``
  files separately), so we pass no ``icon=`` to ``EXE``.
"""

import os
import sys
import certifi
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

certifi_where = certifi.where()

# imagecodecs's LZW/JPEG codecs live in compiled C extensions PyInstaller
# can't find via static analysis alone — collect everything explicitly.
_ic_datas, _ic_binaries, _ic_hiddenimports = collect_all('imagecodecs')

# PyQt6-WebEngine ships extra runtime data (locales, resources) that the
# default hook on Linux sometimes misses.
_we_datas, _we_binaries, _we_hiddenimports = collect_all('PyQt6-WebEngine')

# numpy 2.4+ links against a SciPy-built OpenBLAS that auditwheel places at
# ``numpy.libs/`` (a sibling of the numpy package).  Bundle every .so we
# find so ``import numpy`` doesn't fail at runtime.
import numpy as _np_pkg
_np_binaries = list(collect_dynamic_libs('numpy'))
_np_dir = os.path.dirname(_np_pkg.__file__)
for _root, _dirs, _files in os.walk(_np_dir):
    for _f in _files:
        if _f.endswith('.so'):
            _np_binaries.append((os.path.join(_root, _f), '.'))
_np_libs_sibling = os.path.join(os.path.dirname(_np_dir), 'numpy.libs')
if os.path.isdir(_np_libs_sibling):
    for _f in os.listdir(_np_libs_sibling):
        if _f.endswith('.so') or '.so.' in _f:
            _np_binaries.append((os.path.join(_np_libs_sibling, _f), '.'))
for _candidate_pkg in ('scipy_openblas64', 'scipy_openblas32'):
    try:
        _np_binaries.extend(collect_dynamic_libs(_candidate_pkg))
    except Exception:
        pass
print(
    f"[ChromIQLinux.spec] Bundling {len(_np_binaries)} numpy/openblas entries: "
    f"{sorted({os.path.basename(p) for p, _ in _np_binaries})}",
    file=sys.stderr,
)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[*_ic_binaries, *_we_binaries, *_np_binaries],
    datas=[
        ('assets',               'assets'),
        ('data/parameters.yaml', 'data'),
        (certifi_where,          'certifi'),
        *_ic_datas,
        *_we_datas,
    ],
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtPrintSupport',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',
        'PIL.Image',
        'PIL.ImageFile',
        'PIL.ImageCms',
        'PIL.TiffImagePlugin',
        'yaml',
        'cups',
        'tifffile',
        'numpy',
        *_ic_hiddenimports,
        *_we_hiddenimports,
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChromIQ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ChromIQ',
)
