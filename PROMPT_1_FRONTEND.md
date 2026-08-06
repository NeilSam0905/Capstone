# Claude Code Prompt 1/3 — USTore Frontend (standalone, mock data)

> **Sequence:** this is **Phase 1 of 3**. Do this first, then `PROMPT_2_POWERBI.md`
> (embed the dashboard), then `PROMPT_3_BACKEND.md` (replace mocks with a real server).
> **Depends on:** nothing — start here.
> Edit the **bracketed path** before pasting.

---

You are working on **USTore**, a Business Analytics capstone: a demand-forecasting and inventory
dashboard for the University of Santo Tomas merchandise store. There is an **existing frontend design
that is already coded but mostly non-functional**. This pass is **frontend-only**: make the existing UI
work as a standalone frontend with a mock data layer. **Do NOT build a backend** (deferred to Phase 3)
and **do NOT embed Power BI yet** (Phase 2) — just leave clean seams for both.

## 0. Before writing any code — orient yourself

1. Read `PROJECT_CONTEXT.md` and `PROJECT_LOG.md` — full spec, data model, constraints.
   **Treat `PROJECT_CONTEXT.md` as the source of truth.**
2. Explore the existing frontend at `[PATH TO YOUR FRONTEND CODE]`. Determine: the framework
   (React / Vue / plain HTML-CSS-JS) and structure; which screens exist; for each, whether it's
   static/mocked or partially wired; and how it currently expects data.
3. Note the data files available for realistic fixtures: `USTore_sales_long_allocated.csv`,
   `calendar_ranges_2023_2026.csv`, `USTore_inventory_excel_long.csv`.
4. **Report what you found — the stack, the working vs. broken screens, and your plan — before making
   large changes.** Do not rewrite the existing design.

## 1. The one constraint you must never violate (BIR compliance)

The tallying tool is an **INTERNAL INVENTORY COUNTING TOOL ONLY.** By law (BIR) and university policy it
must **NOT** process payments, act as a checkout, compute customer-facing totals/change, or generate
receipts. It records **unit counts** (what sold, how many, when, which supplier). Unit prices may be
**stored/displayed as reference data** for internal supplier-remittance reporting only. If you find
yourself building a cart, checkout, "total to pay," or receipt — **stop**; that is out of scope and
non-compliant.

## 2. Stack (this pass)

- **Keep the existing frontend framework and design.** React/Vue → run its dev server; plain HTML/JS →
  serve statically. Do not port to a new framework, do not restyle/restructure.
- **No backend, no database.** Data comes from a local mock data layer (§3).
- Runnable with one documented command; update the `README` with run instructions.

## 3. Mock data layer (swappable — this is the key architectural rule)

- Create **one data-access module** (e.g. `dataService` / `api.js`) that **every screen** calls. No
  screen may access data directly. In Phase 3 only this module changes to call the real API.
- Back it with **local JSON fixtures**. **Generate realistic fixtures from the CSVs** (a small one-off
  script is fine) so lists and stock-status numbers look real. **Shape fixtures to mirror the Star
  Schema in `PROJECT_CONTEXT.md` §6** (`Fact_Sales`, `Dim_Product`, `Dim_Date`, `Event_Log`) so field
  names already match the future backend.
- **Tallying persistence:** hold newly entered tallies, closures, and event flags in client-side state
  (in-memory; optionally `localStorage` so a refresh doesn't lose them). **Mark this clearly as
  temporary** (`// TODO: replace with backend API`) — it is throwaway state, not the real store.

## 4. Make the Tallying Interface work (client-side)

- **Add a sales entry:** item, quantity, date, supplier, and **`transaction_type`** (required;
  distinguishes **SALE** from non-sale removals **DAMAGED / PROMO / TRANSFER**). Appends to state and
  shows in a recent-entries list.
- **Field-level validation:** reject null item/quantity/date/transaction_type; quantity must be a
  positive integer; show clear inline errors.
- **Store Closure / Suspension toggle:** tag a date as closed → sets `is_store_closed` for that date in
  mock state (kept separate from event flagging).
- **Event flagging:** mark any date as an event with label + description → adds an `Event_Log` entry in
  mock state and marks `is_event_day` for that date.
- Provide read/list views so entered data shows back up (recent entries, entries by date).

## 5. The dashboard section (placeholder only this pass)

Your design likely has dashboard/analytics screens. **The analytics dashboard will be Power BI, embedded
in Phase 2.** For now:
- Leave the dashboard route/section in place with a **clear placeholder** ("Dashboard — Power BI embed
  configured in Phase 2").
- **Do not rebuild charts in code and do not fabricate forecast/FSN numbers.** If a coded view needs
  analytics that don't exist yet, show a "pending" state.
- If the design has its own coded chart screens, **leave them as-is for now and note them** — whether to
  keep or replace them with the Power BI embed is decided in Phase 2. Don't delete design work.

## 6. How to work (guardrails)

- **Preserve the existing design.** Touch frontend files only to make them function (swap mock data in,
  fix broken handlers). No restyling.
- **Work in small, verifiable steps:** (1) app runs + navigation works; (2) mock data layer + fixtures;
  (3) tallying entry + validation; (4) closure toggle + event flag; (5) remaining views render from
  mock data + dashboard placeholder. Run and test after each.
- **Leave backend seams:** all data access behind the one module; annotate temporary persistence and any
  stubbed calls with `// TODO: backend`. Write **`BACKEND_TODO.md`** listing exactly what Phase 3 must
  implement to replace the mocks (endpoints, persistence, validation to mirror server-side).
- **Ask before** any framework change, large refactor, deleting existing screens, or touching the
  ETL/analytics scripts (`ustore_tbs_to_csv.py`, `proportional_allocation.py`, etc. — leave alone).
- Re-read §1 if any feature starts to feel like a POS.

## 7. Definition of done (this pass)

- One documented command runs the frontend; navigation and all screens render without errors.
- **Tallying:** I can add a sale (with transaction_type), get validation errors on bad input, and see it
  listed; I can toggle a date closed and flag an event, both reflected in mock state.
- Every screen reads through the single mock data layer (shaped to the real schema). Client-side
  persistence clearly marked temporary.
- Dashboard section shows a Phase-2 placeholder; no fabricated analytics anywhere.
- No checkout, payment, customer total, or receipt exists anywhere.
- `BACKEND_TODO.md` written; you've reported the existing stack, what you wired, and what remains.

Start by reading `PROJECT_CONTEXT.md` and exploring `[PATH TO YOUR FRONTEND CODE]`, then give me your
findings and plan before building.
