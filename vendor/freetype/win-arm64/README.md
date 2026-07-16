# Vendored FreeType — Windows/ARM64

`freetype.dll` here is a self-contained **FreeType 2.14.3** build for **Windows
on ARM64**, used only to enable the engine's vector-PDF export on that platform.

## Why it's vendored

`freetype-py` (a runtime dependency, see `requirements.txt`) ships prebuilt
wheels with a bundled native `libfreetype.dll` for Windows x64, macOS and Linux
— but **not** for Windows on ARM. There pip installs the sdist with no native
library, so `import freetype` fails and vector-PDF export is unavailable. On that
platform only, `core/freetype_bootstrap.py` adds this directory to the DLL search
path so `freetype-py` loads this DLL; everywhere else the wheel's own library is
used and this file is ignored. `ChromIQ.spec` bundles it into the frozen ARM app.

## Source

- Project: `ubawurinna/freetype-windows-binaries`
  <https://github.com/ubawurinna/freetype-windows-binaries>
- File: `release dll/arm64/freetype.dll` (FreeType 2.14.3)
- sha256: `a98b185823730cef06a4e2ae7da075e1a6a5de70060fa492aaee4025cc85487f`
- Self-contained: statically linked; needs only the system Visual C++ v14
  (Universal CRT) runtime, present on all supported Windows systems.

To update: download the matching `release dll/arm64/freetype.dll` from a new
release, replace this file, and update the version + sha256 above.

## License

FreeType is distributed under the **FreeType License (FTL)** — a BSD-style
license with a credit clause — dual-licensed with GPLv2. ChromIQ uses it under
the FTL. Attribution:

> Portions of this software are copyright © 2026 The FreeType Project
> (www.freetype.org). All rights reserved.
