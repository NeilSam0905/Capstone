# Forecasting Model Decision Memo — USTore Capstone

> **Purpose:** Prophet underperformed (§9 / open work item #6). This document summarizes what was
> tested, what the evidence shows, and the available options with their documentation costs.
> **Status:** for adviser/panel discussion. Nothing here has been applied to the manuscript.
> **Evidence files:** `prophet_diagnostic_results.csv`, `model_benchmark_results.csv`,
> `prophet_lever_test.csv`. Scripts: `prophet_diagnostic.py`, `model_benchmark.py`.

---

## 1. What was tested

All experiments use the **89 SKUs in the ≥60-observation tier** (§7.2's standard-fit tier — Prophet's
best case), a **chronological 80/20 hold-out** per SKU, 2,347 test points. Identical splits across all
models, so every number below is directly comparable.

### 1.1 Prophet diagnostic (§9's four questions)

| Model | MAE med | MAE mean | RMSE med | MAPE% med | % beats naive | max MAPE |
|---|---|---|---|---|---|---|
| naive_last | 2.54 | 6.12 | 4.20 | 64.7 | — | 3,437% |
| naive_mean | 2.94 | 4.62 | 3.62 | 137.1 | — | — |
| prophet_base (vanilla) | 6.93 | 179.34 | 13.64 | 204.5 | 4% | **554,605%** |
| prophet_reg (+ all §7.2 regressors) | 10.34 | 172.34 | 18.08 | 222.5 | 2% | 537,786% |
| prophet_tuned | 3.35 | 6.67 | 4.68 | 105.1 | 8% | 2,611% |
| **prophet_flatlog** | **2.66** | **4.08** | **3.67** | **77.6** | **34%** | **422%** |

**Answers to §9:**
- **(a) The regressors were included and they *hurt*.** `prophet_reg` is worse than vanilla. Cause: the
  continuous `semester_week` extrapolates badly across semester resets.
- **(b) Prophet genuinely loses to the naive baseline**, not merely misses 20% MAPE.
- **(c) High MAPE is intrinsic burstiness, not break-period instability** — semester-only MAPE (221.8%)
  ≈ all-test MAPE (222.5%). MAE/RMSE are the honest metrics.
- **(d) It fails even on the best-case ≥60 tier.**

### 1.2 Prophet repair attempts

| Lever | median MAE | Verdict |
|---|---|---|
| Drop `semester_week` | 3.35 → 3.05 | Helps modestly; confirms the regressor was harmful |
| `growth='flat'` (no trend extrapolation) | 3.42 | Alone, no help |
| **`log1p` transform + flat growth + no `semester_week`** | **2.66** | **The fix** |

`log1p` is decisive because the series are *multiplicatively* bursty (CV ≈ 1.11). On the raw scale a
single 671-unit enrollment spike contaminates the fit; on the log scale it is a proportional jump that
Prophet's additive model can represent. This collapsed worst-case MAPE from 554,605% → 422%.

### 1.3 Alternative model benchmark

| Model | MAE med | MAE mean | RMSE med | MASE med | % MASE<1 |
|---|---|---|---|---|---|
| **rolling_med** (median of last 5) | **2.24** | **3.96** | **3.74** | **0.61** | **76%** |
| naive_last | 2.54 | 6.12 | 4.20 | 0.70 | 64% |
| ewma (α=0.3) | 2.63 | 5.62 | 4.17 | 0.76 | 69% |
| prophet_flatlog | 2.66 | 4.08 | — | — | — |
| SBA (bias-corrected Croston) | 2.76 | 4.85 | 3.83 | 0.75 | 75% |
| Croston | 2.84 | 4.99 | 3.98 | 0.78 | 73% |
| global XGBoost | 3.33 | 5.07 | 4.17 | 0.84 | 66% |
| prophet_tuned | 3.35 | 6.67 | 4.68 | — | — |

**Rolling median beats every other method**, including Prophet, on all four metrics — and beats
`prophet_flatlog` on 70% of SKUs. The median is robust to bursts; every mean/trend-based method is
contaminated by spikes.

**Note:** global XGBoost was expected to win (M5-competition family) and placed 7th of 8. Causes:
9,114 training rows is thin for GBMs, and a 30-day block forecast freezes its most valuable lag
features.

---

## 2. Two findings that constrain every option

### 2.1 MAPE ≤ 20% is unreachable on this data

Tested at every aggregation level:

| Approach | Best median result | % SKUs ≤20% |
|---|---|---|
| Per-tally-point | 64.7% | 6% |
| Monthly aggregation | 70.3% | 3.4% |
| High-volume SKUs only | 63.9% | 0% |
| WAPE instead of MAPE | 65.6% | 3% |
| **Pooled across ALL SKUs (whole store)** | **43.7%** | **0%** |

**Theoretical floor:** for a *perfect* forecast, MAPE ≈ 0.8 × CV → **~89% daily, ~60% monthly**.
Aggregation does not rescue it because the bursts are **structural** (enrollment/event months are
genuinely 5–10× normal), not random noise that averages out. Monthly CV only falls 1.11 → 0.75.

**Implication: the ≤20% MAPE criterion in §7.4 must be revised regardless of which model is chosen.**

### 2.2 The forecasting target is currently mis-specified

All evaluations so far predict **units on already-known tally dates**. This is the wrong question:

- Tally dates are **recording events, not demand events** — a 67-day gap means nobody wrote anything
  down, not that demand was zero. Predicting dates would mean predicting staff behaviour.
- **In production you would not know future tally dates**, so the models were given information they
  will not have at forecast time.
- **ROP/EOQ (§7.3) does not need dates** — it needs `D` (30-day total demand) and `σ_demand`.

**The correct target is total units per SKU over the next 30 days** — one number per SKU per billing
cycle, matching the consignment settlement period. This also lets calendar regressors work at the
*month* level (known in advance, genuinely predictive) instead of fighting irregular spacing.

---

## 3. Options

### Prerequisites (recommended regardless of model choice)

| # | Action | Cost |
|---|---|---|
| **P1** | **Re-target forecasting to 30-day aggregate demand** per SKU | Re-run evaluation; amend §7.2 horizon wording (already says 30 days) |
| **P2** | **Replace MAPE ≤20% with an achievable criterion** | Amend §7.4 acceptance criteria |

Suggested replacements for P2 (all standard for intermittent demand):
- **MASE < 1.0** — "beats the naive baseline." Hyndman's recommended metric for this data profile.
  Currently achieved by **76% of SKUs** (rolling_med, median MASE 0.61).
- **WAPE ≈ 40%** at portfolio/supplier level — volume-weighted, realistic.
- **Service level / fill rate ≥ 95%** — the actual business outcome and what management cares about.

> **Key argument for P2:** §7.3's `SafetyStock = Z × σ_demand × √(Lead Time)` **exists to absorb
> forecast error**. A 60%-MAPE forecast with correctly sized safety stock still delivers a 95% service
> level. The project objective (prevent stockouts) is achievable even though ≤20% MAPE is not.

---

### Option A — Keep Prophet, corrected specification

Adopt `prophet_flatlog`: `log1p` transform, `growth='flat'`, calendar regressors **without**
`semester_week`.

| | |
|---|---|
| **Accuracy** | MAE mean 4.08 (beats naive's 6.12); MAE med 2.66; beats naive on 34% of SKUs |
| **Manuscript impact** | **None.** §8 decision #1 stands; no amendments to Ch.2, Objective 3, §3.3.2, Table 3, or the two figures |
| **Pros** | Preserves the entire documented justification; Prophet demonstrably *works* once correctly specified; keeps the multiple-seasonality/holiday-effects narrative |
| **Cons** | Not the most accurate option (rolling_med beats it on 70% of SKUs); requires documenting the log-transform and flat-growth deviations from a default Prophet |

---

### Option B — Switch to the best-performing model (rolling median)

| | |
|---|---|
| **Accuracy** | Best on every metric: MAE med 2.24, mean 3.96, RMSE 3.74, MASE 0.61 |
| **Manuscript impact** | **High.** Amend Ch.2 §2.1.4, Objective 3, §3.3.2, Table 3, and two figures |
| **Pros** | Best accuracy; zero fitting cost; no Prophet/Stan dependency; fully explainable to staff and panel |
| **Cons** | **Rolling median is a classical *baseline*, not a sophisticated model** — no trend, no seasonality, and it **ignores the Dim_Date calendar flags entirely**. A panel may reasonably question the analytical contribution of a 1-line heuristic as the headline deliverable |

> If chosen, the contribution must be reframed as **the evaluation framework and the finding**
> ("burstiness favours robust estimators"), not the model itself.

---

### Option C — Two-track system by FSN class *(recommended)*

Route each SKU to the appropriate method based on its FSN classification (§7.1):

| FSN class | Method | Rationale |
|---|---|---|
| **F / HVL** (fast, ≥60 obs) | `prophet_flatlog`, with rolling_med as the documented challenger | Keeps §8 decision #1 intact where Prophet is justified |
| **S** (slow / 30–59 obs) | **Croston / SBA** | Purpose-built for intermittent demand; beats naive on 39% of SKUs |
| **N / <30 obs** (162 SKUs) | rolling median or 30-day rolling average + "Insufficient Data" flag | Already specified in §7.2's sufficiency tiers |

| | |
|---|---|
| **Manuscript impact** | **Low–moderate.** §8 decision #1 survives; add Croston/SBA to §2.1.4 as complementary methods for the intermittent tail |
| **Pros** | Matches the existing FSN architecture; each method used where it is theoretically appropriate; defensible and literature-aligned; strongest analytical narrative |
| **Cons** | More implementation surface; requires FSN classification to run first (currently `fsn_class` is NULL in Dim_Product) |

---

### Option D — Add leading indicators (accuracy ceiling raise)

The only route to a *large* accuracy gain. Your bursts are **caused by known events**; history alone
cannot predict their magnitude.

Candidate inputs: confirmed enrollment headcount per term, event attendance projections, varsity
schedules with expected turnout, graduation cohort sizes.

| | |
|---|---|
| **Pros** | Genuinely raises the achievable accuracy ceiling — the others only reallocate existing signal |
| **Cons** | Requires data USTore may not have or share; adds a data-acquisition dependency to the project scope; may not be feasible within Capstone 2's timeline |

---

### Non-option — Adding the 2023 batch data

**Will not help.** Tested and rejected on evidence:

- **History length does not predict Prophet skill.** SKUs with 219 median observations (2+ years)
  still lose to naive by 24%; Spearman correlation between `n_obs` and skill = **0.13**.
- The 2023 file has **6 batch periods with no dates inside them** and only **1 of 34 labels** matching
  a current SKU — ~88 of the 89 tier-1 SKUs would gain zero usable points.
- §8 decision #2 already settled this (synthetic smearing would dilute the holiday-regressor
  coefficients that justify Prophet at all).
- The file is **not present in the working directory**.

> **Better use of existing inventory data:** flag **stockout periods** — dates where demand was
> *censored* (zero sales because stock was unavailable, not because demand was absent). This improves
> any model and feeds ROP directly.

---

## 4. Recommendation

**Adopt P1 + P2 + Option C.**

1. **P1** — re-target to 30-day aggregate demand. The current per-tally-point evaluation answered §9's
   diagnostic questions but is the wrong experiment for the ROP/EOQ decision it feeds.
2. **P2** — replace MAPE ≤20% with **MASE < 1.0** (primary) and **service level ≥95%** (business
   outcome). MAPE ≤20% is provably unreachable and will fail review if defended as-is.
3. **Option C** — two-track by FSN class. It preserves §8 decision #1 (no Ch.2 rewrite), uses each
   method where it is theoretically justified, and turns the Prophet underperformance into a
   *documented analytical finding* rather than a project failure.

**Report the Prophet diagnostic as a Chapter 4 result.** "We implemented Prophet, correctly specified
it, benchmarked it against seven alternatives under a rigorous protocol, and characterized where it
succeeds and fails on sparse bursty consignment demand" is a stronger contribution than an unexamined
model that happens to work.

---

## 5. Open caveats

- All results come from a **single 80/20 chronological tail per SKU**. §7.4 specifies walk-forward
  validation — a **walk-forward 30-day-window re-run** should confirm the ranking before anything is
  committed to the manuscript. Margins are wide enough that reversal is unlikely, but the protocol
  should match what is documented.
- Results cover the **≥60-observation tier only** (89 of 297 sold SKUs). The 30–59 and <30 tiers are
  untested and are precisely where Croston/SBA are expected to matter most.
- The `CENTRAL SEMINARY` supplier merge (work item #1) remains an unconfirmed assumption.
- Two closed-day tally dates (2025-06-12, 2025-11-30) are handled computationally but their provenance
  is still unverified with staff.
