# Vendor "no colour management" survey (2026-06)

How we know which job options disable driver colour management for printers
we don't own, and what was changed as a result.

## Method (no installation, system left untouched)

Apple's legacy vendor driver bundles contain the PPDs for essentially every
classic-driver printer each vendor ever shipped on macOS. They download
without owning any printer and the PPDs can be read straight out of the
package payload — nothing is installed:

```bash
curl -O https://updates.cdn-apple.com/.../HewlettPackardPrinterDrivers.dmg   # +Brother/Canon/EPSON
hdiutil attach -nobrowse -readonly -mountpoint mnt vendor.dmg
pkgutil --expand-full mnt/Vendor.pkg expanded     # extracts payload, installs nothing
hdiutil detach mnt
find expanded -ipath "*ppds*" -iname "*.gz"       # → Library/Printers/PPDs/...
```

2 032 PPDs were harvested (HP 654, Epson 828, Brother 439, Canon 111) and run
through ChromIQ's actual detector with `scripts/survey_ppd_no_cm.py`.
Everything was downloaded to a temp dir and deleted afterwards; queues,
`/Library/Printers` and the package-receipt list were snapshotted before and
verified unchanged after.

Limitation: AirPrint-only printers have no PPD (driverless IPP) — this survey
only covers printers with a classic macOS driver.

## Findings per vendor

| Vendor | No-CM lever(s) found | Coverage |
|--------|----------------------|----------|
| Epson  | `EPIJ_CMat=3` (inkjet), `EscpageMatchingMethod=5` (colour laser), `EPIJ_OSCMProf=0` (don't declare a source profile; PPD default) | 790/828; misses are mono lasers/faxes with nothing to disable |
| Brother | `BRColorMatching=None` (inkjet "Color Mode"), `BRColorAjst=CAOFF` (old colour lasers), `ColorAdjust=NONE` | 276/439; misses are mono lasers |
| HP | `HPColorMode=application-managed` (Deskjet/Officejet), `HPColorMatchingMode=ApplicationMatching` (DesignJet — its PS invocation sets `RGBColorManagement` to None), `RgbColor=None` / `HPRGBEmulation=HPRGBEmulationNone` / `HPRGBColorMode=HPRGBColorModeNone` (colour lasers), per-object trio `HPTextRGB`/`HPGraphicsRGB`/`HPPhotoRGB=None` (PS lasers — **all three must be set**) | 205/654; most misses are old inkjets whose drivers genuinely have no raw mode (ColorSmart/ColorSync/Grayscale only) and mono devices |
| Canon | none in the Apple bundle — old PIXMA PPDs expose `CNIJIntent2` with only Standard/Vivid Photo. The "No Color Correction" value `1001` exists only in Canon's own current CNIJ drivers (e.g. PRO-300). Old G-series (G6000) has no lever at all | modern Canon-site drivers only |

## Detector changes (workflow/ppd_color.py)

* New CM-option label patterns: anchored `RGB Color`, `Color Transformation`.
* New gated value label: bare `Application` / `Application Matching`.
* `vendor_no_cm_settings()` (plural) returns **every** qualifying pair, and
  both print paths apply them all — required for HP's per-object-type trio.

## The bigger find: `AP_ColorMatchingMode`

HP DesignJet PPDs carry hundreds of `cupsICCProfile` entries, and Apple's
`cgpdftoraster` applies that destination transform **even to untagged device
colour** — the vendor option can't stop it (it only controls HP's own filter
further down). No-ink filter-chain test with the Z2100 PPD: ChromIQ's
untagged PDF came back with every patch altered ((255,0,0)→(219,0,0), …);
`ColorSync=None`, `profile=None`, `ColorModel=DeviceRGB` were all ignored.

The fix is Apple's PrintCore key passed as a plain CUPS job option:

```
-o AP_ColorMatchingMode=AP_ApplicationColorMatching
```

→ 0 altered colours under the Z2100 PPD, on both the PDF and the
cgimagetopdf (TIFF) chain, and Canon PRO-300 / Epson ET-8550 stay bit-exact
with it. It is the same key the native-dialog path has always locked via
`PMPrintSettings`. All three lp paths (PS / PDF / TIFF) now send it
(`_AP_NO_CM` in `workflow/cups_printer.py`).

## Round 2 (same day): vendor-site drivers + laser bundles

A second pass added 927 PPDs from: Canon's own current CUPS drivers
(PRO-1/100/200/310/510/1100, G1000/G3000 — downloaded via Canon's
`pdisp01.c-wss.com/gdl/WWUFORedirectTarget.do?id=<base64 file id>` redirect
service; the PPD hides in the installer's `Scripts/CIJModules/CanonIJPPD.tgz`,
extracted with `pkgutil --expand` + `tar`), and Apple's Ricoh / Lexmark /
Samsung / Canon-laser / Xerox bundles.

| Vendor | Lever(s) | Notes |
|--------|----------|-------|
| Canon (current CNIJ) | `CNIJIntent2=1001` on PRO-200/310/510/1100 | PRO-1, PRO-100, G1000, G3000 genuinely lack the value (Photo Color/PRO Mode/Vivid only) |
| Ricoh | `RPSRGBcorrect=None` ("Color Setting: Off" → `(none) RCsetrgbrevision`) | 179/356; misses are mono/production engines with no CM option |
| Lexmark | `MediaColor=FalseM` — the key is misleadingly named; its *label* is "Color Correction" and Off emits `/ColorCorrection /Off` | label-based gating is what makes this safe: Xerox's `MediaColor` is literally "Paper Color" and stays undetected |
| Samsung | `SECRGBColor=Device` / `id_RGBColor=Device` (`userdict /RGBColorMode (DEVICE) put`) | "Device" added as a gated value |
| Xerox | `XRColorCorrection=None`, plus on FFPS models 12 per-object-type `XR*ColorCorrection=None` siblings — the plural API applies them all | 77/162 |
| Canon laser (UFR/PS) | none — matching options are single-value in these PPDs | |

**Epson SureColor P-series — round 3, verified.** Epson's `*_Lite_*.dmg`
"drivers" are downloader apps, but they fetch their catalog from public,
plain-text URLs (observed in the downloader's `~/Library/Caches/
com.epson.installer/Cache.db` while it ran — it was quit before any install):

```
https://files.support.epson.com/driver_updates/lst19.dat   # gen-19 catalog
https://files.support.epson.com/driver_updates/lst22.dat   # gen-22 (2025)
```

Each is `model_std_driver = https://ftp.epson.com/drivers/<real dmg>;`. The
real driver dmgs contain normal pkgs whose payload has the PPDs. Surveyed 16
PPDs covering the entire modern photo/pro line — SC-P700/P900, SC-P5300,
SC-P7300/P9300, SC-P7500/P9500, SC-P8500D, SC-T7700D, XP-15000, XP-6000/6100/
7100/8500/8600/970: **16/16 detected, all `EPIJ_CMat=3` (+`EPIJ_OSCMProf=0`)**;
the T7700D adds `EPIJ_Manu=201` ("Off (No Color Adjustment)" target preset).
The P-series inference is now verified fact.

## Re-running the survey

```bash
python scripts/survey_ppd_no_cm.py <dir-of-ppds> [...]   # one dir per vendor
```

Reports detected pairs per vendor and dumps the colour-ish options of every
PPD where nothing was found, so new vendor spellings can be added to the
regexes in `workflow/ppd_color.py` (with a trimmed-PPD regression test in
`tests/test_ppd_color.py`).
