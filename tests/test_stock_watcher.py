"""
tests/test_stock_watcher.py
KSL Field Sales Tool

Unit tests for pipeline/stock_watcher.py.
Focuses on the delta logic, stock extraction, and the skip-when-no-activity path.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ZOHO_MAIN_WH1",     "Main WH 1")
os.environ.setdefault("ZOHO_MAIN_WH2",     "Main WH 2")
os.environ.setdefault("ZOHO_CLIENT_ID",     "test")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "test")
os.environ.setdefault("ZOHO_REFRESH_TOKEN", "test")
os.environ.setdefault("ZOHO_ORG_ID",        "test")

from pipeline import stock_watcher as sw


class TestExtractStock(unittest.TestCase):

    def test_extracts_correct_warehouses(self):
        item = {
            "item_id": "ITEM_001",
            "warehouses": [
                {"warehouse_name": "Main WH 1", "warehouse_stock_on_hand": 50},
                {"warehouse_name": "Main WH 2", "warehouse_stock_on_hand": 30},
                {"warehouse_name": "Consignment", "warehouse_stock_on_hand": 100},
            ]
        }
        result = sw._extract_stock(item, "Main WH 1", "Main WH 2")
        self.assertEqual(result["wh1"],      50)
        self.assertEqual(result["wh2"],      30)
        self.assertEqual(result["combined"], 80)

    def test_ignores_unknown_warehouses(self):
        item = {
            "item_id": "ITEM_002",
            "warehouses": [
                {"warehouse_name": "Unknown WH", "warehouse_stock_on_hand": 999},
            ]
        }
        result = sw._extract_stock(item, "Main WH 1", "Main WH 2")
        self.assertEqual(result["wh1"],      0)
        self.assertEqual(result["wh2"],      0)
        self.assertEqual(result["combined"], 0)

    def test_handles_missing_warehouses_key(self):
        item = {"item_id": "ITEM_003"}
        result = sw._extract_stock(item, "Main WH 1", "Main WH 2")
        self.assertEqual(result["combined"], 0)

    def test_rounds_to_whole_number(self):
        item = {
            "item_id": "ITEM_004",
            "warehouses": [
                {"warehouse_name": "Main WH 1", "warehouse_stock_on_hand": 10.6},
                {"warehouse_name": "Main WH 2", "warehouse_stock_on_hand": 5.3},
            ]
        }
        result = sw._extract_stock(item, "Main WH 1", "Main WH 2")
        self.assertIsInstance(result["wh1"], int)
        self.assertIsInstance(result["combined"], int)


class TestDeltaRefresh(unittest.TestCase):

    @patch("pipeline.stock_watcher.fetch_transactions_since")
    @patch("pipeline.stock_watcher.fetch_items_batch")
    def test_skips_when_no_activity(self, mock_batch, mock_txn):
        mock_txn.return_value = {
            "invoices": [], "bills": [], "credit_notes": [],
            "adjustments": [], "transfers": []
        }
        stock_map, count, skipped = sw._delta_refresh(
            "token", "Main WH 1", "Main WH 2",
            "2026-01-01", {}, {"stock": {}}
        )
        self.assertTrue(skipped)
        self.assertEqual(count, 0)
        mock_batch.assert_not_called()

    @patch("pipeline.stock_watcher.fetch_item_detail")
    @patch("pipeline.stock_watcher.fetch_transactions_since")
    @patch("pipeline.stock_watcher.fetch_items_batch")
    def test_updates_only_affected_skus(self, mock_batch, mock_txn, mock_detail):
        mock_txn.return_value = {
            "invoices": ["ITEM_001"], "bills": [], "credit_notes": [],
            "adjustments": [], "transfers": []
        }
        mock_batch.return_value = [{
            "item_id": "ITEM_001",
            "sku": "AG0001",
            "warehouses": [
                {"warehouse_name": "Main WH 1", "warehouse_stock_on_hand": 25},
                {"warehouse_name": "Main WH 2", "warehouse_stock_on_hand": 10},
            ]
        }]
        mock_detail.return_value = {}

        existing = {
            "stock": {
                "AG0001": {"wh1": 50, "wh2": 20, "combined": 70},
                "AG0002": {"wh1": 100, "wh2": 0, "combined": 100},
            }
        }
        catalogue_index = {"ITEM_001": "AG0001", "ITEM_002": "AG0002"}

        stock_map, count, skipped = sw._delta_refresh(
            "token", "Main WH 1", "Main WH 2",
            "2026-01-01", catalogue_index, existing
        )

        self.assertFalse(skipped)
        self.assertEqual(count, 1)
        self.assertEqual(stock_map["AG0001"]["combined"], 35)
        self.assertEqual(stock_map["AG0002"]["combined"], 100)


if __name__ == "__main__":
    unittest.main()
