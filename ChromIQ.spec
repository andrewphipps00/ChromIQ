# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for ChromIQ.

Build command:
    source .venv/bin/activate
    pyinstaller ChromIQ.spec

For a universal2 (ARM + Intel) build:
    PYINSTALLER_TARGET_ARCH=universal2 pyinstaller ChromIQ.spec

The result will be in dist/ChromIQ.app
"""

import os
import sys
import certifi
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs
certifi_where = certifi.where()
_target_arch = os.environ.get("PYINSTALLER_TARGET_ARCH") or None

# Collect all imagecodecs binaries/data: its LZW and other codecs live in
# compiled C extensions that PyInstaller won't find via static analysis alone.
_ic_datas, _ic_binaries, _ic_hiddenimports = collect_all('imagecodecs')

_we_datas, _we_binaries, _we_hiddenimports = collect_all('PyQt6-WebEngine')

# numpy 2.4+ vendors a SciPy-built OpenBLAS as `libscipy_openblas64_.dylib`
# (under numpy/.dylibs/ on macOS). PyInstaller's bundled hook-numpy.py did not
# pick it up in the v3.2.7 universal2 build, leaving Contents/Frameworks/
# missing the dylib and crashing at numpy import time. Collect explicitly so
# the dylib lands where numpy/_core/_multiarray_umath.so expects it via
# @rpath. See issue #11.
_np_binaries = collect_dynamic_libs('numpy')

# PyObjC (macOS native print dialog) — lazily-imported submodules need help.
if sys.platform == 'darwin':
    _oc_datas, _oc_binaries, _oc_hiddenimports = collect_all('objc')
    _ak_datas, _ak_binaries, _ak_hiddenimports = collect_all('AppKit')
else:
    _oc_datas = _oc_binaries = _oc_hiddenimports = []
    _ak_datas = _ak_binaries = _ak_hiddenimports = []

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[*_ic_binaries, *_we_binaries, *_oc_binaries, *_ak_binaries, *_np_binaries],
    datas=[
        ('assets',           'assets'),
        ('data/parameters.yaml', 'data'),
        (certifi_where, 'certifi'),
        *_ic_datas,
        *_we_datas,
        *_oc_datas,
        *_ak_datas,
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
        *_oc_hiddenimports,
        *_ak_hiddenimports,
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
    target_arch=_target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app_icon.icns',
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

app = BUNDLE(
    coll,
    name='ChromIQ.app',
    icon='assets/app_icon.icns',
    bundle_identifier='com.chromiq.app',
    info_plist={
        'CFBundleName':              'ChromIQ',
        'CFBundleDisplayName':       'ChromIQ',
        'CFBundleShortVersionString': '3.2.2',
        'CFBundleVersion':           '3.2.2',
        'NSHighResolutionCapable':   True,
        'NSPrincipalClass':          'NSApplication',
        'NSRequiresAquaSystemAppearance': False,
        'LSApplicationCategoryType': 'public.app-category.graphics-design',
        'LSMinimumSystemVersion':    '12.0',
    },
)
