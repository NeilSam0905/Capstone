"""
The models the Demand Forecast page compares (scripts/forecast_options.py),
as the API serves them.

Run Forecast makes every one: the recommended model into the tables the whole
dashboard reads, every other into the comparison tables (step4d). The page's
dropdown only chooses which of those the page SHOWS - nothing is saved, and
no other page changes - so `source()` says which tables a requested model's
forecast is read from.
"""
import re

from forecast_options import CATEGORY, CHECK_TABLE, COMPARE_TABLES, DEFAULTS, ITEM, OPTIONS  # scripts/

_SHAPE_SUFFIX = re.compile(r"\+[a-z]+_shape$")


def label(scope, model_type):
    """The plain name of a model as stored in a forecast table's model_type.
    step4c appends the day shape it used ("+weekday_shape") and step4 may fall
    back to a plainer shape, so the stored name is matched with and without
    it. Anything else is shown as stored."""
    if not model_type:
        return None
    by_id = {o["id"]: o["label"] for o in OPTIONS[scope]}
    return by_id.get(model_type) or by_id.get(_SHAPE_SUFFIX.sub("", model_type)) or model_type


def source(scope, model):
    """None to read the main tables (no model asked for, or the recommended
    one); otherwise the comparison tables (forecast, metrics) to read that
    model's rows from, filtered on their `model` column. ValueError for a
    model the page does not offer."""
    if not model or model == DEFAULTS[scope]:
        return None
    if model not in {o["id"] for o in OPTIONS[scope]}:
        raise ValueError(f"{model!r} is not a {scope} forecast model.")
    return COMPARE_TABLES[scope]


def made(con):
    """{scope: {model: snapshot_date}} for the comparison models Run Forecast
    has made so far. The recommended model is not listed: it is in the main
    tables, and present whenever there is a forecast at all."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {ITEM: {}, CATEGORY: {}}
    for scope, (forecast_table, _metrics) in COMPARE_TABLES.items():
        if forecast_table in tables:
            out[scope] = dict(con.execute(
                f"SELECT model, MAX(snapshot_date) FROM {forecast_table} GROUP BY model").fetchall())
    return out
