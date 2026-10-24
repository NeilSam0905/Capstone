# Dashboard changes after the consultation (2026-10-24)

Frontend only. No backend, database, pipeline or test changes.

## Why

The consultation on the dashboard gave three comments:

- The hero banner should hold the modules for the decision we want to support.
- Every other module should be checked for whether it supports decision-making.
- The dashboard should help users from one look, without explanation.

The suggested fix was to go from **general to detailed**: the top of the page gives the decision at a
glance (what to reorder), and the detail comes further down. Explanations that a reader only needs
once move behind an info (ⓘ) button instead of taking space on the page.

## Dashboard Overview

Rebuilt top to bottom:

| # | Section | What it answers | Type |
|---|---|---|---|
| 1 | **Hero banner**: "Reorder 50 items", units to order, how many items have no stock left, with **Categories** and **Suppliers** lists and an Open Reorder Alerts button | What do we order now, what kind of stock, and from whom? | Prescriptive |
| 2 | **Expected Demand · 30 Days** tile | How much will the store sell? | Predictive |
| 2 | **Items Without an Inventory Count** tile | Which items can the system not check yet? | Descriptive (action: count them) |
| 2 | **Next on the Calendar** tile | What should the next order plan around? | Descriptive |
| 3 | **Order These First**: the 8 most urgent items with on hand and order quantity | Which items first? | Prescriptive |
| 3 | **What to Do With Each Group**: Keep in stock / Promote / Review / Clear out with counts | What to do with each FSN group? | Prescriptive |
| 4 | **Upcoming Event Advisories** (unchanged) | What should we prepare for? | Prescriptive |
| 5 | **Sales Detail**: Monthly Units Sold, and Best Sellers with each item's stock status | What sold, and when? | Descriptive |

Removed, because they only count things and support no decision:

- the **Total Units Sold** and **Products** tiles;
- the **Units by Category** donut.

The products list is still one click away: **Browse all products** on the Best Sellers card.

Notes:

- The hero's footnote says how much of the answer rests on real stock figures: "Only 76 of 268 items
  have an inventory count (last counted April 2026). Count the other 192 to include them."
- Every number follows the topbar's Supplier and Category filters, and the reorder figures match the
  Reorder Alerts page (same `/api/reorder` data, same urgency order).
- The Categories list joins `/api/reorder` items to their category from `/api/products`.

## FSN Classification

- The **Decision Rules** card is gone from the page. The rules now open from a gold **Decision rules ⓘ**
  button in the header of the FSN Classification Item List, beside the product count, because they
  are the key to that list's Action column.
- The panel keeps everything the card had: the explanation of each rule, the **Show N items** links
  (which filter the list and close the panel) and the Review threshold setting.
- **Best Sellers** now takes the full width.

## Demand Forecast

- **What this means**: the paragraph and the "What to do" line moved behind an ⓘ. The row keeps the
  label and the **Open Reorder Alerts** button. The yellow "These 30 days have already passed…"
  warning stays visible, because it changes how every number on the page should be read.
- New ⓘ beside the **Category** dropdown: what the category forecast includes. It shows how many items
  are fast, slow and non-moving, and every item in the category with its class and units sold, best
  sellers first. With a supplier filter on, it says only that supplier's items are listed while the
  forecast still covers every supplier.

## New shared component: `InfoTip`

`UST Prototype Design/src/components/InfoTip.jsx`. An ⓘ button that opens an explanation panel.

- Opens on **click**, not hover, so it works on touch screens.
- Closes on a second click, a click outside, Escape, scrolling the page, or resizing the window.
- The panel is portalled to `<body>` with fixed positioning, so no card or scrolling area clips it.
  It opens below the button, or above when there is no room, and stays inside the window.
- `text` adds a visible label and turns the button into a gold pill (used for **Decision rules**).

```jsx
<InfoTip label="What this forecast means" width={400}>
  <p>Explanation…</p>
</InfoTip>

<InfoTip text="Decision rules" label="Decision rules" width={460}>…</InfoTip>
```

## Files

| File | Change |
|---|---|
| `src/pages/Overview.jsx` | Rebuilt as above; the products modal is unchanged |
| `src/pages/Classification.jsx` | Decision Rules card moved into an InfoTip; Best Sellers full width |
| `src/pages/Forecast.jsx` | "What this means" behind an InfoTip; category contents InfoTip |
| `src/components/InfoTip.jsx` | New |
| `src/styles/redesign.css` | Hero, decision rows, InfoTip and list styles, appended at the end |

## Checked

- `eslint` on every changed file: no new problems. The one error it reports
  (`react-hooks/set-state-in-effect` in the Overview's products modal) was already there and that code
  is unchanged.
- Vite compiles every changed file.
- Driven in Edge (headless) at 1600, 1100 and 420 px wide: every page loads with no browser errors,
  each ⓘ opens and closes, and **Show 142 items** in the Decision rules panel filters the list.

## What the new screens make visible

These are data findings, not code problems. They are worth knowing before the defense.

- **Inventory counts are thin.** 82 of 520 products have one (5 of 30 fast-moving), and the latest is
  from April 2026. None of the top 10 best sellers has a count, so none of them can ever show up as
  "reorder now". The app's Monthly Inventory Count has no entries yet.
- **Two "reorder now" items have a suggested order of 0** (Bookmarks, Baller). They sell about one unit
  every four to six months, so the reorder point rounds to almost nothing. The Reorder Alerts page
  counts them the same way. Whether they should count is a backend rule for the group to decide.
- **The forecast window (August 2026) has already passed.** The sales data ends on 31 July 2026.
- **Split item histories show in the category ⓘ**: for example the old "UST T-Shirt Santo Tomas…"
  listing (6,637 units, Slow) and its re-listed version (1,417 units, Fast).

## Open

- The Overview's **What to Do With Each Group** card still shows a one-line hint per row. Keep it, or
  move the hints behind an ⓘ to match the FSN page?
- Move the passed-window warning on Demand Forecast into the ⓘ too, or keep it visible?
