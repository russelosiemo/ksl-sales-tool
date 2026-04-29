"""
pipeline/build_pricelists.py
KSL Sales Tool

Fetches all Zoho Inventory price lists and each customer's assigned
price list, then writes data/app/pricelists.json.

Handles two Zoho pricebook types:
  - per_item         : explicit rate per item_id in pricebook_items[]
  - fixed_percentage : blanket % mark-up or mark-down on the item's base rate
                       (final rate = base_rate +/- base_rate * percentage / 100)

The list endpoint (GET /pricebooks) already includes pricebook_items, so no
per-pricebook detail fetch is needed.

Schema written:
{
  "built_at": "2026-04-21T04:00:00Z",
  "priceLists": {
    "<pricelist_id>": {
      "name": "Retail KES",
      "prices": {
        "<item_id>": 250.0,
        ...
      }
    }
  },
  "customerPL": {
    "<customer_id>": "<pricelist_id>",
    ...
  }
}

Run:
    python -m pipeline.build_pricelists

Env vars required (same as other pipeline modules):
    ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ORG_ID
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OUTPUT_PATH = Path("data/app/pricelists.json")
INV_BASE = "https://www.zohoapis.com/inventory/v1"


# -- Auth ---------------------------------------------------------------------

def _get_token() -> tuple[str, str]:
    """Return (access_token, org_id) refreshed from env vars."""
    r = requests.post(
        "https://accounts.zoho.com/oauth/v2/token",
        params={
            "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
            "client_id":     os.environ["ZOHO_CLIENT_ID"],
            "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
            "grant_type":    "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"], os.environ["ZOHO_ORG_ID"]


# -- Zoho helpers -------------------------------------------------------------

def _get(token: str, org_id: str, path: str, params: dict = None) -> dict:
    url = f"{INV_BASE}{path}"
    p = {"organization_id": org_id, **(params or {})}
    r = requests.get(url, headers={"Authorization": f"Zoho-oauthtoken {token}"},
                     params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def _paginate(token: str, org_id: str, path: str, key: str,
              extra: dict = None) -> list:
    """Fetch all pages of a paginated Zoho endpoint."""
    results, page = [], 1
    while True:
        data = _get(token, org_id, path, {"per_page": 200, "page": page, **(extra or {})})
        batch = data.get(key, [])
        results.extend(batch)
        if not data.get("page_context", {}).get("has_more_page"):
            break
        page += 1
        time.sleep(0.3)
    return results


# -- Fetch base item rates -----------------------------------------------------

def fetch_base_rates(token: str, org_id: str) -> dict:
    """
    Returns {item_id: base_rate} for all active inventory items.
    Used to compute final rates for fixed_percentage pricebooks.
    """
    items = _paginate(token, org_id, "/items", "items",
                      extra={"item_type": "inventory", "filter_by": "Status.Active"})
    rates = {}
    for item in items:
        item_id = str(item.get("item_id", ""))
        rate = float(item.get("rate") or item.get("selling_price") or 0)
        if item_id:
            rates[item_id] = rate
    print(f"  Fetched base rates for {len(rates)} items")
    return rates


# -- Fetch price lists ---------------------------------------------------------

def fetch_price_lists(token: str, org_id: str, base_rates: dict) -> dict:
    """
    Returns {pl_id: {name, prices: {item_id: rate}}}

    Despite the API spec, the GET /pricebooks list response does NOT include
    pricebook_items in practice. For per_item lists we fetch each pricebook's
    detail individually via GET /pricebooks/{id} and read pricebook_rate.

    For fixed_percentage lists, final rate is computed as:
        is_increase=True  -> base_rate * (1 + percentage / 100)
        is_increase=False -> base_rate * (1 - percentage / 100)
    """
    pricebooks = _paginate(token, org_id, "/pricebooks", "pricebooks")
    print(f"  Found {len(pricebooks)} price lists")

    result = {}
    for pb in pricebooks:
        pl_id   = str(pb["pricebook_id"])
        pl_name = pb.get("name", "")
        pb_type = pb.get("pricebook_type", "")  # "per_item" | "fixed_percentage"
        is_inc  = pb.get("is_increase", True)    # True = mark-up, False = mark-down
        pct     = float(pb.get("percentage") or 0)
        prices  = {}

        if pb_type == "per_item":
            # List response omits line items — fetch detail for each pricebook
            try:
                detail = _get(token, org_id, f"/pricebooks/{pl_id}")
                pb_detail = detail.get("pricebook", {})
                for li in pb_detail.get("pricebook_items", []):
                    item_id = str(li.get("item_id", ""))
                    rate    = float(li.get("pricebook_rate") or 0)
                    if item_id:
                        prices[item_id] = rate
                time.sleep(0.2)
            except Exception as e:
                print(f"    WARNING: could not fetch detail for {pl_name}: {e}")

        elif pb_type == "fixed_percentage" and pct > 0:
            # Apply blanket percentage to every item's base rate
            multiplier = 1 + (pct / 100) if is_inc else 1 - (pct / 100)
            for item_id, base_rate in base_rates.items():
                final = round(base_rate * multiplier, 2)
                if final > 0:
                    prices[item_id] = final

        result[pl_id] = {"name": pl_name, "prices": prices}

        direction  = "markup" if is_inc else "markdown"
        type_label = ("per_item" if pb_type == "per_item"
                      else f"fixed_percentage ({pct}% {direction})")
        print(f"    {pl_name}: {len(prices)} items [{type_label}]")

    return result


# -- Fetch customer -> price list mapping -------------------------------------

def fetch_customer_pricelist_map(token: str, org_id: str) -> dict:
    """
    Returns {customer_id: pricelist_id}

    The contacts list endpoint omits pricebook_id on most records.
    We fetch each customer's detail individually to get the assigned pricelist.
    Customers with no pricelist assigned are omitted from the map.
    """
    customers = _paginate(token, org_id, "/contacts", "contacts",
                          extra={"contact_type": "customer"})
    print(f"  Found {len(customers)} customers — fetching detail for pricelist assignment...")

    mapping = {}
    for i, c in enumerate(customers):
        cid = str(c.get("contact_id", ""))
        if not cid:
            continue

        # Check list-level field first (sometimes populated)
        pl_id = str(c.get("pricebook_id") or c.get("price_list_id") or "").strip()

        if not pl_id:
            # Fetch contact detail to reliably get pricebook_id
            try:
                detail = _get(token, org_id, f"/contacts/{cid}")
                contact = detail.get("contact", {})
                pl_id = str(contact.get("pricebook_id") or contact.get("price_list_id") or "").strip()
                time.sleep(0.15)  # stay within rate limits
            except Exception as e:
                print(f"    WARNING: could not fetch detail for contact {cid}: {e}")
                continue

        if pl_id and pl_id != "0":
            mapping[cid] = pl_id

        if (i + 1) % 50 == 0:
            print(f"    ...processed {i + 1}/{len(customers)} customers")

    print(f"  {len(mapping)} customers have a price list assigned")
    return mapping


# -- Main ---------------------------------------------------------------------

def main():
    print("build_pricelists: starting")
    token, org_id = _get_token()

    print("Fetching item base rates (needed for percentage-based price lists)...")
    base_rates = fetch_base_rates(token, org_id)

    print("Fetching price lists...")
    price_lists = fetch_price_lists(token, org_id, base_rates)

    print("Fetching customer price list assignments...")
    customer_pl = fetch_customer_pricelist_map(token, org_id)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "priceLists": price_lists,
        "customerPL": customer_pl,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    total_prices = sum(len(pl["prices"]) for pl in price_lists.values())
    print(f"build_pricelists: done — {len(price_lists)} lists, "
          f"{total_prices} price entries, {len(customer_pl)} customer mappings")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()