# The forecast → prescriptive contract

What the prescriptive layer consumes, where each input comes from, and what happens when
there is nothing to consume. Implemented in `forecasting/policy.py`, wired in
`scripts/step5_prescriptive.py`, validated by `scripts/validate_policy_holdout.py`, pinned
by `tests/test_policy.py` and `tests/test_gates_can_fail.py`.

## Why there is a contract at all

`MAPE ≤ 20%` is retired, and not because it was missed. It is *degenerate* on this
catalogue: MAE is minimised by the conditional median, MASE is MAE over a constant so it
shares the minimiser, and on a series where 68,541 of 84,399 rows are zero the median **is**
zero. The method that wins the error metric is therefore the method that forecasts nothing
will sell — `rolling_median_30`, best mean MASE in the benchmark, **prices 0 of 266 SKUs**.
That is proven, not asserted (`docs/DEGENERATE_FORECAST.md`, pinned by
`tests/test_degenerate_forecast.py`).

So the prescriptive layer does not consume a point forecast. It consumes a **demand rate**
and an **uncertainty**, and what has to be reliable is the **policy** those two imply.

| | Input | Source |
| --- | --- | --- |
| Rate | units/day | trailing observed; flag where there is none (pooled fallback available, off by default) |
| Uncertainty | units over one lead time | empirical quantile of the policy's own prior-fold errors |
| Operating point | *q* | per **service tier**, from each SKU's own curve; a stock budget is the dial |

---

## 1. The demand rate: three states, and one of them is not a number

`Result_Prescriptive.rate_source`:

| State | Rule | SKUs |
| --- | --- | ---: |
| `observed` | positive trailing-365d window → own rate | 208 |
| `cluster_pooled` | thin rate shrunk toward its behavioural cluster — **off by default**, see below | 0 |
| `insufficient_data` | trailing-365d window is empty → **flagged, no rate, no reorder point** | 58 |
| | **eligible F+S** | **266** |

The third state is the change that matters. The previous code `continue`d past any SKU whose
trailing window summed to zero, so those 58 SKUs were not reported as unknown — they were
**absent from the output entirely**, and the screen showed nothing where a recommendation
belonged. They now get a row carrying the state, with `reorder_point` and `eoq` NULL on
purpose. A zero there renders as *"you have enough stock"*, which is a recommendation the
pipeline has no evidence for.

**The boundary, stated plainly:** first-season designs, one-off drops and genuinely dead
lines have no learnable rate *in principle*. For those the reliable output **is the flag**,
not a number. Pretending otherwise is what a rigorous panel catches.

### A correction to the original framing, from measurement

The pooled fallback was motivated by *"the 32 of 58 Fast SKUs with an empty trailing
month"*. That count is right, but the ratified basis is the trailing **365** days, and under
it those SKUs are already priced. What the 365-day basis actually drops is different:

- **58 SKUs have no rate at all**, and every one of them last sold between **2024-05-07 and
  2025-07-21** — all silent for over a year. Borrowing a cluster rate for those would
  *invent* demand for items that have demonstrated a year of zero: the mirror image of the
  failure the fallback was meant to prevent.
- **112 SKUs first sold inside the trailing year, and all 112 already have a positive
  rate.** The "new SKU whose window cannot be filled" case, which motivated the fallback, is
  **empty in this data**.
- **36 SKUs are priced off fewer than 10 units across the whole trailing year** (≈0.027/day;
  under an 18-day lead time, half a unit). Their rate exists but carries almost no evidence.

That last group is what pooling is for. So the fallback serves **thin** rates, not **missing**
ones, and the silent get the flag.

### The shrinkage

`rate = w·own + (1−w)·cluster`, with `w = sale_days / 10`, linear in the evidence the SKU
actually has and continuous at the boundary. Clusters come from `forecasting/clustering.py`
unchanged (K-means on density, mean non-zero size, CV, log total, log price), K = 4.

**The shrinkage now defaults OFF, on evidence.** Scored on a reserved window it bought
**+12.7 units served for +155.1 units held** — 12.2 held per extra unit served, against 5.0
from simply raising the buffer quantile — and it priced **no additional SKU** (coverage
identical either way, so it was never a coverage fix). It is dominated: the same service is
available more cheaply from the operating point. The code and the flag are kept so the
decision stays reversible and auditable; `--shrink` re-enables it.

Pooling is **leave-one-out** when enabled: a SKU's cluster rate is the median of the *other*
members. Without that, a K-means singleton gets "pooled" toward its own value and labelled
`cluster_pooled` while nothing was pooled — a no-op wearing a correction's label. That was a
real defect, caught by test, and it reclassified one SKU on live data.

With shrinkage off the rates reproduce the pre-contract values **exactly** — max deviation
0.0 on `avg_daily_demand`, 1.1e-13 on `eoq`. The insufficient-data flag is **not** disabled
by it.

---

## 1b. Service tiers: how much service each SKU's demand will accept

A single population-wide operating point does two wrong things at once. Sorting the scored
population into trailing-volume quintiles and sweeping the buffer quantile shows why:

| quintile | fill @ q=0.80 | fill @ q=0.95 | share of demand |
| --- | ---: | ---: | ---: |
| Q5 (highest) | 0.813 | **0.951** | 50% |
| Q4 | 0.706 | 0.827 | 14% |
| Q3 | 0.389 | 0.681 | 21% |
| Q2 | 0.280 | 0.525 | 10% |
| Q1 (lowest) | 0.117 | 0.288 | 6% |

Service converts into stock about **seven times** more efficiently at the top of that table
than at the bottom, and the bottom does not reach 0.38 at *any* quantile. So the flat
quantile under-serves the half of demand that could reach 0.95, and charges holding to chase
SKUs no reorder point can serve.

`Result_Prescriptive.service_tier`, assigned from each SKU's **own pre-origin folds**:

| Tier | Rule | Operating point | SKUs |
| --- | --- | --- | ---: |
| `servable` | reaches the target fill at some q ≤ 0.95 | the **smallest** q that does | 24 |
| `partial` | cannot reach it | stops at its own marginal knee | 166 |
| `not_stockable` | no q returns the efficiency floor | **none — covers expected demand, buys no buffer** | 18 |

Eight distinct operating points are in use across the catalogue (q = 0.50 … 0.95) where
there was previously one.

**Tiering is on EFFICIENCY, not on fill, and the two disagree sharply.** Writing off every
SKU that cannot reach 50% fill discards 54 SKUs carrying **28.5%** of demand — far too much
to call made-to-order. Writing off every SKU that cannot return 0.10 units served per unit
held discards a similar 52 SKUs carrying **1.8%**. The difference is the SKUs that are hard
to serve but *cheap* to serve: low fill on almost no stock. Fill alone cannot see them, and
the objective — maximise demand met per unit of stock — is about efficiency anyway.

The floor does double duty: it decides both *"not worth stocking"* and *"not worth stocking
further"*, so there is one number to defend rather than two.

**What it buys, measured.** On the reserved window, per-tier operating points against the
flat q = 0.80 they replace:

| Policy | Fill rate | Units held | Served / held |
| --- | ---: | ---: | ---: |
| **Tiered** | **0.6854** | **14,780** | **0.535** |
| flat q=0.80 | 0.6180 | 15,156 | 0.470 |
| flat q=0.95 | 0.7981 | 32,206 | 0.286 |

More demand met on *less* stock — a dominance, not a trade to adjudicate. Across four
rolling origins the tiering beat the flat quantile on **fill at 4 of 4**, and on
**efficiency at 2 of 4**: it reliably buys more service, but at some windows by spending
more stock rather than less. Both claims are reported; the weaker one is not dropped.

## 2. The uncertainty: empirical, at lead-time horizon

The retired buffer was `z·σ·√L` with z = 1.65 (F) / 1.04 (S). It assumes a symmetric normal
around the mean; the demand it buffers is right-skewed intermittent, so it is wrong in the
direction that matters.

The replacement is the **q-quantile of the SKU's own prior-fold policy errors**, expanding
window, strictly prior folds, floored at zero — the same estimator
`tools/service_frontier.py` uses, so the two curves are read on the same axis.

**Measured at the SKU's own lead-time horizon (14 / 18 / 28 days), not at 30.** This is a
deliberate departure from the benchmark frontier and the reason is not cosmetic. The
frontier takes 30-day-aggregate errors from `rolling_mean_30`, which is right for the
benchmark's periodic-review simulation. This policy is forecast-free and its reorder point
is continuous-review — `rate·L + buffer` — so the quantity at risk is demand over **one lead
time**. Taking a 30-day quantile and scaling it by `√(L/30)` would reintroduce exactly the
distributional assumption that moving to an empirical quantile exists to remove. Running the
backtest at the lead-time horizon puts the quantile directly in the unit the reorder point
needs, with no scaling step at all.

Every row records `buffer_source` (`empirical_quantile` or `normal_z_sigma_fallback`) and
carries the retired figure in `safety_stock_normal_legacy`, so the swing between the two
readings is visible — the same way this pipeline prices both readings of the ordering cost
rather than silently picking one.

Deployed: **7.46 units mean empirical buffer against 13.84 z·σ**. The empirical buffer is
*smaller* and, on the holdout below, serves *more* demand. "Bigger" would have been easy and
useless; calibrated is the claim, and `tests/test_policy.py` pins it as coverage calibration
rather than as magnitude.

---

## 3. The operating point is a dial, not an assertion

The operating point is now **per tier**, resolved from each SKU's own pre-origin curve, and
eight distinct quantiles are in use (0.50 … 0.95) where there was previously one.
`--buffer-quantile` remains the default for SKUs with no scoreable history, and
`--flat-quantile` restores the single population-wide point as a control.

`service ≥ 95%` is no longer encoded anywhere as a pass/fail. On the benchmark path a hard
arithmetic ceiling of **0.9490** applies (2,732 units across 584 folds are structurally
unservable before any model runs); on the policy path, reaching high service is not
impossible but is expensive and varies enormously by window — see the correction below.
Asserting 95% would produce a gate that mostly fails for reasons nothing in the pipeline
controls.

### The dial USTore actually turns: a stock budget

Asking the store to pick a buffer quantile asks them to interpret a number that means
nothing outside this repository. `scripts/validate_policy_holdout.py` instead reports a
**total stock budget**, in units they already count, allocated across SKUs greedily by
marginal units-served-per-unit-held so every unit goes where it converts best. One knob,
denominated in stock, with the service it buys beside it:

| Budget | Fill rate | Units held | Served / held |
| ---: | ---: | ---: | ---: |
| 0.5× | 0.4711 | 8,918 | 0.609 |
| **1.0×** | **0.6133** | **14,578** | **0.485** |
| 1.5× | 0.6500 | 25,390 | 0.295 |
| 2.0× | 0.6576 | 30,576 | 0.248 |

Returns flatten hard above ~1.5×: past that, extra stock buys almost nothing, which is the
single most useful thing this table says.

`z_value` is still populated. It is now a **comparison**, not the buffer.

---

## 4. Validation: policy, not point, on a reserved window

Scored by `scripts/validate_policy_holdout.py`. Rate, clusters, tiers and buffer quantiles
are fitted strictly before the origin they are scored against; a gate re-derives them with
the scored window blanked and requires an identical answer, because a docstring promising
`upto=split` is not evidence.

**The primary evidence is the rolling-origin distribution**, not a single window:

| Origin | Demand | Fill rate | Units held | Flat q=0.80 fill |
| --- | ---: | ---: | ---: | ---: |
| 2026-05-03 *(development set)* | 11,538 | 0.6854 | 14,780 | 0.6180 |
| 2026-02-02 | 7,249 | 0.9145 | 29,155 | 0.8012 |
| 2025-11-04 | 12,821 | 0.7352 | 18,930 | 0.6551 |
| 2025-08-06 | 15,064 | 0.6339 | 7,119 | 0.5840 |

**Median 0.7103, range 0.6339–0.9145.** The spread is the honest statement of what this
policy delivers; a single number would have implied a precision the data does not support.

**The most recent window is a development set, not a clean holdout**, and the report says so
rather than letting a reader assume otherwise. It was used to diagnose where the shortfall
sat, to test rate-window and lead-time sensitivity, and to choose efficiency over fill as
the tiering variable. Every fitted quantity is still selected strictly pre-origin, so the
*procedure* is clean — but the design was informed by looking, and that is researcher
degrees of freedom a rigorous panel is right to discount.

| Buffer | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: |
| **empirical, q = 0.80** | **0.6191** | 4,395.3 | **15,311.5** |
| normal z·σ (retired) | 0.6019 | 4,593.5 | 21,021.3 |

Same SKUs, same blocks, same realised demand — only the buffer differs. The empirical buffer
**dominates**: higher service *and* 27% less stock held. That is the measured justification
for the swap, and it is a dominance rather than a trade-off to adjudicate.

Full results, strata and the policy's own frontier: **`docs/POLICY_HOLDOUT.md`**.

---

## Findings the measurements produced that the spec did not anticipate

Reported rather than absorbed, per the work direction's own rule.

**1. A correction, kept visible: there is no proven service ceiling here.** An earlier
version of this document and of `validate_policy_holdout.py` called the worst-block figure —
commit every SKU's largest pre-origin lead-time block, reaching 0.8047 — a *ceiling*, and
concluded that 95% service was unreachable. **That was wrong.** A flat `q = 0.98` buffer
reaches **0.8265 while holding less stock** (37,583 units against 65,080), because committing
the single largest past block over-stocks quiet SKUs without helping lumpy ones, and because
on a rising series an empirical quantile can commit more than any block ever observed. One
rolling origin reached **0.9145**. The honest reading is narrower and still useful: even
extreme history-based commitment misses about a fifth of demand, which measures how lumpy
this demand is — not what is achievable.

**2. There is no interior knee at q ≈ 0.80 on the policy's own frontier.** Marginal holding
cost goes 5.0 (q=0.80) → 9.4 (q=0.95), a **1.88×** rise where `tests/test_service_frontier.py`'s
own knee test requires **>2×**. Cost rises *steadily* rather than bending. The benchmark
frontier's knee is real on the benchmark's curve; it is **inherited, not confirmed**, on this
one — which is exactly why the operating point is now chosen per tier from each SKU's own
curve rather than set population-wide.

**3. The cluster-pooled fallback is dominated.** +12.7 units served for +155.1 units held —
12.2 held per extra unit served, against 5.0 from the dial — and it prices no additional SKU.
Now **off by default**, code and flag kept.

**4. Achievable service varies enormously by window** — 0.6339 to 0.9145 across four rolling
origins, on the same policy. Any single-number claim about this system's service level is
overstated by construction. The spread is the result.

**5. SKU-level fragmentation is not a lever.** Pooling sizes and colours of the same design
was tested and ruled out: only **11 design families covering 25 of 266 SKUs**. Lead time is
not a lever either — fill moves only 0.557 → 0.603 across L = 7 … 28 days, so the provisional
lead-time estimates are not distorting the result and the site visit will not overturn it.
The rate window is second-order: a 120-day window gains +2.2pp fill and −15% holding at
q = 0.80 on a fixed population, but the gain vanishes at q = 0.95 and it costs 63 SKUs of
coverage.

---

## What this does not establish

- **This is a coverage test of the reorder point, not a full inventory simulation.** A full
  simulation needs an opening stock per SKU and `Inventory_Count` is empty in this database.
  Inventing the starting condition and reporting the result as a measurement would be worse
  than not measuring.
- **The cost inputs remain provisional** pending the USTore site visit. Holding is reported
  in **units**, never pesos, for that reason.
- **The acceptance criterion itself still needs adviser sign-off** (§7). This work *measures*
  the policy against a frontier and reports the operating point. It deliberately does not
  encode a replacement pass/fail threshold — that ruling is not the pipeline's to make.

## Evidence integrity: the denominator is now observed days

139 of 821 calendar days (**16.9%**) carried no record of a sale *or* a closure, and the
pipeline counted every one as zero demand. The store demonstrably trades seven days a week —
79 Saturdays and 76 Sundays are tallied — so those were not closures. Demand rates now divide
by **days actually observed**, not by the full window. `Fact_Sales` is untouched, so every
committed invariant holds; `--zero-fill` restores the old denominator as a control (verified
exact: 365 days vs 355).

**The correction barely moves service, and that is a finding rather than a disappointment.**
At the worst-observed origin the rate rises **1.24×** as expected — and the buffer *falls* by
more (−491 units against +365), leaving committed stock slightly lower. The empirical buffer
had been **absorbing the bias**: a systematically low rate produces systematically positive
errors, so the quantile of those errors grew to cover them. Correcting the rate removes both
the bias and its compensating buffer.

Two things follow. The rate USTore is *told* is now correct, which matters independently of
service. And the policy is shown to be **robust to this class of data error by
construction** — the same reason the lead-time sensitivity was small.

123 of the 139 unevidenced days sit in 2024, so recent windows are 96–98% observed and the
2025-08-06 origin is 80.6%. Every reported figure now carries its observability.

---

## The acceptance criterion

`MAPE ≤ 20%` is replaced by a four-condition standard — **`docs/ACCEPTANCE_STANDARD.md`**,
encoded in `tools/acceptance_standard.py`, proved falsifiable by
`tests/test_acceptance_standard.py`. Thresholds are set a priori, from what the system must
*do*, not from what it scores.

**Current verdict: NOT ACCEPTED — 13 of 14 checks pass.** Forward demand coverage fails at
**0.8830** against an a priori 0.90, because dormant SKUs revive and carry 11.7% of realised
demand. Lengthening the rate window moves that by 0.0002 across a doubling, so it is the
cold-start boundary, not a tuning gap. The failure is reported, not repaired.

---

## Gates

`scripts/step5_prescriptive.py` fails the run if any of these break:

- **non-degeneracy** — priced share ≥ 0.78 (today 208/266 = 0.7820). *The gate there was no
  version of before*, and it is the one that catches `rolling_median_30`: best error metric,
  0 SKUs priced.
- a flagged row carrying a reorder point or an EOQ → 0
- a priced row *missing* a reorder point → 0 (so the flag cannot hide a failure to compute)
- every row states a known `rate_source` and, if priced, a known `service_tier` and
  `buffer_source`
- a `not_stockable` row holding safety stock → 0 (*if it can still hold buffer, the tier is
  decorative*)
- a `servable`/`partial` row with no operating point → 0
- `rows == priced × 2 + flagged`

`scripts/validate_policy_holdout.py` additionally fails if the tiering does not beat the flat
quantile it replaces — on fill, on stock, and at a **majority of rolling origins**, so a win
confined to the window the design was chosen on does not count.

`tests/test_gates_can_fail.py` feeds the non-degeneracy gate a populated table in which every
SKU is flagged and requires it to **fail** — every legacy gate passes against that table,
because they are all existence-negations and there is no priced row to violate them. It does
the same for the tier gate, with a `not_stockable` row that still carries safety stock. A
gate that cannot fail is not a gate.
