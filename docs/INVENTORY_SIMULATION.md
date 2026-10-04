# Inventory simulation — the policy scored against real opening stock

Measured by `tools/inventory_simulation.py`, pinned by `tests/test_inventory_simulation.py`.

Every service figure this project reports is **reorder-point coverage**: `docs/POLICY_HOLDOUT.md`
tiles each window into lead-time blocks and asks whether `ROP = rate·L + buffer` covers the
demand in each. That is the right proxy when no starting stock exists. It is not the quantity
the store experiences, which is: *given what was on the shelf that morning, how much demand
walked away?*

This measures the second one, on the subset where it can be measured without inventing anything.

## Read the coverage before the numbers

**73 of the 266 scored SKUs (27%)** appear in the inventory workbook, carrying roughly **18%**
of scored demand. After restricting to SKUs the policy actually priced and to counts fresh
enough to be evidence, each origin simulates **28–45 SKUs**. Nothing here generalises to the
266, and no figure below should be quoted as if it did.

| Origin | Oldest count used | Worst staleness | SKUs | Opening units | Demand |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-02-02 | 2026-02 | 0 mo | 45 | 7,047 | 1,299 |
| 2025-11-04 | 2025-10 | 1 mo | 36 | 6,924 | 1,711 |
| 2025-08-06 | 2025-08 | 0 mo | 28 | 5,370 | 1,555 |

Two filters take each origin from "counted" to "simulated", and both are visible above:

| Origin | Counted in month | Count ≤ 1 mo old | …of those, priced | Simulated |
| --- | ---: | ---: | ---: | ---: |
| 2026-02-02 | 54 | 54 | 45 | **45** |
| 2025-11-04 | 48 | 49 | 36 | **36** |
| 2025-08-06 | 38 | 38 | 28 | **28** |

The drop is almost entirely SKUs flagged `insufficient_data` — they carry no reorder point, so
there is no policy to simulate for them. They are not silently excluded: they are the same
SKUs `docs/ACCEPTANCE_STANDARD.md` condition 1b counts as uncovered demand.

**The 2026-05-03 origin is dropped.** Its nearest usable count is 2026-03 — two months stale,
because 2026-04 is a partial sheet (187 of 1,416 quantities filled, 369 units against 2026-03's
36,051). This is not a new finding: `scripts/step5_prescriptive.py`'s `UNITS_ON_HAND_SOURCE`
already excludes that month for the same reason. Losing that origin costs the development set
and leaves the three that are primary evidence anyway.

## Where the opening stock comes from

`Inventory_Count` — the table — is empty, and this document does not change that. It is fed by
the Digital Tallying Interface and the store has not tallied through the app yet.

The **historical workbook** `data/USTore_inventory_excel_long_mapped.csv` is a different thing:
23 monthly hand counts (2024-11 → 2026-04), already vocabulary-mapped, all 301 of its items
joining cleanly to `Dim_Product.item_name`. `backend/catalog.py::load_csv_stock` already reads
it for the dashboard, and `step5_prescriptive.py` already depends on it — the holding cost `H`
is derived from its 2026-03 snapshot. So for the SKUs it covers, the opening condition is
**measured, not invented**, which is the only reason this tool is allowed to exist.

The loader reconciles exactly against figures already committed elsewhere: month totals of
27,852 / 31,106 / 30,799 units at the three origins, and **36,051** for 2026-03 — which is
`step5_prescriptive.UNITS_ON_HAND_ESTIMATE` to the unit, arrived at independently here.

Two gates guard that claim, both pinned by tests:

- **Leakage** — a count taken after the origin is not available at the origin.
- **Staleness** — a count more than one month old is *dropped*, not carried forward. Carrying
  it forward would quietly re-invent the starting condition this tool exists to avoid inventing.

## What is scored

Per SKU, day by day: receipts land, demand arrives, `served = min(on_hand, demand)`, and
**unmet demand is lost, not backordered** — a student who cannot buy a lanyard today does not
queue for next week. Review is continuous on inventory *position* (on-hand + on-order); when
position falls to the reorder point, an order of EOQ is placed and arrives `L` days later.

Four arms, identical SKUs, identical demand, identical opening stock — only the reorder rule
differs. `none` places no orders at all and simply runs the opening stock down.

## Result

Pooled across the three origins. Holding is reported in **units**; cost inputs remain
provisional pending the site visit.

| Arm | Fill | Units short | Stockout days | Mean on hand | Orders |
| --- | ---: | ---: | ---: | ---: | ---: |
| **tiered** (committed policy) | **0.9345** | 299 | 30 | 9,486 | 31 |
| flat q=0.80 | 0.9301 | 319 | 40 | 9,201 | 30 |
| naive (no model) | 0.9281 | 328 | 39 | 8,935 | 30 |
| **none** (no replenishment) | **0.6745** | 1,486 | 248 | 5,826 | 0 |

Per origin:

| Origin | tiered | flat q=0.80 | naive | none |
| --- | ---: | ---: | ---: | ---: |
| 2025-08-06 | **0.8347** | 0.8322 | 0.8212 | 0.6270 |
| 2025-11-04 | **0.9906** | 0.9813 | 0.9877 | 0.8364 |
| 2026-02-02 | **0.9800** | 0.9800 | 0.9777 | 0.5181 |

### The finding that matters, and it is not flattering

**Replenishing at all is worth +26 points of fill. *Which* rule you replenish by is worth 0.6.**

The tiered policy beats naive stocking at 3 of 3 origins — by 0.0064 pooled, while holding
**6% more stock** (9,486 units against 8,935). That is not a dominance. It is a marginal gain
bought with inventory.

Set that against the same comparison under reorder-point coverage, where the tiered policy
beats naive **0.6851 against 0.4264** (`docs/POLICY_HOLDOUT.md`) — a 26-point gap.
**The proxy overstates the policy's margin over the no-model baseline by roughly fortyfold.**

The mechanism is not mysterious. Reorder-point coverage scores each lead-time block as though
the shelf were empty at its start, so the buffer has to absorb all of the block's variance. A
real shelf carries stock across blocks, and that carried stock absorbs most of the same
variance for free. The buffer is then doing much less work than the proxy credits it with.

This does not retract anything in `POLICY_HOLDOUT.md` — that document measures what it says it
measures, and says plainly it is not an inventory simulation. It does mean **acceptance
condition 2, "beats the no-model alternative," rests on a proxy that exaggerates the margin**,
and on this subset the honest margin is under a point.

### The ordering-cost scenarios differ in holding, not service

| Scenario | S (PHP/order) | Fill | Mean on hand | Units ordered |
| --- | ---: | ---: | ---: | ---: |
| `low_admin_cost` | 1,250 | 0.9345 | 9,486 | 18,169 |
| `high_goods_value` | 200,000 | 0.9345 | 53,426 | 229,823 |

**Fill is identical to four decimals.** Service is set by the reorder point; the order quantity
only sets how much stock sits idle. `high_goods_value` orders 229,823 units against 4,565 units
of realised demand — a 50× overshoot that makes the EOQ, not the reorder point, the dominant
term in holding. That scenario was already flagged in `step5_prescriptive.py` as one of "two
competing interpretations"; this is the first measurement of what it would actually do.

## At what shelf depth does the rule start to matter?

The result above is confounded, and the confound is measurable: the shelf opens at **4.2× the
window's demand** (3.5× / 4.0× / 5.4× by origin). With that much cover, replenishment timing
barely binds — so nothing feeding replenishment, the demand rate included, gets the chance to
matter. Two checks rule out the alternatives:

- **Not an easy-item artifact.** The covered subset is *slower*-moving than the catalogue, not
  faster: **11% Fast-class against 26%**, median 78 units/SKU against 125.
- **Not a metric artifact.** `none` places zero orders and still meets 67% of demand. The shelf
  does the work directly.

So the question is regime, not magnitude. `--stock-scale` multiplies the measured opening stock
and re-runs. **Every row but 1.0× is a counterfactual, not a measurement** — the tool's whole
claim is that it does not invent the starting condition, and a scaled shelf is a supposition.
The reorder points are deliberately *not* rescaled: the policy is fitted on demand history and
does not know what is on the shelf, which is the asymmetry being probed.

| Shelf ÷ demand | tiered | flat q=0.80 | naive | none | **tiered − naive** | tiered − none |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.53× | 0.8519 | 0.8792 | 0.8312 | 0.2607 | 0.0208 | 0.5913 |
| 0.79× | 0.8738 | 0.8635 | 0.8294 | 0.3257 | **0.0444** | 0.5482 |
| 1.06× | 0.8845 | 0.8819 | 0.8412 | 0.3832 | **0.0433** | 0.5013 |
| 1.59× | 0.8874 | 0.9067 | 0.8840 | 0.4715 | 0.0035 | 0.4160 |
| 2.12× | 0.9199 | 0.9292 | 0.9143 | 0.5287 | 0.0056 | 0.3912 |
| 3.18× | 0.9267 | 0.9404 | 0.9074 | 0.6136 | 0.0193 | 0.3131 |
| **4.24× — measured** | **0.9345** | 0.9301 | 0.9281 | 0.6745 | **0.0064** | 0.2600 |
| 6.36× | 0.9567 | 0.9428 | 0.9558 | 0.7426 | 0.0010 | 0.2141 |

### The forecast's value is a function of how much stock you carry

**The rate/buffer/tier apparatus is worth 0.6 points at USTore's current shelf and 4.4 points
at roughly one lead-time's cover — about seven times more.** It beats naive at every depth
tested, so the apparatus is never harmful; what changes is how much it is worth.

The reading that matters operationally: this project exists to *reduce* inventory. As it
succeeds, cover falls toward the 0.8–1.1× band where the gap is widest. **The forecast becomes
more valuable as the project's own recommendations are implemented, not less.** The current
0.6-point result describes USTore's present stocking, not the method's ceiling.

### Fill is not monotone in shelf depth, and that is not a bug

`naive` and `flat q=0.80` both dip as the shelf deepens (e.g. naive 0.9143 at 2.12× against
0.9074 at 3.18×). An earlier draft of this sweep asserted monotonicity and would have logged
these as defects. The mechanism, reproduced minimally in
`test_more_opening_stock_can_serve_less`:

> Continuous review fires on inventory **position**. A deeper opening shelf delays the first
> trigger and slides every later order with it, so inside a finite window one fewer order
> lands. Crossing that boundary the shelf gains Δ units and forgoes a whole order of Q. When
> Q > Δ, total availability — and fill — falls as opening stock rises.

Verified on a single SKU: opening 80 serves **344** where opening 70 serves **346**, gaining 10
units of shelf and forgoing an order of 40. A real property of (s,Q) with lost sales on a
finite horizon.

### The tiering and the flat quantile — adjudicated

`docs/POLICY_HOLDOUT.md` reports the per-tier operating points **dominating** flat q=0.80 under
reorder-point coverage: *"more demand met, on no more stock."* Under simulation that does not
reproduce. The two trade places by depth, and per origin the differences pull in opposite
directions. The gaps are **14–64 units** on 4,565 units of pooled demand, in a catalogue where
single SKUs routinely move hundreds.

That left the reading *measured-and-unresolved*, not measured-and-refuted: the tiering is fitted
on **145–179** SKUs and the workbook makes only **28–45** observable, so sample size was the
obvious suspect. `--synthetic-cover` removes that suspect.

**The anchor first.** On the measured shelf, pooled over the three origins, **52 distinct SKUs**
(41 with demand), paired bootstrap clustered on the SKU, 10,000 resamples:

| | tiered − flat q=0.80 | 95% CI | |
| --- | ---: | --- | --- |
| Fill | **+0.0044** | [−0.0000, +0.0111] | P(>0) = 0.95 |
| Stock held | **+3.09%** | [+0.28%, +8.29%] | |
| | | | **NOT SEPARABLE** |

The fill interval spans zero and the tiering holds *more* stock, so P(dominates) = **0.00**. This
reproduces the finding above with a confidence interval attached rather than an eyeball.

**Then the synthetic sweep.** Every priced SKU opens at `C × rate × L` — the same depth relative
to its own policy — so the whole priced catalogue becomes observable at **242 distinct SKUs**.
Demand, rates, reorder points, lead times and EOQ are all the real ones. **Only the opening
condition is invented**, every row carries `stock_basis = synthetic`, and none of it says
anything about USTore's actual shelf.

| C | Shelf ÷ demand | Δ fill | 95% CI | Δ stock | Opens below both ROPs | Verdict |
| ---: | ---: | ---: | --- | ---: | ---: | --- |
| 0.25 | 0.05 | +0.0000 | [+0.0000, +0.0000] | +0.0% | **100%** | not separable |
| 0.5 | 0.10 | +0.0000 | [+0.0000, +0.0000] | +0.0% | **100%** | not separable |
| 1 | 0.20 | +0.0000 | [+0.0000, +0.0000] | +0.0% | **100%** | not separable |
| 2 | 0.39 | −0.0018 | [−0.0108, +0.0063] | −5.2% | 27% | not separable |
| 3 | 0.59 | +0.0065 | [−0.0017, +0.0156] | +4.8% | 15% | not separable |
| **4** | 0.79 | **+0.0196** | [+0.0053, +0.0353] | +15.6% | 4% | **trade** |
| **6** | 1.18 | **+0.0280** | [+0.0139, +0.0485] | +17.5% | 0% | **trade** |
| **8** | 1.57 | **+0.0260** | [+0.0145, +0.0411] | +14.3% | 0% | **trade** |
| **16** | 3.15 | **+0.0107** | [+0.0048, +0.0188] | +2.4% | 0% | **trade** |
| **24** | 4.72 | **+0.0096** | [+0.0028, +0.0206] | +0.6% | 0% | **trade** |

**Separable at 5 of the 10 depths tested — and a dominance at none of them.**

Three things fall out, and the second is the answer to the question that was left open.

**1. The flat end is zero for a structural reason, not a statistical one.** At C ≤ 1, **100% of
SKUs open below *both* reorder points**, so both rules fire on day zero and the lead time — not
the reorder point — decides what is served. The arms are identical by construction there. A
difference of exactly zero is a mechanism, and the `opens below both ROPs` column is printed so
those rows cannot be misread as a measurement of indifference. Pinned by
`test_an_opening_shelf_below_both_reorder_points_makes_the_arms_identical`.

**2. Once enough SKUs are observable, the tiering *is* separable — and it is a trade, not a
dominance.** From C = 4 upward the fill gain clears zero at every depth (+1.0 to +2.8 points,
P(>0) = 1.00), and at every one of those depths the tiering holds **more** stock. **The dominance
claim does not survive.** It was never refuted by the measured shelf; it is refuted here, on the
only axis that could settle it, and what replaces it is a real but purchased service gain.

**3. The sample size was hiding a real effect, and the honest earlier reading was the right
one.** 28–45 SKUs genuinely could not resolve an effect that 242 resolves comfortably. The
earlier verdict of *measured-and-unresolved* was correct, and the correction it needed was more
observable SKUs rather than more modelling — exactly as this document said.

**What USTore should take from it.** At the depth that brackets its actual shelf (C = 24,
shelf ÷ demand 4.72 against the measured 4.24), the tiering buys **+0.96 points of fill for 0.6%
more stock** — a favourable trade, and one the store can accept or decline knowingly. What it
must not be told is that the tiering is free.

## What this does and does not establish

- **Does:** on 28–45 SKUs at three origins with measured opening stock, the policy meets a
  pooled **93.5%** of demand against **67.5%** for not replenishing — replenishment is
  strongly justified.
- **Does:** the margin over naive stocking is **0.6 points at 6% more stock**, far below what
  reorder-point coverage implies, and that gap between proxy and simulation is the most
  useful thing measured here.
- **Does not:** generalise to the 266. 27% of SKUs, ~18% of demand, and the covered ones skew
  toward items the workbook bothered to count.
- **Does not:** model receipts the store actually made. Opening stock is real; every order
  after day 0 is the simulated policy's, not USTore's. Restock jumps visible in the workbook
  are deliberately not replayed — that would score the store's ordering, not the policy's.
- **Does not:** price anything. Holding is in units; the peso column is absent on purpose.
- **Does not:** cover the 2026-05-03 window, or any SKU whose count is stale or missing.
- **Does:** locate the regime — the apparatus is worth ~0.6 points at the measured 4.2× cover
  and ~4.4 points near 1× cover, so the value of the demand rate is contingent on stocking
  depth rather than fixed.
- **Does not:** establish that any depth other than 4.24× ever occurred. Every other row is a
  counterfactual shelf, and the SKUs, demand and reorder points are otherwise unchanged.
- **Does:** separate the tiering from the flat quantile — but only under `--synthetic-cover`,
  where the whole priced catalogue is observable. From C = 4 upward the tiering wins on fill at
  every depth tested and holds more stock at every one, so **it is a trade and not the dominance
  `docs/POLICY_HOLDOUT.md` reports**. On the measured shelf it remains not separable, and that
  row is the anchor.
- **Does not:** establish that any synthetic cover level ever occurred. `C × rate × L` is an
  invented opening condition, labelled `synthetic` on every row, and it says nothing about
  USTore's shelf. Its only job is to make the two rules observable on enough SKUs to compare.

## Reproducing it

```bash
python tools/inventory_simulation.py                       # writes data/inventory_simulation.csv
python tools/inventory_simulation.py --max-staleness-months 2   # includes 2026-05-03, staler counts
python tools/inventory_simulation.py \
    --stock-scale 0.125 0.1875 0.25 0.375 0.5 0.75 1.0 1.5 \
    --out-csv data/inventory_stock_depth.csv          # the shelf-depth sweep
python tools/inventory_simulation.py --synthetic-cover        # the tiering adjudication
python tools/inventory_simulation.py --synthetic-cover 4 8 16 # a chosen sweep
pytest tests/test_inventory_simulation.py -q                  # 26 tests
```

The tool opens `ustore.db` read-only and writes **nothing** to it — in particular it does not
write `Inventory_Count`, which belongs to the Tallying Interface. Backfilling the workbook into
that table would relabel historical rows as staff counts and break the "more recent month wins,
tie goes to the staff count" precedence in `backend/catalog.py::load_current_stock`.

## Related

- `docs/POLICY_HOLDOUT.md` — the reorder-point coverage test this one is the counterpart to
- `docs/ACCEPTANCE_STANDARD.md` — condition 2, whose comparator this re-measures
- `docs/PRESCRIPTIVE_CONTRACT.md` — the rate/buffer contract supplying the reorder points
- `backend/catalog.py` — the existing reader for the same workbook
