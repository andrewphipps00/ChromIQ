# Scanner misalignment check — how it works, and where the numbers come from

Answers the questions in issue **#119**, with measurements from Knut's two real
600 dpi IT8 scans (a LaserSoft DC Pro Advanced and a Wolf Faust). The raw
output is in [`scanner_edge_study_report.txt`](scanner_edge_study_report.txt);
it is reproduced by

```bash
python scripts/scanner_edge_study.py --data-dir ~/ChromIQ/scanner-test-targets/real
```

and pinned by `tests/test_scan_alignment_real_targets.py` (skipped when the
scans aren't on the machine — they aren't in the repo).

The scans' "aligned" grid is not eyeballed: it is ArgyllCMS `scanin`'s own
solved placement, recovered from its verbose transform. Sampling that grid
reproduces scanin's own `.ti3` to **0.19 %** mean device error over all 288 /
864 patches, so it is a trustworthy zero point.

## Two independent checks

The scanner tool runs two unrelated tests on every page. They share nothing
but the sample boxes.

### 1. Placement agreement — "is a nearby grid position better than this one?"

The scan is sampled at the grid's own position and at every rung of a step
ladder around it: **24 steps of 5 % of a patch pitch, in 8 directions**. Each
position is scored by comparing every patch with the colour the chart says it
should be, through a page-wide monotone response map. The position's score is
the **95th-percentile** absolute residual — worst-rules, with enough immunity
that one dust speck can't condemn a page.

The best rung is 100 %, the least-worst direction is 0 %, and the grid's own
position lands between. That single number is the verdict; it falls below
`scanner_check_agreement` (default 0.85) and the page is flagged.

The report now also prints an **average**: the identical normalisation applied
to the *mean* residual instead of the 95th-percentile one. So

```
(placement agreement: worst 56.88 %, average 96.70 %)
```

means a few patches are badly placed; both numbers low means the whole grid is.

> **Why not a per-patch agreement?** Because it does not work, and the study
> shows it. A single patch's residual measures how well the page-wide response
> model fits *that colour*, not how well its box is placed. Ranking each patch
> on its own ladder gives, on the Wolf Faust, a worst patch of 13.96 % when the
> grid is perfectly aligned and 15.59 % when it is shifted a fifth of a patch —
> the aligned page scores *worse*. Some patch always sits at its own floor, so
> the page minimum pins to 0 % on every scan. Only the pooled statistic
> separates placements, which is why the verdict pools and the average is
> reported as a second pooled number rather than a mean of per-patch values.

### 2. Patch-edge detection — "is a sample box sitting on a border?"

A patch border is a **line of sudden colour change** — a spatial derivative,
taken centred at two scales (4 px and 8 px), over luminance and two
opponent-colour planes, keeping the strongest.

Every sample box carries an **11×11 sensing grid**: the inner 9×9 tiles the box
itself, and the outer ring sits just *outside* it, so a border is sensed while
the box is still approaching. Each cell records its **peak** gradient, never a
mean.

A box counts as sitting on an edge when

1. three or more **connected** cells stand above the page's noise floor — a
   border line runs through adjacent cells, dust specks scatter and never
   connect; **and**
2. the box reads clean at some nearby grid position (±20 % of the pitch) —
   colour bars and wedges printed *inside* a patch stay hot everywhere and must
   not count.

The box's **edge strength** is then

```
flank = (3rd-strongest of its 121 cells − page grain floor) / page brightness range
```

The 3rd-strongest matches the three-connected-cells rule. The grain floor is
the 75th percentile of all inner-9×9 cell peaks on the page.

> **`scanner_flank_limit` is not "a 25 % jump in value."** It is a normalised
> gradient *peak*, offset by the page's own grain floor. `0.20` reads as: *the
> third-hottest cell of this box changes colour across its width by a fifth of
> the page's whole brightness range more steeply than the page's grain does.*

`scanner_flank_min_boxes` whole patches (not sub-cells) must be on an edge at
once before the page is reported. `0` = off.

## The measurements

### Noise floor (aligned grid, inner 9×9 cell gradient peaks)

As a fraction of the page's brightness range:

| target | min | mean | grain floor (P75) | worst patch |
|---|---|---|---|---|
| Wolf Faust | 0.011 | 0.041 | 0.049 | 0.074 |
| LaserSoft | 0.012 | 0.039 | 0.046 | 0.106 |

So the page grain is around **0.04**, and reaches **0.05–0.11** on the noisiest
patches. Anything below ~0.06 starts counting grain, not edges. Knut's estimate
of a 2 % noise floor is about half of what the scans actually show.

### Edge height between neighbouring patches

Fraction of the page's brightness range, over all four directions:

| target | smallest | 5th pct | median | largest |
|---|---|---|---|---|
| Wolf Faust | 0.008 | 0.045 | 0.175 | 2.25 |
| LaserSoft | 0.0007 | 0.011 | 0.081 | 1.62 |

Per category the full breakdown is in the report. The headline: the faintest
real borders (greyscale left/right neighbours, and the LaserSoft's dark
columns) are **below the noise floor** and cannot be separated from grain at
any threshold. Half of all borders are above 0.08, and the borders a *misplaced
box actually lands on* read 0.20 and up — the metric sees a border's peak, not
the mean step, so it is far above the neighbour-to-neighbour difference.

### Choosing the two defaults together

They cannot be chosen separately: lowering the limit raises the count on
aligned pages too. Boxes over the limit, both targets, at `limit = 0.20`:

| case | Wolf Faust | LaserSoft |
|---|---|---|
| aligned | 1 | 2 |
| shifted 5 % of a patch | 1 | 2 |
| shifted 10 % of a patch | 2 | 1 |
| **shifted 20 % of a patch** | **131** | **258** |
| **one corner pulled in** | **4** | **160** |

A box rim is 25 % of the pitch from its border (at 50 % sample area), so
shifts under that *cannot* put a box on an edge and must stay silent. Requiring
silence on the first three rows and detection on the last two:

| limit | must stay ≤ | must catch ≥ | admissible `min_boxes` |
|---|---|---|---|
| 0.08 | 9 | 16 | 10 … 16 |
| 0.10 | 8 | 12 | 9 … 12 |
| **0.20** | **2** | **4** | **3 … 4** |
| 0.25 | 2 | 3 | 3 … 3 |
| 0.30 | 1 | 0 | *impossible* |

**Shipped: `scanner_flank_limit = 0.20`, `scanner_flank_min_boxes = 3`** — the
pair with the widest margin, and exactly the count Knut specified in the #108
design.

- **`min_boxes = 2` false-alarms**: an aligned LaserSoft leaves 2 edge-carrying
  boxes at every limit that also catches the corner pull. Its printed bars are
  genuine edges that fall near box rims.
- **`min_boxes = 7`** (the value shipped previously) **misses the corner pull**: only a
  handful of patches near the dragged corner ever straddle a border, and on the
  Wolf Faust that is 4 boxes, never 7. This is the bug Knut reported.
- **A limit near 0.08**, as Knut proposed from the "25 % jump" reading, would
  need `min_boxes` around 10 to stay quiet — which defeats the point of
  catching "a few patches in that corner".

## Why the ladder is 24 × 5 % and not 60 × 2 %

`placement_probe` downsamples the scan to `max_side = 2200` **before** sampling,
so the scan's dpi does not set the step size — the patch pitch in *sampled*
pixels does:

| chart | patch | 10 % | 5 % | 2 % |
|---|---|---|---|---|
| Wolf Faust IT8 | 81.3 px | 8.13 | 4.07 | 1.63 |
| LaserSoft DC Pro | 50.9 px | 5.09 | 2.54 | 1.02 |
| ChromIQ scanner chart, A4 (4 mm) | 29.6 px | 2.96 | **1.48** | **0.59** |

`_box_ixs` rounds the sample box to whole pixels. On ChromIQ's own 4 mm scanner
charts a 2 % rung is **0.59 px**, so most of the 60 rungs land on the box their
predecessor already measured: 5× the work, duplicate positions. 5 % is the
finest rung that still moves the box on the smallest patch we support — Knut's
own suggestion, and the one adopted.

Raising `max_side` to ~3700 would make 2 % viable, at ~2.8× the pixels on top
of the 5× ladder. Not worth it until something shows 5 % is too coarse.

## A bug this exposed

The clean-nearby ring (step 2 above) was indexed by **ladder rung**, not by
physical distance:

```python
ring_ix = [1 + d * steps + 1 for d in range(8)]   # "rung 2 = ±20 %"
```

That is ±20 % only because the rung was 10 %. At 24 × 5 % it would silently
have become ±10 %, and at 60 × 2 % ±4 % — a ring that close to the box never
clears a border, so edge detection would have quietly stopped firing. The rung
is now derived from `step_frac`, and `test_clean_nearby_ring_tracks_step_frac`
holds it there.
