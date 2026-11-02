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

OPTIONS = {
    ITEM: [
        {"id": "topdown_tsb+calendar+weekday_shape", "label": "Category Blend + School Calendar",
         "recommended": True,
         "desc": "Mixes the item's recent sales with its category's 6-month level, and lowers it when "
                 "the school calendar shows quieter days ahead. The most accurate in testing."},
        {"id": "topdown_tsb+weekday_shape", "label": "Category Blend",
         "desc": "The same mix, without the school-calendar adjustment."},
        {"id": "tsb", "label": "TSB (Recent Sales Trend)",
         "desc": "Follows how often and how much the item has sold lately, and fades when it stops selling."},
        {"id": "ewma_a0.1", "label": "Weighted Average",
         "desc": "An average of past sales that counts recent days more than older ones."},
        {"id": "rolling_mean_30", "label": "Last 30 Days Average",
         "desc": "Repeats the average of the last 30 days."},
        {"id": "prophet", "label": "Prophet",
         "desc": "A seasonal model with weekly and yearly patterns and the school calendar. "
                 "Less accurate in testing."},
    ],
    CATEGORY: [
        {"id": "RM6_6month_180d+calendar", "label": "6-Month Average + School Calendar",
         "recommended": True,
         "desc": "The category's average over the last 6 months, lowered when the school calendar shows "
                 "quieter days ahead. The most accurate in testing."},
        {"id": "RM6_6month_180d", "label": "6-Month Average",
         "desc": "The category's average over the last 6 months, without the school-calendar adjustment."},
        {"id": "RM3_3month_90d", "label": "3-Month Average",
         "desc": "The category's average over the last 3 months: quicker to follow a change, "
                 "and jumpier."},
        {"id": "rolling_mean_30", "label": "Last 30 Days Average",
         "desc": "Repeats the category's average of the last 30 days."},
        {"id": "tsb", "label": "TSB (Recent Sales Trend)",
         "desc": "Follows how often and how much the category has sold lately, counting recent days more."},
        {"id": "prophet", "label": "Prophet",
         "desc": "A seasonal model with weekly and yearly patterns and the school calendar. "
                 "Less accurate in testing."},
    ],
}
DEFAULTS = {scope: next(o["id"] for o in opts if o.get("recommended")) for scope, opts in OPTIONS.items()}

# The comparison tables each scope's alternatives are written to: the main
# tables' columns plus `model`, the --model the rows came from.
COMPARE_TABLES = {
    ITEM: ("Result_Forecast_Compare", "Result_Forecast_Metrics_Compare"),
    CATEGORY: ("Result_Category_Forecast_Compare", "Result_Category_Forecast_Metrics_Compare"),
}


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
