"""
pipeline/build_catalogue.py
KSL Field Sales Tool

Builds catalogue.json from the Zoho Inventory item list.
Runs weekly (Sunday) via GitHub Actions.

Output schema per item:
    item_id     str   Zoho item ID
    sku         str   SKU code
    name        str   Full item name
    category    str   Item group / category
    unit        str   Unit of measure (e.g. PCS, CTN)
    image_url   str   Zoho image URL if available, else empty string
    abc_class   str   A, B, or C (computed from sales velocity, see note)
    status      str   active or inactive
    rate        float Default selling price from Zoho (fallback when no pricelist covers the item)

ABC class is set to empty string here and populated by build_velocity.py
which runs after this script and has the sales data needed to classify.

Usage:
    python -m pipeline.build_catalogue
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.zoho_client import get_access_token, fetch_all_items

OUTPUT_PATH = ROOT / "data" / "app" / "catalogue.json"

# Zoho item image endpoint pattern
IMAGE_URL_PATTERN = (
    "https://www.zohoapis.com/inventory/v1/items/{item_id}/image"
    "?organization_id={org_id}"
)


def _extract_item(raw: dict, org_id: str) -> dict:
    item_id = raw.get("item_id", "")
    has_image = bool(raw.get("image_name") or raw.get("image_document_id"))
    rate = float(raw.get("rate") or raw.get("selling_price") or raw.get("purchase_rate") or 0)
    return {
        "item_id":   item_id,
        "sku":       raw.get("sku", "").strip(),
        "name":      raw.get("name", "").strip(),
        "category":  raw.get("item_type", ""),
        "unit":      raw.get("unit", ""),
        "image_url": IMAGE_URL_PATTERN.format(
                         item_id=item_id, org_id=org_id
                     ) if has_image else "",
        "abc_class": "",   # populated later by build_velocity.py
        "status":    raw.get("status", "active"),
        "rate":      rate,  # default selling price — used as last-resort fallback in pricing
    }


def run():
    import os
    print("[CATALOGUE] Starting catalogue build...")
    token  = get_access_token()
    org_id = os.environ.get("ZOHO_ORG_ID", "")

    raw_items = fetch_all_items(token, item_type="inventory")
    print(f"[CATALOGUE] {len(raw_items)} inventory items fetched from Zoho")

    items = []
    skipped = 0
    for raw in raw_items:
        # Drop anything that is not a physical inventory item in active status
        if raw.get("item_type", "") not in ("inventory", "inventoryitem"):
            skipped += 1
            continue
        if raw.get("status", "active").lower() != "active":
            skipped += 1
            continue
        sku = raw.get("sku", "").strip()
        if not sku:
            skipped += 1
            continue
        items.append(_extract_item(raw, org_id))

    # Sort alphabetically by SKU for stable diffs in git
    items.sort(key=lambda x: x["sku"])

    output = {
        "_meta": {
            "built_at":    datetime.now(timezone.utc).isoformat(),
            "total_items": len(items),
            "skipped":     skipped,
            "source":      "Zoho Inventory /items",
        },
        "items": items,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[CATALOGUE] {len(items)} active inventory items written to {OUTPUT_PATH}")
    if skipped:
        print(f"[CATALOGUE] {skipped} items skipped (inactive, non-inventory type, or no SKU)")


if __name__ == "__main__":
    run()