# Changes made on August 6, 2026 by Gambe

## UST Prototype Design/app
| | |
|**Main.jsx** | replaced placeholder code  | 
|**Powerbi-config.js** | new file for PowerBI embed url | 
|**Powerbi-embed.jsx** | new file for PowerBI embed frontend element |
|**USTore Redesign.html** | added powerbi-config and powerbi-embed into <script> order |

# Initial instructions before changes

## Set up the Power BI dashboard

The frontend's four analytics pages (FSN Classification, Demand Forecast,
Reorder Alerts, Batch Sales Report) embed a published Power BI report via
an iframe. The Dashboard Overview page keeps its own coded charts for now.

The embed URL only exists **after** the `.pbix` report is published to the
Power BI Service. Two methods:

**Method A — Publish to web (free, PUBLIC).** In Power BI Desktop:
`Publish` → sign in → pick a workspace. Then on `app.powerbi.com`, open the
report → `File ▸ Embed report ▸ Publish to web (public)` → `Create embed
code` → copy the URL inside the iframe `src="…"` (looks like
`https://app.powerbi.com/view?r=…`). This makes the report **publicly
viewable and search-indexable** — fine for a capstone demo, a real concern
for live sales data. Revoke anytime under `Settings ▸ Manage embed codes`.
If "Publish to web" is greyed out, the school tenant has disabled it —
publish under a personal Microsoft account, or use Method B.

**Method B — Secure embed (login required).** Requires Power BI Pro (60-day
trial, or via a student Microsoft 365 A1/A3 license). Same publish flow,
then `File ▸ Embed report ▸ Website or portal` → copy that URL. Viewers
must sign in with an account that has report access.

**Where to paste it:** open `app/powerbi-config.js` and set:

```js
window.POWERBI_EMBED_URL = "https://app.powerbi.com/view?r=...";
```

Leave it as an empty string to show the "not configured" placeholder
instead of a broken iframe — this is the default and lets the frontend
ship before the report exists.