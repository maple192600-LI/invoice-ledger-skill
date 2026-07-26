from __future__ import annotations

import json
import unittest

from invoice_ledger.contracts import FieldCandidate, FieldCandidates, InvoiceSource, InvoiceUnit, RecognitionStatus, SchemaDecision, SchemaDecisionStatus, TextUnit, TextUnits
from invoice_ledger.parsing.field_candidates import _enrich_special_items, generate_field_candidates
from invoice_ledger.parsing.field_resolver import resolve_invoice_record
from invoice_ledger.schema.schema_router import decide_schema
from invoice_ledger.validation.record_validator import validate_invoice_record


def text_units(text: str) -> TextUnits:
    return TextUnits(
        invoice_unit_id="test",
        source="ocr",
        page_range=[1],
        units=[
            TextUnit(text=line, page=1, bbox=[0, 0, 0, 0], order=index, source="ocr")
            for index, line in enumerate(text.splitlines(), 1)
        ],
    )


def record_for(units: TextUnits):
    decision = decide_schema(units)
    return validate_invoice_record(
        resolve_invoice_record(
            InvoiceUnit(
                invoice_unit_id=units.invoice_unit_id,
                source_file="sample.pdf",
                page_range=[1],
                unit_type="pdf_page",
                status=RecognitionStatus.READY,
            ),
            decision,
            generate_field_candidates(units, decision),
        )
    )


class PropertyPassengerModelsTest(unittest.TestCase):
    def test_item_amount_comparison_risk_survives_resolve_and_validate(self) -> None:
        candidates = FieldCandidates(
            invoice_unit_id="item-risk",
            schema_id="standard-invoice",
            fields={
                "items": [
                    FieldCandidate(
                        value=json.dumps({"line_no": 1, "item_name": "服务", "line_amount": "30.00"}),
                        source="test",
                        confidence=0.9,
                        evidence="sequence",
                        risk=["item_amount_comparison_failed"],
                    )
                ]
            },
        )
        record = resolve_invoice_record(
            InvoiceUnit(invoice_unit_id="item-risk", source_file="sample.pdf", page_range=[1], unit_type="pdf_page", status=RecognitionStatus.READY),
            SchemaDecision(invoice_unit_id="item-risk", schema_id="standard-invoice", confidence=0.9, decision=SchemaDecisionStatus.MATCHED),
            candidates,
        )
        validated = validate_invoice_record(record, {"fields": {}, "amount_checks": []})
        self.assertEqual(validated.quality.status, RecognitionStatus.REVIEW_REQUIRED)
        self.assertIn("item amount comparison failed", validated.quality.remark)

    def test_schema_context_does_not_overwrite_table_value(self) -> None:
        candidate = FieldCandidate(
            value=json.dumps({"area_unit": "平方米"}),
            source="test",
            confidence=0.8,
            evidence="coordinate table",
        )
        _enrich_special_items(
            ["税率 9%"],
            {"items": [candidate]},
            {"item_context_patterns": {"area_unit": {"patterns": [r"(9%)"]}}},
        )
        self.assertEqual(json.loads(candidate.value)["area_unit"], "平方米")

    def test_property_lease_keeps_ticket_values_and_mobile_lease_is_standard(self) -> None:
        property_record = record_for(text_units("""电子发票（普通发票）
不动产经营租赁服务
发票号码：25142000000026194342
开票日期：2025年04月09日
*经营租赁*停车费
无
㎡
1
45.87155963
45.87
9%
4.13
不动产地址:山西省太原市小店区南中环街426号
租赁期起止:2025-04-08 00:00 2025-04-09 23:59; 跨地（市）标志:否;"""))
        item = property_record.items[0]
        self.assertEqual(property_record.schema_id, "real-estate-operating-lease")
        self.assertNotIn("missing evidence", property_record.quality.remark)
        self.assertEqual(item.property_certificate_no, "无")
        self.assertEqual(item.area_unit, "㎡")
        self.assertEqual(item.real_estate_address, "山西省太原市小店区南中环街426号")
        self.assertEqual((item.lease_start_date, item.lease_end_date, item.cross_city_flag), ("2025-04-08", "2025-04-09", "否"))
        self.assertEqual(decide_schema(text_units("电子发票 发票号码 开票日期 价税合计 *动产租赁*水车租赁")).schema_id, "standard-invoice")

    def test_gaode_headers_without_values_stay_empty_and_require_review(self) -> None:
        record = record_for(text_units("""电子发票（普通发票）
旅客运输服务
发票号码：25427000000134934316
开票日期：2025年04月16日
*运输服务*客运服务费
12.5
1
12.50
3%
0.38
出行人
有效身份证件号
出行日期
出发地
到达地
等级
交通工具类型"""))
        self.assertEqual(record.schema_id, "passenger-transport-service")
        self.assertEqual(record.quality.status, RecognitionStatus.REVIEW_REQUIRED)
        self.assertIn("missing traveler_name", record.quality.remark)
        self.assertIsNone(record.items[0].traveler_name)
        self.assertIsNone(record.items[0].transport_type)


if __name__ == "__main__":
    unittest.main()
