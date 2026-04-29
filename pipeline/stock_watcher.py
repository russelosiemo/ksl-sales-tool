"""
pipeline/stock_watcher.py
KSL Field Sales Tool

Smart stock refresh for stock.json.
Runs on a tiered schedule via GitHub Actions:
    - Business hours (08:00, 09:00, 10:00, 15:00, 16:00, 17:00 EAT)
    - Token-only runs at 07:00 and 14:00 (handled by token-only job)

Logic:
    1. Read the last_updated timestamp from existing stock.json.
    2. Query Zoho for transactions since that timestamp (5 API calls).
    3. If no activity: skip item fetch entirely, but still refresh
       committed stock from open SOs (2-3 calls).
    4. If activity: fetch only the affected items (N calls where N is
       the number of changed items, typically 10-100, not 750).
    5. Fetch open SOs created today to calculate committed quantities
       that have been sold but not yet dispatched (pre-booking awareness).
    6. Merge everything into stock.json.

Output schema per item in stock.json:
    sku         str    SKU code
    item_id     str    Zoho item ID
    wh1         float  Stock on hand in WH1
    wh2         float  Stock on hand in WH2
    combined    float  wh1 + wh2
    committed   float  Qty on open (confirmed) SOs today — pre-booked
    free_stock  float  combined - committed (safe to sell)
    last_updated str   ISO timestamp of last refresh for this item

API call budget per run:
    Token refresh:                           1 call    (always)
    Delta check (fetch_transactions_since):  5 calls   (always)
    Item batch fetch (only changed items):   N calls   (0 on quiet runs)
    Open SO list (committed stock):          1-2 calls (always)
    Typical total on a quiet run:            ~8 calls
    Typical total on a busy run:             ~60-80 calls (vs 750 before)

Usage:
    python -m pipeline.stock_watcher
    python -m pipeline.stock_watcher --full   # force full refresh
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.zoho_client import (
    get_access_token, fetch_transactions_since,
    fetch_items_batch, fetch_item_detail,
    fetch_sos_date_range, today_str,
)

OUTPUT_PATH    = ROOT / "data" / "app" / "stock.json"
CATALOGUE_PATH = ROOT / "data" / "app" / "catalogue.json"

# SOs in these statuses represent committed (pre-booked) stock.
# "draft" is excluded — not confirmed yet.
COMMITTED_STATUSES = {"confirmed", "packed", "shipped"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_existing() -> dict:
    if not OUTPUT_PATH.exists():
        return {"_meta": {}, "stock": {}}
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_catalogue_index() -> dict:
    """Returns {item_id: sku} mapping from catalogue.json."""
    if not CATALOGUE_PATH.exists():
        return {}
    with open(CATALOGUE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {item["item_id"]: item["sku"] for item in data.get("items", [])}


def _extract_stock(item: dict, wh1_name: str, wh2_name: str) -> dict:
    wh1 = 0.0
    wh2 = 0.0
    for wh in item.get("warehouses", []):
        name = wh.get("warehouse_name", "")
        qty  = float(wh.get("warehouse_stock_on_hand", 0) or 0)
        if name == wh1_name:
            wh1 = qty
        elif name == wh2_name:
            wh2 = qty
    return {
        "item_id":      item.get("item_id", ""),
        "wh1":          round(wh1),
        "wh2":          round(wh2),
        "combined":     round(wh1 + wh2),
        # committed and free_stock filled in by _apply_committed_stock()
        "committed":    0,
        "free_stock":   round(wh1 + wh2),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Committed stock from open SOs (pre-booking awareness)
# ---------------------------------------------------------------------------

def _fetch_committed_by_sku(token: str, catalogue_index: dict) -> dict:
    """
    Fetch all open SOs created today and build a {sku: committed_qty} map.

    Only counts SOs in COMMITTED_STATUSES (confirmed, packed, shipped).
    Draft SOs are excluded — not agreed yet.

    Returns {sku: total_committed_qty}.
    Makes 1-2 paginated API calls (one per page of today's SOs).
    """
    today = today_str()
    print(f"[STOCK] Fetching open SOs for committed stock (date: {today})...")

    try:
        sos = fetch_sos_date_range(token, date_from=today, date_to=today)
    except Exception as e:
        print(f"[STOCK] Could not fetch open SOs: {e} — committed stock set to 0")
        return {}

    committed: dict   = defaultdict(float)
    item_id_to_sku    = catalogue_index
    skipped_status    = 0

    for so in sos:
        status = (so.get("status") or so.get("order_status") or "").lower()
        if status not in COMMITTED_STATUSES:
            skipped_status += 1
            continue

        for li in so.get("line_items", []):
            item_id = li.get("item_id", "")
            sku     = li.get("sku", "").strip() or item_id_to_sku.get(item_id, "")
            qty     = float(li.get("quantity", 0) or 0)
            if sku and qty > 0:
                committed[sku] += qty

    total_sos     = len(sos)
    committed_sos = total_sos - skipped_status
    print(
        f"[STOCK] Open SOs today: {total_sos} total, "
        f"{committed_sos} committed, {skipped_status} draft/other skipped"
    )
    print(f"[STOCK] Committed stock: {len(committed)} SKU(s) have pre-booked qty")
    return dict(committed)


def _apply_committed_stock(stock_map: dict, committed_by_sku: dict) -> dict:
    """
    Merge committed quantities into stock_map entries.
    Recalculates free_stock = combined - committed (floor 0).
    Resets all entries first so cancelled SOs are cleared automatically.
    """
    # Reset all to zero first (clears any SOs that were cancelled since last run)
    for sku, entry in stock_map.items():
        entry["committed"]  = 0
        entry["free_stock"] = entry.get("combined", 0)

    # Apply today's committed quantities
    for sku, committed_qty in committed_by_sku.items():
        if sku in stock_map:
            combined  = stock_map[sku].get("combined", 0)
            committed = round(committed_qty)
            free      = max(0, combined - committed)
            stock_map[sku]["committed"]  = committed
            stock_map[sku]["free_stock"] = free

    return stock_map


# ---------------------------------------------------------------------------
# Full refresh
# ---------------------------------------------------------------------------

def _full_refresh(token: str, wh1_name: str, wh2_name: str,
                  catalogue_index: dict, existing: dict) -> dict:
    """Rebuild stock.json from scratch using all catalogue item IDs."""
    print("[STOCK] Running full refresh...")
    item_ids = list(catalogue_index.keys())
    print(f"[STOCK] Fetching stock for {len(item_ids)} items...")

    stock_map = existing.get("stock", {})
    fetched   = 0

    for i in range(0, len(item_ids), 100):
        chunk = item_ids[i:i + 100]
        items = fetch_items_batch(token, chunk)
        for item in items:
            sku = item.get("sku", "").strip()
            if not sku:
                sku = catalogue_index.get(item.get("item_id", ""), "")
            if sku:
                stock_map[sku] = _extract_stock(item, wh1_name, wh2_name)
                fetched += 1
        print(f"[STOCK] Progress: {min(i + 100, len(item_ids))}/{len(item_ids)}")

    print(f"[STOCK] Full refresh complete: {fetched} items updated")
    return stock_map


# ---------------------------------------------------------------------------
# Delta refresh
# ---------------------------------------------------------------------------

def _delta_refresh(token: str, wh1_name: str, wh2_name: str,
                   since_date: str, catalogue_index: dict,
                   existing: dict) -> tuple:
    """
    Check for transactions since since_date.
    Only fetches item details for items that actually moved.
    Returns (stock_map, updated_count, items_skipped).
    items_skipped=True means no transactions found, stock map unchanged.
    """
    print(f"[STOCK] Checking for activity since {since_date}...")
    activity = fetch_transactions_since(token, since_date)

    all_affected: set = set()
    for tx_type, item_ids in activity.items():
        if item_ids:
            print(f"[STOCK]   {tx_type}: {len(item_ids)} item(s) affected")
            all_affected.update(item_ids)

    stock_map = existing.get("stock", {})

    if not all_affected:
        print("[STOCK] No stock-moving transactions detected — skipping item fetch.")
        return stock_map, 0, True

    print(f"[STOCK] {len(all_affected)} unique item(s) to refresh")
    affected_list = list(all_affected)
    items         = fetch_items_batch(token, affected_list)

    # Fallback: individually fetch anything the batch missed
    returned_ids = {item.get("item_id") for item in items}
    missing_ids  = [i for i in affected_list if i not in returned_ids]
    if missing_ids:
        print(f"[STOCK] Fetching {len(missing_ids)} item(s) missed by batch...")
        for item_id in missing_ids:
            detail = fetch_item_detail(token, item_id)
            if detail:
                items.append(detail)

    updated = 0
    for item in items:
        sku = item.get("sku", "").strip()
        if not sku:
            sku = catalogue_index.get(item.get("item_id", ""), "")
        if sku:
            stock_map[sku] = _extract_stock(item, wh1_name, wh2_name)
            updated += 1

    print(f"[STOCK] Delta refresh complete: {updated} item(s) updated")
    return stock_map, updated, False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(force_full: bool = False):
    wh1_name = os.environ.get("ZOHO_MAIN_WH1", "")
    wh2_name = os.environ.get("ZOHO_MAIN_WH2", "")
    if not wh1_name or not wh2_name:
        print("[STOCK] ERROR: ZOHO_MAIN_WH1 and ZOHO_MAIN_WH2 must be set")
        sys.exit(1)

    token           = get_access_token()
    existing        = _load_existing()
    catalogue_index = _load_catalogue_index()
    last_updated    = existing.get("_meta", {}).get("last_updated", "")
    items_skipped   = False

    # ── Step 1: Refresh stock levels (full or delta) ──────────────────────
    if force_full or not last_updated:
        stock_map     = _full_refresh(token, wh1_name, wh2_name,
                                      catalogue_index, existing)
        updated_count = len(stock_map)
        mode          = "full"
    else:
        since_date = last_updated[:10]
        stock_map, updated_count, items_skipped = _delta_refresh(
            token, wh1_name, wh2_name,
            since_date, catalogue_index, existing
        )
        mode = "delta"

    # ── Step 2: Always refresh committed stock from open SOs ──────────────
    # Runs even when no stock transactions were detected, because a new SO
    # can be raised without touching physical stock yet (pre-booking).
    committed_by_sku = _fetch_committed_by_sku(token, catalogue_index)
    stock_map        = _apply_committed_stock(stock_map, committed_by_sku)

    # ── Step 3: Write output ──────────────────────────────────────────────
    output = {
        "_meta": {
            "last_updated":   datetime.now(timezone.utc).isoformat(),
            "wh1":            wh1_name,
            "wh2":            wh2_name,
            "total_skus":     len(stock_map),
            "mode":           mode,
            "items_updated":  updated_count,
            "items_skipped":  items_skipped,
            "committed_skus": len(committed_by_sku),
        },
        "stock": stock_map,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[STOCK] Written to {OUTPUT_PATH}")
    print(
        f"[STOCK] Summary — mode: {mode}, "
        f"items updated: {updated_count}, "
        f"committed SKUs: {len(committed_by_sku)}, "
        f"item fetch skipped: {items_skipped}"
    )


if __name__ == "__main__":
    force = "--full" in sys.argv
    run(force_full=force)