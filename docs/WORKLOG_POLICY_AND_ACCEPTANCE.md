# Work log — the prescriptive contract, service tiers, and the acceptance standard

Covers the work that replaced the `MAPE ≤ 20%` gate with a measured stocking policy and a
four-condition acceptance standard. Grounded against `ustore.db` throughout; every figure
below is reproducible from the scripts named beside it.

**State at the end of this log:** 440 tests pass, 22/22 database invariants hold, the
benchmark evidence base is untouched, and `tools/acceptance_standard.py` reports **13 of 14
checks passing**. The failing check and its analysis are in §6.

---

## 1. What the prescriptive layer now consumes

Not a point forecast. `MAPE ≤ 20%` is degenerate on this catalogue — MAE is minimised by the
conditional median, MASE shares that minimiser, and on a series where 68,541 of 84,399 rows
are zero the median **is** zero, so the error-minimising forecast is "nothing will sell".
`rolling_median_30` wins the benchmark on MASE and prices **0 of 266 SKUs**
(`docs/DEGENERATE_FORECAST.md`).

The layer consumes a demand **rate** and an **uncertainty**, and what has to be reliable is
the **policy** those imply. Implemented in `forecasting/policy.py`, wired into
`scripts/step5_prescriptive.py`.

### Demand rate — three states, one of which is not a number

`Result_Prescriptive.rate_source`:

| State | Rule | SKUs |
| --- | --- | ---: |
| `observed` | positive trailing window → own rate | 208 |
| `cluster_pooled` | thin rate shrunk toward its behavioural cluster — **off by default** | 0 |
| `insufficient_data` | empty trailing window → **flagged, no rate, no reorder point** | 58 |
| | **eligible F+S** | **266** |

The third state is the substantive change. The previous code `continue`d past any SKU whose
trailing window summed to zero, so 58 SKUs were not reported as unknown — they were **absent
from the output entirely**, and the screen showed nothing where a recommendation belonged.
They now carry the state with `reorder_point` and `eoq` NULL on purpose: a zero there renders
as *"you have enough stock"*, which is a recommendation the pipeline has no evidence for.

`backend/app.py` previously coerced that NULL to `0.0` and rendered `needs_reorder = False`.
It now returns an explicit state and a manual-review note.

### Uncertainty — empirical, at lead-time horizon

The retired buffer was `z·σ·√L`, which assumes a symmetric normal around the mean; the demand
it buffers is right-skewed intermittent. The replacement is the **q-quantile of each SKU's own
prior-fold policy errors**, expanding window, strictly prior folds.

Measured at each SKU's own **lead-time horizon** (14/18/28 days), not scaled down from a
30-day quantile. The reorder point is continuous-review — `rate·L + buffer` — so the quantity
at risk is demand over one lead time; taking a 30-day quantile and scaling by `√(L/30)` would
reintroduce the distributional assumption the empirical quantile exists to remove.

Deployed: **7.46 units mean empirical buffer against 13.84 z·σ**. Smaller, and it serves more
demand. `safety_stock_normal_legacy` carries the retired figure per row for comparison.

---

## 2. Service tiers

A single population-wide operating point does two wrong things at once. Sorting the scored
population into trailing-volume quintiles and sweeping the buffer quantile:

| quintile | fill @ q=0.80 | fill @ q=0.95 | share of demand |
| --- | ---: | ---: | ---: |
| Q5 (highest) | 0.813 | **0.951** | 50% |
| Q4 | 0.706 | 0.827 | 14% |
| Q3 | 0.389 | 0.681 | 21% |
| Q2 | 0.280 | 0.525 | 10% |
| Q1 (lowest) | 0.117 | 0.288 | 6% |

Service converts into stock about **seven times** more efficiently at the top than at the
bottom, and the bottom does not reach 0.38 at *any* quantile. So the flat quantile
under-serves the half of demand that could reach 0.95 and charges holding to chase SKUs no
reorder point can serve.

`Result_Prescriptive.service_tier`, assigned from each SKU's **own pre-origin folds**:

| Tier | Rule | Operating point | SKUs |
| --- | --- | --- | ---: |
| `servable` | reaches target fill at some q ≤ 0.95 | the **smallest** q that does | 24 |
| `partial` | cannot reach it | stops at its own marginal knee | 166 |
| `not_stockable` | no q returns the efficiency floor | none — covers expected demand, **no buffer** | 18 |

Eight distinct operating points are now in use (q = 0.50 … 0.95) where there was one.

**Tiering is on efficiency, not fill, and the two disagree sharply.** Writing off every SKU
that cannot reach 50% fill discards 54 SKUs carrying **28.5%** of demand. Writing off every
SKU that cannot return 0.10 units served per unit held discards a similar 52 carrying
**1.8%**. The difference is SKUs that are hard to serve but *cheap* to serve — low fill on
almost no stock — which a fill-based rule cannot see.

**What it buys**, on the reserved window:

| Policy | Fill | Units held | Served / held |
| --- | ---: | ---: | ---: |
| **Tiered** | **0.6851** | **14,751** | **0.536** |
| flat q=0.80 (pre-tiering) | 0.6188 | 15,122 | 0.472 |
| flat q=0.95 | 0.7980 | 32,057 | 0.287 |
| normal z·σ (retired) | 0.6022 | 21,029 | 0.330 |
| naive stocking (no model) | 0.4264 | 3,249 | 1.514 |

More demand met on less stock than the flat quantile it replaces, on this measure.

> **Narrowed 2026-09-23 — it is a trade.** Adjudicated against real opening stock and then
> against a synthetic shelf that makes all 242 priced SKUs observable
> (`docs/INVENTORY_SIMULATION.md`, `--synthetic-cover`). On the 52 SKUs with measured stock the
> two rules are **not separable**; from one lead-time's cover upward they separate cleanly and
> the tiering holds **more** stock at every depth where it wins more demand. The gain is real
> (+1.0 to +2.8 points of fill) and it is bought.

---

## 3. Validation on rolling origins

`scripts/validate_policy_holdout.py`. Rate, clusters, tiers and buffer quantiles are fitted
strictly before the origin they are scored against; a gate re-derives them with the scored
window blanked and requires an identical answer.

| Origin | Observed share | Demand | Fill | Flat q=0.80 | Naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-05-03 | 0.9781 | 11,538 | 0.6851 | 0.6188 | 0.4264 |
| 2026-02-02 | 0.9726 | 7,249 | **0.9149** | 0.8023 | 0.8539 |
| 2025-11-04 | 0.9644 | 12,821 | 0.7277 | 0.6485 | 0.5928 |
| 2025-08-06 | 0.8055 | 15,064 | 0.5987 | 0.5573 | 0.4338 |

**Median 0.7064, range 0.5987–0.9149.** The spread is the honest statement: service depends
more on what demand does in a given quarter than on the policy, and any single-figure claim
about this system overstates its precision.

**The most recent window is a development set, not a clean holdout.** It was used to diagnose
where the shortfall sat, to test rate-window and lead-time sensitivity, and to choose
efficiency over fill as the tiering variable. Fitted quantities are still selected strictly
pre-origin, so the procedure is clean — but the design was informed by looking, and that is
researcher degrees of freedom a rigorous reader is right to discount.

---

## 4. Evidence integrity — the denominator

139 of 821 calendar days (**16.9%**) carried no record of a sale *or* a closure, and the
pipeline counted every one as zero demand. The store demonstrably trades seven days a week —
79 Saturdays and 76 Sundays are tallied — so those were not closures. Rates now divide by
**days actually observed**. `Fact_Sales` is untouched, so every committed invariant holds;
`--zero-fill` restores the old denominator as a control (verified exact: 365 days vs 355).

**The correction barely moves service, and that is a finding rather than a disappointment.**
At the worst-observed origin the rate rises **1.24×** as expected — and the buffer *falls* by
more (−491 units against +365), leaving committed stock slightly lower. The empirical buffer
had been **absorbing the bias**: a systematically low rate produces systematically positive
errors, so the quantile of those errors grew to cover them.

Two things follow. The rate USTore is *told* is now correct, which matters independently of
service. And the policy is shown to be **robust to this class of data error by construction**
— the same reason the lead-time sensitivity is small.

123 of the 139 unevidenced days sit in 2024, so recent windows are 96–98% observed and the
2025-08-06 origin is 80.6%. Every reported figure now carries its observability.

---

## 5. Levers tested and ruled out

Recorded so the same ground is not re-covered.

| Lever | Measured | Verdict |
| --- | --- | --- |
| **Pool SKUs by design** (sizes/colours of one product) | 11 design families, 25 of 266 SKUs; density 0.078 → 0.118 for those alone | Ruled out — the catalogue is not fragmented enough to matter |
| **Lead time** (14/18/28 are provisional) | fill 0.557 / 0.581 / 0.603 / 0.593 at L = 7 / 14 / 18 / 28 | Not a driver, and reassuring: the result is not hostage to the estimate |
| **Rate window** (365d on a growing catalogue) | Fixed population: 120d gives +2.2pp fill and −15% holding at q=0.80, but +0.16pp at q=0.95, and costs 63 SKUs of coverage | ~~Second-order with a real coverage cost~~ — **reopened, see below** |
| **Trend-aware rate** | the 120d-vs-365d result is itself a crude trend proxy, worth ~2pp | ~~Not worth reopening~~ — **superseded with the row above** |
| **Cluster-pooled rate fallback** | +12.7 units served for +155.1 held (12.2 per unit) against 5.0 from the dial; prices no additional SKU | Dominated — defaulted off, code and flag kept |
| **Cold-start donor (analog) rate** (added 2026-09-23) — borrow a rate from similar existing items where the SKU has none of its own | On the 103 cold-start SKUs carrying 11.68% of demand: `product_type` reaches 0.1552 fill on 4,853 units held, against **0.1631 for the uncategorised donor scaled to hold the same stock**. `category` is worth **+0.10pp** over that control. Price band's sign flips with the band count (−0.0058 … +0.0135) | Ruled out — the taxonomy sits **below** a matched-stock control, so it buys fill with stock rather than information; and any donor model flips acceptance condition 1b from FAIL to PASS while leaving 82% of that demand unserved. `docs/COLD_START_ANALOG.md`, `tools/cold_start_donor_test.py` |

### The rate window was mis-ranked, and why that is the more useful finding

Both window rows above were ranked **against a comparison that has since been corrected**. They
were called second-order because the policy apparatus they were measured beside appeared to beat
naive stocking by **26 points** (reorder-point coverage, `docs/POLICY_HOLDOUT.md`).
`docs/INVENTORY_SIMULATION.md` measures that same comparison against real opening stock at
**0.6 points**. A 2.2-point lever is not second-order next to 0.6 — it is roughly four times it.

The stated cost — 63 SKUs losing their rate, which would push the already-failing acceptance
condition 1b lower — was real and is now **removed**. `forecasting/policy.py::resolve_rates`
takes `short_window`, preferring the short window only where a SKU has the sale-days to support
it and falling back to 365d otherwise. Measured across four rolling origins at 90/120/180/270
days, forward coverage stays at **0.8830** and the minimum priced count at **150** — identical
to committed, at every setting.

What the cascade buys, and the interference it exposed, are in
`docs/PRESCRIPTIVE_CONTRACT.md` §1a. In short: it **dominates the flat 365d baseline**
(+2.2pp fill on 12% less stock, full population, coverage preserved), and it **does not stack
with the service tiering** — the two are substitutes, and under the inventory simulation adding
the tiering on top of the cascade is worse on both axes.

**The mis-ranking is the part worth carrying.** It is the same class of error as the three
metric failures this project already documents — `MAPE ≤ 20%`, the trailing-coverage tautology,
and reorder-point coverage itself. In each case a decision was made against a number that did
not mean what it appeared to. Here the number was the project's own headline result, and it
closed a lever for four days.

---

## 6. The acceptance standard

`docs/ACCEPTANCE_STANDARD.md` states it, `tools/acceptance_standard.py` runs it,
`tests/test_acceptance_standard.py` proves every condition can fail. Thresholds are set **a
priori**, from what the system must do to be useful rather than from what it scores.

| # | Condition | Threshold | Result |
| --- | --- | --- | --- |
| 1 | **Actionability** | every eligible SKU carries a state; no silent zeros | 266/266, zero ✅ |
| 1b | **Forward demand coverage** | ≥ 0.90 | **0.8830 ❌** |
| 2 | **Beats the no-model alternative** | policy fill > naive at *every* origin | 4 of 4 ✅ |
| 3 | **Not dominated** | no simpler policy gives ≥ service at ≤ stock | dominated by none ✅ |
| 4 | **Evidence integrity** | headline origins ≥ 90% observed, all reported | 3 of 4, 4th disclosed ✅ |

**Efficiency is deliberately not a condition.** Units-served-per-unit-held is degenerate in
exactly the way MAPE is: a policy that stocks one unit and sells it scores perfect efficiency
and serves nobody. Measured — the naive baseline beats this policy on efficiency at **4 of 4**
origins while serving far less demand. Service and cost are judged together, as a dominance,
or not at all.

### The failing check, and what it turned out to mean

A first draft of the coverage check measured the **trailing** window and scored a perfect
1.0000. That looked like a pass and was arithmetic: a SKU is flagged `insufficient_data`
precisely *because* its trailing window is empty, so flagged SKUs contribute zero to trailing
demand by construction. It could not fail, so it was not a condition. Rewritten to measure
**forward**, on demand that actually arrived, it fails at 0.8830.

Decomposing that shortfall across all four origins:

| Source of demand the system could not price | units | share of all demand |
| --- | ---: | ---: |
| SKUs that had **never sold a unit** before the decision point | 6,257 | **11.68%** |
| SKUs with history that had gone quiet | 12 | 0.02% |

**99.8% of the shortfall is products with no sales history at all.** Nothing fitted on sales
history can forecast those. The ceiling on forward coverage for any such method is **0.8832**;
this system achieves **0.8830**, within 0.0002 — capturing **99.98%** of what was learnable.
Lengthening the rate window from 365 to 730 days moves coverage by 0.0002, confirming the gap
is the cold-start boundary rather than a tuning deficit.

**Open:** the threshold is mis-specified — it requires the logically impossible — and the
planned correction is to measure coverage against the achievable ceiling rather than against
an absolute, keeping the original 0.90 bar and its failure on the record as the audit trail.
**That correction is not yet implemented.** The standard as committed reports NOT ACCEPTED.

---

## 7. Named limitation: cold start

Items with no sales history are unforecastable from sales history. The system flags them for
manual review rather than guessing, which is the correct operational behaviour, and they
account for roughly one unit in nine traded.

A price-and-category **analog model** — borrowing a rate from similar existing items rather
than from the item's own history — could in principle price them. ~~It is named here as future
work and deliberately not built.~~

**Built and measured, 2026-09-23** — `tools/cold_start_donor_test.py`,
`docs/COLD_START_ANALOG.md`. It does not work, and the reason it does not is more useful than
the rates it produces:

- Against a **matched-stock control** — the uncategorised donor rescaled to hold the same stock —
  the eight-bucket product taxonomy is **below the curve** (0.1552 against 0.1631). It was not
  extracting a better rate; it was committing more. The apparel/non-apparel label buys
  **+0.10pp** of fill over scaling alone.
- **Price band is not established either.** Its verdict flips sign with a band count that has no
  principled basis (−0.0058 … +0.0135 across 2/3/4/5/8 bands). Reported, not resolved.
- **The trap is the criterion, not the model.** Condition 1b counts a SKU as covered if it is
  priced *at all*. A donor model prices every cold-start SKU, so coverage moves 0.8830 → 0.9998
  and the verdict flips to **ACCEPTED on demand that is still 82% unserved** — the
  trailing-coverage tautology re-entering the same condition by another door.

Nothing was deployed and no threshold was changed. The correction 1b actually needs is the one
in §6 — measure against the achievable ceiling — which raises the floor without making the
condition unfailable, and it is still not implemented.

---

## 8. Reproducing

```bash
python scripts/step5a_set_lead_times.py
python scripts/step5_prescriptive.py                 # gates all-PASS
python scripts/step5_prescriptive.py --zero-fill     # control: old denominator
python scripts/step5_prescriptive.py --flat-quantile # control: no tiering
python scripts/validate_policy_holdout.py            # rolling origins
python tools/acceptance_standard.py                  # the verdict
python tools/assert_invariants.py --phase a10        # 22/22
python tools/service_frontier.py                     # benchmark evidence untouched
pytest tests/ -q                                     # 440 passed
```

`scikit-learn` moved into `requirements/requirements.txt` when the behavioural-cluster
fallback entered the deployed path. It remains a runtime dependency even with the fallback
defaulted off, because `forecasting/policy.py` imports it on the pooling path.

## 9. Still open

- **Cost inputs remain provisional** pending the USTore site visit; holding is reported in
  units, never pesos.
- **Client rulings outstanding**: the two dates that traded on a flagged closure, the May 2024
  discrepancy of 296 units, and the four price-suffix families.
- **Whether the 2024 gap is a data gap or a genuine closure is a human call.** Unevidenced
  days are treated as unobserved, the conservative reading; confirming a closure means
  flagging those days `is_store_closed`, which the pipeline picks up with no code change.
- **`Inventory_Count` is empty**, so the holdout scores reorder-point coverage rather than a
  full inventory simulation — an opening stock per SKU would have to be invented otherwise.
  **Partly closed.** The table is still empty, but the historical inventory workbook
  (`data/USTore_inventory_excel_long_mapped.csv`, 23 monthly counts, already the source of
  `step5_prescriptive.UNITS_ON_HAND_SOURCE`) supplies real opening stock for 27% of scored
  SKUs. `tools/inventory_simulation.py` simulates them; `docs/INVENTORY_SIMULATION.md`
  reports it. Two findings worth carrying: replenishing at all is worth **+26 points** of
  fill over not replenishing, but the tiered policy's margin over **naive** stocking is
  **0.6 points at 6% more stock** — against 26 points under reorder-point coverage. The
  proxy overstates the comparator gap that acceptance condition 2 rests on.
