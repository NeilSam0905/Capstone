# Do the pooling/clustering experiments improve forecasting?

An independent validation of `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` against the objective,
rather than against its own metrics. Every figure below is recomputed from the committed fold-level
CSVs in `data/`, not read out of any document.

**Short answer: the clustering result is real on error metrics and strictly dominated on the
objective.** `xgboost` pooled by cluster K=4 posts the best demand-weighted MASE in the repository —
and delivers 6.35 points *less* fill rate than `rolling_mean_30` while holding *more* stock. The
coverage advantage it is credited for recovers about half a percent of the demand the incumbent
misses.

None of this is a criticism of the measurement craft in that log, which is good and in places
exemplary. It is a finding about what the log measured, and what it did not.

---

## 0. Why this was needed

`docs/DEGENERATE_FORECAST.md` #21 established that an acceptance criterion defined purely on
forecast error is **structurally invalid** here, because its optimum is a forecast of zero —
demonstrated, not argued: `rolling_median_30` wins MASE and prices 0 of 266 SKUs.

`docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` reports eleven experiments in MAE, MASE, RMSSE and
MAPE. It reports **no fill rate, no holding cost, and no out-of-sample coverage anywhere**. By this
project's own standing argument it therefore could not be evidence for B3 whichever way its numbers
fell — a limitation the log states plainly ("None of this selects a model") and which nobody had
yet measured past.

This document measures past it.

---

## 1. The log's own numbers are correct

Checked first, because a validation that skips this is just an opinion. Every figure in the
corrected log reproduces **exactly** from the committed fold CSVs — §2, §7 and §11 recomputed
independently, to three decimals:

| claim in the log | file it should come from | recomputed | verdict |
|---|---|---|---|
| cluster4 xgboost, fixed: MAE 14.78 / wt 4.04 / glob 1.69 | `model_benchmark_category_results_cluster4.csv` | 14.784 / 4.042 / 1.690 | exact |
| cluster4 random_forest, fixed: 17.36 / 4.69 / 1.98 | same | 17.355 / 4.687 / 1.984 | exact |
| category_speed xgboost, fixed: 24.18 / 5.16 / 2.70 | `..._results_category_speed.csv` | 24.175 / 5.163 / 2.698 | exact |
| pooled hurdle, fixed: 14.75 / 2.08 / 4.43 / 1.69 | same | 14.745 / 2.077 / 4.428 / 1.687 | exact |
| pooled hurdle, leaked: 14.48 / 2.00 / 4.35 / 1.66 | `..._category_speed_hurdle.csv` | 14.477 / 2.001 / 4.346 / 1.656 | exact |

The `fsn_class` leak fix is real and correctly built — `build_group_fn`/`build_speed_fn` return
closures and `score_pooled` resolves groups *inside* the fold loop, with fold as the outer loop.
`tests/test_category_leakage.py` pins the mechanism with stable-SKU controls that must *not* flip,
which is the right way to write that test. Keeping both the leaked and the fixed CSVs side by side
is the honest choice and should stay.

**Clustering's win on error metrics is genuine and slightly understated by the log.** At weighted
MASE 4.042 it beats not just the per-SKU ML baselines but every method in the committed statistical
benchmark, including `rolling_mean_30` at 4.145.

---

## 2. In the objective's currency it is dominated

Each variant re-scored policy-free: `stock = max(forecast, 0)`, no safety stock, identical
treatment for every method in every file — so the differing safety-stock rules (see §5.1: the
experiment CSVs use a fold-scoped service class, the committed benchmark still used a leaked one)
cancel, and what is left is the forecast itself.

| variant | fill rate | overstock (units) | priced / fold |
|---|---:|---:|---:|
| `rolling_mean_30` (production) | **0.6136** | **22,306** | 109.4 |
| `ewma_a0.1` | 0.6045 | 25,678 | 217.3 |
| `tsb` | 0.5915 | 23,547 | 217.3 |
| `weekly_hurdle_12w` | 0.5777 | 22,714 | 135.0 |
| **`xgboost`, pooled by cluster K=4** | **0.5501** | **23,088** | **266.0** |
| `logistic_hurdle`, pooled by cluster K=4 | 0.5281 | 20,252 | 135.9 |

`rolling_mean_30` delivers **6.35 points more fill while holding 782 fewer units**. That is strict
Pareto domination, on the metric Chapter 1 §1.2 actually commits to.

`random_forest` pooled by cluster reaches a higher fill rate (0.6209) than `rolling_mean_30`, but
holds 35,088 units to do it — +57% stock for +0.7 points of fill. That is a different point on the
frontier, not a better method, and it is the pattern to watch for: on this catalogue fill rate can
always be bought with stock. `random_forest` pooled by `category_speed` posts **0.8154**, the
highest fill rate anywhere in this project, holding **147,298 units**.

---

## 3. The coverage defence does not survive measurement

Pooled tree models price 266/266 SKUs on *every* fold, where `tsb` averages 217 and
`rolling_mean_30` averages 109. That is genuine, and it is the strongest honest claim the pooling
work can make — `DEGENERATE_FORECAST.md` #21 exists precisely because a method that prices nothing
can stock nothing.

But coverage is only worth what it serves, and that had not been checked. Stratifying every
(SKU, fold) cell by whether the incumbent priced it:

| against | cells the incumbent zeroes | demand there | recovered by cluster4 | overstock to do it |
|---|---:|---:|---:|---:|
| `tsb` | 584 | 2,732 u | **0.51 %** (≈14 u) | 51 u |
| `rolling_mean_30` | 1,879 | 4,003 u | **3.65 %** (≈146 u) | 3,118 u |

Against `rolling_mean_30` that is **21 units of overstock per unit of demand recovered**. And on
the cells the incumbent *does* price, cluster4 is worse, not better:

| stratum | incumbent fill | cluster4 xgboost fill |
|---|---:|---:|
| 2,608 cells `tsb` prices | 0.6233 | 0.5794 |
| 1,313 cells `rolling_mean_30` prices | 0.6632 | 0.5916 |

The 584 cells `tsb` zeroes are the same 2,732 units `tools/service_frontier.py`'s
`EXP_UNSERVABLE_DEMAND` already identifies as structurally unservable — the demand is not there to
predict, which is why the 0.9490 ceiling exists and why pooling does not recover them.

`docs/SERVICE_LEVEL_FRONTIER.md` compounds this: EOQ's demand basis `D` can be sourced from
*observed* demand (208 of 266 SKUs qualify) rather than from a point forecast, which decouples EOQ
coverage from model choice entirely. Once that lands, the coverage argument for pooled models mostly
evaporates.

---

## 4. Objectives

| Constraint | Status |
|---|---|
| Acceptance criterion `MASE < 1.0` | **Not met by anything.** Best global 1.502, best median 1.697, best mean 4.792 — off by 1.5–5×. |
| Service / holding-cost frontier as the headline | **Not reported** in the log. No pooled, clustered or hurdle variant had ever been through `tools/service_frontier.py`. |
| Segment by sales velocity, not product type | **Adhered.** #3 falsifies product type directly; clustering generalises velocity correctly. |
| Metric-fairness / outlier audit | **Adhered, and the best work in the log.** #6 is correct and applies to every table in this project. |
| Safety stock from empirical walk-forward quantiles | **Not met.** `Z_BY_CLASS = {"F": 1.65, "S": 1.04}` still backs every fill-rate figure in every run. |
| Objective 3's named method (Prophet) | **Untouched** — B5, blocked on cmdstan. An existing documented divergence, not a new gap. |
| Empirical verification over assertion | **Adhered throughout.** Fixed seeds, retained pre-fix CSVs, retracted claims left visible. |

**#9c is why the MASE gap matters.** A controlled simulation put MASE at 0.74–1.05 at every density
tested, including 1%. The criterion is not unreachable in principle — it is unreachable while the
demand *rate* keeps moving. Sparsity is not the binding constraint; rate instability is. Nothing in
the eleven experiments attacks that directly, which is the single largest missed opportunity in the
investigation and the basis for the recommendation in §6.

---

## 5. Defects found, and what was done about them

### 5.1 The leak fix had only half landed — **fixed**

`scripts/model_benchmark_category.py` was corrected to use a fold-scoped service class.
`scripts/model_benchmark.py` and `scripts/model_benchmark_ml.py` were not: both still read the
static full-history `Dim_Product.fsn_class` and passed it to `service_metrics`. So every fill rate
in `data/model_benchmark_summary.csv` was computed on a leaked class **while the experiment CSVs
used the fixed one — and the log then compared the two directly.** Fixing one side of a comparison
is worse than fixing neither. Direction of the bias is optimistic; magnitude is unmeasured until the
re-run.

Now: `forecasting.category.fold_scoped_service_classes` / `build_service_class_fn` is the single
shared implementation, and all three scorers call it. `service_metrics` takes a callable, not a
dict, so the class is resolved at each fold's own origin. Two new tests in
`tests/test_category_leakage.py` pin it — including one guarding the silent-failure mode, where a
label drifting from `"F"`/`"S"` to `"fast"`/`"slow"` would not raise but would quietly zero every
safety stock via `Z_BY_CLASS.get(label, 0.0)`.

### 5.2 `data/robust_metric_comparison.csv` is stale and has no cluster rows — **fixed in code**

Its `pooled_by_category_speed` rows still hold pre-fix values (xgboost MAE 17.797 against the fixed
24.175 now in the results CSV), and `TREE_RUNS` had no cluster entry at all — so §11's headline
weighted/global MASE was not reproducible by any committed tool. The figures are correct (they
reproduce from the raw fold CSV), but *correct and unreproducible* is not a state this project's
numbers are allowed to be in. Cluster entries added; the file needs regenerating.

### 5.3 No variant had ever been on the frontier — **fixed in code**

`tools/service_frontier.py` read only `data/model_benchmark_results.csv`. It now takes `--compare`,
defaulting to every pooled/clustered/hurdle run in `data/`, and prints an objective-currency table
at the knee: fill rate, units short, units held, **held per unit served**, and out-of-sample
coverage. The addition is deliberately additive — `load()` and every pinned gate still see only the
committed CSV, so nothing there can move.

### 5.4 `n_skus_priced` mixes measurement bases — **fixed in code**

It fits on the full history and forecasts from a single origin at the end of the series — a
deployment statistic — and was being read next to 12-fold error metrics. `rolling_mean_30` reads 79
there but prices **109.4 per fold**, and 129 at the last fold. B14 already records the anchor
sensitivity (2026-07 → 79; 2026-06 → 130). A `n_skus_priced_per_fold` column now sits beside it in
all three scorers. The original column is unchanged, because `tests/test_degenerate_forecast.py`
pins exact values against it and a failing assertion is reported, never relaxed.

### 5.5 Trading-day calendar — **opt-in flag added, B7 still open**

`load_daily_series` reindexed onto all 821 calendar days and zero-filled, so the 61-day
June–July 2024 gap was modelled as "customers wanted nothing". `Dim_Date.is_tally_date` already
answers this and was unused by the forecasting path.

Measured on the benchmark span: 608 of 821 days are tally dates, so a mask drops 213 days (25.9%),
**including all 61 days of the 2024 gap (0 of 61 flagged)**. One caveat worth recording, because it
contradicts the natural reading of #10: `is_tally_date` does **not** encode a weekly closure
schedule. Sundays here are 65% tallied, against Monday's 65% and Thursday's 80%. #10's separate
"Sundays are 88% zero" finding is about zero *sales*, not absent tallies, and the two need different
treatment — that is B8.

`--trading-days-only` now exists and refuses to write the committed artifacts, because it changes
what the horizon *means*: 30 trading days, not 30 calendar days, so `actual_30d` becomes a different
quantity. Report it alongside, never in place of. Whether the 2024 gap is missing data or true zero
demand is **B7**, is not answerable from the code, and the two need opposite treatment.

### 5.6 No holdout — **open, needs a decision**

`make_folds` lays origins backward from the end of the series and reserves nothing. Roughly 38
method × variant combinations are now selected *and* reported on the same 3,192 folds. That is
selection-on-test, and it grows with every experiment added.

### 5.7 `docs/DEGENERATE_FORECAST.md`'s measured table is pre-D1

It reports `ets` fill 0.716 and `rolling_median_30` 0.354 against the committed CSV's 0.7768 and
0.5062. The identity-chain argument is unaffected — the ranking inverts exactly as it claims either
way — but the table is a historical record and is not marked as one.

---

## 6. The experiment the evidence actually points at

**Pooled, calendar-aware hurdle, grouped by cluster.** Three findings in the log converge on it and
none is decisive alone:

- **#7** — pooling the sale-probability classifier across similar SKUs works, improved every metric,
  and survived the leak fix.
- **#8** — a *per-SKU* calendar-aware hurdle is null: median change in per-SKU MAE was exactly 0.0.
  The log's own diagnosis is that no single SKU has enough sale events inside an exam week to fit
  four extra binary columns — *"the same thin-data problem #7 solved for the baseline features,
  re-appearing for the new ones."* That names the fix and never applies it.
- **#9** — sparsity is not the binding constraint; the shifting demand *rate* is. The academic
  calendar is that mechanism, and until #8 no model in `forecasting/` had ever read those columns.

So #9 says the calendar is what matters, #8 says a per-SKU model cannot learn it from this little
data, and #7 says pooling is exactly what fixes "this little data" for this classifier.

The two mechanisms are orthogonal, which is why composing them is small: **pooling decides which
rows** are stacked into the fit, **the calendar decides which columns** each row carries.
`forecasting.hurdle.pooled_calendar_logistic_hurdle_fit_predict` now exists and is registered in
`scripts/model_benchmark_category.py`'s `POOLED_METHODS`.

If it succeeds, it is the first result in this project aimed at the constraint #9 identified. If it
fails, it closes the question #8 left open. Either way it must be scored on the frontier, not on
MASE — see #21.

---

## 7. Running it

Nothing below has been executed: this validation was produced without a Python environment, so every
number here comes from arithmetic over the committed CSVs and every code change is unrun.

```bash
# 1. re-baseline with the leak closed on ALL THREE scorers (numbers WILL move)
python scripts/model_benchmark.py
python scripts/model_benchmark_ml.py
python scripts/model_benchmark_category.py --by cluster --k 4

# 2. refresh the robust aggregations, now including the cluster runs
python tools/robust_metric_comparison.py

# 3. the objective's currency - every variant at the knee, in one table
python tools/service_frontier.py

# 4. the untried experiment
python scripts/model_benchmark_category.py --by cluster --k 4 \
    --methods xgboost logistic_hurdle calendar_logistic_hurdle weekly_hurdle_12w

# 5. trading-day re-baseline - reported ALONGSIDE, never overwriting (B7 open)
python scripts/model_benchmark.py --trading-days-only \
    --out data/model_benchmark_results_tradingdays.csv \
    --summary-out data/model_benchmark_summary_tradingdays.csv

pytest tests/
python tools/assert_invariants.py
```

**Several of these are expected to move committed numbers, and that movement is the result.** Per
`CLAUDE.md`, a failing assertion is reported, never relaxed.

### Exactly one gate is expected to fail, and it is the right one

`tools/service_frontier.py`'s `EXP_CAUSE1_POST_FIX` pins `ets` 0.7768, `rolling_mean_30` 0.7746 and
`tsb` 0.7538 — the three fill rates measured from the committed CSV. Those are precisely the figures
computed with the leaked, full-history service class, so closing the leak will move them and the
gate at `main()`'s Cause-1 block will fire.

**That is the leak being closed, not a regression.** Record the new values in the divergence
register with the old ones beside them, then update the constant — in that order. Do not update the
constant first.

Everything else should hold, and if it does not that is a real finding:

| pinned by | expected to move? | why |
|---|---|---|
| `EXP_CAUSE1_POST_FIX` (Cause 1) | **yes** | reads the stored `units_served`, which the z fix changes |
| `EXP_UNSERVABLE_*`, `EXP_CEILING` (Cause 2) | no | computed from `actual_30d` and flat-zero training slices — no safety stock involved |
| `test_service_frontier.py`'s knee and dominance | no | `empirical_quantile_frontier` builds its own buffer from forecast errors and never reads `safety_stock` |
| `EXP_SKUS_POSITIVE_DEMAND` (208) | no | method-independent by construction |
| `test_degenerate_forecast.py` | no | asserts *relative* properties (the MASE leader is the fill-rate loser) and `n_skus_priced`, which uses the unchanged full-history `skus_priced()` |

---

## What this does *not* say

- **Not** that the pooling and clustering work was wasted. #5, #6 and #9 stand on their own, #7 is a
  real improvement on a fix that had only been named, and #11's finding — that a behavioural feature
  stable over time is a more robust pooling basis than a hard percentile cutoff — is a genuine
  result that survived its own leak correction.
- **Not** that clustering is a bad idea. The objection is that it was evaluated in a currency this
  project had already ruled invalid for selecting a model, not that its hypothesis is wrong.
- **Not** that the error metrics are wrong. They are correct and they reproduce exactly. Being
  closest and being useful are different properties, and on this data they point at different
  methods — which is #21's whole argument.
- **Not** a verdict on B3. B3 belongs to the team and is gated on B2. This narrows what evidence can
  be brought to it; it does not answer it.
- **Not** a claim about intermittent demand in general. Every number is specific to `ustore.db`'s
  266-SKU, 821-day catalogue.

## Needs a human

- **B7** — is the June–July 2024 gap missing data or genuine zero demand? Opposite treatments.
- **B8** — does the store trade on Sundays? `is_tally_date` says mostly yes; #10's zero-rate says
  mostly no.
- **B2** — adviser sign-off on replacing MAPE ≤ 20% with a frontier operating point.
- **B1** — the repo is still public with real supplier and sales data. Unchanged, still the
  highest-priority open item.

## Related

- `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` — the eleven experiments validated here
- `docs/DEGENERATE_FORECAST.md` — #21, why an error-only criterion is structurally invalid
- `docs/SERVICE_LEVEL_FRONTIER.md` — #22, the frontier, the q≈0.80 knee, the 0.9490 ceiling
- `docs/STATUS_AND_NEXT_STEPS.md` — the B1–B15 register; B3 is open and gated on B2
- `FORECAST_EXPERIMENT_AUDIT.md` — the earlier audit, run against a stale copy of the repo
- `forecasting/category.py` — `fold_scoped_service_classes`, the shared fix for §5.1
- `forecasting/hurdle.py` — `pooled_calendar_logistic_hurdle_fit_predict`, §6's experiment
