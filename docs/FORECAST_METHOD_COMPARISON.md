# Two more forecasting methods, tried and rejected

Tried alongside the existing 8-method benchmark (`model_benchmark.py`, Block 4.6) to see whether
anything beats `rolling_mean_30` / `tsb` on the same 3,192 walk-forward folds. Neither does. Recorded
here because a negative result on real data is still evidence, not a wasted afternoon.

**Reproduce:** `forecasting/baselines.py` now defines `ewma_fit_predict()` and
`rolling_quantile_fit_predict()`; `model_benchmark.py`'s `build_methods()` wires them in as
`ewma_a0.1` and `rolling_q75_30`. Re-running `python model_benchmark.py` scores all 10 methods.

---

## What was tried and why

**EWMA (α=0.1).** Rolling mean and rolling median both use a flat (boxcar) 30-day window — every one
of the last 30 days counts equally, and day 31 drops out of the average entirely the moment it ages
past the window edge. An exponentially weighted average doesn't have that cliff: every observation
counts, decaying geometrically, so a shift in demand shows up gradually instead of falling off a
window boundary. Worth checking whether that smoother decay profile helps on a catalogue this
intermittent.

**Rolling 75th percentile (30d window).** Every method already in the benchmark minimises point
error, and on a series that's 81% zeros, the error-minimising forecast is low — often exactly zero
(the same degeneracy `DEGENERATE_FORECAST.md` documents for MASE/rolling_median). This method
forecasts the trailing 75th percentile instead of the mean/median, deliberately biased toward
covering demand rather than predicting it precisely — the same idea `SERVICE_LEVEL_FRONTIER.md`
applies to the *safety-stock buffer*, tried here on the *point forecast* itself instead.

## Results

| method | MASE | % beat naive | SKUs priced | fill rate | units held |
|---|---:|---:|---:|---:|---:|
| `rolling_mean_30` (current pick) | 5.27 | 51.1% | 79 | **0.710** | 40,893 |
| `tsb` (current fallback) | 5.33 | 52.6% | **266** | 0.686 | 42,217 |
| **`ewma_a0.1`** | 5.45 | 51.1% | **266** | 0.706 | 43,966 |
| **`rolling_q75_30`** | 5.69 | 45.5% | **0** | 0.653 | 42,562 |

Full run: `python model_benchmark.py`, all 10 methods, same 266 SKUs / 3,192 folds as every other
result in this repo. These numbers came from a scratch run (`--out`/`--summary-out` pointed
elsewhere) — the committed `model_benchmark_results.csv` / `model_benchmark_summary.csv` still hold
only the original 8 methods, so nothing else in the repo that reads those files needed to change.

## Reading the results

**EWMA doesn't beat anything — it sits between the two methods already in use.** Every metric is
slightly worse than `rolling_mean_30`. Its one distinguishing feature is that, like TSB, it prices
the full 266-SKU catalogue rather than 79 — but TSB already does that and does it better (higher
fill rate, fewer units held). There's no case in which EWMA is the method you'd pick over what's
already there.

**The quantile method is the more interesting result, precisely because it's a clear loss.** It
prices **zero** SKUs — worse than `rolling_mean_30`'s already-limited 79. The mechanism: a 75th
percentile is exactly zero whenever more than a quarter of the days in the trailing window are
zero-sale days, which is the normal case for most SKUs on a catalogue where 81% of all rows are
zero. Biasing the point forecast upward doesn't escape the degenerate-forecast trap `#21` describes —
it just moves which percentile lands on zero. Fill rate drops too (0.653, worse than plain
`rolling_mean_30`), because a forecast that's usually 0 gives the safety-stock buffer less to add to,
even when the buffer itself is unchanged.

**Net effect: this confirms, rather than escapes, the structural finding.** The problem isn't which
central tendency (mean vs. median vs. quantile) the point forecast uses — it's that the catalogue is
intermittent enough that most reasonable point-forecast rules land on zero for most SKUs. That's the
same conclusion `DEGENERATE_FORECAST.md` (#21) and `SERVICE_LEVEL_FRONTIER.md` (#22) already reach
from different angles; this is a third, independent route to it.

## What this does not change

- The recommendation stands: `rolling_mean_30` where it prices (79 SKUs), `tsb` as the
  full-catalogue fallback. Neither new method displaces either.
- Not committed to the benchmark's official output — `model_benchmark_results.csv` /
  `model_benchmark_summary.csv` are unchanged. If Chapter 4 wants "we also tried X and it didn't
  help" as a documented negative result, re-running `model_benchmark.py` without the `--out`
  override would make these two methods part of the committed 10-method comparison.
