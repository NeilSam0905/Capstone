# Chapter 4 reconciliation audit

`docs/CHAPTER_4_DRAFT.md` is dated **2026-09-16**. The policy layer landed **2026-09-22/23**. This
document is the line-by-line audit that the edit of the chapter was made from: every principal
finding, every numbered table, every prose subsection and every row of *"Sources for every figure"*,
marked **intact**, **superseded**, **narrowed** or **missing** against the four documents the chapter
predates — `docs/PRESCRIPTIVE_CONTRACT.md`, `docs/ACCEPTANCE_STANDARD.md`, `docs/POLICY_HOLDOUT.md`,
`docs/INVENTORY_SIMULATION.md` — plus the two work logs that record them.

**Nothing here was recomputed.** Every figure cited below is transcribed from a committed document
and named to it. Where two committed documents disagree on the same quantity, the disagreement is
recorded in §6 rather than resolved by re-running anything.

**Verdict in one line:** of 8 principal findings, **4 intact** (1, 5, 6, 7), **1 intact and
extended** (8), **3 superseded** (2, 3, 4). Of 17 numbered tables, **6 fully intact** (5, 6, 7, 16,
17, 18), **1 intact but incomplete** (15), **4 intact as measurements yet superseded in the role the
chapter gives them** (8, 9, 10, 11), and **6 carrying rows that are now wrong** (12, 13, 14, 19, 20,
21). Of 13 source rows, **all 13 intact and none removed**, with six further kinds of evidence to
add. Nine bodies of committed evidence have no home in the chapter at all.

---

## 1. What the four labels mean

| Label | Meaning | What the edit does |
| --- | --- | --- |
| **intact** | The measurement stands and the claim built on it stands. | Keep as written. |
| **narrowed** | The measurement stands; a limitation attached to it has since been partly closed. | Keep, add the narrowing and the document that closed it. |
| **superseded** | The claim describes a design that has been replaced, or a reading later measurement contradicts. | Rewrite. The original measurement is kept and re-scoped, never deleted. |
| **missing** | Committed evidence the chapter does not mention. | Add. |

A superseded *reading* almost never means a wrong *number*. Tables 10, 11 and the 0.9490 ceiling are
all still correct measurements of the benchmark path; what changed is that the benchmark path no
longer decides anything downstream.

---

## 2. Principal findings

### Finding 1 — an error-only acceptance criterion is structurally invalid — **INTACT**

Strengthened, not weakened. It is now the opening argument of two further committed documents:
`PRESCRIPTIVE_CONTRACT.md` ("Why there is a contract at all") and `ACCEPTANCE_STANDARD.md` ("Why a
new criterion") both reproduce it, both citing the same 68,541-of-84,399 zero share and the same
`rolling_median_30` prices-0-of-266 result. Keep verbatim; add that it is what the contract and the
standard are *built on*, not merely consistent with.

### Finding 2 — a fixed 95% service level is unreachable by 0.10pp — **SUPERSEDED**

The number survives; the claim does not.

- **Survives, re-scoped:** the 0.9490 arithmetic ceiling — 584 folds across 103 SKUs with flat-zero
  training slices, 2,732 units, 5.1% of scored demand — is reaffirmed verbatim in
  `PRESCRIPTIVE_CONTRACT.md` §3, explicitly as a property of **the benchmark path**.
- **Superseded:** *"the obvious replacement is unreachable"* as a general statement. On the policy
  path `PRESCRIPTIVE_CONTRACT.md` §3 says reaching high service *"is not impossible but is expensive
  and varies enormously by window"*, and one rolling origin (2026-02-02) measures **0.9149**
  (`POLICY_HOLDOUT.md`). `service ≥ 95%` is no longer encoded anywhere as pass/fail — not because it
  cannot be hit, but because it would be *"a gate that mostly fails for reasons nothing in the
  pipeline controls."*
- **Also superseded:** the *shape* of the replacement. A fixed threshold was not replaced by a
  frontier alone; it was replaced by a **four-condition standard** with a priori thresholds, which
  the system currently **fails** on one of four (`ACCEPTANCE_STANDARD.md`).

### Finding 3 — a frontier with a measured knee at q ≈ 0.80 — **SUPERSEDED**

The knee is real on the benchmark's curve and **absent on the policy's own**.

- `POLICY_HOLDOUT.md`: *"NO interior knee at q = 0.8 on this curve"* — marginal holding goes 4.8
  (q=0.80) → 9.4 (q=0.95), a **1.96×** rise where `tests/test_service_frontier.py`'s knee test
  requires **> 2×**.
- `PRESCRIPTIVE_CONTRACT.md` finding 2 states the same conclusion from a different run: 5.0 → 9.4,
  **1.88×**. *"The benchmark frontier's knee is real on the benchmark's curve; it is inherited, not
  confirmed, on this one."*
- The chapter's sentence *"the knee is measured rather than chosen"* is therefore false as a claim
  about the policy. It must become: measured on the benchmark's curve, **inherited** on the
  policy's, which is precisely why the operating point is now resolved **per service tier from each
  SKU's own curve** — eight distinct quantiles, q = 0.50 … 0.95, where there was one.
- **Also missing from this finding:** the dial USTore actually turns is not a quantile at all. It is
  a **stock budget in units they already count**, allocated greedily by marginal units-served-per-
  unit-held (`POLICY_HOLDOUT.md`, "The dial").

### Finding 4 — at the knee the production model dominates — **SUPERSEDED**

Moot rather than wrong. Table 11's measurement stands. What it decided no longer needs deciding: the
prescriptive layer **does not consume a point forecast**. It consumes a demand rate and an
uncertainty (`PRESCRIPTIVE_CONTRACT.md`, opening). There is no operating point at which
`rolling_mean_30` beating `ets` and `tsb` selects the production model for the prescriptive layer,
because the layer never reads a forecast. `backend/pipeline.py:33` states it in the code:
*"step5a/step5, neither of which read `Result_Forecast`."*

The replacement finding is stronger and is measured: **replenishing at all is worth +26 points of
fill; which rule you replenish by is worth 0.6** (`INVENTORY_SIMULATION.md`).

### Finding 5 — the binding constraint is rate instability, not sparsity — **INTACT**

Untouched by the policy layer, and now load-bearing in a way the chapter could not know: it is the
diagnosis `PRESCRIPTIVE_CONTRACT.md` §1a cites as the reason to reopen the rate window —
*"the only lever tested that addresses §9's diagnosed bottleneck — the underlying rate shifts — head
on rather than around it."* Keep verbatim; add the forward pointer.

### Finding 6 — pooling helps, but only on a behavioural basis — **INTACT**

Untouched, and now deployed rather than only benchmarked: `forecasting/clustering.py` supplies the
same K = 4 clusters on the same five features to the policy's rate shrinkage
(`PRESCRIPTIVE_CONTRACT.md` §1, "The shrinkage"). One addition worth making: the shrinkage that
consumes those clusters was measured and **defaulted off** as dominated — +12.7 units served for
+155.1 units held, and it prices no additional SKU. That is a result about the *fallback*, not about
the clustering, and does not disturb the finding.

### Finding 7 — two partition leaks found by audit, closed, results re-run — **INTACT**

Untouched. Its lesson — a docstring is not evidence — is restated as policy in three later places
(`POLICY_HOLDOUT.md`'s re-derivation gate, `tests/test_gates_can_fail.py`,
`PRESCRIPTIVE_CONTRACT.md` §4: *"a docstring promising `upto=split` is not evidence"*). Keep; add the
continuity.

### Finding 8 — the prescriptive layer is arithmetically sound and economically unresolved — **INTACT, EXTENDED**

Not named in the work log's audit of findings 1–7, adjudicated here. The 12.65× swing, the 204/208
and 208/208 counts and the 4.34× / 54.94× medians are all untouched. What is new is the **first
measurement of what the swing actually does**, from `INVENTORY_SIMULATION.md`: across the two
scenarios, **fill is identical to four decimals (0.9345)** while mean on hand goes **9,486 → 53,426**
and units ordered go **18,169 → 229,823** against 4,565 units of realised demand — a 50× overshoot.

That sharpens the finding: the ambiguous USTore figure does not put *service* at risk, it puts
*holding* at risk, and it is the EOQ rather than the reorder point that becomes the dominant term.
The chapter's decision to lead the Reorder screen with an order-up-to level is vindicated by
measurement rather than only by caution.

---

## 3. Numbered tables

> **Numbering.** The numbers below are the **16 September draft's**, which is what an audit of that
> draft must use. The reconciled chapter renumbers every table in document order and runs 5 → 38.
> Tables 5–17 keep their numbers; the old **Table 18** (accuracy by demand density) becomes **31**,
> **19** → **36**, **20** → **37**, **21** → **38**, and the seventeen tables added by the edit take
> 18–30 and 32–35.

| # | Table | Status | What the edit does |
| ---: | --- | --- | --- |
| 5 | Loaded dataset volumes | **intact** | Keep. 84,399 / 68,541 are re-cited by both new documents. |
| 6 | Proportional allocation by weighting basis | **intact** | Keep. |
| 7 | FSN classification of 519 items | **intact** | Keep. |
| 8 | Fifteen forecasting methods | **intact as measurement; role superseded** | Keep every figure. Add that the benchmark now selects nothing downstream — it is the evidence *for* the degeneracy argument, not a model-selection instrument. |
| 9 | Service outcome of ten statistical methods | **intact as measurement; role superseded** | Keep. Re-scope "against a 95% target": that target no longer exists. The residual-leak footnote stands. |
| 10 | Service / holding frontier for `rolling_mean_30` | **intact as measurement; reading superseded** | Keep the table. The knee reading moves to the benchmark path only, and is set beside the policy's own frontier, which has no knee. |
| 11 | Method comparison at the knee | **intact as measurement; role superseded** | Keep. Label explicitly as a benchmark-path result that no longer settles model selection. |
| 12 | Validation of the production forecast against the acceptance criteria | **partly superseded** | "The acceptance criteria" is the retired criterion. Re-title to name the retired criteria, keep every row, and carry the last row — 26 of 58 SKUs with a positive forecast — forward as the opening of the orphaned-predictive-stage finding. |
| 13 | Prescriptive inputs (all provisional) | **two rows superseded** | `Service z` 1.65 / 1.04 is **retired as the buffer** — `z_value` is still populated but is now a comparison (`PRESCRIPTIVE_CONTRACT.md` §2/§3). `Review period 30 days` is the benchmark's periodic-review assumption; the policy's reorder point is continuous review at the SKU's own lead time. `Demand basis` is no longer a default pending a model decision — it is the committed contract. Holding cost, ordering costs and lead time are intact. |
| 14 | Prescriptive results | **three rows superseded** | `Result_Prescriptive` rows is **no longer 416**. The gate in `scripts/step5_prescriptive.py:876` requires `rows == priced × 2 + flagged` = 208 × 2 + 58 = **474**; the 58 flagged rows are the change that matters. The two σ-provenance rows (334 / 82 of 416) describe the retired `z·σ` buffer; `buffer_source` now records `empirical_quantile` / `normal_z_sigma_fallback`. EOQ rows and the 44 / 164 priced split are intact. |
| 15 | Experiments that did not improve accuracy | **intact; incomplete** | Every row stands. It is now only half the experiment record: the policy levers tested and ruled out (`WORKLOG_POLICY_AND_ACCEPTANCE.md` §5) and the cold-start donor rules (`WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §2) belong beside it as a separate table, because they are negative results about *policy*, not about accuracy. |
| 16 | Grouping bases for pooled models | **intact** | Keep. |
| 17 | Cluster composition and per-cluster accuracy | **intact** | Keep; note these are the clusters the policy's shrinkage consumes. |
| 18 | Accuracy by demand density | **intact** | Keep. |
| 19 | Application screens and their state | **one row stale** | Reorder Alerts must state that flagged SKUs now render an explicit state and a manual-review note — `backend/app.py` previously coerced the NULL reorder point to `0.0` and rendered `needs_reorder = False` (`WORKLOG_POLICY_AND_ACCEPTANCE.md` §1). Demand Forecast is unchanged but should say the prescriptive layer does not read it. |
| 20 | Power BI: five views and their readiness | **two rows stale** | View 1 (Stock Status) is still blocked, but the blocking coverage is now quantified from the workbook that `INVENTORY_SIMULATION.md` reads: 73 of 266 scored SKUs, ~18% of demand. View 4 (Restocking Advisory) must show the flagged rows, not only the priced ones. |
| 21 | Objectives and outcomes | **two rows superseded** | Objective 3's outcome rests on "the stated criterion" — now replaced by a four-condition standard with a stated verdict. Objective 4's "Met, provisionally" must carry both the 58 flagged SKUs and the **NOT ACCEPTED, 13 of 14** verdict. Objectives 1, 2, 5 intact. |

---

## 4. Prose subsections

| Section | Status | Note |
| --- | --- | --- |
| §4.1 Integrated dataset | **intact** | Including the 416-not-411 correction. |
| §4.1 Data quality | **intact** | The 16.8% stock-signal figure is narrowed later, not here. |
| §4.1 Product classification | **intact** | |
| §4.1 Forecast method comparison | **intact** | |
| §4.1 Service level and holding cost | **intact as measurement** | The closing sentence — "77.7% against a 95% target" — needs the target re-scoped. |
| §4.1 The production forecast | **intact** | |
| §4.1 Prescriptive outputs | **superseded in part** | Describes inputs and outputs of a layer that has since gained a contract, three rate states, service tiers, an empirical buffer and 58 flagged rows. |
| §4.1 Experiments on the sparse-demand problem | **intact** | |
| §4.2 opening (scope, sparsity, allocation tiers) | **intact** | |
| §4.2 The acceptance criterion is degenerate | **intact** | The chapter's strongest section and the foundation of both new documents. |
| §4.2 The obvious replacement is also unreachable | **superseded in framing** | Causes 1, 2, 3 are all intact measurements. Cause 2's ceiling must be scoped to the benchmark path; Cause 3's empirical quantile is now the deployed buffer, measured at lead-time horizon rather than at 30 days, which is a *change of estimator*, not only of quantile. |
| §4.2 What the frontier delivers | **superseded** | The section that most needs rewriting. Its three consequences are (1) the knee is evidence — now false on the policy's curve; (2) `rolling_mean_30` dominates — now moot; (3) 0.742 is the honest service level — superseded by a rolling-origin *distribution*, median 0.7064, range 0.5987–0.9149. |
| §4.2 The cost of the trailing window | **intact, promoted** | The 208-vs-26 table is exactly the orphaned-predictive-stage finding, arrived at from the other direction. Keep and connect. |
| §4.2 Why accuracy does not improve | **intact** | |
| §4.2 Pooling, and the leakage audit | **intact** | |
| §4.2 The prescriptive layer rests on provisional costs | **intact, extended** | See finding 8. |
| §4.2 Validity of the comparison | **narrowed** | "No untouched holdout exists" is now true of the **benchmark path only**. The policy path is scored at four rolling origins with every fitted quantity selected strictly pre-origin and a gate that re-derives them with the scored window blanked. The most recent window remains a development set and the chapter must say so. |
| §4.3 The delivered system | **intact, one row stale** | See Table 19. |
| §4.3 The Power BI layer | **intact, two rows stale** | See Table 20. |
| §4.3 What the dashboard does not show | **intact** | All three omissions stand. |
| §4.4 Against the objectives | **two rows superseded** | See Table 21. |
| §4.4 Principal findings | **3 of 8 superseded** | See §2. |
| §4.4 Limitations | **three narrowed, one to add** | Inventory coverage — narrowed by `INVENTORY_SIMULATION.md`. Trading-day calendar — narrowed on the policy path by the observed-days denominator. No untouched holdout — narrowed to the benchmark path. **To add: cold start**, the named structural limit carrying 11.68% of demand. |
| §4.4 Decisions this chapter does not make | **intact, to extend** | Add: whether a system failing one of four conditions should be adopted; whether the failing threshold should be measured against the achievable ceiling; whether `cascade + flat` or `tiered + 365d` is the better policy. |

---

## 5. "Sources for every figure" rows

All thirteen existing rows are **intact** — every artifact named still exists at the path given
(verified by existence, not by re-running). Nothing is removed. Six kinds of evidence are
**missing**:

| Missing row | Artifact |
| --- | --- |
| The prescriptive contract | `docs/PRESCRIPTIVE_CONTRACT.md`; `forecasting/policy.py`, pinned by `tests/test_policy.py` |
| The acceptance standard | `docs/ACCEPTANCE_STANDARD.md`; `tools/acceptance_standard.py`, proved falsifiable by `tests/test_acceptance_standard.py` |
| Policy validation on rolling origins | `docs/POLICY_HOLDOUT.md`; `scripts/validate_policy_holdout.py` |
| The inventory simulation | `docs/INVENTORY_SIMULATION.md`; `tools/inventory_simulation.py`, pinned by `tests/test_inventory_simulation.py`; `data/inventory_simulation.csv`, `data/inventory_stock_depth.csv` |
| The rate-window cascade | `docs/PRESCRIPTIVE_CONTRACT.md` §1a; `forecasting/policy.py::resolve_rates` |
| Gates and their falsifiability | `scripts/step5_prescriptive.py` (`run_gates`), `tests/test_gates_can_fail.py` |

The two work logs — `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` and
`docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md` — are added as the narrative record behind all six,
joined by `docs/WORKLOG_RECONCILIATION_AND_WIRING.md`, the session that acted on this audit.

In the reconciled chapter the table ends up with more rows than this, because the renumbering splits
one existing range: the old `Tables 15–18` row spanned a section boundary and the seventeen new
tables land inside it, so it becomes `Tables 15–17` plus a row of its own for the density table (now
31). The source table is also reordered to run in table order, which it previously did not.

---

## 6. Where two committed documents disagree

Recorded, not resolved. Nothing below was re-run to adjudicate it. Where the chapter must pick one,
it picks the set that **`scripts/validate_policy_holdout.py` generates** and that three documents
agree on, and says so.

| # | Quantity | `POLICY_HOLDOUT.md` / `ACCEPTANCE_STANDARD.md` / `WORKLOG_POLICY` §3 | `PRESCRIPTIVE_CONTRACT.md` | Chapter uses |
| --- | --- | --- | --- | --- |
| A | Rolling-origin fill | 0.6851 / 0.9149 / 0.7277 / 0.5987 — median **0.7064**, range 0.5987–0.9149 | §4: 0.6854 / 0.9145 / 0.7352 / 0.6339 — median 0.7103, range 0.6339–0.9145 | the three-document set |
| B | Tiered vs flat at the development origin | tiered 0.6851 @ 14,750.7; flat q=0.80 0.6188 @ 15,122.2 | §1b: tiered 0.6854 @ 14,780; flat 0.6180 @ 15,156 | the three-document set |
| C | Empirical vs normal buffer | flat q=0.80 0.6188 @ 15,122.2; normal z·σ 0.6022 @ 21,029.2 | §4: 0.6191 @ 15,311.5; 0.6019 @ 21,021.3 | the three-document set; the claim (higher service, ~27% less stock) holds under both |
| D | Marginal holding, q=0.80 → q=0.95, on the policy's frontier | 4.8 → 9.4 = **1.96×** | finding 2: 5.0 → 9.4 = **1.88×** | **both**, stated as a range; both are below the > 2× the knee test requires, which is the claim |
| E | Stock-budget dial at 1× | 0.6130 @ 14,647.0 | §3: 0.6133 @ 14,578 | the three-document set |

Two further pairs look like conflicts and are not — different populations, both correct:

| # | Quantity | Reading A | Reading B | Why both stand |
| --- | --- | --- | --- | --- |
| F | Service-tier counts | `PRESCRIPTIVE_CONTRACT.md` §1b: servable 24 / partial 166 / not_stockable 18 = **208** | `POLICY_HOLDOUT.md`: servable 24 / partial 177 / not_stockable 14 = **215** | A is the priced catalogue; B is the population scored at the development origin. The chapter states which. |
| G | Inventory-workbook coverage | Chapter §4.1: 75 of 286 selling products in the workbook; 62 of 519 with a derivable `days_of_supply` | `INVENTORY_SIMULATION.md`: 73 of 266 **scored** SKUs (27%), ~18% of demand | Different denominators — selling products vs scored SKUs, and workbook presence vs a derivable days-of-supply. |
| H | Non-trading days | Chapter §4.2: **169 of 821 (21%)** plausibly not trading days — the 61-day 2024 gap plus zero Sundays | `PRESCRIPTIVE_CONTRACT.md`: **139 of 821 (16.9%)** carrying no record of a sale *or* a closure | Different definitions. The policy path already divides by observed days and so acts on the 139; `Fact_Sales` is untouched, so every density figure in §4.1 still sits on the full calendar. |
| I | MAPE pass count | Chapter Table 12: **1 of 58 SKUs** at the overall period scope | `WORKLOG_INVENTORY` §7b: **2 of 174 rows** of `Result_Forecast_Metrics` | 174 rows = 58 SKUs × three period scopes. Different units, not different answers. |

---

## 7. Committed evidence with no home in the chapter — **MISSING**

Nine bodies of measured, committed evidence the chapter does not mention at all. Each becomes
content in the edit.

1. **The forecast → prescriptive contract.** Three rate states and the fact that one of them is not
   a number: `observed` 208, `cluster_pooled` 0 (off by default), `insufficient_data` **58** — rows
   that previously did not exist at all. `PRESCRIPTIVE_CONTRACT.md` §1.
2. **Service tiers.** Eight operating points where there was one; tiering on **efficiency**, not
   fill, and the measured reason (a fill floor discards 54 SKUs carrying 28.5% of demand; an
   efficiency floor discards 52 carrying 1.8%). §1b.
3. **The empirical buffer.** 7.46 units mean against 13.84 for the retired `z·σ`, measured at the
   SKU's own lead-time horizon rather than scaled from a 30-day quantile. Smaller *and* serving more.
   §2.
4. **The acceptance standard and its verdict.** Four conditions, a priori thresholds, every one
   falsifiable by fixture; **NOT ACCEPTED, 13 of 14**, forward coverage 0.8830 against 0.90. Plus
   the trailing-coverage tautology caught and removed, and the deliberate exclusion of efficiency as
   a condition for being degenerate in exactly MAPE's way. `ACCEPTANCE_STANDARD.md`.
5. **Cold start as a named structural limit.** 6,257 units, **11.68%** of realised demand, from SKUs
   that had never sold a unit before the decision point; 99.8% of the shortfall. Ceiling on forward
   coverage for any history-fitted method **0.8832**; achieved 0.8830. Lengthening the window 365 →
   730 days moves coverage by 0.0002. `WORKLOG_POLICY_AND_ACCEPTANCE.md` §6.
6. **Evidence integrity.** 139 of 821 days carried no record of a sale or a closure and were counted
   as zero demand; rates now divide by observed days. The correction barely moves service *because
   the empirical buffer had been absorbing the bias* — which is a finding about robustness, not a
   disappointment. `PRESCRIPTIVE_CONTRACT.md`, "Evidence integrity".
7. **The inventory simulation.** The single most consequential omission. Real opening stock for 27%
   of scored SKUs; replenishing at all worth +26 points, the rule worth 0.6; the reorder-point proxy
   overstating the margin over naive **roughly fortyfold**; the apparatus worth 0.6 points at
   USTore's 4.2× cover and 4.4 points near one lead-time's cover; fill non-monotone in shelf depth
   as a real property of (s,Q) with lost sales. `INVENTORY_SIMULATION.md`.
8. **The rate window, reopened.** A lever closed as "second-order" against a comparison since
   corrected; the cascade removes its coverage cost entirely (0.8830 at every setting tested) and
   dominates the flat 365d baseline, but does not stack with the tiering — the two are substitutes.
   Default unchanged. `PRESCRIPTIVE_CONTRACT.md` §1a.
9. **The chain is broken at both joints.** Descriptive 266 SKUs → predictive **26 usable of 266
   (9.8%)** → prescriptive 266 scored, 208 priced. `Result_Forecast` is computed and read by nothing.
   `WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §7b.

Two further items are **measured but deliberately unwritten**, and the chapter names them as such
rather than adopting them:

- **The cold-start donor experiment** (`WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §2) — categorization
  scored as an analog source on the cold-start subset. The category label is worth **+0.17pp** of
  fill and product type is dominated by simply scaling the global donor. Recorded with the trap it
  exposes: acceptance condition 1b counts a SKU as covered if it is priced *at all*, so a donor model
  moves coverage 0.8830 → ~1.00 and flips the verdict to ACCEPTED while serving 13% of that demand —
  the same tautology already removed once.
- **The mis-specified threshold** (`WORKLOG_POLICY_AND_ACCEPTANCE.md` §6) — the planned correction is
  to measure coverage against the achievable ceiling rather than an absolute. **Not implemented.**
  The standard as committed reports NOT ACCEPTED and the chapter reports that.

---

## 8. One methodological thread the edit makes explicit

Four separate times this project has made a decision against a number that did not mean what it
appeared to mean, and each time the correction came from measurement rather than review:

| # | The number | What it appeared to mean | What it meant |
| ---: | --- | --- | --- |
| 1 | `MAPE ≤ 20%` | forecast quality | its optimum is a forecast of zero |
| 2 | trailing coverage = 1.0000 | the system prices the demand that arrives | an identity that cannot fail |
| 3 | reorder-point coverage, 26 points over naive | the apparatus is worth a great deal | a proxy overstating the margin ~40× |
| 4 | "the rate window is second-order" | a lever not worth pulling | ranked against #3, and roughly four times the corrected baseline |

Rows 1 and 2 are already in the chapter. Rows 3 and 4 are the new material. Stating them as one
sequence is the chapter's strongest methodological claim and costs no new measurement.

---

## 9. Consequences for `docs/chapter4.html`

The rendered chapter at `docs/chapter4.html` was consolidated **16 Sep 2026** and has **no
generator** — it is a hand-conversion of the draft, so it cannot be brought forward by re-running
anything. Its construct set is small and entirely mechanical (headings, paragraphs, pipe tables with
`***Table N.***` captions, italic source lines, blockquote notes, one fenced block, two list kinds,
one rule), so the resolution taken is to **write the generator** — `tools/render_chapter4.py` — and
regenerate from `docs/CHAPTER_4_DRAFT.md`, preserving the existing stylesheet verbatim. From this
point the HTML is derived from the draft rather than transcribed from it, and a stale HTML is a
command away from correct rather than a re-transcription away.
