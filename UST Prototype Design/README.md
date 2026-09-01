# USTore Frontend

Vite + React frontend for the USTore demand-forecasting and inventory capstone.

**Phase 1** (done): the UI, standalone against a mock data layer generated
from the pipeline. **Phase 2** (done): the analytics route embeds a
published Power BI report — add the URL and it appears, see below.
**Phase 3** (done): the mock data layer is gone; every screen reads and
writes a real Flask + SQLite API (`../backend`) against `ustore.db`.

## Run it

Two servers, in two terminals, both from the repo root (`Capstone/`):

```bash
# Terminal 1 — backend
cd backend
pip install -r requirements.txt
python app.py                      # http://127.0.0.1:5000

# Terminal 2 — frontend
cd "UST Prototype Design"
npm install
npm run dev                        # http://localhost:5173
```

The frontend dev server proxies `/api/*` to the Flask backend (see
`vite.config.js`), so open only `http://localhost:5173` — no CORS setup
needed. `npm run build` produces `dist/` (a static bundle; it still needs
the backend running somewhere and reachable at `/api` to show real data).
`npm run lint` runs ESLint.

If `http://127.0.0.1:5000/api/meta` doesn't return JSON, the frontend
will show a **"Connection problem"** banner instead of spinning forever —
that means the backend isn't running or `ustore.db` doesn't exist yet
(see below).

## Where the data comes from

Nothing in this app is invented, and nothing reads a CSV or a JSON
fixture — every screen reads and writes `../ustore.db` through the
backend API, live, on every request. `backend/catalog.py` computes ADUS,
FSN sensitivity, stock position, etc. straight from `Fact_Sales` /
`Dim_Product` / `Dim_Date` on each call; nothing is cached to a file.

If `ustore.db` doesn't exist (or is stale relative to the current
schema — check `python -c "import sqlite3; sqlite3.connect('../ustore.db').execute('SELECT 1 FROM Result_Prescriptive')"`
from `backend/`), rebuild it from the repo root:

```bash
python scripts/create_schema.py
python scripts/populate_dim_date.py
python scripts/step1_apply_mapping.py
python scripts/proportional_allocation.py
python scripts/step2_load_fact_sales.py
python scripts/step3_fsn_classification.py
python scripts/step5a_set_lead_times.py
python scripts/step5_prescriptive.py
python scripts/verify_data.py        # should print "All data verification checks passed."
```

`scripts/step4_forecast_model.py` was left out of that list back when it
needed `cmdstan` for Prophet. It doesn't any more — it forecasts with a
rolling mean and finishes in seconds, so you can add it to the sequence
above. Its absence remains a documented, handled state: skip it and the
Demand Forecast screen shows a "pending" card rather than a number, and
`/api/meta`'s `available.forecast` flag stays `false` until it's run.

Field names mirror the star schema in `scripts/create_schema.py`
(`product_id`, `item_name`, `unit_price_php`, `calendar_date`,
`quantity_sold`), and every API response uses those names directly.

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

**Every screen reads and writes through `src/services/dataService.js`. No
screen calls `fetch` itself, and none of them knows the API's URL shape.**
Every function in that file is a thin wrapper around one backend endpoint
(see `backend/app.py`); a screen calling `getProducts(filters)` has no idea
whether that resolves from a fixture, an API, or anything else — which is
exactly what let Phase 3 swap the whole data layer without touching a
single page component.

```
src/
  services/
    dataService.js   ← the only data access point in the app; calls /api/*
  hooks/useData.js    ← async-read hook: { data, loading, error }
  components/
    ErrorBanner.jsx   ← shown when the backend is unreachable
    Pending.jsx       ← shown when the pipeline hasn't produced a number yet
  pages/              ← unchanged design, reading through the service
```

`backend/README.md` documents the API contract from the server side.

## What is real and what is pending

| Screen | State |
|---|---|
| Tally Interface | Fully working: entry with validation, closure toggle, event flagging, recent-entries and by-date views — every write is a real `Fact_Sales` / `Event_Log` / `Closure_Log` row |
| Dashboard Overview | Real — units, categories, top products, FSN split, reorder-now count |
| FSN Classification | Real — ADUS, HVL, the 75/80/85 sensitivity table |
| Batch Sales Report | Real — per-supplier quantities and remittance line totals |
| Reorder Alerts | Real — stock position, and ROP/Safety Stock/EOQ under both ordering-cost scenarios, all explicitly flagged **provisional** (`Dim_Parameters` holds estimates pending the USTore site visit, not confirmed figures) |
| Demand Forecast | Observed history is real; the forecast itself shows **pending** (Prophet/`cmdstan` hasn't been run — see above) |
| Analytics (Power BI) | Embeds the published report once configured; placeholder otherwise |

A screen shows a "pending" card whenever `/api/meta`'s `available` block
says the pipeline hasn't produced that output, and a Reorder Alerts figure
is explicitly labelled provisional rather than presented as final — a
number shown here ends up quoted in Chapter 4.

## Persistence

Every tally entry, store closure and event flag is a real write to
`ustore.db`, validated server-side (`backend/validation.py`) independently
of the client-side check in `dataService.js` — the client copy is a
convenience, not the guarantee. There is no browser-local fallback state
any more; if the backend is unreachable, writes fail with the same
`{ ok: false, errors }` shape the forms already render inline, and reads
show the connection-error banner instead of a permanent spinner.

## Scope constraint (BIR)

This is an **internal inventory counting tool**. It records unit counts — what
sold, how many, when, which supplier. It must never process payments, act as a
checkout, compute a customer total or change due, or generate a receipt. Unit
prices appear only as reference data for internal supplier-remittance reporting.
If a feature starts to look like a point of sale, it is out of scope.
