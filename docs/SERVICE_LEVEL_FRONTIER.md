# The service level is a frontier, not a threshold

**Divergence Register #22.** Reproduced by `tools/service_frontier.py`.

Divergence #6 and `DEGENERATE_FORECAST.md` (#21) establish that §3.3.4's **MAPE ≤ 20%** criterion is
unreachable and, worse, degenerate. #6 names the replacement: *"Report service level ≥ 95% + MASE
< 1.0."*

This document checks that replacement against the data before it is proposed to the adviser, and
finds that **service level ≥ 95% is also unreachable** — for three separable reasons, one of which
is a defect in our own scoring code rather than a property of the demand.

The conclusion is not to lower the bar a second time. It is that a fixed service threshold is the
wrong *kind* of object for this demand regime, and that the frontier below is what should be
reported in its place.

---

## The gap

`model_benchmark_summary.csv`, `fill_rate_at_target` column, all eight methods scored on the same
3,192 folds:

| method | fill rate |
|---|---|
| ets | 0.7161 |
| rolling_mean_30 | 0.7098 |
| tsb | 0.6862 |
| seasonal_naive | 0.6557 |
| croston | 0.6459 |
| sba | 0.6343 |
| naive | 0.5702 |
| rolling_median_30 | 0.3536 |

The best method reaches **71.6%** against a 95% target. That column has been in the summary since
the benchmark was first run; nothing in `CHANGES_tyrone.md` or `STATUS_AND_NEXT_STEPS.md` reads it
against the criterion #6 proposes.

---

## Cause 1 — the risk period was wrong in `service_metrics()` (a defect, fixed 2026-08-19)

**Status: fixed.** `REMEDIATION_MASTER_v2.md` D1 corrected this at the source in
`scripts/model_benchmark.py`; what follows is the historical record of the defect, not the current
state. `tools/service_frontier.py` measures today's committed data directly (no rescaling needed
any more) and confirms the fix — see its Cause-1 output.

`model_benchmark.py` used to set

```python
ss    = z * sigma * np.sqrt(SERVICE_LEAD_TIME)   # SERVICE_LEAD_TIME = 7
stock = max(pred + ss, 0.0)                      # pred is a 30-day aggregate
```

The simulated policy is periodic review, order-up-to: stock is set once, thirty days of demand hit
it, and there is no replenishment inside the window. The interval the buffer has to survive is
therefore **review + lead time = 37 days**, not the 7-day lead time alone. `z·σ·√L` is the
continuous-review ROP formula, where an order is triggered the moment stock crosses the point and
only the lead time is exposed. The two are not interchangeable.

**`step5_prescriptive.py` never shared this defect.** Its `reorder_point(add, lt, ss) = add*lt + ss`
paired with `safety_stock(z, sigma, lt) = z*sigma*sqrt(lt)` is a correct continuous-review ROP —
internally consistent, and exactly what §3.3.3 specifies. The separate question it raises is whether
continuous review is the right *policy*: USTore reorders on the monthly billing cycle, and §3.3.2
regenerates the forecast "at the start of each billing month," which is periodic review with R = 30.
That remains a modelling assumption to justify or change, not a bug to fix (`REMEDIATION_MASTER_v2.md`
explicitly leaves it alone). The benchmark's defect was narrower and unambiguous: it simulated
periodic review while sizing the buffer for continuous review.

Correcting `√7 → √37` (now done at the source, `SERVICE_RISK_PERIOD = SERVICE_REVIEW_PERIOD +
SERVICE_LEAD_TIME`) scaled every safety stock by 2.2991 and moved the fill rate:

| method | as built (pre-fix) | risk period corrected (now the default) |
|---|---|---|
| ets | 0.7161 | 0.7775 |
| rolling_mean_30 | 0.7098 | 0.7746 |
| tsb | 0.6862 | 0.7538 |

Real, and worth having fixed on its own account. Not nearly enough to reach 95%.

---

## Cause 2 — a hard arithmetic ceiling at 94.90%

Across the scored folds, **584 folds spanning 103 SKUs have a flat-zero training slice**. `sigma` is
computed as `np.std(train, ddof=1)` over that slice, so `sigma = 0`; every method's point forecast on
an all-zero history is also 0. Stock is therefore exactly zero, and every unit that arrives in the
test window is short.

That is **2,732 units — 5.1% of all scored demand** — which no stocking policy, no safety-stock
formula and no forecasting method can serve, because the decision is made before any of them are
consulted.

```
total scored demand           53,573
structurally unservable        2,732   (5.1%)
=> ceiling on any fill rate     0.9490
fill on the servable remainder  0.7480
```

**95% is not merely hard on this data. It is arithmetically out of reach by 0.10 percentage points
before a single modelling choice is made.** Tightening or loosening the target does not touch this;
it is a cold-start property of a catalogue where SKUs enter mid-series.

---

## Cause 3 — normal quantiles under-size on intermittent demand

`z·σ` prices the buffer off a normal distribution. The 30-day aggregate of an 81.2%-zero series is
right-skewed, and a normal quantile understates its upper tail.

Replacing the formula with the **empirical quantile of the SKU's own walk-forward forecast errors** —
expanding window, strictly prior folds only, never the fold being scored — reaches **0.794** at
q = 0.95 on `rolling_mean_30`. Better than 0.710. Still not 0.95, because Cause 2 caps it at 0.949
and the remaining shortfall is per-SKU volatility that no buffer priced at a fixed quantile removes.

**Reconciled 2026-08-19 (remediation C4):** the table below is `tools/service_frontier.py`'s own
measured output, made authoritative over this document's original prose figures. The two never
agreed exactly — the sentence above originally read 0.818, the script has always measured 0.794 —
because "expanding window, strictly prior folds only" under-specifies what a SKU's *first* scored
fold (zero prior observations) should do, and no script producing the original 0.673→0.818 numbers
was ever committed to check against. Now pinned by `tests/test_service_frontier.py` (R3) so it can't
drift again. What survives the reconciliation, and is the actual load-bearing claim: the frontier is
monotonic, the knee sits at q ≈ 0.80, and `rolling_mean_30` dominates `ets`/`tsb` there — all three
hold under both the original and the script's methodology, which is itself a robustness result, not
a discrepancy to explain away.

The evidence needed for this was already in the repository: 3,192 folds of `(pred_30d, actual_30d)`
pairs the benchmark computed and then only used for error metrics.

---

## What to report instead

Sweeping the empirical quantile produces a service / holding curve. `rolling_mean_30`, from
`tools/service_frontier.py` (authoritative — see the reconciliation note above):

| q | fill rate | units short | units held | held per *extra* unit served |
|---|---|---|---|---|
| 0.50 | 0.645 | 19,018.5 | 30,115.0 | — |
| 0.60 | 0.664 | 17,984.0 | 34,031.2 | 3.8 |
| 0.70 | 0.699 | 16,151.4 | 40,511.2 | 3.5 |
| 0.75 | 0.719 | 15,065.5 | 44,773.8 | 3.9 |
| **0.80** | **0.742** | **13,844.4** | **49,852.6** | **4.2** |
| 0.85 | 0.758 | 12,955.3 | 56,972.6 | 8.0 |
| 0.90 | 0.779 | 11,856.5 | 66,360.2 | 8.5 |
| 0.95 | 0.794 | 11,040.2 | 79,714.6 | 16.4 |
| 0.98 | 0.800 | 10,707.4 | 87,887.6 | 24.6 |

The last column is the whole argument. Below q ≈ 0.80 a unit of service costs about four units of
holding. Above it the price roughly doubles, then doubles again. **The knee is at q ≈ 0.80, and
it is a property of USTore's demand rather than of a number we picked.**

This is also what §1.2 already promised and never delivered: *"an EOQ-based optimization model to
minimize the total inventory cost ... subject to a cycle service level constraint."* The constrained
optimisation was written into Chapter 1. The curve is the missing half of it.

At the knee, the model comparison changes character. Same rule, q = 0.80:

| method | fill rate | units short | units held |
|---|---|---|---|
| rolling_mean_30 | 0.742 | 13,844.4 | 49,852.6 |
| ets | 0.738 | 14,032.1 | 64,336.4 |
| tsb | 0.722 | 14,913.9 | 52,202.2 |

`rolling_mean_30` **dominates**: higher service *and* less stock than both. On the frontier there is
no accuracy-versus-usability trade to adjudicate, which is the trade B3 and B15 are currently
deadlocked on.

---

## The EOQ demand basis, and why it decouples B3

`n_skus_priced` — 0 for `rolling_median_30`, 79 for `rolling_mean_30`, 266 for TSB — counts SKUs
whose **30-day point forecast is positive**, because `step5_prescriptive.py` derives EOQ's annual
demand `D` by annualising that forecast. A method that forecasts zero yields `D = 0` and no EOQ.

That coupling is a design choice, not a constraint. EOQ is batching economics: it answers how large
an order should be given fixed ordering and holding costs, and it is insensitive to short-run
forecast error. Sourcing `D` from **trailing observed demand** instead makes the count
method-independent:

```
SKUs with >0 observed demand across scored folds: 208 of 266
```

208 SKUs get an EOQ under any method. The pressure to select a forecasting method on SKU coverage
disappears, and #21's stated trade — *"TSB costs 10.2% more error and prices the entire catalogue
rather than none of it"* — stops being a trade at all.

---

## What this changes in the register

| # | Was | Becomes |
|---|---|---|
| B2 | Adviser sign-off on "service level ≥ 95%" | Adviser sign-off on **reporting a frontier with a recommended operating point**, since ≥95% is unreachable by 0.10pp before modelling |
| B3 | Deadlocked: MASE winner prices 0 SKUs | Selection on the frontier. `rolling_mean_30` dominates at the knee |
| B5 | Prophet blocked on cmdstan; unclear whether it appears in Ch4 | Lower stakes. If the selected method is a rolling mean that dominates seven alternatives, Prophet's absence is a **reported benchmark result**, not a hole |
| B15 | Croston vs TSB for S/N tiers | Follows B3 and is largely moot once EOQ coverage is decoupled from the forecast |

Chapter 2's Prophet-over-ARIMA justification (§2.1.4) and the *"20 percent MAPE threshold is widely
cited"* passage both need rewriting regardless — that debt predates this document.

---

## What this does *not* say

- **Not** that the benchmark is wrong. Every number here is a re-scoring of forecasts
  `model_benchmark.py` already produced. The harness — non-overlapping folds, σ from the training
  slice only, no leakage — is what made this checkable at all.
- **Not** that fill rate is a bad metric. It is the right one. What fails is pinning a *fixed target*
  to it, for the same structural reason a fixed MAPE target fails: the criterion was chosen before
  the demand distribution was known.
- **Not** that 0.742 is a good service level in absolute terms. It is the honest one at a defensible
  holding cost on this data, and stating it with the curve behind it is stronger than asserting a
  95% that was never met.
- **Not** a decision. #22 supplies evidence for **B2** (adviser) and **B3** (team, after B2) and
  settles neither. The code changes it implies — risk period, empirical-quantile safety stock, EOQ
  demand basis — are three separate calls, and the first is a defect fix that stands whatever is
  decided about the other two.

---

## Reproducing it

```bash
python scripts/model_benchmark.py    # writes data/model_benchmark_results.csv
python tools/service_frontier.py
```

The script reads only `data/model_benchmark_results.csv`, touches no database and fits no models, so it
cannot change what the benchmark found — only what is asked of it.

Pinned by `tests/test_service_frontier.py` (remediation R3, on the pattern
`tests/test_degenerate_forecast.py` set): the 0.9490 ceiling, the knee being a real (>2x) jump in the
marginal-cost column rather than a rounding artefact, `rolling_mean_30`'s dominance over `ets`/`tsb`
at q = 0.80, and the 208-of-266 EOQ demand-basis count.

---

## Suggested Chapter 4 placement

Results, immediately after the #21 material: #21 establishes that an error-based criterion is
degenerate; #22 establishes that the obvious replacement is unreachable and supplies the object that
works instead. Read together they are a single argument about **choosing an objective for
intermittent demand**, demonstrated on our own data — which is a stronger contribution than either
would be as a standalone limitation.

---

## The operating point, re-measured after the padding fix (2026-09-30)

`bf08ca7` took 23 days of step0 zero-fill out of the rate denominators, which
moved every buffer (mean safety stock 8.850 → 13.442) and every tier count
(servable 24 → 17, not_stockable 14 → 9). The thresholds that produce those
counts — `DEFAULT_TIER_TARGET = 0.90`, `DEFAULT_TIER_MIN_EFFICIENCY = 0.10` —
were chosen against the old measurement, so `tools/tier_operating_point.py` was
written to ask whether they are still the right ones.

It runs the holdout across 30 combinations and asks, of each, whether any flat
quantile dominates it at **all four** rolling origins. That bar is stricter than
the gate's majority on purpose: a search permitted to miss one window will find a
configuration that misses the awkward one.

**0 of 30 clear 4 of 4.** 16 reach 3, 14 reach 2. The tiering cannot be placed on
the efficient frontier at every origin by moving these knobs, and that is a
statement about the tiering rather than about the knobs.

Within the 16 that reach 3 of 4, the live default is **12th on served-per-held**:

| tier_target | min_efficiency | fill (demand-wtd) | units held | served/held |
|---:|---:|---:|---:|---:|
| 0.90 | 0.30 | 0.6260 | 49,476 | **0.6337** |
| 0.85 | 0.30 | 0.6241 | 49,762 | 0.6281 |
| 0.90 | 0.20 | 0.6434 | 55,303 | 0.5827 |
| **0.90** | **0.10** *(live)* | **0.6685** | **66,125** | **0.5063** |

Raising `min_efficiency` from 0.10 to 0.30 buys **25% better efficiency on 25%
less stock, for about 4pp of fill**. That is a trade, not a correction, so the
defaults are left alone here: it is a decision about how much service the store
wants to buy, and it moves every reorder point. The evidence is recorded so the
decision can be made rather than inherited.

One caveat on reading the sweep: across the 30 configurations the domination
lands on 2025-07-14 (21 times) and 2026-01-10 (20), and on the development-set
origin only 3 times. The live default is one of those 3, which is why the first
look at this — which had only the development set's frontier — read as a
development-set artefact. It is closer to the reverse.
