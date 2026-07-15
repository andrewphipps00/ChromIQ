"""Port of ArgyllCMS's gamut mapping algorithm (gammap/nearsmth) — P4b, #122.

LICENCE NOTICE
--------------
This package is a translation of algorithm code from ArgyllCMS 3.5.0
(``gamut/gammap.c``, ``gamut/nearsmth.c``), Copyright Graeme W. Gill,
licensed under the GNU Affero General Public License v3 (AGPL-3.0).
This translated module is therefore likewise licensed **AGPL-3.0**.
ChromIQ as a whole is GPL-3.0; conveying the combination is permitted by
GPL-3.0 §13 / AGPL-3.0 §13. Full credit to Graeme W. Gill — the algorithm
design and its tuning are his work.

Port scope and status (see issue #122, P4b plan):

* ``weights.py`` — DONE: the perceptual/saturation intent weight tables and
  smoothing constants, extracted programmatically from gammap.c.
* ``portmap.md`` — DONE: the function-by-function port map with source line
  ranges, dependencies and validation checkpoints.
* ``nearsmth.py`` — TODO: the guide-vector optimiser (nearsmth.c) — the
  algorithmic core.
* ``gammap.py`` — TODO: the top-level flow (gammap.c ~L700–1600): grey-axis
  alignment, white/black point mapping, guide generation via nearsmth, warp
  fit (ChromIQ substitutes the issue-#122 maths-A fitter for rspl —
  equivalence measured at 0.23 ΔE held-out, below colprof's own 0.50
  build-to-build noise).

Validation contract (must pass before this replaces anything): reproduce
colprof's realized perceptual mapping on the second-oracle triples to
≤ 0.5 ΔE median *without* running colprof; then the identical code runs on
multi-ink destination surfaces where no oracle can exist.
"""
