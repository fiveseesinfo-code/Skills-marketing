"""
Harvest grocery catalogs and restaurant menus from locations in each Canadian
provincial and territorial capital, classify grocery items against Canada's
front-of-package (FOP) "High in" nutrition symbol rules, then merge,
de-duplicate and sort the results into the Google Sheet:

    "Canadian Grocery & Restaurant Food Log - Health Warning Analysis"

Tabs written (the original three columns stay first; location columns follow):
    Health Warning Foods       -> Product Number | Product Name | Regular Retail Price (CAD) | City | Province | Store
    No Health Warning Foods    -> Product Number | Product Name | Regular Retail Price (CAD) | City | Province | Store
    Undecided Products         -> Product Name | Retail Price (CAD) | Ambiguity / Notes | City | Province | Store
    Canadian Restaurant Menus  -> Item ID | Product Name | Regular Retail Price (CAD) | City | Province | Store
Restaurant items skip FOP classification: the symbol rules cover prepackaged
foods, not restaurant meals.

Setup:
    pip install requests pandas gspread
    Create a Google Cloud service account, download its JSON key, and share the
    spreadsheet with the service account's email (Editor).
    Replace every REPLACE-ME endpoint in STORE_SOURCES; sources still holding a
    placeholder are skipped.
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
TAB_RESTAURANTS = "Canadian Restaurant Menus"

LOCATION_COLS = ["City", "Province", "Store"]
STD_HEADER = ["Product Number", "Product Name", "Regular Retail Price (CAD)"] + LOCATION_COLS
UNDECIDED_HEADER = ["Product Name", "Retail Price (CAD)", "Ambiguity / Notes"] + LOCATION_COLS
RESTAURANT_HEADER = ["Item ID", "Product Name", "Regular Retail Price (CAD)"] + LOCATION_COLS

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

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Where each field lives in an API item, as dotted paths tried in order.
# A source can override any of these with its own "fields" entry.
DEFAULT_FIELDS = {
    "product_number": ["code", "upc", "id", "itemId"],
    "product_name": ["title", "name", "displayName"],
    "regular_price": ["pricing.regularPrice", "price", "prices.price"],
    "category": ["categoryName", "category"],
    "sat_fat_dv": ["nutrition.saturatedFatDV"],
    "sugars_dv": ["nutrition.sugarsDV"],
    "sodium_dv": ["nutrition.sodiumDV"],
    "reference_size": ["nutrition.referenceSize"],
}
DEFAULT_LIST_KEYS = ["items", "products", "menuItems", "results"]
DEFAULT_STORE_LIST_KEYS = ["stores", "locations", "restaurants", "results"]


def _source(chain, kind, locator_url, catalog_url, store_id_param="storeId",
            id_prefix="", **extra):
    """Build one STORE_SOURCES entry. kind is 'grocery' or 'restaurant'."""
    return {"chain": chain, "kind": kind, "locator_url": locator_url,
            "catalog_url": catalog_url, "store_id_param": store_id_param,
            "id_prefix": id_prefix, "headers": dict(DEFAULT_HEADERS), **extra}


# Each chain's store locator is queried near every capital; capitals where the
# chain has no location within SEARCH_RADIUS_KM are skipped for that chain
# (e.g. Atlantic Superstore only appears in the Atlantic capitals).
# Banners owned by one company usually share a platform, so their entries
# differ only by a banner value; fill these in from the chain's site/app.
STORE_SOURCES = [
    # --- Loblaw Companies banners ---
    _source("Loblaws", "grocery",
            "https://REPLACE-ME/loblaw/stores", "https://REPLACE-ME/loblaw/products"),
    _source("Real Canadian Superstore", "grocery",
            "https://REPLACE-ME/superstore/stores", "https://REPLACE-ME/superstore/products"),
    _source("No Frills", "grocery",
            "https://REPLACE-ME/nofrills/stores", "https://REPLACE-ME/nofrills/products"),
    _source("Your Independent Grocer", "grocery",
            "https://REPLACE-ME/independent/stores", "https://REPLACE-ME/independent/products"),
    _source("Atlantic Superstore", "grocery",
            "https://REPLACE-ME/atlanticsuperstore/stores", "https://REPLACE-ME/atlanticsuperstore/products"),
    # --- Sobeys Inc. banners ---
    _source("Sobeys", "grocery",
            "https://REPLACE-ME/sobeys/stores", "https://REPLACE-ME/sobeys/products"),
    _source("IGA", "grocery",
            "https://REPLACE-ME/iga/stores", "https://REPLACE-ME/iga/products"),
    # --- Restaurants (id_prefix matches the existing Item ID style, e.g. MCD-101) ---
    _source("McDonald's", "restaurant",
            "https://REPLACE-ME/mcdonalds/restaurants", "https://REPLACE-ME/mcdonalds/menu",
            id_prefix="MCD"),
    _source("Burger King", "restaurant",
            "https://REPLACE-ME/burgerking/restaurants", "https://REPLACE-ME/burgerking/menu",
            id_prefix="BK"),
    _source("KFC", "restaurant",
            "https://REPLACE-ME/kfc/restaurants", "https://REPLACE-ME/kfc/menu",
            id_prefix="KFC"),
    _source("Tim Hortons", "restaurant",
            "https://REPLACE-ME/timhortons/restaurants", "https://REPLACE-ME/timhortons/menu",
            id_prefix="TH"),
    _source("Wendy's", "restaurant",
            "https://REPLACE-ME/wendys/restaurants", "https://REPLACE-ME/wendys/menu",
            id_prefix="WEN"),
    _source("Pizza Hut", "restaurant",
            "https://REPLACE-ME/pizzahut/restaurants", "https://REPLACE-ME/pizzahut/menu",
            id_prefix="PH"),
]
SEARCH_RADIUS_KM = 25
STORES_PER_CITY = 1          # nearest N locations per chain per capital
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


def _first_list(payload, keys):
    if isinstance(payload, list):
        return payload
    for key in keys:
        value = _get(payload, key)
        if isinstance(value, list):
            return value
    return []


def _is_configured(source):
    return "REPLACE-ME" not in source["locator_url"] + source["catalog_url"]


def find_stores_near(source, capital, session):
    """Return up to STORES_PER_CITY locations of one chain near a capital, nearest first."""
    params = {"lat": capital["lat"], "lon": capital["lon"], "radius": SEARCH_RADIUS_KM,
              **source.get("locator_params", {})}
    response = session.get(source["locator_url"], headers=source.get("headers"),
                           params=params, timeout=30)
    if response.status_code != 200:
        print(f"  {source['chain']} locator failed in {capital['city']}: Status {response.status_code}")
        return []

    stores = []
    for store in _first_list(response.json(), source.get("store_list_keys", DEFAULT_STORE_LIST_KEYS)):
        distance = _get(store, "distance", "distanceKm")
        if distance is not None and float(distance) > SEARCH_RADIUS_KM:
            continue
        stores.append({
            "id": _get(store, "id", "storeId", "storeNumber"),
            "name": _get(store, "name", "storeName") or source["chain"],
            "distance": float(distance) if distance is not None else 0.0,
        })
    stores.sort(key=lambda s: s["distance"])
    return [s for s in stores if s["id"] is not None][:STORES_PER_CITY]


def extract_retail_catalog(api_url, headers, params=None, session=None, fields=None,
                           list_keys=DEFAULT_LIST_KEYS):
    """
    Template for harvesting product catalogs from public e-commerce endpoints.
    Pass `fields` to override where any value lives in the store's API response.
    """
    response = (session or requests).get(api_url, headers=headers, params=params, timeout=30)
    if response.status_code != 200:
        print(f"Extraction failed: Status {response.status_code}")
        return pd.DataFrame()

    paths = {**DEFAULT_FIELDS, **(fields or {})}
    catalog = []
    for item in _first_list(response.json(), list_keys):
        row = {name: _get(item, *field_paths) for name, field_paths in paths.items()}
        row["product_number"] = str(row["product_number"] or "")
        # Nutrition (% Daily Value per reference amount) is optional; grocery items
        # without it are routed to "Undecided Products" for manual review.
        row["reference_size"] = row["reference_size"] or "general"
        catalog.append(row)
    return pd.DataFrame(catalog)


def harvest_capitals(sources=STORE_SOURCES, capitals=CAPITALS):
    """Pull every configured chain's catalog/menu from its nearest location(s) in each capital."""
    active = [s for s in sources if _is_configured(s)]
    for source in sources:
        if source not in active:
            print(f"Skipping {source['chain']}: endpoints not configured")

    frames = []
    with requests.Session() as session:
        for capital in capitals:
            print(f"{capital['city']}, {capital['province']}")
            for source in active:
                stores = find_stores_near(source, capital, session)
                if not stores:
                    print(f"  {source['chain']}: no location within {SEARCH_RADIUS_KM} km")
                time.sleep(REQUEST_DELAY_SECONDS)
                for store in stores:
                    df = extract_retail_catalog(
                        source["catalog_url"], source.get("headers"),
                        params={source["store_id_param"]: store["id"], **source.get("catalog_params", {})},
                        session=session, fields=source.get("fields"),
                        list_keys=source.get("list_keys", DEFAULT_LIST_KEYS),
                    )
                    time.sleep(REQUEST_DELAY_SECONDS)
                    if df.empty:
                        continue
                    df["kind"] = source["kind"]
                    df["chain"] = source["chain"]
                    df["id_prefix"] = source["id_prefix"]
                    df["city"] = capital["city"]
                    df["province"] = capital["province"]
                    df["store"] = store["name"]
                    frames.append(df)
                    print(f"  {store['name']}: {len(df)} items")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def classify_fop(row):
    """Return (tab_name, note) for one grocery product row."""
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

    buckets = {TAB_WARNING: [], TAB_NO_WARNING: [], TAB_UNDECIDED: [], TAB_RESTAURANTS: []}
    for _, row in catalog_df.iterrows():
        price = _format_price(row.get("regular_price"))
        location = [row.get("city", ""), row.get("province", ""), row.get("store", "")]
        if row.get("kind") == "restaurant":
            item_id = row["product_number"]
            if row.get("id_prefix") and item_id:
                item_id = f"{row['id_prefix']}-{item_id}"
            buckets[TAB_RESTAURANTS].append([item_id, row["product_name"], price] + location)
            continue
        tab, note = classify_fop(row)
        if tab == TAB_UNDECIDED:
            buckets[tab].append([row["product_name"], price, note] + location)
        else:
            buckets[tab].append([row["product_number"], row["product_name"], price] + location)

    layouts = {
        TAB_WARNING: (STD_HEADER, ["Product Number"]),
        TAB_NO_WARNING: (STD_HEADER, ["Product Number"]),
        TAB_UNDECIDED: (UNDECIDED_HEADER, ["Product Name"]),
        TAB_RESTAURANTS: (RESTAURANT_HEADER, ["Item ID"]),
    }
    sheet = gspread.service_account(filename=credentials_file).open_by_key(spreadsheet_id)
    for tab, rows in buckets.items():
        if not rows:
            continue
        header, key = layouts[tab]
        # Same item can appear once per location, so location is part of the key.
        total = _merge_into_tab(sheet.worksheet(tab), rows, header, key + LOCATION_COLS)
        print(f"{tab}: +{len(rows)} new/updated, {total} total rows")


if __name__ == "__main__":
    catalog_df = harvest_capitals()
    catalog_df.to_csv("grocery_inventory.csv", index=False)  # local backup
    compile_to_google_sheet(catalog_df, credentials_file="service_account.json")
