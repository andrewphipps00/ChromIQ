# Bundled scanner/camera target recognition files

These `.cht` files describe **where the patches sit** on standard scanner/camera
calibration targets (IT8, ColorChecker-style, etc.). ChromIQ uses them in
**Tools ▸ Build scanner or camera profile** so it can lay its reading grid over
a scan or photo of a target you own. They contain **geometry only — no colours**;
the true patch colours come from the reference file that ships with your own
physical target (batch-specific, so it can't be bundled).

## The patch-area-corner convention

The real printed targets **have no fiducial marks** — just the patch grid, and
sometimes a single dot (rectarg *adds* fiducials when it renders a preview, which
is why some third-party `.cht` fiducials don't match the real sheet). So every
file here sets its **`F` line to the patch-area bounding box** (top-left,
top-right, bottom-right, bottom-left of the whole patch grid). You place ChromIQ's
reading grid on the **visible corners of the patch block** — no invisible
fiducials to hunt for — and it works the same at any scan resolution.

## Why these are bundled

ArgyllCMS ships `.cht` files for these targets in its `ref/` folder, but several
had fiducial coordinates that don't match the printed sheet, and a few
third-party "corrected" copies broke the box grid outright (box pitch too small,
so patches cluster or overlap). Each file here was **rebuilt to the patch-area-
corner convention above and validated end-to-end** through the real ArgyllCMS
`scanin -F`, at 100 / 200 / 300 / 600 dpi (worst per-patch registration error
0.0 — see `tests/test_scanner_multidpi.py`). ChromIQ prefers these over the
copies in Argyll's `ref/` when the names match.

Bundled:

- **HutchColor HCT** — `Hutchcolor.cht`
- **LaserSoft ISO 12641-2** — `ISO12641_2_1.cht`
- **LaserSoft DCPro** — `LaserSoftDCPro.cht`
- **QPcard 202** — `QPcard_202.cht`
- **SpyderChecker** — `SpyderChecker.cht`
- **SpyderChecker 24** — `SpyderChecker24.cht`
- **CMP Digital Target-4** — `CMP_Digital_Target-4.cht`

The last four had earlier "corrected" copies that misregistered with `scanin`
(box pitch not matching the patch positions, an undersized fiducial, or an
inconsistent `EXPECTED` list). Rebuilding them onto the patch-area-corner
convention — with the correct box geometry — fixes all four.

## Credit & licence

- Corrected `.cht` files: **Knut Georg Larsson** — rectarg
  (<https://github.com/soul-traveller/rectarg>).
- Derived from the recognition files distributed with **ArgyllCMS** by
  Graeme W. Gill (<https://www.argyllcms.com>).

These files are distributed under the **GNU General Public License v3** — see the
`LICENSE` file in this folder. They are bundled as data alongside ChromIQ (mere
aggregation) and do not change ChromIQ's own licensing.
