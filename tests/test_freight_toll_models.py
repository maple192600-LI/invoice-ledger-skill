from __future__ import annotations

import json
import unittest

from invoice_ledger.contracts import TextUnit, TextUnits
from invoice_ledger.parsing.field_candidates import generate_field_candidates
from invoice_ledger.schema.schema_loader import load_schema
from invoice_ledger.schema.schema_router import decide_schema


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
            "通行费 某某站入 某某站出",
            "征税类型：征税",
            "车牌号：晋A12345",
            "入口：昌平站",
            "出口：清河站",
            "通行时间：2026-07-26 08:30:00",
            "*通行费",
            "服务",
            "不征税",
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
            decide_schema(text_units("电子发票 发票号码 开票日期 价税合计 不动产经营租赁服务 停车费")).schema_id,
            "real-estate-operating-lease",
        )


if __name__ == "__main__":
    unittest.main()
