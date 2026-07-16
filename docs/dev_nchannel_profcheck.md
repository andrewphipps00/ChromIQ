# N-channel profile accuracy check (`profcheck` for CMY+N)

ArgyllCMS's `profcheck` refuses device colourspaces beyond 4 inks — its `.ti3`
reader is hardcoded to Grey/RGB/CMY/CMYK (the "generic colorant read" in its
TTBD is unimplemented; `pval.p[]` is filled from only `ci/mi/yi/ki`). So Check &
Refine used to hard-error on a 6-ink profile with *"use RGB, CMYK or another
standard colour space"*.

`workflow/profcheck_nchannel.py` reproduces profcheck for **any** channel count
in Python, driving Argyll's `icclu` for the one thing that needs the profile:

1. **Read** the `.ti3` generically — device fields are `<REP>_<letter>` per ink
   (e.g. `CMYKOG_C … CMYKOG_G`); the fixed-name ≤4 reps (`iRGB → RGB_R/G/B`,
   `K → GRAY_K`, …) are mapped explicitly.
2. **Predict** — device → PCS through the profile's forward (A2B) table via
   `icclu -ff` (icclib handles 2..15 channels), at profcheck's intent
   (default **absolute**).
3. **Measure** — the file's XYZ→Lab (Argyll `icmD50 = 96.420288, 100, 82.490540`),
   or a spectral recompute (below).
4. **ΔE** — CIE76 / CIE94 / CIEDE2000, computed here.
5. **Emit** profcheck's exact text: `No of test patches = N`, per-patch
   `[dE] SID[ @ LOC]: <dev 8dp> -> <predLab> should be <measLab>`, and
   `Profile check complete, errors[ (CIE94|CIEDE2000)]: max. = , avg. = , RMS = `.

Because the output is byte-for-byte profcheck format, the whole
`workflow/profcheck_runner.py` pipeline — summary parse, per-patch parse,
quality grade, **refine-strip flagging** (keyed off `SAMPLE_LOC`, so it's
ink-count-independent) — and both the Guided and Manual **Check & Refine** flows
work unchanged for 6-ink profiles. No new compiled binary (unlike the gamut
helper — `icclu` is enough).

## Routing

`ProfcheckRunner.run` counts device channels (`_ti3_device_channels`): **>4**
runs the Python check in a `_NChannelWorker(QThread)` streaming the same lines;
**≤4** still uses the stock `profcheck` binary. So nothing changes for RGB/CMYK.

## Spectral options (`-i` illuminant / `-o` observer / `-f` FWA)

profcheck can recompute the patch colours from the raw **spectral** data before
comparing. Argyll's own `spec2cie` does exactly that — but it *also* refuses
>4 ink. Since it only integrates the spectral columns, `_recompute_spectral_xyz`
hands it a **relabelled RGB copy**: dummy `RGB` device set to each patch's
brightness (so FWA can find the media-white patch), the real `SPEC_*` columns,
and **all** original header keywords (`TARGET_INSTRUMENT` etc. — FWA needs the
instrument illuminant). It then merges the recomputed XYZ back by `SAMPLE_ID`.
Result: **bit-exact Argyll colorimetry + FWA, no reimplementation**. If the
`.ti3` has no spectral data, it errors cleanly rather than silently using D50.

## Validation

Line-for-line against real profcheck on RGB/CMYK (where both run): summary
`max/avg/RMS` identical and predicted Lab identical; only a 1e-6 rounding differs
in the ΔE *display*. `-i D65`, `-o 1964_10` match to 1e-6; `-f` delegates to
`spec2cie`, so it errors identically when the data carries no FWA illuminant.
Tests: `tests/test_profcheck_nchannel.py`.
