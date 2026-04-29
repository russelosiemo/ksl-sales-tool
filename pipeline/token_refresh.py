"""
pipeline/token_refresh.py
KSL Field Sales Tool

Writes a fresh Zoho access token to data/app/token.json.
Also discovers and writes the Zoho Mail account ID so the PWA
can send emails without a hardcoded placeholder.
Run by GitHub Actions every hour so the PWA always has a usable token.
Token expires after 1 hour. The PWA reads this file for all live API calls.

Usage:
    python -m pipeline.token_refresh
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.zoho_client import get_access_token

OUTPUT_PATH = ROOT / "data" / "app" / "token.json"
MAIL_ADDRESS = "russel@kingdom.limited"


def _get_mail_account_id(token: str) -> str:
    """
    Fetch Zoho Mail accounts and return the numeric accountId for
    MAIL_ADDRESS (or the first account if no exact match).
    Returns empty string if Mail scopes are not present on this token.
    """
    try:
        r = requests.get(
            "https://mail.zoho.com/api/accounts",
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            timeout=20,
        )
        if not r.ok:
            print(f"[TOKEN] Mail accounts returned {r.status_code} — skipping mail ID")
            return ""
        accounts = r.json().get("data", [])
        for acc in accounts:
            if acc.get("mailAddress", "").lower() == MAIL_ADDRESS.lower():
                aid = str(acc.get("accountId", ""))
                print(f"[TOKEN] Mail account found: {MAIL_ADDRESS} → {aid}")
                return aid
        # Fallback: use first account
        if accounts:
            aid = str(accounts[0].get("accountId", ""))
            print(f"[TOKEN] Mail account fallback (first account): {aid}")
            return aid
    except Exception as e:
        print(f"[TOKEN] Could not fetch mail account ID: {e}")
    return ""


def run():
    print("[TOKEN] Refreshing access token...")
    token = get_access_token()

    mail_account_id = _get_mail_account_id(token)

    payload = {
        "access_token":    token,
        "refreshed_at":    datetime.now(timezone.utc).isoformat(),
        "org_id":          os.environ.get("ZOHO_ORG_ID", ""),
        "mail_account_id": mail_account_id,
        "note":            "Rotated hourly by GitHub Actions. Valid for 60 minutes.",
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"[TOKEN] Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    run()