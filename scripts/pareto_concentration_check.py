"""
scripts/pareto_concentration_check.py
------------------------------------------------------------------
Two questions about the 80/20 framing in sections 2.1.3 and 3.3.1, both
answered from data rather than assumed:

1. **Does the USTore catalogue actually exhibit an 80/20 split?**
   Section 2.1.3 invokes the Pareto Principle ("80 percent of outputs
   from 20 percent of inputs") as the theoretical basis for FSN
   classification, and section 3.3.1 sets the Fast cutoff at the 80th
   ADUS percentile on that reasoning. Whether the real catalogue is
   80/20, 70/20 or 90/10 is an empirical question nobody has checked.

2. **Do the forecasts REPRODUCE that concentration?**
   This is a different question from forecast error and it is the one
   procurement actually needs answered first. A model can have a bad
   MASE - wrong about how much sells THIS month - while still getting
   right which SKUs are the big ones and roughly what they are worth
   over a year. That is what sizes a consignment order and allocates
   attention across suppliers. Three measures:

     pred_top20_share    share of forecast demand in the top 20% of
                         SKUs BY FORECAST, against the same statistic
                         computed on actuals
     spearman_rho        rank correlation between forecast and actual
                         SKU totals - does the model order the catalogue
                         correctly, regardless of level
     total_pred_pct      total forecast units as a percent of total
                         actual units - aggregate bias, where 100 is
                         unbiased

   A method can score well on all three and still be useless for
   monthly reordering; the MASE tables in
   `data/fastmoving_benchmark_summary.csv` are what answer that. Both
   readings are needed, which is why this is a separate script rather
   than more columns on the summary.

Reads `data/fastmoving_benchmark_results.csv`; writes
`data/pareto_concentration.csv` and `data/pareto_curve.csv`.

Run (from the repo root):
    python scripts/pareto_concentration_check.py
------------------------------------------------------------------
"""
import argparse
import importlib.util
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "_bm", os.path.join(ROOT, "scripts", "benchmark_fast_raw_vs_clean.py"))
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

DB_PATH = "ustore.db"
RESULTS = "data/fastmoving_benchmark_results.csv"
OUT_CONC = "data/pareto_concentration.csv"
OUT_CURVE = "data/pareto_curve.csv"


def top_share(values, pct=0.20):
    """Share of the total held by the top `pct` of items, ranked by their
    own value. Scale-free, so a method that under-forecasts everything
    uniformly still scores its concentration correctly - which is the
    point: this measures SHAPE, and `total_pred_pct` measures level."""
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    if v.sum() <= 0:
        return np.nan
    k = max(1, int(round(len(v) * pct)))
    return 100.0 * v[:k].sum() / v.sum()


def pareto_curve(units):
    """The concentration curve, as the two readings people actually
    quote: 'top X% of SKUs hold Y% of units' and its inverse."""
    u = np.sort(np.asarray(units, dtype=float))[::-1]
    u = u[u > 0]
    cum = np.cumsum(u) / u.sum()
    n = len(u)

    rows = []
    for pct in (5, 10, 20, 25, 30, 40, 50):
        k = max(1, int(round(n * pct / 100)))
        rows.append({"reading": f"top {pct}% of SKUs", "n_skus": k,
                     "pct_of_skus": round(100.0 * k / n, 1),
                     "pct_of_units": round(100.0 * cum[k - 1], 1)})
    for tgt in (0.5, 0.8, 0.9, 0.95):
        k = int((cum < tgt).sum()) + 1
        rows.append({"reading": f"{int(tgt * 100)}% of units", "n_skus": k,
                     "pct_of_skus": round(100.0 * k / n, 1),
                     "pct_of_units": round(100.0 * cum[k - 1], 1)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="CLEAN", choices=["CLEAN", "RAW"])
    args = ap.parse_args()

    # ---- 1. is the catalogue 80/20? ---------------------------------
    con = sqlite3.connect(DB_PATH)
    series, meta, _ = bm.load_clean(con)
    con.close()
    units = pd.Series({k: v.sum() for k, v in series.items()})
    units = units[units > 0]
    curve = pareto_curve(units)
    curve.to_csv(OUT_CURVE, index=False, lineterminator="\n")

    fast = [p for p in meta.loc[meta["fsn_pipeline"] == "F", "product_id"]
            if p in series]
    fast_units = float(sum(series[p].sum() for p in fast))

    print("=" * 78)
    print(f"1. Concentration of the whole catalogue ({len(units)} SKUs with any sale, "
          f"{units.sum():,.0f} units)")
    print("=" * 78)
    print(curve.to_string(index=False))
    print(f"\nFSN Fast segment: {len(fast)} SKUs = "
          f"{100 * len(fast) / len(units):.1f}% of sellers, carrying "
          f"{fast_units:,.0f} units = {100 * fast_units / units.sum():.1f}% of demand")
    print(f"-> the split is closer to {curve.loc[2, 'pct_of_units']:.0f}/20 than to 80/20; "
          f"80% of units needs the top "
          f"{curve.loc[curve['reading'] == '80% of units', 'pct_of_skus'].iat[0]:.0f}% of SKUs")

    # ---- 2. do the forecasts reproduce it? --------------------------
    from scipy.stats import spearmanr

    r = pd.read_csv(RESULTS)
    r = r[(r["stage"] == args.stage) & (r["in_fast_set"])]
    # actuals are identical across methods by construction (identical
    # folds) - take one method's rows rather than summing all of them
    ref = r["method"].iloc[0]
    act = r[r["method"] == ref].groupby("sku")["actual_30d"].sum()
    actual_share = top_share(act)

    rows = []
    for method, g in r.groupby("method"):
        p = g.groupby("sku")["pred_30d"].sum().reindex(act.index).fillna(0.0)
        rows.append({
            "method": method,
            "pred_top20_share_pct": top_share(p),
            "actual_top20_share_pct": actual_share,
            "share_error_pp": top_share(p) - actual_share,
            "spearman_rho": float(spearmanr(act.to_numpy(), p.to_numpy()).statistic),
            "total_pred_pct_of_actual": 100.0 * p.sum() / act.sum(),
            "n_skus_forecast_zero": int((p <= 0).sum()),
            "n_skus": len(act),
        })
    conc = pd.DataFrame(rows)
    conc["abs_share_error_pp"] = conc["share_error_pp"].abs()
    conc = conc.sort_values("abs_share_error_pp").reset_index(drop=True)
    conc.to_csv(OUT_CONC, index=False, lineterminator="\n")

    print("\n" + "=" * 78)
    print(f"2. Do the forecasts reproduce that concentration? ({args.stage} stage, "
          f"{len(act)} Fast SKUs)")
    print("=" * 78)
    print(f"Actual: the top 20% ({max(1, round(len(act) * 0.2))} items) carry "
          f"{actual_share:.1f}% of scored demand\n")
    print(conc[["method", "pred_top20_share_pct", "share_error_pp", "spearman_rho",
                "total_pred_pct_of_actual", "n_skus_forecast_zero"]]
          .to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    print(f"\nWrote {OUT_CURVE} and {OUT_CONC}")
    print("""
Reading note. Getting the concentration right is a WEAKER claim than
forecasting well, and the two must not be conflated. A method can put
the right share of demand in the right SKUs over a year (good
spearman_rho, share_error near zero, total near 100%) while still being
badly wrong about which MONTH the units land in - which is what MASE in
data/fastmoving_benchmark_summary.csv measures, and where every method
here scores above 3. Cross-sectional accuracy supports annual sizing
and supplier allocation; it does not support monthly reorder timing.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
