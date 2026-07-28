"""
Open work item #2 — derive `cumulative_monthly_units` and `daily_depletion_rate`.

Input : USTore_sales_long_allocated_normalized.csv   (canonical, ISO dates)
        calendar_ranges.csv                          (is_store_closed source)
Output: USTore_fact_sales_derived.csv                (one row per product x date)

Grain
-----
The metrics are per-PRODUCT-per-DATE window functions, so the normalized sales
are first aggregated to (canonical_item_name, Date). This is required, not
cosmetic: 25 (canonical, date) pairs carry >1 source row (item-name merges +
18 items sold under >1 supplier string), and a running window over duplicate
keys would double-count. This matches the Fact_Sales grain — product_id already
encodes the supplier via Dim_Product, so supplier is not part of this grain.

Selling-day calendar (the depletion denominator)
------------------------------------------------
A calendar day counts as an active *selling day* iff:

    is_tally_date OR (NOT is_store_closed AND NOT Sunday)

i.e. a recorded tally is treated as direct evidence the store sold that day and
overrides both the Sunday and the store-closed exclusion. This is what resolves
the two known closed-day tallies (2025-06-12, 2025-11-30) and the 14 Sunday
tallies without dropping them: they are genuine selling days.

The four rules that PROJECT_CONTEXT.md §10.2 left undecided are implemented with
the suggested defaults, each exposed as a constant below so they can be flipped:
  #1 first-ever observation  -> FIRST_OBS_DENOM
  #2 Sundays                 -> folded into the selling-day predicate above
  #3 long gaps               -> LONG_GAP_DAYS (NULL the rate + flag, no smearing)
  #4 month boundaries        -> intervals span months; only the cumulative resets
"""
import pandas as pd

SALES_CSV = "USTore_sales_long_allocated_normalized.csv"
CALENDAR_CSV = "calendar_ranges.csv"
OUT_CSV = "USTore_fact_sales_derived.csv"

# --- rule knobs (see docstring / PROJECT_CONTEXT.md §10.2) ------------------
FIRST_OBS_DENOM = "month_start"   # 'month_start' | 'entry_date'
LONG_GAP_DAYS = 30                # calendar-day gap above which the rate is NULL
ROUND_DP = 4                      # decimal places for daily_depletion_rate


def build_selling_calendar(tally_dates, closed_dates, lo, hi):
    """Daily index [lo, hi] with a cumulative *inclusive* selling-day count, so
    the number of selling days in an interval is a single subtraction."""
    idx = pd.date_range(lo, hi, freq="D")
    is_tally = idx.isin(tally_dates)
    is_closed = idx.isin(closed_dates)
    is_sunday = idx.weekday == 6
    is_selling = is_tally | (~is_closed & ~is_sunday)
    cum = pd.Series(is_selling.astype(int), index=idx).cumsum()
    return cum


def main():
    sales = pd.read_csv(SALES_CSV)
    sales["Date"] = pd.to_datetime(sales["Date"], format="%Y-%m-%d")
    sales["Total Quantity"] = pd.to_numeric(sales["Total Quantity"])

    # ---- aggregate to (product, date) --------------------------------------
    grp = sales.groupby(["canonical_item_name", "Date"], as_index=False).agg(
        quantity_sold=("Total Quantity", "sum"),
        imputation_flag=("imputation_flag", "max"),   # 1 if any contributing row imputed
        n_source_rows=("Total Quantity", "size"),
    )
    grp = grp.sort_values(["canonical_item_name", "Date"]).reset_index(drop=True)

    units_in = int(sales["Total Quantity"].sum())
    units_out = int(grp["quantity_sold"].sum())
    assert units_in == units_out, f"units changed on aggregation: {units_in} != {units_out}"

    # ---- selling-day calendar ---------------------------------------------
    tally_dates = pd.DatetimeIndex(sales["Date"].unique())
    cal = pd.read_csv(CALENDAR_CSV)
    closed_ranges = cal[cal["flag"] == "is_store_closed"]
    closed_dates = pd.DatetimeIndex(
        [d for _, r in closed_ranges.iterrows()
         for d in pd.date_range(r["start_date"], r["end_date"], freq="D")]
    ).unique()

    lo = grp["Date"].min().to_period("M").to_timestamp()  # 1st of the earliest month
    hi = grp["Date"].max()
    cum = build_selling_calendar(tally_dates, closed_dates, lo, hi)

    def cum_incl(ts):                     # selling days from lo..ts inclusive
        return int(cum.loc[ts])

    def cum_before(ts):                   # selling days strictly before ts
        prev = ts - pd.Timedelta(days=1)
        return int(cum.loc[prev]) if prev in cum.index else 0

    # ---- cumulative_monthly_units (resets each calendar month) -------------
    grp["cumulative_monthly_units"] = grp.groupby(
        ["canonical_item_name", grp["Date"].dt.year, grp["Date"].dt.month]
    )["quantity_sold"].cumsum()

    # ---- daily_depletion_rate ---------------------------------------------
    grp["prev_obs_date"] = grp.groupby("canonical_item_name")["Date"].shift(1)
    is_first = grp["prev_obs_date"].isna()
    grp["first_obs_flag"] = is_first.astype(int)

    gap_days = (grp["Date"] - grp["prev_obs_date"]).dt.days
    grp["long_gap_flag"] = ((~is_first) & (gap_days > LONG_GAP_DAYS)).astype(int)

    selling_days = []
    for row in grp.itertuples(index=False):
        d = row.Date
        if pd.isna(row.prev_obs_date):
            if FIRST_OBS_DENOM == "entry_date":
                sd = 1                                   # the observation day itself
            else:  # month_start
                m0 = d.to_period("M").to_timestamp()
                sd = cum_incl(d) - cum_before(m0)
        else:
            sd = cum_incl(d) - cum_incl(row.prev_obs_date)  # days in (prev, d]
        selling_days.append(max(sd, 1))                     # d is a tally day -> >=1
    grp["selling_days_in_interval"] = selling_days

    grp["daily_depletion_rate"] = (
        grp["quantity_sold"] / grp["selling_days_in_interval"]
    ).round(ROUND_DP)
    grp.loc[grp["long_gap_flag"] == 1, "daily_depletion_rate"] = pd.NA

    # all current data is historical tally data
    grp["tally_date_flag"] = 1

    # ---- write -------------------------------------------------------------
    out = grp[[
        "canonical_item_name", "Date", "quantity_sold",
        "cumulative_monthly_units", "daily_depletion_rate",
        "selling_days_in_interval", "prev_obs_date",
        "first_obs_flag", "long_gap_flag", "tally_date_flag",
        "imputation_flag", "n_source_rows",
    ]].copy()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    out["prev_obs_date"] = out["prev_obs_date"].dt.strftime("%Y-%m-%d")
    out.to_csv(OUT_CSV, index=False)

    # ---- summary -----------------------------------------------------------
    n = len(out)
    rate = grp["daily_depletion_rate"]
    print("=== SUMMARY ===")
    print(f"input rows                 : {len(sales)}")
    print(f"output rows (product x date): {n}")
    print(f"units conserved            : {units_out} (was {units_in})")
    print(f"distinct products          : {out['canonical_item_name'].nunique()}")
    print(f"first-observation rows     : {int(grp['first_obs_flag'].sum())}")
    print(f"long-gap rows (rate NULL)  : {int(grp['long_gap_flag'].sum())}  (gap > {LONG_GAP_DAYS}d)")
    print(f"rate computed (non-NULL)   : {int(rate.notna().sum())}")
    print(f"daily_depletion_rate  min/median/max: "
          f"{rate.min():.3f} / {rate.median():.3f} / {rate.max():.3f}")
    print(f"selling_days_in_interval max: {int(grp['selling_days_in_interval'].max())}")
    # spot audit: cumulative should reset each month -> monthly max == monthly total
    chk = grp.groupby(["canonical_item_name", grp['Date'].dt.to_period('M')]).agg(
        last_cum=("cumulative_monthly_units", "last"),
        month_total=("quantity_sold", "sum")).reset_index()
    assert (chk["last_cum"] == chk["month_total"]).all(), "cumulative monthly reset broken"
    print("cumulative_monthly_units: monthly reset verified (last cum == month total for every product-month)")
    print(f"\nwrote {OUT_CSV}")


if __name__ == "__main__":
    main()
