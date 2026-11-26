# USTore Re-check Plan (after the 23 November changes)

A second pass over the system, from the pipeline to the screens. It does three things:

1. **Verifies the three commits pushed on 23 November** and everything they touch.
2. **Re-tests the 36 bugs** logged by the first pass, so each one has a current status.
3. **Closes the gaps** the first pass left: checks it could not run, checks that only partly passed, and the
   kinds of bug it was not designed to catch.

- **Prepared:** 2026-11-25, against `main` at `5139c9a`.
- **Builds on:** [QUALITY_CHECK_PLAN.md](QUALITY_CHECK_PLAN.md) (the first plan; its check IDs such as `C1-22` are
  reused here) and its results, `USTore_Quality_Check_Results_2026-11-20.xlsx` at the repo root. The workbook is
  gitignored because it holds store data; ask whoever ran the first pass for a copy.
- **New check IDs** start with `R` (R1 = rounding, R2 = on-hand, R3 = sidebar, R5 = property checks). Add them to the
  workbook's Checklist as new rows. New bugs continue from `QA-040`.

---

## 0. Before you start

- [ ] Pull `main` (at `5139c9a` or later).
- [ ] **Rebuild the dashboard:** `cd "UST Prototype Design"; npm run build`. `python serve.py` serves `dist/`, and the
  `dist/` on the first-pass PC was built on 2026-11-20, before these fixes. Without a rebuild you would be testing the
  old screens.
- [ ] `pip install -r backend/requirements.txt`. `waitress` was missing on the first-pass PC (QA-028).
- [ ] Make a QA copy of the whole project folder and test only in it, exactly as the first plan's §0 says. Several
  checks here record counts and sales.
- [ ] Optional, for §7: install the 64-bit **SQLite ODBC driver** so Power BI can refresh (QA-024).

The ground rules from the first plan still apply: never edit the controlled vocabulary, allocation groups, supplier
mapping, calendar ranges or a pinned expected value to make a check pass.

---

## 1. What changed since the first pass

| Commit | Change | Code touched |
|---|---|---|
| `7ec569c` | **Order quantities round up.** Rounding to the nearest unit left items flagged "Reorder now" with an order of 0 (Baller, Bookmarks), and others a unit short (2.45 → 2). Now `ceil`, in the Reorder list, What-if and Edit Settings. | `backend/app.py`, `reorder_settings.py`, `whatif.py`, 2 test files |
| `05187a6` | **On-hand runs down with tallied sales.** On hand used to be the last count, unchanged until the next one. It is now the last *staff* count minus every unit tallied out since its count day (sales, damaged, promo, transfer), never below 0. Workbook figures are not run down. Every on-hand number gets a tooltip saying where it came from. | `backend/catalog.py`, `app.py`, `season_ahead.py`; `Overview.jsx`, `Reorder.jsx`, `OrderAhead.jsx`, `TallyInterface.jsx`, `lib/format.js`; new `tests/test_stock_running_balance.py` |
| `5139c9a` | **Sidebar icons name their page** on narrow screens (≤ 720 px): tooltip, screen-reader label, current page marked. | `App.jsx` |

**None of the 36 logged bugs is fixed by these commits.** They fix three problems the first pass did not log. The
only overlap is that `Reorder.jsx` (where QA-030, QA-031 and QA-038 live) changed, but only to add the on-hand tooltip.
§4 re-tests all 36 anyway.

**Why the first pass missed these three:**

- *Rounding.* Its check (C1-22) rebuilt the order quantity with the same formula the code used, so a wrong formula
  agreed with itself. §5 adds checks of what must be true (an item to reorder gets at least 1 unit) instead of
  re-deriving the code.
- *On-hand.* No check entered a sale after a count and looked at on-hand again. The behaviour was a design gap, not a
  crash.
- *Sidebar.* The narrow-screen check (F1-01) measured page width only, not whether icon-only buttons had names.

### First-pass checks whose expected result has changed

| Check | Old expectation | New expectation |
|---|---|---|
| C1-22, D5-02, E-03 | order = max(0, **round**(ROP + 30 × daily demand − on hand)) when due | order = max(0, **ceil**(ROP + 30 × daily demand − on hand)) when due, and ≥ 1 |
| D5-01 | footer "TOTAL TO ORDER" = 796 units (QA copy, 2026-11-20) | higher: every part unit now rounds up |
| C1-27, E-06, E2-05 | a staff count shows as on-hand unchanged | a staff count is run down by everything tallied out since its count day |
| D1-06, F1-01 | icon-only sidebar at 390 px | each icon has a tooltip and label |

---

## 2. Baseline today (2026-11-25, main working copy)

| Check | Result | Compared with the first pass |
|---|---|---|
| `python -m pytest tests -q` | **772 passed, 3 failed** (2 min 19 s) | +8 new tests, all passing. Same 3 failures: two pinned counts (QA-007) and step 5's gate (QA-001) |
| `npm run lint` | **9 errors** | Unchanged (one moved from `Overview.jsx:1180` to `:1183`) |
| `npm run build` | builds; main chunk **513.65 kB** | +0.9 kB (QA-006) |
| `python scripts/vault.py status` | 12 conflict, 1 local-changed, **1 new** | The new file is the results workbook, which `vault.py lock` will encrypt like the other root workbooks (QA-022 unchanged) |
| `git config core.hooksPath` | unset | Unchanged (QA-008) |
| `ustore.db` | last rebuilt 2026-11-09 | Unchanged. The three fixes are computed when the API is called, so no pipeline run is needed to see them |

`verify_data.py`, `assert_invariants.py` and `verify_rebuild_state.py` were not re-run: neither the data nor the
pipeline changed, so they will match the first pass. Re-run them in §6 anyway.

---

## 3. Verify the three fixes

### 3.1 Order quantities round up (`7ec569c`)

- [ ] **R1-01** Every item the API marks `needs_reorder` has `suggested_order_qty` ≥ 1. On screen, no "Reorder now"
  row shows an order of 0. Check **Baller** and **Bookmarks** by name: the commit says they showed 0.
- [ ] **R1-02** For each due item, order = ceil(ROP + 30 × daily demand − on hand), using the API's own numbers.
  A shortfall of 2.45 orders 3; an exact whole number stays whole (14.0 → 14, not 15).
- [ ] **R1-03** One item, same order quantity everywhere: the Reorder list, its What-if panel with nothing changed,
  its Edit Settings preview, and the Overview's "Reorder N items · x–y units" range.
- [ ] **R1-04** The Overview range's lower figure (`suggested_order_min`, the order without safety stock) also rounds
  up, and is never more than the upper figure.
- [ ] **R1-05** **Plan Ahead** ("Reaches ROP · qty" and "Extra for Rush") rounds up too. It already used `ceil`
  before this commit (`season_ahead.py` lines 180 and 225), but **without** the `round(…, 6)` guard the commit added
  elsewhere, so a float like 14.000000001 could still show as 15. Compare one item's Plan Ahead quantity with the
  Reorder list. If they differ by one, log it.
- [ ] **R1-06** The Reorder Now tile and the "TOTAL TO ORDER" footer equal the sum of the Order Qty column, now
  rounded up.
- [ ] **R1-07** `python -m pytest tests/test_reorder_settings.py tests/test_whatif.py -q` passes.

### 3.2 On-hand runs down with tallied sales (`05187a6`)

Do these in the QA copy. Pick an item **P** that sells, has no staff count yet, and is not discontinued. Note its
on-hand, ROP and status first.

- [ ] **R2-01** Count P at 20 for the current month. On-hand becomes 20 minus any of P's units already tallied
  **today** (the count day itself is included by design). The tooltip on the number reads "Counted 20 as of
  <date>, minus N sold or removed since" (or "nothing sold since").
- [ ] **R2-02** Record a sale of 3 for P. **Without running the pipeline**, on-hand drops by 3 on every screen that
  shows it: Reorder list, Overview "Order these first", Plan Ahead, the Inventory tab's Current stock, and
  `/api/stock`.
- [ ] **R2-03** Record 1 DAMAGED, 1 PROMO and 1 TRANSFER for P. Each lowers on-hand by 1.
- [ ] **R2-04** Sell more than is left. On-hand stops at 0, never negative, and P's status becomes Stockout or
  Reorder now.
- [ ] **R2-05** Recount P (needs the admin password). On-hand resets to the new figure, and the count day becomes
  today.
- [ ] **R2-06** **Correction from Current Inventory, typed after sales the same day.** Sell 4 of P in the morning,
  then use Current Inventory to correct on-hand to what is really on the shelf, say 10. Expected from the code: the
  screen then shows **6**, because the correction's count day is today and today's earlier 4 are subtracted again.
  Decide whether that is intended. For a correction that says "this is what is on the shelf now", staff will expect
  10. If not intended, log it as a bug.
- [ ] **R2-07** **A count for an earlier month typed today** (e.g. October's figure entered on 25 November) stands
  for 1 October, so every unit sold since 1 October is subtracted. Confirm with the group that this is what a late
  entry means.
- [ ] **R2-08** **Imported counts** (Inventory tab > Import, including the store's own inventory report workbook)
  stand for the 1st of their month, and are run down from there.
- [ ] **R2-09** An item whose only figure comes from the **inventory workbook** is not run down. Its tooltip reads
  "From the <Month> inventory workbook; count it to keep it current".
- [ ] **R2-10** **Delete P's count** (admin). P falls back to its previous count month (run down from that) or to
  the workbook figure, and the tooltip changes with it.
- [ ] **R2-11** **Edit or delete one of P's sales after the count** (admin). On-hand moves back by that amount.
- [ ] **R2-12** A sale **dated before** the count day does not lower on-hand; one on the count day does.
- [ ] **R2-13** **Days of cover agree.** The Reorder list's days of cover now uses the run-down on-hand
  (`days_cover_remaining`). But `days_of_supply` on `/api/products` and `/api/stock` (Stock Position, Current stock)
  still comes from the pipeline's workbook-based figure in `Fact_Sales`. After R2-02, compare P's days of cover on
  both screens. If they differ, log it. It is the README's "Known gap" made more visible.
- [ ] **R2-14** **Decisions follow without a pipeline run.** When P's run-down on-hand reaches its ROP, P turns
  "Reorder now". The Reorder Now tile, the Overview's "Reorder N items", and the Advisories count all go up by one.
- [ ] **R2-15** **Same sale entered twice.** If a day's sales are typed into the app *and* that day is later loaded
  from the tally workbook, they are counted twice: in `Fact_Sales`, and now in the on-hand run-down too. Ask the group
  which path the store uses for which days. If both are possible for the same day, log it.
- [ ] **R2-16** **Speed.** `units_out_since` adds a query to every catalog request. Time `/api/products`,
  `/api/stock` and `/api/reorder` (median of 5). The first pass measured 0.53, 0.54 and 0.58 s. Anything over 1 s
  goes in the log.
- [ ] **R2-17** **Wording.** The Overview no longer says "as of the last count". Every on-hand number on Overview,
  Reorder, Plan Ahead and Current stock has the source tooltip.
- [ ] **R2-18** `python -m pytest tests/test_stock_running_balance.py -q` passes (6 tests).

### 3.3 Sidebar names on narrow screens (`5139c9a`)

- [ ] **R3-01** At 720 px wide and below, hovering each icon shows its page name. Each button has an `aria-label`,
  and the current page has `aria-current="page"` (DevTools > Elements).
- [ ] **R3-02** At 721 px and above, the sidebar looks as it did (text labels, no change in layout).
- [ ] **R3-03** Keyboard: Tab through the icon-only sidebar. Focus is visible, and Enter opens the page.
- [ ] **R3-04** Other icon-only controls at 390 px have a name too. Look at the Advisories bell (icon plus a count)
  and "Back to Tally Interface" (icon only). Log any without a tooltip or label.

---

## 4. Re-test the 36 bugs from the first pass

Re-run each one's reproduction from the workbook's Bug Log, then set its Status: **Verified** (fixed and checked),
**Open** (still there), or record the group's decision. None of the code behind these was changed, so expect them
to be open. Confirm, don't assume.

| ID | Sev | Re-test | Fixed when |
|---|---|---|---|
| QA-001 | S1 | `python -m pytest tests/test_gates_can_fail.py` | Priced share ≥ 0.78, or the group records its decision on the floor |
| QA-002 | S2 | `python scripts/verify_data.py` | "BAGS ALL YOU WANT" mapped (group decision; protected file) |
| QA-003 | S2 | `SELECT COUNT(*) FROM Dim_Product WHERE is_hvl=1` | > 0, or the step 3 rule is shown to be right for this data |
| QA-007 | S2 | The three pinned checks | The group approves new expected values |
| QA-022 | S2 | `python scripts/vault.py status` | All ok after `--prefer-local` or `--prefer-vault` is chosen |
| QA-026 | S2 | Sign in with `staff123` / `admin123` | Defaults changed, and a documented way to change a password |
| QA-031 | S2 | Reorder Alerts: look for "provisional" | ROP / safety stock / EOQ labelled provisional |
| QA-037 | S2 | Kill the backend between steps 1 and 3 of a run; restart | Staleness says interrupted and the Tally Interface warns |
| QA-008 | S3 | `git config core.hooksPath` | `.githooks` in every clone |
| QA-012 | S3 | `/api/fsn/sensitivity` p80 vs `Dim_Product` | They agree, or the row says it predates the recency rules |
| QA-014 | S3 | Batch Report screen + CSV subtotals | Peso subtotal per supplier, as in the PDF |
| QA-016 | S3 | `POST /api/tally` and `/api/inventory` with quantity `"inf"` / `"nan"` | 400, not 500 |
| QA-019 | S3 | Tally a quantity of 1,000,000,000,000 | Refused or confirmed |
| QA-021 | S3 | Start the backend with no `ustore.db` | Refuses to start with the message |
| QA-024 | S3 | Power BI refresh on a PC with the driver | Refresh works; the driver is in the setup notes |
| QA-027 | S3 | Tally today's date between 00:00 and 07:59 | Accepted |
| QA-028 | S3 | `python serve.py` on a fresh setup | Starts (requirements installed, or the README says to) |
| QA-030 | S3 | Reorder Alerts at 1366 px | No sideways scroll inside the table |
| QA-034 | S3 | An unreadable `.xlsx` in `rawdata/`, then run | That file is named and skipped; the others are read |
| QA-038 | S3 | Reorder search "Santo Tomas Shirt/UST" | The two items can be told apart |
| QA-039 | S3 | Batch Report, stop backend, switch to Year | An error shows, or the old figures clear |
| QA-004 | S4 | `scripts/verify_rebuild_state.py` | Table exists, or the check is retired by the group |
| QA-005 | S4 | `npm run lint` | 0 errors |
| QA-006 | S4 | `npm run build` | Main chunk < 500 kB |
| QA-011 | S4 | `/api/products/999999/history` | 404 |
| QA-013 | S4 | `/api/fsn/decision-rules?threshold=abc` | 400 |
| QA-015 | S4 | `/api/calendar/2026-13-45`, `/tally?date=2026-13-01`, `/inventory?month=2026-13` | 400 |
| QA-017 | S4 | Same tally entry edited in two browsers | Second save warned or refused |
| QA-020 | S4 | Tally a sale on a closed day | Refused or warned |
| QA-023 | S4 | Run pytest; check `.vault_state.json` and `data/` timestamps | Tests leave the real working copy alone |
| QA-025 | S4 | Change a price, refresh Power BI, compare remittance | Same as the app |
| QA-029 | S4 | Time a full run | README states about 6 minutes |
| QA-032 | S4 | Forecast > Uncategorised | Tag wording doesn't contradict WAPE |
| QA-033 | S4 | Contrast of gold text and tags | ≥ 4.5:1 |
| QA-035 | S4 | README / OPEN_ISSUES #7 vs a run without `rawdata/` | Docs match |
| QA-036 | S4 | 1 wrong admin password, wait > 60 s, then 6 quick | Locks on the 5th quick failure |

QA-009, QA-010 and QA-018 are unused IDs (one finding was withdrawn on review).

**One record from the first pass was wrong and has been corrected in the workbook (2026-11-25).** D8-17 (calendar
banner) was confirmed PASS, then overwritten with FAIL by a later script that looked for the banner with the wrong
selector. D8-19 (staleness banner on screen) used the same wrong selector, so its result is void and it is marked
NOT RUN. Re-do D8-19 by hand: produce each warning case (entries waiting, a stopped run, a failed run) and check the
banner appears in the Tally Interface.

---

## 5. Checks the first pass did not have

These check what must always be true, rather than re-deriving a figure with the code's own formula. Run them after
§3, against the API, and spot-check them on screen.

- [ ] **R5-01** Every item marked "Reorder now" orders at least 1 unit.
- [ ] **R5-02** For every due item, on hand + order ≥ ROP + 30 × daily demand (the order really brings stock up to
  the target).
- [ ] **R5-03** On-hand is never negative, and never above the last count unless a recount raised it.
- [ ] **R5-04** **One number, one value.** On-hand for an item is the same on Reorder, Overview, Plan Ahead and
  Current stock. The order quantity is the same on the Reorder list, What-if, Edit Settings and Plan Ahead. Days of
  cover is the same wherever it appears (R2-13).
- [ ] **R5-05** **Every figure says where it came from** where the design promises it: the on-hand tooltip; the
  provisional label on reorder figures (QA-031); "through <date>" on a month still in progress.
- [ ] **R5-06** **A change reaches every screen it should, without a pipeline run, and no other.** After a sale, a
  count, or a closure: list every screen that should change, and check each one did. Check that a screen that
  shouldn't change (e.g. last week's Batch Report) didn't.
- [ ] **R5-07** **Every icon-only control has a name** at 390 px and at 720 px: a tooltip or `aria-label`
  (generalises R3-04).
- [ ] **R5-08** **A failed request never leaves old figures under a new heading** (generalises QA-039). Stop the
  backend and change a filter or period on each dashboard page.

---

## 6. Full regression pass

The fixes touch the code that serves the Reorder, Overview, Plan Ahead and Inventory screens, so re-run the first
plan in full. Expect the first pass's results except where §1's table says the expectation changed.

| First-plan phase | What to re-run | Watch for |
|---|---|---|
| A | All of it | Matches §2 |
| B | One full run, then a second for B2-01 (identical tables) | The pipeline code did not change: the run should match the first pass |
| C | All endpoints | `/reorder`, `/reorder/:id/whatif`, `/reorder/:id/settings`, `/reorder/season`, `/stock`, `/products`, `/overview/performance` (the changed code paths) |
| D | All screens | D2 Overview, D5 Reorder, D8-11 / D8-12 Inventory, D1-06 sidebar |
| E | All traces | New expected values for E-03 (round up) and E-06 (run-down on-hand) |
| F | F1-01 at 390, 720, 721, 800, 1366 and 1920 px; F2-02 timings; F1-03 keyboard | R2-16 speed |
| G | Only once the ODBC driver is installed (§7) | — |

---

## 7. Finish what the first pass could not

**Not run (9):**

| ID | Needs |
|---|---|
| G-01, G-03, G-05 | Power BI Desktop with the SQLite ODBC driver: refresh, render the 3 pages, try the slicers |
| D6-06 | Browser print preview of the Batch Sales Report |
| D7-02 | A build with a pasted `<iframe>` tag as the Power BI URL, to see the warning |
| D8-16 | Opening the Edit Calendar dialog |
| D8-19 | The staleness banner for each warning case (see §4) |
| D4-06 | An item with no accuracy check, opened on the Forecast page |
| F3-07 | Checking on GitHub whether the repository is public (README B1) |

**Partly done, needing a person (the main ones):**

- **Zoom:** every screen at 125% and 150% (F1-01).
- **Network:** the dashboard from a second computer with `serve.py --host 0.0.0.0` (F3-06).
- **Screen-only paths** tested through the API in the first pass:
  - Custom Range with From after To (D1-08)
  - Top Sellers filter (D2-03)
  - the Classification search count (D3-03)
  - the contributors note on Forecast (D4-03)
  - Forecast supplier filter and hover names (D4-08)
  - Edit/Delete links and Add Item, price and discontinue on the Inventory and Calendar tabs (D8-07, D8-09 to
    D8-15)
- **Power BI totals inside Power BI** (G-04, E-09).

---

## 8. Recording results

- Update the workbook as you go:
  - Checklist **Status** for every first-plan check you re-run
  - Bug Log **Status** and **Owner**
  - a new Checklist row for each `R` check
- A new bug gets the next ID from **QA-040**, with steps to reproduce, expected, actual and evidence, as before.
- A check that needs a group decision (R2-06, R2-07, R2-15, QA-001, QA-002, QA-007, QA-022) is recorded with the
  decision and who made it.

## 9. Exit criteria

1. Every R check in §3 and §5 passes, or has a bug ID.
2. Every one of the 36 bugs in §4 has a status dated in this pass, with evidence.
3. Phase A matches §2, apart from items already in the bug log.
4. No new S1 or S2 bug is open without an owner and a decision.
5. The §7 items are done, or listed with the reason they still can't be.

## 10. Suggested order

| When | Work |
|---|---|
| Day 1 | §0, §2, §3 (the fixes): about half a day in the QA copy |
| Day 2 | §4 (the 36 bugs) and §5 (property checks) |
| Day 3 | §6 regression |
| When the equipment is there | §7 (Power BI PC, a second computer, a person for zoom and print) |
