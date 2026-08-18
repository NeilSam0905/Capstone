# USTore Capstone — Code Work Plan (v2, merged)

**Supersedes** `CODE_WORK_PLAN.md` and `REMEDIATION_STEPS.md`. Keeps the block structure and owner
split from the peer plan; folds in the audit items it dropped; rewrites Block 0 as a verification
checklist rather than a recency comparison.

**Context.** Chapters 1–3 are submitted and locked — **we do not edit the manuscript.** Where the
system diverges from Chapter 3, we build to Chapter 3 where we can and **explain the gap in
Chapter 4** where we can't. Every such gap goes in the Divergence Register at the end of this
document, as it is found. That register is what Chapter 4's limitations section gets written from.

**The repo may be stale.** Significant local work — the zero-fill that grew `Fact_Sales` to ~84,000
rows, the ADUS decision, the `is_hvl` fix, the Prophet re-run — may never have been committed. The
audit that produced these findings read the **repo**, so some findings describe a snapshot rather
than reality. Block 0 determines which.

---

## Block 0 — Secure, push, then verify which reality is real
**1 person · ~1.5 hours · DO FIRST, TODAY, IN THIS ORDER**

Order matters here. The original plan said "make the repo private" in Block 4 but "push everything
today" in Block 0 — which pushes months of client data to a public repo.

- [ ] **0.1 — Make the repo private FIRST.** It is currently public with a real client's supplier
      names, unit prices (to ₱1,400) and full sales volumes, under a data-sharing agreement.
      Settings → General → Danger Zone → Change visibility. Verify:
      `curl -s -o /dev/null -w "%{http_code}\n" https://github.com/NeilSam0905/Capstone` → `404`.
- [ ] **0.2 — Check the git *history*, not just the current tree.** Anyone who cloned while it was
      public has everything. Confirm what the data-sharing agreement permits.
- [ ] **0.3 — Now push the local work.** Even if messy, even on a branch. Months of work on one
      laptop is one disk failure from gone. This is the highest-value item in the whole plan.
- [ ] **0.4 — Tag the pre-remediation state:** `git tag pre-remediation-2026-07-28 && git push --tags`

### 0.5 — Verify, don't assume

"Newer" does not mean "correct." Two different classes of defect are in play and only one heals by
working locally:

| Class | Example | Likely local status |
|---|---|---|
| **Loud** — crashes or blocks | `DD/MM/YYYY` dates crashing `populate_dim_date.py` | **Probably fixed** — you can't have run the script without dealing with it |
| **Silent** — runs clean, wrong output | `TERM_STARTS` covering 3 of 12 terms; `is_tally_date` never populated | **Probably still broken** — the script prints a cheerful summary either way |

Run these four against the **local** `ustore.db`. This is the actual Block 0 deliverable:

```sql
-- 1. semester_week coverage.  512 = bug present.  ~1400 = fixed.
SELECT COUNT(*) FROM Dim_Date WHERE semester_week IS NOT NULL;

-- 2. Term-window overrun.  Above 30 = AY2526-ST still bleeding into AY2627-T1.
SELECT MAX(semester_week) FROM Dim_Date;

-- 3. is_tally_date.  0 = never populated.  411 = correct.
SELECT COUNT(*) FROM Dim_Date WHERE is_tally_date = 1;

-- 4. Unit conservation.  MUST be 88481, zero-filled or not.
SELECT SUM(quantity_sold) FROM Fact_Sales;
```

Query 4 is the one to keep permanently. Zero-filling adds rows with `quantity_sold = 0`, so the
total is unchanged — **~84,000 rows must still sum to 88,481.** If it doesn't, the zero-fill lost or
duplicated something and that has to be found before anything else proceeds.

- [ ] **0.6 — Record the four answers in this file.** Every later block is conditional on them.
- [ ] **0.7 — Declare the source of truth in the README** so no one loads the wrong fact table.

---

## Block 1 — Three blocking defects
**1 person · SEQUENTIAL · only for whichever defects Block 0.5 shows are still live**

- [ ] **1.1 — Dates → ISO 8601.** All four CSVs are `DD/MM/YYYY`. Repair with
      `pd.to_datetime(col, dayfirst=True, errors="raise")` then `.dt.strftime("%Y-%m-%d")`, or
      re-run the converters. **Use `errors="raise"`, never `errors="coerce"`** — coercion turns an
      unparseable date into a blank cell silently.
      *Verify:* every date column matches `\d{4}-\d{2}-\d{2}`; in `calendar_ranges.csv`,
      `end_date >= start_date` for all 135 rows (an inverted range means a date was read as `MM/DD`
      and the file is untrustworthy); unit totals still 88,481 in both sales CSVs.
- [ ] **1.2 — Team rule: these CSVs are edited by script only, never opened in Excel.** Commit the
      1.1 verification as `verify_data.py` and run it before every commit touching a CSV. Then break
      a copy deliberately and confirm the guard fails — a check that never fails is not a check.
- [ ] **1.3 — Get `populate_dim_date.py` running.** Currently dies at line 89 on
      `date.fromisoformat('09/01/2023')`. Expected after the fix: **1,461 rows**; flags
      enrollment 34, exam 173, event 54, sem_break 185, store_closed 43.
      The `is_tally_date 0` and `semester_week 512` lines are *expected failures* here — 1.4 fixes them.
- [ ] **1.4 — Fix `TERM_STARTS` and `is_tally_date`.** `TERM_STARTS` hardcodes 3 terms; the calendar
      has 12. Everything before 2025-08-07 gets a null `semester_week` — **42% of sales rows, 53% of
      tally dates.** Derive each term's window from `min(start_date)`/`max(end_date)` of its own
      ranges in `calendar_ranges.csv`. That also fixes the `AY2526-ST` → 2026-12-31 overrun.
      Populate `is_tally_date` from the 411 distinct sales dates.
      - **Decide and write down:** does week 1 start at the term's first *enrollment* range or its
        first *class* day? A naïve `min()` starts at enrollment. Enrollment is when the surge
        happens, so that's defensible — but it must be a stated choice matching whatever Chapter 4
        reports. → Divergence Register.
      - **Also fix:** `semester_id` is written by every overlapping range, so where flags overlap
        (Paskuhan inside finals) the **last CSV row wins**. Assign `semester_id` from the derived
        term windows and use `calendar_ranges.csv` only for the booleans. A regressor whose value
        depends on spreadsheet row order is not reproducible.
      *Verify:* `semester_id IS NOT NULL AND semester_week IS NULL` → 0; `MAX(semester_week)` in the
      18–26 range; `is_tally_date = 1` → 411; running the script twice produces an identical table.

---

## Block 2 — Pipeline correctness
**1 person · after Block 1**

- [ ] **2.1 🔴 Zero-fill breaks the sufficiency tiers — fix before Prophet runs again.**
      §3.3.2 and §3.3.4 route SKUs by **observation count**: ≥60 → standard Prophet, 30–59 →
      simplified settings, <30 → rolling average + "Insufficient Data for Forecasting" flag. On
      tally-date data that split was **87 / 56 / 162**.

      Zero-fill to ~84,000 rows and **every SKU clears 60 observations while carrying exactly the
      same information.** All 162 sparse SKUs get routed to full Prophet and the "Insufficient Data"
      flag never fires for anything — producing confident-looking forecasts for items with three
      real sales.

      **Fix:** count tiers on **distinct dates with a non-zero sale**, not on row count.
      *Verify:* the tier split still reads ≈ 87 / 56 / 162, not 305 / 0 / 0.
      → Divergence Register.
- [ ] **2.2 — Resolve the map-vs-allocate order.** `step1_apply_mapping.py` reads the
      *pre-allocation* file and states it does not allocate; `proportional_allocation.py` built the
      allocated table from the same source. Two branches, one input, and loading the wrong one
      corrupts everything downstream with no error.
      **Recommend map → allocate:** allocation weights each SKU by beginning-of-month inventory
      stock, which is a name-matching operation, so canonical names are the correct join key. The 18
      group labels and 45 variants matched verbatim on raw names by luck; that won't survive new data.
      `allocation_groups.csv` stores raw names in `generic_sales_name` and `inventory_variant` — both
      must be re-expressed as canonical and re-verified before re-running.
      *Verify:* units still **88,481**. Row and audit counts may legitimately shift if canonicalisation
      merged names — record that. A changed unit total is a bug.
- [ ] **2.3 — Confirm `is_hvl` made it into the committed schema.** The DDL's
      `CHECK (fsn_class IN ('F','S','N'))` raises `IntegrityError` on `'HVL'`, so FSN halts at the
      first HVL item. A separate `is_hvl INTEGER DEFAULT 0` is the right shape — §3.3.1 says HVL
      items are "recognized as Fast-moving (F)", so HVL is a *modifier on F*, not a fourth class,
      and `fsn_class` stays consistent with Objective 2's three categories.
      *Verify:* insert `fsn_class='F', is_hvl=1`, confirm success, delete the test row.
      → Divergence Register (Figure 5 shows no `is_hvl`).
- [ ] **2.4 — Stockout / censoring flags, before locking the model.** Dates where sales were zero
      because stock was *out*, not because demand was absent. Mostly harmless to the F/S boundary —
      ADUS already divides by tally dates *with a recorded sale* — but it can wrongly push a
      stocked-out item into **N**, and it biases 30-day forecasts and `σ_demand` (Safety Stock)
      downward. Interacts with 2.1: a zero-filled row and a censored row look identical unless flagged.
- [ ] **2.5 — Flag-frequency check (~10 min).** Count training rows where each calendar flag = 1.
      Expected finding on tally-date data: `is_store_closed` true in ~**2 of 411** rows — both
      probably the misdated 2025-06-12 / 2025-11-30 pair — making the coefficient unidentifiable, and
      `is_sem_break` systematically understating the effect it exists to capture, because §3.1.2
      dropped the near-zero observations that *define* break periods. This is a Chapter 4 finding in
      its own right: it explains the regressor failure as a data-representation consequence rather
      than a Prophet failure. If the zero-fill is in place, re-run it — the counts should improve, and
      **that improvement is the argument for the zero-fill.**
- [ ] **2.6 — Derived fields.** `daily_depletion_rate`, `cumulative_monthly_units` (both in the DDL
      and Figure 5) plus `days_of_supply` (§3.1.3). Depends on `is_tally_date` from 1.4. Four rules
      still need signing off: first-of-month handling, Sundays (14 of 411 tally dates), long-gap
      capping, month-boundary spanning. Key evidence: **55% of consecutive per-SKU observations are
      >1 day apart, max gap 67 days**, so a `÷1` denominator is wrong for historical data.
- [ ] **2.7 — Supplier normalisation.** 40 distinct strings for ~15 real suppliers
      (`NAPOLIZ`/`NAPOLIZ ENTERPRISES`; `JYL`/`JYL ATHLETICA`; `VARSITY LIFE STYLE`/`VARSITY LIFESTYLE`;
      `STITCH CORP.` ×5; `THREADMARKED` ×3), plus a bare `(Paid)` parser artefact.
      `Dim_Product.supplier_name` is a single `TEXT` column with nowhere for the
      `(CONSIGNMENT)`/`(PAID)` suffix — add `payment_status`. The batch sales report view needs this,
      since §3.2 says supplier grouping runs directly off the product dimension.
      *Verify:* `SELECT DISTINCT supplier_name FROM Dim_Product;` → ~15 rows, no `(Paid)` orphan.

---

## Block 3 — Inventory coverage
**1 person · runs alongside Blocks 1–2 · needs a GROUP decision**

Only **42 canonical items** appear in both sales and inventory — **13.7% of units sold** (12,107 of
88,481) have any stock record. Not a mapping artefact: canonicalisation *improves* overlap from 29
raw names to 42. The two source systems genuinely name different things.

- [ ] **3.1 — Export the 231 sales-only and 260 inventory-only canonical names.**
- [ ] **3.2 — Take both lists to USTore staff** (fold into Block 5). Some fraction is reconcilable by hand.
- [ ] **3.3 — Pick a position as a group:** (a) reconcile what staff can match and re-measure, or
      (b) scope the Stock Status view to the covered subset and state the coverage figure on the
      dashboard itself.
- [ ] **3.4 — Record the final coverage % as a headline Chapter 4 data-quality metric**, not a footnote.

**What this affects:** the Stock Status view (§1.5 designates it the *entry point* of the five-view
narrative), `days_of_supply`, §3.2's stated reason for omitting `Dim_Inventory` ("inventory level is
a derived calculation"), and Objective 4's promise to validate ROP/EOQ "against beginning inventory
counts." **Does not block** FSN, Prophet or EOQ. → Divergence Register.

---

## Block 4 — Forecasting position
**1 person + adviser · dropped from the original plan, and load-bearing**

- [ ] **4.1 — Talk to the adviser.** The ≤20% MAPE criterion is now permanent in eight places
      including Objective 3, so **Chapter 4 must report against a criterion the data provably cannot
      meet** — a harder writing job than editing it would have been, and not one to discover the
      adviser disagrees with after drafting. The conversation changed shape from "amend the
      objective" to "agree the framing." Bring:
      - The floor for a *perfect* forecast is ~**89% daily / ~60% monthly**, because bursts are
        **structural** (enrollment/event months genuinely 5–10× normal), not noise that averages out.
        Aggregation does not rescue it.
      - **Existing textual cover:** Table 4 row 2 already prescribes assessing MAE, RMSE and MAPE
        "collectively rather than a single threshold," contradicting §3.3.4's hard gate. The document
        already contains the more defensible position.
      - **Frame it as a result.** A rigorous eight-method benchmark showing the documented model
        loses to a rolling median, plus a proof the threshold is unreachable, is a stronger Chapter 4
        than a model that quietly hit its target.
      - Proposed reporting: **service level / fill rate ≥95%** as the headline (the actual business
        objective; safety stock exists to absorb forecast error), **MASE < 1.0** underneath as the
        model-selection metric.
- [ ] **4.2 🔴 Re-target to 30-day aggregate demand per SKU.** Every test so far predicted units on
      **already-known tally dates** — recording events, not demand events, and unknowable in
      production. **§3.3.3 needs "Average Daily Demand derived from the Prophet model's point
      forecast for the relevant 30-day future period" to compute ROP at all, so without this Phase 4
      has no valid input.**
      **Re-target the evaluation, not the training resolution** — keep fitting on tally-date
      observations, score on 30-day aggregates. Retargeting the training unit makes the natural
      observation a month, and ~26 months of data means no SKU can reach the ≥60 tier.
- [ ] **4.3 — Use `prophet_flatlog`, not vanilla Prophet.** `log1p` + `growth='flat'` + calendar
      regressors **without** `semester_week`. Vanilla Prophet produced a **554,605% worst-case MAPE**;
      flatlog collapses it to **422%** and beats naive on 34% of SKUs. PROJECT_LOG lists this as a
      locked decision. Note the conflict: §1.2 and §3.3.2 both name `semester_week` as a regressor.
      **Before dropping it, try cyclical encoding** (sin/cos or categorical week bins) — that fixes
      the extrapolation-across-resets problem while keeping the documented feature, which is the
      cheaper story for Chapter 4. → Divergence Register either way.
- [ ] **4.4 — Walk-forward validation is now a code obligation.** §3.3.4 promises "walk-forward
      validation" then describes a single 80/20 holdout; Figure 3 repeats it. Since the manuscript
      can't be edited, **the code has to deliver what it promises** — genuine rolling origins with
      multiple folds. A re-run under real walk-forward may also reshuffle the model ranking, which is
      why model choice shouldn't lock until after 4.2 and 4.4.
- [ ] **4.5 — Then lock Option C two-track routing:** `prophet_flatlog` for F/HVL, Croston/SBA for
      the intermittent S tier, rolling median or 30-day average + "Insufficient Data" flag for <30.
      Croston/SBA are not in §2.1.4. → Divergence Register.
- [ ] **4.6 — Report the benchmark as a Chapter 4 result.** Rolling median beat every method
      (MAE 2.24, MASE 0.61, 76% of SKUs beat naive); XGBoost placed 7th of 8. The rigorous benchmark
      *is* the contribution.

---

## Block 5 — Consolidated USTore visit
**1 person to organise · dropped from the original plan · blocks the whole prescriptive layer**

`lead_time_days` is **null for all 533 products**. Seeding provisional estimates (Phase 4 below) is a
sensible unblock, but the visit still has to happen. Nine items, one session:

- [ ] Lead time estimate → `Dim_Product.lead_time_days`
- [ ] Ordering cost per cycle + holding cost per unit → `Dim_Parameters` (EOQ inputs)
- [ ] The two closed-day tallies — 2025-06-12 (Independence Day), 2025-11-30 (Bonifacio Day). Open
      anyway, or misdated?
- [ ] **Sunday operating policy** — 14 of 411 tally dates are Sundays. Nothing in `Dim_Date` encodes
      an operating schedule, and the depletion denominator needs it → feeds ADUS → feeds FSN.
- [ ] `RE-CHECKING FEB 2025` — does it supersede `FEB 2025 - TBS`?
- [ ] Confirm the `CENTRAL SEMINARY` ↔ full-name supplier merge
- [ ] The 231 sales-only / 260 inventory-only lists (Block 3.2)
- [ ] Confirm the 40 → ~15 supplier mapping (Block 2.7)
- [ ] Flag that borderline FSN items will need a second session — §3.3.1 commits to cross-referencing
      them against management's experiential knowledge

*Verify:* `SELECT COUNT(*) FROM Dim_Product WHERE lead_time_days IS NULL;` → 0

---

## Block 6 — Repository hygiene
**1 person · remainder after Block 0**

- [ ] **6.1 — `README.md`** with the corrected run order: `create_schema.py` →
      `populate_dim_date.py` → `step1_apply_mapping.py` → `proportional_allocation.py` → fact load.
      `ustore.db` is gitignored, so run order is the only way a teammate can rebuild the database.
      Include the invariants table, the `verify_data.py` guard, the no-Excel rule, and which files are
      inputs vs generated outputs.
      *Verify:* a member who hasn't touched the pipeline rebuilds `ustore.db` from the README alone —
      `Dim_Date` 1,461 rows, `Fact_Sales` summing to 88,481. If they have to ask, it's incomplete.
- [ ] **6.2 — Commit the forecasting scripts and evidence.** `FORECASTING_OPTIONS.md`,
      `prophet_diagnostic.py`, `model_benchmark.py`, `prophet_diagnostic_results.csv`,
      `model_benchmark_results.csv`, `prophet_lever_test.csv`, plus `PROJECT_LOG.md` and
      `PROJECT_CONTEXT.md`. Chapter 4's central result is currently unreproducible from the repo, and
      a result no one can reproduce is not a defensible contribution.
- [ ] **6.3 — Normalise git identities** (`Netlopeds` / `Netlope` / `Neil Sam Perez`). Add a
      `.mailmap`. Contribution is usually a graded element.
      *Verify:* `git shortlog -sne` shows one entry per real person.
- [ ] **6.4 — Reconcile `USTore_Build_Plan.pdf`.** Good phased plan, but it predates the forecasting
      findings: still assumes "historical 2023–2026 tally records" (data starts 2024-05-02) and an
      unqualified Prophet path.

---

## Remaining pipeline

- [ ] **Load `Fact_Sales`** from the allocated + zero-filled data, if not already loaded in the
      source-of-truth DB. Join `product_id` on canonical name, `date_id` on ISO `calendar_date`; set
      `tally_date_flag = 1` for historical rows, `transaction_type = 'sale'`.
      *Verify:* `SUM(quantity_sold)` = 88,481; zero orphans on both `LEFT JOIN` checks against
      `Dim_Date` and `Dim_Product`. A non-zero orphan count is the date bug or a canonical-name
      mismatch resurfacing.
- [ ] **FSN classification** — 80th-percentile cutoff, 75/85 sensitivity, imputed rows weighted 0.5,
      per-SKU `entry_date` observation windows, HVL rule. Keep the borderline and sensitivity tables
      for Chapter 4. **Tier counts must come from Block 2.1's corrected definition.** Note the
      catalog is **533 canonical items** of which only **273 have sales** — the 260 inventory-only
      items would classify as N by default, so state whether they belong in the FSN denominator at all.
- [ ] **Prophet forecasting** — per Block 4: flatlog config, 30-day aggregate target, real
      walk-forward, Option C routing.
- [ ] **Phase 4 — ROP / Safety Stock / EOQ** — seed `Dim_Parameters` with provisional lead time and
      cost estimates, **clearly flagged provisional** (already covered by §1.4.2 constraints 6 and 9,
      so this one needs no Chapter 4 defence). FSN-differentiated z-scores (F = 1.65 / 95%,
      S = 1.04 / 85%, N excluded). EOQ sensitivity at 0.5× / 1× / 2×.
      **Note for Chapter 4:** §3.1.1 counts "the opportunity cost of capital tied up in stock" as
      holding cost, but under consignment the university doesn't own the stock — reframe `H` as
      shelf-space and handling cost, or present EOQ as an order-batching heuristic. → Divergence Register.
- [ ] **Power BI dashboard** — five views in narrative order; Stock Status scoped per Block 3;
      calendar-contextual interpretation cards; PDF export for Purchasing/Finance.

---

## Owner split

| Person | Owns | Interim work while blocked |
|---|---|---|
| **A** | Block 0 (secure → push → verify) → Block 1 (three defects, in order) | — starts immediately |
| **B** | Block 6 (README + commit evidence) → Block 2 (pipeline correctness) | — starts immediately |
| **C** | Block 3 (coverage) → Block 5 (organise the visit) → FSN + Prophet | — starts immediately |
| **D** | Phase 4 (ROP/EOQ) + Power BI dashboard | **Blocked behind C for both.** Meanwhile: seed `Dim_Parameters` structure, build the Power BI → SQLite connection, build the five-view shell with dummy data, draft the EOQ sensitivity calculation against synthetic inputs |
| **Whole group** | Block 3.3 — the inventory-coverage scope position | |
| **A or C + adviser** | Block 4.1 — the adviser conversation. **Start this week**; it gates 4.2–4.5 | |

**Today:** 0.1 private → 0.2 history check → 0.3 push → 0.5 the four queries.

---

## Divergence Register — for Chapter 4

Every place the system departs from Chapters 1–3. Add to this as you go; this is what the
limitations and results discussion get written from. Nothing here is a manuscript edit.

| # | Chapter 3 says | System does | Where to explain |
|---|---|---|---|
| 1 | Data scope 2023–2026 (§1.4.1, §3.1.1, Table 2) | Sales 2024-05-02 → 2026-06-30; inventory 2024-11-01 → 2026-04-01; 2023 = 6 undated batch aggregates, 1 of 34 labels matching a current SKU | Ch4 data description |
| 2 | No artificial daily interpolation of zero-sale values (§3.3.2); historical zeros treated as missing (§3.1.2) | Zero-filled to ~84,000 rows | Ch4 — **argue this**, it's plausibly why the regressors now work (Block 2.5) |
| 3 | Sufficiency tiers by observation count (§3.3.2, §3.3.4) | Counted on distinct non-zero sale dates — consequence of #2 | Ch4 method note |
| 4 | `is_suspension_day` (Figure 5) | `is_store_closed` — matches all prose and the DDL | Ch4 footnote |
| 5 | `fsn_class ∈ {F,S,N}` (§3.2, Figure 5) | Added `is_hvl` boolean; HVL is a modifier on F per §3.3.1 | Ch4 schema note |
| 6 | MAPE ≤20% primary acceptance criterion (8 locations) | Unreachable — perfect-forecast floor ~89% daily / ~60% monthly. Report service level ≥95% + MASE <1.0 | Ch4 — **the headline result** |
| 7 | Forecast units on tally dates | 30-day aggregate demand per SKU (what ROP/EOQ and the billing cycle need) | Ch4 method |
| 8 | `semester_week` as a Prophet regressor (§1.2, §3.3.2) | Dropped, or cyclically re-encoded — continuous form extrapolates badly across resets | Ch4 model spec |
| 9 | Prophet outperforms ARIMA on seasonal series (§2.1.4) | Rolling median beat all 8 methods (MAE 2.24, MASE 0.61); XGBoost 7th | Ch4 benchmark |
| 10 | Prophet applied blanket to F/HVL (§3.3.2); §2.1.4 lists no intermittent-demand methods | Option C two-track: flatlog for F/HVL, Croston/SBA for S | Ch4 model selection |
| 11 | Inventory omitted because stock is derived (§3.2); ROP validated against beginning counts (Obj 4) | 13.7% coverage — derivable for 42 of 273 selling items | Ch4 data quality + limitations |
| 12 | Walk-forward validation (§3.3.4, Figure 3) | Implement genuine rolling origins (Block 4.4) — or explain the holdout | Ch4 validation protocol |
| 13 | — | `semester_week` week-1 origin: enrollment vs first class day (Block 1.4) | Ch4 method note |
| 14 | Holding cost includes opportunity cost of capital tied up in stock (§3.1.1) | University doesn't own consignment stock — reframe `H` or present EOQ as order-batching | Ch4 EOQ discussion |
| 15 | — | Nearest-month allocation weights, some ±20 months stale; 312 of 2,852 splits used an equal split with no stock backing | Ch4 imputation limitations |
| 16 | Closure flags feed the depletion denominator | Two tally dates fall on flagged closures (2025-06-12, 2025-11-30) — pending Block 5 | Ch4 data quality |
| 17 | Benchmark tier counts | `PROJECT_LOG` says 89 SKUs in the ≥60 tier; tier counts elsewhere are 87/56/162 = 305. Reconcile before publishing either | Ch4 — fix, don't explain |

---

## One thing to confirm before accepting "locked"

The IS 26312 Gantt lists **"Post-Defense Revisions (10-day window)"** and **"Revised Document
Submission (PDF + Ring-bound)"** at **0%**. If that window is still open, four permanent errors are
still cheap to fix — well under an hour total:

- Two figures numbered **3** (IS 26316 Gantt p.21 and CRISP-DM p.42); everything after shifts by one
- **No Table 1** — numbering starts at Table 2
- §3.3.1 says "**The 20th percentile** cutoff" where the same paragraph correctly says "top 20
  percent" — it is the **80th**
- Figure 5's `is_suspension_day` → `is_store_closed`, and add `is_hvl`

Worth ten minutes to confirm the window's status rather than assuming it closed. If it is closed,
these move to the Divergence Register as footnotes instead.
