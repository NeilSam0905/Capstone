# Remediation Master Register — v2

**Supersedes v1** (2026-08-19). Adds S12–S14 from a database rebuild, and **revises S3, whose
original hypothesis the data does not support** — see the correction notice below.

**Reviewed:** `neil` @ `6221d87` (2026-08-18), on 2026-08-19.

**What was run, not read.** Full `pytest tests/` (**352 passed, 10 skipped**). `tools/service_frontier.py`
(23/23 gates pass). An anonymous `git clone` (it succeeded — R1). A Prophet install-and-fit from a
prebuilt wheel (O1). And — new in v2 — **a complete rebuild of `ustore.db` from the source workbooks
in `drive-download-.../`**, through `create_schema → populate_dim_date → step0 → step1 →
proportional_allocation → step2 → step3 → step5a → step5`, all gates passing. Every figure below with
a **✓** was measured against that database, not carried forward from an earlier document.

**Relationship to existing docs.** This is the action list. `DIVERGENCE_REGISTER.md` remains the
record of manuscript departures; several items here create new rows and say so.
`STATUS_AND_NEXT_STEPS.md` remains the record of human decisions; where an item overlaps a `BN`, it
says which. IDs (`D`/`O`/`C`/`S`/`R`) do not collide with `#N` or `BN`.

---

## Correction notice — S3 (v1) was wrong

v1 proposed that `is_store_closed` might encode *"holiday"* rather than *"closed"*, based on the
register's finding that 15 tally dates fall on flagged closures with month-day pairs repeating
annually against Philippine public holidays. I proposed splitting the flag.

**The database says otherwise.** ✓

```
flagged-closed dates that are also tally dates : 15
  of those, dates carrying ANY units sold      :  2   (2025-06-12: 72u, 2025-11-30: 71u)
  of those, dates carrying zero units          : 13
total units across all 15 dates                : 143  (0.16% of 89,232)
```

Thirteen of fifteen flagged-closed dates sold **nothing**, which is exactly what a genuine closure
looks like. And the two that did trade are precisely the two the *original* register entry named
before the rebuild.

**The real finding is different and more precise.** `populate_dim_date.py` sets `is_tally_date` from
the distinct dates in the **zero-inclusive** file (608 dates) rather than the positive-sales file
(411 dates) — a deliberate and well-argued choice, documented in its own docstring. The
consequence, not noted anywhere, is that a closed day acquires `is_tally_date = 1` merely because
zero-fill wrote rows for it. **The 15-date figure is an artifact of that definition, not evidence
the closure flag is backwards.**

So: do not split the flag. `is_store_closed` looks substantially correct. What remains is (a) two
dates that genuinely traded on a flagged closure, and (b) a second-order effect of the zero-fill
worth a Chapter 4 sentence, since it makes "tally date" and "a physical tally happened" diverge.
Revised proposal in S3 below.

I am recording this because the register's discipline is to document self-correction rather than
quietly amend. v1's S3 was a plausible hypothesis, checkable, and checked wrong.

---

## Part A — Defects

### D1 — safety-stock risk period is 7 days where the simulation needs 37
**Claude can fix** · **Objectives** 3, 4 · **Effort** ~1h incl. re-runs

`scripts/model_benchmark.py:85` sets `SERVICE_LEAD_TIME = 7`; line 399 computes
`z * sigma * np.sqrt(SERVICE_LEAD_TIME)`. The fold it scores is periodic-review, order-up-to: stock
set once, thirty days of demand arrive, no replenishment inside the window. The buffer must survive
**review + lead time = 37 days**. `z·σ·√L` is the continuous-review formula.

`step5_prescriptive.py` does **not** share this defect — its continuous-review ROP is correct and is
what §3.3.3 specifies.

**Fix.** Make the constant explain itself so nobody reverts it:

```python
SERVICE_LEAD_TIME     = 7    # days, supplier lead time [PROVISIONAL - Block 5]
SERVICE_REVIEW_PERIOD = 30   # days, the fold horizon: stock set once, no replenishment inside
SERVICE_RISK_PERIOD   = SERVICE_REVIEW_PERIOD + SERVICE_LEAD_TIME
```

**Effect ✓** (from `service_frontier.py`): scales safety stock by 2.2991. Fill rate
`ets` 0.7161 → 0.7775, `rolling_mean_30` 0.7098 → 0.7746, `tsb` 0.6862 → 0.7538.

**Blast radius, checked.** Benchmark CSVs rewritten. `test_degenerate_forecast.py` asserts orderings
not absolutes — should survive, verify. `test_benchmark_ranking.py` pins `n_skus`/`n_folds` —
unaffected. `service_frontier.py:80–82` hardcodes as-built pairs as gates → relabel `PRE_FIX_` and
**keep** them; they are the evidence the defect existed. Fill-rate columns in
`FORECAST_METHOD_COMPARISON.md` and `SERVICE_LEVEL_FRONTIER.md` become pre-fix figures — date them,
don't overwrite. The before/after *is* a Chapter 4 finding.

**Leaves open.** Whether periodic review is the right policy at all (§3.3.2 implies R=30, §3.3.3
specifies continuous). A Chapter 4 discussion, not a code change.

### D2 — re-running the ETL destroys every row the tallying interface wrote
**Claude can fix** · **Objectives** 1, 4, 5 · **Effort** ~30 min

`backend/app.py` `POST /api/tally` inserts into `Fact_Sales`; `step2_load_fact_sales.py:182` opens
`DELETE FROM Fact_Sales`. Undocumented anywhere. Objective 1's whole purpose is accumulation; an
accumulator a routine rebuild empties does not accumulate.

**Fix — one word.** The flag distinction already exists and is already correct on both sides:
step2 writes `tally_date_flag = 1` (historical), the backend writes `0` (interface).

```sql
DELETE FROM Fact_Sales WHERE tally_date_flag = 1
```

Plus a regression test that inserts an interface row, runs step 2, asserts survival.

### D3 — re-running the ETL destroys every staff-set closure
**Claude can fix** · **Objectives** 1, 2, 3 · **Effort** ~2h · **Creates a divergence row**

`PUT /api/calendar/<iso_date>/closure` writes `is_store_closed` in `Dim_Date`;
`populate_dim_date.py:176` opens `DELETE FROM Dim_Date`. `Event_Log` is safe — nothing deletes it.
That asymmetry is the fix.

**Fix.** Mirror the pattern §3.1.1 already specifies for events. Add `Closure_Log`
(`closure_date`, `reason`, `created_by`, `date_logged`); the toggle writes there;
`populate_dim_date.py` reads it after repopulating.

**This diverges from §3.1.1**, which says the toggle updates `Dim_Date` directly. File it and
explain it in Chapter 4 as the manuscript's own `Event_Log` design applied consistently.

---

## Part B — Objective-level risks

### O1 — Objective 3 names Prophet; Prophet has never run on the current data
**Claude can prepare, human must execute** · **Objectives** 3 · **Status** B5

You cannot report *"Prophet was outperformed"* without running Prophet. Chapter 4 can currently say
neither that it met the criterion nor that it lost. The benchmark does not substitute — the
objective names the library.

**The blocker is probably stale, and I verified this.** Prophet **1.4.0** publishes
`prophet-1.4.0-py3-none-win_amd64.whl` — platform-tagged, Python-version-agnostic, Stan binaries
**inside the wheel**; `requires_python >=3.10`, so 3.13.9 is in scope. I installed with
`--only-binary=:all:` and fitted an 80%-zero synthetic series: **no toolchain build**. ✓
(Verified on manylinux, not `win_amd64` — same packaging mechanism, but confirm on your machine.)

**Fix.** Pin `prophet==1.4.0`; install `--only-binary=:all:` so pip **fails loudly** instead of
falling back to the sdist — that fallback is what triggers the cmdstan build. Re-run `step4`, then
add Prophet as a ninth method in `model_benchmark.py` so it is scored on the same 266 SKUs / 3,192
folds. A result on a different protocol is not comparable.

**Note on §3.3.2 as written.** It specifies MCMC at 1,000 samples. MAP is seconds per SKU; MCMC is
minutes. Across 58 Fast SKUs that is a long unattended run. Budget it, or report MAP with a
divergence row.

### O2 — the acceptance criterion is structurally invalid, and its obvious replacement is too
**Claude can draft the one-pager; adviser must decide** · **Objectives** 3, 4 · **Status** B2 → B3

MAPE ≤ 20% is degenerate (#21): on an 81.2%-zero series the error-minimising forecast is zero, and
the MASE leader prices **0 of 266 SKUs**. Service level ≥ 95% is unreachable by 0.10pp *before
modelling* (#22): 584 folds / 103 SKUs have flat-zero training slices, making 2,732 units (5.1%)
structurally unservable — ceiling **0.9490**.

**Resolution.** Report a **service / holding-cost frontier with a recommended operating point at the
knee**, not a threshold.

**Frame it as delivering a commitment.** §1.2 already promises "an EOQ-based optimization model to
minimize the total inventory cost ... subject to a cycle service level constraint." The frontier
*is* that constrained optimisation.

**Hold the meeting after D1 and S1 land** — both change the numbers on the page.

### O3 — the dashboard does not exist; two of five views are blocked
**Human must build** · **Objectives** 5

| View | State |
|---|---|
| 1 Stock Status | Blocked on B10 (S7) |
| 2 FSN Classification | **Ready** — 58 F / 228 S / 233 N / 6 HVL ✓ |
| 3 Demand Forecast | Blocked on B3/B5 |
| 4 Restocking Advisory | **Buildable now** |
| 5 Batch Sales Report | **Ready** for units; **blocked on S12/S13 for pesos** |

**`Result_Forecast` and `Result_Forecast_Metrics` are not in the schema at all.** ✓ `sqlite_master`
returns seven tables: `Dim_Date`, `Dim_Parameters`, `Dim_Product`, `Event_Log`, `Exception_Log`,
`Fact_Sales`, `Result_Prescriptive`. Not unpopulated — absent. Creating and populating them is a
prerequisite for view 3 **regardless of which method B3 picks**, so it can start now.

Power BI has no native SQLite connector. Add an explicit "export result tables to CSV" pipeline step
rather than depending on an ODBC driver being present on a defence machine.

**PDF export** is named in Objective 5 and §1.4.1 and is still unimplemented. Decide now: Power BI
native, or the React screen.

### O4 — the Digital Tallying Interface has no durable write path
**Covered by D2 + D3** · **Objectives** 1

**BIR compliance — verified, and worth asserting in Chapter 4 rather than leaving to inference.** ✓
`TallyInterface.jsx` totals **units only**. `backend/app.py`'s docstring forbids checkout, payment,
customer-total and receipt endpoints, and none exist across its 20 routes. The only peso arithmetic
is `/api/reports/batch` — supplier compensation, which §1.1 requires. Correct side of the line.

Also open: no auth (`created_by` hardcoded `'local'`), and `Overview.jsx`'s ROP KPI still reads
"Needs lead time & cost inputs" — false since Phase 4.

---

## Part C — Documentation contradictions

**Claude can fix all four.**

- **C1** — register row #6 proposes *"service level ≥95%"*, the exact criterion #22 disproves. Both
  committed, same branch, same directory. Rewrite #6 to name the frontier; keep the ≥95% proposal's
  rise and fall, it is part of the argument.
- **C2** — the register stops at #21 (last touched 2026-08-05). #22 landed 2026-08-18 carrying a
  "What this changes in the register" table for B2/B3/B5/B15 that was never folded in.
- **C3** — `STATUS_AND_NEXT_STEPS.md` is wrong in three places: says Phase 5 not started (backend
  shipped 08-10), says Phase 6 is "the next task" (embed landed 08-08/09), says Phase 4 is "not yet
  committed" (it was, same day).
- **C4** — #22's headline table cannot be reproduced from the repo. `service_frontier.py` is
  admirably honest about it: the doc says 0.673 → 0.818, knee 0.767; the script gives 0.645 → 0.794,
  knee 0.7416. Every qualitative claim reproduces; only absolutes differ. **Make the script
  authoritative**, rewrite the prose table, and note that q ≈ 0.80–0.85 holds under both
  methodologies — which is itself a robustness result.

---

## Part D — Substance and data quality

### S1 — decouple EOQ's annual demand from the forecast
**Claude can implement; team ratifies** · **Objectives** 4 · **Status** unblocks B3, B15

`step5_prescriptive.py:273`: `annual = forecast_30d * (365/30)`. A method forecasting zero yields
`D = 0` and no EOQ — which is why `n_skus_priced` reads 0 / 79 / 266 across methods, and why
`Result_Prescriptive` is 158 rows (79 SKUs × 2 scenarios). EOQ is batching economics; it is
insensitive to short-run forecast error.

**Measured on the rebuilt database ✓** (286 eligible F+S SKUs, series ending 2026-07-31):

| demand basis | SKUs with D > 0 |
|---|---:|
| forecast (current) | **79** |
| trailing 30d | 79 |
| trailing 90d | 141 |
| trailing 180d | 163 |
| **trailing 365d** | **208** |

**Fix.** A `--demand-basis forecast|trailing` switch, default `trailing`, window 365d. Objective 4
says "for each fast-moving inventory item"; at 79 the shortfall is an internal coupling, not a data
limit.

### S3 — *revised* — the closure flag is broadly sound; `is_tally_date` is the artifact
**Claude can implement; USTore confirms the residue** · **Objectives** 2, 3 · **Status** narrows B8

See the correction notice. 13 of 15 flagged-closed dates sold nothing ✓.

**Revised fix.** (1) Do **not** split `is_store_closed`. (2) Add a derived
`is_tally_date_positive` (dates with any `quantity_sold > 0`, 411 dates) alongside the existing
zero-inclusive definition (608), so any denominator can state which it means. (3) Take the two
genuinely-trading closure dates (2025-06-12, 2025-11-30) to USTore — that is the whole residue, 143
units, 0.16% of volume. (4) Write it up: the zero-fill's second-order effect on `is_tally_date` is a
good Chapter 4 paragraph and further evidence for #2's "argue this" framing.

### S4 — merge the four no-op price-suffix families; hold the four real ones
**Claude can implement half; USTore rules on the rest** · **Objectives** 2 · **Status** partial B6

`PRICE_SUFFIX_AUDIT.md` already split them. `Arch`, `Keychain`, `Lanyard`, `Long Sticker` have bare
rows at **0 units, class N** — vocabulary artifacts; merging moves nothing. `Eco Bag`, `ID Case`,
`New Tiger Plushie Big/Small` have bare rows carrying **real sales** — merging moves units and
changes the FSN split. Merge the first four now; re-run FSN and confirm the 80th-percentile cutoff
holds via the 75th/85th sensitivity Objective 2 already mandates.

### S5 — close the May 2024 provenance gap by ruling, not deferral
**Claude can draft; team ratifies** · **Status** B7

The DSR's three channels (retail 3,719 + discounted 551 + special 48) sum to **4,318**, exactly the
old CSV's May total. TBS over the same 23 dates: **4,022**. The old 88,481 was a *mixed-provenance*
series. **Ruling:** adopt TBS as canonical (already what 89,232 reflects) and report the 296 as
**a lower bound on channel-level under-recording** — the DSR captures discount channels the TBS does
not. That turns an inconsistency into a quantified data-quality claim.

### S6 — state the real data window
**Claude can draft** · **Status** #1

§1.4.1 / §3.1.1 / Table 2 say 2023–2026. Sales run **2024-05-02 → 2026-07-31**; inventory
2024-11-01 → 2026-04-01; 2023 is 6 undated batch aggregates with 1 of 34 labels matching a current
SKU. State the analysable window exactly and present the exclusion as a documented decision with its
evidence. An unexplained scope gap reads as carelessness; a measured one reads as rigour.

### S7 — put inventory coverage on the dashboard, not behind it
**Human decision** · **Objectives** 4, 5 · **Status** B10

Stock coverage is **16.8% of rows** ✓ (14,160 of 84,399). §1.4.1 already says "a real-time
**estimated** stock status view" — the manuscript's own hedge gives the cover to show an estimate
with its coverage attached. Silently omitting uncovered SKUs is the only indefensible option.

### S8 — keep the ordering-cost ambiguity visible
**Already correct; USTore confirms** · **Objectives** 4 · **Status** B9

Lead time from a keyword classifier (jacket 28d / embroidered 18d / shirt 14d / else 18d; tiers
122/23/26/348). Holding cost derived: 0.25 × ₱210,000 ÷ 36,051 = **₱1.4563/unit/year**. Ordering
cost priced under both ₱1,250 and ₱200,000 — EOQ swings exactly **12.65× = √(200,000/1,250)**. Keep
the dual presentation; the swing is the finding. Weakest half: **348 of 519 products fall to the
18-day default, 204 uncategorised** — take a category list to the site visit.

### S9 — standardise the demand anchor
**Team decision** · **Status** B14

Resolves largely into S1: trailing 365d, stated, with 30/90/180 reported as sensitivity ✓.
Anchoring annual demand on a summer-term month and multiplying by 12.17 will be asked about.

### S10 — commit the two rejected methods
**Claude can run** · **Objectives** 3

EWMA and rolling-q75 exist only in a scratch run; the committed CSVs hold eight methods. The q75
result is the valuable one — it prices **zero** SKUs, because a 75th percentile is zero whenever a
quarter of a trailing window is zero-sale days. A third independent route to #21/#22. Re-run without
`--out` **at the same time as D1** so the benchmark regenerates once.

### S11 — do not extend zero-filling
**Standing constraint** · **Status** #2, #3

84,399 rows: 68,541 zero, 15,858 positive, 89,232 units ✓. Already handled correctly — `step4`'s
tiers key on **distinct sale-days**, not row count (92/51/123 = 266 all moving; 38/10/10 = 58 Fast).
Keep `tools/tier_counts.py` asserting it.

### S12 — ★ price coverage: `unit_price_php` has one source and it is the sparse one
**Claude can implement** · **Objectives** 4, 5 · **NEW in v2**

`step1_apply_mapping.py:131` sources `unit_price_php` from `inv_price.get(item)` — the inventory
sheets, and **only** the inventory sheets. It therefore inherits the inventory coverage gap wholesale.

**Measured ✓**

| | |
|---|---|
| Products with no price | **239 of 519** |
| Units on unpriced products | **73,428 of 89,232 (82.3%)** |
| **Fast** SKUs with no price | **48 of 58** |
| Supplier attribution (for contrast) | only 3,950 units (4.4%) unattributed — **not** the problem |

Objective 5 and §1.1 both name the batch sales report as the Finance Department's deliverable. It can
currently compute peso amounts for **17.7% of units**.

**The price is sitting in the item names.** 71 products carry an `@NNN` suffix; **64 of them have
`unit_price_php = NULL`** ✓. `Lanyard @180` — the largest SKU in the dataset at 7,201 units — is one.

**Convention validated against the 7 products having both ✓**

```
Bamboo Notebook @150   150 = 150      Logo Sticker @25             20 ≠ 25
Bamboo Pen @60          60 =  60      Two-toned windbreaker @1700  1800 ≠ 1700
Kraft Notebook @130    130 = 130
Paper Bag BIG @130     130 = 130      → 5 of 7 agree exactly
Tiger Claw Keychain    375 = 375
```

Both disagreements look like price drift — the suffix is the price when the name was coined,
inventory is current. Informative, not disqualifying.

**Fix.** Parse `@NNN` as a **fallback** where inventory gives no price, and add a `price_source`
column (`inventory` | `name_suffix` | `NULL`) so the two are never silently conflated and the
7 conflicts stay visible.

**Measured effect ✓**

| | now | after |
|---|---:|---:|
| Units priced | 15,804 (17.7%) | **38,298 (42.9%)** |
| Products priced | 280 | **344** of 519 |
| **Fast** SKUs priced | 10 | **27** of 58 |

Per supplier, the batch report goes from unusable to usable for several: `TET AND DARS` 7% → **100%**,
`STITCH CORP.` 31% → **86%**, `MADEBYRUZ` 15% → **67%**, `THREADMARKED` 8% → **61%**, `USTORE`
22% → **49%**.

### S13 — ★ the May 2024 DSR is an unmined price list
**Claude can implement** · **Objectives** 5 · **NEW in v2**

§3.1.1 says the DSR carries unit prices; you use it only for provenance checking
(`tools/provenance_may2024.py`). I opened it ✓ — **26 daily sheets**, each with three price columns
per item: `RETAIL PRICE`, `DISCOUNTED PRICE`, `SPECIAL DISC. PRICE`. That is a full catalogue price
list as of May 2024.

**Fix.** Extract a `dsr_price_list.csv` and add it as `price_source = 'dsr_may2024'`, third in
precedence after inventory and name-suffix, with the extraction date recorded so staleness is
visible. It also supplies the discount tiers, which nothing else in the pipeline has.

This closes S5's loop too: the 296-unit gap *is* those discount channels. Same sheet, two findings.

### S14 — three smaller data facts worth stating rather than discovering at the defence
**Claude can draft** · **NEW in v2**

- **`transaction_type` is 100% `sale`** across all 84,399 rows ✓. Your risk register lists non-sale
  removals as Moderate/High with `transaction_type` as the mitigation. Correctly built, entirely
  untested — there is no historical instance to demonstrate it on. One sentence in Chapter 4.
- **218 of 519 products have no `category`** ✓. Affects grouping on the FSN view.
- **HVL is detected on a sixth of the catalogue.** §3.3.1 defines it via Stock Depletion Rate — >80%
  of *initial consignment stock* in 14 days — which needs beginning stock, present for 16.8% of
  rows. Six HVL items ✓ is plausibly six *detectable* ones. Same root cause as S7 and S12, and the
  same reason Objective 4's "validated against beginning inventory counts" covers a sixth of the
  catalogue.

**The through-line.** Inventory coverage is not one blocked dashboard view. It is the single
upstream constraint on **pricing (S12), HVL detection (S14), ROP validation (Obj 4), and the batch
report (Obj 5)**. B10 is filed as a scope decision for one view; it is larger than that — and the
price half is fixable from data already in the repository.

---

## Part E — Repository and process

- **R1 — the repository is public** and holds a real client's commercial data. I cloned it
  anonymously ✓. It contains five of USTore's actual workbooks (4.4 MB), 19 named suppliers with
  payment status, prices and volumes. **Human only.** Going private does not retract what is already
  cloned, and the workbooks remain in git history — a history rewrite would break every teammate's
  clone for a risk already live for weeks, which is disproportionate. Go private; stop adding client
  data. *The only item here whose consequence is not academic.*
- **R2 — everything lives on a personal branch.** `main` is **41 commits behind** `neil`, **0
  ahead**; `tyrone` is fully contained in `neil` ✓. Anyone cloning the default branch gets the
  pre-ETL state. **Human only.**
- **R3 — #22 is unpinned.** #21 has `test_degenerate_forecast.py`; #22 has nothing. **Claude can
  fix** — folded into C4.

---

# What I can deliver

Everything below I can write and verify against the rebuilt database in this session. Nothing here
needs a decision from you first, except where noted.

### Code patches
| # | Deliverable | Verifiable how |
|---|---|---|
| D1 | `SERVICE_RISK_PERIOD` in `model_benchmark.py` + relabelled `PRE_FIX_` gates | re-run benchmark + frontier gates |
| D2 | `DELETE ... WHERE tally_date_flag = 1` + regression test | new test asserts an interface row survives step 2 |
| D3 | `Closure_Log` table, ETL read-back, backend endpoint change | closure survives a `populate_dim_date` re-run |
| S1 | `--demand-basis forecast\|trailing` switch in `step5_prescriptive.py` | priced SKUs 79 → 208 |
| S12 | `@NNN` suffix fallback + `price_source` column in `step1_apply_mapping.py` | units priced 17.7% → 42.9% |
| S13 | `tools/extract_dsr_prices.py` → `data/dsr_price_list.csv` + third precedence tier | coverage delta measured |
| S3 | derived `is_tally_date_positive` (411) alongside the zero-inclusive definition (608) | both counts asserted |
| S4 | merge the 4 zero-unit suffix families in `vocab_mapping_FINAL_v5.csv` | FSN re-run, cutoff unmoved |
| S10 | re-run the benchmark committing all 10 methods | committed CSVs carry 10 rows |

### Tests
`tests/test_service_frontier.py` (R3/C4 — the 0.9490 ceiling, the knee, `rolling_mean_30`'s
dominance at q = 0.80); the D2 survival test; the S12 price-source test; S3's two tally-date counts.

### Documents
Register rows #22–#27 in the existing format; the rewritten row #6 (C1); a refreshed and re-dated
`STATUS_AND_NEXT_STEPS.md` (C3); `SERVICE_LEVEL_FRONTIER.md`'s reconciled Cause 3 table (C4); the
adviser one-pager for B2 (O2); the S5 ruling; the S6 data-window section; the S14 notes; and a
`PRICE_COVERAGE.md` writing up S12/S13 in the style of `DEGENERATE_FORECAST.md`.

### What I cannot do
Change repo visibility · merge or push branches · install anything on your machines · author a
`.pbix` · talk to your adviser or to USTore · make the team's decisions for you.

---

# What you need to do

Ordered. One structural rule drives it: **fix the numbers before writing the story about them.** D1,
S1 and S12 change figures that C1–C4 and the adviser one-pager quote.

## Now — today, before anything else
1. **Make the repository private.** Settings → General → Danger Zone. Five minutes. R1 is the only
   item with a consequence outside the project.
2. **Merge `neil` → `main`,** or repoint the default branch. R2.

## This week — no decisions needed, just execution
3. **Confirm Prophet installs from a wheel on your Windows machine:**
   `pip install --only-binary=:all: prophet==1.4.0`. If it succeeds, B5 is dead and Objective 3 is
   back. If it fails, send me the error. *(O1)*
4. **Apply the Wave-1 patches** (D1, D2, D3, S12, S10) and re-run the benchmark **once**, not twice.
5. **Re-run `step4_forecast_model.py`** if 3 succeeded, then add Prophet as method 9.
6. **Start Power BI views 2, 4 and 5.** None of them is blocked. Build the "export result tables to
   CSV" step rather than chasing an ODBC driver. *(O3)*

## This week — four small team calls, evidence already gathered
7. **S1** — ratify trailing-365d as the EOQ demand basis. You will be looking at 79 vs 208.
8. **S5** — ratify TBS 4,022 as canonical and the 296 as channel under-recording. Closes B7.
9. **S9** — standardise the demand anchor. Follows S1; closes B14.
10. **S12/S13** — agree the price-source precedence (inventory → name-suffix → DSR) and that the 7
    conflicts stay visible rather than being silently overwritten.
11. **O3** — decide PDF export: Power BI native, or the React screen. It is named in Objective 5.

## Next — outside the team
12. **Book the adviser (B2/O2).** Hold it *after* items 4 and 7. One page: the theoretical floor,
    the 0.9490 ceiling, the frontier table, one recommended operating point. Ask for sign-off on the
    **object** — a frontier plus a recommended point — not on a number.
13. **Batch one USTore site visit** covering everything at once:
    - lead times (348 defaults, 204 uncategorised) and the two cost parameters *(S8/B9)*
    - the 4 price-suffix families with real sales *(S4/B6)*
    - the two closure dates that traded: 2025-06-12, 2025-11-30 *(S3/B8)*
    - inventory coverage and the Stock Status view's scope *(S7/B10)*
    - whether the DSR discount tiers still apply in 2026 *(S13)*
14. **Then B3** — select the forecasting method, once B2 has landed and EOQ coverage no longer
    depends on it.

## Then — the remaining build
15. Create and populate `Result_Forecast` / `Result_Forecast_Metrics`. **Not in the schema at all.**
    Prerequisite for dashboard view 3 regardless of B3 — can start before item 14.
16. Power BI views 1 and 3, after items 13 and 14.
17. Fix `Overview.jsx`'s stale ROP KPI. One-line copy fix that currently contradicts `Reorder.jsx`.
18. Write the Chapter 4 sections: the data window (S6), price coverage (S12/S13), the zero-fill's
    effect on `is_tally_date` (S3), the untested `transaction_type` (S14).

## Explicitly not doing
Stated so scope does not creep before the defence.

- **Not** rewriting git history to purge the workbooks. Disproportionate; breaks every clone.
- **Not** changing `Z_BY_CLASS = {"F": 1.65, "S": 1.04}`. Those values are named in §3.3.3 and
  Objective 4. The empirical quantile belongs in the benchmark's *scoring*; changing prescriptive
  defaults waits on the adviser.
- **Not** splitting `is_store_closed`. The data does not support v1's hypothesis — see the
  correction notice.
- **Not** re-litigating the zero-fill. Register row #2, flagged "argue this."
- **Not** switching the prescriptive layer to periodic review. §3.3.3 specifies continuous and
  `step5_prescriptive.py` implements it correctly. A Chapter 4 discussion.
- **Not** adding backend auth. Single-machine capstone demo; note the limitation.

---

*Prepared 2026-08-19 against `neil` @ `6221d87`, with `ustore.db` rebuilt from source workbooks.
Figures marked ✓ were measured against that database. Where a figure could not be reproduced, that
is stated (C4). Where an earlier recommendation was wrong, that is stated (S3).*
