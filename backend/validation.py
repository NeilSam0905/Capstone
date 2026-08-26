"""
validation.py — server-side mirror of dataService.js's validateEntry().

The client copy is a convenience; this is the guarantee (BACKEND_TODO.md §2).
Same field->message contract so the frontend's existing inline-error
rendering needs no changes.
"""
import re
from datetime import date

TRANSACTION_TYPES = {"SALE", "DAMAGED", "PROMO", "TRANSFER"}
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def validate_entry(con, payload):
    errors = {}

    product_id = payload.get("product_id")
    if not product_id:
        errors["product_id"] = "Select an item."
    else:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            errors["product_id"] = "Select an item."
        else:
            if not con.execute(
                "SELECT 1 FROM Dim_Product WHERE product_id = ?", (product_id,)
            ).fetchone():
                errors["product_id"] = "Select an item."

    quantity_sold = payload.get("quantity_sold")
    if quantity_sold in (None, ""):
        errors["quantity_sold"] = "Enter a quantity."
    else:
        try:
            n = float(quantity_sold)
        except (TypeError, ValueError):
            errors["quantity_sold"] = "Quantity must be a number."
        else:
            if n != int(n):
                errors["quantity_sold"] = "Quantity must be a whole number."
            elif int(n) <= 0:
                errors["quantity_sold"] = "Quantity must be greater than zero."

    calendar_date = payload.get("calendar_date")
    if not calendar_date:
        errors["calendar_date"] = "Pick a date."
    elif not ISO_DATE_RE.match(calendar_date):
        errors["calendar_date"] = "Date must be YYYY-MM-DD."
    elif calendar_date > date.today().isoformat():
        errors["calendar_date"] = "Date cannot be in the future."
    elif not con.execute(
        "SELECT 1 FROM Dim_Date WHERE calendar_date = ?", (calendar_date,)
    ).fetchone():
        errors["calendar_date"] = "Date not found in the calendar."

    transaction_type = payload.get("transaction_type")
    if not transaction_type:
        errors["transaction_type"] = "Select a transaction type."
    elif str(transaction_type).upper() not in TRANSACTION_TYPES:
        errors["transaction_type"] = "Unknown transaction type."

    return errors


def validate_inventory_count(con, payload):
    """Monthly stock count from the interface's inventory card.

    Differs from validate_entry() in two ways that matter:

    - **Zero is valid.** A tally entry of 0 units is meaningless, but a
      stock count of 0 is the most operationally important count there is -
      it is the store saying "we are out of this". Rejecting it would throw
      away exactly the signal the reorder screen needs.
    - **A future month is not.** Same reasoning as the future-date check on
      tally entries: you cannot count stock you have not held yet. The
      current month is allowed, since a count taken today belongs to it.
    """
    errors = {}

    product_id = payload.get("product_id")
    if not product_id:
        errors["product_id"] = "Select an item."
    else:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            errors["product_id"] = "Select an item."
        else:
            if not con.execute(
                "SELECT 1 FROM Dim_Product WHERE product_id = ?", (product_id,)
            ).fetchone():
                errors["product_id"] = "Select an item."

    quantity = payload.get("quantity")
    if quantity in (None, ""):
        errors["quantity"] = "Enter the units on hand."
    else:
        try:
            n = float(quantity)
        except (TypeError, ValueError):
            errors["quantity"] = "Units on hand must be a number."
        else:
            if n != int(n):
                errors["quantity"] = "Units on hand must be a whole number."
            elif int(n) < 0:
                errors["quantity"] = "Units on hand cannot be negative."

    count_month = payload.get("count_month")
    if not count_month:
        errors["count_month"] = "Pick a month."
    elif not ISO_MONTH_RE.match(count_month):
        errors["count_month"] = "Month must be YYYY-MM."
    elif count_month > date.today().strftime("%Y-%m"):
        errors["count_month"] = "Month cannot be in the future."

    return errors
