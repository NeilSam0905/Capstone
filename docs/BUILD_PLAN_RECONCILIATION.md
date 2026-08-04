# Build plan vs. reality

`USTore_Build_Plan.pdf` (2026-07-06) against the state of the repo on 2026-08-04, branch `tyrone`.

Every "Build Plan says" cell below is quoted from the PDF, which was extracted and read directly
rather than recalled. Every "Reality" cell is asserted somewhere in the repo — the assertion is
named so the claim can be checked rather than believed.

This is not a criticism of the plan. Most of it held. The point is to record where reality moved so
Chapter 4 describes the system that exists.

---

## The deltas

| # | Build Plan says | Reality | Evidence |
|---|---|---|---|
| 1 | "Chapter 4's results run mostly on the historical **2023–2026** tally records" | Sales start **2024-05-02** and end 2026-07-31. 2023 is 6 undated batch aggregates, 1 of 34 labels matching a current SKU — it is not a usable training year | `assert_invariants.py`, "sales date span" |
| 2 | Phase 3: Prophet for Fast + HVL SKUs, unqualified | Prophet has never beaten a rolling median here. Across 7 methods on identical folds, `rolling_median_30` leads on MASE and Prophet is not runnable from a clean clone at all | `model_benchmark_summary.csv` |
| 3 | "MAPE **<= 20%** for standard periods" | Provably unreachable — the perfect-forecast floor is ≈89% daily / ≈60% monthly on this series. The gate cannot be met by any model | Divergence #6; deferred decision **B2** |
| 4 | Tiers by observation count: "**60+ obs** → standard fit" | Must be **distinct non-zero sale days**. Counting rows counts zero-fill padding as observations and collapses every SKU into the top tier (305/0/0) | `tier_counts.py`; Block 2.1 |
| 5 | "Validate: **walk-forward**" | Was a single 80/20 chronological holdout until this branch. Now genuine rolling origins, expanding window, minimum 3 folds | `forecasting/evaluate.py`; `tests/test_evaluate.py` |
| 6 | Phase 1 derived fields: `daily_depletion_rate`, `cumulative_monthly_units` | Done, **plus** `days_of_supply` and `is_censored` | `step2_load_fact_sales.py` |
| 7 | "D = annualised 30-day **Prophet** forecast" | D comes from a non-Prophet forecast. More importantly the best-scoring method cannot supply it at all — see below | `step5_prescriptive.py` |
| 8 | Phase 4: z = 1.65 fast / 1.04 slow, non-moving excluded, CV fallback for sparse σ | **Implemented exactly as written**, and extended from a point estimate to a 5×5 sensitivity grid because the lead time and costs it needs do not exist | `step5_prescriptive.py`; **B9** |
| 9 | "the full SQLite schema DDL for **all five tables**" | Six tables exist. `Exception_Log` — the plan's "route unresolvable rows to an exception table" — is created ad hoc by `step2_load_fact_sales.py`, not by `create_schema.py` | `assert_invariants.py`, "Exception_Log exists" |
| 10 | Phase 5 writes `Event_Log`; store-closure toggle sets `is_store_closed` | `Event_Log` is **0 rows**. Phase 5 (the Flask interface) has not been started | `assert_invariants.py`, "Event_Log rows" |

---

## What held

Worth saying plainly, because the list above is all deltas:

- **Phase 1 was correctly identified as the critical path.** "The vocabulary mapping in Phase 1 will
  take longer than expected, and everything downstream inherits its errors." That is exactly what
  happened, and front-loading it is why `step1_apply_mapping.py` now reports 0 unmatched names.
- **One SQLite database as the single source of truth** — held. Nothing forecasts inside Power BI.
- **The three formulas** are implemented verbatim, including the FSN-differentiated z-scores and the
  coefficient-of-variation fallback.
- **The EOQ sensitivity check** at 0.5× / 1× / 2× is implemented and confirms EOQ is the cost
  minimum, at exactly 1.25× the optimum on both sides.
- **"Most SKUs land in the rolling-average fallback. That is not a failure."** Correct, and the
  benchmark strengthens it: the rolling median is not a fallback, it is the best-scoring method.

---

## The one that needs a decision, not a note

Item 7 above is more than a substitution. The Build Plan assumes the forecast that scores best is
also the forecast that feeds the prescriptive math. On this data those are **different forecasts**:

> The rolling 30-day **median** leads the benchmark on MASE, and prices **0 of 266 SKUs**. On an
> intermittent daily series the trailing median is zero, so annual demand comes out zero and there
> is no EOQ to compute.

The method that minimises forecast error predicts "nothing will sell" — nearly right day to day, and
useless for deciding how much to order. `step5_prescriptive.py` therefore defaults to the rolling
mean, and records the method in every row.

This is the accuracy/actionability split, and it is a real result rather than a workaround. Any
model selection made on MASE alone (deferred decision **B3**) has to confront it.

A second, smaller version of the same problem: an annualised **30-day** demand basis anchors on
2026-07, which falls inside the AY2526 summer term. 79 of 266 SKUs get a positive D at 30 days,
against 141 at 90, 163 at 180 and 208 at 365. Whether to widen the basis is a Block 5 decision.

---

## Phase status

| Phase | Build Plan scope | Status |
|---|---|---|
| 0 | Setup, stack, raw data | Done |
| 1 | Schema + ETL | Done; derived fields exceed the plan |
| 2 | FSN classification | Done — 58 F / 228 S / 233 N, 6 HVL |
| 3 | Prophet forecasting | **Blocked** on cmdstan; superseded in practice by the 7-method benchmark. **B5** |
| 4 | ROP / Safety Stock / EOQ | Done as a sensitivity surface pending real parameters. **B9** |
| 5 | Flask tallying interface | **Not started** — `Event_Log` is 0 rows |
| 6 | Power BI dashboard | Not started; blocked on B10 for the Stock Status view |
| 7 | Chapter 4 write-up | In progress; this file and `DIVERGENCE_REGISTER.md` are inputs to it |
