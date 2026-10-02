"""
order_types.py - what kind of sale a sale was: walk-in, bulk, or pre-order.

Bulk and organisation orders, and pre-orders, used to be tallied exactly like
walk-in sales. They are the largest single cause of forecast error tested:
knowing the big one-day purchases in advance would cut the fast-item error
from 64.5% to 38-51% (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 3.4). So a sale
now carries Fact_Sales.order_type:

    walk_in     an ordinary counter sale (the default; NULL means this too)
    bulk        a bulk or organisation order
    pre_order   collected against an earlier order

Where it is set:
  - the Tally Interface's Order Type field and an import's "Order Type" column
    (backend/app.py);
  - for history, step2 reads the store's answers in
    data/bulk_day_candidates.csv (column bulk_order_confirmed) and marks those
    item-days.

Who reads it: the FORECASTS train on walk-in sales only (walk_in_only), so a
700-unit organisation order no longer reads as a spike in everyday demand.
Classification, reorder points and the dashboard's sales figures still count
every sale - stock leaves the shelf either way.

Known orders are the other half. An organisation order the store already
knows about is recorded ahead of time in the Tally Interface (Upcoming_Order:
item, expected date, quantity), and both forecasts add it on top of everyday
demand on that date (add_known_orders). When it is collected it is tallied
as a SALE of type bulk / pre-order, so it never counts twice.

With nothing marked yet, every sale is walk-in and every result is unchanged.
"""
import pandas as pd

ORDER_TYPES = ("walk_in", "bulk", "pre_order")

# How a person may write each one in an import file or in
# bulk_day_candidates.csv; compared lower-case, without spaces, - or _.
_SPELLINGS = {
    "walk_in": {"walkin", "walk", "counter", "regular", "no", "n", "false", "0"},
    "bulk": {"bulk", "bulkorder", "organisation", "organization", "org", "organisationorder",
             "organizationorder", "yes", "y", "true", "1"},
    "pre_order": {"preorder", "pre", "reservation", "reserved"},
}


def normalize(value, default="walk_in"):
    """An order type from free text, `default` for a blank, None if unknown."""
    key = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    if not key:
        return default
    for order_type, spellings in _SPELLINGS.items():
        if key in spellings or key == order_type.replace("_", ""):
            return order_type
    return None


def has_column(con):
    return "order_type" in {r[1] for r in con.execute("PRAGMA table_info(Fact_Sales)")}


def ensure_column(con):
    """Add Fact_Sales.order_type to a database built before it existed."""
    if not has_column(con):
        con.execute("ALTER TABLE Fact_Sales ADD COLUMN order_type TEXT")


def walk_in_only(con, alias="f"):
    """SQL condition keeping walk-in sales, for the forecasts. On a database
    without the column every sale is walk-in, so it keeps everything."""
    if not has_column(con):
        return "1 = 1"
    return f"COALESCE({alias}.order_type, 'walk_in') = 'walk_in'"


def known_orders(con):
    """Upcoming orders as a DataFrame (product_id, forecast_category,
    forecast_date 'YYYY-MM-DD', quantity); empty without the table."""
    cols = ["product_id", "forecast_category", "forecast_date", "quantity"]
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Upcoming_Order'").fetchone():
        return pd.DataFrame(columns=cols)
    has_cat = "forecast_category" in {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    return pd.read_sql_query(f"""
        SELECT o.product_id, {'p.forecast_category' if has_cat else 'NULL'} AS forecast_category,
               o.expected_date AS forecast_date, o.quantity
          FROM Upcoming_Order o LEFT JOIN Dim_Product p ON p.product_id = o.product_id
    """, con)


def add_known_orders(forecast_df, orders, key):
    """Add known orders onto forecast rows matching on (key, forecast_date):
    yhat and its band move up by the order. `key` is "product_id" (item
    forecast) or "forecast_category". Returns (new frame, units added)."""
    if forecast_df.empty or orders.empty:
        return forecast_df, 0.0
    extra = orders.groupby([key, "forecast_date"])["quantity"].sum().rename("known_orders").reset_index()
    out = forecast_df.merge(extra, on=[key, "forecast_date"], how="left")
    add = out.pop("known_orders").fillna(0.0)
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        if col in out.columns:
            out[col] = out[col] + add
    return out, float(add.sum())
