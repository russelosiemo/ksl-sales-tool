"""
pipeline/build_reps.py
KSL Field Sales Tool

Converts data/config/reps_customers.csv into data/app/reps.json.
Runs weekly (Sunday) or whenever the CSV changes.

CSV format (header required):
    rep_id, rep_name, pin, zoho_customer_id, customer_name

The pin column holds the raw 4-digit PIN. It is hashed (SHA-256) before
writing to reps.json. The raw PIN never appears in the output file.

Output reps.json schema:
    reps: [
        {
            rep_id:       str
            rep_name:     str
            pin_hash:     str   SHA-256 hex of the PIN
            customers: [
                {
                    customer_id:   str   Zoho contact ID
                    customer_name: str
                }
            ]
        }
    ]

Usage:
    python -m pipeline.build_reps
"""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INPUT_PATH  = ROOT / "data" / "config" / "reps_customers.csv"
OUTPUT_PATH = ROOT / "data" / "app" / "reps.json"

REQUIRED_COLS = {"rep_id", "rep_name", "pin", "zoho_customer_id", "customer_name"}


def _hash_pin(pin: str) -> str:
    """SHA-256 hash of a PIN string."""
    return hashlib.sha256(pin.strip().encode()).hexdigest()


def run():
    print("[REPS] Building reps.json from CSV...")

    if not INPUT_PATH.exists():
        print(f"[REPS] ERROR: {INPUT_PATH} not found. "
              "Create this file with columns: rep_id,rep_name,pin,"
              "zoho_customer_id,customer_name")
        sys.exit(1)

    rows = []
    with open(INPUT_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        # Strip whitespace from column headers to catch Excel-introduced spaces
        reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
        missing = REQUIRED_COLS - set(reader.fieldnames)
        if missing:
            print(f"[REPS] ERROR: CSV missing columns: {missing}")
            sys.exit(1)
        for row in reader:
            rows.append(row)

    print(f"[REPS] {len(rows)} rows loaded from CSV")

    # Group customers per rep
    rep_meta      = {}              # rep_id -> {rep_name, pin_hash}
    rep_custs     = defaultdict(list)  # rep_id -> [{customer_id, customer_name}]
    seen_custs    = defaultdict(set)   # rep_id -> set of customer_ids (dedup)
    skipped_rows  = 0
    no_pin_reps   = set()          # rep_ids skipped due to missing PIN

    for i, row in enumerate(rows, start=2):  # start=2 because row 1 is the header
        rep_id    = row["rep_id"].strip()
        rep_name  = row["rep_name"].strip()
        pin       = row["pin"].strip()
        cust_id   = row["zoho_customer_id"].strip()
        cust_name = row["customer_name"].strip()

        # FIX: Log every skipped row so missing customers are visible in CI logs
        if not rep_id or not cust_id:
            print(
                f"[REPS] WARNING: Row {i} skipped — "
                f"rep_id='{rep_id}' cust_id='{cust_id}' "
                f"rep_name='{rep_name}' cust_name='{cust_name}'"
            )
            skipped_rows += 1
            continue

        if rep_id not in rep_meta:
            if not pin:
                print(f"[REPS] WARNING: Row {i} — rep {rep_id} ({rep_name}) "
                      "has no PIN; all their customers will be skipped")
                no_pin_reps.add(rep_id)
                skipped_rows += 1
                continue
            rep_meta[rep_id] = {
                "rep_name": rep_name,
                "pin_hash": _hash_pin(pin),
            }

        # If this rep was already marked as no-PIN, skip their subsequent rows too
        if rep_id in no_pin_reps:
            print(f"[REPS] WARNING: Row {i} skipped — rep {rep_id} ({rep_name}) "
                  f"has no PIN (customer '{cust_name}' will not appear)")
            skipped_rows += 1
            continue

        if cust_id not in seen_custs[rep_id]:
            rep_custs[rep_id].append({
                "customer_id":   cust_id,
                "customer_name": cust_name,
            })
            seen_custs[rep_id].add(cust_id)
        else:
            print(f"[REPS] INFO: Row {i} — duplicate customer_id '{cust_id}' "
                  f"for rep {rep_name}, skipping")

    if skipped_rows:
        print(f"[REPS] {skipped_rows} row(s) skipped total (see warnings above)")

    # Build output list sorted by rep_name
    reps_list = []
    for rep_id, meta in sorted(rep_meta.items(),
                                key=lambda x: rep_meta[x[0]]["rep_name"]):
        customers = sorted(rep_custs[rep_id], key=lambda c: c["customer_name"])
        reps_list.append({
            "rep_id":         rep_id,
            "rep_name":       meta["rep_name"],
            "pin_hash":       meta["pin_hash"],
            "customer_count": len(customers),
            "customers":      customers,
        })

    output = {
        "_meta": {
            "built_at":          datetime.now(timezone.utc).isoformat(),
            "total_reps":        len(reps_list),
            "total_assignments": sum(r["customer_count"] for r in reps_list),
            "source":            "data/config/reps_customers.csv",
        },
        "reps": reps_list,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[REPS] {len(reps_list)} reps written to {OUTPUT_PATH}")
    for rep in reps_list:
        print(f"[REPS]   {rep['rep_name']} ({rep['rep_id']}): "
              f"{rep['customer_count']} customer(s)")


if __name__ == "__main__":
    run()
