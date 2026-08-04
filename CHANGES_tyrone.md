# CHANGES — branch `tyrone`

| | |
|---|---|
| **Base** | `NeilSam0905/Capstone` @ `neil`, commit `1510a61` |
| **Branch** | `tyrone` (local only — **not pushed**) |
| **Operator** | Tyrone Yazon <tyronegryneth.yazon.cics@ust.edu.ph> |
| **Date** | 2026-08-04 |
| **Model / effort** | Claude Opus 5, effort `high` |
| **Environment** | Windows 11, Anaconda Python 3.13.9. pandas 2.3.3, numpy 2.3.5, scipy 1.16.3, openpyxl 3.1.5, pytest 8.4.2 — all already present, no `pip install` needed and no venv created |

---

## 1. What this branch is for

It closes the gap between what the manuscript promises and what the repo can demonstrate, without
touching anything that requires a human decision. Every task here ends in an assertion against a
value that was written down before the run started, so the whole branch either reproduces the
contract or reports exactly where it fails. Work that could only be judged by a person — which model
to select, whether to merge price-suffixed SKUs, what the real lead time is — was deliberately **not
attempted**; it was made precise instead and is recorded in the Part B register below.

The single most important result: **the full data contract reproduces from a from-scratch rebuild,
21/21 checks, including `SUM(quantity_sold) = 89,232`.**

---

## 2. Gate failures

**None.** No gate in Part A failed, and no expected value was edited.

Three things did go wrong during the run and were fixed properly rather than worked around. They are
not gate failures — every one of them was caught by a check doing its job — but they are the part of
this document worth your attention.

### 2.1 `USTore_Build_Plan.pdf` was corrupted in every Windows checkout

Found by A1. With no `.gitattributes`, git classified the PDF as text, so `core.autocrlf=true`
inflated it 13,058 → 13,220 bytes on checkout. That shifted every absolute byte offset in its xref
table — `startxref 12442` no longer landed on `xref`, and the file would not parse.

Worse, `git add --renormalize .` then staged the corrupted version, because `.gitattributes` had by
then marked it binary and renormalise took the working-tree bytes verbatim. It was one commit away
from being committed as the new canonical PDF.

Restored byte-exact from HEAD and marked binary. Verified afterwards by parsing all 4 pages. The
five `.xlsx` workbooks were auto-detected as binary and were never affected.

### 2.2 Croston and SBA were being scored on a broken initialisation

Found by A9. Both scored MASE ≈ 42 against rolling median's ≈ 4.8, which is implausible for methods
designed for intermittent demand. Diagnosis on the worst SKUs: `NB @100` has 4 sales, all in the
first ~8 days of a 600-day window, and Croston forecast **137 units/day against a true mean of
0.44**.

Two causes, and only one was a bug:

- **Fixed.** Classical initialisation anchors the size estimate to the *first* demand; at α = 0.1
  with few demands it never moves (`z_hat` = 190 against a mean size of 66). Default is now
  `init="mean"`. MASE 42.3 → 12.5.
- **Not fixed, because it is the method.** Croston updates only on periods when demand *arrives*, so
  trailing zeros are structurally invisible and a dead SKU keeps forecasting its old rate forever.
  Pinned by `test_croston_cannot_see_trailing_zeros`: appending 500 zero days changes nothing. The
  known remedy is TSB, not implemented.

Croston/SBA still place last. That is now a finding rather than an artifact.

### 2.3 Four gates passed against an empty table

Found by A10, and this one is the most instructive. The first run of `step5_prescriptive.py` priced
**0 SKUs** and cheerfully printed four `[PASS]` lines, because "no N-class rows" and "EOQ is the cost
minimum" are trivially true of zero rows.

The cause was itself a finding (§4.1 below). The fix was a non-emptiness gate that runs *first* and
short-circuits the rest, so the others can never again pass vacuously.

---

## 3. Deviations from the run guide

Small, deliberate, and each one is a place the guide's instruction and the guide's own gate
disagreed.

| Where | Guide said | Done instead | Why |
|---|---|---|---|
| A0 | `.mailmap` line 2 maps `mrproatmcorentiixm@gmail.com` to the name "Neil Sam Perez" but keeps that address | Canonicalised **both** addresses to `pneilsam@gmail.com` | As written it produced *two* shortlog entries for Neil, failing the task's own gate ("one entry per person"). Now 17 commits, one entry |
| A0 | `python -m venv .venv && pip install ...` | Used the existing Anaconda base directly | Every required package was already present at a satisfactory version. No network fetch, nothing installed, nothing polluted |
| A1 | `git add --renormalize .` | Did that, then **did not** force-refresh the working tree to LF | The index is LF (that is what a push carries), `.gitattributes` is committed so fresh clones get LF, and `git status` is clean. Refreshing this checkout's stale CRLF was cosmetic and would have needed a destructive re-checkout. Explicitly declined by the operator mid-run |
| A6 | — | Added `conftest.py` | Bare `pytest tests/` — the exact form in the Part C checklist — failed collection with `No module named 'forecasting'`. `python -m pytest` masked it |
| A9 | ETS / Holt-Winters | Implemented in `forecasting/baselines.py` rather than imported from statsmodels | statsmodels is installed here but is **not** in the declared dependency set. The whole point of A9 is reproducibility from a clean clone; silently adding a dependency would defeat it |
| A10 | — | `assert_invariants.py` gained `--phase` | Seeding `Dim_Parameters` is a deliberate state change that breaks the baseline `Dim_Parameters = 0` invariant. See §6 |

---

## 4. Findings the run produced

These are new. They are not in the guide and were not expected.

### 4.1 The best-scoring forecast cannot drive the prescriptive math

The rolling 30-day **median** leads the seven-method benchmark on MASE. It also prices **0 of 266
SKUs**, because on an intermittent daily series the trailing median is zero — so annualised demand is
zero and there is no EOQ to compute.

The method that minimises forecast error predicts "nothing will sell". That is nearly right day to
day and useless for deciding how much to order.

This is the accuracy/actionability split, and it lands directly on **B3**: a model selected on MASE
alone would be unusable for the thing the system exists to do. `step5_prescriptive.py` defaults to
the rolling mean and records the demand method in every row.

### 4.2 The 30-day demand basis lands inside a semester break

An annualised **30-day** forecast anchors on 2026-07, which is inside the AY2526 summer term. Most
SKUs sell nothing then:

| Trailing window | SKUs with positive D |
|---|---:|
| **30 days** (the spec) | **79** / 266 |
| 90 days | 141 / 266 |
| 180 days | 163 / 266 |
| 365 days | 208 / 266 |

Widening the basis is a Block 5 decision. It is now measured rather than implicit.

### 4.3 MAE and MASE disagree about which method wins

`rolling_mean_30` leads on MAE; `rolling_median_30` leads on MASE. Not a bug — MAE is dominated by
high-volume SKUs, MASE scales each SKU by its own variability first. Which one matters is a
question about what the store cares about, and it is part of **B3**.

### 4.4 `is_store_closed` looks like it may mean "holiday"

15 tally dates fall on flagged closures (not 2 — see Divergence #16), and **the month-day pairs
repeat annually**: `01-09`, `02-25`, `04-09`, `06-12`, `06-24` all appear in both 2025 and 2026.
Those are Philippine public holidays. The store appears to trade on them anyway.

If the flag means "holiday" rather than "closed", it is the wrong denominator wherever it gates a
depletion calculation. **Recorded, not acted on.** This is **B8**.

---

## 5. The contract — verified on rebuild

Re-derived from a from-scratch rebuild on 2026-08-04. `python tools/assert_invariants.py` prints
one line per row and exits 0. **All 21 passed.**

| Invariant | Value | ✓ |
|---|---:|:-:|
| `Dim_Date` rows | 1,461 | ✓ |
| `semester_week` non-null | 1,453 | ✓ |
| `MAX(semester_week)` | 23 | ✓ |
| `is_tally_date = 1` | 608 | ✓ |
| `Fact_Sales` rows | 84,399 | ✓ |
| `SUM(quantity_sold)` | **89,232** | ✓ |
| zero-quantity rows | 68,541 | ✓ |
| `Dim_Product` rows | 519 | ✓ |
| FSN split (F / S / N) | 58 / 228 / 233 | ✓ |
| `is_hvl = 1` | 6 | ✓ |
| Orphan joins to `Dim_Date` / `Dim_Product` | 0 / 0 | ✓ |
| Products with ≥1 `Fact_Sales` row | 286 | ✓ |
| Products with >0 total units | 266 | ✓ |
| Sales date span | 2024-05-02 → 2026-07-31 | ✓ |
| `Dim_Parameters` / `Event_Log` / `Exception_Log` rows | 0 / 0 / 0 | ✓ |

`Exception_Log` is created by `step2_load_fact_sales.py`, not `create_schema.py` — its existence is
now asserted, since a rebuild that skipped step2 would otherwise pass.

---

## 6. ⚠ Running the invariant gate after `step5_prescriptive.py`

A10 seeds `Dim_Parameters` with 16 provisional grid-definition rows. That **deliberately** breaks the
baseline `Dim_Parameters = 0` invariant. After step5 has run:

```bash
python tools/assert_invariants.py --phase a10     # 22/22, exit 0
```

Running it bare after step5 exits 1 and prints a note naming the flag. This is expected, not a
regression. To re-check the true baseline, rebuild the database from scratch.

---

## 7. Files

### New

| File | Why |
|---|---|
| `tools/assert_invariants.py` | The full contract as a regression gate. Run after anything that could move a number |
| `tools/provenance_may2024.py` | Reproduces 88,481 → 89,232 and locks the decomposition |
| `tools/audit_price_suffix_skus.py` | Measures the 71 price-suffixed SKUs. Changes nothing |
| `tools/tier_counts.py` | Closes Divergence #17 |
| `forecasting/metrics.py` | MAE, RMSE, MAPE (undefined cases counted), MASE, fill rate, cycle service level |
| `forecasting/evaluate.py` | Walk-forward harness: rolling origins, ≥3 folds, 30-day aggregate scoring |
| `forecasting/intermittent.py` | Croston and SBA with the size/interval decomposition exposed |
| `forecasting/baselines.py` | naive, seasonal naive, rolling mean/median, Holt-Winters |
| `model_benchmark.py` | Seven methods on identical folds, no Prophet |
| `step5_prescriptive.py` | ROP / Safety Stock / EOQ across a 5×5 grid |
| `tests/` (3 files, 232 tests) | Property tests. The leakage tests in `test_evaluate.py` are the ones that matter |
| `conftest.py` | Puts the repo root on `sys.path` so bare `pytest tests/` works |
| `docs/DIVERGENCE_REGISTER.md` | The register, promoted out of `CODE_WORK_PLAN_v2.md`, corrected and extended to 20 rows |
| `docs/BUILD_PLAN_RECONCILIATION.md` | The delta against `USTore_Build_Plan.pdf` |
| `docs/DATA_PROVENANCE.md`, `docs/may2024_dsr_vs_tbs.csv` | A3 outputs |
| `docs/PRICE_SUFFIX_AUDIT.md`, `docs/price_suffix_audit.csv` | A4 outputs |
| `model_benchmark_results.csv`, `model_benchmark_summary.csv` | A9 outputs |
| `.gitattributes`, `.mailmap`, `CLAUDE.md`, `.claude/settings.json` | A0/A1 policy |

### Modified

| File | Change |
|---|---|
| `proportional_allocation.py` | `lineterminator="\n"` on both `csv.writer` calls (≈ lines 188, 191) |
| `verify_data.py` | Added `verify_zerofill_decomposition()` — fails if any month other than 2024-05 and 2026-07 moves |
| `create_schema.py` | Added `Result_Prescriptive` |
| `USTore_Build_Plan.pdf` | Restored byte-exact from HEAD (see §2.1). **Content unchanged** |

### Deliberately untouched

`vocab_mapping_FINAL_v5.csv`, `allocation_groups.csv`, `supplier_mapping.csv`,
`calendar_ranges.csv`, every `Dim_Product.item_name`, every `fsn_class` and `is_hvl` value, and every
expected value in a verification script.

---

## 8. Part B — deferred decisions

**Making these precise is the deliverable. Resolving them is not.** Nothing below was attempted.

| # | Decision | What's known now | What's missing | Who decides | What unblocks |
|---|---|---|---|---|---|
| B1 | Make the repo private | Still public with supplier names, unit prices, sales volumes | Nothing — it is a setting | Repo owner | Removes a disclosure risk before the defence |
| B2 | The MAPE ≤20% framing | Provably unreachable; floor ≈89% daily / ≈60% monthly. Table 4 row 2 already prescribes assessing MAE/RMSE/MAPE collectively, contradicting §3.3.4's hard gate | Whether the adviser accepts replacing the gate | Adviser (Block 4.1) | **B3**. Nothing about model choice can be settled while the acceptance criterion is undefined |
| B3 | Which model to select | A9 ranks 7 methods on identical folds. Option C two-track routing is the proposal. §4.1 above shows the MASE leader cannot drive the prescriptive math | B2, and a decision on whether accuracy or actionability leads | Team, after B2 | Phase 3 sign-off; the Demand Forecast dashboard view |
| B4 | `semester_week`: drop or cyclically re-encode | §1.2 and §3.3.2 name it as a regressor; continuous form extrapolates badly across term resets; Block 1 took it from 512 → 1,453 non-null rows | A trial of sin/cos encoding | Team — try sin/cos first, cheaper Ch4 story | The Prophet regressor spec |
| B5 | Prophet / `prophet_flatlog` | Needs a cmdstan build. Vanilla Prophet hit 554,605% worst-case MAPE; flatlog collapses it to 422% and beats naive on 34% of SKUs. `step4` has **not** been re-run since Block 1 or Block 2.2 — stored forecasts are stale twice over | A working toolchain, and a re-run | Run in its own session | Divergence #9; whether Prophet appears in Ch4 at all |
| B6 | Price-suffix merge ruling | A4: 71 suffixed SKUs, 12 twins in 8 families. **4 families have a 0-unit N-class bare row (artifact, merging moves nothing); 4 carry real sales (merging moves units and changes the FSN split)** | Per-family confirmation that the labels mean one product or two | USTore staff | Only the second group of 4 is blocking |
| B7 | May 2024 DSR vs TBS 296-unit gap | A3 proves TBS = 4,022 is faithful and that the DSR's channels sum to exactly 4,318. Leading hypothesis: the DSR's separate discounted-price column (551 units) and special-discount column (48) | Which figure the store considers the true May 2024 total | USTore staff | Closes Divergence #18 |
| B8 | `is_store_closed` = closed or holiday? | 15 tally dates fall on flagged closures; month-day pairs repeat annually and match Philippine public holidays (§4.4) | What the flag was intended to mean | USTore staff | The depletion-rate denominator; Divergence #16 |
| B9 | Lead time, ordering cost, holding cost | `lead_time_days` NULL for all 519. A10 gives the full surface across a 5×5 grid | Three numbers from the store | USTore staff — then read the value off `Result_Prescriptive` | Phase 4 becomes a lookup, not a recompute |
| B10 | Inventory coverage position | 76 vs 82 items in both sales and inventory depending on definition; 16.8% of `Fact_Sales` rows | An agreed definition, or a scoped claim | Whole group (Block 3.3) | The Stock Status dashboard view |
| B11 | Holding cost under consignment | §3.1.1 counts opportunity cost of capital; the university doesn't own consignment stock | A reframing of `H` | Team | Whether EOQ is presented as cost-optimal or as order-batching |
| B12 | The four attribution commits on `neil` | Removing them rewrites every SHA from `9c3c9c8` forward; `tyrone` would share zero history with `neil` | A group decision, taken once | Whole group, on `neil` — **recommendation: leave them** | Nothing. Cost of acting exceeds cost of not |
| B13 | Post-defense revision window | If open: two Figure 3s, missing Table 1, §3.3.1's "20th percentile" → 80th, Figure 5's `is_suspension_day` → `is_store_closed` + `is_hvl` | Whether the window is still open | Programme coordinator — confirm status, don't assume | Four permanent errors, under an hour to fix |

---

## 9. Verification — the Part C checklist

Run from the repo root. `python` must be a 3.13 interpreter with pandas/numpy/scipy/openpyxl/pytest —
on this machine that is `C:\Users\Ty\anaconda3\python.exe`, which is **not** on `PATH`.

```bash
python tools/assert_invariants.py --phase a10   # 22/22, exit 0   (see §6)
python verify_data.py                           # exit 0, incl. the A3 decomposition assert
python tools/provenance_may2024.py              # 11 checks; TBS 4,022; DSR 4,318; net +751
python tools/tier_counts.py                     # 92/51/123 all-moving; 38/10/10 Fast-only
python tools/audit_price_suffix_skus.py         # 71 suffixed, 12 twins, 8 families
pytest tests/                                   # 232 passed
python model_benchmark.py                       # 7 methods, identical folds (~5 min)
python step5_prescriptive.py                    # 1,975 rows, all gates pass

git status --porcelain                          # empty — the A1 gate
git log neil..tyrone --format='%B' | grep -Ei 'co-authored-by|claude-session|generated with'
                                                # returns nothing
git shortlog -sne HEAD                          # one entry per real person
```

> **Note the `HEAD`.** Bare `git shortlog -sne` reads from stdin when it is not attached to a
> terminal, so in a script or a piped shell it silently prints nothing and looks like a pass.
> Expected output:
>
> ```
>     17  Neil Sam Perez <pneilsam@gmail.com>
>     12  Tyrone Yazon <tyronegryneth.yazon.cics@ust.edu.ph>
> ```

To re-verify the **baseline** contract (`Dim_Parameters = 0`), rebuild first:

```bash
rm ustore.db
python create_schema.py && python populate_dim_date.py && python step1_apply_mapping.py \
  && python proportional_allocation.py && python step2_load_fact_sales.py \
  && python step3_fsn_classification.py
python tools/assert_invariants.py               # 21/21, exit 0
```

---

## 10. Commit log

| Task | SHA | Message |
|---|---|---|
| A0 | `cfee0fa` | chore: branch setup, attribution policy, .mailmap |
| A1 | `5058856` | fix: normalise CSV line endings via .gitattributes, stop 199k-line phantom diffs |
| A2 | `515cb4b` | test: assert_invariants.py — the full contract as a regression gate |
| A3 | `dd51e96` | docs: reproduce and lock the May 2024 DSR→TBS re-sourcing |
| A4 | `e576467` | analysis: price-suffix SKU audit (measurement only, no vocabulary changes) |
| A5 | `7148530` | analysis: reconcile sufficiency-tier counts across three populations (Divergence #17) |
| A6 | `989af50` | feat: forecasting metrics with MASE and fill rate |
| A7 | `8df6b15` | feat: 30-day aggregate walk-forward evaluation harness (Block 4.2/4.4) |
| A8 | `756d0e2` | feat: Croston and SBA for intermittent demand (Block 4.5) |
| A8′ | `739f99b` | fix: Croston/SBA initialisation was dominated by the first demand |
| A9 | `4256241` | feat: seven-method benchmark, reproducible without Prophet (Block 4.6) |
| A10 | `f07c0dc` | feat: ROP/Safety Stock/EOQ as a parameter sensitivity surface (Phase 4) |
| A11 | `e5f8186` | docs: divergence register, build plan reconciliation, handoff record |

No commit carries an AI-attribution trailer.

### Closing verification

After the final commit, the database was deleted and the pipeline rebuilt from empty:

```
create_schema → populate_dim_date → step1_apply_mapping
              → proportional_allocation → step2_load_fact_sales
              → step3_fsn_classification
```

Result: **21/21 baseline contract checks passed**, and `git status --porcelain` was **empty** — no
modified CSVs after a full regeneration. That is the A1 fix and the whole data contract confirmed
together, from scratch, on a clean database.

---

## 11. Part C — pushing

Not done by this run, by design. `.claude/settings.json` forces a prompt on `git push`.

```bash
git push -u origin tyrone
```

`neil`, `main` and `marco` are untouched — `tyrone` is a new local branch and nothing else was
modified. Verify with `git branch -vv` before pushing.
