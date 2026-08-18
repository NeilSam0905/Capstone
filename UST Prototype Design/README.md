# USTore Frontend

Vite + React frontend for the USTore demand-forecasting and inventory capstone.

**Phase 1** (done): the UI runs standalone against a local mock data layer
generated from the real pipeline. **Phase 2** (done): the analytics route
embeds a published Power BI report — add the URL and it appears, see below.
**Phase 3** (not started): replace the mock data layer with a real backend, per
`BACKEND_TODO.md`.

## Run it

```bash
npm install
npm run dev
```

That's the one command pair — the app comes up at http://localhost:5173 with the
Tally Interface as the landing view. `npm run build` produces `dist/`,
`npm run lint` runs ESLint.

## Set up the Power BI dashboard

The **Analytics (Power BI)** screen embeds a published Power BI report. Until a
URL is configured it shows a placeholder — the app runs fine without one, so
you can ship and demo before the report exists.

### 1. Where the URL goes

```bash
cp .env.example .env.local     # .env.local is gitignored
```

Then set the one key and restart `npm run dev`:

```
VITE_POWERBI_EMBED_URL=https://app.powerbi.com/view?r=eyJrIjoi…
```

That's the only place it lives — `src/config.js` reads it and nothing
hardcodes it. Paste **only the URL**, not the whole `<iframe>` tag Power BI
gives you; the screen detects that mistake and says so rather than rendering a
dead frame.

### 2. How to get the URL (a manual step, done once, by a human)

The URL exists only **after** the `.pbix` is published to the Power BI Service.
Building the report itself — the five views: stock status, FSN, forecast, batch
sales report, calendar cards — is a separate task from this frontend.

**Method A — Publish to web (free, PUBLIC).** In Power BI Desktop →
**Publish** → sign in → pick a workspace. Then on
[app.powerbi.com](https://app.powerbi.com) open the report →
**File ▸ Embed report ▸ Publish to web (public)** → **Create embed code** →
copy the URL inside the iframe's `src="…"`. It looks like
`https://app.powerbi.com/view?r=…`.

> ⚠️ This makes the report **publicly viewable and search-indexable by anyone
> with the link**. Acceptable for a capstone demo; a genuine problem for live
> client sales data, which this project is under a data-sharing agreement for.
> Revoke anytime under **Settings ▸ Manage embed codes**.
> If "Publish to web" is greyed out, the UST tenant has disabled it — publish
> under a personal Microsoft account, or use Method B.

**Method B — Secure embed (login required).** Needs **Power BI Pro** (60-day
trial, or possibly via a student Microsoft 365 A1/A3 licence). Same publish
flow, then **File ▸ Embed report ▸ Website or portal** → copy that URL
(`https://app.powerbi.com/reportEmbed?reportId=…`). Viewers must sign in with
an account that has access, so **the demo machine has to be logged in** — worth
rehearsing before a defence.

### 3. Not implemented: Power BI Embedded

**Power BI Embedded / Azure "app-owns-data"** (a service principal minting
embed tokens so viewers need no Microsoft account) is the production upgrade
path, not part of this project. It requires a backend to mint tokens and a paid
Azure capacity. Noted here so nobody assumes the current embed does it.

## Where the data comes from

Nothing in this app is invented. The fixtures under
`src/services/fixtures/` are generated from `ustore.db` at the repo root:

```bash
python scripts/generate_fixtures.py
```

Re-run that after re-running the ETL pipeline. If `ustore.db` doesn't exist,
rebuild it first — see the repo README (`scripts/create_schema.py` →
`scripts/populate_dim_date.py` → `scripts/step1` → `scripts/proportional_allocation` → `scripts/step2` → `scripts/step3`, run from the repo root).

Field names mirror the star schema in `create_schema.py` (`product_id`,
`item_name`, `unit_price_php`, `calendar_date`, `quantity_sold`), so the
fixtures already have the shape the Phase 3 API will return.

## Design system

The look comes from the redesign prototype kept in `design-reference/`, not
from this app's original Tailwind styling.
`src/styles/redesign.css` **is** that prototype's `styles.css`, copied verbatim
and then extended at the bottom for the screens the prototype never built
(pending states, sortable tables, notices, the batch report). Screens use its
class vocabulary — `.card`, `.card__pad`, `.kpi`, `.tbl`, `.tag`, `.banner`,
`.field`, `.btn` — rather than utility classes.

Also ported from the prototype: the sidebar/topbar markup
(`design-reference/chrome.jsx`),
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
