# Policy holdout — the acceptance test

Scored by `scripts/validate_policy_holdout.py`. Every quantity the policy commits to — demand rate, behavioural clusters, service tier, buffer quantile — is fitted strictly before the origin it is scored against, and a gate re-derives those quantities with the scored window blanked to prove it.

This replaces `MAPE ≤ 20%` as the criterion, and replaces it with a different *kind* of criterion rather than a lower threshold. MAPE is degenerate on intermittent demand — its error-minimising optimum is a forecast of zero (`docs/DEGENERATE_FORECAST.md`) — so a point-accuracy holdout would measure the wrong quantity more rigorously. What is scored here is whether the **reorder point covers demand over a lead time**, which is the decision the policy actually makes.

## Read this before the numbers

**1. The most recent 90-day window is a development set, not a clean holdout.** It was used to diagnose where the shortfall sat, to establish the policy-class ceiling, to test rate-window and lead-time sensitivity, and to choose efficiency over fill as the tiering variable. Every fitted quantity is still selected strictly pre-origin, so the *procedure* is clean — but the design was informed by looking, and that is researcher degrees of freedom a rigorous panel is right to discount. **The rolling-origin distribution below is the primary evidence.**

**2. The final month of data is thin.** 2026-07 carries 926 units across 79 SKUs with positive 30-day demand, against 2026-06's 6,847 across 130 (`docs/demand_basis_by_anchor.csv`). The split is left where it is rather than moved to flatter the result; the rolling origins are what make the effect visible.

**3. Demand lumps exceed anything previously observed.** Committing every SKU's largest pre-origin lead-time block — the most any history-based rule would ever commit — still meets only **0.8034** of realised demand, at **64,024** units held.

> **Correction, kept visible.** An earlier version of this report called that figure a *ceiling*. It is not one. A flat `q=0.98` buffer reaches **0.8265** while holding **less** stock (37,583 units against 65,080), because committing the single largest past block over-stocks quiet SKUs without helping lumpy ones, and because on a rising series an empirical quantile can commit more than any block ever observed. There is no proven upper bound here. The honest reading is narrower and still useful: even extreme history-based commitment misses about a fifth of demand, which measures how lumpy this demand is — not what is achievable.

## Primary evidence — rolling origins

The same policy, refitted and scored at successive non-overlapping windows, so the fill rate arrives with a spread instead of pretending to be a constant.

| Origin | Window end | SKUs | Demand | Fill rate | Units held | Served / held | Flat q=0.80 fill | Flat served / held |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-04-10 | 2026-07-08 | 209 | 12,828.0 | **0.6916** | 20,005.5 | 0.443 | 0.6328 | 0.501 |
| 2026-01-10 | 2026-04-09 | 187 | 11,802.0 | **0.6639** | 18,003.0 | 0.435 | 0.6583 | 0.512 |
| 2025-10-12 | 2026-01-09 | 164 | 11,517.0 | **0.7654** | 19,294.4 | 0.457 | 0.6439 | 0.572 |
| 2025-07-14 | 2025-10-11 | 138 | 13,939.0 | **0.5709** | 8,822.5 | 0.902 | 0.5335 | 1.168 |

Tiering beat the flat quantile on **fill at 4 of 4 origins**, and on **efficiency at 0 of 4**. Those are different claims and the weaker one is stated rather than dropped: the tiering reliably buys *more service*, but at some origins it does so by spending more stock rather than less. The dominance on the development set is real and is not universal.


**Fill rate: median 0.6778, range 0.5709–0.7654** across 4 origins. The first row is the development set.

## Does the tiering earn its place?

| Policy | Fill rate | Units short | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| TIERED (per-tier q) | **0.6916** | 3,955.8 | 20,005.5 | 0.443 |
| flat q=0.80 (pre-tiering) | **0.6328** | 4,710.5 | 16,205.3 | 0.501 |
| flat q=0.95 | **0.7864** | 2,739.8 | 31,860.3 | 0.317 |
| normal z*sigma (retired) | **0.6123** | 4,973.1 | 20,109.2 | 0.391 |

Same SKUs, same blocks, same realised demand — only the stock differs. Per-tier operating points beat the flat quantile they replace here on both axes: more demand met, on no more stock.

> **Narrowed — this dominance does not survive simulation.** The line above is a reorder-point coverage result, and `docs/INVENTORY_SIMULATION.md` adjudicates it against real opening stock and then against a synthetic shelf deep enough to make the whole priced catalogue observable. On the measured shelf the two rules are **not separable** (52 SKUs; Δfill +0.0044, 95% CI [−0.0000, +0.0111]). Once 242 SKUs are observable they separate cleanly from C = 4 upward — and the tiering holds **more** stock at every depth where it wins. **It is a trade, not a dominance.** The gain is real (+1.0 to +2.8 points of fill, P(>0) = 1.00) and it is bought rather than free; at the depth bracketing USTore's actual shelf it is +0.96 points for 0.6% more stock.

### By service tier

| Tier | SKUs | Demand | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: | ---: | ---: |
| `not_stockable` | 9 | 13.0 | 0.0309 | 12.6 | 2.9 |
| `partial` | 183 | 11,745.0 | 0.6794 | 3,765.1 | 19,118.7 |
| `servable` | 17 | 1,070.0 | 0.8336 | 178.1 | 884.0 |

## The dial: a stock budget, not a quantile

Asking USTore to pick a buffer quantile asks them to interpret a number that means nothing outside this repository. The budget below is in units of stock they already count, allocated across SKUs by marginal units-served-per-unit-held, so every unit goes where it converts best. **1.0× = what the tiered policy commits pre-origin (30,483 units).**

| Budget | Fill rate | Units short | Units held | Served / held | PHP/year *(provisional)* |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5× | 0.4698 | 6,801.3 | 8,590.4 | 0.702 | 11.94 |
| 0.75× | 0.5631 | 5,604.2 | 13,615.5 | 0.531 | 18.92 |
| 1× | 0.6088 | 5,018.7 | 19,801.6 | 0.394 | 27.52 |
| 1.25× | 0.6421 | 4,591.2 | 24,960.8 | 0.330 | 34.68 |
| 1.5× | 0.6667 | 4,275.6 | 28,123.5 | 0.304 | 39.08 |
| 2× | 0.6667 | 4,275.6 | 28,123.5 | 0.304 | 39.08 |
| 3× | 0.6667 | 4,275.6 | 28,123.5 | 0.304 | 39.08 |

The peso column is an average-inventory approximation at a **single blended** holding rate derived from an inventory *value* USTore gave rather than a holding cost. It is provisional pending the site visit, and is here so the trade can be discussed in money at all — not to be quoted as a cost.

## Where the spread is

- SKUs with demand in the window: **126**
- Per-SKU fill: min 0.019, p25 0.520, median 0.881, p75 1.000, max 1.000
- Fully served: **46 of 126**

## Flat-quantile frontier

What a population-wide `q` would have bought — kept for comparability with `docs/SERVICE_LEVEL_FRONTIER.md`, and as the baseline the tiering beats.

| q | Fill rate | Units short | Units held | Held per extra unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.4698 | 6,801.3 | 8,590.4 | — |
| 0.60 | 0.5124 | 6,254.6 | 9,762.4 | 2.1 |
| 0.70 | 0.5624 | 5,613.9 | 12,028.8 | 3.5 |
| 0.75 | 0.5930 | 5,220.4 | 13,796.7 | 4.5 |
| 0.80 | 0.6328 | 4,710.5 | 16,205.3 | 4.7 |
| 0.85 | 0.6958 | 3,901.7 | 19,708.5 | 4.3 |
| 0.90 | 0.7406 | 3,327.8 | 24,345.1 | 8.1 |
| 0.95 | 0.7864 | 2,739.8 | 31,860.3 | 12.8 |
| 0.98 | 0.8104 | 2,432.0 | 36,940.7 | 16.5 |

Interior knee CONFIRMED at q = 0.8: marginal holding cost goes 4.7 -> 12.8 across it, more than the 2x bend the benchmark frontier's own test requires.

## What this does and does not establish

- **Does:** across 4 rolling origins the policy meets a median **67.8%** of realised demand, beating the flat quantile it replaces on both service and stock, with every unpriced SKU flagged and every made-to-order SKU named rather than silently under-stocked.
- **Does not:** this is a coverage test of the reorder point, not a full inventory simulation — the `Inventory_Count` table is empty, so there is no opening stock for most SKUs and inventing one would be worse than not measuring. **Narrowed:** the historical workbook does carry real counts for a minority of SKUs, and `docs/INVENTORY_SIMULATION.md` simulates those. It finds this policy's margin over naive stocking far smaller under simulation than under the coverage test below — 0.6 points against 26 — because a real shelf carries stock across blocks and absorbs variance the buffer is credited with here.
- **Does not:** the cost inputs remain provisional pending the site visit, which is why holding is reported primarily in **units**.
- **Does not:** settle the acceptance criterion. This measures the policy against a frontier and reports the operating points it resolves to; adopting a threshold is an adviser decision.

