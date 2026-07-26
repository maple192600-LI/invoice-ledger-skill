from __future__ import annotations

import unittest

from invoice_ledger.contracts import TextUnit, TextUnits
from invoice_ledger.parsing._line_item_ocr_table import _extract_ocr_table_items
from invoice_ledger.parsing._parties import _extract_names_and_tax_ids
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


if __name__ == "__main__":
    unittest.main()
