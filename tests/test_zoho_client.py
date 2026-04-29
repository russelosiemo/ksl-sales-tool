"""
tests/test_zoho_client.py
KSL Field Sales Tool

Unit tests for pipeline/zoho_client.py.
All Zoho API calls are mocked — no network required.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ZOHO_CLIENT_ID",     "test_client_id")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "test_secret")
os.environ.setdefault("ZOHO_REFRESH_TOKEN", "test_refresh")
os.environ.setdefault("ZOHO_ORG_ID",        "test_org_123")

from pipeline import zoho_client as zc


def _mock_response(data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.ok = (status < 400)
    r.status_code = status
    r.json.return_value = data
    r.raise_for_status = MagicMock()
    return r


class TestGetAccessToken(unittest.TestCase):

    @patch("pipeline.zoho_client.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value = _mock_response({"access_token": "abc123"})
        token = zc.get_access_token()
        self.assertEqual(token, "abc123")

    @patch("pipeline.zoho_client.requests.post")
    def test_missing_token_raises(self, mock_post):
        mock_post.return_value = _mock_response({"error": "invalid_grant"})
        with self.assertRaises(RuntimeError):
            zc.get_access_token()


class TestGet(unittest.TestCase):

    @patch("pipeline.zoho_client.requests.get")
    def test_returns_json_on_success(self, mock_get):
        mock_get.return_value = _mock_response({"items": [{"sku": "A001"}]})
        result = zc._get("token", "https://example.com/items")
        self.assertEqual(result["items"][0]["sku"], "A001")

    @patch("pipeline.zoho_client.requests.get")
    def test_retries_on_timeout(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = [
            req_lib.exceptions.Timeout(),
            _mock_response({"items": []}),
        ]
        result = zc._get("token", "https://example.com/items")
        self.assertEqual(mock_get.call_count, 2)

    @patch("pipeline.zoho_client.requests.get")
    def test_raises_after_all_retries(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.Timeout()
        with self.assertRaises(RuntimeError):
            zc._get("token", "https://example.com/items")


class TestPaginate(unittest.TestCase):

    @patch("pipeline.zoho_client.requests.get")
    def test_collects_all_pages(self, mock_get):
        mock_get.side_effect = [
            _mock_response({
                "items": [{"sku": "A001"}, {"sku": "A002"}],
                "page_context": {"has_more_page": True}
            }),
            _mock_response({
                "items": [{"sku": "A003"}],
                "page_context": {"has_more_page": False}
            }),
        ]
        results = zc._paginate("token", "https://example.com/items", "items")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[2]["sku"], "A003")

    @patch("pipeline.zoho_client.requests.get")
    def test_stops_on_empty_batch(self, mock_get):
        mock_get.return_value = _mock_response({
            "items": [],
            "page_context": {"has_more_page": True}
        })
        results = zc._paginate("token", "https://example.com/items", "items")
        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, 1)


class TestFetchTransactionsSince(unittest.TestCase):

    @patch("pipeline.zoho_client._paginate")
    def test_returns_affected_item_ids(self, mock_pag):
        mock_pag.return_value = [
            {"line_items": [{"item_id": "ITEM_001"}, {"item_id": "ITEM_002"}]},
            {"line_items": [{"item_id": "ITEM_001"}]},
        ]
        result = zc.fetch_transactions_since("token", "2026-01-01")
        # All 5 transaction types call _paginate; invoices should have 2 unique IDs
        self.assertIn("invoices", result)
        self.assertIn("ITEM_001", result["invoices"])
        self.assertIn("ITEM_002", result["invoices"])

    @patch("pipeline.zoho_client._paginate")
    def test_returns_empty_when_no_activity(self, mock_pag):
        mock_pag.return_value = []
        result = zc.fetch_transactions_ince("token", "2026-01-01") \
                 if hasattr(zc, "fetch_transactions_ince") \
                 else zc.fetch_transactions_since("token", "2026-01-01")
        for v in result.values():
            self.assertEqual(v, [])


class TestHelpers(unittest.TestCase):

    def test_parse_date_valid(self):
        d = zc.parse_date("2026-03-15")
        from datetime import date
        self.assertEqual(d, date(2026, 3, 15))

    def test_parse_date_empty(self):
        self.assertIsNone(zc.parse_date(""))
        self.assertIsNone(zc.parse_date(None))

    def test_today_str_format(self):
        s = zc.today_str()
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
