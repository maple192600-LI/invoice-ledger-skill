from __future__ import annotations

import json
import unittest

from invoice_ledger.contracts import InvoiceUnit, RecognitionStatus, TextUnit, TextUnits
from invoice_ledger.parsing._line_item_ocr_table import _extract_freight_subtable
from invoice_ledger.parsing.field_candidates import generate_field_candidates
from invoice_ledger.parsing.field_resolver import resolve_invoice_record
from invoice_ledger.schema.schema_loader import load_schema
from invoice_ledger.schema.schema_router import decide_schema
from invoice_ledger.validation.record_validator import validate_invoice_record


def text_units(text: str) -> TextUnits:
    return TextUnits(
        invoice_unit_id="test",
        source="pdf_text",
        page_range=[1],
        units=[
            TextUnit(text=line, page=1, order=index, source="pdf_text")
            for index, line in enumerate(text.splitlines(), start=1)
        ],
    )


class FreightTollModelsTest(unittest.TestCase):
    def test_freight_subtable_scales_with_page_coordinates(self) -> None:
        headers = ("运输工具种类", "运输工具牌号", "起运地", "到达地", "运输货物名称")
        values = ("公路运输", "测试牌号", "甲地", "乙地", "测试货物")
        schema = load_schema("freight-transport-service")
        for scale in (1, 2, 3):
            spans = []
            for index, (header, value) in enumerate(zip(headers, values)):
                x0 = (index * 100 + 10) * scale
                spans.append({"text": header, "x0": x0, "x1": x0 + 60 * scale, "y0": 100 * scale, "y1": 110 * scale})
                spans.append({"text": value, "x0": x0, "x1": x0 + 60 * scale, "y0": 130 * scale, "y1": 140 * scale})
            spans.append({"text": "价税合计", "x0": 10, "x1": 80, "y0": 180 * scale, "y1": 190 * scale})
            with self.subTest(scale=scale):
                rows = _extract_freight_subtable(spans, schema)
                self.assertEqual(len(rows), 1)
                self.assertEqual(set(rows[0].values()), set(values))

    def test_freight_keeps_five_required_fields_and_excludes_sf_collection(self) -> None:
        schema = load_schema("freight-transport-service")
        self.assertTrue(all(schema["fields"][field]["required"] for field in (
            "transport_vehicle_type",
            "transport_vehicle_no",
            "origin_place",
            "destination_place",
            "transport_goods_name",
        )))
        self.assertEqual(
            decide_schema(text_units("电子发票 发票号码 开票日期 价税合计 顺丰速运 收派服务")).schema_id,
            "standard-invoice",
        )

    def test_toll_context_populates_first_item_and_parking_stays_real_estate(self) -> None:
        text = "\n".join((
            "电子发票 发票号码 12345678 开票日期 2026-07-26 价税合计",
            "通行费",
            "晋A12345 昌平站入 清河站出 2026-07-26 08:30:00",
            "*通行费",
            "服务",
            "3%",
            "次",
            "10.00",
            "0.00",
            "10.00",
            "1",
        ))
        decision = decide_schema(text_units(text))
        self.assertEqual(decision.schema_id, "toll-invoice")
        candidate = generate_field_candidates(text_units(text), decision).fields["items"][0]
        item = json.loads(candidate.value)
        self.assertEqual(
            {field: item[field] for field in ("toll_variant", "vehicle_plate_no", "toll_entrance", "toll_exit", "passage_time")},
            {
                "toll_variant": "征税",
                "vehicle_plate_no": "晋A12345",
                "toll_entrance": "昌平站",
                "toll_exit": "清河站",
                "passage_time": "2026-07-26 08:30:00",
            },
        )
        self.assertEqual(
            decide_schema(text_units("电子发票 发票号码 开票日期 价税合计\n不动产经营租赁服务\n停车费")).schema_id,
            "real-estate-operating-lease",
        )

    def test_etc_recharge_non_tax_recognized_ready_and_written(self) -> None:
        units = text_units("""电子发票（普通发票）
发票号码：00000000000000000001
开票日期：2026年07月26日
购买方名称：示例采购集团有限公司
统一社会信用代码/纳税人识别号：TESTBUYER000000001
销售方名称：ETC客户服务机构
统一社会信用代码/纳税人识别号：TESTSELLER00000001
ETC充值
*预付卡充值* 服务 次 1 100.00 100.00 不征税
不征税
价税合计（小写）¥100.00""")
        decision = decide_schema(units)
        # PR#7 后无站入/站出的通行费票面（含 ETC 充值）回退 standard-invoice；要求识别成功、不复核、直接写入
        self.assertEqual(decision.schema_id, "standard-invoice")
        self.assertEqual(decision.variant_id, "digital-invoice-form")
        record = validate_invoice_record(
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
        # 不征税发票：明细无税额，合计金额=价税合计、税额=0（不编造税额）
        self.assertIsNone(record.items[0].line_tax_amount)
        self.assertEqual(str(record.invoice.amount_total), "100.00")
        self.assertEqual(str(record.invoice.tax_total), "0.00")
        self.assertEqual(str(record.invoice.total_with_tax), "100.00")
        self.assertEqual(record.quality.status.value, "ready")


if __name__ == "__main__":
    unittest.main()
