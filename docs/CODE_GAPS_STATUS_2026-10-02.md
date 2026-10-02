# Code gaps closed (2026-10-02, branch `neil`, on top of `cb50bdb`)

This pass worked through every item in `docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md` that code can close. It
lists what changed, what staff can now do, what is left, and why it is left. Section numbers refer to
that file, which has a status note under each item.

**In short**

- **Ready for daily use:** a production server, daily database backups, and a warning before the
  calendar runs out (1.3, 2.1).
- **Bulk and organisation orders can be recorded,** both after the sale and ahead of time. The forecasts
  then predict everyday sales and add the known orders on top (3.4). This was the largest error cause
  the team measured.
- **Lead times can be measured.** Staff record restock deliveries, and a supplier's real delivery time
  replaces the verbal estimate once there are 3 deliveries (5).
- **The pipeline is faster:** a routine run takes 55 s instead of 125 s, and a run with a changed
  workbook 74 s (8).
- **Today's numbers are unchanged.** On the current data every table, forecast and reorder point is
  identical to the run before these changes. The new features only change results once staff use them.

---

## Done in this pass

| § | What changed | Where |
|---|---|---|
| 1.3 | **Production server:** `python serve.py` serves the API and the built dashboard from one address (waitress; `--host 0.0.0.0` for the store's network). **Daily backup** of `ustore.db` while it runs, with SQLite's online backup API; each copy is integrity-checked and the last 14 are kept in `backups/`. `backup.py --list` / `--restore` (a restore saves the current database first). | `backend/serve.py`, `backend/backup.py`, README "Daily use at the store" |
| 2.1 | The calendar is **not** extended (protected file). The Tally Interface warns from 120 days before its last day (shown now: 90 days left), and a date past the end is refused with a clear reason instead of "Date not found in the calendar". | Tally Interface banner, `backend/validation.py` |
| 3.4 | **Order type** on every sale: walk-in / bulk / pre-order. It can be set in the Transaction Type list, an import's "Order Type" column, or for history through the store's answers in `data/bulk_day_candidates.csv`. The **forecasts** train on walk-in sales only; classification, reorder points and the dashboard still count every sale. **Upcoming Bulk Orders:** orders the store knows are coming are added to the forecasts on their date. | `scripts/order_types.py`, steps 1b / 2 / 4 / 4b / 4c, Tally Interface |
| 5 | **Restock Deliveries** card: supplier, ordered on, delivered on. It shows each supplier's deliveries, median and range, and the lead time in use. From 3 deliveries, step 5a uses the supplier's median; `Result_Prescriptive` marks this "measured (n deliveries)". | `Restock_Log`, `step5a_set_lead_times.py`, Tally Interface |
| 7.2 | README brought up to date: 84,430 rows, 95,182 units, 520 products, F 30 / S 238 / N 252, July complete, measured run times. It says the pinned checks still expect the pre-July values. | `README.md` |
| 8 | **Speed.** Step 0 writes the price columns step 1 needs (so step 1 opens no workbook). It reads sheets in read-only mode and skips conversion when no workbook changed (`--force` converts anyway). Step 1 also no longer needs `rawdata/`. | step 0 / step 1, `.step0_state.json` (gitignored) |
| — | **Vault fix:** Marco's `USTore_sales_2023_daily_synthetic.csv.enc` was pushed without a manifest entry, so no other machine could unlock it (his synthetic-history step was skipped everywhere else). It is now in the manifest, together with the two new price CSVs. | `vault/manifest.json` |

### Pipeline times (same data, same code apart from these changes)

| Run | Before | Now |
|---|---:|---:|
| Step 0, converting | 34.6 s | 27.3 s |
| Step 0, no workbook changed | 34.6 s | 1.0 s |
| Step 1 | 45.6 s | 2.2 s |
| Full run, a workbook changed | 124.7 s | 73.6 s |
| Routine run, no workbook changed | 124.7 s | 54.6 s |

### Done earlier today

- 3.1 `transaction_type`, 3.5 suppliers, 3.6 2023 batch file, 6.1 renamed names, 6.2 new workbooks: see
  `docs/PROGRESS_2026-10-02.md`.
- 1.2 login and CORS, 4.1 Fast-list rule, 4.2 Non-moving definition, 4.3 30-start-date test, 4.4
  prediction targets: Marco's commits `88fb624` and `cb50bdb`.

---

## What staff can do now (Tally Interface)

- **Transaction Type** has "SALE — walk-in", "SALE — bulk or organisation order" and "SALE — pre-order".
  Pick bulk or pre-order for organisation orders.
- **Upcoming Bulk Orders** (under Sales Inventory Tally): record an order you already know about, with
  its expected date and quantity.
- **Restock Deliveries:** record each restock when ordered, and mark it "Arrived" when it comes.
- **Calendar warning** at the top of the page, shown until the calendar is extended.
- **For daily use:** run `python serve.py` instead of the two development servers.

---

## Not done by code, and why

| § | Gap | Why not |
|---|---|---|
| 1.1 | Public repository | Owner action (GitHub settings), then a group decision on purging history |
| 2.1 / 2.2 | Calendar past 2026; first-term enrollment | `calendar_ranges.csv` is protected; needs the published UST calendar. Only a warning was added |
| 3.2 | Stock counts into step 2 | Marked a group decision: it changes `is_censored` and the pinned checks |
| 3.3, 3.5 | Renamed pairs; "Alessandrini" / "Hernandez"; Corp Jacket v.3 | Vocabulary decisions (`vocab_merge_candidates_2026-10.csv`) |
| 4.3 | Enrollment raise | Blocked on 2.2 |
| 4.4, 6.3 | HVL rule, advisories, zero-sale verification | Moved to Recommendations in the errata |
| 7.1 | Pinned expected values | Group approval; never edited by code (CLAUDE.md) |
| 7.3 | Merging `tyrone` / `gambe` | Team call. `tyrone`'s `d5275e6` deletes the CLAUDE.md policies |
| 7.4 | Splitting `app.py` / `TallyInterface.jsx` | Marco and Tyrone are editing these files; a large move now would cause merge conflicts |
| 8 | Step 2 still reloads all of `Fact_Sales` (~17 s) | Left for later |

---

## Needs the group

1. **Calendar before December (2.1, 2.2).** From 1 January 2027 no tally can be saved.
2. **Pinned values (7.1).** After the next pipeline run, `assert_invariants.py` fails 11 of 21 checks,
   all known:
   - rows, units, zero rows and products, from the July update;
   - F 58 → 30, S 228 → 238, N 233 → 252 and HVL 6 → 0, from the 4.1 / 4.2 rules;
   - `Dim_Parameters` 0 → 17.

   These changes add no new failures.
3. **Confirm the vocabulary-write extension (6.1)** for Names to review and Rename Item.
4. **Bulk orders and classification (3.4):** should a recorded bulk order also stay out of FSN and the
   reorder points? Today they still count it, because the stock left either way.
5. **Default password:** `staff` / `staff123` is in this public repository. Change it before any
   shared use.
6. **Power BI:** keep both the web dashboard and the embedded report (6.4)?

## Needs USTore

- Mark the past bulk / organisation days in `data/bulk_day_candidates.csv` (column
  `bulk_order_confirmed`: yes / pre-order / no).
- From now on, record such orders as SALE — bulk or pre-order, and known future ones under Upcoming Bulk
  Orders.
- Record restocks (ordered and delivered).
- Enter monthly stock counts.
- Provide ordering and holding costs, and the first-term enrollment dates.

---

## Checks run

- **Pipeline:** the full pipeline ran on a copy of the repo before and after these changes. Every table
  and output CSV is identical (timestamps aside). The step 0 sales CSV is byte-identical, and the price
  files match full-mode workbook reading row for row. New: two empty tables (`Restock_Log`,
  `Upcoming_Order`) and an empty `order_type` column.
- **`pytest`:** 467 passed, 1 skipped, 2 failed. The 2 failures are the known pinned values in
  `tests/test_degenerate_forecast.py`.
- **New tests:**
  - `test_backup_and_serve.py`
  - `test_order_types.py`
  - `test_restocks.py`
  - new cases in `test_step0_workbooks.py`
- **Browser:** checked through `serve.py` against a copy of the database. Sign-in, a bulk sale, an
  upcoming order, two restocks and the calendar warning all worked, with no console errors. A page
  reload keeps the app, and a write from the served address passes the origin check.
- **Frontend:** builds. ESLint shows no new problems (the 2 existing setState-in-effect errors in
  `TallyInterface.jsx` remain).
- **Protected files:** the vocabulary, supplier mapping, allocation groups, calendar ranges and
  verification expected values were not edited.

## Notes for teammates

- **Dependencies:** `pip install -r backend/requirements.txt` adds `waitress`. `serve.py` needs a built
  dashboard: `npm run build`.
- **After pulling:** the backend or a pipeline run restores the new price CSVs and the synthetic 2023 file
  from the vault. Step 1 then works without `rawdata/`.
- **New operational tables** (created on startup, never cleared by the pipeline):
  - `Restock_Log`
  - `Upcoming_Order`
  - `Fact_Sales.order_type` and `Pending_Import_Row.order_type` columns
- **Backups:** `backups/` and `.step0_state.json` are machine-local and gitignored. Copy `backups/`
  somewhere safe now and then.
