# Engine evaluation harness (issue #123, W0)

Dev-only tooling — never imported by the app, never shipped.

## What it is

Candidate improvements to the **Maximum accuracy** engine mode land dark
behind tokens (`CHROMIQ_ENGINE_NEXT="ucs,joint-sep"` /
`BuildSettings.engine_candidates`). Nothing replaces the shipped accurate
mode until it provably wins here.

- `synthetic.py` — six analytic spectral printers (S1–S6) where
  `f_true(device) → XYZ` is exact; instrument-noise + misread models.
- `battery.py` — builds a profile per printer and scores the **written
  bytes** against `f_true` on dense quasi-random points (ΔE2000):
  A2B accuracy, B2A end-to-end (profile ink printed on the true printer),
  round-trip, neutral-K smoothness, OOG hue keeping, outlier-flag F1,
  build time. Also evaluates the promotion gates.
- `iccread.py` — minimal mft2 CMM replay so the referee judges the file,
  not the in-memory model.
- `heldout.py` — the real-measurement secondary leg (90/10 held-out
  protocol, endpoints protected).

## Usage

```bash
python -m benchmarks.battery --candidates "" --out baseline.json
python -m benchmarks.battery --candidates ucs --out ucs.json
python -m benchmarks.battery --compare baseline.json ucs.json

python -m benchmarks.heldout ~/charts/*.ti3 --candidates ucs
```

## Promotion gates

A candidate set is promoted into the shipped accurate mode only when:

1. Synthetic battery: aggregate median ΔE00 improves **≥ 5 %**, no device
   class regresses **> 2 %** on median or p95, max / round-trip max not
   worse (beyond quantisation jitter).
2. Robustness: S4 misread F1 not worse; clean-chart false flags not up.
3. Smoothness: neutral K TV-vs-net not worse on S3/S5/S6.
4. Build time ≤ 2× the current accurate mode; full test suite green.
5. Real-measurement leg: no consistent regression across the corpus.

## Interpretation rule (data-integrity policy)

Real-data ΔE00 median differences **below ~0.05 are noise** — never argue
from them. The synthetic battery decides ties. The owner's own
measurements are benchmark smoke tests only; **no constant, threshold or
curve may ever be tuned against them.** The battery definitions in
`synthetic.py` are fixed referees — do not tune them against a candidate.
