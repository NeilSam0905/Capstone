# Work log — reconciling the record, wiring the chain, and adjudicating the tiering

Covers the session of **2026-09-23** that follows `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md`
and works its §9 list. Four of its six open items are closed. No new measurement was needed for
the first; the other three are new measurement, and two of them **corrected a committed claim**.

**State at the end of this log:** 500 tests pass (462 → 500), 22/22 database invariants hold,
`tools/acceptance_standard.py` is **unchanged** — 13 of 14 checks, forward coverage 0.8830,
NOT ACCEPTED — and `Result_Prescriptive` is **identical row-for-row at 474 rows**. `ustore.db`
*was* modified, deliberately and only in `Result_Forecast` (1,740 → 5,564 rows); every other
table is untouched. No threshold, expected value or verdict was changed.

---

## 1. What was built

| Artifact | |
| --- | --- |
| `docs/CHAPTER_4_RECONCILIATION.md` | The finding-by-finding audit the chapter edit was made from |
| `tools/render_chapter4.py`, `tools/chapter4_shell.html` | The generator `docs/chapter4.html` never had |
| `tools/cold_start_donor_test.py` | Donor rules on the cold-start subset, against a matched-stock control frontier |
| `tests/test_cold_start_donor.py` | 12 tests |
| `docs/COLD_START_ANALOG.md` | The write-up |
| `data/cold_start_donor.csv`, `data/cold_start_donor_origins.csv` | |
| `scripts/step4b_policy_forecast.py` | Publishes the policy rate + interval into `Result_Forecast` |
| `tests/test_policy_forecast.py` | 15 tests |
| `--synthetic-cover` + `paired_bootstrap` in `tools/inventory_simulation.py` | The tiering adjudication |
| `tests/test_inventory_simulation.py` | +11 tests (15 → 26) |
| `data/inventory_synthetic_cover.csv` | Every row `stock_basis = synthetic` |

**Narrowed, not deleted:** `docs/POLICY_HOLDOUT.md` (via its generator),
`docs/PRESCRIPTIVE_CONTRACT.md` §1b, `docs/ACCEPTANCE_STANDARD.md` condition 3,
`docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` §2 and §5, `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md`
§2 and §4b, `README.md`, `backend/pipeline.py`, `docs/USTORE_AUTO_RUN_GUIDE_tyrone.md`.

---

## 2. Chapter 4 reconciled against the policy layer

`docs/CHAPTER_4_DRAFT.md` was dated 2026-09-16 and cited none of the four documents that landed
on 09-22/23. The edit was made from an audit rather than from reading, and the audit is kept:
every principal finding, every numbered table, every prose subsection and every source row
marked **intact / narrowed / superseded / missing**.

| | Count |
| --- | --- |
| Principal findings intact (1, 5, 6, 7) | 4 |
| Intact and extended (8) | 1 |
| Superseded (2, 3, 4) | 3 |
| Findings added (9, 10) | 2 |
| Numbered tables 17 → | **37** (5…41) |
| Source rows 13 → | 25 |

**A superseded reading is almost never a wrong number.** The 0.9490 ceiling, Table 10's knee and
Table 11's dominance are all still correct measurements *of the benchmark path*; what changed is
that the benchmark path no longer decides anything. Each was re-scoped rather than deleted.

**Two committed documents disagree on five quantities**, and the audit's §6 records each rather
than resolving it by re-running anything: `PRESCRIPTIVE_CONTRACT.md` §4/§1b carries a different
run vintage from `POLICY_HOLDOUT.md` / `ACCEPTANCE_STANDARD.md` / `WORKLOG_POLICY` §3 on the
rolling-origin fill, the tiered-vs-flat pair, the buffer comparison, the marginal-holding ratio
and the budget dial. The chapter uses the three-document set that
`scripts/validate_policy_holdout.py` generates, and says so. Four further pairs look like
conflicts and are not — different populations or definitions, both correct.

**`docs/chapter4.html` now has a generator.** It was a hand-conversion with none, so it could
only be brought forward by re-transcription — the one thing the chapter's own evidence rule
forbids. `tools/render_chapter4.py` renders it from the draft, reusing the 16 Sep stylesheet
verbatim; twelve blocks captured from the pre-edit page reproduce byte-for-byte. `--check` exits
non-zero when the HTML is stale.

---

## 3. Categorisation does not rescue the cold start — and the taxonomy is *below* a matched-stock control

`docs/COLD_START_ANALOG.md`, from `tools/cold_start_donor_test.py`. The population and origins
come from `validate_policy_holdout.load`, the labels from `forecasting/category.py` unchanged.

Every structural figure reproduces the ad-hoc run in §2 of the previous log exactly — **6,257
units, 11.68% of demand, 99.8% of the shortfall, 103 distinct SKUs, pooled coverage 0.8830**,
matching `acceptance_standard.py` at every origin. The rule *ordering* reproduces too. The fill
and held *levels* do not, and the previous log does not record its scored set or donor statistic
precisely enough to reconstruct. **The tool is the source now; those levels should not be quoted.**

**The correction that matters is the control.** A single `global × 1.2` point cannot adjudicate a
rule that holds *more* stock than the point does — it reads as a win when it is only a purchase.
Swept into a frontier and compared at each rule's own stock level:

| Rule | Fill | Units held | Control at that stock | Δ |
| --- | ---: | ---: | ---: | ---: |
| `category` | 0.1412 | 3,974 | 0.1402 | **+0.0010** |
| `product_type` | 0.1552 | 4,853 | 0.1631 | **−0.0079** |
| `price_band` (4) | 0.1816 | 5,763 | 0.1844 | **−0.0028** |

**The product taxonomy is not extracting a better rate; it is committing more stock** — below the
uncategorised control at every setting tested. The apparel/non-apparel label is worth **+0.10pp**
over scaling alone, not the +0.17pp the raw comparison suggested.

**Price band does not survive either**, and the previous log's *"only price band carries marginal
signal"* is withdrawn. Its verdict flips sign with the quantile-band count — **−0.0058 at eight
bands to +0.0135 at five** — a free parameter that log never stated. Reported, not resolved.

**The trap is the criterion, not the model.** Condition 1b counts a SKU as covered if it is
priced *at all*. A donor model prices every cold-start SKU, so coverage moves **0.8830 → 0.9998**
and the verdict flips to ACCEPTED **with 82% of that demand still unserved** — worse than the
~13% the earlier estimate implied, because the honest scored set includes the cold SKUs that
never sell. That is the trailing-coverage tautology re-entering the same condition by another
door.

---

## 4. The predictive stage is wired, and nothing moved

`scripts/step4b_policy_forecast.py` publishes what `step5_prescriptive.py` actually consumes —
`resolve_rates`' demand rate and `empirical_buffer`'s lead-time interval — into `Result_Forecast`
as `model_type='policy_rate'`. step5 reads them instead of recomputing.

| | Before | After |
| --- | --- | --- |
| Predictive coverage | 58 SKUs, 26 usable (9.8%), **consumed by nothing** | **208 priced + 58 flagged = 266 of 266**, consumed by step5 |
| `Result_Forecast` | 1,740 rows | 5,564 (1,740 point-forecast **untouched** + 3,824 policy) |
| `Result_Prescriptive` | 474 rows | **474 rows, identical byte-for-byte across all 27 columns** |

Three decisions, each of which could have gone the other way:

- **A new step rather than a change to step4.** The interval is measured at each SKU's own
  lead-time horizon, so it cannot run before `step5a` sets lead times. Order is now
  `step5a → step4b → step5`. step4b is **not** skippable; step4 still is, and the reason has
  changed rather than disappeared.
- **Both model types in one table.** `rolling_mean_30`'s rows are what the Demand Forecast screen
  draws and are untouched. Two writers now share one table, so step4's `DELETE` is scoped to its
  own `model_type` — with `IS NOT`, so legacy NULL-model rows are still cleared. Pinned by test.
- **The consumed quantities are stored whole, not recovered from the band.** The band *is*
  populated and meaningful — `SUM(yhat_upper)` across a SKU's lead-time rows is its reorder point,
  gated on write — but recovering the buffer as `(yhat_upper − yhat) × L` returns it to within an
  ulp, and an ulp in the buffer is an ulp in every reorder point. `--recompute-policy` keeps the
  pre-wiring path runnable as the control that makes the equivalence checkable.

The 58 flagged SKUs carry a row with a NULL `yhat` and `rate_source='insufficient_data'`, not a
zero. A zero would read as *"nothing will sell"* — the degeneracy this project removed from the
prescription, re-entering through the predictive table.

---

## 5. The tiering adjudicated: a trade, not a dominance

`--synthetic-cover C` gives every **priced** SKU `C × rate × L` of opening stock, so the whole
priced catalogue is observable instead of the 28–45 the workbook covers. Demand, rates, reorder
points, lead times and EOQ are all real; **only the opening condition is invented**, and every
row carries `stock_basis = synthetic`. Precedent: `tools/sparsity_sensitivity_sim.py`. The
difference is read with a bootstrap **paired on the arm and clustered on the SKU**.

| Shelf | SKUs | Δ fill (tiered − flat80) | 95% CI | Δ stock | Verdict |
| --- | ---: | ---: | --- | ---: | --- |
| **measured — the anchor** | 52 | +0.0044 | [−0.0000, +0.0111] | +3.1% | **not separable** |
| synthetic, *C* = 2 | 242 | −0.0018 | [−0.0108, +0.0063] | −5.2% | not separable |
| synthetic, *C* = 4 | 242 | **+0.0196** | [+0.0053, +0.0353] | +15.6% | **trade** |
| synthetic, *C* = 8 | 242 | **+0.0260** | [+0.0145, +0.0411] | +14.3% | **trade** |
| synthetic, *C* = 24 ≈ measured depth | 242 | **+0.0096** | [+0.0028, +0.0206] | **+0.6%** | **trade** |

**Separable at 5 of 10 depths, a dominance at none.** §4b's suspect was sample size and it was
the right suspect — 242 SKUs resolve what 52 cannot. But the effect it was hiding is a
*purchased* one: the tiering wins +1.0 to +2.8 points of fill and holds **more** stock at every
depth where it wins. `POLICY_HOLDOUT.md`'s *"more demand met, on no more stock"* is narrowed in
its own generator, and the same narrowing propagated to the contract §1b, the standard's
condition 3 and both earlier logs.

At the depth bracketing USTore's actual shelf the price is **0.6% more stock for +0.96 points of
fill** — a favourable trade, and one the store should be offered knowingly rather than told is
free.

**One mechanism worth carrying.** At *C* ≤ 1, **100% of SKUs open below *both* reorder points**,
so both rules fire on day zero and the lead time — not the reorder point — decides what is
served. The arms are identical there *by construction*. A difference of exactly zero is a
mechanism, not indifference, and the sweep prints the share of SKUs in that state so those rows
cannot be misread.

---

## 6. Corrections made to this session's own work

Recorded because each was caught by measurement or by a failing assertion rather than by review,
and the same mistakes are available to a future session.

1. **"A single scaled control point is enough."** It is not. It cannot adjudicate a rule that
   holds more stock than the point does, and swept into a frontier it **reversed the price-band
   conclusion** the previous log carried. The same fix was needed twice in one session — §3's
   donor control and §5's tiering control are the same idea.
2. **"Recover the buffer from the band by subtraction."** Ulp-lossy, and byte-identity of
   `Result_Prescriptive` was the requirement. Caught before it shipped by asking what the
   comparison would have to look like to fail; the columns exist because of it, and a test
   demonstrates the lossy route failing rather than asserting the design.
3. **"Two figures of about 26 points are the same finding."** They are not — tiered-against-none
   under simulation (0.9345 − 0.6745) and policy-against-naive under coverage (0.6851 − 0.4264)
   are coincidentally the same size and say opposite things about the apparatus. Now disambiguated
   in both places the chapter uses them.
4. **The renderer read a wrapped cross-reference as a list.** A paragraph ending *"…in Table"* and
   continuing *"2. This reflects…"* is not an ordered list. Hardened to require the list to open
   at `1.`, which is also what the original hand-conversion got wrong.
5. **An inserted table broke a source-row range.** `Tables 15–18` spanned a section boundary, and
   the seventeen new tables landed inside it. Caught by the renumberer's own assertion, which is
   why the renumberer asserts.

---

## 7. What moved and what did not

| | Before this session | After |
| --- | --- | --- |
| Tests | 462 | **500** |
| Database invariants | 22/22 | 22/22 |
| Acceptance verdict | NOT ACCEPTED, 13/14, 0.8830 | **unchanged** |
| `Result_Prescriptive` | 474 rows | **474 rows, byte-identical** |
| `Result_Forecast` | 1,740 rows, read by nothing | 5,564 rows, read by step5 |
| Predictive coverage | 26 usable SKUs | **208 priced + 58 flagged** |
| Chapter 4 | 894 lines, 17 tables, 0 of 4 policy docs cited | 1,799 lines, 37 tables, all 4 cited |
| Committed claims withdrawn | — | tiering *dominance*; *"only price band carries signal"* |

---

## 8. Open, in the order worth doing

1. **Adjudicate `cascade + flat` against `tiered + 365d`.** The `--synthetic-cover` method built
   in §5 applies unchanged and has not been run on this pair. §5 already settles the *shape* of
   the answer: these differences are purchases, so the question is not "which wins" but "what does
   each cost". `docs/PRESCRIPTIVE_CONTRACT.md` §1a is where it lands.
2. **Re-specify acceptance condition 1b against the achievable ceiling** (0.8832 rather than an
   absolute 0.90), keeping the original bar and its failure on the record. Named in
   `WORKLOG_POLICY_AND_ACCEPTANCE.md` §6 and **still not implemented**. §3 above adds urgency: it
   is the correction that raises the floor *without* making the condition unfailable, and it
   should be done before anyone is tempted by a donor model.
3. **Surface `policy_rate` on the Demand Forecast screen.** The three `app.py` queries were scoped
   to exclude it so no screen behaviour changed silently. 208 SKUs now have a published rate and
   interval and the screen still shows a pending card for them.
4. **Replace the paraphrased objective descriptors in Chapter 4 §4.4** with §1.3's exact wording.
   Flagged in the chapter's own editorial note; the outcomes are measured and do not change.
5. **Site-visit asks, not builds** — inventory counts (27% coverage still bounds §5's anchor),
   launch quantities for cold start (11.68% of demand, absent from sales history by nature), the
   June–July 2024 gap ruling, and cost inputs (EOQ swings 12.65× between two readings).
6. **Deferred:** the pooled × behaviourally-clustered × calendar-aware hurdle. Unchanged from the
   previous log — it improves a point forecast that, even now the predictive stage is wired, is
   still not what the prescriptive layer consumes.

---

## 9. Reproducing

```bash
python tools/render_chapter4.py                            # regenerate the chapter page
python tools/render_chapter4.py --check                    # is it stale?
python tools/cold_start_donor_test.py                      # the donor rules
python tools/cold_start_donor_test.py --price-bands 4 2 3 5 8   # the band-count flip
python scripts/step4b_policy_forecast.py                   # publish the policy forecast
python scripts/step5_prescriptive.py                       # reads it; gates all-PASS
python scripts/step5_prescriptive.py --recompute-policy    # the control: must agree exactly
python tools/inventory_simulation.py --synthetic-cover     # the tiering adjudication
python scripts/validate_policy_holdout.py                  # regenerates POLICY_HOLDOUT.md
python tools/acceptance_standard.py                        # unchanged: 13/14, 0.8830
python tools/assert_invariants.py --phase a10              # 22/22
pytest tests/ -q                                           # 500 passed
```

`step4b` and `step5` write; everything else opens `ustore.db` read-only. The equivalence in §4 was
verified on a copy first, then on `ustore.db` itself.

## Related

- `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md` — the session this one follows, whose §9 is its agenda
- `docs/CHAPTER_4_RECONCILIATION.md` — §2 in full, including the divergence register
- `docs/COLD_START_ANALOG.md` — §3 in full
- `docs/PRESCRIPTIVE_CONTRACT.md` §5 — §4 in full
- `docs/INVENTORY_SIMULATION.md` — §5 in full
- `docs/POLICY_HOLDOUT.md` — the dominance claim §5 narrows, in its generator
