# Work log — the inventory simulation, the rate-window cascade, and what they cost the record

Covers the session of **2026-09-23**, which began as a question about categorization and ended
by re-measuring two committed claims. Grounded against `ustore.db` throughout; every figure is
reproducible from the script named beside it.

**State at the end of this log:** 462 tests pass, 22/22 database invariants hold,
`tools/acceptance_standard.py` output is **byte-identical** to the start of the session (13 of
14 checks, forward coverage 0.8830), `ustore.db` is unmodified, and `Inventory_Count` is still
0 rows. No threshold, expected value or verdict was changed.

---

## 1. What was built

| Artifact | |
| --- | --- |
| `tools/inventory_simulation.py` | Inventory simulation on real opening stock. Four arms, two ordering-cost scenarios, `--stock-scale` depth sweep, `--short-window`, `--max-staleness-months`. Read-only. |
| `tests/test_inventory_simulation.py` | 15 tests — leakage gate, staleness gate, lost-sales, reorder-boundary mechanism |
| `docs/INVENTORY_SIMULATION.md` | The write-up |
| `data/inventory_simulation.csv` | Measured shelf |
| `data/inventory_stock_depth.csv` | Counterfactual depth sweep |
| `forecasting/policy.py` | `short_window` cascade in `resolve_rates` **and** `trailing_rate_fn` |
| `tests/test_policy.py` | +7 tests (25 → 32) for the cascade |
| `docs/PRESCRIPTIVE_CONTRACT.md` §1a | The rate window, reopened |

Narrowed, not deleted: `docs/POLICY_HOLDOUT.md` (via its generator),
`docs/PRESCRIPTIVE_CONTRACT.md`, `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` §5 and §9,
`docs/ACCEPTANCE_STANDARD.md` condition 2.

---

## 2. Categorization does not rescue the forecast — measured, not yet written up

The session's opening question. The cold-start SKUs — **103 distinct, 6,257 units, 11.68%** of
forward demand, 99.8% of the coverage shortfall — are the one place categorization had never
been tried, as a **donor** (analog) rate borrowed from similar existing items.

Scored on the four holdout origins, fitted strictly pre-origin, on the cold-start subset only.

> **Superseded 2026-09-23 by `tools/cold_start_donor_test.py`.** The table below is this session's
> ad-hoc run. It records neither its scored set nor its donor statistic precisely enough to
> reconstruct, and the tool does not reproduce its levels — two candidate reconstructions bracket
> its held figures without hitting them. **Every structural figure and the full rule ordering do
> reproduce exactly**, so this is a level difference rather than a contradiction, but the levels
> below should not be quoted. `docs/COLD_START_ANALOG.md` is the source.

| Donor rule | Fill | Units held | Served/held |
| --- | ---: | ---: | ---: |
| commit nothing (current system) | 0.0000 | 0 | — |
| borrow from everything (no categorization) | 0.1309 | 2,033 | 0.390 |
| borrow from same category (apparel/non-apparel) | 0.1326 | 2,061 | 0.390 |
| borrow from same product type (8 buckets) | 0.1445 | 2,500 | 0.350 |
| *control:* everything × 1.2 | **0.1513** | **2,440** | 0.376 |
| borrow from same price band | 0.1556 | 2,405 | 0.392 |

**The category label is worth +0.17pp** here, **+0.10pp** against the tool's matched-stock
control. Product type is *dominated* by simply scaling the global donor — the control gets more
fill on less stock, and the tool confirms it at every setting tested (0.1552 against 0.1631 for
the uncategorised donor rescaled to hold the same stock). ~~Only price band carries marginal
signal~~ — **that one does not survive.** Against a swept control frontier rather than a single
×1.2 point, price band's verdict flips sign with the quantile-band count (−0.0058 … +0.0135
across 2/3/4/5/8 bands), a free parameter this log never stated. Price is not a category, and on
this evidence it is not a signal either. Consistent with
`POOLING_AND_CLUSTERING_EXPERIMENTS.md` §3 (drinkware, the most homogeneous group, pooled
worst) and §11 (behavioural clustering beat every manual grouping).

**A trap to record before anyone builds this.** Acceptance condition 1b counts a SKU as
*covered* if it is priced **at all**, regardless of whether the price serves any demand. A
donor model therefore moves coverage 0.8830 → ~1.00 and flips the verdict to ACCEPTED while
serving 13% of that demand — the same tautology already caught and removed from the
trailing-coverage draft.

**Written up 2026-09-23.** `docs/COLD_START_ANALOG.md`, reproducible from
`tools/cold_start_donor_test.py` and pinned by `tests/test_cold_start_donor.py`; the sixth row is
now in `WORKLOG_POLICY_AND_ACCEPTANCE.md` §5 and §7 no longer calls the analog model unbuilt. It
answers the adviser's own suggestion with a measurement, and the answer is no.

---

## 3. `Inventory_Count` is empty; the inventory data is not

A correction made mid-session, and the most consequential turn in it.

The **table** is empty (0 rows) — it is fed by the Digital Tallying Interface and the store has
not tallied through the app. The **historical workbook** is not:
`data/USTore_inventory_excel_long_mapped.csv` holds **19,049 rows across 23 monthly counts**
(2024-11 → 2026-04), 301 canonical items, all 301 joining cleanly to `Dim_Product.item_name`
(unique, 519/519 — no fan-out). `backend/catalog.py::load_csv_stock` already reads it, and
`step5_prescriptive.UNITS_ON_HAND_ESTIMATE` is already derived from it.

The loader reconciles exactly: month totals **27,852 / 31,106 / 30,799** at the three usable
origins and **36,051** for 2026-03 — that last figure is `UNITS_ON_HAND_ESTIMATE` to the unit,
arrived at independently.

**Not backfilled into `Inventory_Count`, deliberately.** `create_schema.py:245` documents that
table as staff-entered counts and names the CSV "its digital counterpart";
`catalog.load_current_stock` merges the two with a documented precedence ("more recent month
wins, tie goes to the staff count"). Backfilling would relabel historical rows as staff counts
and destroy that rule.

Coverage bounds everything below: **73 of 266 scored SKUs (27%), ~18% of demand.** After the
priced filter and a one-month staleness cap, **28 / 36 / 45** SKUs are simulated at the three
origins. The 2026-05-03 origin drops — its nearest count is 2026-04, a partial sheet (**187 of
1,416** quantities filled, 369 units against 36,051), already excluded by
`UNITS_ON_HAND_SOURCE` for the same reason.

---

## 4. The inventory simulation, and what it cost two committed claims

Real opening stock, lost sales, continuous review on inventory position. Pooled over three
origins:

| Arm | Fill | Mean on hand | Stockout days |
| --- | ---: | ---: | ---: |
| **tiered** (committed policy) | **0.9345** | 9,486 | 30 |
| flat q=0.80 | 0.9301 | 9,201 | 40 |
| naive (no model) | 0.9281 | 8,935 | 39 |
| **none** (no replenishment) | **0.6745** | 5,826 | 248 |

**Replenishing at all is worth +26 points of fill. Which rule you replenish by is worth 0.6.**

Two consequences for the record:

**(a) Reorder-point coverage overstates the margin over naive roughly fortyfold.** The same
comparison reads 0.6851 against 0.4264 under the proxy. The mechanism is not mysterious:
coverage scores each lead-time block as though the shelf were empty at its start, so the buffer
must absorb all of the block's variance; a real shelf carries stock across blocks and absorbs
most of it for free. **Acceptance condition 2 rests on that proxy.**

**(b) The tiering's dominance over flat q=0.80 does not reproduce.** `POLICY_HOLDOUT.md` reports
"more demand met on less stock — a dominance, not a trade." Under simulation service is
indistinguishable and the tiering holds **more** (9,486 against 9,201) where the proxy said
less (14,751 against 15,122).

It is *not* that the tiering collapses into the flat rule — only **12–17%** of SKUs are assigned
exactly q=0.80, the rest spreading across 0.50–0.95 with masses at both ends. It makes
materially different per-SKU bets that wash out in aggregate. But the tiering is **fitted on
145–179 SKUs and observable on 28–45**, and pooled gaps are 14–64 units on 4,565 where single
SKUs move hundreds. **Not separable at this sample size** — measured-and-unresolved, not
measured-and-refuted.

**Adjudicated 2026-09-23** — `--synthetic-cover` in `tools/inventory_simulation.py`,
`docs/INVENTORY_SIMULATION.md`. The suspect was sample size and it was the right suspect, but
the verdict it was hiding is not the one the contract carries:

| Shelf | SKUs | Δ fill (tiered − flat80) | 95% CI | Δ stock | Verdict |
| --- | ---: | ---: | --- | ---: | --- |
| measured *(the anchor)* | 52 | +0.0044 | [−0.0000, +0.0111] | +3.1% | not separable |
| synthetic, C = 4 … 24 | 242 | +0.0096 … +0.0280 | all clear of zero | **+0.6% … +17.5%** | **trade** |

Separable at 5 of 10 depths, **a dominance at none**. The tiering wins real fill once enough
SKUs are observable, and holds more stock at every depth where it wins. `POLICY_HOLDOUT.md`'s
*"more demand met, on no more stock"* is narrowed in its own generator; the honest claim is a
purchased gain — at the depth bracketing USTore's shelf, +0.96 points of fill for 0.6% more
stock.

One mechanism worth carrying: at C ≤ 1 the two arms are identical **by construction**, because
100% of SKUs open below *both* reorder points and so both rules fire on day zero. A zero there
is not indifference, and the sweep prints the share of SKUs in that state so the rows cannot be
misread.

---

## 5. Shelf depth is the moderator: the forecast's value is contingent

The result above is confounded — the shelf opens at **4.2× the window's demand**. Two checks
rule out the alternatives: the covered subset is *slower*-moving than the catalogue (**11%
Fast against 26%**, median 78 units/SKU against 125), and `none` places zero orders yet meets
67% of demand.

`--stock-scale` re-runs on a multiplied shelf. **Only 1.0× is a measurement; every other row is
a counterfactual** and is labelled `stock_basis = counterfactual`.

| Shelf ÷ demand | tiered | flat q=0.80 | naive | none | **tiered − naive** |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.53× | 0.8519 | 0.8792 | 0.8312 | 0.2607 | 0.0208 |
| 0.79× | 0.8738 | 0.8635 | 0.8294 | 0.3257 | **0.0444** |
| 1.06× | 0.8845 | 0.8819 | 0.8412 | 0.3832 | **0.0433** |
| 2.12× | 0.9199 | 0.9292 | 0.9143 | 0.5287 | 0.0056 |
| **4.24× — measured** | 0.9345 | 0.9301 | 0.9281 | 0.6745 | **0.0064** |
| 6.36× | 0.9567 | 0.9428 | 0.9558 | 0.7426 | 0.0010 |

**The apparatus is worth 0.6 points at USTore's current shelf and 4.4 points near one
lead-time's cover — about seven times more.** It beats naive at every depth, so it is never
harmful; what changes is how much it is worth.

The operational reading: this project exists to *reduce* inventory. As it succeeds, cover falls
toward the 0.8–1.1× band where the gap is widest. **The forecast becomes more valuable as the
project's own recommendations are implemented.**

### Fill is not monotone in shelf depth, and that is not a bug

An earlier draft of the sweep asserted monotonicity as a correctness check. It is wrong.
Continuous review fires on inventory **position**, so a deeper shelf delays the first trigger
and slides every later order with it; inside a finite window one fewer order lands, and
crossing that boundary the shelf gains Δ while forgoing a whole order of Q. Reproduced on one
SKU: **opening 80 serves 344 where opening 70 serves 346.** Pinned by
`test_more_opening_stock_can_serve_less`.

---

## 6. The rate window was mis-ranked — and the cascade removes its cost

`WORKLOG_POLICY_AND_ACCEPTANCE.md` §5 closed this lever as *"second-order with a real coverage
cost"*: a flat 120-day window bought +2.2pp fill and −15% holding but cost **63 SKUs** their
rate entirely.

**The measurement reproduces exactly** on its own terms (fixed population, flat q=0.80, dev
origin): 63 SKUs lost, 0.6188 → 0.6415, −14.2% holding. Nothing was wrong with it.

**The ranking was wrong.** "Second-order" was judged beside an apparatus the evidence then said
beat naive by 26 points. §4 measures that at 0.6. A 2.2-point lever is roughly *four times* it.

`resolve_rates(..., short_window=N)` adds one layer: prefer the short window where a SKU has the
sale-days to support it, otherwise fall through to the committed rule on 365d. No new
`rate_source` value — `RATE_SOURCES` is controlled vocabulary reaching `Result_Prescriptive`,
`backend/app.py` and the invariants, so the existing **`window_days`** field carries it.
`trailing_rate_fn` takes the same cascade and **must** be given it whenever `resolve_rates` is,
or `policy_fold_errors` calibrates the buffer against a rate the policy does not deploy.

**The coverage cost is gone.** At short windows of 90/120/180/270 across four rolling origins,
forward coverage stays at **0.8830** and the minimum priced count at **150** — identical to
committed at every setting.

| Window | Operating point | Fill | Units held |
| --- | --- | ---: | ---: |
| 365 | flat q=0.80 | 0.6188 | 15,122 |
| **120 cascade** | **flat q=0.80** | **0.6412** | **13,305** |
| 365 | tiered (committed) | **0.6851** | 14,751 |
| 120 cascade | tiered | 0.6603 | 12,758 |

**It dominates the flat baseline** (+2.2pp on 12% less stock, full population, coverage
preserved) **and does not stack with the tiering.** Under simulation the best fill is flat
q=0.80 *with* the cascade (0.9395 @ 10,729); adding the tiering makes it worse on both axes
(0.9371 @ 10,828). The short window and the service tiering are **substitutes** — one allocates
stock across SKUs, the other across time. Together the tiering reads the more responsive rate's
fold errors, concludes less buffer is needed, and over-trims.

**Default unchanged:** 365d with tiering. `short_window` is measured and defaulted off, the
treatment `cluster_pooled` received for the same reason.

---

## 7. Two things this session found in the written record

**(a) Chapter 4 is stale.** `docs/CHAPTER_4_DRAFT.md` is dated **2026-09-16**; the policy layer
landed 09-22/23. It cites none of `PRESCRIPTIVE_CONTRACT.md`, `ACCEPTANCE_STANDARD.md`,
`POLICY_HOLDOUT.md`, `policy.py` or `INVENTORY_SIMULATION.md`. Three of its eight principal
findings describe a replaced design — #2 (a fixed 95% service level), #3 ("the knee is measured
rather than chosen", which `POLICY_HOLDOUT.md` subsequently found *absent* on the policy's own
curve), #4 (`rolling_mean_30` dominates at that operating point, moot now the layer consumes a
rate not a point forecast). Findings **1, 5, 6, 7 survive intact** and are the durable spine.

**(b) The descriptive → predictive → prescriptive chain is broken at both joints.**

| Stage | Coverage | Consumed downstream? |
| --- | --- | --- |
| Descriptive — `Fact_Sales`, FSN, density, observability | 266 SKUs, 821 days | yes |
| **Predictive** — `Result_Forecast` (rolling_mean_30 + intervals) | **58 SKUs**, of which **32 forecast zero** → **26 usable (9.8%)** | **no — orphaned** |
| Prescriptive — ROP / EOQ / tiers | 266 SKUs, 208 priced (78.2%) | — computes its own rate |

`backend/pipeline.py:33` states it: *"step5a/step5, neither of which read `Result_Forecast`."*
`Result_Forecast_Metrics` shows **2 of 174** rows meeting the MAPE threshold.

The predictive stage is not missing — it is *buried*. `resolve_rates` produces a demand rate for
208 SKUs with an explicit `insufficient_data` state for the rest, and `empirical_buffer`
produces an uncertainty.

**Built 2026-09-23** — `scripts/step4b_policy_forecast.py`, `docs/PRESCRIPTIVE_CONTRACT.md` §5,
pinned by `tests/test_policy_forecast.py`. The rate and the lead-time interval are published
into `Result_Forecast` as `model_type='policy_rate'` (one row per day across each SKU's own
lead time; `SUM(yhat_upper)` across them is the reorder point), `rolling_mean_30`'s 1,740 rows
are kept beside them untouched, and `step5_prescriptive.py` reads them instead of recomputing.

| Stage | Before | After |
| --- | --- | --- |
| Predictive coverage | 58 SKUs, 26 usable (9.8%), consumed by nothing | **208 priced + 58 flagged = 266 of 266**, consumed by step5 |
| `Result_Forecast` rows | 1,740 | 5,564 (1,740 point + 3,824 policy) |
| `Result_Prescriptive` | — | **identical, 474 of 474 rows byte-for-byte** |

The equivalence is a control rather than a claim: `--recompute-policy` keeps the pre-wiring
path runnable and the two are required to agree. The acceptance verdict is unchanged at 13 of
14, forward coverage 0.8830, and 22/22 invariants hold.

---

## 8. Corrections made to this session's own work

Recorded because each was caught by measurement rather than review, and the same mistakes are
available to a future session.

1. **"`Inventory_Count` is empty, therefore the data must be collected."** Wrong — the workbook
   exists on disk, already vocabulary-mapped, and the backend already reads it. It is a loading
   question, not a collection one.
2. **"The covered subset skews toward easy high-volume items."** Backwards — it skews *slow*
   (11% Fast against 26%). Shelf depth, not item mix, is the confound.
3. **"Non-monotone fill in shelf depth is a bug."** It is a real property of (s,Q) with lost
   sales on a finite horizon. A monotonicity assertion was replaced with a test pinning the
   mechanism.

---

## 9. Open, in the order worth doing

1. ~~**Reconcile Chapter 4**~~ — **done 2026-09-23.** `docs/CHAPTER_4_DRAFT.md` rewritten
   against the policy layer; findings 2, 3 and 4 replaced with their measurements re-scoped rather
   than deleted, 1/5/6/7 intact, 8 extended, two added. The finding-by-finding audit is
   `docs/CHAPTER_4_RECONCILIATION.md`, and `docs/chapter4.html` now has a generator
   (`tools/render_chapter4.py`) so it is derived rather than transcribed.
2. ~~**Write up the cold-start / categorization result**~~ — **done 2026-09-23.**
   `docs/COLD_START_ANALOG.md`, from `tools/cold_start_donor_test.py`. The write-up carries two
   corrections to §2 above: the matched-stock control is a swept frontier rather than one point,
   and price band does not survive it.
3. ~~**Wire the predictive stage**~~ — **done 2026-09-23.** `scripts/step4b_policy_forecast.py`
   publishes the rate and interval; `step5_prescriptive.py` reads them. 26 → 208 usable, 58
   flagged, `Result_Prescriptive` byte-identical. `docs/PRESCRIPTIVE_CONTRACT.md` §5.
4. ~~**Adjudicate the tiering**~~ — **done 2026-09-23.** The synthetic uniform-cover shelf was
   built (`--synthetic-cover`) and it does give the power 28–45 SKUs cannot: 242 SKUs
   observable, separable at 5 of 10 depths, **a dominance at none**. Eight operating points buy
   +1.0 to +2.8 points of fill and are paid for in stock. The answer to a panel is now a trade
   with a confidence interval, not a proxy.
5. **Site-visit asks, not builds** — inventory counts (27% coverage bounds everything above),
   launch quantities for cold start (11.68% of demand, absent from sales history by nature),
   the June–July 2024 gap ruling, and cost inputs (EOQ swings 12.65× between two readings).
6. **Deferred:** the pooled × behaviourally-clustered × calendar-aware hurdle, named by §8 and
   §11 of `POOLING_AND_CLUSTERING_EXPERIMENTS.md` as the natural next step. It improves a point
   forecast nothing downstream consumes, and is worth single-digit MASE on a quantity now shown
   to be third-order.

---

## 10. Reproducing

```bash
python tools/inventory_simulation.py                        # measured shelf
python tools/inventory_simulation.py --stock-scale 0.125 0.1875 0.25 0.375 0.5 0.75 1.0 1.5 \
    --out-csv data/inventory_stock_depth.csv                # depth sweep
python tools/inventory_simulation.py --short-window 120     # the cascade, simulated
python scripts/validate_policy_holdout.py --short-window 120
python tools/acceptance_standard.py                         # unchanged: 13/14, 0.8830
python tools/assert_invariants.py --phase a10               # 22/22
pytest tests/ -q                                            # 462 passed
```

Every tool above opens `ustore.db` read-only. `scripts/step5_prescriptive.py` writes, and was
verified this session against a **copy** — gates all-PASS, `Result_Prescriptive` identical
row-for-row, `rate_source` still only `observed` / `insufficient_data`.

## Related

- `docs/INVENTORY_SIMULATION.md` — §3–5 in full
- `docs/PRESCRIPTIVE_CONTRACT.md` §1a — §6 in full
- `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` — the session this one follows
- `docs/WORKLOG_RECONCILIATION_AND_WIRING.md` — the session that follows this one and works
  its §9: Chapter 4 reconciled, §2 written up and corrected, §7b built, §4b adjudicated
- `docs/POLICY_HOLDOUT.md` — the coverage proxy §4 re-measures
- `docs/ACCEPTANCE_STANDARD.md` — condition 2, whose margin §4 narrows
- `docs/COLD_START_ANALOG.md` — §2 in full, re-measured and reproducible
- `docs/PRESCRIPTIVE_CONTRACT.md` §5 — §7b, built
