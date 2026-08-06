# USTore Frontend

Vite + React + Tailwind frontend for the USTore demand-forecasting and inventory
capstone. This is **Phase 1**: the UI runs standalone against a local mock data
layer generated from the real pipeline. There is no backend yet (Phase 3) and no
Power BI embed yet (Phase 2).

## Run it

```bash
npm install
npm run dev
```

That's the one command pair — the app comes up at http://localhost:5173 with the
Tally Interface as the landing view. `npm run build` produces `dist/`,
`npm run lint` runs ESLint.

## Where the data comes from

Nothing in this app is invented. The fixtures under
`src/services/fixtures/` are generated from `ustore.db` at the repo root:

```bash
python scripts/generate_fixtures.py
```

Re-run that after re-running the ETL pipeline. If `ustore.db` doesn't exist,
rebuild it first — see the repo README (`create_schema.py` →
`populate_dim_date.py` → `step1` → `proportional_allocation` → `step2` → `step3`).

Field names mirror the star schema in `create_schema.py` (`product_id`,
`item_name`, `unit_price_php`, `calendar_date`, `quantity_sold`), so the
fixtures already have the shape the Phase 3 API will return.

## Design system

The look comes from the redesign prototype in the sibling folder
(`UST Prototype Design/app/`), not from this app's original Tailwind styling.
`src/styles/redesign.css` **is** that prototype's `styles.css`, copied verbatim
and then extended at the bottom for the screens the prototype never built
(pending states, sortable tables, notices, the batch report). Screens use its
class vocabulary — `.card`, `.card__pad`, `.kpi`, `.tbl`, `.tag`, `.banner`,
`.field`, `.btn` — rather than utility classes.

Also ported from the prototype: the sidebar/topbar markup (`app/chrome.jsx`),
the stroke icon set (`src/components/Icon.jsx`) and the hand-rolled SVG charts
(`src/components/charts.jsx`). The design tokens are set once in `main.jsx` as
`data-` attributes on `<html>`: gold accent, dark chrome, regular density, soft
corners. The prototype drove those from a Tweaks panel; here they are fixed to
the approved direction. Changing the palette means changing those attributes or
the `:root` block — not editing screens.

Because the whole UI now uses that stylesheet, Tailwind, PostCSS, Recharts and
lucide-react were removed from `package.json` — nothing imported them any more.
React and Vite are the only runtime dependencies left.

## Architecture — the one rule

**Every screen reads through `src/services/dataService.js`. No screen touches a
fixture, a JSON file, or the tally store directly.** In Phase 3 only that file
changes: each function body becomes a `fetch()`. The service is already async
for exactly that reason, so the screens handle loading states today.

```
src/
  services/
    dataService.js     ← the only data access point in the app
    tallyStore.js      ← TEMPORARY localStorage persistence (see below)
    fixtures/*.json    ← generated from ustore.db, not hand-written
  hooks/useData.js     ← small async-read hook the screens use
  pages/               ← unchanged design, now reading through the service
  components/          ← unchanged, plus Pending.jsx
```

`BACKEND_TODO.md` lists exactly what Phase 3 must implement.

## What is real and what is pending

| Screen | State |
|---|---|
| Tally Interface | Fully working: entry with validation, closure toggle, event flagging, recent-entries and by-date views |
| Dashboard Overview | Real — units, categories, top products, FSN split |
| FSN Classification | Real — ADUS, HVL, the 75/80/85 sensitivity table |
| Batch Sales Report | Real — per-supplier quantities and remittance line totals |
| Reorder Alerts | Stock position is real; ROP/EOQ/alerts show **pending** (`Dim_Parameters` is empty) |
| Demand Forecast | Observed history is real; the forecast shows **pending** (`Result_Forecast` doesn't exist yet) |
| Analytics (Power BI) | Placeholder route for the Phase 2 embed |

A screen shows a "pending" card whenever `meta.json`'s `available` block says the
pipeline hasn't produced that output. **Do not fill those in client-side** — a
number shown here ends up quoted in Chapter 4.

## Temporary persistence

Tallies, closures and event flags entered in the UI are held in `localStorage`
via `tallyStore.js` and marked "this session" in the recent-entries list. This is
**not the system of record** — it is per-browser, unauthenticated and disappears
when someone clears their storage. It exists so a demo survives a page refresh.
Phase 3 replaces it entirely.

## Scope constraint (BIR)

This is an **internal inventory counting tool**. It records unit counts — what
sold, how many, when, which supplier. It must never process payments, act as a
checkout, compute a customer total or change due, or generate a receipt. Unit
prices appear only as reference data for internal supplier-remittance reporting.
If a feature starts to look like a point of sale, it is out of scope.
