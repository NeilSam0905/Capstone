# Remediation Master v2 — Wave 1 status

**Date:** 2026-08-19
**Source:** `REMEDIATION_MASTER_v2.md` (repo root, untracked — treated as a frozen input document,
not edited by this pass). Executed per `[[goofy-kindling-bubble]]` plan, approved without changes.

This is the record the remediation doc itself asks for: what changed, why it's a defensible
departure from the manuscript where it is one, and what's still sitting on someone else's desk.
Nothing below has been committed or pushed yet.

---

## 1. What changed

### Code fixes

| Item | File(s) | What |
|---|---|---|
| **D1** | `scripts/model_benchmark.py`, `tools/service_frontier.py` | Safety stock was sized off `√lead_time` (7d, continuous-review ROP formula) while the simulated policy is periodic review with no replenishment inside the 30-day fold. Fixed to `√(review_period + lead_time)` = `√37`. Fill rate moves from ~0.71 to ~0.775 for the deterministic methods; `ets` lands at 0.7768 (was assumed 0.7775 — ETS/BLAS non-determinism, already documented in `tests/test_determinism.py`, so the gate uses a wider tolerance for that one method only). |
| **D2** | `scripts/step2_load_fact_sales.py`, `tests/test_step2_load_fact_sales.py` (new) | ETL's `DELETE FROM Fact_Sales` wiped interface-written rows (`tally_date_flag = 0`) on every reload, not just the historical tally rows it was meant to replace. Scoped to `WHERE tally_date_flag = 1`. |
| **D3** | `scripts/create_schema.py`, `backend/app.py`, `scripts/populate_dim_date.py`, `tests/test_populate_dim_date.py` (new) | Same defect class as D2, for closures: the calendar UI's closure toggle wrote directly to `Dim_Date`, which a `populate_dim_date` rebuild then overwrote. Added an append-only `Closure_Log` table (latest-entry-wins), the closure endpoint now dual-writes to it, and `populate_dim_date` replays it after every rebuild. **Extended beyond the doc's literal scope:** found that `Event_Log` had the identical gap — `create_schema.py`'s own comment claims the ETL replays it, but nothing did, unnoticed because `Event_Log` has 0 rows in real data so far. Fixed the same way, same place. |
| **S1** | `scripts/step5_prescriptive.py` | EOQ's annual demand `D` was sourced from a forecast method's 30-day output annualised — coupling EOQ (batching economics) to forecast accuracy, and zeroing out any SKU a method forecasts as 0. Added `--demand-basis {forecast,trailing}`, **default `trailing`** (SKU's own trailing-365-day observed sum). This is a default flip the doc frames as a team call, not a pure bug fix — see §3. |
| **S3** | `scripts/populate_dim_date.py` | Added `is_tally_date_positive` (dates with `Total Quantity > 0`) alongside the existing zero-inclusive `is_tally_date`, additive only — nothing that reads `is_tally_date` changes meaning. Measured 416 positive-tally dates, not the doc's assumed 411 — see §3. |
| **S10** | `data/model_benchmark_results.csv`, `data/model_benchmark_summary.csv` | Re-ran the full benchmark (already-wired `ewma_a0.1` and `rolling_q75_30` were sitting uncommitted from an earlier session). Committed CSVs now carry 10 methods, not 8. |
| **S12** | `scripts/step1_apply_mapping.py`, `scripts/create_schema.py` | Items with no inventory price now fall back to a `@NNN` suffix parsed from the item name, tracked via a new `Dim_Product.price_source` column (`inventory` / `name_suffix` / `NULL`) so the two sources are never silently conflated. |

### Documentation fixes (C1–C4, R3)

- **`docs/DIVERGENCE_REGISTER.md`** — row #6 rewritten to point at the frontier (below) instead of the
  disproven "service level ≥ 95%" proposal; row #16 corrected a second time (13/15 flagged-closed
  dates sold nothing, only 2 genuinely traded); new rows #22 (frontier finding) and #23 (Closure_Log
  divergence from §3.1.1).
- **`docs/STATUS_AND_NEXT_STEPS.md`** — three stale claims fixed against `git log` (backend shipped
  2026-08-10, Power BI embed 2026-08-08/09), phase table updated.
- **`docs/SERVICE_LEVEL_FRONTIER.md`** — Cause 1 rewritten past-tense/fixed; Cause 3 frontier table
  replaced with `tools/service_frontier.py`'s actual measured numbers (0.645→0.794 at q=0.95, knee at
  q=0.80/fill 0.742), with a reconciliation note explaining the original 0.673→0.818 prose was never
  reproducible from a committed script.
- **`tests/test_service_frontier.py`** (new, R3) — pins the 0.9490 structural ceiling, the knee being
  a real marginal-cost jump, `rolling_mean_30`'s dominance at q=0.80, and the 208-of-266 EOQ count.

### The `rawdata/` path fix (separate from the doc, triggered by user note)

The user renamed `drive-download-20260724T120738Z-1-001/` to `rawdata/` for convenience, which broke
`scripts/step0_convert_sales_with_zeros.py` (`FileNotFoundError`) and two hardcoded references.
Fixed:

- `scripts/step0_convert_sales_with_zeros.py` — `SRC_DIR = "rawdata"`, docstring updated.
- `README.md` line 39 — pipeline table's step0 row.
- `tools/provenance_may2024.py` line 53 — `WORKBOOK` constant.

Verified: `step0` now runs successfully for the first time this session (75,120 rows). A complete
from-scratch pipeline rebuild — all 9 steps, `create_schema` through `step5_prescriptive`, including
`step0` for the first time — reproduces byte-identical output to what was already committed. No data
changed; this was purely a stale-path bug.

---

## 2. Verification performed

- Fresh `ustore.db` rebuilt from scratch through the full chain; all invariants that shouldn't move
  didn't (84,399 Fact_Sales rows / 89,232 units), the ones that should move did (`price_source`
  populated, `Result_Prescriptive` now 208-SKU-based, `is_tally_date_positive` = 416 dates).
- `python tools/assert_invariants.py --phase a10` — 22/22 pass.
- `python scripts/verify_data.py` — pass.
- `python -m pytest tests/ -q` — new D2/S3/R3 tests pass, no regressions.
- `python scripts/model_benchmark.py` (full run) → `python tools/service_frontier.py` — all gates
  pass, including the new pre-fix/post-fix D1 comparison.
- Backend closure endpoint exercised live against the new `Closure_Log` path; confirmed a
  `populate_dim_date` re-run does not erase a closure set through the API (this was D3's actual
  failure mode, proven fixed rather than assumed).
- `python tools/provenance_may2024.py` — passes against the new `rawdata/` path.

---

## 3. Called out, not silently decided

- **S4 excluded entirely.** Merging four price-suffix families in `vocab_mapping_FINAL_v5.csv` would
  modify the controlled vocabulary, which `CLAUDE.md`'s scope policy forbids outright regardless of
  what a planning document proposes. Not implemented, not partially implemented.
- **S1's default flip (`trailing`, 79→208 priced SKUs)** is live behind `--demand-basis`, but the doc
  frames the *choice* between demand bases as a team ratification, not a bug fix. `--demand-basis
  forecast` restores the old behavior; both paths are tested. Needs sign-off.
- **`is_tally_date_positive` = 416, not the doc's assumed 411.** My implementation derives both flags
  from the same zero-inclusive source file, filtered differently; the doc's 411 apparently came from
  mixing in an older, incomplete file (missing July 2026 data). Kept 416 as the methodologically
  sound value and flagged the discrepancy rather than quietly matching either number.
- **Event_Log read-back** (under D3) is scope beyond what the doc named — a real gap found while
  implementing the closure fix, fixed the same way for consistency, disclosed here rather than folded
  in silently.

---

## 4. What's still needed

### Human/adviser/USTore decisions (doc's own labeling — not something to act on unilaterally)

- **R1 — repo visibility.** The repo is currently public and holds a real client's commercial data.
  Unresolved, and now sharper because of the item below: `rawdata/` is untracked while the old
  `drive-download-.../` files show as tracked-deletions in git — an unstaged rename. Before doing
  anything (`git add rawdata/`, `.gitignore` it, or leave it as-is), decide whether raw workbooks
  should be in a public repo at all. Nothing has been staged.
- **R2** — branch merge (`neil` → `main`) timing/ownership.
- **O1** — on-machine Prophet confirmation (environment-specific, not something I can verify here).
- **O2** — adviser meeting to actually walk through the frontier (§ SERVICE_LEVEL_FRONTIER.md) and
  ratify reporting a curve instead of a fixed service-level target.
- **O3** — the actual Power BI `.pbix` build (embed route exists; report itself doesn't).
- **S1 ratification** — pick `trailing` (current default) or `forecast` as the real default.
- **S5, S7, S8, S9** — four "team ratifies" calls the doc lists; only their documentation drafts were
  in scope for this pass, not the decisions themselves.

### Not yet started (doc items outside Wave 1)

- **S6, S13, S14** — deferred, not evaluated for a Wave 2 yet.
- **S4** — permanently out of scope per CLAUDE.md unless that policy itself is revisited by the user.

### Mechanical, low-risk, no decision needed

- Nothing is currently committed. `git status` shows the full Wave-1 diff plus the `rawdata/` fix
  sitting unstaged/untracked. Committing is a one-line ask away whenever you want it done — this
  document exists partly so that commit, whenever it happens, has a message that can point here
  instead of re-deriving the list.

---

## 5. Files touched this pass (for the eventual commit)

```
M  README.md
M  backend/app.py
M  conftest.py
M  data/model_benchmark_results.csv
M  data/model_benchmark_summary.csv
M  docs/DIVERGENCE_REGISTER.md
M  docs/SERVICE_LEVEL_FRONTIER.md
M  docs/STATUS_AND_NEXT_STEPS.md
D  drive-download-20260724T120738Z-1-001/*.xlsx (5 files, effectively renamed to rawdata/, not yet staged as such)
M  scripts/create_schema.py
M  scripts/model_benchmark.py
M  scripts/populate_dim_date.py
M  scripts/step0_convert_sales_with_zeros.py
M  scripts/step1_apply_mapping.py
M  scripts/step2_load_fact_sales.py
M  scripts/step5_prescriptive.py
M  tools/provenance_may2024.py
M  tools/service_frontier.py
?? rawdata/ (new location of the 5 files above)
?? tests/test_populate_dim_date.py
?? tests/test_service_frontier.py
?? tests/test_step2_load_fact_sales.py
```
