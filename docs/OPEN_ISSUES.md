# Open issues

Technical problems that still need fixing. Moved out of the README so that file
stays readable; the resolved history is kept at the bottom rather than deleted.

**Team decisions** (B1–B15 — repo visibility, the acceptance criterion, model
selection, the site visit) live in `docs/STATUS_AND_NEXT_STEPS.md`. This file is
for engineering work, not decisions awaiting a human call.

---

## Open

### 1. 15 of 58 forecast SKUs return zero
The default model's inputs are empty for 15 Fast SKUs, so their forecast is a
flat zero line and `step5 --demand-basis forecast` prices only 43 SKUs (against
`trailing`'s 208) — a priced share of 0.1617, which fails the 0.78
non-degeneracy floor. This is `docs/DEGENERATE_FORECAST.md`'s argument appearing
in production rather than in a benchmark. It is the direct cost of switching to
the benchmarked trailing window, and it is a live decision, not a bug. See
`docs/ROLLING_MEAN_FORECAST.md` §4.

Re-measured 2026-09-30 against the rebuilt database. The figures above were 32
zeros and 26 priced under `rolling_mean_30`; the blend default
(`topdown_tsb+calendar+weekday_shape`) roughly halves the zeros without getting
near the floor, so the shape of the problem is unchanged.

### 2. No auth on the backend
`Event_Log.created_by` is hardcoded `'local'`. Fine for a single-machine
capstone demo, not for anything beyond that.

### 3. Inventory coverage is low
~14–17% of products have any stock count, which limits both the Stock Status
view and the Reorder screen's "on hand" column to a minority of SKUs. Block 3 /
B10, unresolved.

### 4. Everything Phase 4 produces is provisional
Lead time, holding cost, and both ordering-cost interpretations are estimates
pending the USTore site visit (Block 5 / B9). Don't treat `Result_Prescriptive`
as final.

### 5. Power BI (Phase 6) hasn't been built
The frontend has an embed placeholder (`PowerBIDashboard.jsx`) wired to
`VITE_POWERBI_EMBED_URL`, but no `.pbix` has been authored or published. Three
of the five views are buildable today; Stock Status is blocked on issue 3 above.
`docs/POWERBI_DASHBOARD_PLAN.md` has the chart-by-chart spec.

### 6. `create_schema.py` cannot repair an existing schema
It uses `CREATE TABLE IF NOT EXISTS` throughout, so a DDL change never reaches a
database that already exists — and SQLite cannot `ALTER` a CHECK constraint. This
bit once already: a widened `price_source` CHECK shipped in `fcd597d` never
landed, and `step1_apply_mapping.py` failed with `IntegrityError` on every run
until `ustore.db` was rebuilt from scratch. There is no migration path and no
schema-version marker. Until there is, **a schema change means a full rebuild**,
and that has to be remembered rather than enforced.

### 7. `step1_apply_mapping.py` needs `rawdata/`, and nothing says so
Two of its four price sources read the client workbooks directly: `may2024_dsr`
parses the 23 daily sheets of `rawdata/2024 5 MAY DSR & TBS.xlsx`, and
`tbs_item_price` parses every workbook's TBS sheet (both added in `fcd597d`).
Neither has a fallback, so without `rawdata/` step 1 raises `FileNotFoundError`
before it reaches `Dim_Product` — and it is **not** marked optional in
`backend/pipeline.py`, so the whole run stops there instead of skipping one step
the way step 0 does. Those two sources carry **157 of the 519 catalogue prices**
(121 `tbs_item_price`, 36 `may2024_dsr`), and `unit_price_php` feeds the EOQ and
both ordering-cost scenarios, so degrading them to NULL would not be neutral
either.

The workbooks themselves are **not lost** — see issue 11. Restored from
`origin/gambe` into `rawdata/` on 2026-09-30, the full pipeline runs from
`create_schema.py` to `step5_prescriptive.py`, and at that point it reproduced
the shipped database exactly: `Result_Prescriptive` sha256 `da843d54…` over all
474 rows, `data/USTore_sales_long_with_zeros.csv` byte-identical to the
committed copy once CRLF is normalised, every tracked result CSV clean in
`git status`. (Issue 12 has since moved that deliberately — a rebuild now
produces 480 rows. The point stands: the rebuild is reproducible, and it was
verified neutral before anything was changed on purpose.)

So this is a documentation-and-packaging defect, not a data loss: the README
said "Only step 0 reads them; every later step reads `data/*.csv` … so the
pipeline runs without them", which was true when written and is corrected in the
same pass as this entry. What is still missing is a `rawdata/` check at the top
of step 1 that names the four workbooks, and a decision about where they are
meant to live (issue 11).

### 9. `verify_rebuild_state.py`'s `model_type` check cannot pass on this tree
`scripts/verify_rebuild_state.py:184` asserts that the set of `model_type`
values in `Result_Forecast` equals `{step4.DEFAULT_MODEL}`. That holds on the
branch it was written on; here `step4b_policy_forecast.py` deliberately
publishes `policy_rate` rows **beside** the point forecast, so the set always
has two members and the check always fails — while its own detail line shows
step 4's default matching exactly. Per `CLAUDE.md` the assertion is reported,
not relaxed: whoever owns that script decides whether the check should read
`step4`'s rows only.

### 11. The client tally-sheet workbooks are still committed on `origin/gambe`
All five raw workbooks — supplier names, item prices, daily sales volumes — sit
at the tip of `origin/gambe` under `drive-download-20260724T120738Z-1-001/`:

| file | bytes |
|---|---:|
| `2024 5 MAY DSR & TBS.xlsx` | 2,005,665 |
| `2025 USTore TBS.xlsx` | 1,230,219 |
| `USTore TBS OCTOBER A.Y. 2025-2026.xlsx` | 991,543 |
| `USTore TBS AUG-DEC 2024.xlsx` | 262,851 |
| `2023 total sales by batch (3).xlsx` | 20,409 |

They were added in `6f26f24` (2026-07-27) and removed in `f7ebb62` (2026-08-19,
Remediation Wave 1), which is also where `.gitignore` gained `rawdata/`. `gambe`
branched on 2026-08-06, between those two commits, and was never updated — so
the removal never reached it and the files are still live there. No other
branch carries them (`main`, `tyrone`, `neil`, `marco` and `consolidate` all
have zero such paths).

Two consequences, pulling opposite ways:

- **This is why the rebuild works at all.** They are the only copy of the
  source data on this machine, and step 1 needs them (issue 7). Whatever is
  decided, they have to be preserved somewhere first — the vault would be the
  consistent home, since it already holds their *derived* outputs
  (`data/rebuild_*.csv`).
- **Everything else in this repo treats them as too sensitive to commit.**
  `.gitignore`, `f7ebb62` and the README all say so explicitly. Anyone with
  access to the repository can download them from that branch today. Whether
  that matters depends on the repository's visibility, which is a question for
  the team, not for this file.

### 12. The tiering is not robustly on the efficient frontier, at any operating point
`step5_prescriptive.py::load_series` spanned the zero-padded panel; fixed
2026-09-30 by ending the history at the last date any SKU sold, the rule
`step4_forecast_model.py::build_calendar` already used. **The fix is not in
question** — the source settles it: the "JULY 2026 - TBS" sheet has a date
column for every day of the month and does not contain one literal zero
anywhere, so 2026-07-09..07-31 is "not written up yet", not 23 days of no
sales. Those days still carried 176 `Fact_Sales` rows each, which made
`build_observed_mask()` count them as evidence.

**What it moved.** Trimming re-anchors the window as well as correcting the
denominator, so the effect is much larger than the ~6% the note in
`docs/FORECASTING_EXPLORATION_NOTES.md` §2.5 predicted:

| | before | after |
|---|---|---|
| history span | 2024-05-02 .. 2026-07-31 (821 d) | .. **2026-07-08 (798 d)** |
| days counted as evidence | 682 of 821 | **659 of 798** |
| SKUs priced / flagged | 208 / 58 | **214 / 52** |
| service tiers (not_stockable / partial / servable) | 18 / 166 / 24 | **21 / 176 / 17** |
| `Result_Prescriptive` rows | 474 | **480** |
| priced share | 0.7820 | **0.8045** |
| mean safety stock (empirical) | 8.850 | **13.442** |
| holdout fill, median (spread) | 0.7064 (0.3162) | **0.6778 (0.1945)** |
| TIERED fill / units held | 0.6851 / 14,750.7 | **0.6916 / 20,005.5** |
| flat q=0.80 fill / units held | 0.6188 / 15,122.2 | **0.6328 / 16,205.3** |
| TIERED served per held | 0.536 | **0.443** |
| pooled forward coverage | 0.8830 | **0.9080** |
| acceptance verdict | NOT ACCEPTED 13/14 | **ACCEPTED 14/14** |
| holdout gates | 14/14 | **13/14 — one FAIL** |

517→520 tests, 22/22 invariants and `verify_rebuild_state` 26/28 are unchanged.

**The failing gate, reported and not relaxed** (`CLAUDE.md`):

```
[FAIL] tiering costs no more stock than flat q=0.80    False != expected True
```

Tiering still wins on fill at 4 of 4 origins, but it now holds **20,005.5
units against flat q=0.80's 16,205.3** — 23% more, where before it held
slightly less. Its efficiency drops below the flat policy's (0.443 against
0.501). The buffer is the q-quantile of each SKU's own prior-fold policy
errors; with the fabricated zero actuals gone those errors are genuinely
larger, so every buffer grows, and the tiered arm grows most because it
assigns the higher quantiles.

So `docs/PRESCRIPTIVE_CONTRACT.md`'s claim that the cascade "**dominates**:
higher service *and* 27% less stock held" (line 353) no longer holds. The
honest statement is a trade-off: more service, more stock. **That is a claim
to retract, not a gate to loosen** — either the tiering's operating point
needs revisiting now that the buffers are right, or the gate's premise
(dominance on both axes) was always stronger than the evidence could carry.
Someone has to choose; nothing here should be edited to make it green.

**And the acceptance flip needs a human read.** Coverage clears the a priori
0.90 partly because six SKUs it could not price before are priced now — `Eco
Bag @ 130`, `Eco Bag CGEEE!`, `Keychain @180`, `Kit Set`, `UST OAT MUG (W
inside)`, `UST OAT MUG (Y/B inside)`, 1,533 lifetime units between them, 1.7%
of the catalogue — and partly because the rolling origins are anchored off
the end of history, so all four moved back 23 days
(2026-05-03/02-02, 2025-11-04/08-06 → 2026-04-10/01-10, 2025-10-12/07-14).
**Before and after are therefore not the same test.** The threshold was not
touched, but the windows it is measured on changed, and a flip to ACCEPTED on
windows the corrected anchor chose should not be reported as "the system now
passes" without that said out loud. `validate_policy_holdout.py` has no flag
to pin origins to absolute dates, so the two causes cannot be separated
without changing a verification script.

**Answered 2026-09-30, and the answer is not the one the entry predicted.**
The first reading — "flat q=0.85 dominates, so either re-fit or retract" — was
taken from `data/policy_holdout_frontier.csv`, which is scored at ONE split: the
most recent window, which `validate_policy_holdout.py`'s own docstring calls a
development set rather than a clean holdout. Scoring the quantile sweep at every
rolling origin instead (`data/policy_holdout_frontier_by_origin.csv`, new) gives
a different picture:

| origin | tiering | cheapest q reaching that fill | margin |
|---|---|---|---|
| 2026-04-10 (development set) | 0.6916 @ 20,005.5 | q=0.85 → 19,708.5 | **−297 (1.5%)**, dominated |
| 2026-01-10 | 0.6639 @ 18,003.0 | q=0.85 → 18,125.8 | +123 (0.7%) |
| 2025-10-12 | 0.7654 @ 19,294.4 | q=0.95 → 28,311.3 | +9,017 |
| 2025-07-14 | 0.5709 @ 8,822.5 | q=0.90 → 10,648.4 | +1,826 |

So the live policy is on the frontier at **3 of 4** origins, and the window that
dominates it does so by 1.5% — on the one window it was designed on. Both gates
now judge on the rolling origins and report the development set as context;
`tools/acceptance_standard.py`'s condition 3 does the same, which is why the
verdict is ACCEPTED again on evidence that is stronger, not weaker, than what it
used before.

**But the sweep says the tiering is never robustly efficient.**
`tools/tier_operating_point.py` (new) runs the holdout across 30 combinations of
`tier_target` × `min_efficiency`, asking whether any is on the frontier at **all
four** origins — a stricter bar than the gate's majority, deliberately, because a
search allowed to miss one window will find a configuration that misses the
awkward one. Result: **0 of 30 clear 4 of 4.** 16 reach 3 of 4, 14 reach 2.

Two things follow that the team has to weigh, and neither is a bug to fix:

- **The live operating point is a poor member of its own group.** Among the 16
  configurations that reach 3 of 4 it ranks **12th on served-per-held** (0.5063).
  `tier_target=0.90, min_efficiency=0.30` reaches **0.6337 — 25% better — while
  holding 49,476 units against the live 66,125, a 25% reduction**, at a
  demand-weighted fill of 0.6260 against 0.6685. That is a real trade (about 4pp
  of service for a quarter of the stock), not a free win, so it is a decision
  about how much service to buy and not a correctness fix. Nothing here changes
  the defaults.
- **The weak windows are not the ones the first reading found.** Across the 30
  configurations the domination lands on 2025-07-14 (21 times) and 2026-01-10
  (20), and on the development set only 3 times. The live default happens to be
  one of those 3, which is what made the original finding look like a
  development-set artefact when it is closer to the reverse.

**What is actually retracted — less than the first reading claimed.** Checking
the documents rather than paraphrasing them:

- `docs/PRESCRIPTIVE_CONTRACT.md:353` — "the empirical buffer **dominates**:
  higher service *and* 27% less stock held" — is about the empirical buffer
  against the retired normal z·σ, **not** about the tiering, and it **survives**:
  0.6328 fill on 16,205.3 units against 0.6123 on 20,109.2, still higher service
  on less stock. Only the magnitude moved, **27% → 19.4%**, and the fill figures
  in the table above it (0.6191 / 15,311.5 / 21,021.3) are pre-fix. Restate the
  numbers; keep the claim.
- `docs/PRESCRIPTIVE_CONTRACT.md:145-155` — the 120-day cascade table and "the
  cascade dominates the flat baseline — +2.2pp on 12% less stock" — carries
  pre-fix figures throughout (0.6188 / 15,122 / 0.6851 / 14,751). That
  comparison has not been re-run since `bf08ca7` and cannot be restated from the
  holdout's current outputs; it needs its own measurement.
- The claim this entry originally said was retracted — "the tiering dominates
  the flat policy" — is not a sentence either document makes. It was a
  paraphrase, and the sweep above is what the documents should gain in its
  place: on the frontier at 3 of 4 origins, never at 4 of 4, and at an operating
  point that ranks 12th of 16 on efficiency among those that reach 3.

**Also found:** `--buffer-quantile` is **inert** for the holdout's tiered arm.
`validate_policy_holdout.py::fit()` accepts `q` and never passes it on, and
`assign_service_tier`'s `default_q` is reached only on the "no scoreable folds"
branch, which no SKU in this catalogue takes. A first sweep varied it over
0.70–0.85 and produced byte-identical results. It is live for `step4b`/`step5`,
where `DEFAULT_BUFFER_QUANTILE` sets the published buffer — but a CLI flag that
looks like it controls the policy and does not is worth either wiring up or
removing.

**Figures that moved in documents, not yet restated.** Thirteen files carry at
least one superseded number: `ACCEPTANCE_STANDARD.md`, `CHAPTER_4_CONTEXT.md`,
`CHAPTER_4_DRAFT.md`, `CHAPTER_4_RECONCILIATION.md`, `COLD_START_ANALOG.md`,
`FORECAST_VALIDATION.md`, `INVENTORY_SIMULATION.md`,
`PRESCRIPTIVE_CONTRACT.md`, `SERVICE_LEVEL_FRONTIER.md`, the three
`WORKLOG_*.md`, and this file. `POLICY_HOLDOUT.md` regenerates itself and is
already current. Chapter 4 is a manuscript and the cascade's justification is
one of its arguments, so restating it is the team's call, not a sweep.

**A new inconsistency the fix creates.** `model_benchmark.py::load_daily_series`
still spans the full 821 days, so `tools/service_frontier.py` — which imports
it — measures a different span from the policy it sits beside. Its output is
byte-identical before and after, `EXP_CEILING = 0.9490` and
`EXP_SKUS_POSITIVE_DEMAND = 208` still pass, and that is the problem: 208 is
no longer what the deployed path reports. Fixing it moves every benchmark
table Neil published.

---

## Blocked on inputs, not defects

`scripts/verify_rebuild_state.py` reported five failures before `rawdata/` was
restored; it now reports **26 of 28 checks passing**. `Dim_Day_Status`,
`Fact_Batch_Sales` (both from `scripts/rebuild_extract_tbs.py`) and
`Dim_Demand_Cluster` (`scripts/cluster_demand_profiles.py`, which reads that
script's `data/rebuild_*.csv`) all populate once the workbooks are present.

Two failures remain, and neither is a code defect:

| Check | Why |
|---|---|
| `model_type matches step4's configured default` | Issue 9 — the assertion cannot hold alongside `step4b`'s `policy_rate` rows. |
| `Result_Category_Prophet_Metrics` exists | `scripts/forecast_category_prophet.py` needs `prophet`, which `requirements/requirements-prophet.txt` deliberately keeps out of the default install. |

Both of those scripts sit **outside** `backend/pipeline.py`, so a rebuild drops
all three tables again and they have to be re-run by hand afterwards. That is
why the same check reads 15/20 immediately after a rebuild and 26/28 once they
have been run.

One thing to watch: `rebuild_extract_tbs.py` writes
`data/day_status_vocabulary.csv` from its keyword rules when the file is absent,
and that file is a **human-editable override** whose `status` column wins on a
later run. The vault holds a copy that may carry review edits; the one generated
here on 2026-09-30 is unreviewed, so unlock the vault's version before trusting
either.

---

## Resolved

Kept as a record so the same ground isn't re-covered.

- **Six copies of "where the real history ends", and three things they hid.**
  The rule — end the series at the last date any SKU sold, not at the last row
  of the zero-filled panel — was written out six times across `scripts/`, in
  two shapes: index-building (`step4_forecast_model.build_calendar`,
  `step5_prescriptive.history_index`) and wide-frame trimming
  (`step4c_category_forecast.trim_padding`, `tune_category_forecast_params`,
  `test_category_forecast_shape`, `test_lead_time_gap`). Now one module,
  `forecasting/history.py`, with `history_end` / `history_index` /
  `trim_to_history`; `trim_padding` is kept as a name because two research
  scripts call it as `s4c.trim_padding`. Verified by rebuilding the database
  from scratch twice: identical both times, every table, count and the 480-row
  digest.
  - The four wide-frame copies had **no NaN guard** — `total[total > 0].index.max()`
    is NaT on an all-zero frame and `wide.loc[:NaT]` raises, so a caller with
    no sales crashed where the index-building pair degraded gracefully. Nothing
    hits it today. It is the kind of divergence that makes two copies of a rule
    stop being one rule.
  - `scripts/model_benchmark.py` never got the rule at all, which is why
    `tools/service_frontier.py` still measures 821 days against the deployed
    798 and its `EXP_SKUS_POSITIVE_DEMAND = 208` passes while the pipeline
    reports 214. Unchanged here — fixing it moves every published benchmark
    table — but it is now visibly the one holdout rather than one of seven
    implementations.
  - `forecasting/observed_day.py`'s docstring asserted the opposite ("they are
    observed, and they stay in") about the same 23 days. Corrected, with the
    workbook evidence and a note that `observed_mask_from_index` reads
    `Dim_Date.is_tally_date`, which is 1 on all 23 — so the module would still
    count them if it ever shipped. It is research code; nothing deployed
    imports it.
- **Two files a rebuild could not reproduce, for two different reasons.**
  `data/USTore_sales_long_with_zeros.csv` and `data/rebuild_sales_long.csv`
  went `local-changed` against the vault after every rebuild.
  - step0 writes with `csv.writer`, which defaults to **CRLF**, while the
    vault's copy is LF — identical content, 75,121 bytes larger, one per line.
    64 of the repo's 93 `to_csv` calls already pin `lineterminator="\n"`; this
    was the writer that does not go through pandas. Pinned.
  - `rebuild_extract_tbs.py` read the supplier mapping with `astype(str)`,
    which renders a blank cell as the **literal string "nan"**.
    `data/supplier_mapping.csv` deliberately leaves `supplier_name` empty on
    two rows — "(Paid)" and "Subli. Shirt 2 colors", both annotated *"no
    supplier can be recovered from these rows"* — so 179 sales rows were
    attributed to a supplier named `nan`. It round-tripped invisibly because
    `read_csv` maps `nan` back to NULL. Now `dtype=str` + `fillna("")`, which
    is what `step1_apply_mapping.py` does with the same file. The mapping file
    itself is untouched (`CLAUDE.md`).

    Both now match the vault byte-for-byte, which also confirms the earlier
    "take the vault's copy" calls on these two were the right ones.
- **`vault.py`'s guard stopped at depth 1, and `lock` and `check-commit`
  disagreed about it.** `is_protected` pairs fnmatch with a depth check — the
  check is what makes `*.xlsx` mean the repo root rather than every folder — so
  `data/pre_contract/*.csv` were outside the guard entirely, two store-derived
  files tracked in plain form while the hook reported nothing to refuse. The
  data/docs patterns are now `RECURSIVE` and opt out of the depth check.

  Widening `is_protected` alone would have built a trap: `local_files()` globs
  with `Path.glob`, whose `*` stops at a directory boundary where fnmatch's does
  not, so the hook would have refused a file the vault could never store —
  neither committable nor vaultable. `local_files()` uses `rglob` for those
  patterns now. Both files are vaulted and untracked; **0 tracked files at any
  depth would be refused**.

  `.gitignore` had the identical hole for the identical reason — a gitignore
  `*` also stops at a directory boundary, so `data/*.csv` never covered
  `data/pre_contract/`. Untracking the two files surfaced it immediately: they
  reappeared as untracked rather than disappearing. `data/**/*.csv` and its two
  siblings close it. The two lists have to agree, or the hook refuses a file
  git is happy to stage.
- **A dead parameter that made a flag look live.**
  `validate_policy_holdout.fit()` took `q`, was handed `args.buffer_quantile` by
  both callers, and never passed it on — `resolve_rates` settles the rate, and
  the buffer is applied later at each SKU's own tier quantile. That is why
  `--buffer-quantile` appeared to control the tiered arm and did not; a sweep
  over 0.70–0.85 returned byte-identical results at every tier setting. Removed.
  The flag stays live for `step4b`/`step5`.

- **The vault pre-commit hook was not enabled, and could not be** (was open
  issue 10 — number left vacant above, as 8 is, since `README.md` and this file
  cite the others by number). `.githooks/pre-commit` refuses any commit that
  stages a plain protected file — the guard against exactly what put five client
  workbooks on `origin/gambe` (issue 11). It is on now, and all three things
  that were in the way are cleared.
  - **`cryptography` was undeclared** in all four `requirements/` files, so
    `vault.py` died with `ModuleNotFoundError` instead of saying what was wrong
    — the case `requirements.txt`'s own header exists to catch. Pinned at
    `cryptography==50.0.1`. `/usr/bin/python3` carries it too, so the hook does
    not depend on having the venv on PATH.
  - **`USTORE_KEY` arrived** (2026-09-30), in `backend/.env`, which `.gitignore`
    covers. `check_commit()` calls `load_keys()` before it inspects anything
    staged, so without the key it refused *every* commit, not just bad ones.
  - **Nine result CSVs were tracked in plain form** and the hook would have
    refused them — `data/cold_start_donor{,_origins}.csv`,
    `data/inventory_{simulation,stock_depth,synthetic_cover}.csv`,
    `data/policy_holdout_{comparison,frontier,origins}.csv`,
    `docs/SESSION_RESULTS_2026-09-23.csv`. They are in `vault/` now and
    untracked (`git rm --cached`, files left on disk).
  - **And the hook file was committed `100644`.** Enabling `core.hooksPath` gave
    a hook git silently declines to run: it prints *"the hook was ignored
    because it's not set as executable"* as a **hint** and commits anyway.
    Found by turning it on and watching the first commit sail through with plain
    data staged. Now `100755`, which git tracks, so it is fixed for everyone
    rather than per clone. **A configured-but-inert hook is worse than no hook**
    — it reports success either way.
  - Tested in both directions rather than assumed: `git add -f
    data/cold_start_donor.csv` then commit is refused and HEAD does not move;
    an ordinary commit passes.
- **The seven vault conflicts.** `vault.py` has no per-file flag and both
  blanket flags (`lock --prefer-local`, `unlock --prefer-vault`) destroy one
  side wholesale, but `status()` tests `cur == man` *before* the conflict
  branches — so writing the vault's bytes to a local path flips that file to
  `ok` and out of the conflict set. That is how these were taken one at a time:
  the vault's copy for `robust_metric_comparison.csv` (two genuinely different
  runs — **still needs Neil's word on which is current**),
  `rebuild_sales_long.csv` (358 rows wrote the string `nan` where the vault
  writes an empty field), `USTore_sales_long_with_zeros.csv` (CRLF vs LF) and
  `model_benchmark_summary.csv` (float precision); the local copy for
  `category_assignment_audit.csv` and `demand_clusters.csv` (the vault's say
  `Uncategorised` where the current rules say `Shirts & Tops`) and for
  `ustore.db` (the vault held Neil's Sept-29 database; it now holds the rebuilt
  one, and his stays recoverable at `4d8d34a:vault/ustore.db.enc`, blob
  `db4a65f7`). `vault.py status` reads 165 ok, 0 conflict, 0 new.
- **`storage_category` was wiped by every `step1` run and never restored**
  (was open issue 8 — the number is left vacant above rather than reused, since
  `README.md` and this file both cite the others by number).
  `step1_apply_mapping.py:360` does `DELETE FROM Dim_Product` and re-appends
  `build_dim_product()`'s ten columns, which do not include `storage_category`
  or `forecast_category`, so both went NULL on every run after the first.
  `step1b`'s backfill was guarded on the column being **absent**, so it fired
  exactly once and could never heal a column that existed and was empty. From
  the second pipeline run onward `category.storage_category_sql()`'s COALESCE
  fell through to `category`, which `step1b` had just overwritten with the
  semantic label — the collision `085e01f` fixed, arriving one run later.
  - **Found by actually running the pipeline twice**, not by reading it.
    `Result_Prescriptive` came back bit-identical both times (the two
    lead-time tiers `category` separates are both 18 days), which is why it
    had gone unnoticed — but the cold-start `category` donor rule moved from
    fill **0.1412**, +0.0010 against its matched-stock control, to **0.1384**,
    −0.0006, inverting `docs/COLD_START_ANALOG.md`. 517/517 tests, 22/22
    invariants and the acceptance verdict were all unchanged while that
    happened.
  - **Fixed** by backfilling on the value rather than on the column's
    existence, with `category NOT IN (<step1b's own labels>)` so a re-run of
    `step1b` alone — or a row added through the Tally Interface, which
    `backend/app.py` defaults to `"Uncategorised"` — can never write a
    forecast category into the storage slot.
  - **`tests/test_step1b_storage_category.py` is the gate that was missing.**
    Three cases against an in-memory fixture: first run, second pipeline run,
    and `step1b` alone. The middle one fails without the fix
    (`('Outerwear', None) == ('Outerwear', 'APPAREL')`), which is the check
    that makes the other two worth having. `tests/test_cold_start_donor.py`
    could never have caught this — it is synthetic by design, "no ustore.db -
    so these pin the REASONING".
- **Prophet.** Superseded rather than fixed: step 4 no longer uses Prophet at
  all, so the `cmdstan` question (Block 5 / B5) is closed. Nothing in the repo
  imports `prophet`. See `docs/ROLLING_MEAN_FORECAST.md`.
- **`Overview.jsx`'s "Items Below / Near ROP" KPI was stale.** It now computes
  the same reorder-now count `Reorder.jsx` does (stock ≤ reorder point, both
  real), with a copy pass across the screen.
- **No PDF export on the Batch Sales Report.** Now server-rendered by
  `backend/batch_pdf.py` at `GET /api/reports/batch.pdf?month=YYYY-MM`
  (`&inline=1` to view rather than download). Four things worth keeping:
  - The blocker was the dependency, not the work — `weasyprint` needs
    GTK/Pango/Cairo on Windows and `reportlab` ships a C extension.
    **`fpdf2` is pure Python from a plain wheel.**
  - The PDF and the on-screen report share one builder
    (`app.build_batch_report`), so they cannot disagree — both report 15
    suppliers / 114 line items / 2,637 units for 2026-04.
  - **Latin-1, deliberately.** fpdf2's built-in Helvetica is Latin-1 and
    embedding a Unicode TTF would mean shipping a licensed font. All 539
    catalogue names are already Latin-1, so nothing is lost; money prints as
    `PHP 1,234.00` because ₱ (U+20B1) is not.
  - Totals are **unit counts**, not peso figures — the BIR constraint holds:
    this is an internal counting document, not an invoice.
- **`ustore.db` predated `Result_Prescriptive` / `Closure_Log` / the Wave 1
  schema.** It had never been rebuilt since the original ETL work, and
  `backend/db.py`'s unconditional `CREATE INDEX ... ON Result_Prescriptive`
  meant every API call 500'd. Rebuilt from scratch; every documented invariant
  reproduced exactly. **A stale-but-present database fails differently, and less
  visibly, than a missing one** — that lesson generalised into open issue 6.
