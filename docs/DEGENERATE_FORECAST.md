# The degenerate forecast

**Divergence Register #21.** Pinned by `tests/test_degenerate_forecast.py`.

On this dataset, the method that minimises forecast error is the method that predicts nothing will
sell — and therefore the one that can stock nothing. That is not a defect in the pipeline. It
follows from the definitions of the metrics and the shape of the demand, and it is demonstrated
below on our own data rather than argued from the literature.

---

## The identity chain

Three steps, each individually unremarkable. Each is asserted directly in the test file, not merely
the measured outcome, so the reasoning survives even if the numbers move.

**1. MAE is minimised by the conditional median.**

For absolute-error loss, the constant that minimises `E|y − c|` is the median of `y`, not the mean.
Standard result; verified numerically over 10 random series in
`test_mae_is_minimised_by_the_median_not_the_mean`.

**2. MASE has the same minimiser as MAE.**

MASE is MAE divided by a scale factor computed from the *training* data:

```
MASE = MAE(forecast) / MAE(seasonal-naive on training)
```

The denominator does not depend on the forecast. Dividing an objective by a positive constant cannot
move its argmin, so ranking candidate forecasts by MASE and ranking them by MAE give the same
answer. Asserted in `test_mase_has_the_same_minimiser_as_mae`.

This is the step that matters, because "use MASE instead of MAPE" is the usual fix for MAPE's
undefined-at-zero problem — and it does solve that problem. It does **not** solve this one.

**3. On a majority-zero series, the median is zero.**

`Fact_Sales` holds **68,541 zero-quantity rows out of 84,399** — 81.2%. Once more than half the
observations are zero, the median is zero, and so is the error-minimising constant forecast.
Asserted at 55%, 70%, 90% and 99% zero-density in
`test_on_a_majority_zero_series_the_mae_minimiser_is_zero`, and the real 81.2% figure is asserted
against the contract in `test_zero_fraction_of_fact_sales_exceeds_one_half`.

**Therefore:** any selection rule that minimises MASE converges, by construction, on the forecast
"nothing will sell".

---

## The measured instance

Eight methods, 266 SKUs, 12 walk-forward folds each, all scored on identical folds — 25,536
predictions. From `model_benchmark_summary.csv`:

| Method | MASE ↓ | rank | Fill rate ↑ | rank | SKUs priced |
|---|---:|---:|---:|---:|---:|
| **rolling_median_30** | **4.834** | **1** | **0.354** | **8** | **0** |
| rolling_mean_30 | 5.272 | 2 | 0.710 | 2 | 79 |
| tsb | 5.326 | 3 | 0.686 | 3 | **266** |
| seasonal_naive | 6.297 | 4 | 0.656 | 5 | 0 |
| naive | 8.727 | 5 | 0.570 | 7 | 0 |
| ets | 9.291 | 6 | **0.716** | **1** | 251 |
| sba | 12.079 | 7 | 0.634 | 6 | 266 |
| croston | 12.500 | 8 | 0.646 | 4 | 266 |

The ranking inverts exactly where the argument predicts. **The MASE winner is the fill-rate loser**,
and it prices zero SKUs of 266.

`SKUs priced` is the column to read first. It counts the SKUs for which a method produces a positive
30-day forecast — the precondition for computing an annual demand, and therefore for an EOQ or a
reorder point at all. A method that prices nothing cannot stock anything, whatever its error metric
says. Three of the eight methods price nothing: `rolling_median_30`, `seasonal_naive` and `naive`,
all of which return zero on a series whose recent values are mostly zero.

Verified independently of the benchmark artifact in
`test_the_rolling_median_prices_nothing_on_the_real_series`, which recomputes the trailing median
over all 266 eligible daily series and confirms it is zero for every one of them, while the trailing
mean is not.

---

## What this means for the acceptance criterion

Divergence #6 records that §3.3.4's **MAPE ≤ 20%** bar cannot be cleared on this data — the
perfect-forecast floor is ≈89% daily and ≈60% monthly. On its own that reads as a request to lower
the bar.

#21 is a different and stronger statement:

> An acceptance criterion defined purely on forecast error is **structurally invalid** for
> intermittent demand, because its optimum is a forecast of zero.

Tightening the threshold makes this worse, not better: the closer a selection rule gets to the
error-minimising forecast, the closer it gets to a system that recommends stocking nothing. The
problem is not the value 20%. The problem is the choice of objective.

This is a contribution rather than a limitation, and it is demonstrated on the project's own data
with a reproducible script — not asserted from the literature.

**The recommendation is not drawn here.** What replaces or supplements the criterion is deferred
decision **B2** (the adviser's call), and which method is selected under whatever criterion results
is **B3** (the team's, after B2). This document supplies the evidence for both and settles neither.

---

## What it does *not* say

Worth stating plainly, because each of these is an easy misreading:

- **Not** that MASE is a bad metric. It is a good one, and it fixes MAPE's undefined-at-zero problem.
  It simply cannot be used *alone* as a selection rule on intermittent demand.
- **Not** that the rolling median is a bad method. It is genuinely the most accurate of the eight by
  the error metric. It is unusable as the *demand input* to the prescriptive math, which is a
  different question.
- **Not** that the pipeline should be changed to avoid this. The zero-fill is correct and is what
  makes the zero days real observations rather than gaps (Divergence #2). Suppressing the zeros to
  make the metric behave would be fitting the data to the criterion.
- **Not** a claim about intermittent demand in general. It is a demonstration on this dataset, whose
  81.2% zero density is what drives it.

---

## Reproducing it

```bash
python model_benchmark.py            # writes model_benchmark_summary.csv
pytest tests/test_degenerate_forecast.py
```

The tests skip cleanly if `ustore.db` has not been built or the benchmark has not been run, and the
identity-chain tests (steps 1–3) need neither.
