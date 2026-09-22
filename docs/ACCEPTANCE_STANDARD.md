# The USTore acceptance standard

The criterion replacing `MAPE ≤ 20%` (§3.3.4). Stated here, encoded in
`tools/acceptance_standard.py`, and proved falsifiable by
`tests/test_acceptance_standard.py`.

**Current verdict: NOT ACCEPTED — 13 of 14 checks pass; forward demand coverage fails at
0.8830 against an a priori 0.90.** That failure is reported, not repaired, and §5 explains
why it cannot be closed by tuning.

---

## Why a new criterion, and why this is not moving the goalposts

`MAPE ≤ 20%` was not missed by a little. It is *degenerate* on this catalogue: MAE is
minimised by the conditional median, MASE is MAE over a constant so it shares the minimiser,
and on a series where 68,541 of 84,399 rows are zero the median **is** zero. The
error-minimising forecast is "nothing will sell" — `rolling_median_30` wins the benchmark on
MASE and prices **0 of 266 SKUs**. Proven, not asserted
(`docs/DEGENERATE_FORECAST.md`, pinned by `tests/test_degenerate_forecast.py`).

Being handed freedom to choose a replacement is the *risk*, not the relief. The obvious
attack is *"you failed one criterion, so you invented one you pass."* Two things answer it,
and the standard is built around both:

**1. The thresholds are set a priori.** Each comes from what an inventory system must *do* to
be useful, written down before the measurement was taken. They would read the same if the
system were failing them — and one of them **is** failing, which is the proof.

**2. Every condition can fail.** `tests/test_acceptance_standard.py` feeds each one a fixture
built to break it and requires the break. A criterion that cannot fail is not a criterion,
it is a certificate.

### It is stricter than what it replaces

| | `MAPE ≤ 20%` | This standard |
| --- | --- | --- |
| Scope | one number | four conditions |
| Evidence | one split | four held-out windows |
| At a forecast of zero | **optimal** | **fails** conditions 1 and 3 |
| Baseline comparison | none | a real no-model policy |
| Asks if its inputs were observed | no | yes — 16.9% of this calendar is assumption |

---

## The four conditions

### 1. Actionability

*A stocking system must price the items that carry the trade, and say so explicitly where it
cannot.*

| Check | Threshold | Measured |
| --- | --- | --- |
| Every eligible SKU carries a state | **100%** — a silent omission is the exact failure this contract exists to prevent | 266/266 ✅ |
| Flagged rows emitting a reorder point | **0** | 0 ✅ |
| Priced rows missing a reorder point | **0** | 0 ✅ |
| **Forward demand coverage** | **≥ 0.90** | **0.8830 ❌** |

Forward coverage asks: *of the demand that actually arrived, how much came from SKUs the
system priced in advance?*

> **A tautology caught and removed.** The first draft measured coverage on the *trailing*
> window and scored a perfect **1.0000**. That looked like a pass and was arithmetic: a SKU is
> flagged `insufficient_data` precisely *because* its trailing window is empty, so flagged
> SKUs contribute zero to trailing demand by construction and the ratio can never be anything
> but 1. It could not fail, so it was not a condition. The forward form can fail — and does.

| Origin | Forward coverage | Flagged demand |
| --- | ---: | --- |
| 2026-05-03 | 0.9750 | 296 of 11,834 |
| 2026-02-02 | 0.8964 | 848 of 8,184 |
| 2025-11-04 | **0.7798** | 3,760 of 17,073 |
| 2025-08-06 | 0.9172 | 1,365 of 16,482 |
| **pooled** | **0.8830** | 6,269 of 53,573 |

### 2. Beats the no-model alternative

*A system that cannot beat what the store could do with no system is not worth deploying.*

The comparator is a **naive stocking policy** — commit what the last lead-time block actually
sold — not another setting of our own model. "Every origin", not "on average": a policy that
wins on average and loses some quarters is not one a store can rely on.

| Origin | Policy | Naive |
| --- | ---: | ---: |
| 2026-05-03 | **0.6851** | 0.4264 |
| 2026-02-02 | **0.9149** | 0.8539 |
| 2025-11-04 | **0.7277** | 0.5928 |
| 2025-08-06 | **0.5987** | 0.4338 |

**4 of 4 ✅**

### 3. Not dominated

*If a simpler policy delivers at least as much service for no more stock, the complexity is
unjustified.*

| Alternative | Fill | Units held | Verdict |
| --- | ---: | ---: | --- |
| flat q=0.80 (pre-tiering) | 0.6188 | 15,122 | we dominate |
| normal z·σ (retired) | 0.6022 | 21,029 | we dominate |
| flat q=0.95 | 0.7980 | 32,057 | trade-off |
| naive stocking | 0.4264 | 3,249 | trade-off |
| **this policy** | **0.6851** | **14,751** | dominated by none ✅ |

> **Why efficiency is deliberately *not* a condition.** Units-served-per-unit-held is reported
> throughout this project and is excluded from the standard on purpose, because it is
> degenerate in exactly the way MAPE is: a policy that stocks one unit and sells it scores
> perfect efficiency and serves nobody. Measured — the naive baseline beats this policy on
> efficiency at **4 of 4** origins while serving far less demand. Service and cost are judged
> **together**, as a dominance, or not at all. Designing the replacement criterion with the
> original's failure mode in view is the point.

### 4. Evidence integrity

*No headline figure may rest on a window that is mostly assumption.*

139 of 821 calendar days (**16.9%**) carry no record of a sale *or* a closure, and the store
demonstrably trades seven days a week (79 Saturdays and 76 Sundays are tallied). Those days
used to be counted as zero demand.

| Origin | Observed share | Status |
| --- | ---: | --- |
| 2026-05-03 | 0.9781 | headline |
| 2026-02-02 | 0.9726 | headline |
| 2025-11-04 | 0.9644 | headline |
| 2025-08-06 | 0.8055 | **disclosed** |

**Passes ✅** — three headline windows clear the bar, and the fourth is reported and labelled.
It is not dropped: excluding an unfavourable window would be selection, a worse failure than
the low observability itself.

---

## 5. The failing condition, and why it is not tuned away

Forward coverage fails because **dormant SKUs revive**: 11.7% of realised demand comes from
SKUs that had sold nothing in the trailing year and were therefore flagged
`insufficient_data`.

The obvious response is to lengthen the rate window so fewer SKUs are flagged. **Measured, it
does nothing:**

| Rate window | Forward coverage | Fill |
| ---: | ---: | ---: |
| 365 d | 0.8830 | 0.7046 |
| 456 d | 0.8832 | 0.6996 |
| 547 d | 0.8832 | 0.6984 |
| 730 d | 0.8832 | 0.6974 |

Coverage moves by **0.0002** across a doubling of the window. The revived SKUs are not
dormant-for-eighteen-months; they have no sales anywhere in the record before the origin.
This is the **cold-start boundary** the project already documents: first-season designs,
one-off drops and genuinely new lines have no learnable rate *in principle*, and for them the
reliable output is the flag, not a number.

So the honest statement is: **the standard's own threshold has surfaced a real structural
limit, which is what a good criterion is for.** The system flags these SKUs for human review
rather than guessing at them — it simply cannot price them, and roughly one unit in nine
traded comes from them.

**This is a limitation to state, not a number to fix.** Closing it would require either
lowering the evidence bar for pricing (re-introducing the degenerate stocking the contract
exists to prevent) or information the sales history does not contain.

---

## Reproducing the verdict

```bash
python scripts/step5_prescriptive.py        # Result_Prescriptive
python scripts/validate_policy_holdout.py   # the rolling-origin evidence
python tools/acceptance_standard.py         # the verdict; exits non-zero when not accepted
pytest tests/test_acceptance_standard.py    # proves each condition can fail
```

## What the standard does not settle

- **Whether a system that fails one of four conditions should be adopted** is the adviser's
  call. The standard reports; it does not decide.
- **Cost inputs remain provisional** pending the USTore site visit — holding is reported in
  units, never pesos.
- **Client rulings still outstanding**: the two dates that traded on a flagged closure, the
  May 2024 discrepancy of 296 units, and the four price-suffix families. The observability
  column makes their effect visible rather than resolving them.
