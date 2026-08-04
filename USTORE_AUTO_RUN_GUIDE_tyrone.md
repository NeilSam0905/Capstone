# USTore — canonical run and verification guide (branch `tyrone`)

**Last updated:** 2026-08-05 · after Batch 2 (A12–A19)

This file exists because the checklist kept drifting. It lived only inside the run prompts, so each
batch corrected it in its own copy and the corrections did not survive into anything a reader would
find. **This is the canonical version.** Where a batch prompt disagrees with this file, this file is
right — every command below has been executed and its expected output recorded from a real run.

---

## Environment

Dependencies are pinned. Do not rely on a system or Anaconda environment having the right versions —
Batch 1 did, and it hid a missing `rapidfuzz` that made `build_vocab_mapping.py` unrunnable.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt              # runtime
pip install -r requirements-dev.txt          # pytest + the statsmodels reference
```

`requirements-prophet.txt` is separate on purpose: installing Prophet pulls in a cmdstan build, and
nothing in this checklist needs it. See **B5**.

---

## Rebuilding from scratch

```bash
rm ustore.db
python create_schema.py && python populate_dim_date.py && python step1_apply_mapping.py \
  && python proportional_allocation.py && python step2_load_fact_sales.py \
  && python step3_fsn_classification.py
```

Then `python tools/assert_invariants.py` → **21/21, exit 0**. That is the baseline contract.

---

## The checklist

Run from the repo root.

```bash
python tools/assert_invariants.py --phase a10   # 22/22, exit 0      (1)
python verify_data.py                            # exit 0
python tools/provenance_may2024.py               # 11 checks; TBS 4,022; DSR 4,318; net +751
python tools/tier_counts.py                      # 92/51/123 all-moving; 38/10/10 Fast-only
python tools/audit_price_suffix_skus.py          # 71 suffixed, 12 twins, 8 families
python tools/demand_basis_by_anchor.py           # 27 anchors; 2026-07 gives 79 @30d, 208 @365d
pytest tests/                                    # 345 passed
python model_benchmark.py                        # 8 methods, both ranking tables (~6 min)
python step5_prescriptive.py                     # 1,975 rows, N excluded, all gates pass

git status --porcelain                           # empty — no modified CSVs        (2)
git diff --stat HEAD -- '*.pdf' '*.xlsx'         # empty — binaries byte-exact     (3)
git log neil..tyrone --format='%B' | grep -Ei 'co-authored-by|claude-session|generated with'
                                                 # returns nothing
git shortlog -sne HEAD                           # one entry per person            (4)
```

### The four things that will otherwise mislead you

**(1) `--phase a10` after `step5_prescriptive.py` has run.**
Seeding `Dim_Parameters` with the provisional grid is a deliberate state change that breaks the
baseline `Dim_Parameters = 0` invariant. The bare form exits 1 and prints a note naming the flag —
that note is expected, not a regression. To re-check the true baseline, rebuild the database first.

**(2) `git status --porcelain` may show ` M` on CSVs that are byte-identical.**
After a pipeline re-run, git's stat cache can mark `USTore_sales_long_allocated.csv` and
`allocation_audit.csv` modified while `git diff` is empty. That is the stale-stat artifact of the
CRLF→LF transition, not a content change. Confirm with:

```bash
git diff --exit-code --quiet && echo "identical"
```

**(3) Binary assets must be byte-exact, not merely parseable.**
`USTore_Build_Plan.pdf` was silently corrupted on every Windows checkout before `.gitattributes`
existed — inflated 13,058 → 13,220 bytes, which shifted every absolute offset in its xref table. It
still *looked* like a PDF. Checking that the blob matches its committed bytes is the only test that
would have caught it.

**(4) `git shortlog -sne` needs the explicit `HEAD`.**
Without a revision, shortlog reads from stdin. In a script or a piped shell it silently prints
nothing, which looks exactly like a pass. Expected:

```
    17  Neil Sam Perez <pneilsam@gmail.com>
    2x  Tyrone Yazon <tyronegryneth.yazon.cics@ust.edu.ph>
```

---

## Pushing

Not done by any automated run. `.claude/settings.json` forces a prompt on `git push`.

```bash
git push -u origin tyrone
```

`neil`, `main` and `marco` are untouched. Verify with `git branch -vv` first.

---

## Where things are written down

| Question | File |
|---|---|
| What changed on this branch, and what is still open | `CHANGES_tyrone.md` |
| Every departure from Chapters 1–3 | `docs/DIVERGENCE_REGISTER.md` |
| Why `SUM(quantity_sold)` is 89,232 | `docs/DATA_PROVENANCE.md` |
| Why the most accurate method is unusable | `docs/DEGENERATE_FORECAST.md` |
| The build plan vs. what exists | `docs/BUILD_PLAN_RECONCILIATION.md` |
| The 71 price-suffixed SKUs | `docs/PRICE_SUFFIX_AUDIT.md` |
| How the demand basis varies by anchor | `docs/demand_basis_by_anchor.csv` |
