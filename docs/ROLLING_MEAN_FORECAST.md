# The forecast is now `rolling_mean_30`, scored on 30-day aggregates

**What changed, and where it leaves the acceptance criteria.**

`scripts/step4_forecast_model.py` (formerly `step4_prophet_forecast.py`) no longer fits Prophet.
Every Fast SKU is forecast with the trailing 30-day mean, and validation now runs on the same
harness and at the same settings as `model_benchmark.py`.

**On the criteria: still no on the two thresholds, and both were already known unreachable. The
change is that the numbers are now measured on the quantity the pipeline actually serves, and are
directly comparable to the benchmark.** The most important new finding is a bad one, recorded in
§4: **32 of 58 SKUs now forecast zero.**

---

## 1. What the model is

```
level      = mean of the trailing 30 observations, clipped at 0
yhat       = level, on each of the 30 horizon days
yhat_lower = max(level - SD, 0)          SD = sample SD of the FULL series
yhat_upper = level + SD
```

This is literally `forecasting.baselines.rolling_mean_fit_predict(30)` — the identical callable
`model_benchmark.py` scores as `rolling_mean_30` and `step5_prescriptive.py` lists in
`DEMAND_METHODS`. That was the point of the switch: **the benchmark's and the frontier's findings
about `rolling_mean_30` are now findings about the production model**, not about a near relative.
The previous full-history (expanding) mean was never scored by either.

`model_type` in `Result_Forecast` is `rolling_mean_30`; 1,740 rows = 58 SKUs × 30 days.

**The forecast is a constant per SKU.** No trend, no seasonality, no calendar effects — a mean has
no design matrix. Only the Fast class (58 of 519 products) is forecast at all.

The band uses the full-series SD deliberately: the 30-day window is the right basis for the *level*
but a poor one for dispersion, since a SKU whose last 30 days are flat would get a zero-width
interval claiming a certainty the model does not have. The band is display-only.

## 2. What the validation is

Scoring moved from an 80/20 daily holdout to `forecasting.evaluate.walk_forward_evaluate` at
`model_benchmark.py`'s exact settings:

```
horizon 30 | min_folds 3 | max_folds 12 | min_train 60
```

The scoring unit is **one 30-day aggregate per fold**, not 30 daily points. Three reasons this
replaced the old harness:

- **It measures what the pipeline serves.** `Result_Forecast` is a 30-day forecast and
  `step5_prescriptive.py --demand-basis forecast` consumes its 30-day total. Daily one-step-ahead
  accuracy measured a quantity nothing consumed.
- **The old holdout was not a fair fight.** The model was frozen at the split and predicted up to
  ~70 days ahead while `naive` re-read *yesterday's actual* at every test point — persistence was
  handed one-step-ahead information the model never got. Under rolling origins both see the same
  training slice per fold, and the fold layout is computed **once per SKU and handed to both**.
- **§3.3.4 and Figure 3 both promise walk-forward validation.** The 80/20 holdout was not that.

Series are reindexed over the full 821-day calendar, zero-filled — the convention
`step5_prescriptive.py::load_series` and `model_benchmark.py::load_daily_series` already use. This
also **fixed the unscoreable-SKU problem**: the old code built each series from only that SKU's own
`Fact_Sales` dates, giving series as short as 6 rows and leaving 5 SKUs unvalidated. Every Fast SKU
now reaches the full 12 folds. **58 of 58 scored, 0 heuristic.**

Sale-day tiers (38 / 10 / 10) are now **descriptive labels only** — they select neither the model
nor the harness. They are still written out because `tools/tier_counts.py` reconciles Divergence
#17 against them.

---

## 3. The criteria

### Criterion 1 — §3.3.4: MAPE ≤ 20% · **NOT MET (1 of 58)**

| | |
|---|---|
| SKUs meeting ≤20% | **1 of 58** |
| MAPE defined for | 44 of 58 (undefined where the aggregate actual is 0) |
| Best / median / worst | **16.9%** / 120.6% / 3254.1% |

One SKU squeaks under. The median misses by 6×. Unchanged in substance from the daily harness
(which scored 0 of 53): Divergence #6 established the perfect-forecast floor here is ≈89% daily and
≈60% monthly — **the bar is unreachable by any model, including a perfect one** — and #21 argues
the objective itself is degenerate. Reported, not relaxed.

### Criterion 2 — #6's replacement: service ≥ 95% + MASE < 1.0 · **NOT MET**

MASE became computable for the first time when scoring moved to aggregates (its denominator is the
naive scale over 30-day blocks of the training slice, which needs the same unit as the errors).

| | |
|---|---|
| Mean MASE | **2.147** (naive: 5.294) |
| SKUs with MASE < 1.0 | 23 of 58 — **but see below** |

**Do not report 23.** Thirteen of those SKUs have `MAE = 0` exactly: they sold nothing across the
scored 360-day window, and both the model and naive "perfectly" predicted zero. That is
`DEGENERATE_FORECAST.md`'s argument appearing directly in our own production metrics — a forecast of
nothing scoring perfectly. **The honest count is 10 of 58.**

The service-level half is unreachable regardless: #22 established a hard arithmetic ceiling of
**0.9490**, missing 95% by 0.10pp before any modelling choice, because 584 folds across 103 SKUs
have a flat-zero training slice.

### Criterion 3 — #22's frontier · **the result now transfers**

This is the criterion the register recommends adopting, and the switch to the trailing-30 window is
what makes it citable. #22's finding for **B3**:

> Selection on the frontier. `rolling_mean_30` **dominates** at the knee (q ≈ 0.80) — higher service
> *and* less stock than both `ets` and `tsb`.

| method at q = 0.80 | fill rate | units short | units held |
|---|---:|---:|---:|
| **rolling_mean_30** | **0.742** | **13,844.4** | **49,852.6** |
| ets | 0.738 | 14,032.1 | 64,336.4 |
| tsb | 0.722 | 14,913.9 | 52,202.2 |

The production model is now that method exactly, so this is no longer a result about a different
callable. The earlier full-history variant could not claim it.

### Against the naive baseline · **now materially better**

The fair harness changes this conclusion outright:

| | old daily holdout | new 30-day aggregate |
|---|---:|---:|
| SKUs beating naive | 19 / 53 (36%) | **35 / 58 (60%)** |
| Mean MAE | 4.29 vs naive 3.72 ✗ | **40.02 vs naive 104.19 ✓** |
| Mean MASE | not computable | **2.147 vs naive 5.294** |

Under the old harness naive appeared to *beat* the model. It does not; that was the unfair holdout
plus the wrong horizon. On the aggregate the model is **2.6× better than persistence**, consistent
with the benchmark's 266-SKU result (MAE 13.47 vs 34.24).

*Note on comparability:* step4's mean MASE (2.147) is better than the benchmark's `rolling_mean_30`
(5.27) because step4 scores only the 58 Fast SKUs while the benchmark scores all 266 including slow
and non-moving. Denser demand, better MASE. The two figures are not interchangeable.

---

## 4. The cost of the trailing window: 32 of 58 SKUs forecast zero

**This is the finding to carry forward, and it is a regression against the full-history variant.**

```
Fast SKUs with a forecast : 58
  30-day total > 0        : 26
  30-day total = 0        : 32
```

The trailing 30-day window (2026-07-02..2026-07-31) is empty for 32 SKUs, so the window mean is 0,
so the forecast is a flat zero line. The full-history mean forecast a positive number for
essentially all of them.

This is `DEGENERATE_FORECAST.md` in production rather than in a benchmark: *on a majority-zero
series the error-minimising forecast is zero, and a forecast of zero cannot stock anything.* It is
the same mechanism that made `rolling_mean_30` price only 79 of 266 SKUs in the benchmark (here:
26 of 58, a better rate but the same effect).

Two consequences:

- **Dashboard.** 32 of 58 Fast SKUs now render a flat-zero forecast line. Defensible — the SKU
  genuinely sold nothing for a month — but it is worse to look at than the full-history line, and
  it is the direct trade for making the frontier result citable.
- **`step5 --demand-basis forecast`.** Prices **26** SKUs, against `trailing`'s 208. See §5.

**This is a live decision**, not a settled one. Recovering both properties would mean decoupling the
*point forecast* from the *level used for stocking* — which is exactly what #22 already recommends
doing with the empirical-quantile buffer, and what `rolling_q75_30` tried on the point forecast
itself (`FORECAST_METHOD_COMPARISON.md`).

## 5. step5 now reads `Result_Forecast`

Previously `step5_prescriptive.py` **never read the table in either mode**. In `forecast` mode it
re-computed a forecast in-process from `--demand-method`'s callable, meaning step4's published
output had no consumer anywhere in the pipeline and could silently disagree with what the dashboard
showed. Two sources of truth, one invisible.

`forecast` mode now reads `SUM(yhat)` per SKU out of `Result_Forecast`, annualised ×365/30, and
records the provenance as `result_forecast:rolling_mean_30`. It exits with a clear message if step4
has not been run rather than silently falling back. `--demand-method` is now ignored in that mode
and warns when passed.

**`trailing` remains the default, and is now ratified rather than provisional:**

| basis | SKUs priced | why |
|---|---:|---|
| **trailing_365d** *(default)* | **208** | observed history; independent of the open B3 model choice |
| result_forecast:rolling_mean_30 | 26 | bounded by step4's Fast-only coverage, then halved again by §4's zero forecasts |

The gap is the argument for the default. EOQ is batching economics and is insensitive to short-run
forecast error, so making the prescriptive layer hostage to a forecasting-method decision that is
still open (B3) buys nothing and costs 182 SKUs.

## 6. A limitation the new harness introduced

The `standard_period` / `semestral_break` split is now **much coarser**, and nearly disappeared
silently.

At 30-day aggregation **no window on this calendar is majority-break** — the fullest is 14/30
(0.47), because a semestral break is shorter than half a 30-day tile and never aligns with one. The
initial >50% majority rule was therefore structurally unreachable and produced *zero* break rows
without erroring. The threshold is now "at least a third of the window", which yields exactly **one
break-exposed fold per SKU** (2025-12-04..2026-01-02, 14/30 break days) against 11 standard folds.

So break-scope metrics rest on a **single 30-day window** and should be read as indicative, not as a
peer of the 12-fold overall figure. The daily harness separated the two cleanly. That resolution is
the price of scoring the aggregate the pipeline actually serves.

---

## 7. Database rebuild

The `ustore.db` these numbers come from was rebuilt from scratch, because its `Dim_Product` schema
predated commit `fcd597d` and its `price_source` CHECK still allowed only two values, which made
`step1_apply_mapping.py` fail outright. `create_schema.py` uses `CREATE TABLE IF NOT EXISTS`, so
re-running it could never repair the constraint.

The rebuild reproduces every pinned value (`Fact_Sales` 84,399; `Dim_Product` 519; F=58 / S=228 /
N=233; tiers 92/51/123 and 38/10/10) and finally landed the price recovery that commit had shipped:

| price_source | before | after |
|---|---:|---:|
| unpriced | 158 | **22** |
| inventory | 301 | 280 |
| name_suffix | 60 | 60 |
| may2024_dsr | — | **36** |
| tbs_item_price | — | **121** |

## 8. Reproducing it

```bash
python scripts/step4_forecast_model.py           # ~10 s
python scripts/step5_prescriptive.py             # trailing (default), 208 SKUs
python scripts/step5_prescriptive.py --demand-basis forecast   # 26 SKUs
python tools/tier_counts.py                      # 4/4 PASS
python tools/assert_invariants.py --phase a10    # 22/22 PASS
```

## 9. Related

- `docs/DEGENERATE_FORECAST.md` — #21; §4 above is that argument in production
- `docs/SERVICE_LEVEL_FRONTIER.md` — #22, the frontier and the q ≈ 0.80 knee
- `docs/FORECAST_METHOD_COMPARISON.md` — `rolling_q75_30`, relevant to §4's open decision
- `forecasting/evaluate.py` — the shared harness, and its leakage guarantee
