"""
tests/test_build_reps.py
KSL Field Sales Tool

Unit tests for pipeline/build_reps.py.
Focuses on PIN hashing correctness and CSV parsing edge cases.
"""
import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import build_reps as br


class TestHashPin(unittest.TestCase):

    def test_produces_hex_string(self):
        h = br._hash_pin("1234")
        self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_consistent(self):
        self.assertEqual(br._hash_pin("5678"), br._hash_pin("5678"))

    def test_different_pins_differ(self):
        self.assertNotEqual(br._hash_pin("1234"), br._hash_pin("5678"))

    def test_matches_manual_sha256(self):
        expected = hashlib.sha256("9999".encode()).hexdigest()
        self.assertEqual(br._hash_pin("9999"), expected)

    def test_strips_whitespace(self):
        self.assertEqual(br._hash_pin(" 1234 "), br._hash_pin("1234"))


class TestBuildReps(unittest.TestCase):

    def _write_csv(self, rows, path):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "rep_id", "rep_name", "pin", "zoho_customer_id", "customer_name"
            ])
            w.writeheader()
            w.writerows(rows)

    def test_basic_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "reps_customers.csv"
            out_path = Path(tmpdir) / "reps.json"
            self._write_csv([
                {"rep_id": "R01", "rep_name": "Alice",
                 "pin": "1234", "zoho_customer_id": "C001",
                 "customer_name": "Outlet A"},
                {"rep_id": "R01", "rep_name": "Alice",
                 "pin": "1234", "zoho_customer_id": "C002",
                 "customer_name": "Outlet B"},
                {"rep_id": "R02", "rep_name": "Bob",
                 "pin": "5678", "zoho_customer_id": "C003",
                 "customer_name": "Outlet C"},
            ], csv_path)

            # Patch the module paths
            original_in  = br.INPUT_PATH
            original_out = br.OUTPUT_PATH
            br.INPUT_PATH  = csv_path
            br.OUTPUT_PATH = out_path
            try:
                br.run()
            finally:
                br.INPUT_PATH  = original_in
                br.OUTPUT_PATH = original_out

            with open(out_path) as f:
                data = json.load(f)

            reps = {r["rep_id"]: r for r in data["reps"]}
            self.assertIn("R01", reps)
            self.assertIn("R02", reps)
            self.assertEqual(reps["R01"]["customer_count"], 2)
            self.assertEqual(reps["R02"]["customer_count"], 1)

    def test_pin_not_stored_raw(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "reps_customers.csv"
            out_path = Path(tmpdir) / "reps.json"
            self._write_csv([
                {"rep_id": "R01", "rep_name": "Alice",
                 "pin": "9999", "zoho_customer_id": "C001",
                 "customer_name": "Outlet A"},
            ], csv_path)

            br.INPUT_PATH  = csv_path
            br.OUTPUT_PATH = out_path
            try:
                br.run()
            finally:
                br.INPUT_PATH  = ROOT / "data" / "config" / "reps_customers.csv"
                br.OUTPUT_PATH = ROOT / "data" / "app" / "reps.json"

            raw_content = out_path.read_text()
            self.assertNotIn("9999", raw_content)
            self.assertIn(hashlib.sha256("9999".encode()).hexdigest(), raw_content)

    def test_duplicate_customers_deduped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "reps_customers.csv"
            out_path = Path(tmpdir) / "reps.json"
            self._write_csv([
                {"rep_id": "R01", "rep_name": "Alice",
                 "pin": "1234", "zoho_customer_id": "C001",
                 "customer_name": "Outlet A"},
                {"rep_id": "R01", "rep_name": "Alice",
                 "pin": "1234", "zoho_customer_id": "C001",
                 "customer_name": "Outlet A"},
            ], csv_path)

            br.INPUT_PATH  = csv_path
            br.OUTPUT_PATH = out_path
            try:
                br.run()
            finally:
                br.INPUT_PATH  = ROOT / "data" / "config" / "reps_customers.csv"
                br.OUTPUT_PATH = ROOT / "data" / "app" / "reps.json"

            with open(out_path) as f:
                data = json.load(f)
            self.assertEqual(data["reps"][0]["customer_count"], 1)


if __name__ == "__main__":
    unittest.main()
