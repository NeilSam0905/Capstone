# Open issues

Technical problems that still need fixing. Moved out of the README so that file
stays readable; the resolved history is kept at the bottom rather than deleted.

**Team decisions** (B1–B15 — repo visibility, the acceptance criterion, model
selection, the site visit) live in `docs/STATUS_AND_NEXT_STEPS.md`. This file is
for engineering work, not decisions awaiting a human call.

---

## Open

### 1. 15 of 58 forecast SKUs return zero
The default model's inputs are empty for 15 Fast SKUs, so their forecast is a
flat zero line and `step5 --demand-basis forecast` prices only 43 SKUs (against
`trailing`'s 208) — a priced share of 0.1617, which fails the 0.78
non-degeneracy floor. This is `docs/DEGENERATE_FORECAST.md`'s argument appearing
in production rather than in a benchmark. It is the direct cost of switching to
the benchmarked trailing window, and it is a live decision, not a bug. See
`docs/ROLLING_MEAN_FORECAST.md` §4.

Re-measured 2026-09-30 against the rebuilt database. The figures above were 32
zeros and 26 priced under `rolling_mean_30`; the blend default
(`topdown_tsb+calendar+weekday_shape`) roughly halves the zeros without getting
near the floor, so the shape of the problem is unchanged.

### 2. No auth on the backend
`Event_Log.created_by` is hardcoded `'local'`. Fine for a single-machine
capstone demo, not for anything beyond that.

### 3. Inventory coverage is low
~14–17% of products have any stock count, which limits both the Stock Status
view and the Reorder screen's "on hand" column to a minority of SKUs. Block 3 /
B10, unresolved.

### 4. Everything Phase 4 produces is provisional
Lead time, holding cost, and both ordering-cost interpretations are estimates
pending the USTore site visit (Block 5 / B9). Don't treat `Result_Prescriptive`
as final.

### 5. Power BI (Phase 6) hasn't been built
The frontend has an embed placeholder (`PowerBIDashboard.jsx`) wired to
`VITE_POWERBI_EMBED_URL`, but no `.pbix` has been authored or published. Three
of the five views are buildable today; Stock Status is blocked on issue 3 above.
`docs/POWERBI_DASHBOARD_PLAN.md` has the chart-by-chart spec.

### 6. `create_schema.py` cannot repair an existing schema
It uses `CREATE TABLE IF NOT EXISTS` throughout, so a DDL change never reaches a
database that already exists — and SQLite cannot `ALTER` a CHECK constraint. This
bit once already: a widened `price_source` CHECK shipped in `fcd597d` never
landed, and `step1_apply_mapping.py` failed with `IntegrityError` on every run
until `ustore.db` was rebuilt from scratch. There is no migration path and no
schema-version marker. Until there is, **a schema change means a full rebuild**,
and that has to be remembered rather than enforced.

### 7. `step1_apply_mapping.py` needs `rawdata/`, and nothing says so
Two of its four price sources read the client workbooks directly: `may2024_dsr`
parses the 23 daily sheets of `rawdata/2024 5 MAY DSR & TBS.xlsx`, and
`tbs_item_price` parses every workbook's TBS sheet (both added in `fcd597d`).
Neither has a fallback, so without `rawdata/` step 1 raises `FileNotFoundError`
before it reaches `Dim_Product` — and it is **not** marked optional in
`backend/pipeline.py`, so the whole run stops there instead of skipping one step
the way step 0 does. Those two sources carry **157 of the 519 catalogue prices**
(121 `tbs_item_price`, 36 `may2024_dsr`), and `unit_price_php` feeds the EOQ and
both ordering-cost scenarios, so degrading them to NULL would not be neutral
either.

The workbooks themselves are **not lost** — see issue 11. Restored from
`origin/gambe` into `rawdata/` on 2026-09-30, the full pipeline runs from
`create_schema.py` to `step5_prescriptive.py` and reproduces the shipped
database exactly (`Result_Prescriptive` sha256 `da843d54…` over all 474 rows;
`data/USTore_sales_long_with_zeros.csv` byte-identical to the committed copy
once CRLF is normalised; every tracked result CSV clean in `git status`).

So this is a documentation-and-packaging defect, not a data loss: the README
said "Only step 0 reads them; every later step reads `data/*.csv` … so the
pipeline runs without them", which was true when written and is corrected in the
same pass as this entry. What is still missing is a `rawdata/` check at the top
of step 1 that names the four workbooks, and a decision about where they are
meant to live (issue 11).

### 8. `storage_category` is wiped by every `step1` run and never restored
`step1_apply_mapping.py:360` does `DELETE FROM Dim_Product` and then appends
`build_dim_product()`'s ten columns, which do not include `storage_category` or
`forecast_category` — so both go NULL on every run after the first.
`step1b_categorize_products.py:164` backfills `storage_category` only when the
column is **absent**, so it never heals a column that exists and is empty. From
the second pipeline run onward `forecasting.category.storage_category_sql()`
therefore COALESCEs to `category`, which `step1b` has just overwritten with the
semantic label — the exact collision `085e01f` was written to prevent, returning
one run later.

Verified end to end on 2026-09-30 by running the whole pipeline **twice** over a
from-scratch database — not simulated. After the second run `storage_category`
is NULL on all 519 rows and `step1b` does not restore it:

- `step5a_set_lead_times.py` — `lead_time_days` does **not** move (122 at 14d,
  371 at 18d, 26 at 28d either way): `category` only separates two tiers that
  are both 18 days. But 144 SKUs move from `default (confirmed non-apparel)` to
  `default (uncategorized)`, so the tier label stops meaning what it says.
- `tools/cold_start_donor_test.py` — the `category` donor rule moves from fill
  **0.1412**, +0.0010 against the matched-stock control ("the label buys
  something") to fill **0.1384**, −0.0006 ("the label costs fill").
  **`docs/COLD_START_ANALOG.md`'s conclusion inverts.**
- `pytest tests/test_cold_start_donor.py` still passes. Those 12 tests are
  synthetic by design — "no ustore.db - so these pin the REASONING" — so nothing
  in the suite guards that pinned figure against a database-state regression.

**Everything that ships survives.** `Result_Prescriptive` is bit-identical after
the second run (same sha256), `pytest` is 517/517, invariants 22/22 and the
acceptance verdict unchanged. The damage is confined to the cold-start analysis,
and no gate anywhere catches it — which is what makes it worth fixing before
someone re-runs the pipeline and quotes the flipped number.

The fix is one line: make the backfill depend on the values rather than on the
column, `UPDATE Dim_Product SET storage_category = category WHERE
storage_category IS NULL`, run unconditionally before the `category` overwrite.
Not applied — it changes how `step1b` interacts with the categorisation Neil
owns, so it is his call.

### 9. `verify_rebuild_state.py`'s `model_type` check cannot pass on this tree
`scripts/verify_rebuild_state.py:184` asserts that the set of `model_type`
values in `Result_Forecast` equals `{step4.DEFAULT_MODEL}`. That holds on the
branch it was written on; here `step4b_policy_forecast.py` deliberately
publishes `policy_rate` rows **beside** the point forecast, so the set always
has two members and the check always fails — while its own detail line shows
step 4's default matching exactly. Per `CLAUDE.md` the assertion is reported,
not relaxed: whoever owns that script decides whether the check should read
`step4`'s rows only.

### 10. `scripts/vault.py`'s `cryptography` dependency is undeclared
It imports `cryptography.hazmat`, which appears in none of the four files under
`requirements/`. `python scripts/vault.py unlock` therefore fails with
`ModuleNotFoundError` on a clean clone even with the key in hand — the precise
failure mode `requirements/requirements.txt`'s own header says the file exists
to prevent ("`rapidfuzz` is in here precisely because it was such a case").

### 11. The client tally-sheet workbooks are still committed on `origin/gambe`
All five raw workbooks — supplier names, item prices, daily sales volumes — sit
at the tip of `origin/gambe` under `drive-download-20260724T120738Z-1-001/`:

| file | bytes |
|---|---:|
| `2024 5 MAY DSR & TBS.xlsx` | 2,005,665 |
| `2025 USTore TBS.xlsx` | 1,230,219 |
| `USTore TBS OCTOBER A.Y. 2025-2026.xlsx` | 991,543 |
| `USTore TBS AUG-DEC 2024.xlsx` | 262,851 |
| `2023 total sales by batch (3).xlsx` | 20,409 |

They were added in `6f26f24` (2026-07-27) and removed in `f7ebb62` (2026-08-19,
Remediation Wave 1), which is also where `.gitignore` gained `rawdata/`. `gambe`
branched on 2026-08-06, between those two commits, and was never updated — so
the removal never reached it and the files are still live there. No other
branch carries them (`main`, `tyrone`, `neil`, `marco` and `consolidate` all
have zero such paths).

Two consequences, pulling opposite ways:

- **This is why the rebuild works at all.** They are the only copy of the
  source data on this machine, and step 1 needs them (issue 7). Whatever is
  decided, they have to be preserved somewhere first — the vault would be the
  consistent home, since it already holds their *derived* outputs
  (`data/rebuild_*.csv`).
- **Everything else in this repo treats them as too sensitive to commit.**
  `.gitignore`, `f7ebb62` and the README all say so explicitly. Anyone with
  access to the repository can download them from that branch today. Whether
  that matters depends on the repository's visibility, which is a question for
  the team, not for this file.

---

## Blocked on inputs, not defects

`scripts/verify_rebuild_state.py` reported five failures before `rawdata/` was
restored; it now reports **26 of 28 checks passing**. `Dim_Day_Status`,
`Fact_Batch_Sales` (both from `scripts/rebuild_extract_tbs.py`) and
`Dim_Demand_Cluster` (`scripts/cluster_demand_profiles.py`, which reads that
script's `data/rebuild_*.csv`) all populate once the workbooks are present.

Two failures remain, and neither is a code defect:

| Check | Why |
|---|---|
| `model_type matches step4's configured default` | Issue 9 — the assertion cannot hold alongside `step4b`'s `policy_rate` rows. |
| `Result_Category_Prophet_Metrics` exists | `scripts/forecast_category_prophet.py` needs `prophet`, which `requirements/requirements-prophet.txt` deliberately keeps out of the default install. |

One thing to watch: `rebuild_extract_tbs.py` writes
`data/day_status_vocabulary.csv` from its keyword rules when the file is absent,
and that file is a **human-editable override** whose `status` column wins on a
later run. The vault holds a copy that may carry review edits; the one generated
here on 2026-09-30 is unreviewed, so unlock the vault's version before trusting
either.
