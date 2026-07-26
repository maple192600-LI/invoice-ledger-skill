from __future__ import annotations

import unittest

from invoice_ledger.contracts import TextUnit, TextUnits
from invoice_ledger.schema.schema_loader import load_schema
from invoice_ledger.schema.schema_router import decide_schema


def text_units(text: str) -> TextUnits:
    return TextUnits(
        invoice_unit_id="test",
        source="pdf_text",
        page_range=[1],
        units=[
            TextUnit(
                text=text,
                page=1,
                order=1,
                source="pdf_text",
            )
        ],
    )


class SchemaInheritanceAndRoutingTest(unittest.TestCase):
    def test_extension_inherits_standard_fields(self) -> None:
        schema = load_schema("real-estate-operating-lease")
        self.assertIn("invoice_no", schema["fields"])
        self.assertIn("real_estate_address", schema["fields"])

    def test_routes_six_supported_extensions(self) -> None:
        invoice_signals = "电子发票 发票号码 开票日期 价税合计 "
        cases = {
            "建筑服务": "building-service",
            "成品油": "refined-oil",
            "货物运输服务 运输工具种类 起运地 到达地": "freight-transport-service",
            "不动产经营租赁服务": "real-estate-operating-lease",
            "旅客运输服务": "passenger-transport-service",
            "通行费 晋A12345 某某站入 某某站出": "toll-invoice",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    decide_schema(text_units(invoice_signals + text)).schema_id,
                    expected,
                )

    def test_boundaries_remain_standard(self) -> None:
        cases = (
            "电子发票 发票号码 开票日期 价税合计 物流辅助服务 收派服务",
            "电子发票 发票号码 开票日期 价税合计 机票代订",
            "电子发票 发票号码 开票日期 价税合计 机械租赁",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(decide_schema(text_units(text)).schema_id, "standard-invoice")


if __name__ == "__main__":
    unittest.main()
