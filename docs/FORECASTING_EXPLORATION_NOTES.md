# Forecasting accuracy exploration — findings and open work

Session summary after merging `marco` into `neil`: what was tested, what
actually worked, what broke, and what's left to do. Written to be picked up
by anyone on the team, not just re-derived from chat history.

## What we did

1. **Merged `origin/marco` into `neil`.** Resolved 5 conflicts: kept
   `forecasting/ml_models.py` as two separate, incompatible modules
   (`ml_models.py` for the per-SKU/pooled-category experiments this
   branch already had, `ml_models_fastmoving.py` for marco's direct
   30-day-total regressors), and took marco's category-first Forecast UI
   (`App.jsx`, `Forecast.jsx`) and the `ComboBox` component
   (`TallyInterface.jsx`, `redesign.css`).

2. **Benchmarked marco's ML models** (`ml_models_fastmoving.py`:
   XGBoost, LightGBM, Random Forest, Ridge) against the existing rolling-
   average baselines. All of them did worse — none beat `rolling_mean_30`
   or `tsb` on the CLEAN fast-moving benchmark. Ridge (the linear
   control) did worst of all, confirming the gap isn't about model class.

3. **Checked marco's demand-behaviour clustering**
   (`scripts/cluster_demand_profiles.py`, k=5 chosen by silhouette). The
   script itself never saved its own quality numbers, so we recomputed
   them from `data/demand_clusters.csv`: **silhouette 0.239** (below the
   script's own 0.25 "weak structure" threshold) and **purity 0.525**
   (clusters do cut across semantic categories, which is the point of
   trying it — the structure is just noisy).

4. **Verified the existing cluster-based pooling (k=4,
   `forecasting/clustering.py`)** beats category+speed-based pooling for
   every pooled ML method (30–49% lower MASE), though pooled models still
   never beat the best single per-SKU baseline (`weekly_hurdle_12w`,
   MASE 1.625).

5. **Verified cleaning is not the bottleneck.** CLEAN vs RAW stage
   comparison in `data/fastmoving_benchmark_summary.csv`: cleaning cuts
   MASE ~30–45% across almost every method. Real, but not sufficient on
   its own — even the best CLEAN-stage method sits at MASE 3.45.

6. **Rebuilt the local DB with marco's `forecast_category` pipeline**
   (`step1b_categorize_products.py` → `migrate_category_to_forecast.py`)
   and ran `forecast_category_prophet.py` live. Reproduced the documented
   result: summing sales into ONE series per category, then forecasting
   that series (instead of forecasting each item and adding the
   forecasts up) gets **8 of 12 categories to beat naive** — the best
   result in the project so far. `forecast_category` values matched
   marco's original run byte-for-byte across all 519 products.

7. **Confirmed why MAPE can't be trusted here**: not zero-division (no
   category had an undefined MAPE), but small actual volumes in the
   small categories (Apparel Accessories, Home & Novelty) blowing up the
   percentage for a normal-sized miss. MASE (denominator = naive
   forecast's own error) is the metric that actually reflects accuracy.

8. **Built `scripts/category_split_prophet.py`** — the 4 categories that
   still don't beat naive (Shirts & Tops, Lanyards & IDs, Home & Novelty,
   Uncategorised) split into Fast/Slow/Non-moving sub-series, fold-scoped
   (`forecasting.category.fold_scoped_fsn_labels`, new — recomputes the
   bucket from only pre-fold-origin sales, no lookahead), each
   sub-series forecast separately and summed back. An earlier, quick
   non-fold-scoped version of this same idea looked much better (17%
   improvement on Shirts & Tops) than the properly fold-scoped version
   (0.9%) — the quick version both used a static full-history label and
   double-counted the MASE denominator across sub-groups. Corrected
   result: helps Lanyards & IDs (+12%) and barely touches Shirts & Tops
   (+0.9%); actively hurts Home & Novelty (-126%) and Uncategorised
   (-23%), because those categories are too small for their sub-groups
   to still have enough volume — Home & Novelty's Fast bucket shrank to
   1 item, and the model's fallback (average of the last 30 days that
   item sold something) badly overshoots for a sparse item.

9. **Built `scripts/cluster_split_prophet.py`** — a fair, apples-to-apples
   test of "does grouping by demand-behaviour cluster beat grouping by
   semantic category, when both use the SAME technique (combine sales
   into one series, forecast that)?" Fold-scoped cluster assignment
   (reuses `forecasting.clustering.cluster_skus` + `match_clusters_to_
   reference`, same as `model_benchmark_category.py --by cluster`).
   Raw result: category-based (MASE 1.102) clearly beat cluster-based
   (MASE 2.699) on 10 shared fold origins. Diagnosed why: one 1–2 item
   cluster ("NEW CLAPPERS", "SCI TOTEBAG" — items that sell almost never
   but in huge bulk quantities when they do) got predicted at ~3,000
   units/month against an actual of 0, every fold — the same sparse-
   group fallback problem as #8, now hitting the cluster-based scheme.
   **Excluding that one broken bucket: cluster-based MASE 1.027 vs.
   category-based 1.102 — clustering edges ahead.** Caveat: that
   exclusion isn't a fully rigorous fix (drops 2 items from one side's
   total rather than repairing their forecast), so treat this as
   promising, not proven.

## The core finding, in one line

**Combining sales into one series before forecasting (whichever grouping
you use) beats forecasting per item and adding the forecasts up
afterward** — the earlier per-item and pooled-training approaches never
touched the underlying noise; aggregating the target itself does. This
is proven for category-based grouping (8/12 beat naive) and looking
promising but unproven for cluster-based grouping.

## What's NOT done yet

- **The live dashboard still uses the OLD, worse method.**
  `/api/forecast/category` in `backend/app.py` sums each item's own
  forecast rather than using the validated category-level model. Never
  wired in — this is the single highest-leverage change sitting undone,
  and it comes with a real trade-off: the category total would stop
  matching the sum of items shown in the drill-down view. Needs a DB
  table for a genuine forward-looking category forecast (the current
  Prophet run only produces walk-forward *evaluation* data, not a
  "predict from today" snapshot), an endpoint change, and a UI decision
  on the drill-down inconsistency.

- **The sparse-group fallback bug needs a real fix, not a workaround.**
  Both split experiments (#8, #9) hit the same wall: when a sub-group
  shrinks to 1-2 items, the fallback ("average of days it actually
  sold") ignores the zero days in between and badly overshoots for rare/
  bulk-event items. This should average over all calendar days instead,
  or route very-sparse groups to a different, safer fallback. Fixing
  this is the blocker for trusting the cluster-based split result and
  for making category-splitting safe on small categories.

- **Cluster-based aggregate forecasting is promising but not proven.**
  Needs: the fallback fix above, then a clean re-run; also worth trying
  other values of k (only k=4 was tested) to see if a different cluster
  count avoids producing a degenerate 1-2 item cluster at all.

- **Shirts & Tops (the largest, most heterogeneous category, 136 items)
  barely moved (+0.9%) from a Fast/Slow/Non-moving split.** Worth trying
  a different split — e.g. `product_type` (clothes/drinkware/bags/...,
  unused exploratory code already in `forecasting/category.py`), or a
  finer cut than 3 buckets — before concluding splitting doesn't help it.

- **65 of 519 products (12.5%) are still "Uncategorised."** Extending the
  keyword rules in `step1b_categorize_products.py` would shrink the one
  category that's a noisy grab-bag by construction.

- **~10 day-status classifications in `data/day_status_vocabulary.csv`
  are unreviewed** (e.g. "ELECTION DAY", "AFTERNOON SELLING FRAY MHEL'S
  BDAY") — needs someone with real store operating history, not code.

- **None of this is written up for the manuscript yet.** The aggregation
  ladder (per-SKU → pooled → category → cluster) is the strongest result
  in the project and currently lives only in script docstrings and this
  file.
