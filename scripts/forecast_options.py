"""
The forecast models the Demand Forecast page offers for comparison, with
names a store reader can follow.

Run Forecast makes every one of them: the recommended model into the tables
the whole dashboard reads (step4_forecast_model.py / step4c_category_forecast.py
as they always ran), every other into the comparison tables
(step4d_forecast_comparison.py, which runs those scripts with --compare).
The Demand Forecast page's model dropdown switches between them; nothing
else on the dashboard reads the comparison tables.

Every id must be one the script accepts as --model, and the recommended one
its DEFAULT_MODEL; tests/test_forecast_models.py checks both.
"""
ITEM, CATEGORY = "item", "category"

# `desc` is what the Demand Forecast page says a model does: in its menu on
# hover and in the (i) beside it. Written for store staff, not analysts - what
# the model looks at and what it assumes, with no method names.
_PROPHET = ("Looks for patterns that repeat (busier days of the week, busier months, school breaks) "
            "and carries them forward. It needs several years of sales to learn them and the store has "
            "about two, so it did worse in testing.")

OPTIONS = {
    ITEM: [
        {"id": "topdown_tsb+calendar+weekday_shape", "label": "Category Blend + School Calendar",
         "recommended": True,
         "desc": "Averages two guesses: one from how the item's whole category sold over the last 6 months "
                 "and the item's share of it last month, one from the item's own recent sales. Then it is "
                 "lowered when the school calendar shows breaks or exams ahead. Chosen from 47 methods "
                 "compared on the last 12 months of sales."},
        {"id": "topdown_tsb+weekday_shape", "label": "Category Blend",
         "desc": "The same two guesses averaged, without the school-calendar adjustment."},
        {"id": "tsb", "label": "TSB (Recent Sales Trend)",
         "desc": "Looks at how often the item has sold lately and how many it sells each time, paying most "
                 "attention to the latest weeks. If the item stops selling, its forecast slowly goes down."},
        {"id": "ewma_a0.1", "label": "Weighted Average",
         "desc": "An average of past sales in which recent days count more than older ones."},
        {"id": "rolling_mean_30", "label": "Last 30 Days Average",
         "desc": "Assumes the next 30 days will sell the same as the last 30 days."},
        {"id": "prophet", "label": "Prophet", "desc": _PROPHET},
    ],
    CATEGORY: [
        {"id": "RM6_6month_180d+calendar", "label": "6-Month Average + School Calendar",
         "recommended": True,
         "desc": "Takes the category's average sales per day over the last 6 months and expects that to "
                 "continue, lowered when the school calendar shows breaks or exams ahead. Chosen from 27 "
                 "methods compared on the last 12 months of sales."},
        {"id": "RM6_6month_180d", "label": "6-Month Average",
         "desc": "The category's average over the last 6 months, without the school-calendar adjustment."},
        {"id": "RM3_3month_90d", "label": "3-Month Average",
         "desc": "The category's average over the last 3 months: it follows a change sooner, but jumps "
                 "around more."},
        {"id": "rolling_mean_30", "label": "Last 30 Days Average",
         "desc": "Assumes the next 30 days will sell the same as the category's last 30 days."},
        {"id": "tsb", "label": "TSB (Recent Sales Trend)",
         "desc": "Looks at how often and how much the category has sold lately, paying most attention to "
                 "the latest weeks."},
        {"id": "prophet", "label": "Prophet", "desc": _PROPHET},
    ],
}
DEFAULTS = {scope: next(o["id"] for o in opts if o.get("recommended")) for scope, opts in OPTIONS.items()}

# The comparison tables each scope's alternatives are written to: the main
# tables' columns plus `model`, the --model the rows came from.
COMPARE_TABLES = {
    ITEM: ("Result_Forecast_Compare", "Result_Forecast_Metrics_Compare"),
    CATEGORY: ("Result_Category_Forecast_Compare", "Result_Category_Forecast_Metrics_Compare"),
}


# Each past 30-day check behind a model's accuracy figures: what it forecast
# for that period and what actually sold, one row per fold, for the Demand
# Forecast page's Actual vs Predicted (the adviser, Nov 2026). Written by the
# same run that writes the metrics, so the two always describe the same
# checks. One table for both scopes and every model, the recommended one
# under its own id.
CHECK_TABLE = "Result_Forecast_Check"


def check_rows(key, folds, index, model_rows, naive_rows, snapshot_date):
    """The CHECK_TABLE rows for one item or category: `folds` and the two
    walk_forward_evaluate row lists scored on them (the model's and the
    naive forecast's), `index` the series' dates (so a fold's positions
    become the calendar days it covered)."""
    out = []
    for f, m, n in zip(folds, model_rows, naive_rows):
        out.append({
            "series_key": str(key), "fold": int(f.fold_index),
            "start_date": index[f.test_start].strftime("%Y-%m-%d"),
            "end_date": index[f.test_end - 1].strftime("%Y-%m-%d"),
            "actual_30d": float(m["actual_30d"]), "pred_30d": float(m["pred_30d"]),
            "naive_30d": float(n["pred_30d"]), "snapshot_date": snapshot_date,
        })
    return out


def write_checks(con, scope, model, rows):
    """Replace one model's checks for a scope. Does not commit."""
    con.execute(f"""CREATE TABLE IF NOT EXISTS {CHECK_TABLE} (
        scope TEXT NOT NULL, model TEXT NOT NULL, series_key TEXT NOT NULL, fold INTEGER NOT NULL,
        start_date TEXT, end_date TEXT, actual_30d REAL, pred_30d REAL, naive_30d REAL,
        snapshot_date TEXT)""")
    con.execute(f"DELETE FROM {CHECK_TABLE} WHERE scope = ? AND model = ?", (scope, model))
    con.executemany(
        f"""INSERT INTO {CHECK_TABLE} (scope, model, series_key, fold, start_date, end_date,
                                       actual_30d, pred_30d, naive_30d, snapshot_date)
            VALUES (:scope, :model, :series_key, :fold, :start_date, :end_date, :actual_30d,
                    :pred_30d, :naive_30d, :snapshot_date)""",
        [{"scope": scope, "model": model, **r} for r in rows])


def alternatives(scope):
    """The ids Run Forecast makes for comparison: every offered one but the
    recommended, which the main step makes."""
    return [o["id"] for o in OPTIONS[scope] if o["id"] != DEFAULTS[scope]]


def write_compare(con, scope, model, forecast_rows, metric_rows):
    """Replace one model's comparison rows. `forecast_rows` / `metric_rows`
    are the dicts the script would write to its main tables; the comparison
    tables have the same columns plus `model`, created here from the main
    tables' own columns so the two can never differ. Does not commit."""
    main = {ITEM: ("Result_Forecast", "Result_Forecast_Metrics"),
            CATEGORY: ("Result_Category_Forecast", "Result_Category_Forecast_Metrics")}[scope]
    for src, dst, rows in zip(main, COMPARE_TABLES[scope], (forecast_rows, metric_rows)):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({src})")][1:]   # minus the id
        con.execute(f"CREATE TABLE IF NOT EXISTS {dst} (model TEXT NOT NULL, "
                    + ", ".join(cols) + ")")
        con.execute(f"DELETE FROM {dst} WHERE model = ?", (model,))
        if rows:
            names = [c for c in cols if c in rows[0]]
            con.executemany(
                f"INSERT INTO {dst} (model, {', '.join(names)}) VALUES (?, {', '.join('?' * len(names))})",
                [(model, *(r.get(c) for c in names)) for r in rows])
