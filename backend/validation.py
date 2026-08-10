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
