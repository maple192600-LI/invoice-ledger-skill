from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from invoice_ledger.contracts import (
    InvoiceFields,
    InvoiceQuality,
    InvoiceRecord,
    InvoiceSource,
    RecognitionStatus,
    TextUnit,
    TextUnits,
)
from invoice_ledger.schema import schema_loader
from invoice_ledger.schema.schema_loader import load_schema
from invoice_ledger.schema.schema_router import decide_schema
from invoice_ledger.validation.record_validator import validate_invoice_record


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
    def test_inheritance_cycle_reports_complete_chain(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            schemas = root / "schemas"
            schemas.mkdir()
            (schemas / "a.yaml").write_text("extends: b\n", encoding="utf-8")
            (schemas / "b.yaml").write_text("extends: a\n", encoding="utf-8")

            load_schema.cache_clear()
            with patch.object(schema_loader, "PROJECT_ROOT", root):
                with self.assertRaisesRegex(ValueError, r"a -> b -> a"):
                    load_schema("a")
            self.assertEqual(load_schema.cache_info().currsize, 0)
            load_schema.cache_clear()

    def test_self_inheritance_uses_cycle_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            schemas = root / "schemas"
            schemas.mkdir()
            (schemas / "a.yaml").write_text("extends: a\n", encoding="utf-8")

            load_schema.cache_clear()
            with patch.object(schema_loader, "PROJECT_ROOT", root):
                with self.assertRaisesRegex(ValueError, r"a -> a"):
                    load_schema("a")
            load_schema.cache_clear()

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
                    decide_schema(text_units(invoice_signals + "\n" + text)).schema_id,
                    expected,
                )

    def test_boundaries_remain_standard(self) -> None:
        cases = (
            "电子发票 发票号码 开票日期 价税合计 物流辅助服务 收派服务",
            "电子发票 发票号码 开票日期 价税合计 机票代订",
            "电子发票 发票号码 开票日期 价税合计 机械租赁",
            "电子发票 发票号码 开票日期 价税合计 *信息技术服务*旅客运输服务平台软件维护",
            "电子发票 发票号码 开票日期 价税合计 *咨询服务*成品油行业咨询",
            "电子发票 发票号码 开票日期 价税合计 *租赁服务*建筑服务设备出租",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(decide_schema(text_units(text)).schema_id, "standard-invoice")

    def test_extension_keeps_digital_invoice_number_validation(self) -> None:
        record = InvoiceRecord(
            invoice_unit_id="test",
            schema_id="building-service",
            variant_id="digital-invoice-form",
            source=InvoiceSource(source_file="test.pdf", page_range=[1]),
            invoice=InvoiceFields(invoice_no="123", total_with_tax="1.00"),
            items=[],
            quality=InvoiceQuality(
                status=RecognitionStatus.REVIEW_REQUIRED,
                confidence=1,
            ),
        )
        validated = validate_invoice_record(record)
        self.assertIn("digital invoice number invalid", validated.quality.remark)


if __name__ == "__main__":
    unittest.main()
