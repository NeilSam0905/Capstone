# Policy holdout — the acceptance test

Scored by `scripts/validate_policy_holdout.py`. Every quantity the policy commits to — demand rate, behavioural clusters, service tier, buffer quantile — is fitted strictly before the origin it is scored against, and a gate re-derives those quantities with the scored window blanked to prove it.

This replaces `MAPE ≤ 20%` as the criterion, and replaces it with a different *kind* of criterion rather than a lower threshold. MAPE is degenerate on intermittent demand — its error-minimising optimum is a forecast of zero (`docs/DEGENERATE_FORECAST.md`) — so a point-accuracy holdout would measure the wrong quantity more rigorously. What is scored here is whether the **reorder point covers demand over a lead time**, which is the decision the policy actually makes.

## Read this before the numbers

**1. The most recent 90-day window is a development set, not a clean holdout.** It was used to diagnose where the shortfall sat, to establish the policy-class ceiling, to test rate-window and lead-time sensitivity, and to choose efficiency over fill as the tiering variable. Every fitted quantity is still selected strictly pre-origin, so the *procedure* is clean — but the design was informed by looking, and that is researcher degrees of freedom a rigorous panel is right to discount. **The rolling-origin distribution below is the primary evidence.**

**2. The final month of data is thin.** 2026-07 carries 926 units across 79 SKUs with positive 30-day demand, against 2026-06's 6,847 across 130 (`docs/demand_basis_by_anchor.csv`). The split is left where it is rather than moved to flatter the result; the rolling origins are what make the effect visible.

**3. Demand lumps exceed anything previously observed.** Committing every SKU's largest pre-origin lead-time block — the most any history-based rule would ever commit — still meets only **0.7920** of realised demand, at **61,382** units held.

> **Correction, kept visible.** An earlier version of this report called that figure a *ceiling*. It is not one. A flat `q=0.98` buffer reaches **0.8265** while holding **less** stock (37,583 units against 65,080), because committing the single largest past block over-stocks quiet SKUs without helping lumpy ones, and because on a rising series an empirical quantile can commit more than any block ever observed. There is no proven upper bound here. The honest reading is narrower and still useful: even extreme history-based commitment misses about a fifth of demand, which measures how lumpy this demand is — not what is achievable.

## Primary evidence — rolling origins

The same policy, refitted and scored at successive non-overlapping windows, so the fill rate arrives with a spread instead of pretending to be a constant.

| Origin | Window end | SKUs | Demand | Fill rate | Units held | Served / held | Flat q=0.80 fill | Flat served / held |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-05-03 | 2026-07-31 | 215 | 16,393.0 | **0.6403** | 12,158.3 | 0.863 | 0.5874 | 0.762 |
| 2026-02-02 | 2026-05-02 | 191 | 7,249.0 | **0.9149** | 28,925.9 | 0.229 | 0.8023 | 0.284 |
| 2025-11-04 | 2026-02-01 | 164 | 12,821.0 | **0.7277** | 18,279.7 | 0.510 | 0.6485 | 0.646 |
| 2025-08-06 | 2025-11-03 | 150 | 15,064.0 | **0.5987** | 6,795.7 | 1.327 | 0.5573 | 1.307 |

Tiering beat the flat quantile on **fill at 4 of 4 origins**, and on **efficiency at 2 of 4**. Those are different claims and the weaker one is stated rather than dropped: the tiering reliably buys *more service*, but at some origins it does so by spending more stock rather than less. The dominance on the development set is real and is not universal.


**Fill rate: median 0.6840, range 0.5987–0.9149** across 4 origins. The first row is the development set.

## Does the tiering earn its place?

| Policy | Fill rate | Units short | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| TIERED (per-tier q) | **0.6403** | 5,895.8 | 12,158.3 | 0.863 |
| flat q=0.80 (pre-tiering) | **0.5874** | 6,763.2 | 12,631.7 | 0.762 |
| flat q=0.95 | **0.7774** | 3,648.7 | 28,519.9 | 0.447 |
| normal z*sigma (retired) | **0.5844** | 6,813.6 | 18,433.7 | 0.520 |

Same SKUs, same blocks, same realised demand — only the stock differs. Per-tier operating points beat the flat quantile they replace here on both axes: more demand met, on no more stock.

> **Narrowed — this dominance does not survive simulation.** The line above is a reorder-point coverage result, and `docs/INVENTORY_SIMULATION.md` adjudicates it against real opening stock and then against a synthetic shelf deep enough to make the whole priced catalogue observable. On the measured shelf the two rules are **not separable** (52 SKUs; Δfill +0.0044, 95% CI [−0.0000, +0.0111]). Once 242 SKUs are observable they separate cleanly from C = 4 upward — and the tiering holds **more** stock at every depth where it wins. **It is a trade, not a dominance.** The gain is real (+1.0 to +2.8 points of fill, P(>0) = 1.00) and it is bought rather than free; at the depth bracketing USTore's actual shelf it is +0.96 points for 0.6% more stock.

### By service tier

| Tier | SKUs | Demand | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: | ---: | ---: |
| `not_stockable` | 14 | 225.0 | 0.0152 | 221.6 | 19.0 |
| `partial` | 177 | 14,772.0 | 0.6409 | 5,304.9 | 10,692.1 |
| `servable` | 24 | 1,396.0 | 0.7355 | 369.3 | 1,447.2 |

## The dial: a stock budget, not a quantile

Asking USTore to pick a buffer quantile asks them to interpret a number that means nothing outside this repository. The budget below is in units of stock they already count, allocated across SKUs by marginal units-served-per-unit-held, so every unit goes where it converts best. **1.0× = what the tiered policy commits pre-origin (26,221 units).**

| Budget | Fill rate | Units short | Units held | Served / held | PHP/year *(provisional)* |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5× | 0.4360 | 9,246.0 | 7,436.6 | 0.961 | 10.05 |
| 0.75× | 0.4989 | 8,213.7 | 8,847.2 | 0.925 | 11.95 |
| 1× | 0.5799 | 6,886.0 | 12,212.3 | 0.778 | 16.50 |
| 1.25× | 0.6025 | 6,516.3 | 17,222.8 | 0.573 | 23.27 |
| 1.5× | 0.6410 | 5,885.2 | 22,704.6 | 0.463 | 30.67 |
| 2× | 0.6423 | 5,863.4 | 26,419.6 | 0.399 | 35.69 |
| 3× | 0.6423 | 5,863.4 | 26,419.6 | 0.399 | 35.69 |

The peso column is an average-inventory approximation at a **single blended** holding rate derived from an inventory *value* USTore gave rather than a holding cost. It is provisional pending the site visit, and is here so the trade can be discussed in money at all — not to be quoted as a cost.

## Where the spread is

- SKUs with demand in the window: **132**
- Per-SKU fill: min 0.008, p25 0.449, median 0.769, p75 0.984, max 1.000
- Fully served: **31 of 132**

## Flat-quantile frontier

What a population-wide `q` would have bought — kept for comparability with `docs/SERVICE_LEVEL_FRONTIER.md`, and as the baseline the tiering beats.

| q | Fill rate | Units short | Units held | Held per extra unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.4360 | 9,246.0 | 7,436.6 | — |
| 0.60 | 0.4768 | 8,576.2 | 8,126.4 | 1.0 |
| 0.70 | 0.5269 | 7,754.9 | 9,959.7 | 2.2 |
| 0.75 | 0.5562 | 7,275.1 | 11,106.4 | 2.4 |
| 0.80 | 0.5874 | 6,763.2 | 12,631.7 | 3.0 |
| 0.85 | 0.6433 | 5,846.7 | 16,096.1 | 3.8 |
| 0.90 | 0.6996 | 4,924.1 | 21,160.1 | 5.5 |
| 0.95 | 0.7774 | 3,648.7 | 28,519.9 | 5.8 |
| 0.98 | 0.8057 | 3,185.2 | 33,734.4 | 11.2 |

NO interior knee at q = 0.8 on this curve: marginal holding cost goes 3.0 (q=0.8) -> 5.8 (q=0.95), a 1.93x rise where the benchmark frontier's test requires >2x. Marginal cost rises STEADILY here rather than bending, so q = 0.8 is not singled out by this data - it is inherited from the benchmark's curve. That is exactly why the operating point is now chosen PER TIER from each SKU's own curve rather than set population-wide.

## What this does and does not establish

- **Does:** across 4 rolling origins the policy meets a median **68.4%** of realised demand, beating the flat quantile it replaces on both service and stock, with every unpriced SKU flagged and every made-to-order SKU named rather than silently under-stocked.
- **Does not:** this is a coverage test of the reorder point, not a full inventory simulation — the `Inventory_Count` table is empty, so there is no opening stock for most SKUs and inventing one would be worse than not measuring. **Narrowed:** the historical workbook does carry real counts for a minority of SKUs, and `docs/INVENTORY_SIMULATION.md` simulates those. It finds this policy's margin over naive stocking far smaller under simulation than under the coverage test below — 0.6 points against 26 — because a real shelf carries stock across blocks and absorbs variance the buffer is credited with here.
- **Does not:** the cost inputs remain provisional pending the site visit, which is why holding is reported primarily in **units**.
- **Does not:** settle the acceptance criterion. This measures the policy against a frontier and reports the operating points it resolves to; adopting a threshold is an adviser decision.

