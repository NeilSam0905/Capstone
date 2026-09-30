# Cold start — can a category label price the SKUs that have never sold?

Measured by `tools/cold_start_donor_test.py`, pinned by `tests/test_cold_start_donor.py`.
Population and origins come from `scripts/validate_policy_holdout.py::load`, labels from
`forecasting/category.py::classify` and `::classify_product_type`, both imported unchanged.
Nothing here is deployed and no threshold is altered.

`docs/ACCEPTANCE_STANDARD.md` condition 1b — forward demand coverage — is the one failing check,
at **0.8830** against an a priori 0.90. This document measures the only escape route that does not
invent data, finds it does not work, and records precisely why the one thing that *looks* like it
works is an artifact of the criterion rather than a result.

---

## Read the boundary before the numbers

**Cold start here means: no sale anywhere in the record strictly before the origin.** Not "empty
trailing window" — that is a different and much larger set, and the two must not be conflated.
`ACCEPTANCE_STANDARD.md` §5 opens by calling the failure *"dormant SKUs revive"* and corrects
itself a paragraph later; the second sentence is the definition. A SKU silent for fourteen months
has a history to reason from. One that has never sold does not, and no amount of window is going to
find it.

That distinction is load-bearing and is pinned by test:

| | Population | What it is |
| --- | ---: | --- |
| Flagged at the live origin | **58** | All last sold 2024-05-07 … 2025-07-21 — *dormant over a year*, not new |
| Cold start across the four origins | **103** | Had **never sold a unit** before the origin |

The second is what breaks coverage, and it is invisible from a single origin: **112 SKUs first sold
inside the trailing year and all 112 already carry a positive rate** (`PRESCRIPTIVE_CONTRACT.md`
§1). Looking backward from one origin, the "new SKU whose window cannot be filled" case is empty —
because by the time you look back, it has history. It only appears scoring *forward*.

### The shortfall, decomposed

| Origin | Window end | Cold-start SKUs | Window demand | Cold-start demand | Gone-quiet demand | Forward coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-05-03 | 2026-07-31 | 11 | 11,834 | 296 | 0 | 0.9750 |
| 2026-02-02 | 2026-05-02 | 40 | 8,184 | 848 | 0 | 0.8964 |
| 2025-11-04 | 2026-02-01 | 78 | 17,073 | 3,748 | 12 | **0.7798** |
| 2025-08-06 | 2025-11-03 | 103 | 16,482 | 1,365 | 0 | 0.9172 |
| **pooled** | | **103 distinct** | **53,573** | **6,257** | **12** | **0.8830** |

**6,257 units — 11.68% of forward demand, 99.8% of the entire coverage shortfall — come from SKUs
with no sales history at all.** Twelve units, 0.02%, come from SKUs that merely went quiet. The
coverage figures reproduce `tools/acceptance_standard.py` to four decimals at every origin, which
is the check that this tool is scoring the same thing the standard is.

---

## What is scored, and the choices behind it

Six donor rules on the cold-start subset only, fitted strictly pre-origin, block-tiled into
lead-time blocks exactly as `validate_policy_holdout.score` does so the fill rates are read on the
same axis as every other fill rate this project reports.

| Rule | Where the borrowed rate comes from |
| --- | --- |
| `none` | commit nothing — **the current system** |
| `global` | median rate over all priced SKUs — *no categorisation* |
| `category` | median within apparel / non-apparel |
| `product_type` | median within the eight keyword buckets |
| `global_x{s}` | **control** — `global`, rescaled, no label anywhere |
| `price_band` | median within the SKU's unit-price quantile band |

Four choices are stated because they are choices, and two of them change the answer.

- **The donor statistic is the median**, not the mean, of the pool members that have a rate — the
  same choice and the same reason as `policy.cluster_rates`: these distributions are right-skewed
  and one high-volume member would otherwise drag every borrower upward.
- **No buffer.** The commitment is `donor_rate × L`. A cold-start SKU has no prior-fold errors to
  take an empirical quantile of, so any buffer would itself have to be borrowed — a second borrowed
  quantity confounding the first.
- **Every cold-start SKU is scored, including those that sell nothing.** A deployed donor model
  prices all of them and carries their stock. See *The selection effect* below for what excluding
  them would do.
- **Price bands are quantiles of `unit_price_php`.** There is no committed band definition in this
  repository, so the count is a free parameter — and it turns out to decide the verdict.

**Leakage.** Every donor rate is fitted with `upto=split`, and a gate re-derives the rates with the
scored window blanked and requires an identical answer. The label inputs — `Dim_Product.category`,
`item_name`, `unit_price_php` — are static catalogue attributes, not derived from `Fact_Sales`, so
unlike `fsn_class` they carry no temporal leak. That distinction is the subject of
`FORECAST_EXPERIMENT_AUDIT.md`, and the gate makes the claim checkable rather than asserted.

---

## Result

Pooled over the four origins, cold-start subset only, on the raw unit totals rather than an average
of per-origin rates.

| Donor rule | Fill | Units served | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| `none` — commit nothing | 0.0000 | 0.0 | 0.0 | — |
| `global` — no categorisation | 0.1392 | 843.8 | 3,935.1 | 0.214 |
| `category` — apparel / non-apparel | 0.1412 | 855.8 | 3,974.1 | 0.215 |
| `product_type` — 8 buckets | 0.1552 | 940.5 | 4,852.9 | 0.194 |
| `price_band` — 4 quantiles | **0.1816** | 1,100.5 | 5,763.2 | 0.191 |

Read alone, that column says the finer the taxonomy the better, and price band wins. Read alone, it
is wrong, and the next section is why.

---

## The matched-stock control, and it is the whole analysis

A donor rule can always buy fill by committing more stock, and the finer the grouping the more
likely its median lands high. So `global` is rescaled across a range and re-scored — giving fill as
a function of stock with **no label in it anywhere** — and each labelled rule is compared against
the control's fill *at its own stock level*.

| Control point | Fill | Units served | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| `global × 0.25` | 0.0432 | 261.5 | 933.2 | 0.280 |
| `global × 0.5` | 0.0791 | 479.3 | 1,910.2 | 0.251 |
| `global × 0.75` | 0.1104 | 669.1 | 2,915.1 | 0.230 |
| `global × 1.0` | 0.1392 | 843.8 | 3,935.1 | 0.214 |
| `global × 1.2` | 0.1608 | 974.4 | 4,760.5 | 0.205 |
| `global × 1.25` | 0.1660 | 1,005.7 | 4,968.0 | 0.202 |
| `global × 1.5` | 0.1903 | 1,153.2 | 6,015.4 | 0.192 |
| `global × 1.75` | 0.2131 | 1,291.1 | 7,071.9 | 0.183 |
| `global × 2.0` | 0.2345 | 1,421.3 | 8,136.6 | 0.175 |
| `global × 2.5` | 0.2736 | 1,658.3 | 10,289.1 | 0.161 |
| `global × 3.0` | 0.3090 | 1,872.7 | 12,464.3 | 0.150 |

The comparison that matters — `rule fill − control fill at the same units held`:

| Rule | Fill | Units held | Control at that stock | Δ | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| `category` | 0.1412 | 3,974 | 0.1402 | **+0.0010** | above the control — buys something |
| `product_type` | 0.1552 | 4,853 | 0.1631 | **−0.0079** | **below** — the label costs fill |
| `price_band` (4) | 0.1816 | 5,763 | 0.1844 | **−0.0028** | **below** — the label costs fill |

**`product_type` is not extracting a better rate. It is committing more stock.** Scaling the
uncategorised donor to hold the same 4,853 units reaches 0.1631 where the eight buckets reach
0.1552 — the finer taxonomy is *worse than no taxonomy at all*, once stock is held fixed. That is
the result, and it is consistent with the pooling experiments already on record: drinkware, the
most internally similar group in the catalogue, was the single worst pooling group
(`POOLING_AND_CLUSTERING_EXPERIMENTS.md` §3), and behavioural clustering beat every manual grouping
(§11). The taxonomy fails as a pooling basis and fails again as a donor basis.

**The category label is worth +0.10pp against the control** (+0.20pp raw, 0.1392 → 0.1412, at 39
extra units held). It is positive, it is real, and it is a tenth of a percentage point on 11.68% of
demand.

---

## Price band is not established either

The worklog this document replaces concluded *"only price band carries marginal signal."* Against a
single control point that reads correctly. Against the frontier, and swept over the band count the
worklog never stated, it does not survive:

| Bands | Fill | Units held | Control at that stock | Δ |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 0.1626 | 4,632 | 0.1574 | +0.0052 |
| 3 | 0.1586 | 4,355 | 0.1502 | +0.0084 |
| **4** | 0.1816 | 5,763 | 0.1844 | **−0.0028** |
| 5 | 0.1683 | 4,532 | 0.1548 | +0.0135 |
| 8 | 0.1867 | 6,119 | 0.1925 | **−0.0058** |

**Δ ranges −0.0058 to +0.0135 and the sign flips.** A free analysis parameter with no principled
basis decides whether price band beats scaling or loses to it. That is not a result; it is a
degree of freedom, and it is reported rather than resolved. `product_type` is below the control at
every setting tested, which is why that rejection stands and this one is withheld.

## The selection effect, measured

Scoring only the cold-start SKUs that *turned out* to have demand leaves fill identical and cuts
stock by two-thirds, because the excluded SKUs contribute held and no demand:

| Scored set | `global` fill | `global` units held | Served / held |
| --- | ---: | ---: | ---: |
| all cold-start SKUs *(committed)* | 0.1392 | 3,935.1 | 0.214 |
| only those with demand *(control)* | 0.1392 | 1,334.0 | **0.633** |

Efficiency nearly triples on a choice about which SKUs to count. A donor model deployed at USTore
prices every cold-start SKU before knowing which will sell, so the first row is the honest one; the
second is survivorship. The domination verdicts are unchanged either way — `product_type` is below
the control in both — which is the reason the headline finding can be stated at all.

---

## What deploying this would do to acceptance condition 1b

**This is the most important thing in this document and it is not a number about donors.**

Condition 1b counts a SKU as *covered* if it is priced **at all**, regardless of whether that price
serves any demand. A donor model prices every cold-start SKU. Therefore:

| | Forward coverage | Verdict against the a priori 0.90 |
| --- | ---: | --- |
| today — flag, no number | 0.8830 | **FAILS** |
| with any donor model | **0.9998** | **PASSES** |
| …share of that demand actually served | **0.1816** | at the most generous rule measured |

**The verdict would flip to ACCEPTED on demand that is still 82% unserved.** That is the
trailing-coverage tautology again, in the same condition: the first draft of 1b measured the
*trailing* window, scored a perfect 1.0000, and was discarded because a flagged SKU contributes
zero to trailing demand by construction. Rewritten to measure forward, it can fail — and a donor
model is precisely the move that makes it stop being able to fail again.

**No threshold is changed here and no donor rule is deployed.** 1b would have to be re-specified
first — against served demand rather than priced SKUs — and that re-specification would have to be
argued on its own merits by someone who is not the party it flatters. `WORKLOG_POLICY_AND_ACCEPTANCE.md`
§6 already records a separate, unimplemented correction to 1b (measure against the achievable
ceiling of 0.8832 rather than an absolute); that one raises the floor without making the condition
unfailable, and it is the one to do first.

---

## Divergence from the run this replaces

`docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §2 records an ad-hoc version of this measurement. Its
figures and this tool's agree on everything structural and differ on level:

| | Worklog §2 | This tool |
| --- | ---: | ---: |
| Cold-start units / share / share of shortfall | 6,257 / 11.68% / 99.8% | **identical** |
| Distinct cold-start SKUs | 103 | **identical** |
| Pooled forward coverage | 0.8830 | **identical** |
| Rule ordering by fill | none < global < category < product_type < control < price_band | **identical** |
| `global` fill / held | 0.1309 / 2,033 | 0.1392 / 3,935 |
| `category` fill / held | 0.1326 / 2,061 | 0.1412 / 3,974 |
| `product_type` fill / held | 0.1445 / 2,500 | 0.1552 / 4,853 |
| Category label worth | +0.17pp | +0.20pp raw, **+0.10pp against the control** |
| `product_type` verdict | dominated by the control | **dominated at every setting tested** |
| `price_band` verdict | "carries marginal signal" | **not established — sign flips with band count** |

The worklog does not record its scored set or its donor statistic precisely enough to reconstruct,
and two candidate reconstructions bracket its held figure without reproducing it. **This tool is
now the source; the worklog's levels should not be quoted.** That every structural figure and the
full ordering reproduce is what makes the divergence a level difference rather than a contradiction
— but a number that cannot be re-derived from what was written down is not evidence, which is a
lesson this project has now learned four times.

---

## What this does and does not establish

- **Does:** the cold-start boundary is real and it is 11.68% of demand, 99.8% of the coverage
  shortfall, from 103 SKUs with no history at all. Reproducible, and matching the acceptance
  standard's own coverage figures at every origin.
- **Does:** the **product taxonomy does not work as a donor basis**. Below the uncategorised
  control at every setting tested — it buys fill with stock, not with information.
- **Does:** the **apparel / non-apparel label buys +0.10pp of fill** over scaling alone. Real,
  positive and negligible.
- **Does:** deploying any donor model would **flip acceptance condition 1b from FAIL to PASS while
  serving under a fifth of the demand in question.**
- **Does not:** establish price band either way. The verdict flips sign with a band count that has
  no principled basis.
- **Does not:** test a donor model with a buffer, or one that borrows from behavioural clusters
  rather than a manual label. Clustering beat every manual grouping as a *pooling* basis
  (`POOLING_AND_CLUSTERING_EXPERIMENTS.md` §11) and is the obvious next donor basis to try — it is
  named here and not built, because it would need its own validation before any claim rested on it.
- **Does not:** deploy anything, write to the database, or change a threshold, a default or a
  committed figure. `forecasting/policy.py` is untouched; the database is opened read-only.
- **Does not:** speak to SKUs with *some* history. Those are priced today, and the 36 with fewer
  than 10 units across the trailing year are what the cluster-pooled shrinkage exists for — itself
  measured and defaulted off as dominated (`PRESCRIPTIVE_CONTRACT.md` §1).

## Reproducing it

```bash
python tools/cold_start_donor_test.py                          # the tables above
python tools/cold_start_donor_test.py --price-bands 4 2 3 5 8  # the band-count sweep
python tools/cold_start_donor_test.py --scored-set with-demand # the selection-effect control
python tools/cold_start_donor_test.py --buffer-quantile-donor 0.80   # a donor buffer, sensitivity
pytest tests/test_cold_start_donor.py -q                       # 12 tests
```

Writes `data/cold_start_donor.csv` and `data/cold_start_donor_origins.csv`. Opens `ustore.db`
read-only and writes nothing to it.

## Related

- `docs/ACCEPTANCE_STANDARD.md` — condition 1b, the failing check this is about
- `docs/PRESCRIPTIVE_CONTRACT.md` §1 — the three rate states, and why the flag is the output
- `docs/POLICY_HOLDOUT.md` — the origins and population reused here
- `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` §3, §11 — the taxonomy failing as a pooling basis
- `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` §5 — the lever table this is the sixth row of, and §6–7
- `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §2 — the ad-hoc run this replaces
