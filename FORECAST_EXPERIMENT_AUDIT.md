# Audit of the pooling / categorization / clustering experiment log

An independent verification of an eleven-experiment write-up on pooling, categorization and
clustering, checked against the code and the committed CSVs in this repository rather than
against its own narrative.

The short version: the measurement craft in that log is good — better than most — but three
of its load-bearing claims do not survive contact with the code. One headline experiment has
no implementation. The grouping used by half the experiments leaks test-period information,
for a reason that is documented backwards in three separate docstrings. And the whole log is
denominated in a currency this project has already ruled structurally invalid for selecting a
model.

Nothing here is a finding about intermittent demand. It is a finding about this repository.

---

## Summary

| # | Claim audited | Verdict |
|---|---|---|
| A | Experiment #11 (K-means clustering, "best result of the whole investigation") | **Unbacked.** No `clustering.py`, no `--by cluster`, no cluster row in any CSV |
| B | `--by category_speed` pooling results (#2, #4, #7, #8) | **Leaked.** `fsn_class` is derived from the full history, including every fold's test window |
| C | Fill-rate figures on *every* code path, including `--by category` | **Leaked, separately.** The safety-stock z-score is selected by the same full-history label |
| D | "`weekly_hurdle_12w` … remains the strongest single result on fill rate and is still the project's committed champion" | **False on both counts.** 0.7285 vs `rolling_mean_30`'s 0.7746; B3 is open |
| E | The log as evidence for model selection | **Cannot serve.** Reports no fill rate, holding cost, or SKUs-priced anywhere |
| F | #6, the MASE-aggregation audit | **Confirmed.** Reproduces exactly against `data/robust_metric_comparison.csv` |
| G | #4's mid-experiment bug catch and retraction | **Confirmed, and exemplary.** `_real_denom` is the correct fix |

B and C are the findings that matter. A is the one that needs a decision from a human before
anything else proceeds.

---

## 0. A note on which directory is real

Two copies of this project exist on this machine:

- `/home/ty/Downloads/Japan - October 2019/Capstone-neil (2)/` — **the real one.** Has
  `forecasting/{category,hurdle,ml_models}.py` and the full `tools/` experiment suite.
- `/home/ty/Downloads/Capstone-neil (2)/` — a **stale partial copy.** Missing all of the above.
  An audit run against it will conclude, wrongly, that `weekly_hurdle_12w` does not exist.

Neither is a git repository, so there is no history to recover anything from. Everything below
is verified against the first.

---

## A. Experiment #11 has no implementation

The log presents K-means clustering on five behavioural features as "the strongest result in the
investigation," beating every manual grouping and beating the per-SKU baseline on weighted and
global MASE. Checked four ways:

- **No module.** `find . -name clustering.py` → nothing. `grep -rn "KMeans\|choose_k" --include=*.py .`
  → nothing. `forecasting/` contains `baselines.py`, `category.py`, `evaluate.py`, `hurdle.py`,
  `intermittent.py`, `metrics.py`, `ml_models.py`.
- **No grouper.** `scripts/model_benchmark_category.py:107`:
  ```python
  GROUPERS = {
      "category":       lambda cat, speed: cat,
      "speed":          lambda cat, speed: speed,
      "category_speed": lambda cat, speed: f"{cat}-{speed}",
      "product_type":   None,
  }
  ```
  No `cluster`. And no `--k` argument is defined anywhere in `main()`. The log's own repro line —
  `python scripts/model_benchmark_category.py --by cluster --k 4 …` — would fail on argument
  parsing before reaching the database.
- **No artifact.** `grep -ril cluster data/*.csv` → nothing. `data/robust_metric_comparison.csv`
  carries 33 method/variant rows and none is a cluster variant.
- **No prose.** `grep` for `pooled|category_speed|pooling` across `docs/*.md` returns hits only in
  `SPARSE_DEMAND_EXPERIMENTS.md`. There are 27 committed pooling CSVs and no document describing
  them. The eleven-experiment log is not in this repository.

One further observation, offered as an observation and not as proof. In the log's experiment-#11
table, every *non*-cluster row reproduces `data/robust_metric_comparison.csv` to the digit:

| row in the log | MAE | MASE (wt) | MASE (global) | matches CSV? |
|---|---|---|---|---|
| xgboost, per-SKU | 19.45 | 5.21 | 2.23 | exact |
| xgboost, category+speed | 17.80 | 4.73 | 2.04 | exact |
| xgboost, product_type | 16.43 | 4.99 | 1.88 | exact |
| xgboost, **cluster (K=4)** | 14.48 | 4.01 | **1.66** | **no such row** |

The cluster row's MAE (14.48) and global MASE (1.66) are the MAE (14.477) and global MASE (1.66)
of the `logistic_hurdle_pooled_cat / real history` row in that same file.

**Required before anything cites #11:** establish whether `forecasting/clustering.py` exists on an
uncommitted branch or another machine. If it does, it enters the re-scoring below like any other
variant. If it does not, #11 must be struck from the log — summary row and "best result" framing
included. It must *not* be rescued by writing a clustering module now and back-filling the published
table; that is fitting code to a result.

---

## B. The `category_speed` grouping leaks the test period

### B.1 The mechanism

`scripts/model_benchmark_category.py:444-446`, in `main()`, **before `make_folds` is called at
line 480**:

```python
series, names, index = mb.load_daily_series(con, args.limit)
group_map = load_group_map(con, series.keys(), args.by)
fsn_class = dict(con.execute(
    "SELECT product_id, fsn_class FROM Dim_Product").fetchall())
```

One `group_map` for the entire run. `load_group_map` builds the speed half of each label from
`Dim_Product.fsn_class` via `forecasting.category.speed_label`.

That column is written by `scripts/step3_fsn_classification.py`, whose source query has no date
filter at all:

```python
fact = pd.read_sql(
    "SELECT product_id, date_id, quantity_sold, imputation_flag, is_censored FROM Fact_Sales",
    con,
)
```

ADUS is aggregated over that whole table, and the F/S split is a cross-sectional quantile of the
resulting full-history distribution:

```python
cutoffs = {t: moving["ADUS"].quantile(t / 100.0) for t in THRESHOLDS}
for t in THRESHOLDS:
    moving[f"class_{t}"] = np.where(moving["ADUS"] >= cutoffs[t], "F", "S")
```

`Fact_Sales` spans the full 25 months, which includes all twelve fold test windows. So fold 0's
pooled model is trained on a partition that already encodes how much each SKU sold in folds 0
through 11.

It is worse than a per-SKU leak, because the 80th-percentile cutoff is *relative*: a SKU's label
depends on other SKUs' full-history totals too. The leak is cross-sectional as well as temporal.

### B.2 Which runs are affected

| `--by` | Leaks? | Why |
|---|---|---|
| `category` | No | `classify()` uses `Dim_Product.category` plus an `item_name` keyword match — static product attributes |
| `product_type` | No | `classify_product_type()` is keyword-only on `item_name` |
| `speed` | **Yes** | Entirely `fsn_class` |
| `category_speed` | **Yes** | Half the label is `fsn_class` |

Every `category_speed` artifact in `data/` is contaminated — which is to say, the headline ones.
`data/model_benchmark_category_summary_category_speed.csv` carries `random_forest_pooled_cat` at
**fill rate 0.8157**, the highest figure anywhere in this project, against production
`rolling_mean_30`'s 0.7746.

### B.3 Why the harness did not catch it

`forecasting/evaluate.py` enforces `Fold.assert_no_leakage()` in `make_folds`, again per fold in
`walk_forward_evaluate`, and again across randomised configs in `tests/test_evaluate.py`. The
guarantee is real, and correctly scoped — the docstring says:

> `fit_predict` is handed nothing but that training slice, so a model physically cannot see its
> own test window.

That is a claim about the callable's input array. The group label is chosen outside the fold loop
and never passes through `fit_predict`, so no assertion in that file can see it.

### B.4 Why the audit was never done: three docstrings state the opposite

`forecasting/category.py:77-81`:

> Dim_Product.fsn_class is the store's own fast/slow-mover tag (F/S …) — controlled vocabulary,
> read-only here, same as `category`. **Not derived or guessed**

`scripts/model_benchmark_category.py:19-21`:

> `speed` — fast / slow mover, from Dim_Product.fsn_class — **the store's own tag** (also
> read-only), **not derived**

and `load_group_map`'s own docstring repeats it.

These are factually wrong. `fsn_class` is not the store's tag and is not controlled vocabulary —
step 3 of this repository's own ETL computes it from `Fact_Sales` and `UPDATE`s it into
`Dim_Product`. It was conflated with `category`, which genuinely *is* a controlled-vocabulary
column. "Read-only here" is true and beside the point: reading a derived label is exactly the leak.

This is the root cause worth recording. The code is otherwise demonstrably leakage-literate —
see C.3 below — and the audit was skipped because a comment told every reader there was nothing
to check.

### B.5 Severity, stated honestly

This is **partition leakage, not target leakage.** The label is one bit, it never enters the model
as a feature, and its effect is that pooling groups are cleaner than they could have been assembled
in real time. The realistic consequence is an optimistic bias of unmeasured magnitude, not a
fabricated result. The fix is cheap. But until it is run, the `category_speed` numbers cannot be
published.

---

## C. Fill rate is contaminated on every path, including `--by category`

A second, independent instance of the same defect. `service_metrics_real_offset`
(`model_benchmark_category.py:288`), and `mb.service_metrics` likewise, select the safety-stock
z-score from the same full-history label:

```python
z = Z_BY_CLASS.get(fsn_class.get(sku), 0.0)
ss = z * sigma * np.sqrt(mb.SERVICE_RISK_PERIOD)
stock = max(pred + ss, 0.0)
```

So the fill-rate column is conditioned on full-history information in **every** run, independently
of the grouping question and regardless of `--by`. Since fill rate is the project's actual
objective metric (section E), this is the more consequential of the two leaks.

### C.1 A related figure that is full-series by design

`skus_priced_pooled` (`model_benchmark_category.py:235-250`) passes the whole series as training,
deliberately:

```python
"""How many SKUs each method would actually price, fit on the FULL
history exactly as skus_priced() does for the per-SKU methods..."""
```

That is defensible — `n_skus_priced` is a deployment-coverage statistic, not a scored metric — but
it must never be read as out-of-sample.

### C.2 What is not leaking

`mb.load_daily_series` reindexing onto a shared calendar span is what makes fold origins identical
across SKUs, and is fine. The `--synthetic-years` bootstrap is *prepended*, so fold origins and
every `actual_30d` are unchanged. `price` is not used as a grouping feature anywhere.

### C.3 The contrast that shows this was an oversight, not a habit

σ is handled correctly, with an explicit warning — `model_benchmark.py:392-393`:

> series would leak the test window into the safety stock and quietly flatter every method — the
> same leakage the harness is built to [prevent]

and `service_metrics_real_offset` slices `series[sku][real_offset:origin]`, strictly pre-origin.
`_real_denom` is equally careful to keep synthetic pre-history out of the MASE denominator. Two
leaks were anticipated and guarded; this one was hidden behind a comment.

---

## D. The "committed champion" claim is inverted

The log states that `weekly_hurdle_12w` "remains the strongest single result on fill rate and is
still the project's committed champion."

On fill rate it is **sixth of thirteen.** From `data/model_benchmark_summary.csv` (all 3,192 folds,
D1-corrected √37 buffer) and `data/model_benchmark_category_summary_category_speed_hurdle.csv`:

| method | fill rate | units held | SKUs priced | MASE |
|---|---:|---:|---:|---:|
| `ets` | **0.7768** | 84,728 | 255 | 9.277 |
| `ewma_a0.1` | **0.7755** | 71,097 | **266** | 5.446 |
| `rolling_mean_30` (production) | **0.7746** | 68,266 | 79 | 5.272 |
| `tsb` | 0.7538 | 69,437 | 266 | 5.326 |
| `logistic_hurdle_per_sku` | 0.7389 | 72,362.8 | 141 | 7.330 |
| **`weekly_hurdle_12w`** | **0.7285** | 69,216.0 | 140 | **4.793** |
| `croston` | 0.7126 | 116,191 | 266 | 12.500 |
| `rolling_median_30` | 0.5062 | 40,274 | 0 | 4.834 |

It is 4.6 percentage points of fill behind `ets` while holding a comparable 69,216 units. Its win is
on MASE/RMSE and on coverage (140 SKUs vs production's 79) — not on service.
`SPARSE_DEMAND_EXPERIMENTS.md:237` says so itself: *"**Not** that `weekly_hurdle_12w` should replace
`rolling_mean_30` in production."*

### D.1 A method may have been dismissed on the wrong axis

Reading that table in the objective's currency rather than MASE's surfaces something the existing
docs miss. **`ewma_a0.1` delivers 0.7755 fill at 71,097 units held and prices all 266 SKUs.**
`ets` buys 0.0013 more fill with 84,728 units — roughly **19% more stock for a rounding error of
service** — and prices 255. On service-per-unit-held, `ewma_a0.1` is arguably the strongest method
in the file.

`docs/FORECAST_METHOD_COMPARISON.md` rejects it purely on error metrics: MASE 5.45, *"sits between
the two methods already in use"* and displaces neither. That judgement was made on the axis
`DEGENERATE_FORECAST.md` had already argued is the wrong one. Worth re-examining before B3.

### D.2 The frontier has never been run on the hurdle models

`tools/service_frontier.py:80` sets `KNEE_METHODS = ["rolling_mean_30", "ets", "tsb"]`, and the
hurdle rows are not in its input `RESULTS_CSV` regardless. So the one comparison that would actually
settle whether the hurdle work advances the objective — hurdle models swept across the q-frontier
against the incumbents at the knee — **does not exist.** It is also the cheapest thing on this list
to produce.

And there is no committed champion to be behind. `docs/STATUS_AND_NEXT_STEPS.md:79`:

> | B3 ⚠ | Which forecasting model to select | Open — cannot run on MASE alone (the MASE winner
> prices 0/266 SKUs). Needs B2 first, then a target service level |

B3 is open and gated on B2, which needs adviser sign-off. The log is right to defer to B3; it is
wrong to describe a champion as already selected.

**Also worth flagging:** `SPARSE_DEMAND_EXPERIMENTS.md` contains **four** experiments, not eleven.
"11" appears in it once, as *"Best MASE/RMSE of 11 methods tested"* in experiment #1. If a claim is
attributed to "experiment #11," confirm which document that number came from.

---

## E. The log is denominated in the wrong currency

`docs/CODE_WORK_PLAN_v2.md:205-207`:

> Proposed reporting: **service level / fill rate ≥95%** as the headline (**the actual business
> objective**; safety stock exists to absorb forecast error), **MASE < 1.0** underneath as the
> model-selection metric.

`docs/SERVICE_LEVEL_FRONTIER.md:154`, quoting Chapter 1 §1.2:

> *"an EOQ-based optimization model to minimize the total inventory cost … subject to a cycle
> service level constraint."*

And `docs/DEGENERATE_FORECAST.md`, which already settled this:

> An acceptance criterion defined purely on forecast error is **structurally invalid** for
> intermittent demand, because its optimum is a forecast of zero. … The problem is not the value
> 20%. The problem is the choice of objective.

Demonstrated, not argued: `rolling_median_30` wins MASE (4.834) and prices **0 of 266 SKUs**.

Across eleven experiments the log reports MAE, MASE, RMSSE and MAPE — and not one fill rate,
holding cost, or SKUs-priced count. By this project's own standing argument it therefore cannot be
evidence for B3, whichever way its numbers fall. The log acknowledges this ("None of this selects a
model"), which is correct and which also limits what it can be used for.

---

## F. Two structural issues the log raises but under-weights

**No holdout exists.** `evaluate.py::make_folds()` lays origins backward from the end of the series
in steps of `horizon` and reserves nothing beyond them.
`docs/BUILD_PLAN_RECONCILIATION.md:22` confirms the original 80/20 holdout was *replaced by*, not
added to, walk-forward. Counting `data/robust_metric_comparison.csv`, roughly 33 method/variant
combinations — plus the frontier knee at q≈0.80, plus K=4 — are now being compared on the same
3,192 folds they are reported on. That is a selection-on-test problem, and it grows with every
experiment added.

**Experiment #10 is a precondition, not a footnote.** If 21% of modelled days are not trading days
(the June–July 2024 gap plus closed Sundays), every MASE denominator and every density figure in
experiments #1–#9 is computed on a contaminated calendar. It is reported last, as something "worth
making before the next forecasting pass." It is upstream of everything above it.

---

## G. What holds up, and should be kept

Recorded deliberately, because an audit that lists only faults misrepresents the work.

- **#4's bug catch is the best thing in the log.** Noticing that `ets` and `rolling_q75_30` produced
  *byte-identical* predictions with and without synthetic data, yet showed MASE improving ~3×, and
  chasing that to a denominator defect, is exactly the right instinct. `_real_denom`
  (`model_benchmark_category.py:151`) is the correct fix, and retracting the earlier claim in the
  document rather than quietly restating it is how this should be done.
- **#6 reproduces exactly.** `data/robust_metric_comparison.csv`'s `pct_mase_from_worst10` column
  really does span 23.1% (`rolling_median_30`) to 73.1% (`lightgbm`, product_type). Mean MASE is
  dominated by a handful of near-dead SKUs, and this finding applies to every table in this project,
  not just the log's.
- **#5 proves a mechanism, not a direction.** Establishing that fixed-window methods are
  *structurally* unable to benefit from prepended history — with identical predictions as evidence —
  is stronger than reporting that they didn't improve.
- **#9 triangulates.** Three methods, including a controlled simulation trained and tested inside
  the same synthetic world, converging on one conclusion. Its finding — that rate instability rather
  than sparsity is the binding constraint — is the most useful result in the log and the one that
  should drive the next experiment.
- **The harness itself.** `evaluate.py`'s leakage guarantee is triple-enforced and correctly scoped.
  The two leaks in this audit are outside it, not failures of it.

---

## Recommended sequence

1. **Resolve #11's provenance.** Human decision. Recover `clustering.py` or strike the experiment.
2. **Close the `fsn_class` leak,** both paths. Recompute ADUS from `values[:fold.train_end]` inside
   the fold loop and re-cut the 80th percentile per fold — for the grouping label (B) *and* the
   safety-stock z-score (C). Correct the three docstrings in `forecasting/category.py` and
   `scripts/model_benchmark_category.py` that call `fsn_class` "not derived"; leaving them is how
   this recurs. Add a test asserting the fold-scoped label differs from the `Dim_Product` label for
   at least one SKU — evidence the leak was real and is closed.
3. **Fix the trading-day calendar (#10) and re-baseline.** Confirm the June–July 2024 gap with the
   client first: a data gap and true zero demand need opposite treatment, and that is not a call to
   make from the code. Do not widen `Dim_Date.is_store_closed` to cover regular Sundays — that flag
   means special closures; add a separate derived trading-day mask.
4. **Re-score every surviving variant in the objective's currency.** `tools/service_frontier.py`
   already re-scores existing fold CSVs without fitting models, and its expanding-window empirical
   quantile is leakage-safe. Report fill rate at the knee, units short, units held, and SKUs priced
   / 266 alongside MAE and MASE. A variant that improves MASE while pricing fewer SKUs is a
   `DEGENERATE_FORECAST.md` #21 regression and must be reported as one.
5. **Carve a real holdout before B3 is answered.** Reserve the most recent ~90 days, select on folds
   strictly before it, and score only the finalists on it, once.

Per `CLAUDE.md`, a failing assertion in any verification script is reported, never relaxed. Several
of the steps above are *expected* to move committed numbers, and that movement is the result — not a
regression to suppress.

---

## What this audit does not say

- **Not** that the pooling work was wasted. #5, #6 and #9 stand on their own, and #7's pooled hurdle
  is a real improvement on a fix `SPARSE_DEMAND_EXPERIMENTS.md` §2 had only named.
- **Not** that the leaks fabricated the results. Both are partition leaks of a one-bit label; the
  expected effect is optimistic bias of unmeasured size. "Unmeasured" is the operative word — the
  re-run is what settles it, not this document.
- **Not** that clustering is a bad idea. #3's finding that product *type* fails to predict
  poolability while product *speed* partly succeeds is a genuine motivation for behavioural
  clustering. The objection to #11 is that it has no implementation in this repository, not that its
  hypothesis is wrong.
- **Not** a verdict on B3. B3 belongs to the team and is gated on B2. This audit narrows what
  evidence can be brought to it; it does not answer it.
- **Not** a claim about intermittent demand in general. Every number here is specific to
  `ustore.db`'s 266-SKU, 821-day catalogue, exactly as `SPARSE_DEMAND_EXPERIMENTS.md` says of its own.

## Related

- `docs/SPARSE_DEMAND_EXPERIMENTS.md` — the four experiments the audited log follows up on
- `docs/DEGENERATE_FORECAST.md` — #21, why an error-only acceptance criterion is invalid here
- `docs/SERVICE_LEVEL_FRONTIER.md` — #22, the frontier and the q≈0.80 knee
- `docs/STATUS_AND_NEXT_STEPS.md` — the B1–B15 decision register; B3 at line 79
- `forecasting/evaluate.py` — the harness and the exact scope of its leakage guarantee
- `scripts/step3_fsn_classification.py` — where `fsn_class` is actually computed
