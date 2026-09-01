# Power BI Dashboard — page-by-page build plan

This is the concrete visual spec for the five-view dashboard `docs/STATUS_AND_NEXT_STEPS.md`
§4 scoped: chart type, fields, filters, and colour for each page, grounded in the actual
current `ustore.db` — not a generic Power BI tutorial. Whoever opens Power BI Desktop next
should be able to build views 2, 4 and 5 directly from this; views 1 and 3 are specified as
far as the data allows and flagged where they stop.

**One rule carried over from the build plan and from every other part of this project:
Power BI never computes anything.** It renders numbers Python already wrote to `ustore.db`.
If a page needs a number that isn't in a table below, that's a missing pipeline step, not a
Power BI measure to invent.

## 0. Numbers to check your build against

Pulled live from `ustore.db` while writing this — if your build doesn't match, something
upstream changed and the counts below are stale, not your Power BI file:

| | |
|---|---|
| Products | 519 (266 with any sale, 62 with a stock/`days_of_supply` reading) |
| FSN split | F 58 · S 228 · N 233 · **6 HVL** (a modifier on F, not a 4th class) |
| Sales data span | 2024-05-02 → 2026-07-31 |
| Calendar span (`Dim_Date`) | 2023-01-01 → 2026-12-31 (1,461 rows) |
| Suppliers | 19, e.g. `USTORE`, `JYL ATHLETICA`, `VARSITY LIFESTYLE`, `TET AND DARS`, `JUC` |
| `payment_status` | CONSIGNMENT 47 · PAID 55 · UNKNOWN 161 · *(NULL)* 256 — NULL means no sales history, not "no status" |
| `Result_Prescriptive` | 416 rows = 208 priced SKUs × 2 ordering-cost scenarios |
| σ source | 334 observed, 82 `cv_fallback` (thin history, class-median CV substituted) |
| Store-closed dates | 43 of 1,461 |
| Calendar flag days | enrollment 34 · exam week 173 · event day 54 · sem break 185 |

## 1. Connecting Power BI to the data

Power BI Desktop has no native SQLite connector. Two options, pick one and say so in
Chapter 4 — don't let the report silently depend on a driver nobody else has installed:

**Option A — CSV/Parquet export (recommended for a defence machine).** Add one script,
`scripts/export_for_powerbi.py`, that dumps exactly the tables below to `data/powerbi/*.csv`
and point Power BI's **Get Data → Text/CSV** at that folder. Re-run it after any pipeline
change; Power BI's **Refresh** picks up the new files without touching queries. This is more
portable than an ODBC driver being present on whatever laptop the report gets shown on.

**Option B — ODBC driver** (e.g. the [community SQLite ODBC driver](http://www.ch-werner.de/sqliteodbc/)
or Devart's). Lets Power BI query `ustore.db` directly, no export step, but every machine
that opens the `.pbix` needs the driver installed and configured to point at the same file
path — fragile for a shared file or a defence on someone else's laptop.

Tables/views to expose either way (name them exactly this so the field names below line up):

```sql
-- Dim_Product, unfiltered
SELECT * FROM Dim_Product;

-- Fact_Sales joined to its date, since Power BI's date filtering wants a real date column
SELECT f.*, d.calendar_date, d.semester_id, d.semester_week,
       d.is_enrollment_period, d.is_exam_week, d.is_event_day, d.is_sem_break, d.is_store_closed
FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id;

-- Dim_Date, unfiltered (for the calendar-card view / date-flag lookups independent of a sale)
SELECT * FROM Dim_Date;

SELECT * FROM Dim_Parameters;
SELECT * FROM Result_Prescriptive;
-- once it exists (Prophet run):
SELECT * FROM Result_Forecast;
SELECT * FROM Result_Forecast_Metrics;
```

In Power BI, build a star schema mirroring `create_schema.py`: `Fact_Sales` (renamed
`Sales`) at the centre, `Dim_Product`/`Dim_Date` on one-to-many relationships from their key
columns. Mark `Dim_Date.calendar_date` as the model's **Date table** (Modeling → Mark as
Date Table) so month/quarter/year hierarchies and any built-in time intelligence work.

## 2. Colour and type — shared across all five views

Reuse the frontend's tokens (`UST Prototype Design/src/styles/redesign.css`) so the embedded
report and the coded screens around it read as one system, not two:

| Role | Hex | Use |
|---|---|---|
| Brand gold | `#F4B400` | primary accent, single-series bars/lines, active-filter highlight |
| Ink / near-black | `#16140F` | headers, KPI card labels, dark-chrome backgrounds if you theme the canvas |
| Fast / OK | `#2E7D55` | FSN "F", stock-healthy, service-level met |
| Slow / warn | `#B5791A` | FSN "S", approaching ROP, provisional-data callouts |
| Non-moving / critical | `#C1452F` | FSN "N", at/below ROP, stockout |
| HVL / info accent | `#6B4FB0` | HVL badge only — don't reuse this purple elsewhere, it should mean exactly one thing |
| Warm off-white bg | `#F4F3EF` | canvas background |
| Card white | `#FFFFFF` | visual containers |

Font: **Plus Jakarta Sans** (same as the frontend) if licensing/availability allows in Power
BI's font picker; otherwise Segoe UI is the safe fallback and won't clash badly.

**FSN, stock-status, and reorder-status colour must mean the same thing on every page.**
Green/amber/red is already load-bearing in the coded frontend (`.tag--ok/--warn/--crit`) —
don't introduce a second red-for-something-else on any Power BI page.

## 3. View 2 — FSN Classification *(ready now)*

**Source:** `Dim_Product` (`fsn_class`, `is_hvl`, `category`, `supplier_name`).

| Visual | Fields | Notes |
|---|---|---|
| KPI cards ×3 | `COUNT(product_id)` filtered by `fsn_class` | F / S / N, green/amber/red per §2. Put the HVL count as a 4th small card, purple, subtitled "of the F count" — it's a subset, not a sibling category |
| Donut or 100%-stacked bar | `fsn_class` (legend), `COUNT(product_id)` (values) | Same 3-colour rule. A donut matches the frontend's `Donut` component (Overview page) for visual continuity |
| Bar chart, by category | `category` (axis), `fsn_class` (legend, stacked), count (values) | Surfaces that 218/519 products have a NULL category — bucket those explicitly as **"Uncategorised"**, don't drop them |
| Table | `item_name`, `category`, `supplier_name`, `fsn_class`, `is_hvl` | Sortable/filterable full list, mirrors the coded FSN screen's paginated table |
| Slicer | `supplier_name`, `category` | Same filter vocabulary as the coded frontend's FilterBar |

**Caption, verbatim or close to it:** *"Fast/Slow/Non-moving classification is by 80th-percentile
ADUS (Average Daily Units Sold); 6 Fast items are additionally flagged High-Velocity-Limited
(HVL) — thin history, not a 4th class."* Put the classification method in a caption, not just
in Chapter 3 — someone reading only the dashboard should be able to tell how F/S/N was decided.

**Sensitivity note (optional but cheap):** `backend/catalog.py`'s `fsn_sensitivity()` /
`/api/fsn/sensitivity` gives the 75th/80th/85th percentile classification counts — if you
export that too, a small table showing how many items would move class at a different cutoff
is a legitimate robustness check, and it's already computed in the coded frontend's FSN page.

## 4. View 4 — Restocking Advisory *(ready now, provisional)*

**Source:** `Result_Prescriptive` joined to `Dim_Product` on `product_id`.

Every ROP/Safety Stock/EOQ number here is provisional pending the USTore site visit
(`Dim_Parameters`, all 17 rows, every one tagged `[PROVISIONAL - pending Block 5]`) — that
label has to be visible on the page itself, not just known to the person who built it.

| Visual | Fields | Notes |
|---|---|---|
| Banner/text box, top of page | static text | *"All figures below are provisional estimates — lead time, holding cost and ordering cost are not yet confirmed by USTore. See Dim_Parameters."* Same wording pattern as the coded Reorder Alerts screen |
| Slicer | `ordering_cost_scenario` | **Default to showing both side-by-side, not one.** EOQ swings ~12.6× between `low_admin_cost` (₱1,250/order) and `high_goods_value` (₱200,000/order) — collapsing to one scenario without showing the other misrepresents how uncertain the ordering-cost assumption is |
| Table | `item_name`, `fsn_class`, `lead_time_days`, `avg_daily_demand`, `safety_stock`, `reorder_point`, `eoq` (per scenario) | One row per SKU per scenario, or pivot the two `eoq` values into side-by-side columns if your Power BI version supports it cleanly |
| Card row | count of SKUs, `sigma_source = 'cv_fallback'` vs `'observed'` | 82 of 416 rows use the class-median CV fallback (thin per-SKU history) — flag which recommendations rest on a fallback, don't bury it |
| Bar/column | `item_name` (top N by `reorder_point`), `eoq` | Which items would need the largest order — useful as an "at a glance" chart above the full table |

**Do not** compute a "recommended reorder now" flag inside Power BI by comparing `reorder_point`
to a stock figure — `Fact_Sales.current_stock`-equivalent isn't in `Result_Prescriptive`, and
mixing a Python-computed ROP with a Power-BI-computed comparison is exactly the kind of
model-logic-leaking-into-the-report the build plan's own principle forbids. If a "reorder now"
flag is wanted here, compute it in Python (the same join `backend/app.py`'s `/api/reorder` and
`Overview.jsx`/`Reorder.jsx` already do — stock ≤ reorder_point) and export the joined result,
not the two tables separately.

## 5. View 5 — Automated Batch Sales Report *(ready now)*

**Source:** `Fact_Sales` joined to `Dim_Product` (`supplier_name`, `payment_status`,
`item_name`, `unit_price_php`), filtered to one month.

This mirrors the coded Batch Sales Report screen exactly — same grouping, same "units, not
pesos" framing (see below) — so a viewer flipping between the embedded Power BI page and the
coded screen sees the same shape of report, not two different report designs for one concept.

| Visual | Fields | Notes |
|---|---|---|
| Page-level filter | `calendar_date` (month) | One month at a time, matching the coded screen's month picker |
| Matrix, grouped by supplier | Rows: `supplier_name` → `item_name`; Values: `SUM(quantity_sold)`, `unit_price_php` | `payment_status` (CONSIGNMENT/PAID/UNKNOWN) as a slicer — the build plan's supplier-grouping requirement names this explicitly |
| Card | total units in period | The coded screen deliberately reports **units, not a peso grand total** (only ~340 of 519 products carry a `unit_price_php`, so a peso total silently under-counts) — carry that decision here, don't add back a misleading revenue figure Power BI would compute over a partial column |
| Export | Power BI's built-in **Export to PDF** | This *is* the PDF export the coded frontend explicitly deferred (native PDF libs on Windows) — worth stating in Chapter 4 that Power BI's own export covers the requirement, so the frontend not having its own PDF button isn't a real gap |

**Caption:** *"Quantities only. Unit price is shown as supplier-remittance reference data,
not a transaction total — this is an internal counting report, not an invoice."* Keep the BIR
framing visible here too; it's not just a frontend-code constraint, it should read that way
on every artifact this project produces.

## 6. Calendar-contextual interpretation cards

Not a full view — small annotation cards (build plan, Phase 6) that sit near the charts they
explain, tied to `Dim_Date`'s boolean flags, so a demand spike or dip reads as "enrollment
period" or "exam week" instead of an unexplained anomaly.

**Source:** `Dim_Date` (`is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`,
`is_store_closed`), currently 34 / 173 / 54 / 185 / 43 days respectively.

Implementation: a small multiple-row card or a background shaded region on any time-series
visual, keyed to whichever flag is true for the visible date range. Power BI's **conditional
formatting on the X-axis** or a thin secondary area series (0/1 stepped) both work; pick
whichever your Power BI version renders without a custom visual, since custom visuals are
another install dependency on a defence machine (same portability concern as §1).

Text pattern per card, e.g.: *"Nov–Dec: Foundation Day / Paskuhan (54 flagged event days across
the dataset) — Souvenirs and Apparel demand typically rises."* Ground the claim in the actual
flagged-day count, not a vibe.

## 7. View 1 — Stock Status *(blocked — build the page shell only)*

**Blocked on B10** (`docs/STATUS_AND_NEXT_STEPS.md` §3): only 62 of 519 products (≈14–17% of
units) have any `days_of_supply` reading at all — the raw inventory workbook simply doesn't
cover most SKUs. The build plan makes this the dashboard's *entry point*, which means putting
a barely-populated view first, unless the coverage gap is put on the page itself rather than
quietly filtered away.

What you can build today, honestly:

| Visual | Fields | Notes |
|---|---|---|
| Card, prominent | `62 / 519 products have a stock reading (≈14–17% of units)` | This has to be the first thing on the page, not a footnote — the same framing the coded Overview screen's Stock Status banner already uses |
| Table, scoped to the covered subset | `item_name`, `current_stock`-equivalent, `days_of_supply`, `is_censored` day count | Title it *"Stock Position — covered items only"*, not *"Stock Status"* unqualified |
| Do not build | a store-wide "% in stock" or "at risk" summary number | Any store-wide percentage computed only over the covered 14–17% misrepresents the other 86% as either fine or absent — neither is true, it's just unmeasured |

Finish this view once the team's B10 decision lands (scope it to the covered subset
permanently, or invest in closing the coverage gap). Until then, ship the honest partial
version above rather than leaving the page blank or, worse, quietly interpolating the missing
86%.

## 8. View 3 — Demand Forecast *(unblocked — the tables exist)*

No longer blocked on `cmdstan`. `step4_forecast_model.py` forecasts every Fast SKU with a
rolling mean, runs in seconds with no toolchain, and has been run: `Result_Forecast` holds
1,740 rows (58 SKUs x 30 days) and `Result_Forecast_Metrics` holds 144 rows. Two things to
build against honestly: `yhat` is **flat across the horizon** (the model is a constant per
SKU, so a forecast line has no slope or seasonality to show), and 5 of the 58 SKUs carry
`is_heuristic = 1` with null metrics — surface that flag rather than showing a blank
accuracy cell.

The view is:

| Visual | Fields | Notes |
|---|---|---|
| Line chart per SKU | `Result_Forecast`: `forecast_date`, `forecast_value`, actual `quantity_sold` for the same window | Actual vs. forecast, same SKU-selector pattern as the coded Demand Forecast screen |
| KPI cards | `Result_Forecast_Metrics`: MASE / MAPE for the selected SKU | Report against the metric B2/B3 actually settle on — `model_benchmark_results.csv` already has candidate numbers; don't invent a number here ahead of that decision |
| Slicer | `item_name` (Fast/HVL only — Prophet only fits those per `step4`'s design) | |

Do not stub this page with a placeholder chart drawn from `rolling_mean_30` styled to look
like a forecast — the coded Demand Forecast screen deliberately shows a "pending" card instead
of a number for exactly this reason (see `UST Prototype Design/README.md`). Match that
decision here: an empty page with a one-line "pending — see docs/STATUS_AND_NEXT_STEPS.md §3"
caption is more honest than a chart that looks finished but rests on an unselected model.

## 9. Build order

Matches `docs/STATUS_AND_NEXT_STEPS.md` §4.4: **2 (FSN) → 4 (Restocking) → 5 (Batch Report) →
6 (calendar cards) now**, while B2/B3/B10 get resolved with the adviser/USTore in parallel.
Add **1 (Stock Status)** once B10 lands, **3 (Demand Forecast)** once B3 lands and `step4` has
actually been run. Every number this dashboard will ever show already has to exist in
`ustore.db` before the page gets built — that's the whole point of Power BI being a
presentation layer, not a second place where numbers get computed.
