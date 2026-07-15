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

### comp_ce exact algorithm (L842–974, transcribed — ready to code)

1. rotate `in` by rot[0] → lab; LCh.
2. hue segment: find c0 with cusp_lch[0][c0].h ≤ h < cusp_lch[0][c1].h
   (wrap +360 when h1<h0); c1 = (c0+1)%6.
3. light/dark: ld = 0 if icmPlaneDist3(cusp_pe[0][c0], lab) ≥ 0 else 1.
4. bb = cusp_bc[0][c0][ld] · (lab − cusp_lab[0][8]).
5. tww = min(|bb0+bb1|, 1); ccx = 1 + (ccx−1)·tww;
   tpw = 1 if ctw ≤ 0 else tww^ctw; cw_l *= tpw; cw_h *= tpw; cw_c *= tpw.
6. mlab = cusp_bc[1][c0][ld] · bb + cusp_lab[1][8]; mlch = LCh(mlab).
7. olch = LCh(rot[1] · in) (unchanged source in dest-aligned space).
8. blends: mlch.L = cw_l·mlch.L + (1−cw_l)·olch.L;
   mlch.C = cw_c·mlch.C + (1−cw_c)·olch.C;
   **hue uses cw_c as well** (mlch.h = cw_c·mlch.h + (1−cw_c)·olch.h,
   after same-side ±360 adjustment; wrap ≥360) — cw_h is used ONLY in the
   activation test + tpw scaling. Argyll quirk — replicate EXACTLY.
9. mlch.C *= ccx; out = irot[1] · Lab(mlch).
Gate: only when docusp and (cw_l>0 or cw_c>0 or cw_h>0 or ccx>0); else
out = in.

## Stage 4/5 status + near_smooth flow skeleton (offsets rel. L1809)

Banked so far: gamutsurf.py (radial/nradial/vector_isect substrate, sphere-
validated), xweights.py, error.py, cusps.py, geom.py, primitives.py.

near_smooth(sc_gam, si_gam, dc_gam, xwh[14], gamcknf/gamxknf knees,
usecomp/useexp, xvra, mapres, mapsmooth, …) phases (offset = line−1809):

+47 compat check · +58 opts setup · +64 init_ce · +69 alloc guides ·
+76 src_gam = image∩colorspace (skip: engine has no separate image gamut,
si == sc) · +109 dst_gam = compression target: intersection of src and
dest (or knee-expanded for expansion; gamcknf) — PORT NOTE: engine uses
dest cloud directly for compression-only v1, knees later ·
+164 create guide list (null mapping): vertices of src_gam (+ xvra extra) —
each guide: sv (source point), wt (interp_xweights at sv), flags in/out ·
+249 early-out if nothing to map · +259 per-point weights ·
+282 cusp-rotated mapping setup (comp_ce per point → _sv raw, sv rotated) ·
+329 W/B blend factor (comp_naxbf → naxbf, pins W/B) ·
+336 m3d: per-point 3D→2D tangent frame at radial surface point
(optimisation runs in the 2D tangent plane) · +350 neighbour lists within
relative-smoothing radius (r.rdl/r.rdh scaled, on normalised surface) ·
+575/+579 PASS 1: per point, powell 2D → weighted-nearest point wn/aodv
(optfunc1/1a minimising aerrf to source) · +702 PASS 2: per point,
powell 2D → nrdv (optfunc2 minimising comperr incl. depth) · then
iterative smoothing loop (it/codf damping): neighbour-averaged relative
targets, re-optimise, mxmv stop threshold · +787 range expand · +851
inward correction vectors · +1077 fine-tune vs smoothing side effects ·
+1273 depth compensation · +1563 restore non-cusp-rotated sv ·
+1581 sub-surface points · +1937 grid surface points (surfpnts).

**Port strategy for the engine (documented deviation, gates decide):**
compression-only v1 (printer gamuts ⊂ typical source), guides = dest-
projected optimum per passes 1–2 with the neighbour-smoothing iteration;
sub-surface/grid points replaced by the maths-A warp fit over guide
displacements (gammap.c does exactly this via rspl with PSMOOTH).

## Gate status + measured diagnosis (2026-07-15, end of first assembly)

Validation runs (ET-8550 AdobeRGB oracle, 200 real-content probes,
port-vs-colprof realized perceptual):
- assembly v1 (pre-align + naxbf-scales-vector): 4.96 / 11.96 / 19.98
- naxbf fixed to smoothing-only + deep-core anchors: 5.03 / 12.07 / 19.97
- raw-source guides (no pre-align): 6.73 / 14.89 / 22.18 ← worse: the
  warp must then carry the W/B alignment; revert consideration pending.
- guide targets themselves vs colprof at guide sources: median 7.6 —
  the gap is IN THE GUIDES, not the warp.

Identified structural gaps to close (in this order, each with an
instrumented check — no more blind knob-turning):
1. **gamcknf knee surface is absent**: gammap.c maps to a knee-adjusted
   compression target (dst expanded/blended by gamcknf per intent — read
   gammap.c intent table entries for gamcknf/gamxknf values), NOT the raw
   dest surface. All guide radii are systematically off without it.
2. depth ratios once-per-iteration instead of per objective evaluation.
3. The C's second pass optimises from the pass-1 point with the FULL
   comperr including relative-error terms to neighbour targets inside the
   objective (my loop applies smoothing outside the objective).
4. Fine-tune + evector correction phases (+1077, +1273) unported.
5. Instrumentation plan: modify a local Argyll build to print guide
   (sv, dv) pairs from near_smooth (gammap.c GAMMAP_DEBUG / dump flags
   exist), diff against the port's guides point-by-point — replaces
   probe-level guessing with per-stage ground truth.

Shipping behaviour remains: oracle path (0.78/0.59 = inside colprof's own
noise) for everything colprof can build; proxy-anchored family for +N.
The port stays unwired until the ≤0.5 gate passes.

## BREAKTHROUGH TOOL + gate history (2026-07-15, late)

**Ground truth without compiling Argyll**: `colprof -P` writes
`gammap_p.x3d.html` / `gammap_s.x3d.html` containing an IndexedLineSet of
the ACTUAL guide vectors (2,891 for the ET-8550/ClayRGB case). Parse:
coordIndex pairs (i, j, -1), point list newline-separated triples, axes
(x,y,z) = (a, b, L−50). Extraction script pattern in the session notes;
saved once at scratchpad/guides_src.npy/guides_dst.npy.

Measured facts from those guides:
- 66% of colprof's guide targets land >2% INSIDE the intersection surface
  (median nradial 0.926) — net knee/sub-surface effect.
- Port guides matched at sources: length agrees (10.0 vs 10.7 median),
  direction mostly agrees (cos median 0.973), residual concentrated in
  saturated midtones (L 18–68, C 45–100) → knee zone.
- Gate history: 4.96 → 5.03 (naxbf fix) → 6.73 (no pre-align) → 6.08
  (intersection target + null guides) → 6.80 (first knee sub-vector
  attempt). Interactions unattributable at probe level.

**Next-session methodology (mandatory)**: validate STAGE BY STAGE against
the extracted guides — (1) match the guide-source vertex sets; (2) diff
pass-1 aodv against… (needs pass-level dumps: consider `colprof -P` at
several stages via nearsmth's SHOW_* defines if compiling, else fit
sub-models); minimum: tune the sub-surface/knee composition until the
port's NET map reproduces the 66%-inside distribution and the per-guide
targets ≤0.5 median, THEN probe-level gate. No composite probe-level
tuning — it measurably degrades (see history).

## Instrumented campaign results (2026-07-15, smthdump)

Built Argyll's own smthtest from source (deps: gamut/nearsmth/rspl/cgats/
vrml/icc.c/numlib + CoreFoundation; -DUNIX, NO -DNT) → `smthdump` variant:
colprof pweights + perceptual call (gamcknf 1.1, useexp 0, xvra 3.0,
mapres 29) dumping per-guide sv/dv/aodv/drv text. Ground truth: 26,850
records on ClayRGB1998.gam × pdiag.gam (compile line + harness in
scratchpad/argbuild, regenerate as needed).

Stage diffs (port vs Argyll internals, SAME inputs — .gam vertices,
header cusps/wb):
- drv radial on binned substrate: 0.82 median → **TriSurface (exact
  Möller–Trumbore over .gam triangles, batched): 0.001 median** —
  substrate divergence eliminated; intersection = per-ray min(src,dst).
- PASS-1 aodv on tri isect surface: **0.006 median** (95% 1.1 = rare
  multi-minima) — weights/cusps/aerrf/optimiser VALIDATED.
- Next: final-dv diff (pass 2 + iteration incl. knee); then GammapMapper
  consumes .gam-equivalent surfaces (engine generates its own vertex
  clouds + triangulation OR uses iccgamut at build time for RGB/CMYK and
  own mesh for +N), wire, probe gate, suite, UI task #43.

## Stage-4/5 diff results (instrumented, same inputs)

- PASS-1 aodv: **0.006 median** — EXACT (weights/cusps/aerrf/optimiser
  proven).
- Argyll's loop contribution: |dv−aodv| median 2.44 / 95% 9.1 — the
  iteration phases are worth exactly this much.
- My simplified loop output vs their dv: 2.87 median — WORSE than
  stopping at aodv. Conclusion: port the loop phases literally (pass-2
  objective with in-loop depth, evector rspl correction (+851), fine-tune
  (+1077), depth compensation (+1273)), each validated by adding printf
  dumps to smthdump at that phase. All tooling in place (compile line
  above; add dumps to the smthdump.c copy, not the pristine source).

## VECADJ loop ported + validated (2026-07-15)

- smthdump now dumps nrdv too → decomposition: pass-2 ≈ no-op for this
  pair (|nrdv−aodv| 0.012 median); ALL loop movement is the smoothing
  stages (|dv−nrdv| 2.40 median / 95% 9.09).
- Faithful VECADJ port (build_neighbours + vecadj_loop in nearsmth.py):
  neighbour ellipse metric with per-point radii + opposite-hue exclusion +
  ×1.5 growth (≥8), smoothstep weights normalised; nscale = ddev/sdev with
  the C's guards; per pass: dav mixes dv-L (not iterated) with anv-a/b;
  rdsm = 1−sqrt(dsm) blend; clip against FULL dest along evector
  direction; naxbf pinning blend. Validated on all 26,850 guides with
  Argyll's nrdv as input: **0.909 median / 3.84 95%** vs their final dv
  (keeping nrdv = 2.40). evect_fn is still the cvect-essence
  approximation (toward neutral at own L) — the C fits an rspl of
  directions to a SHRINK=5 shrunk gamut via optfunc1a.
- REMAINING to gate: (1) RSPLPASSES stage (L3150–3360): per pass fit
  map rspl (_sv→anv, mapsmooth), evaluate at _sv, evect-projected error
  clen vs tdst, local weighted-max correction with gain schedule
  (icgain 1.4 → fcgain 0.5·, expansion gain × wt.f.x, tt=it/(P−1)),
  rext target set on it 0 (RSPLSCALE 1.8·maxext); adjust anv. Port with
  WarpMapper playing the rspl role. (2) exact evect via shrunk gamut.
  (3) find tdst setting (grep) — the aim point of corrections.

### RSPLPASSES full transcription (L3100–3360) — ready to implement

Setup: per point, if sv AND dv inside FULL dest → tdst=dv, nott=1 (never
tuned); else evect=evectmap(dv) normalised, tdst = vintersect2(dest, evect,
from dv) if sane (dist ≤ nearest+5) else nearest-on-dest. coff=0, rext=0.

Per pass it∈0..3 (RSPLPASSES=4, mapsmooth=1.0, GAMMAP_RSPLAVGDEV):
1. fit rspl `map` over (_sv → anv) at mapres;
2. per point: temp = map(_sv); evect = evectmap(temp) norm;
   clen = evect · (tdst − temp);
3. per point: maxext = max_j(rw_j·(clen_j − (−20))) + (−20) over
   neighbours (rw = UNnormalised weights!); it==0: rext += maxext if
   rext ≤ 0 else RSPLSCALE(1.8)·maxext;
4. tpoint = tdst + rext·evect; gains icgain=1.4, ixgain=wt.f.x·1.4,
   fc=0.5·ic, fx=0.5·ix, tt=it/3, cgain/xgain lerp, xgain=0 for it>0;
   gain = cgain if rext>0 else xgain;
   cvect = gain·(tpoint − temp); coff += cvect;
5. smooth corrections: fit rspl over (dv → coff) [gpnts p=dv v=coff...
   verify at L3280-3320: p=dv, v=coff accumulated]; then per point:
   coff = map2(dv) (filtered), anv = dv + naxbf·coff (L3325: cp.v scaled
   by naxbf then anv = dv + cp.v; RSPLUSEPOW off).
Final guides: dv_final = anv after last pass.
Port: WarpMapper (value-field mode: target = train + field) plays both
rspl roles; ~2 fits × 4 passes on 26k pts, grid 13–17³.

### RSPLPASSES first implementation: 1.089 (VECADJ-only: 0.909) — fix list

Slightly WORSE than VECADJ alone. Known deficiencies to fix (in order):
1. build_neighbours must ALSO return the unnormalised smoothstep weights
   (C's nd[].rw) — maxext uses rw, I passed normalised w.
2. evect_fn is still the neutral-axis approximation; RSPLPASSES leans on
   evect hard (tdst + corrections along it) → port the shrunk-gamut
   (SHRINK=5, doshrink along cvect) + optfunc1a weighted-nearest evector
   construction + fit the direction field (WarpMapper on directions).
3. Per-pass instrumentation: extend smthdump to print temp/clen/rext per
   iteration (add printfs inside the RSPLPASSES loop) → diff each pass.
4. WarpMapper grid 13/λ0.01 as rspl stand-in — calibrate against their
   mapres=29/mapsmooth=1.0/avgdev fit (their smoothing level differs; can
   be measured directly: fit warp on (sv→their dv), compare map(_sv) vs
   their per-pass temp dump).
Operating point meanwhile: VECADJ-only output (0.909 median).

## Session results: VECADJ validated · final-fit recipe · ROOT CAUSE of e2e gap

- VECADJ chain now at **0.518–0.536 median vs their V** (frame fix: loop
  runs on CUSP-MAPPED source; exact evector construction: cvect+doshrink
  (icmNormalize33 = from `in` toward `p2` by len) + weighted-nearest on
  shrunk gamut, cos 0.993 vs their dumped evectors; neighbour count
  matches their verbose output exactly: 829.72).
- SampledSurface: TriSurface radial field sampled 720×360 → tri accuracy
  at table speed (pass-1 0.038, vecadj 73 s full-size).
- smthdump now dumps S-records: sv2/dv2/sd3/w2/w3/vflag per guide.
  gammap.c final rspl rows (L1620–1746): (sv→blend(sv,dv,gamcpf)) w1=1.01;
  (sv2→blend(sv2,dv2,cpexf)) w2; (sd3→sd3 IDENTITY) w3 — colprof's own
  interior anchors. Perceptual gamcpf=1.0 → div=dv.
- **E2E gap root cause found**: even with THEIR guides + sub-rows the
  realized-probe diff stays ~4.2 → the missing piece is gammap.c's
  PRE-TRANSFORM: grey-axis rotation + 1-D L map applied to the source
  BEFORE near_smooth (its scl_gam input is pre-mapped; my smthdump run
  used raw gamuts — consistent for guide diffs, but the realized map =
  Lmap∘rot∘nearsmth∘fit). Port gammap.c L700–1200 next: sswp/ssbp same-L
  interpolants, dr_cs = greymf blend, fawp/fabp clip to dest, wrot
  white-point rotation, bendBP branch per intent (perceptual bph from
  xicc.c), lpnts grey L-map (1-D rspl, smoothing 5.0 — fit with 1-D
  maths-A), then scl_gam = transformed source; re-run the whole validated
  chain on that; full map = greyL/rot → warp(rows recipe).
- Validation harness for the E2E gate: gamut/maptest.c is an existing
  new_gammap harness — compile like smthtest if a full-gammap dump is
  needed; else colprof -P net guides + realized probes as before.

### gammap.c grey-axis + L-map port refs (final block)

- L700–1000: sswp/ssbp same-L interpolants; dr_cs = greymf blend of dest
  vs source cs points; fawp/fabp clipped to dest via vector_isect; wrot
  white rotation; bendBP/clipBP/BPadpt branches per gmi->bph; equal-length
  ssbp scaling (dvl/svl); grot/igrot = icmVecRotMat pairs (rotation FIRST
  on all source points).
- L1000–1180: L-curve targets swL/dwL/sbL/dbL via glumwcpf/glumwexf/
  glumbcpf/glumbexf blends; revrspl swap for symmetry; lpnts: endpoints
  weight 10, USE_GLUMKNF knee points (cppos 0.50, kpwpos 0.30, kpbpos
  0.15, knee via glumknf) → grey rspl fit smoothing 5.0 (1-D maths-A
  equivalent); s->map final 3-D fit smoothing `smooth` over the row
  recipe; full map(x) = grey-L(rot(x)) then 3-D map (verify exact
  composition order in gammap's domap/interp — read gammap.c interp fn).
- Perceptual gmi values: xicc.c entry near L2280 (gamcknf=1.1 block) —
  read greymf/glumwcpf/glumbcpf/glumknf/bph exact values next session
  (grep output banked below in git history).
- NEXT-SESSION ORDER: port grey-axis+Lmap → scl_gam transform → rerun
  validated chain (smthdump inputs must ALSO be scl_gam-premapped for
  strict comparison: generate transformed .gam via gamio write or feed
  premapped verts to smthdump? simpler: e2e gate directly vs colprof
  realized probes) → row-recipe fit → gate → wire → suite → UI #43.

## Grey 1-D rspl fit ported EXACTLY (2026-07-15)

- PCHIP interpolant was WRONG: it honoured the weak knee anchors
  (w 0.5–2.25) as hard constraints; Argyll's smoothing-5.0 rspl nearly
  ignores them (their curve ~the endpoint line with a soft knee).
  Symptom: port neutrals L10→8.96 vs colprof realized L10→14.4.
- Ground truth harness: `scratchpad/argbuild/greyfit.c` — calls Argyll's
  own new_rspl(1,1)+fit_rspl_w exactly as gammap.c L1160–1180 (il −1..101,
  gres 256, ol 0..100, smooth 5.0, avgdev 0.005), lpnts on stdin, dumps
  f(x) x=−1..101/0.25. Compile: like smthdump minus gamut/nearsmth
  (needs plot/vrml.c cgats icc.c for rspl/gam.c symbol).
- fit_rspl_w 1-D semantics transcribed from rspl/scat.c (SMOOTH2 undef →
  table opt_smooth; V17 2nd-order):
  E(u) = Σ_n w_n(Σ_j φ_j(x_n)u_j − y_n)² + cw·Σ_i(u[i−1]−2u[i]+u[i+1])²,
  cw = smooth · 10^lsm · vw · (gres−1)⁴/(gres−2); vw = output range
  (100, the "incorrect but built into the tables" d.vw scale);
  lsm = log-space bilinear from smf[0] table over (nc=ndp, ad=avgdev);
  data rows multilinear, k_n = cow.w raw, values unnormalised; curvature
  = pure second differences (ipos NULL). Multigrid is just the solver —
  direct solve of the final objective matches.
- Validation (3 lpnt sets vs greyfit dumps): max |diff| 0.078 / 0.036 /
  0.008 — ZERO fitted constants. Ported into GreyAxis._fit_curve.
- adjust1_wb_func (gammap.c L1183+): after the fit, linear rescale so
  f(sbL)=dbL, f(swL)=dwL exactly — ported into GreyAxis.__init__.
- GammapMapper (gammap.py) now assembles the WHOLE pipeline as the
  shipping class; e2e harness = scratchpad/argbuild/e2e_gate.py (uses
  cached surface tables keyed by grey-curve tag; run from repo root).
  Assembly-faithfulness check: class reproduced the heredoc's 3.65
  (3.63) before the curve fix. Grey-curve fix: 3.63 → 3.44 (neutrals
  now match the grey curve; residual is elsewhere — see below).

## ROOT CAUSE #2: colprof maps in Jab, not Lab (2026-07-15)

- xicc.c L2030: USE_CAM (default build) → perccas = 0x2 → perceptual
  gmi->usecas = 0x2 = CIECAM02 Jab. profout.c: intentp =
  icxPerceptualAppearance, intento = icxAppearance — the gamuts handed
  to new_gammap AND all mapped colours are Jab, converted via cam02 with
  xicc_enum_viewcond(x, vc, -1, …) profile-derived DEFAULT viewing
  conditions (no -c/-d given).
- Our .gam pair was made with iccgamut default -pl (Lab) — every
  geometric stage validated correctly (smthdump consumed the same Lab
  gamuts) but the SPACE disagrees with colprof's actual map. In Jab the
  ET-8550 black is J=9.77 and ClayRGB black J=7.90 (vs Lab 4.67/0.0) —
  the compression geometry is completely different in the darks, which
  is exactly where the realized-vs-port residual concentrated.
- Jab gamuts regenerated: iccgamut -ff -ir -pj -d3 (COLOR_REP JAB);
  iccgamut/xicclu use the same xicc_enum_viewcond defaults as colprof,
  so Argyll's own tools provide all conversions for gating (xicclu
  -pj = dev→Jab) — no cam02 port needed for validation. For the ENGINE
  (no profiles at runtime), cam02.c + xicc_enum_viewcond defaults must
  be ported (validate against an xicclu/cam02 dump harness).
- Jab gate: scratchpad/argbuild/e2e_gate_jab.py (port maps Jab→Jab on
  Jab gamuts; realized ref chain: Lab→B2A(-ip)→dev→xicclu -pj).
- Jab gate result with the same pipeline: **1.92 / 4.74 / 8.29** (from
  3.44 / 12.40 / 26.48 in Lab) — hypothesis confirmed decisively.

## Final-fit recipe completed + Rspl3 exact fitter (2026-07-15)

New ground truth + ports, each validated:
- **Rspl3 (rspl3.py)**: fit_rspl_w 3→3 transcribed (smf[2] 3-D table,
  trilinear rows, per-axis 2nd-diff curvature, cw = smooth·10^lsm·vw_f·
  (mres−1)⁴/Π(gres−2), per-channel vw, matrix-free Jacobi-CG).
  Validated vs compiled Argyll `fit3d` harness (scratchpad/argbuild):
  **0.0065 / 0.018 / 0.028** on 3k synthetic rows, gres 29, smooth 2.0.
  fit3d.c reusable: header line + D/Q rows on stdin.
- **Complete gammap.c row assembly** (was the biggest omission):
  1. 512 grey-axis rows (USE_GREYMAP L1380–1459): premapped source axis
     → blend of BENT black axis (dr_be_bp) into STRAIGHT (dr_cs_bp),
     smoothstep-spline within brad of black, weight 1+t·brad. This is
     what straightens colprof's neutrals above black (measured).
  2. guide rows w1=1.01 (+ interior identity guides).
  3. sub-surface w2 / sd3-identity w3 rows (unchanged).
  4. surface-grid anchors (nearsmth.c L3746+): outer two layers
     {0,hdmapres,hm−1,hm−1−hd} of the half-res grid over the
     gexp(1.10)-expanded box, wn-mapped to source surface, dv via
     lastmap, w1=0.1·min(1, |dv−cp|/|cp−cent|).
  5. Final fit: Rspl3 gres=29(mapres -qm), smooth=psmooth=2.0 (NOT
     PSMOOTH the weight-table const — gammap.c L345 psmooth=2.0 blended
     by gampwf), avgdev=0.005, il/ih = gexp-expanded union box.
- **W/B fine-tune (gammap.c L1799–1856)**: after the fit, rigid
  icmVecRotMat taking the map's ACTUAL (a_wp,a_bp) — domap of s_mt_wp/bp
  (source cs points scaled to swL/sbL, pre-rotated back via igrot) — to
  (d_mt_wp,d_mt_bp) (dest straight axis at dwL; note C reuses t for bp).
  Baked into map_lab output. Makes white/black EXACT (measured: white
  100/−0.72/1.08 == colprof).
- **RSPLPASSES ported literally** (nearsmth.c L3100–3345): tdst/nott
  setup (inside-dest points never tuned; evect intersect with sanity
  |isec|≤|nearest|+5), 4 passes: fit (raw _sv→anv) Rspl3 smooth=2.0 →
  temp, clen along evect field, neighbourhood-max rext (rw UNnormalised,
  self included, minext −20, RSPLSCALE 1.8 branch), gain schedule
  icgain 1.4 → 0.5·, xgain=wt.f.x-scaled first pass only, coff
  accumulate → smoothed via Rspl3(dv→coff, smooth=1.0!) → anv = dv +
  naxbf·coff (nott points untouched). lastmap = pass-3 (_sv→anv) fit,
  reused for the surfpnt dv lookup. NOTE: pass fits use the RAW source
  point (_sv), not the cusp-mapped sv.
- Jab gate history this session: 1.92 (old warp recipe) → 2.08 (full
  rows + Rspl3, no RSPLPASSES; white 99.61) → 2.28 (+wb fine-tune;
  white/black exact, neutrals all match) → RSPLPASSES run pending.
- Remaining known gaps if gate unmet: guide density (theirs 26,850 via
  nssverts stratified sampling at xvra=3.0 vs our 9,988 .gam verts —
  engine controls its own cloud density; for .gam gates consider
  triangle subdivision), evectmap as warp-of-directions vs their rspl
  fit, cam02 port for engine wiring (xicclu validates), two-profile
  realized floor ~0.7–0.8 applies to the reference chain itself.

## GATE PASSED at map level (2026-07-15, late)

Final fixes, each found via the in-frame instrumented dumps (gmdump3/4 =
gmdump + nearsmth_inst + gammap_inst; nsm_dump.txt has N rows uflag/
vflag/w1/naxbf/_sv/sv/dv/aodv/nrdv + S rows sv2/dv2/sd3/w2/w3 in the
premapped frame; nspasses.txt has V + per-pass P/A):

1. **VECADJ is GAUSS-SEIDEL** (anv written per point IN pass, clip
   included; dv never updated until after all passes; J from original
   dv, a/b from iterating anv): 0.422 → **0.038** vs their V.
2. **Inside-isect guides are REJECTED entirely** (nearsmth.c L1989) —
   no interior identity rows exist in the C. (My null rows were an
   invention; removed.)
3. **Sub-surface rows transcribed literally** (L3390–3685): CYLIN_SUBVEC
   defined → dv2 = pure neutral-target direction, adepth = adepth2
   always; napoint = line-line-closest of guide ray with the DEST GAMUT
   wb axis, J half-blended toward dv-J then endpoint-clipped; inner gate
   |mint−1| < |maxt|−1 ∧ radial_isect(dv) < radial_src(dv); trivial-gate
   ml ≤ 0.1; nonsense case sets dv=aodv; SUBVEC_SMOOTHING afterwards
   (neighbour filter with cylindrical a/b scaling; calloc zeros from
   invalid neighbours are REAL C behaviour). Line∩isect computed as
   interval intersection of exact per-surface crossings
   (inside(src)∧inside(dst)). Validated vs in-frame S rows: sd3 EXACT,
   dv2 0.15, vflag 93.8%.
4. **RSPLPASSES tdst is on the INTERSECTION gamut** (smp[i].dgam), not
   the full dest, with vintersect2 segment semantics (inside → segment
   entry behind the point; outside → first +ve crossing) and nearest =
   nearest isect vertex. Fixed clen from ±17 tails to matching; with
   this the full RSPLPASSES helps rather than harms.
   `_RSPLPASSES_ON` module flag allows disabling (0.349 without).

**Definitive gate (port vs Argyll's own domap, gmdump, -d10 Jab pair,
200 probes): median 0.340 / 95% 1.660 / max 4.19** — median below
colprof's own realized-roundtrip floor (0.41/0.90/2.0). Guide-level:
aodv 0.086, VECADJ 0.038, naxbf exact, temp-fit 0.043, evect cos 0.9995.
Their-rows-through-my-fitter cross-check: 0.079.

Remaining tail contributors (documented, small): guide sampling
positions (my Sobol ≠ their sobol.c sequence), evect field 5% tail
(cos 0.87), rsplpasses residual. NEXT: cam02.c port + vc defaults for
engine wiring (no profiles at runtime), engine-side triangulated
surfaces from model clouds (UV-sphere sampling of the radial field),
wire into build_mapped_b2a, engine-vs-colprof realized E2E, suite,
UI #43, #44.

## THE definitive gate: gmdump (domap harness) + true floors (2026-07-15)

- **gmdump** (scratchpad/argbuild/gmdump.c): compiles gammap.c itself
  (compile line: smthdump deps + gamut/gammap.c + -I xicc -I spectro +
  a one-line xicc_dump_gmi stub) and calls new_gammap EXACTLY as
  colprof -S perceptual does (profout.c L2670: sc=src, s=NULL, d=dst,
  sh=NULL, kbp 0/0, cmymap 0, rel 0, mapres, mn/mx NULL) with the
  xicc.c "p" gmi hardcoded; dumps domap() for stdin probes. This is
  noise-free ground truth for the whole map.
- **colprof -qm uses gamut detail gres=10.0** (profout.c L2456+) — all
  earlier gates ran -d3 gamuts (denser than colprof's!). -qm pair:
  Clay_d10.gam / pdiag_d10.gam (iccgamut -ff -ir -pj -d10).
- **Measured floors** (200 AdobeRGB-device probes, Jab): their own
  domap vs their own realized -qm profile (pdiag.icc): **0.41 / 0.90 /
  2.00** — the realized chain adds this much by itself; vs the -qh(?)
  BASE.icc: 0.87 / 1.29 / 2.02 (quality mismatch inflates it more).
  Gate = port vs domap directly (e2e_gate_domap.py); domap_ref.npy has
  (jab_in, domap) for the standard 200 probes.

## Stage 6 wiring + saturation first pass (2026-07-15, latest)

- **cam02.py**: xicc/cam02.c literal port (NOTE: SYMETRICJ IS defined
  despite its "[Undef]" comment). Appearance = rel Lab → Bradford
  D50→media-white (icclib 'arts') → cam02(vc 'd', media white).
  EXACT vs xicclu -ir -pj: 0.0001 median both profiles. Media whites
  from wtpt tags (ClayRGB=D65! printer=paper white).
- **wire.py**: fit_gammap_port_mappers — source gamut via iccgamut -pj
  -d10 on the source profile; DEST gamut via a minimal temp model-only
  ICC handed to the same iccgamut (Argyll's gamut construction on the
  engine model; the UV-mesh approach measured 3.2 — scrapped).
  PortMapper = Ap_dst.jab_to_lab ∘ GammapMapper ∘ Ap_src.lab_to_jab.
  Engine E2E vs colprof realized: **0.907 / 2.16 / 5.23** (= compound
  floor). build_mapped_b2a chain: port (B2A0 perceptual, default intent
  only; -t/-c/-d/-nP → oracle) → colprof oracle → family.
- **Saturation extension** (intent="s"): gmi verbatim (gamexf 1.0,
  gamcknf 1.1, gamxknf 0.5, smooth=ssmooth=4.0, satenh 0.9), expansion
  target = FULL dest (no image gamut → dst_gam = dc_gam), pass-1
  radial swap (approx: radial for the C's nearest in the swap test),
  expansion knee sub-rows, satenh post-step (adjust_sat_func with the
  section-alternating blend, applied before wb-tune at eval time).
  gmdump_s harness (gmdump.c + intent arg 's', links instrumented
  nearsmth/gammap → in-frame dumps available for the s-run too).
  First gate: **0.953 / 7.71 / 13.2** vs domap(s); perceptual
  regression unchanged (0.340). B2A2 stays on the oracle until the
  tail converges (#45) — iterate with nsm_dump/nspasses like P4b.
