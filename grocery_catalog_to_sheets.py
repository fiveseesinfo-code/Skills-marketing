"""
Harvest retail product catalogs from stores in each Canadian provincial and
territorial capital, classify each item against Canada's front-of-package (FOP)
"High in" nutrition symbol rules, then merge, de-duplicate and sort the results
into the Google Sheet:

    "Canadian Grocery & Restaurant Food Log - Health Warning Analysis"

Tabs written (the original three columns stay first; location columns follow):
    Health Warning Foods      -> Product Number | Product Name | Regular Retail Price (CAD) | City | Province | Store
    No Health Warning Foods   -> Product Number | Product Name | Regular Retail Price (CAD) | City | Province | Store
    Undecided Products        -> Product Name | Retail Price (CAD) | Ambiguity / Notes | City | Province | Store
("Canadian Restaurant Menus" is left untouched.)

Setup:
    pip install requests pandas gspread
    Create a Google Cloud service account, download its JSON key, and share the
    spreadsheet with the service account's email (Editor).
    Fill in STORE_SOURCES with each chain's store-locator and catalog endpoints.
"""
import re
import time

import gspread
import pandas as pd
import requests

SPREADSHEET_ID = "1FmHOUhewVQRERrW5fiZg9zVkYmbZWregBcCQGGVml2c"

TAB_WARNING = "Health Warning Foods"
TAB_NO_WARNING = "No Health Warning Foods"
TAB_UNDECIDED = "Undecided Products"

LOCATION_COLS = ["City", "Province", "Store"]
STD_HEADER = ["Product Number", "Product Name", "Regular Retail Price (CAD)"] + LOCATION_COLS
UNDECIDED_HEADER = ["Product Name", "Retail Price (CAD)", "Ambiguity / Notes"] + LOCATION_COLS

# Provincial and territorial capitals, west to east then the territories.
CAPITALS = [
    {"province": "BC", "city": "Victoria",       "lat": 48.4284, "lon": -123.3656},
    {"province": "AB", "city": "Edmonton",       "lat": 53.5461, "lon": -113.4938},
    {"province": "SK", "city": "Regina",         "lat": 50.4452, "lon": -104.6189},
    {"province": "MB", "city": "Winnipeg",       "lat": 49.8951, "lon": -97.1384},
    {"province": "ON", "city": "Toronto",        "lat": 43.6532, "lon": -79.3832},
    {"province": "QC", "city": "Quebec City",    "lat": 46.8139, "lon": -71.2080},
    {"province": "NB", "city": "Fredericton",    "lat": 45.9636, "lon": -66.6431},
    {"province": "NS", "city": "Halifax",        "lat": 44.6488, "lon": -63.5752},
    {"province": "PE", "city": "Charlottetown",  "lat": 46.2382, "lon": -63.1311},
    {"province": "NL", "city": "St. John's",     "lat": 47.5615, "lon": -52.7126},
    {"province": "YT", "city": "Whitehorse",     "lat": 60.7212, "lon": -135.0568},
    {"province": "NT", "city": "Yellowknife",    "lat": 62.4540, "lon": -114.3718},
    {"province": "NU", "city": "Iqaluit",        "lat": 63.7467, "lon": -68.5170},
]
PROVINCE_ORDER = {c["province"]: i for i, c in enumerate(CAPITALS)}

# One entry per chain/banner. Each chain's store locator is queried near every
# capital; capitals where the chain has no store within SEARCH_RADIUS_KM are
# skipped for that chain. Replace the placeholder URLs and adjust the key names
# to match each chain's API responses.
STORE_SOURCES = [
    {
        "chain": "Example Grocer",
        "locator_url": "https://example-store.ca/api/stores",      # GET ?lat=&lon=&radius=
        "catalog_url": "https://example-store.ca/api/products",    # GET ?storeId=
        "store_id_param": "storeId",
        "headers": {"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    },
]
SEARCH_RADIUS_KM = 25
STORES_PER_CITY = 1          # nearest N stores per chain per capital
REQUEST_DELAY_SECONDS = 1.0  # pause between requests to stay polite

# FOP threshold (% Daily Value per reference amount) for sat fat, sugars, sodium.
# 15% general, 10% for small reference amounts (<= 30 g/ml), 30% for main dishes.
FOP_THRESHOLD = {"general": 15, "small": 10, "main_dish": 30}
# Items within this many %DV points of the threshold go to "Undecided Products".
BORDERLINE_MARGIN = 2

# Sort order applied to every tab after merging: (column, ascending).
# Product first, then capitals west to east, so one product's prices sit together.
SORT_BY = [("Product Name", True), ("Province", True), ("Store", True)]


def _get(item, *paths):
    """Return the first non-empty value from dotted key paths, e.g. 'pricing.regularPrice'."""
    for path in paths:
        value = item
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value not in (None, ""):
            return value
    return None


def find_stores_near(source, capital, session):
    """Return up to STORES_PER_CITY stores of one chain near a capital, nearest first."""
    params = {"lat": capital["lat"], "lon": capital["lon"], "radius": SEARCH_RADIUS_KM}
    response = session.get(source["locator_url"], headers=source.get("headers"),
                           params=params, timeout=30)
    if response.status_code != 200:
        print(f"  {source['chain']} locator failed in {capital['city']}: Status {response.status_code}")
        return []

    stores = []
    for store in response.json().get("stores", []):
        distance = _get(store, "distance", "distanceKm")
        if distance is not None and float(distance) > SEARCH_RADIUS_KM:
            continue
        stores.append({
            "id": _get(store, "id", "storeId"),
            "name": _get(store, "name", "storeName") or source["chain"],
            "distance": float(distance) if distance is not None else 0.0,
        })
    stores.sort(key=lambda s: s["distance"])
    return [s for s in stores if s["id"] is not None][:STORES_PER_CITY]


def extract_retail_catalog(api_url, headers, params=None, session=None):
    """
    Template for harvesting product catalogs from public e-commerce endpoints.
    Adjust key names in _get() calls to match the store's API response structure.
    """
    response = (session or requests).get(api_url, headers=headers, params=params, timeout=30)
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


def harvest_capitals(sources=STORE_SOURCES, capitals=CAPITALS):
    """Pull every chain's catalog from its nearest store(s) in each capital."""
    frames = []
    with requests.Session() as session:
        for capital in capitals:
            print(f"{capital['city']}, {capital['province']}")
            for source in sources:
                stores = find_stores_near(source, capital, session)
                if not stores:
                    print(f"  {source['chain']}: no store within {SEARCH_RADIUS_KM} km")
                time.sleep(REQUEST_DELAY_SECONDS)
                for store in stores:
                    df = extract_retail_catalog(
                        source["catalog_url"], source.get("headers"),
                        params={source["store_id_param"]: store["id"]}, session=session,
                    )
                    time.sleep(REQUEST_DELAY_SECONDS)
                    if df.empty:
                        continue
                    df["city"] = capital["city"]
                    df["province"] = capital["province"]
                    df["store"] = store["name"]
                    frames.append(df)
                    print(f"  {store['name']}: {len(df)} products")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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


def _sort_key(series):
    if "Price" in series.name:
        return series.map(_price_value)
    if series.name == "Province":
        return series.map(lambda p: PROVINCE_ORDER.get(p, len(PROVINCE_ORDER)))
    return series.str.lower()


def _merge_into_tab(worksheet, new_rows, header, key_cols):
    """Merge new rows with what's already in the tab, de-dupe on key_cols, sort, rewrite."""
    existing = worksheet.get_all_values()
    old_df = pd.DataFrame(existing[1:], columns=existing[0]) if existing else pd.DataFrame(columns=header)
    old_df = old_df.reindex(columns=header).fillna("")

    new_df = pd.DataFrame(new_rows, columns=header)
    merged = pd.concat([old_df, new_df], ignore_index=True)
    merged = merged[merged[key_cols[0]].astype(str).str.strip() != ""]
    merged = merged.drop_duplicates(subset=key_cols, keep="last")  # fresh data wins

    sort_cols = [c for c, _ in SORT_BY if c in merged.columns]
    ascending = [a for c, a in SORT_BY if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols, ascending=ascending, key=_sort_key)

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
        location = [row.get("city", ""), row.get("province", ""), row.get("store", "")]
        if tab == TAB_UNDECIDED:
            buckets[tab].append([row["product_name"], price, note] + location)
        else:
            buckets[tab].append([row["product_number"], row["product_name"], price] + location)

    sheet = gspread.service_account(filename=credentials_file).open_by_key(spreadsheet_id)
    for tab, rows in buckets.items():
        if not rows:
            continue
        header = UNDECIDED_HEADER if tab == TAB_UNDECIDED else STD_HEADER
        # Same product can appear once per store, so location is part of the key.
        key_cols = (["Product Name"] if tab == TAB_UNDECIDED else ["Product Number"]) + LOCATION_COLS
        total = _merge_into_tab(sheet.worksheet(tab), rows, header, key_cols)
        print(f"{tab}: +{len(rows)} new/updated, {total} total rows")


if __name__ == "__main__":
    catalog_df = harvest_capitals()
    catalog_df.to_csv("grocery_inventory.csv", index=False)  # local backup
    compile_to_google_sheet(catalog_df, credentials_file="service_account.json")
