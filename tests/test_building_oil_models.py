from __future__ import annotations

import json
import unittest

from invoice_ledger.contracts import FieldCandidate, TextUnit, TextUnits
from invoice_ledger.parsing.field_candidates import _enrich_special_items
from invoice_ledger.schema.schema_loader import load_schema
from invoice_ledger.schema.schema_router import decide_schema


def text_units(text: str) -> TextUnits:
    return TextUnits(
        invoice_unit_id="test",
        source="pdf_text",
        page_range=[1],
        units=[TextUnit(text=text, page=1, order=1, source="pdf_text")],
    )


class BuildingOilModelsTest(unittest.TestCase):
    def test_routes_only_exact_special_type_labels(self) -> None:
        signals = "电子发票 发票号码 开票日期 价税合计 "
        cases = {
            "建筑服务": "building-service",
            "成品油": "refined-oil",
            "建筑材料": "standard-invoice",
            "机械租赁": "standard-invoice",
            "某某能源有限公司": "standard-invoice",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(decide_schema(text_units(signals + "\n" + text)).schema_id, expected)

    def test_special_models_keep_shared_headers_and_checks(self) -> None:
        expected_headers = {
            "item_name",
            "spec_model",
            "unit",
            "quantity",
            "unit_price",
            "line_amount",
            "tax_rate",
            "line_tax_amount",
        }
        for schema_id, required_fields in {
            "building-service": {"special_invoice_type", "service_location", "project_name"},
            "refined-oil": {"special_invoice_type", "item_name", "unit", "quantity"},
        }.items():
            with self.subTest(schema_id=schema_id):
                schema = load_schema(schema_id)
                self.assertTrue(required_fields.issubset(schema["fields"]))
                self.assertTrue(all(schema["fields"][name]["required"] for name in required_fields))
                self.assertTrue(schema["line_table"]["required"])
                self.assertTrue(expected_headers.issubset(schema["line_table"]["header_aliases"]))
                self.assertIn(
                    "amount_total_plus_tax_total_equals_total_with_tax",
                    schema["amount_checks"],
                )

    def test_refined_oil_discount_line_keeps_negative_amount_and_link(self) -> None:
        fields = {
            "items": [
                FieldCandidate(
                    value=json.dumps({"line_no": 1, "line_amount": "100.00"}),
                    source="rule",
                    evidence="test",
                    confidence=1,
                ),
                FieldCandidate(
                    value=json.dumps({"line_no": 2, "line_amount": "-10.00"}),
                    source="rule",
                    evidence="test",
                    confidence=1,
                ),
            ]
        }
        _enrich_special_items([], fields, load_schema("refined-oil"))
        regular, discount = (json.loads(candidate.value) for candidate in fields["items"])
        self.assertEqual(regular["line_amount"], "100.00")
        self.assertEqual(discount["line_amount"], "-10.00")
        self.assertEqual(discount["discount_for_line_no"], 1)


if __name__ == "__main__":
    unittest.main()
