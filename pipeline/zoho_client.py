"""
pipeline/zoho_client.py
KSL Field Sales Tool

Single point of contact for all Zoho Inventory and Zoho Mail API calls
made by the pipeline layer. All pipeline scripts import from here only.
Never call requests directly from pipeline scripts.

Environment variables required:
    ZOHO_CLIENT_ID
    ZOHO_CLIENT_SECRET
    ZOHO_REFRESH_TOKEN
    ZOHO_ORG_ID
"""
import os
import time
import requests
from datetime import datetime, date
from pathlib import Path

# Auto-load .env from repo root if present.
# This means every pipeline script works locally without any extra setup step.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
        print(f"[CLIENT] Loaded .env from {_env_path}")
except ImportError:
    pass  # python-dotenv not installed — env vars must be set externally (GitHub Actions)

ZOHO_TOKEN_URL   = "https://accounts.zoho.com/oauth/v2/token"
INVENTORY_BASE   = "https://www.zohoapis.com/inventory/v1"
MAIL_BASE        = "https://mail.zoho.com/api"

_RETRY_TIMEOUTS  = (60, 90, 120)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    resp = requests.post(ZOHO_TOKEN_URL, data={
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
        "client_id":     os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "grant_type":    "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    print("[AUTH] Access token refreshed.")
    return data["access_token"]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type":  "application/json",
    }


def _params(extra: dict = None) -> dict:
    p = {"organization_id": os.environ["ZOHO_ORG_ID"]}
    if extra:
        p.update(extra)
    return p


# ---------------------------------------------------------------------------
# Resilient GET with retry
# ---------------------------------------------------------------------------

def _get(token: str, url: str, params: dict = None) -> dict:
    """GET with progressive timeout retry. Returns parsed JSON dict."""
    full_params = _params(params)
    last_err = None
    for attempt, timeout in enumerate(_RETRY_TIMEOUTS, 1):
        try:
            resp = requests.get(url, headers=_headers(token),
                                params=full_params, timeout=timeout)
            if resp.ok:
                return resp.json()
            print(f"[CLIENT] GET {url} returned {resp.status_code} on attempt {attempt}")
            last_err = f"HTTP {resp.status_code}"
        except requests.exceptions.Timeout as e:
            print(f"[CLIENT] GET {url} timed out on attempt {attempt} ({timeout}s)")
            last_err = str(e)
        except Exception as e:
            print(f"[CLIENT] GET {url} failed on attempt {attempt}: {e}")
            last_err = str(e)
            break
    raise RuntimeError(f"GET {url} failed after {len(_RETRY_TIMEOUTS)} attempts: {last_err}")


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _paginate(token: str, url: str, key: str, extra_params: dict = None) -> list:
    """Fetch all pages from a paginated Zoho endpoint."""
    results = []
    page    = 1
    while True:
        params = {"per_page": 200, "page": page}
        if extra_params:
            params.update(extra_params)
        data  = _get(token, url, params)
        batch = data.get(key, [])
        if not batch:
            break
        results.extend(batch)
        if not data.get("page_context", {}).get("has_more_page"):
            break
        page += 1
    return results


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def fetch_all_items(token: str, item_type: str = "inventory") -> list:
    """
    Fetch all active inventory items.
    Filters by item_type=inventory and status=active at the API level.
    Returns list of item dicts from the /items list endpoint.
    Note: list endpoint does not include warehouse breakdown.
    """
    return _paginate(token, f"{INVENTORY_BASE}/items", "items",
                     {"item_type": item_type, "filter_by": "Status.Active"})


def fetch_item_detail(token: str, item_id: str) -> dict:
    """Fetch a single item with full warehouse breakdown."""
    data = _get(token, f"{INVENTORY_BASE}/items/{item_id}")
    return data.get("item", {})


def fetch_items_batch(token: str, item_ids: list) -> list:
    """
    Fetch warehouse stock for a list of item IDs.

    The /items list endpoint does NOT return warehouse breakdown regardless
    of any filter applied — warehouse detail only exists on the individual
    /items/{item_id} endpoint. This function fetches all items concurrently
    using a thread pool to keep total time reasonable.

    Uses 8 concurrent threads by default. Each thread makes one GET request.
    For 750 items this typically completes in 60-90 seconds vs 10+ minutes
    sequentially.
    """
    if not item_ids:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results    = []
    errors     = 0
    completed  = 0
    total      = len(item_ids)
    WORKERS    = 8

    def _fetch_one(item_id):
        try:
            return fetch_item_detail(token, item_id)
        except Exception as e:
            print(f"[CLIENT] fetch_items_batch: item {item_id} failed: {e}")
            return {}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_one, iid): iid for iid in item_ids}
        for future in as_completed(futures):
            completed += 1
            item = future.result()
            if item:
                results.append(item)
            else:
                errors += 1
            if completed % 100 == 0 or completed == total:
                print(f"[CLIENT] Stock fetch: {completed}/{total} "
                      f"({'errors: ' + str(errors) if errors else 'ok'})")

    return results


# ---------------------------------------------------------------------------
# Customers / Contacts
# ---------------------------------------------------------------------------

def fetch_all_customers(token: str) -> list:
    """Fetch all active customer contacts."""
    return _paginate(token, f"{INVENTORY_BASE}/contacts", "contacts",
                     {"contact_type": "customer", "status": "active"})


def fetch_customer_invoices(token: str, customer_id: str,
                             limit: int = 50) -> list:
    """Fetch recent invoices for a single customer, newest first."""
    data = _get(token, f"{INVENTORY_BASE}/invoices", {
        "customer_id": customer_id,
        "per_page":    limit,
        "sort_column": "date",
        "sort_order":  "D",
    })
    return data.get("invoices", [])


def fetch_customer_sos(token: str, customer_id: str, limit: int = 50) -> list:
    """Fetch recent sales orders for a single customer, newest first."""
    data = _get(token, f"{INVENTORY_BASE}/salesorders", {
        "customer_id": customer_id,
        "per_page":    limit,
        "sort_column": "date",
        "sort_order":  "D",
    })
    return data.get("salesorders", [])


# ---------------------------------------------------------------------------
# Transaction activity check (for stock_watcher delta logic)
# ---------------------------------------------------------------------------

def fetch_transactions_since(token: str, since_date: str) -> dict:
    """
    Check for any stock-moving transactions since since_date (YYYY-MM-DD).
    Returns {endpoint_name: [affected_item_ids]} for each transaction type.
    Makes exactly 5 API calls (one per transaction type).
    """
    affected = {
        "invoices":    set(),
        "bills":       set(),
        "credit_notes":set(),
        "adjustments": set(),
        "transfers":   set(),
    }

    # Invoices
    try:
        records = _paginate(token, f"{INVENTORY_BASE}/invoices",
                            "invoices", {"date_start": since_date})
        for r in records:
            for li in r.get("line_items", []):
                if li.get("item_id"):
                    affected["invoices"].add(li["item_id"])
    except Exception as e:
        print(f"[CLIENT] Invoice activity check failed: {e}")

    # Bills
    try:
        records = _paginate(token, f"{INVENTORY_BASE}/bills",
                            "bills", {"date_start": since_date})
        for r in records:
            for li in r.get("line_items", []):
                if li.get("item_id"):
                    affected["bills"].add(li["item_id"])
    except Exception as e:
        print(f"[CLIENT] Bill activity check failed: {e}")

    # Credit notes
    try:
        records = _paginate(token, f"{INVENTORY_BASE}/creditnotes",
                            "creditnotes", {"date_start": since_date})
        for r in records:
            for li in r.get("line_items", []):
                if li.get("item_id"):
                    affected["credit_notes"].add(li["item_id"])
    except Exception as e:
        print(f"[CLIENT] Credit note activity check failed: {e}")

    # Inventory adjustments
    try:
        records = _paginate(token, f"{INVENTORY_BASE}/inventoryadjustments",
                            "inventory_adjustments", {"date_start": since_date})
        for r in records:
            for li in r.get("line_items", []):
                if li.get("item_id"):
                    affected["adjustments"].add(li["item_id"])
    except Exception as e:
        print(f"[CLIENT] Adjustment activity check failed: {e}")

    # Transfer orders
    try:
        records = _paginate(token, f"{INVENTORY_BASE}/transferorders",
                            "transfer_orders", {"date_start": since_date})
        for r in records:
            for li in r.get("line_items", []):
                if li.get("item_id"):
                    affected["transfers"].add(li["item_id"])
    except Exception as e:
        print(f"[CLIENT] Transfer order activity check failed: {e}")

    # Convert sets to lists for JSON serialisation
    return {k: list(v) for k, v in affected.items()}


# ---------------------------------------------------------------------------
# Sales orders for velocity computation
# ---------------------------------------------------------------------------

def fetch_sos_date_range(token: str, date_from: str, date_to: str) -> list:
    """
    Fetch all sales orders in a date range (YYYY-MM-DD strings).
    Used by build_velocity.py.
    """
    return _paginate(token, f"{INVENTORY_BASE}/salesorders", "salesorders", {
        "date_start": date_from,
        "date_end":   date_to,
    })


def fetch_so_detail(token: str, so_id: str) -> dict:
    """Fetch a single SO with full line items."""
    data = _get(token, f"{INVENTORY_BASE}/salesorders/{so_id}")
    return data.get("salesorder", {})


# ---------------------------------------------------------------------------
# Date helpers used by multiple pipeline scripts
# ---------------------------------------------------------------------------

def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def parse_date(s: str):
    """Parse YYYY-MM-DD string to date object. Returns None on failure."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None