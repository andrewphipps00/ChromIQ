# gammap/nearsmth port map (P4b, issue #122)

Source: ArgyllCMS 3.5.0, `gamut/gammap.c` (2771 lines) + `gamut/nearsmth.c`
(4125 lines) + `gamut/nearsmth.h` (301 lines). Local copy:
`~/Downloads/Argyll_V3.5.0_orig/gamut/`. Licence: AGPL-3.0 (see
`__init__.py`); owner-approved combination with GPL-3.0 ChromIQ.

## Already banked

- `weights.py` — pweights/sweights tables + PSMOOTH + XVRA, extracted
  programmatically (gammap.c L195–494). Struct layout documented there
  from nearsmth.h `gammapweights`.
- Warp fit substitute: `workflow/profile_engine/forward_model._grid_solve`
  (equivalence to rspl measured: 0.23 ΔE held-out, issue #122 iteration 4).
- Gamut surfaces: `gamut_map.destination_surface_lab` /
  `source_surface_from_profile` provide the point clouds; the port needs a
  triangulated `gamut` equivalent (radial bins exist; nearsmth wants
  nearest-point + normal queries — implement via the existing radial tables
  plus a k-d nearest lookup over the cloud).
- Validation rig: second-oracle triples (issue #122 comments) — build
  colprof with any `-S` source on a real `.ti3`, sample realized maps;
  port must reproduce ≤ 0.5 ΔE median with **no** colprof at runtime.

## To port, in dependency order (source line refs)

1. **nearsmth.c primitives** — `spow`/`spow3` (L346–361), `wdesq`
   (L126–198, the weighted ΔE² with sum power), `diffLChsq` (L200–252),
   `aerrf` (L364–410, absolute error incl. white/grey/black L-dominance
   blending — consumes the `absolute` block of the weight table).
2. **Cusp/twist machinery** — `init_ce`/`comp_ce`/`inv_comp_ce`
   (L340–342, defined ~L1550–1900): cusp-aligned rotation/expansion of
   source colours (consumes the `cusp align` block; the twist power).
3. **comperr** (L254–338): the composite per-guide error — absolute +
   radial + depth compression/expansion terms.
4. **optfunc1 (+ optfunc2)** (L412 ff): the per-guide-point objective
   driven through Argyll's powell/conjgrad; port with
   `scipy`-free Nelder–Mead or the damped-Newton pattern already used in
   `b2a._gauss_newton` (validate per point against printed reference
   values from an instrumented Argyll build if divergence appears).
5. **near_smooth()** (the main entry, ~L2400–4100): guide-vector setup
   (XVRA vertex expansion), iterative optimisation with neighbour
   smoothing (the `relative` weight block: radius L*/H*, degree), final
   guide list. This is the core and the bulk of the effort.
6. **gammap.c top level** (~L700–1600): grey-axis mapping (white/black
   point align modes `gmm_BPadpt`/`gmm_bendBP`/`gmm_clipBP` L835–880),
   L-curve construction, call into near_smooth, rspl warp fit of the
   guide displacements (substitute maths-A fitter, smoothing = PSMOOTH),
   plus the `-t/-T` intent-code → weight-table/param selection.

## Wiring plan once validated

`gamut_map.build_mapped_b2a`: replace `fit_colprof_mappers` (oracle) and
the closed-form family with `gammap_port.gammap.map_for(intent, src_cloud,
dst_cloud, weights)` — one code path for RGB/CMYK **and** +N surfaces; the
oracle rig stays as the CI cross-check.

## Validation checkpoints (each stage, no guessing)

- primitives: unit vectors vs hand-computed values from the C expressions.
- comp_ce: cusp rotation of known probes vs an instrumented trace.
- near_smooth: guide displacements vs oracle realized maps on ET-8550
  (≤ 0.5 median target), sRGB + ClayRGB sources, both intents.
- end-to-end: engine build with port vs colprof build — realized maps at
  250 real-content probes ≤ the 0.5/1.65 floor; then a 6CLR build sanity
  (smooth tables, sips/iccdump clean, neutrals monotone).

## Stage-2 working notes (init_ce/comp_ce, from reading L596–1105)

- `src_adj[]` (L606–625): obfuscated anti-tamper canary; the log-sum
  resolves to exactly **1.0** (computed) — port as constant 1.0 with a
  comment; it scales the rotation target white L=100 (i.e. no-op).
- `init_ce` structure: per side (src=0/dst=1): (a) white/black points from
  the gamut (`getwb`), fallback W=100/K=0/grey=50 with `donaxis=0`;
  (b) rotation matrix `rot[sd]` mapping black→white segment onto 0→100 L
  axis (`icmVecRotMat(m, s1, s2, t1, t2)` maps segment s1–s2 to t1–t2:
  rotation+translation, icclib icm.c — port as standard rigid transform);
  `irot` = inverse; (c) grey = blend(white, black, param) with param =
  cusp-average-L normalised (docusp) else 0.5; (d) per cusp k∈0..5:
  rotated Lab (`cusp_lab`), LCh (`cusp_lch`); (e) per cusp pair (k, k+1):
  plane through (grey, cusp_m, cusp_k) → `cusp_pe` (light/dark cone
  decision); barycentric matrices `cusp_bc[sd][k][n]` with columns
  (cusp_k−grey, cusp_m−grey, {white|black}−grey), transposed; src side
  inverted. n=0 uses white ([6+0]... note: comment says [7]&[8] but code
  uses index 6+n → n=0 white[6], n=1 black[7]).
- `comp_ce` (L842–974): out=in; if wt: cw_l/c/h from cusp-align block,
  ctw=twist power, ccx=chroma expansion. Rotate input by rot[0]; find hue
  segment k (via cusp_lch hue angles), light/dark side n via plane
  distance sign (cusp_pe); barycentric coords b = cusp_bc[0][k][n] · (p −
  grey_src); apply per-component weights: scale barycentric components by
  cusp weights (cw_*) toward destination cusp frame; chroma expansion ccx
  on the cusp-plane components with twist power ctw shaping how alignment
  fades toward neutral; reconstruct p' = cusp_bc[1][k][n]ᵀ · b + grey_dst;
  un-rotate via irot[1]. EXACT formulas still to transcribe from
  L860–974 — read before writing cusps.py.
- `comp_naxbf` (L974–1010): neutral-axis blend factor; `comp_lvc`
  (L1010–1105): L-value-of-cusp measure. Read fully before port.
- icm helpers needed: icmVecRotMat, icmPlaneEqn3/icmPlaneDist3,
  icmInverse3x3/icmTranspose3x3, icmLab2LCh, icmBlend3 — all standard
  geometry; write numpy equivalents in gammap_port/geom.py with tests.
