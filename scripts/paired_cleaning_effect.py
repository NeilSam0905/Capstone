"""
scripts/paired_cleaning_effect.py
------------------------------------------------------------------
The controlled half of the raw-vs-clean question, and the correction to
the obvious way of reading it.

`benchmark_fast_raw_vs_clean.py` compares the RAW Fast segment against
the CLEAN Fast segment. That comparison is real and operationally
meaningful, but it confounds two different things:

  (a) cleaning changed the SERIES  - allocation moved units between SKUs,
      the vocabulary merged two spellings into one history;
  (b) cleaning changed the ROSTER  - a different set of items ends up
      classified Fast, so the two stages score different products.

This script separates them. For every canonical item whose name appears
verbatim in the raw workbooks (an exact 1:1 pair, no merge to argue
about), it compares the raw daily series against the cleaned one:

  * IDENTICAL pairs      cleaning provably did not touch this history.
                         Any raw-vs-clean error difference for these
                         items is exactly zero, by construction.
  * CHANGED pairs        allocation or a merge rewrote the series. These
                         are the ONLY items where a paired forecast
                         comparison has anything to measure - so they
                         are re-scored on both versions, same folds,
                         same methods, and the deltas are reported per
                         item.

Reading the output
------------------
If most pairs are identical, then most of the headline raw-vs-clean gap
is effect (b) - the roster - not effect (a). That is a finding about
what the ETL is FOR, not a defect: section 3.1.3's cleaning exists to
get the right items in front of the forecaster and the units attributed
to the right SKU, not to smooth anybody's time series.

Writes two sheets into the workbook produced by
`benchmark_fast_raw_vs_clean.py` (which must be run first), leaving the
existing sheets untouched.

Run (from the repo root):
    python scripts/paired_cleaning_effect.py
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "_bm", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "benchmark_fast_raw_vs_clean.py"))
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

from forecasting.evaluate import evaluate_methods

DB_PATH = "ustore.db"
WORKBOOK = "data/USTore_FastMoving_Model_Benchmark.xlsx"


def paired_methods(quick=False):
    """A representative slice of the full method list, not all 26.

    The paired set is at most a couple of dozen items; the point here is
    the DELTA between two versions of the same series, and that delta is
    already visible on a handful of methods spanning the families
    (trailing average, intermittent, hurdle, boosted trees). Re-running
    all 26 would add runtime without adding an answer.
    """
    m = {
        "naive": bm.naive_fit_predict(),
        "RM3_3day": bm.rolling_mean_fit_predict(3),
        "RM6_6day": bm.rolling_mean_fit_predict(6),
        "rolling_mean_30": bm.rolling_mean_fit_predict(30),
        "RM3_3month_90d": bm.rolling_mean_fit_predict(90),
        "RM6_6month_180d": bm.rolling_mean_fit_predict(180),
        "rolling_median_30": bm.rolling_median_fit_predict(30),
        "ewma_a0.1": bm.ewma_fit_predict(0.1),
        "tsb": bm.tsb_fit_predict(bm.ALPHA, bm.BETA),
        "weekly_hurdle_12w": bm.weekly_hurdle_fit_predict(12),
    }
    if not quick:
        try:
            from forecasting.ml_models_fastmoving import (
                lightgbm_fit_predict, ridge_fit_predict, xgboost_fit_predict,
            )
            m["ridge"] = ridge_fit_predict()
            m["xgboost"] = xgboost_fit_predict()
            m["lightgbm"] = lightgbm_fit_predict()
        except ImportError:
            pass
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default=WORKBOOK)
    ap.add_argument("--quick", action="store_true", help="baselines only")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    clean_series, clean_meta, index = bm.load_clean(con)
    products = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class FROM Dim_Product", con)
    imputed = pd.read_sql_query(
        "SELECT product_id, SUM(imputation_flag) AS imputed_rows, "
        "COUNT(*) AS fact_rows FROM Fact_Sales GROUP BY product_id", con)
    con.close()
    raw_series, _, _ = bm.load_raw(index)

    # ---- pair up on the exact name ----------------------------------
    rows = []
    for _, r in products.iterrows():
        nm, pid = r["item_name"], r["product_id"]
        if nm not in raw_series or pid not in clean_series:
            continue
        a, b = raw_series[nm], clean_series[pid]
        rows.append({
            "product_id": pid, "item_name": nm, "fsn_class": r["fsn_class"],
            "category": bm.categorise(nm),
            "raw_units": float(a.sum()), "clean_units": float(b.sum()),
            "units_moved_by_cleaning": float(np.abs(a - b).sum()),
            "identical": bool(np.allclose(a, b)),
        })
    pairs = pd.DataFrame(rows).merge(imputed, on="product_id", how="left")
    pairs["imputed_rows"] = pairs["imputed_rows"].fillna(0).astype(int)
    pairs["pct_units_moved"] = np.where(
        pairs["raw_units"] > 0,
        100.0 * pairs["units_moved_by_cleaning"] / pairs["raw_units"], np.nan)

    changed = pairs[~pairs["identical"]].copy()
    print(f"Exact-name pairs: {len(pairs)}  "
          f"identical after cleaning: {int(pairs['identical'].sum())}  "
          f"changed: {len(changed)}")
    print(f"  of the changed, Fast: {int((changed['fsn_class'] == 'F').sum())}")
    if changed.empty:
        print("Nothing to score - cleaning changed no matched series.")
        return 0

    # ---- score both versions of each changed item -------------------
    methods = paired_methods(args.quick)
    print(f"Scoring {len(changed)} changed items x 2 versions x "
          f"{len(methods)} methods ...")

    raw_sub = {r.item_name: raw_series[r.item_name] for r in changed.itertuples()}
    clean_sub = {r.item_name: clean_series[r.product_id] for r in changed.itertuples()}

    t0 = time.time()
    raw_res, _ = evaluate_methods(raw_sub, methods, bm.HORIZON, bm.MIN_FOLDS,
                                  bm.MAX_FOLDS, bm.MIN_TRAIN)
    clean_res, _ = evaluate_methods(clean_sub, methods, bm.HORIZON, bm.MIN_FOLDS,
                                    bm.MAX_FOLDS, bm.MIN_TRAIN)
    print(f"  scored in {time.time() - t0:.0f}s")

    rp = bm.per_sku_metrics(raw_res).rename(columns={"sku": "item_name"})
    cp = bm.per_sku_metrics(clean_res).rename(columns={"sku": "item_name"})
    keep = ["item_name", "method", "mae", "rmse", "mase", "wmape_pct"]
    paired = cp[keep].merge(rp[keep], on=["item_name", "method"],
                            suffixes=("_clean", "_raw"))
    for m in ("mae", "rmse", "mase", "wmape_pct"):
        paired[f"delta_{m}"] = paired[f"{m}_clean"] - paired[f"{m}_raw"]
        paired[f"pct_change_{m}"] = 100.0 * paired[f"delta_{m}"] / paired[f"{m}_raw"]
    paired = paired.merge(
        changed[["item_name", "fsn_class", "category", "units_moved_by_cleaning",
                 "pct_units_moved"]], on="item_name", how="left")
    paired = paired.sort_values(["item_name", "mase_clean"]).reset_index(drop=True)

    # method-level summary of the paired deltas: does cleaning help the
    # SAME item, averaged over the items it actually touched?
    by_method = (paired.groupby("method")
                 .agg(n_items=("item_name", "nunique"),
                      wmape_pct_raw=("wmape_pct_raw", "mean"),
                      wmape_pct_clean=("wmape_pct_clean", "mean"),
                      mae_raw=("mae_raw", "mean"), mae_clean=("mae_clean", "mean"),
                      rmse_raw=("rmse_raw", "mean"), rmse_clean=("rmse_clean", "mean"),
                      mase_raw=("mase_raw", "mean"), mase_clean=("mase_clean", "mean"),
                      median_pct_change_mae=("pct_change_mae", "median"),
                      items_improved_wmape=("delta_wmape_pct", lambda s: int((s < 0).sum())),
                      items_worsened_wmape=("delta_wmape_pct", lambda s: int((s > 0).sum())),
                      items_improved_mae=("delta_mae", lambda s: int((s < 0).sum())),
                      items_worsened_mae=("delta_mae", lambda s: int((s > 0).sum())))
                 .reset_index())
    for m in ("wmape_pct", "mae", "rmse", "mase"):
        by_method[f"pct_change_{m}"] = (
            100.0 * (by_method[f"{m}_clean"] - by_method[f"{m}_raw"])
            / by_method[f"{m}_raw"])
    by_method["pct_items_improved_wmape"] = (
        100.0 * by_method["items_improved_wmape"]
        / (by_method["items_improved_wmape"]
           + by_method["items_worsened_wmape"]).replace(0, np.nan))
    # ordered on the SCALE-FREE metric - see the note printed below for
    # why ordering this table on MAE would invert its conclusion
    by_method = by_method.sort_values("pct_change_wmape_pct").reset_index(drop=True)

    raw_u = float(changed["raw_units"].sum())
    clean_u = float(changed["clean_units"].sum())

    print("\n" + "=" * 78)
    print("PAIRED effect of cleaning, on the items cleaning actually changed")
    print("=" * 78)
    print(f"These {len(changed)} items carry {raw_u:,.0f} units before cleaning and "
          f"{clean_u:,.0f} after ({100 * (clean_u - raw_u) / raw_u:+.1f}%): allocation "
          f"moves units INTO them\nfrom the price-grouped rows they were bundled in. "
          f"MAE therefore has to rise - the quantity being\nforecast is bigger. WMAPE "
          f"divides that out, so it is the column that answers whether the SAME\nitem "
          f"got easier to forecast. The two columns point in opposite directions here, "
          f"and only\none of them is answering the question.\n")
    print(by_method[["method", "n_items", "wmape_pct_raw", "wmape_pct_clean",
                     "pct_change_wmape_pct", "mae_raw", "mae_clean", "pct_change_mae",
                     "items_improved_wmape", "items_worsened_wmape"]]
          .to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # ---- append to the workbook -------------------------------------
    if not os.path.exists(args.workbook):
        print(f"\n{args.workbook} not found - run benchmark_fast_raw_vs_clean.py "
              f"first. Writing CSVs instead.")
        pairs.to_csv("data/paired_cleaning_pairs.csv", index=False, lineterminator="\n")
        paired.to_csv("data/paired_cleaning_effect.csv", index=False, lineterminator="\n")
        return 0

    profile = segment_profile(raw_series, clean_series)
    print("\n" + "=" * 78)
    print("Fast-segment profile, before and after cleaning")
    print("=" * 78)
    print(profile.to_string(index=False))

    append_sheets(args.workbook, {
        "Paired_Cleaning_Effect": by_method,
        "Paired_Per_Item": paired,
        "Series_Changed_By_ETL": pairs.sort_values(
            "units_moved_by_cleaning", ascending=False),
        "Segment_Profile": profile,
    })
    print(f"\nAppended 4 sheets to {args.workbook}")
    return 0


def segment_profile(raw_series, clean_series):
    """What the Fast segment looks like on each side of the ETL.

    This is the context every accuracy number in the workbook needs: a
    method's MAE only means something against how much demand the
    segment carries and how dense each item's history is. Both change
    when spelling variants are consolidated, and the direction of that
    change is the ETL's actual argument for itself.
    """
    out = []
    for label, series in (("RAW", raw_series), ("CLEAN", clean_series)):
        d, cutoff = bm.classify_fsn(series)
        f = d[d["fsn"] == "F"]
        total = float(d["units"].sum())
        out.append({
            "stage": label,
            "distinct_series": len(d),
            "fast_skus": len(f),
            "fast_share_of_skus_pct": round(100.0 * len(f) / len(d), 1),
            "total_units": total,
            "fast_units": float(f["units"].sum()),
            "fast_share_of_units_pct": round(100.0 * f["units"].sum() / total, 1),
            "adus_cutoff_80th": round(cutoff, 3),
            "median_adus_fast": round(float(f["ADUS"].median()), 2),
            "median_active_sale_days_fast": float(f["active_sale_days"].median()),
            "median_nonzero_day_frac_fast": round(float(f["nonzero_frac"].median()), 4),
            "median_units_per_fast_sku": float(f["units"].median()),
        })
    df = pd.DataFrame(out)
    delta = {"stage": "CHANGE (clean - raw)"}
    for c in df.columns[1:]:
        delta[c] = round(float(df[c].iloc[1] - df[c].iloc[0]), 3)
    return pd.concat([df, pd.DataFrame([delta])], ignore_index=True)


def append_sheets(path, sheets):
    """Add sheets to an existing workbook and re-apply the header style.

    `mode='a'` with `if_sheet_exists='replace'` so a re-run overwrites its
    own sheets instead of piling up 'Sheet1', 'Sheet11', ...
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(path, engine="openpyxl", mode="a",
                        if_sheet_exists="replace") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)

    import openpyxl
    wb = openpyxl.load_workbook(path)
    head_fill = PatternFill("solid", fgColor="1F3864")
    head_font = Font(color="FFFFFF", bold=True)
    for name in sheets:
        ws = wb[name]
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
        ws.freeze_panes = "A2"
        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            width = max((len(str(c.value)) for c in col[:200] if c.value is not None),
                        default=10)
            ws.column_dimensions[letter].width = min(max(width + 2, 10), 30)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, float):
                    c.number_format = "0.000" if abs(c.value) < 1000 else "#,##0.0"
    wb.save(path)


if __name__ == "__main__":
    raise SystemExit(main())
