from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts.fp_acceptance import _compare_record


class AcceptanceManifestTest(unittest.TestCase):
    def test_compares_schema_amount_items_and_status(self) -> None:
        record = SimpleNamespace(
            schema_id="standard-invoice",
            quality=SimpleNamespace(status=SimpleNamespace(value="ready")),
            invoice=SimpleNamespace(total_with_tax="57.94"),
            items=[
                SimpleNamespace(line_amount="54.66"),
                SimpleNamespace(line_amount="-1.00"),
            ],
        )
        expected = {
            "schema_id": "standard-invoice",
            "status": "ready",
            "invoice": {"total_with_tax": "57.94"},
            "item_count": 2,
            "item_values": {"line_amount": ["54.66", "-1.00"]},
        }
        self.assertEqual(_compare_record(expected, record), [])

    def test_reports_missing_expected_item_value(self) -> None:
        record = SimpleNamespace(
            schema_id="refined-oil",
            quality=SimpleNamespace(status=SimpleNamespace(value="ready")),
            invoice=SimpleNamespace(),
            items=[SimpleNamespace(line_amount="265.49")],
        )
        issues = _compare_record(
            {
                "schema_id": "refined-oil",
                "item_values": {"line_amount": ["-7.96"]},
            },
            record,
        )
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()
