# Bundled scanner/camera target recognition files

These `.cht` files describe **where the patches sit** on standard scanner/camera
calibration targets (IT8, ColorChecker-style, etc.). ChromIQ uses them in
**Tools ▸ Build scanner or camera profile** so it can lay its reading grid over
a scan or photo of a target you own. They contain **geometry only — no colours**;
the true patch colours come from the reference file that ships with your own
physical target (batch-specific, so it can't be bundled).

## Why these are bundled

ArgyllCMS already ships `.cht` files for these targets in its `ref/` folder, and
ChromIQ falls back to those. But several of Argyll's shipped files had incorrect
**fiducial (`F`) coordinates**, which broke registration. The versions here are
**Knut Georg Larsson's corrected files**, released with his **rectarg** project.
ChromIQ prefers these over the copies in Argyll's `ref/` when the names match.

Bundled (each **validated end-to-end** through real `scanin -F` → `colprof`,
avg ΔE ≈ 0.1–2.3):

- **HutchColor HCT** — `Hutchcolor.cht`
- **LaserSoft ISO 12641-2** — `ISO12641_2_1.cht`
- **LaserSoft DCPro** — `LaserSoftDCPro.cht`

Only files that pass that validation are bundled. Some other rectarg example
`.cht` (QPcard 202, SpyderChecker, SpyderChecker 24, CMP Digital Target-4) render
correctly in rectarg but do **not** register correctly with ArgyllCMS `scanin`
(their box pitch doesn't match the patch positions, or their `EXPECTED` list is
inconsistent), so they are intentionally **not** bundled — for those targets
ChromIQ falls back to Argyll's own `ref/` copy.

## Credit & licence

- Corrected `.cht` files: **Knut Georg Larsson** — rectarg
  (<https://github.com/soul-traveller/rectarg>).
- Derived from the recognition files distributed with **ArgyllCMS** by
  Graeme W. Gill (<https://www.argyllcms.com>).

These files are distributed under the **GNU General Public License v3** — see the
`LICENSE` file in this folder. They are bundled as data alongside ChromIQ (mere
aggregation) and do not change ChromIQ's own licensing.
