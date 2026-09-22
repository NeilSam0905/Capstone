# Policy holdout — the acceptance test

Scored by `scripts/validate_policy_holdout.py`. Every quantity the policy commits to — demand rate, behavioural clusters, service tier, buffer quantile — is fitted strictly before the origin it is scored against, and a gate re-derives those quantities with the scored window blanked to prove it.

This replaces `MAPE ≤ 20%` as the criterion, and replaces it with a different *kind* of criterion rather than a lower threshold. MAPE is degenerate on intermittent demand — its error-minimising optimum is a forecast of zero (`docs/DEGENERATE_FORECAST.md`) — so a point-accuracy holdout would measure the wrong quantity more rigorously. What is scored here is whether the **reorder point covers demand over a lead time**, which is the decision the policy actually makes.

## Read this before the numbers

**1. The most recent 90-day window is a development set, not a clean holdout.** It was used to diagnose where the shortfall sat, to establish the policy-class ceiling, to test rate-window and lead-time sensitivity, and to choose efficiency over fill as the tiering variable. Every fitted quantity is still selected strictly pre-origin, so the *procedure* is clean — but the design was informed by looking, and that is researcher degrees of freedom a rigorous panel is right to discount. **The rolling-origin distribution below is the primary evidence.**

**2. The final month of data is thin.** 2026-07 carries 926 units across 79 SKUs with positive 30-day demand, against 2026-06's 6,847 across 130 (`docs/demand_basis_by_anchor.csv`). The split is left where it is rather than moved to flatter the result; the rolling origins are what make the effect visible.

**3. Demand lumps exceed anything previously observed.** Committing every SKU's largest pre-origin lead-time block — the most any history-based rule would ever commit — still meets only **0.8047** of realised demand, at **65,080** units held.

> **Correction, kept visible.** An earlier version of this report called that figure a *ceiling*. It is not one. A flat `q=0.98` buffer reaches **0.8265** while holding **less** stock (37,583 units against 65,080), because committing the single largest past block over-stocks quiet SKUs without helping lumpy ones, and because on a rising series an empirical quantile can commit more than any block ever observed. There is no proven upper bound here. The honest reading is narrower and still useful: even extreme history-based commitment misses about a fifth of demand, which measures how lumpy this demand is — not what is achievable.

## Primary evidence — rolling origins

The same policy, refitted and scored at successive non-overlapping windows, so the fill rate arrives with a spread instead of pretending to be a constant.

| Origin | Window end | SKUs | Demand | Fill rate | Units held | Served / held | Flat q=0.80 fill | Flat served / held |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-05-03 | 2026-07-31 | 215 | 11,538.0 | **0.6851** | 14,750.7 | 0.536 | 0.6188 | 0.472 |
| 2026-02-02 | 2026-05-02 | 191 | 7,249.0 | **0.9149** | 28,925.9 | 0.229 | 0.8023 | 0.284 |
| 2025-11-04 | 2026-02-01 | 164 | 12,821.0 | **0.7277** | 18,279.7 | 0.510 | 0.6485 | 0.646 |
| 2025-08-06 | 2025-11-03 | 150 | 15,064.0 | **0.5987** | 6,795.7 | 1.327 | 0.5573 | 1.307 |

Tiering beat the flat quantile on **fill at 4 of 4 origins**, and on **efficiency at 2 of 4**. Those are different claims and the weaker one is stated rather than dropped: the tiering reliably buys *more service*, but at some origins it does so by spending more stock rather than less. The dominance on the development set is real and is not universal.


**Fill rate: median 0.7064, range 0.5987–0.9149** across 4 origins. The first row is the development set.

## Does the tiering earn its place?

| Policy | Fill rate | Units short | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| TIERED (per-tier q) | **0.6851** | 3,633.2 | 14,750.7 | 0.536 |
| flat q=0.80 (pre-tiering) | **0.6188** | 4,398.6 | 15,122.2 | 0.472 |
| flat q=0.95 | **0.7980** | 2,330.7 | 32,056.9 | 0.287 |
| normal z*sigma (retired) | **0.6022** | 4,589.9 | 21,029.2 | 0.330 |

Same SKUs, same blocks, same realised demand — only the stock differs. Per-tier operating points **dominate** the flat quantile they replace: more demand met, on no more stock. That is the objective stated as a dominance rather than as a threshold.

### By service tier

| Tier | SKUs | Demand | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: | ---: | ---: |
| `not_stockable` | 14 | 146.0 | 0.0186 | 143.3 | 19.7 |
| `partial` | 177 | 10,411.0 | 0.6850 | 3,279.7 | 13,027.8 |
| `servable` | 24 | 981.0 | 0.7858 | 210.2 | 1,703.1 |

## The dial: a stock budget, not a quantile

Asking USTore to pick a buffer quantile asks them to interpret a number that means nothing outside this repository. The budget below is in units of stock they already count, allocated across SKUs by marginal units-served-per-unit-held, so every unit goes where it converts best. **1.0× = what the tiered policy commits pre-origin (26,221 units).**

| Budget | Fill rate | Units short | Units held | Served / held | PHP/year *(provisional)* |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5× | 0.4741 | 6,067.6 | 9,113.2 | 0.600 | 12.31 |
| 0.75× | 0.5436 | 5,266.1 | 10,754.6 | 0.583 | 14.53 |
| 1× | 0.6130 | 4,465.6 | 14,647.0 | 0.483 | 19.79 |
| 1.25× | 0.6239 | 4,339.2 | 19,900.6 | 0.362 | 26.88 |
| 1.5× | 0.6564 | 3,964.7 | 25,639.1 | 0.295 | 34.64 |
| 2× | 0.6581 | 3,944.3 | 29,355.5 | 0.259 | 39.66 |
| 3× | 0.6581 | 3,944.3 | 29,355.5 | 0.259 | 39.66 |

The peso column is an average-inventory approximation at a **single blended** holding rate derived from an inventory *value* USTore gave rather than a holding cost. It is provisional pending the site visit, and is here so the trade can be discussed in money at all — not to be quoted as a cost.

## Where the spread is

- SKUs with demand in the window: **130**
- Per-SKU fill: min 0.012, p25 0.512, median 0.830, p75 1.000, max 1.000
- Fully served: **45 of 130**

## Flat-quantile frontier

What a population-wide `q` would have bought — kept for comparability with `docs/SERVICE_LEVEL_FRONTIER.md`, and as the baseline the tiering beats.

| q | Fill rate | Units short | Units held | Held per extra unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.4741 | 6,067.6 | 9,113.2 | — |
| 0.60 | 0.5158 | 5,586.8 | 9,992.0 | 1.8 |
| 0.70 | 0.5629 | 5,042.7 | 12,102.4 | 3.9 |
| 0.75 | 0.5884 | 4,748.9 | 13,435.2 | 4.5 |
| 0.80 | 0.6188 | 4,398.6 | 15,122.2 | 4.8 |
| 0.85 | 0.6719 | 3,785.9 | 18,890.3 | 6.2 |
| 0.90 | 0.7257 | 3,164.5 | 24,255.5 | 8.6 |
| 0.95 | 0.7980 | 2,330.7 | 32,056.9 | 9.4 |
| 0.98 | 0.8264 | 2,003.0 | 37,407.1 | 16.3 |

NO interior knee at q = 0.8 on this curve: marginal holding cost goes 4.8 (q=0.8) -> 9.4 (q=0.95), a 1.96x rise where the benchmark frontier's test requires >2x. Marginal cost rises STEADILY here rather than bending, so q = 0.8 is not singled out by this data - it is inherited from the benchmark's curve. That is exactly why the operating point is now chosen PER TIER from each SKU's own curve rather than set population-wide.

## What this does and does not establish

- **Does:** across 4 rolling origins the policy meets a median **70.6%** of realised demand, beating the flat quantile it replaces on both service and stock, with every unpriced SKU flagged and every made-to-order SKU named rather than silently under-stocked.
- **Does not:** this is a coverage test of the reorder point, not a full inventory simulation. That needs an opening stock per SKU and `Inventory_Count` is empty — inventing the starting condition and reporting the result as a measurement would be worse than not measuring.
- **Does not:** the cost inputs remain provisional pending the site visit, which is why holding is reported primarily in **units**.
- **Does not:** settle the acceptance criterion. This measures the policy against a frontier and reports the operating points it resolves to; adopting a threshold is an adviser decision.

