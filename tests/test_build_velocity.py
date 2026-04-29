"""
tests/test_build_velocity.py
KSL Field Sales Tool

Unit tests for pipeline/build_velocity.py.
Focuses on the ABC classification logic and velocity computation —
the two areas most critical to get right for the reorder calculator.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ZOHO_CLIENT_ID",     "test")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "test")
os.environ.setdefault("ZOHO_REFRESH_TOKEN", "test")
os.environ.setdefault("ZOHO_ORG_ID",        "test")

from pipeline import build_velocity as bv


class TestClassifyABC(unittest.TestCase):
    """ABC classification based on cumulative revenue contribution."""

    def test_top_20_percent_is_A(self):
        # Build a proper case: first SKU is exactly 20% of revenue
        totals = [
            ("SKU_A", 200),   # 20% -> cumulative 20% -> A
            ("SKU_B", 300),   # 30% -> cumulative 50% -> B
            ("SKU_C", 500),   # 50% -> cumulative 100% -> C
        ]
        abc = bv._classify_abc(totals)
        self.assertEqual(abc["SKU_A"], "A")
        self.assertEqual(abc["SKU_B"], "B")
        self.assertEqual(abc["SKU_C"], "C")

    def test_boundary_between_B_and_C(self):
        # SKU A = 15% revenue -> A
        # SKU B = 10% revenue -> cumulative 25% -> B
        # SKU C = 75% revenue -> but listed last so cumulative 100% -> C
        # Build descending: largest first
        totals_sorted = [
            ("LARGE", 750),   # 75% -> cumulative 75% -> C
            ("MED",   150),   # 15% -> cumulative 90% -> C
            ("SMALL", 100),   # 10% -> cumulative 100% -> C
        ]
        abc = bv._classify_abc(totals_sorted)
        # LARGE alone is 75% cumulative which is > 50%, so it's C
        self.assertEqual(abc["LARGE"], "C")

        # Now test proper A/B/C split
        totals_proper = [
            ("TOP",  200),   # 20% -> cumulative 20% -> A
            ("MID1", 150),   # 15% -> cumulative 35% -> B
            ("MID2", 150),   # 15% -> cumulative 50% -> B
            ("BOT1", 250),   # 25% -> cumulative 75% -> C
            ("BOT2", 250),   # 25% -> cumulative 100% -> C
        ]
        abc2 = bv._classify_abc(totals_proper)
        self.assertEqual(abc2["TOP"],  "A")
        self.assertEqual(abc2["MID1"], "B")
        self.assertIn(abc2["BOT1"],    ("C",))

    def test_all_equal_revenue(self):
        totals = [("SKU1", 100), ("SKU2", 100), ("SKU3", 100),
                  ("SKU4", 100), ("SKU5", 100)]
        abc = bv._classify_abc(totals)
        classes = set(abc.values())
        self.assertTrue(classes.issubset({"A", "B", "C"}))

    def test_zero_revenue_all_C(self):
        totals = [("SKU1", 0), ("SKU2", 0)]
        abc = bv._classify_abc(totals)
        self.assertEqual(abc["SKU1"], "C")
        self.assertEqual(abc["SKU2"], "C")


class TestComputeVelocity(unittest.TestCase):
    """Velocity aggregation from row data."""

    def _make_rows(self, entries):
        rows = []
        for sku, name, date_str, qty, rate, customer in entries:
            rows.append({
                bv.HISTORY_SKU_COL:      sku,
                bv.HISTORY_NAME_COL:     name,
                bv.HISTORY_DATE_COL:     date_str,
                bv.HISTORY_QTY_COL:      str(qty),
                bv.HISTORY_RATE_COL:     str(rate),
                bv.HISTORY_CUSTOMER_COL: customer,
            })
        return rows

    def test_weekly_avg_computed_correctly(self):
        from datetime import date, timedelta
        today = date.today()
        rows = self._make_rows([
            ("A001", "Razor", str(today - timedelta(days=7)),  24, 240, "Outlet A"),
            ("A001", "Razor", str(today - timedelta(days=14)), 12, 240, "Outlet A"),
        ])
        result = bv._compute_velocity(rows, weeks=8)
        self.assertIn("A001", result)
        buckets = result["A001"]["week_buckets"]
        total   = sum(buckets.values())
        self.assertEqual(total, 36)

    def test_outlet_count_deduplicates(self):
        from datetime import date, timedelta
        today = date.today()
        rows = self._make_rows([
            ("A001", "Razor", str(today - timedelta(days=3)), 10, 200, "Outlet A"),
            ("A001", "Razor", str(today - timedelta(days=5)), 10, 200, "Outlet A"),
            ("A001", "Razor", str(today - timedelta(days=7)), 10, 200, "Outlet B"),
        ])
        result = bv._compute_velocity(rows, weeks=8)
        self.assertEqual(len(result["A001"]["outlets"]), 2)

    def test_rows_outside_window_excluded(self):
        from datetime import date, timedelta
        today = date.today()
        rows = self._make_rows([
            ("A001", "Razor", str(today - timedelta(weeks=10)), 50, 200, "Outlet A"),
        ])
        result = bv._compute_velocity(rows, weeks=8)
        self.assertNotIn("A001", result)

    def test_missing_sku_skipped(self):
        from datetime import date, timedelta
        today = date.today()
        rows = self._make_rows([
            ("", "Unknown", str(today - timedelta(days=2)), 10, 100, "Outlet A"),
        ])
        result = bv._compute_velocity(rows, weeks=8)
        self.assertNotIn("", result)


class TestBufferMultipliers(unittest.TestCase):

    def test_a_class_has_highest_buffer(self):
        self.assertGreater(bv.ABC_BUFFER["A"], bv.ABC_BUFFER["B"])
        self.assertGreater(bv.ABC_BUFFER["B"], bv.ABC_BUFFER["C"])

    def test_all_classes_present(self):
        self.assertIn("A", bv.ABC_BUFFER)
        self.assertIn("B", bv.ABC_BUFFER)
        self.assertIn("C", bv.ABC_BUFFER)


if __name__ == "__main__":
    unittest.main()
