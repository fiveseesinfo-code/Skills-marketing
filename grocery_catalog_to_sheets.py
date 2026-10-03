"""
Harvest a retail product catalog, classify each item against Canada's
front-of-package (FOP) "High in" nutrition symbol rules, then merge, de-duplicate
and sort the results into the Google Sheet:

    "Canadian Grocery & Restaurant Food Log - Health Warning Analysis"

Tabs written:
    Health Warning Foods      -> Product Number | Product Name | Regular Retail Price (CAD)
    No Health Warning Foods   -> Product Number | Product Name | Regular Retail Price (CAD)
    Undecided Products        -> Product Name | Retail Price (CAD) | Ambiguity / Notes
("Canadian Restaurant Menus" is left untouched.)

Setup:
    pip install requests pandas gspread
    Create a Google Cloud service account, download its JSON key, and share the
    spreadsheet with the service account's email (Editor).
"""
import re

import gspread
import pandas as pd
import requests

SPREADSHEET_ID = "1FmHOUhewVQRERrW5fiZg9zVkYmbZWregBcCQGGVml2c"

TAB_WARNING = "Health Warning Foods"
TAB_NO_WARNING = "No Health Warning Foods"
TAB_UNDECIDED = "Undecided Products"

STD_HEADER = ["Product Number", "Product Name", "Regular Retail Price (CAD)"]
UNDECIDED_HEADER = ["Product Name", "Retail Price (CAD)", "Ambiguity / Notes"]

# FOP threshold (% Daily Value per reference amount) for sat fat, sugars, sodium.
# 15% general, 10% for small reference amounts (<= 30 g/ml), 30% for main dishes.
FOP_THRESHOLD = {"general": 15, "small": 10, "main_dish": 30}
# Items within this many %DV points of the threshold go to "Undecided Products".
BORDERLINE_MARGIN = 2

# Sort order applied to every tab after merging: (column, ascending)
SORT_BY = [("Product Name", True)]


def _get(item, *paths):
    """Return the first non-empty value from dotted key paths, e.g. 'pricing.regularPrice'."""
    for path in paths:
        value = item
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value not in (None, ""):
            return value
    return None


def extract_retail_catalog(api_url, headers, params=None):
    """
    Template for harvesting product catalogs from public e-commerce endpoints.
    Adjust key names in _get() calls to match the store's API response structure.
    """
    response = requests.get(api_url, headers=headers, params=params, timeout=30)
    if response.status_code != 200:
        print(f"Extraction failed: Status {response.status_code}")
        return pd.DataFrame()

    catalog = []
    for item in response.json().get("items", []):
        catalog.append({
            "product_number": str(_get(item, "code", "upc") or ""),
            "product_name": _get(item, "title", "name"),
            "regular_price": _get(item, "pricing.regularPrice", "price"),
            "category": _get(item, "categoryName"),
            # Nutrition (% Daily Value per reference amount) -- optional; items
            # without it are routed to "Undecided Products" for manual review.
            "sat_fat_dv": _get(item, "nutrition.saturatedFatDV"),
            "sugars_dv": _get(item, "nutrition.sugarsDV"),
            "sodium_dv": _get(item, "nutrition.sodiumDV"),
            "reference_size": _get(item, "nutrition.referenceSize") or "general",
        })
    return pd.DataFrame(catalog)


def classify_fop(row):
    """Return (tab_name, note) for one product row."""
    nutrients = {
        "saturated fat": row.get("sat_fat_dv"),
        "sugars": row.get("sugars_dv"),
        "sodium": row.get("sodium_dv"),
    }
    known = {k: float(v) for k, v in nutrients.items() if v is not None and not pd.isna(v)}
    if not known:
        return TAB_UNDECIDED, "No nutrition data from source; verify label manually."

    threshold = FOP_THRESHOLD.get(row.get("reference_size"), FOP_THRESHOLD["general"])
    over = [k for k, v in known.items() if v >= threshold]
    if over:
        return TAB_WARNING, ""

    near = [f"{k} {v:g}% DV" for k, v in known.items() if v >= threshold - BORDERLINE_MARGIN]
    if near or len(known) < len(nutrients):
        missing = [k for k in nutrients if k not in known]
        parts = []
        if near:
            parts.append(f"Borderline vs {threshold}% DV cutoff: {', '.join(near)}.")
        if missing:
            parts.append(f"Missing data: {', '.join(missing)}.")
        return TAB_UNDECIDED, " ".join(parts)

    return TAB_NO_WARNING, ""


def _format_price(value):
    if value is None or value == "" or pd.isna(value):
        return ""
    return f"${float(value):.2f}"


def _price_value(text):
    match = re.search(r"[\d.]+", str(text))
    return float(match.group()) if match else float("nan")


def _merge_into_tab(worksheet, new_rows, header, key_col):
    """Merge new rows with what's already in the tab, de-dupe on key_col, sort, rewrite."""
    existing = worksheet.get_all_values()
    old_df = pd.DataFrame(existing[1:], columns=existing[0]) if existing else pd.DataFrame(columns=header)
    old_df = old_df.reindex(columns=header).fillna("")

    new_df = pd.DataFrame(new_rows, columns=header)
    merged = pd.concat([old_df, new_df], ignore_index=True)
    merged = merged[merged[key_col].astype(str).str.strip() != ""]
    merged = merged.drop_duplicates(subset=key_col, keep="last")  # fresh data wins

    sort_cols, ascending = [], []
    for col, asc in SORT_BY:
        if col in merged.columns:
            sort_cols.append(col)
            ascending.append(asc)
    if sort_cols:
        price_cols = [c for c in sort_cols if "Price" in c]
        merged = merged.sort_values(
            sort_cols, ascending=ascending,
            key=lambda s: s.map(_price_value) if s.name in price_cols else s.str.lower(),
        )

    worksheet.clear()
    worksheet.update([header] + merged.astype(str).values.tolist(), "A1",
                     value_input_option="RAW")  # RAW keeps leading zeros in UPCs
    return len(merged)


def compile_to_google_sheet(catalog_df, credentials_file, spreadsheet_id=SPREADSHEET_ID):
    """Classify catalog rows and merge them, sorted, into the grocery spreadsheet."""
    if catalog_df.empty:
        print("Nothing to write.")
        return

    buckets = {TAB_WARNING: [], TAB_NO_WARNING: [], TAB_UNDECIDED: []}
    for _, row in catalog_df.iterrows():
        tab, note = classify_fop(row)
        price = _format_price(row.get("regular_price"))
        if tab == TAB_UNDECIDED:
            buckets[tab].append([row["product_name"], price, note])
        else:
            buckets[tab].append([row["product_number"], row["product_name"], price])

    sheet = gspread.service_account(filename=credentials_file).open_by_key(spreadsheet_id)
    for tab, rows in buckets.items():
        if not rows:
            continue
        header = UNDECIDED_HEADER if tab == TAB_UNDECIDED else STD_HEADER
        key_col = "Product Name" if tab == TAB_UNDECIDED else "Product Number"
        total = _merge_into_tab(sheet.worksheet(tab), rows, header, key_col)
        print(f"{tab}: +{len(rows)} new/updated, {total} total rows")


if __name__ == "__main__":
    URL = "https://example-store.ca/api/products"  # replace with the store endpoint
    HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    catalog_df = extract_retail_catalog(URL, HEADERS)
    catalog_df.to_csv("grocery_inventory.csv", index=False)  # local backup
    compile_to_google_sheet(catalog_df, credentials_file="service_account.json")
