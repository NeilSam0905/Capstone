# Open issues

Technical problems that still need fixing. Moved out of the README so that file
stays readable; the resolved history is kept at the bottom rather than deleted.

**Team decisions** (B1–B15 — repo visibility, the acceptance criterion, model
selection, the site visit) live in `docs/STATUS_AND_NEXT_STEPS.md`. This file is
for engineering work, not decisions awaiting a human call.

---

## Open

### 1. 32 of 58 forecast SKUs return zero
`rolling_mean_30`'s trailing window is empty for 32 Fast SKUs, so their forecast
is a flat zero line and `step5 --demand-basis forecast` prices only 26 SKUs
(against `trailing`'s 208). This is `docs/DEGENERATE_FORECAST.md`'s argument
appearing in production rather than in a benchmark. It is the direct cost of
switching to the benchmarked trailing window, and it is a live decision, not a
bug. See `docs/ROLLING_MEAN_FORECAST.md` §4.

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

---

## Resolved

Kept as a record so the same ground isn't re-covered.

- **Prophet.** Superseded rather than fixed: step 4 no longer uses Prophet at
  all, so the `cmdstan` question (Block 5 / B5) is closed. Nothing in the repo
  imports `prophet`. See `docs/ROLLING_MEAN_FORECAST.md`.
- **`Overview.jsx`'s "Items Below / Near ROP" KPI was stale.** It now computes
  the same reorder-now count `Reorder.jsx` does (stock ≤ reorder point, both
  real), with a copy pass across the screen.
- **No PDF export on the Batch Sales Report.** Now server-rendered by
  `backend/batch_pdf.py` at `GET /api/reports/batch.pdf?month=YYYY-MM`
  (`&inline=1` to view rather than download). Four things worth keeping:
  - The blocker was the dependency, not the work — `weasyprint` needs
    GTK/Pango/Cairo on Windows and `reportlab` ships a C extension.
    **`fpdf2` is pure Python from a plain wheel.**
  - The PDF and the on-screen report share one builder
    (`app.build_batch_report`), so they cannot disagree — both report 15
    suppliers / 114 line items / 2,637 units for 2026-04.
  - **Latin-1, deliberately.** fpdf2's built-in Helvetica is Latin-1 and
    embedding a Unicode TTF would mean shipping a licensed font. All 539
    catalogue names are already Latin-1, so nothing is lost; money prints as
    `PHP 1,234.00` because ₱ (U+20B1) is not.
  - Totals are **unit counts**, not peso figures — the BIR constraint holds:
    this is an internal counting document, not an invoice.
- **`ustore.db` predated `Result_Prescriptive` / `Closure_Log` / the Wave 1
  schema.** It had never been rebuilt since the original ETL work, and
  `backend/db.py`'s unconditional `CREATE INDEX ... ON Result_Prescriptive`
  meant every API call 500'd. Rebuilt from scratch; every documented invariant
  reproduced exactly. **A stale-but-present database fails differently, and less
  visibly, than a missing one** — that lesson generalised into open issue 6.
