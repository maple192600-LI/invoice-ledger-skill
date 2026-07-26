from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from invoice_ledger.contracts import FieldCandidate, TextUnit, TextUnits
from invoice_ledger.parsing._line_item_sequence import _extract_items_from_text_units
from invoice_ledger.parsing._line_item_ocr_table import _extract_ocr_table_items
from invoice_ledger.parsing._parties import _extract_names_and_tax_ids, _role_party_values_from_columns
from invoice_ledger.parsing._totals import _extract_money_totals
from invoice_ledger.schema.schema_loader import load_schema


def unit(text: str, x: float, y: float, order: int) -> TextUnit:
    return TextUnit(
        text=text,
        page=1,
        bbox=[x, y, x + 40, y + 12],
        order=order,
        source="ocr",
    )


class CommonParserRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_schema("standard-invoice")

    def test_single_page_visual_total_and_no_money_order_fallback(self) -> None:
        fields: dict = {}
        text_units = TextUnits(
            invoice_unit_id="total",
            source="pdf_text",
            page_range=[1],
            units=[
                unit("合", 10, 100, 1), unit("计", 30, 100, 2), unit("100.00", 80, 100, 3), unit("13.00", 150, 100, 4),
                unit("价税合计（小写）", 10, 150, 5), unit("113.00", 100, 161, 6),
            ],
        )
        _extract_money_totals(["¥1.00", "¥2.00"], fields, self.schema, text_units)
        self.assertEqual(fields["amount_total"][0].value, "100.00")
        self.assertEqual(fields["tax_total"][0].value, "13.00")
        self.assertEqual(fields["total_with_tax"][0].value, "113.00")

    def test_final_page_total_uses_arithmetic_to_select_cumulative_pair(self) -> None:
        fields: dict = {}
        text_units = TextUnits(
            invoice_unit_id="multi-page-total",
            source="pdf_text",
            page_range=[1, 2],
            units=[
                TextUnit(text="小 ¥98496.30 ¥12804.50", page=2, bbox=[10, 100, 300, 110], order=1, source="pdf_text"),
                TextUnit(text="计 ¥1718849.19", page=2, bbox=[10, 115, 300, 125], order=2, source="pdf_text"),
                TextUnit(text="合 计 ¥223450.39", page=2, bbox=[10, 130, 300, 140], order=3, source="pdf_text"),
                TextUnit(text="价税合计（小写） ¥1942299.58", page=2, bbox=[10, 150, 400, 160], order=4, source="pdf_text"),
            ],
        )
        _extract_money_totals([], fields, self.schema, text_units)
        self.assertEqual(fields["amount_total"][0].value, "1718849.19")
        self.assertEqual(fields["tax_total"][0].value, "223450.39")
        self.assertEqual(fields["total_with_tax"][0].value, "1942299.58")

    def test_party_column_geometry_binds_buyer_and_seller(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="party",
            source="ocr",
            units=[
                unit("购买方", 100, 20, 1), unit("销售方", 500, 20, 2),
                unit("甲有限公司", 100, 50, 3), unit("乙有限公司", 500, 50, 4),
                unit("92140110MA0KFPPE9P", 100, 80, 5), unit("91140122778108880D", 500, 80, 6),
            ],
        )
        fields: dict = {}
        _extract_names_and_tax_ids([], fields, self.schema, text_units)
        self.assertEqual(fields["buyer_name"][-1].value, "甲有限公司")
        self.assertEqual(fields["seller_name"][-1].value, "乙有限公司")
        self.assertEqual(fields["buyer_tax_id"][-1].value, "92140110MA0KFPPE9P")
        self.assertEqual(fields["seller_tax_id"][-1].value, "91140122778108880D")

    def test_vertical_party_headers_keep_names_inside_header_height(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="vertical-party",
            source="ocr",
            units=[
                TextUnit(text="购买方信息", page=1, bbox=[28, 154, 46, 238], order=1, source="ocr"),
                TextUnit(text="名称：大连嘉世精密机械制造有限公司", page=1, bbox=[58, 158, 304, 172], order=2, source="ocr"),
                TextUnit(text="销售方信息", page=1, bbox=[504, 154, 521, 238], order=3, source="ocr"),
                TextUnit(text="名称：上海优定电子科技有限公司", page=1, bbox=[533, 159, 747, 173], order=4, source="ocr"),
                TextUnit(text="规格型号", page=1, bbox=[197, 248, 264, 269], order=5, source="ocr"),
                TextUnit(text="单价", page=1, bbox=[558, 249, 607, 268], order=6, source="ocr"),
            ],
        )
        fields: dict = {}
        _extract_names_and_tax_ids(
            [item.text for item in text_units.units],
            fields,
            self.schema,
            text_units,
        )
        buyer = max(fields["buyer_name"], key=lambda item: item.confidence)
        seller = max(fields["seller_name"], key=lambda item: item.confidence)
        self.assertEqual(buyer.value, "大连嘉世精密机械制造有限公司")
        self.assertEqual(seller.value, "上海优定电子科技有限公司")

    def test_party_columns_do_not_read_names_below_item_header(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="missing-party",
            source="ocr",
            units=[
                TextUnit(text="购买方信息", page=1, bbox=[28, 154, 46, 238], order=1, source="ocr"),
                TextUnit(text="销售方信息", page=1, bbox=[504, 154, 521, 238], order=2, source="ocr"),
                TextUnit(text="规格型号", page=1, bbox=[197, 248, 264, 269], order=3, source="ocr"),
                TextUnit(text="*信息技术服务*平台维护", page=1, bbox=[58, 280, 300, 295], order=4, source="ocr"),
            ],
        )
        self.assertNotIn("buyer_name", _role_party_values_from_columns(text_units))

    def test_ocr_table_uses_header_columns_and_stops_at_total(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"]
        values = ["*服务", "规格", "项", "2", "50", "100.00", "13%", "13.00"]
        units = [unit(text, index * 100, 10, index + 1) for index, text in enumerate(headers)]
        units += [unit(text, index * 100, 40, index + 20) for index, text in enumerate(values)]
        units += [unit("合计", 0, 70, 40), unit("*错误行", 0, 100, 41)]
        text_units = TextUnits(invoice_unit_id="table", source="ocr", units=units)
        items = _extract_ocr_table_items(text_units, self.schema)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["line_amount"], "100.00")
        self.assertEqual(items[0]["line_tax_amount"], "13.00")

    def test_text_sequence_keeps_more_items_than_coordinate_table(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="discount",
            source="pdf_text",
            units=[unit("*汽油", 0, 10, 1)],
        )
        fields: dict = {}

        def add_two_items(_lines, target, _schema) -> None:
            for line_no in (1, 2):
                target.setdefault("items", []).append(
                    FieldCandidate(
                        value=json.dumps({"line_no": line_no}),
                        source="test",
                        confidence=0.8,
                        evidence="sequence",
                    )
                )

        with (
            patch("invoice_ledger.parsing._line_item_sequence._extract_items", side_effect=add_two_items),
            patch(
                "invoice_ledger.parsing._line_item_sequence._extract_digital_text_table_items",
                return_value=[{"item_name": "*汽油"}],
            ),
        ):
            _extract_items_from_text_units(text_units, fields, self.schema)

        self.assertEqual(len(fields["items"]), 2)

    def test_text_sequence_wins_when_equal_count_matches_invoice_total(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="cumulative-total",
            source="pdf_text",
            units=[unit("*服务", 0, 10, 1)],
        )
        fields: dict = {
            "amount_total": [
                FieldCandidate(value="30.00", source="test", confidence=1, evidence="total")
            ]
        }

        def add_sequence(_lines, target, _schema) -> None:
            for line_no, amount in ((1, "10.00"), (2, "20.00")):
                target.setdefault("items", []).append(
                    FieldCandidate(
                        value=json.dumps({"line_no": line_no, "line_amount": amount}),
                        source="test",
                        confidence=0.8,
                        evidence="sequence",
                    )
                )

        with (
            patch("invoice_ledger.parsing._line_item_sequence._extract_items", side_effect=add_sequence),
            patch(
                "invoice_ledger.parsing._line_item_sequence._extract_digital_text_table_items",
                return_value=[
                    {"item_name": "*服务一", "line_amount": "10.00"},
                    {"item_name": "*服务二", "line_amount": "30.00"},
                ],
            ),
        ):
            _extract_items_from_text_units(text_units, fields, self.schema)

        self.assertEqual(json.loads(fields["items"][1].value)["line_amount"], "20.00")

    def test_star_tax_columns_keep_zero_tax_item(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="star-tax",
            source="pdf_text",
            units=[
                TextUnit(text=text, page=1, order=index, source="pdf_text")
                for index, text in enumerate(("*电信服务*通信服务费", "项", "1", "100", "100.00", "*", "*"), 1)
            ],
        )
        fields: dict = {}
        _extract_items_from_text_units(text_units, fields, self.schema)
        item = json.loads(fields["items"][0].value)
        self.assertEqual((item["line_amount"], item["tax_rate"], item["line_tax_amount"]), ("100.00", "*", "0.00"))


if __name__ == "__main__":
    unittest.main()
