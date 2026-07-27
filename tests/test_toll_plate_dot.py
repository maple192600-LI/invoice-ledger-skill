from __future__ import annotations

import json
import unittest

from invoice_ledger.contracts import TextUnit, TextUnits
from invoice_ledger.parsing.field_candidates import generate_field_candidates
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


class TollPlateMiddleDotTest(unittest.TestCase):
    """车牌带间隔号(U+00B7,如 晋A·V2068)也必须能抓到,入口不连带丢失。"""

    def test_plate_with_middle_dot_and_entrance_are_captured(self) -> None:
        text = "\n".join((
            "电子发票 发票号码 12345678 开票日期 2026-07-13 价税合计",
            "通行费",
            "晋A·V2068 青龙收费站入 滨河收费站出 2026-07-13 12:43:59",
            "*通行费",
            "服务",
            "3%",
            "次",
            "155.34",
            "4.66",
            "160.00",
            "1",
        ))
        decision = decide_schema(text_units(text))
        self.assertEqual(decision.schema_id, "toll-invoice")
        item = json.loads(generate_field_candidates(text_units(text), decision).fields["items"][0].value)
        self.assertEqual(item["vehicle_plate_no"], "晋A·V2068")
        self.assertEqual(item["toll_entrance"], "青龙收费站")
        self.assertEqual(item["toll_exit"], "滨河收费站")


if __name__ == "__main__":
    unittest.main()
