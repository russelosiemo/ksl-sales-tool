"""
pipeline/build_velocity.py
KSL Field Sales Tool

Computes per-SKU sales velocity metrics and ABC classification.
Runs weekly (Sunday) via GitHub Actions.

Sources:
    1. so_history.csv  (all historical data, manually updated)
    2. Live Zoho API   (last 8 weeks of SOs, fills any gap after CSV)

Output schema per item in velocity.json:
    sku                 str    SKU code
    name                str    Item name
    weekly_avg_qty      float  Average units invoiced per week (8 wk window)
    weekly_avg_value    float  Average KES value invoiced per week
    outlet_count        int    Number of distinct customers stocking this SKU
    sales_rank          int    1 = highest velocity across all customers
    abc_class           str    A (top 20% revenue), B (next 30%), C (bottom 50%)
    weeks_with_data     int    How many of the 8 weeks had sales for this SKU

ABC classification is based on cumulative revenue contribution:
    Class A: SKUs contributing the top 20% of total revenue
    Class B: SKUs contributing the next 30% of total revenue
    Class C: remaining SKUs

Buffer multipliers per class (used by the PWA reorder calculator):
    A: 1.0 week (fast mover, keep well stocked)
    B: 0.75 week
    C: 0.5 week

Usage:
    python -m pipeline.build_velocity
    python -m pipeline.build_velocity --weeks 12   # use 12-week window
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.zoho_client import (
    get_access_token, fetch_sos_date_range, fetch_so_detail, parse_date
)

OUTPUT_PATH    = ROOT / "data" / "app" / "velocity.json"
CATALOGUE_PATH = ROOT / "data" / "app" / "catalogue.json"
HISTORY_PATH   = ROOT / "data" / "config" / "so_history.csv"

ABC_BUFFER = {"A": 1.0, "B": 0.75, "C": 0.5}

HISTORY_CUSTOMER_COL  = "customer_name"
HISTORY_DATE_COL      = "date"
HISTORY_SKU_COL       = "sku"
HISTORY_NAME_COL      = "item_name"
HISTORY_QTY_COL       = "quantity_invoiced"
HISTORY_RATE_COL      = "rate"


def _load_history(weeks: int) -> list:
    """Load rows from so_history.csv within the lookback window."""
    if not HISTORY_PATH.exists():
        print(f"[VELOCITY] so_history.csv not found at {HISTORY_PATH}")
        return []
    cutoff = date.today() - timedelta(weeks=weeks)
    rows   = []
    try:
        with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = parse_date(row.get(HISTORY_DATE_COL, ""))
                if d and d >= cutoff:
                    rows.append(row)
    except Exception as e:
        print(f"[VELOCITY] Could not load so_history.csv: {e}")
    print(f"[VELOCITY] Loaded {len(rows)} rows from so_history.csv")
    return rows


def _load_catalogue_names() -> dict:
    """Returns {sku: name} from catalogue.json."""
    if not CATALOGUE_PATH.exists():
        return {}
    with open(CATALOGUE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {item["sku"]: item["name"] for item in data.get("items", [])}


def _fetch_live_sos(token: str, weeks: int, history_rows: list) -> list:
    """
    Fetch live SOs from Zoho for the last `weeks` weeks.
    Only fetches weeks not already covered by the CSV.
    Returns list of SO line item rows in the same format as history rows.
    """
    if not token:
        return []

    # Find the latest date in history to avoid re-fetching
    latest_in_history = date.min
    for row in history_rows:
        d = parse_date(row.get(HISTORY_DATE_COL, ""))
        if d and d > latest_in_history:
            latest_in_history = d

    fetch_from = latest_in_history + timedelta(days=1) if latest_in_history > date.min \
                 else date.today() - timedelta(weeks=weeks)
    fetch_to   = date.today()

    if fetch_from > fetch_to:
        print("[VELOCITY] History is current. No live fetch needed.")
        return []

    print(f"[VELOCITY] Fetching live SOs from {fetch_from} to {fetch_to}...")
    raw_sos  = fetch_sos_date_range(token, str(fetch_from), str(fetch_to))
    live_rows = []
    for so in raw_sos:
        # Enrich with line items if not present
        if not so.get("line_items"):
            so = fetch_so_detail(token, so["salesorder_id"]) or so
        customer = so.get("customer_name", "")
        so_date  = so.get("date", "")
        for li in so.get("line_items", []):
            qty_inv = float(li.get("quantity_invoiced", 0) or 0)
            if qty_inv <= 0:
                continue
            live_rows.append({
                HISTORY_CUSTOMER_COL: customer,
                HISTORY_DATE_COL:     so_date,
                HISTORY_SKU_COL:      li.get("sku", ""),
                HISTORY_NAME_COL:     li.get("name", ""),
                HISTORY_QTY_COL:      str(qty_inv),
                HISTORY_RATE_COL:     str(li.get("rate", 0)),
            })

    print(f"[VELOCITY] {len(live_rows)} line rows from live Zoho fetch")
    return live_rows


def _compute_velocity(all_rows: list, weeks: int) -> dict:
    """
    Aggregate rows into per-SKU metrics.
    Returns {sku: {name, weekly_buckets, outlet_set, total_value}}
    """
    cutoff = date.today() - timedelta(weeks=weeks)
    sku_data = defaultdict(lambda: {
        "name":           "",
        "week_buckets":   defaultdict(float),  # {iso_week_key: qty}
        "value_buckets":  defaultdict(float),  # {iso_week_key: value}
        "outlets":        set(),
    })

    for row in all_rows:
        d = parse_date(row.get(HISTORY_DATE_COL, ""))
        if not d or d < cutoff:
            continue
        sku = (row.get(HISTORY_SKU_COL) or "").strip()
        if not sku:
            continue
        try:
            qty = float(row.get(HISTORY_QTY_COL, 0) or 0)
        except Exception:
            qty = 0.0
        try:
            rate = float(row.get(HISTORY_RATE_COL, 0) or 0)
        except Exception:
            rate = 0.0

        wk_key = f"{d.isocalendar()[0]}-{d.isocalendar()[1]:02d}"
        sku_data[sku]["name"]                  = row.get(HISTORY_NAME_COL, "")
        sku_data[sku]["week_buckets"][wk_key]  += qty
        sku_data[sku]["value_buckets"][wk_key] += qty * rate
        customer = row.get(HISTORY_CUSTOMER_COL, "").strip()
        if customer:
            sku_data[sku]["outlets"].add(customer)

    return sku_data


def _classify_abc(sku_totals: list) -> dict:
    """
    Assign ABC class based on cumulative revenue contribution.
    sku_totals: list of (sku, total_value) sorted descending.
    Returns {sku: "A"|"B"|"C"}
    """
    grand_total = sum(v for _, v in sku_totals)
    if grand_total == 0:
        return {sku: "C" for sku, _ in sku_totals}

    abc = {}
    cumulative = 0.0
    for sku, val in sku_totals:
        cumulative += val
        pct = cumulative / grand_total
        if pct <= 0.20:
            abc[sku] = "A"
        elif pct <= 0.50:
            abc[sku] = "B"
        else:
            abc[sku] = "C"
    return abc


def run(weeks: int = 8):
    print(f"[VELOCITY] Building velocity data ({weeks}-week window)...")

    token         = get_access_token()
    cat_names     = _load_catalogue_names()
    history_rows  = _load_history(weeks)
    live_rows     = _fetch_live_sos(token, weeks, history_rows)
    all_rows      = history_rows + live_rows

    print(f"[VELOCITY] Total rows to process: {len(all_rows)}")

    sku_data = _compute_velocity(all_rows, weeks)
    if not sku_data:
        print("[VELOCITY] No data to process. Exiting.")
        return

    # Compute weekly averages and totals
    results = []
    for sku, data in sku_data.items():
        wb    = data["week_buckets"]
        vb    = data["value_buckets"]
        n     = max(len(wb), 1)
        total_qty   = sum(wb.values())
        total_val   = sum(vb.values())
        weeks_seen  = len([v for v in wb.values() if v > 0])
        results.append({
            "sku":             sku,
            "name":            data["name"] or cat_names.get(sku, ""),
            "weekly_avg_qty":  round(total_qty / n, 1),
            "weekly_avg_value":round(total_val / n, 1),
            "total_value":     round(total_val, 1),
            "outlet_count":    len(data["outlets"]),
            "weeks_with_data": weeks_seen,
        })

    # Sort by total value descending for ABC classification
    results.sort(key=lambda x: -x["total_value"])
    sku_totals = [(r["sku"], r["total_value"]) for r in results]
    abc_map    = _classify_abc(sku_totals)

    # Assign rank and ABC class
    for rank, item in enumerate(results, 1):
        item["sales_rank"] = rank
        item["abc_class"]  = abc_map.get(item["sku"], "C")
        item["buffer_weeks"] = ABC_BUFFER.get(item["abc_class"], 0.5)
        del item["total_value"]  # internal only, not needed in app

    # Update catalogue.json abc_class field
    _update_catalogue_abc(abc_map)

    output = {
        "_meta": {
            "built_at":      datetime.now(timezone.utc).isoformat(),
            "window_weeks":  weeks,
            "total_skus":    len(results),
            "history_rows":  len(history_rows),
            "live_rows":     len(live_rows),
            "abc_breakdown": {
                "A": sum(1 for r in results if r["abc_class"] == "A"),
                "B": sum(1 for r in results if r["abc_class"] == "B"),
                "C": sum(1 for r in results if r["abc_class"] == "C"),
            },
        },
        "velocity": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    meta = output["_meta"]
    print(f"[VELOCITY] {meta['total_skus']} SKUs written to {OUTPUT_PATH}")
    print(f"[VELOCITY] ABC: A={meta['abc_breakdown']['A']} "
          f"B={meta['abc_breakdown']['B']} C={meta['abc_breakdown']['C']}")


def _update_catalogue_abc(abc_map: dict):
    """Back-fill abc_class into catalogue.json after velocity is computed."""
    if not CATALOGUE_PATH.exists():
        return
    with open(CATALOGUE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    updated = 0
    for item in data.get("items", []):
        cls = abc_map.get(item["sku"], "C")
        if item.get("abc_class") != cls:
            item["abc_class"] = cls
            updated += 1
    if updated:
        with open(CATALOGUE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[VELOCITY] Updated abc_class for {updated} items in catalogue.json")


if __name__ == "__main__":
    weeks = 8
    for arg in sys.argv[1:]:
        if arg.startswith("--weeks="):
            weeks = int(arg.split("=")[1])
        elif arg == "--weeks" and sys.argv.index(arg) + 1 < len(sys.argv):
            weeks = int(sys.argv[sys.argv.index(arg) + 1])
    run(weeks=weeks)