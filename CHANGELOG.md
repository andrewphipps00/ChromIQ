# Changelog

## v1.2.0
### Added
- **Smart ti2 loading** in Print and Measure tabs: when a `.ti2` file is loaded from outside the working folder, a copy dialog guides the user to name and import the chart files. When the file is already in the working folder, the user can choose to continue printing as-is or use it as the base for a new profile.
### Improved
- **Profile quality grading** now accounts for peak ΔE as well as average ΔE. A profile with an excellent average but high-error outlier patches is now graded accordingly, and the explanation tells the user which metric is limiting the grade.
- **Guided refinement** recommendation logic is now patch-based: the "start over" recommendation is only triggered when more than 50 % of individual patches exceed the threshold, not when 50 % of strips are flagged. This avoids false "start over" recommendations on small charts with a handful of outlier patches.
- Log file moved to `~/Library/Logs/ChromIQ/chromiq.log` (standard macOS location). The app no longer creates a `ChromIQ` folder in the user's home directory on launch.
### Fixed
- Per-patch profcheck results were sometimes lost due to a QProcess output-buffer race condition. The fix ensures all output is drained before the process finish callback runs.
- The profile quality assessment popup was not shown when `profcheck` exited with a non-zero code (its normal exit behaviour when errors are found).
- An unhandled exception in the quality report file-write no longer silently prevents the assessment dialog from appearing.

## v1.1.6
### Fixed
- A3 Portrait is now hidden in guided mode when i1Pro / i1Pro 2 / i1Pro 3 is selected, matching the existing behaviour for i1Pro 3 Plus. A3 Landscape is shown and selected automatically instead.

## v1.1.5
### Fixed
- i1Pro 3 Plus patch capacity was incorrectly assumed identical to the regular i1Pro. All paper sizes have now been measured with `-i3p` and the database updated (e.g. A4: 504 → 108, A3: 735 → 153). Charts for this instrument will now correctly fill the page.
- A3 Portrait is hidden in guided mode when i1Pro 3 Plus is selected — it yields only 153 patches vs 225 on A3 Landscape, so the landscape variant is offered and selected automatically.
### Improved
- Guided mode now adds grey-axis patches (`-g`) scaled to total patch count: `max(8, total // 30)`, capped at 64. Previously grey patches were always disabled.
- White and black patches (`-e`, `-B`) in guided mode now scale with page count: base + (pages − 1) × 2 per type.

## v1.1.3
### Fixed
- Tooltip (ⓘ) and settings (⚙) icons now render crisp on Retina / HiDPI displays instead of appearing blurry.

## v1.1.2
### Improved
- The universal DMG now runs **natively on both Apple Silicon and Intel Macs** (universal2 fat binary). Previously it was arm64 only and required Rosetta on Intel machines.

## v1.1.1
### Improved
- macOS title bar now matches the dark app theme.
- Check & Refine: re-measurement threshold is now user-editable.
- Check & Refine: the resume option is hidden when no matching `.ti3` file exists.
- Tooltip buttons remain clickable even when their parent panel is collapsed.
### Fixed
- Button heights and vertical alignment corrected throughout the UI.

## v1.1.0
### Added
- **Check & Refine tab** — run `profcheck` on a finished profile, see per-patch ΔE results, and selectively re-measure only the worst patches with guided strip-by-strip instructions.
### Improved
- File open dialogs now show only relevant file types and include sidebar shortcuts for quick navigation.

## v1.0.3
### Fixed
- Pink / magenta screen artifact on Apple Silicon during measurement (disabled native file dialogs).
- App now auto-detects ArgyllCMS on launch and shows a clear setup guide if it is not found.
- Wrong-strip dialog now appears when chartread detects a mismatch during measurement.
- Unexpected colour-response warning dialog added (high ΔE on a known patch).
- "No instrument detected" dialog shown at measurement start instead of silent failure.
### Added
- Resume measurement flag (`-r`) exposed in measurement options.

## v1.0.2
### Added
- Calibration prompt dialog when chartread asks to position the instrument.
- Navigation instructions popup shown after instrument calibration completes.
- Completion dialog when all stripes have been successfully measured.
- Retry dialog on strip read failure.
### Improved
- Measurement settings are disabled while chartread is running to prevent accidental changes.

## v1.0.1
### Added
- Initial public release as a distributable DMG.
- Resolved macOS App Translocation crash (app must be run from /Applications).
- PyQt6 compatibility fix for macOS 15 Sequoia.
