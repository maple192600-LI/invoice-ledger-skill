from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from invoice_ledger.contracts import FieldCandidate, TextUnit, TextUnits
from invoice_ledger.parsing._helpers import _add
from invoice_ledger.parsing._line_item_sequence import _extract_items_from_text_units
from invoice_ledger.parsing._line_item_ocr_table import _derive_digital_edges, _extract_digital_text_table_items, _extract_ocr_table_items, _ocr_table_layout
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

    def test_duplicate_candidate_keeps_stronger_evidence(self) -> None:
        fields: dict = {}
        _add(fields, "seller_name", "乙有限公司", "weak geometry", 0.84, ["weak_geometry"])
        _add(fields, "seller_name", "乙有限公司", "party column geometry", 0.99)
        candidate = fields["seller_name"][0]
        self.assertEqual(candidate.confidence, 0.99)
        self.assertEqual(candidate.evidence, "party column geometry")
        self.assertEqual(candidate.risk, [])

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

    def test_plain_yuan_detail_line_does_not_become_totals(self) -> None:
        fields: dict = {}
        _extract_money_totals(["商品明细 ¥100.00 ¥13.00"], fields, self.schema)
        self.assertNotIn("amount_total", fields)
        self.assertNotIn("tax_total", fields)

    def test_total_and_ocr_table_geometry_scale_with_text_height(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"]
        values = ["*服务", "规格", "项", "2", "50", "100.00", "13%", "13.00"]
        for scale in (0.5, 1, 2, 3):
            with self.subTest(scale=scale):
                units = [
                    TextUnit(
                        text=text,
                        page=1,
                        bbox=[index * 100 * scale, (10 + (index % 2) * 1.5) * scale, (index * 100 + 40) * scale, (22 + (index % 2) * 1.5) * scale],
                        order=index + 1,
                        source="ocr",
                    )
                    for index, text in enumerate(headers)
                ]
                units += [
                    TextUnit(text=text, page=1, bbox=[index * 100 * scale, 14 * scale, (index * 100 + 40) * scale, 26 * scale], order=index + 20, source="ocr")
                    for index, text in enumerate(values)
                ]
                units += [TextUnit(text="合计", page=1, bbox=[0, 40 * scale, 40 * scale, 52 * scale], order=40, source="ocr")]
                table_units = TextUnits(invoice_unit_id="scaled-table", source="ocr", units=units)
                _, end_y = _ocr_table_layout(table_units, self.schema)
                self.assertEqual(len(_extract_ocr_table_items(table_units, self.schema)), 1)
                self.assertIn(1, end_y)

                fields: dict = {}
                total_units = TextUnits(
                    invoice_unit_id="scaled-total",
                    source="pdf_text",
                    page_range=[1],
                    units=[
                        TextUnit(text="价税合计（小写）", page=1, bbox=[0, 100 * scale, 100 * scale, 110 * scale], order=1, source="pdf_text"),
                        TextUnit(text="113.00", page=1, bbox=[0, 118 * scale, 50 * scale, 128 * scale], order=2, source="pdf_text"),
                    ],
                )
                _extract_money_totals([], fields, self.schema, total_units)
                self.assertEqual(fields["total_with_tax"][0].value, "113.00")

    def test_ocr_header_is_not_split_at_scaled_bucket_boundary(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
        header_y = [250, 248, 248, 248, 249, 250, 250, 248]
        values = ["*工业仪表*耐震压力表", "Y60", "个", "1", "86.73", "86.73", "13%", "11.27"]
        units = [
            TextUnit(text=text, page=1, bbox=[index * 100, y, index * 100 + 40, y + 19], order=index + 1, source="ocr")
            for index, (text, y) in enumerate(zip(headers, header_y))
        ]
        units += [
            TextUnit(text=text, page=1, bbox=[index * 100, 290, index * 100 + 40, 309], order=index + 20, source="ocr")
            for index, text in enumerate(values)
        ]
        units += [
            TextUnit(text="合", page=1, bbox=[0, 335, 20, 354], order=40, source="ocr"),
            TextUnit(text="计", page=1, bbox=[20, 333, 40, 354], order=41, source="ocr"),
            TextUnit(text="¥86.73", page=1, bbox=[500, 331, 560, 352], order=42, source="ocr"),
        ]

        text_units = TextUnits(invoice_unit_id="ocr-boundary", source="ocr", units=units)
        items = _extract_ocr_table_items(text_units, self.schema)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["line_amount"], "86.73")
        self.assertEqual(items[0]["line_tax_amount"], "11.27")
        self.assertLess(_ocr_table_layout(text_units, self.schema)[1][1], 335)

    def test_pdf_header_is_not_split_at_scaled_bucket_boundary(self) -> None:
        aliases = {
            "项目名称": "item_name",
            "规格型号": "spec_model",
            "单位": "unit",
            "数量": "quantity",
            "单价": "unit_price",
            "金额": "line_amount",
            "税率/征收率": "tax_rate",
            "税额": "line_tax_amount",
        }
        y_values = [144.816, 144.657, 144.935, 145.312, 145.196, 145.587, 145.075, 144.907]
        spans = [
            {"text": text, "x0": index * 100.0, "x1": index * 100.0 + 40, "y0": y, "y1": y + 10.413, "page": 1}
            for index, (text, y) in enumerate(zip(aliases, y_values))
        ]

        derived = _derive_digital_edges(spans, aliases)

        self.assertIsNotNone(derived)
        edges, header_boundary = derived
        self.assertIn("line_amount", edges)
        self.assertIn("tax_rate", edges)
        self.assertLess(header_boundary[1], 155.3)

    def test_pdf_total_boundary_scales_without_overwriting_last_item(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
        values = ["*服务*测试项目", "A1", "项", "1", "30.00", "30.00", "13%", "3.90"]
        for scale in (0.5, 1, 2, 3):
            with self.subTest(scale=scale):
                spans = [
                    {
                        "text": text,
                        "x0": index * 100 * scale,
                        "x1": (index * 100 + 40) * scale,
                        "y0": 100 * scale,
                        "y1": 110 * scale,
                        "page": 1,
                        "order": index + 1,
                    }
                    for index, text in enumerate(headers)
                ]
                spans += [
                    {
                        "text": text,
                        "x0": index * 100 * scale,
                        "x1": (index * 100 + 40) * scale,
                        "y0": 130 * scale,
                        "y1": 140 * scale,
                        "page": 1,
                        "order": index + 20,
                    }
                    for index, text in enumerate(values)
                ]
                spans += [
                    {"text": "100.00", "x0": 500 * scale, "x1": 550 * scale, "y0": 190 * scale, "y1": 200 * scale, "page": 1, "order": 40},
                    {"text": "13.00", "x0": 700 * scale, "x1": 750 * scale, "y0": 190 * scale, "y1": 200 * scale, "page": 1, "order": 41},
                    {"text": "合计", "x0": 0, "x1": 40 * scale, "y0": 200 * scale, "y1": 210 * scale, "page": 1, "order": 42},
                ]
                text_units = TextUnits(
                    invoice_unit_id="pdf-total-scale",
                    source="pdf_text",
                    source_file="dummy.pdf",
                    page_range=[1],
                    units=[],
                )
                with patch("invoice_ledger.parsing._line_item_ocr_table._read_pdf_spans", return_value=spans):
                    items = _extract_digital_text_table_items(text_units, self.schema)

                self.assertEqual(items[0]["line_amount"], "30.00")
                self.assertEqual(items[0]["line_tax_amount"], "3.90")

    def test_pdf_table_groups_cells_with_small_baseline_offsets(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
        rows = [
            ["*家具*会议桌", "A1", "张", "1", "3238.05", "3238.05", "13%", "420.95"],
            ["*家具*工位桌", "B1", "张", "1", "2412.39", "2412.39", "13%", "313.61"],
            ["*照明装置*平板灯", "C1", "个", "10", "39.15", "391.50", "13%", "50.90"],
        ]
        spans = [
            {"text": text, "x0": index * 100.0, "x1": index * 100.0 + 40, "y0": 100.0, "y1": 110.0, "page": 1, "order": index + 1}
            for index, text in enumerate(headers)
        ]
        order = len(spans) + 1
        for row_index, values in enumerate(rows):
            row_y = 130.0 + row_index * 25
            for column_index, text in enumerate(values):
                y = row_y + (0.15 if column_index == 0 else 0.0)
                spans.append({"text": text, "x0": column_index * 100.0, "x1": column_index * 100.0 + 40, "y0": y, "y1": y + 10, "page": 1, "order": order})
                order += 1
        spans.append({"text": "合计", "x0": 0, "x1": 40, "y0": 210, "y1": 220, "page": 1, "order": order})
        text_units = TextUnits(invoice_unit_id="pdf-offsets", source="pdf_text", source_file="dummy.pdf", page_range=[1], units=[])

        with patch("invoice_ledger.parsing._line_item_ocr_table._read_pdf_spans", return_value=spans):
            items = _extract_digital_text_table_items(text_units, self.schema)

        self.assertEqual([item["line_amount"] for item in items], ["3238.05", "2412.39", "391.50"])
        self.assertEqual([item["line_tax_amount"] for item in items], ["420.95", "313.61", "50.90"])

    def test_ocr_table_groups_cells_with_small_baseline_offsets(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
        rows = [
            ["*家具*会议桌", "A1", "张", "1", "3238.05", "3238.05", "13%", "420.95"],
            ["*家具*工位桌", "B1", "张", "1", "2412.39", "2412.39", "13%", "313.61"],
            ["*照明装置*平板灯", "C1", "个", "10", "39.15", "391.50", "13%", "50.90"],
        ]
        units = [unit(text, index * 100, 100, index + 1) for index, text in enumerate(headers)]
        order = len(units) + 1
        for row_index, values in enumerate(rows):
            row_y = 130 + row_index * 25
            row_units = [
                TextUnit(text=text, page=1, bbox=[column_index * 100, row_y + (0.15 if column_index == 0 else 0), column_index * 100 + 40, row_y + 10], order=order + column_index, source="ocr")
                for column_index, text in enumerate(values)
            ]
            units.extend(sorted(row_units, key=lambda item: item.bbox[1]))
            order += len(values)
        units.append(TextUnit(text="合计", page=1, bbox=[0, 210, 40, 220], order=order, source="ocr"))

        items = _extract_ocr_table_items(TextUnits(invoice_unit_id="ocr-offsets", source="ocr", units=units), self.schema)

        self.assertEqual([item["line_amount"] for item in items], ["3238.05", "2412.39", "391.50"])
        self.assertEqual([item["line_tax_amount"] for item in items], ["420.95", "313.61", "50.90"])

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

    def test_final_page_total_ignores_earlier_equal_sum_pair(self) -> None:
        fields: dict = {}
        text_units = TextUnits(
            invoice_unit_id="adjacent-total",
            source="pdf_text",
            page_range=[1],
            units=[
                TextUnit(text="无关金额 ¥40.00 ¥60.00", page=1, bbox=[10, 100, 300, 110], order=1, source="pdf_text"),
                TextUnit(text="计 ¥90.00", page=1, bbox=[10, 115, 300, 125], order=2, source="pdf_text"),
                TextUnit(text="合 计 ¥10.00", page=1, bbox=[10, 130, 300, 140], order=3, source="pdf_text"),
                TextUnit(text="无关金额 ¥40.00 ¥60.00", page=1, bbox=[10, 145, 300, 155], order=4, source="pdf_text"),
                TextUnit(text="价税合计（小写） ¥100.00", page=1, bbox=[10, 165, 400, 175], order=5, source="pdf_text"),
            ],
        )
        _extract_money_totals([], fields, self.schema, text_units)
        self.assertEqual(fields["amount_total"][0].value, "90.00")
        self.assertEqual(fields["tax_total"][0].value, "10.00")

    def test_total_page_precedes_trailing_remark_page(self) -> None:
        fields: dict = {}
        text_units = TextUnits(
            invoice_unit_id="trailing-remark",
            source="pdf_text",
            page_range=[1, 2],
            units=[
                TextUnit(text="合 计 ¥2177399.09 ¥195965.92", page=1, bbox=[10, 100, 400, 110], order=1, source="pdf_text"),
                TextUnit(text="价税合计（小写） ¥2373365.01", page=1, bbox=[10, 120, 400, 130], order=2, source="pdf_text"),
                TextUnit(text="工程地点：内蒙古自治区", page=2, bbox=[10, 100, 300, 110], order=3, source="pdf_text"),
            ],
        )

        _extract_money_totals([], fields, self.schema, text_units)

        self.assertEqual(fields["amount_total"][0].value, "2177399.09")
        self.assertEqual(fields["tax_total"][0].value, "195965.92")
        self.assertEqual(fields["total_with_tax"][0].value, "2373365.01")

    def test_party_column_geometry_binds_buyer_and_seller(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="party",
            source="ocr",
            units=[
                unit("购买方", 100, 20, 1), unit("销售方", 500, 20, 2),
                unit("甲有限公司", 100, 50, 3), unit("乙有限公司", 500, 50, 4),
                unit("TESTBUYER000000001", 100, 80, 5), unit("TESTSELLER00000001", 500, 80, 6),
            ],
        )
        fields: dict = {}
        _extract_names_and_tax_ids([], fields, self.schema, text_units)
        self.assertEqual(fields["buyer_name"][-1].value, "甲有限公司")
        self.assertEqual(fields["seller_name"][-1].value, "乙有限公司")
        self.assertEqual(fields["buyer_tax_id"][-1].value, "TESTBUYER000000001")
        self.assertEqual(fields["seller_tax_id"][-1].value, "TESTSELLER00000001")

    def test_vertical_party_headers_keep_names_inside_header_height(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="vertical-party",
            source="ocr",
            units=[
                TextUnit(text="购买方信息", page=1, bbox=[28, 154, 46, 238], order=1, source="ocr"),
                TextUnit(text="名称：示例精密机械有限公司", page=1, bbox=[58, 158, 304, 172], order=2, source="ocr"),
                TextUnit(text="销售方信息", page=1, bbox=[504, 154, 521, 238], order=3, source="ocr"),
                TextUnit(text="名称：示例电子科技有限公司", page=1, bbox=[533, 159, 747, 173], order=4, source="ocr"),
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
        self.assertEqual(buyer.value, "示例精密机械有限公司")
        self.assertEqual(seller.value, "示例电子科技有限公司")

    def test_split_vertical_party_headers_bind_names_and_tax_ids(self) -> None:
        units = []
        order = 1
        for role, x in (("购买方信息", 27), ("销售方信息", 313)):
            for offset, character in enumerate(role):
                units.append(TextUnit(text=character, page=1, bbox=[x, 150 + offset * 12, x + 12, 160 + offset * 12], order=order, source="pdf_text"))
                order += 1
        units.extend(
            [
                TextUnit(text="名称：示例市政建设有限公司", page=1, bbox=[74, 158, 210, 172], order=order, source="pdf_text"),
                TextUnit(text="名称：示例道路养护集团有限公司", page=1, bbox=[356, 159, 468, 173], order=order + 1, source="pdf_text"),
                TextUnit(text="TESTBUYER000000001", page=1, bbox=[179, 210, 265, 224], order=order + 2, source="pdf_text"),
                TextUnit(text="TESTSELLER00000001", page=1, bbox=[463, 210, 550, 224], order=order + 3, source="pdf_text"),
                TextUnit(text="规格型号", page=1, bbox=[197, 248, 590, 269], order=order + 4, source="pdf_text"),
            ]
        )
        values = _role_party_values_from_columns(TextUnits(invoice_unit_id="split-party", source="pdf_text", units=units))
        self.assertEqual(values["buyer_name"], "示例市政建设有限公司")
        self.assertEqual(values["seller_name"], "示例道路养护集团有限公司")
        self.assertEqual(values["buyer_tax_id"], "TESTBUYER000000001")
        self.assertEqual(values["seller_tax_id"], "TESTSELLER00000001")

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

    def test_party_columns_do_not_invent_name_when_item_header_is_missing(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="missing-item-header",
            source="ocr",
            units=[
                TextUnit(text="购买方信息", page=1, bbox=[28, 154, 46, 238], order=1, source="ocr"),
                TextUnit(text="销售方信息", page=1, bbox=[504, 154, 521, 238], order=2, source="ocr"),
                TextUnit(text="*信息技术服务*平台维护", page=1, bbox=[58, 280, 300, 295], order=3, source="ocr"),
            ],
        )
        self.assertNotIn("buyer_name", _role_party_values_from_columns(text_units))

    def test_party_columns_stop_names_at_tax_id_without_item_header(self) -> None:
        text_units = TextUnits(
            invoice_unit_id="company-after-tax-id",
            source="ocr",
            units=[
                TextUnit(text="购买方信息", page=1, bbox=[28, 154, 46, 238], order=1, source="ocr"),
                TextUnit(text="销售方信息", page=1, bbox=[504, 154, 521, 238], order=2, source="ocr"),
                TextUnit(text="TESTBUYER000000001", page=1, bbox=[58, 205, 250, 220], order=3, source="ocr"),
                TextUnit(text="TESTSELLER00000001", page=1, bbox=[533, 205, 730, 220], order=4, source="ocr"),
                TextUnit(text="某某供应商有限公司", page=1, bbox=[533, 280, 730, 295], order=5, source="ocr"),
            ],
        )
        self.assertNotIn("seller_name", _role_party_values_from_columns(text_units))

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

    def test_text_sequence_keeps_items_and_marks_review_when_comparison_fails(self) -> None:
        text_units = TextUnits(invoice_unit_id="invalid-sequence", source="pdf_text", units=[unit("*服务", 0, 10, 1)])
        fields: dict = {"amount_total": [FieldCandidate(value="30.00", source="test", confidence=1, evidence="total")]}

        def add_invalid_sequence(_lines, target, _schema) -> None:
            target["items"] = [FieldCandidate(value=json.dumps({"line_no": 1, "line_amount": "错误金额"}), source="test", confidence=0.8, evidence="sequence")]

        with (
            patch("invoice_ledger.parsing._line_item_sequence._extract_items", side_effect=add_invalid_sequence),
            patch("invoice_ledger.parsing._line_item_sequence._extract_digital_text_table_items", return_value=[{"item_name": "*服务", "line_amount": "30.00"}]),
        ):
            _extract_items_from_text_units(text_units, fields, self.schema)

        self.assertEqual(json.loads(fields["items"][0].value)["line_amount"], "错误金额")
        self.assertIn("item_amount_comparison_failed", fields["items"][0].risk)

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

    def test_ocr_table_star_tax_keeps_visual_unit(self) -> None:
        headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"]
        values = ["*电信服务*通信服务费", "", "项", "1", "100", "100.00", "*", "*"]
        units = [unit(text, index * 100, 10, index + 1) for index, text in enumerate(headers)]
        units.extend(unit(text, index * 100, 40, index + 20) for index, text in enumerate(values) if text)
        units.append(unit("合计", 0, 70, 50))
        items = _extract_ocr_table_items(TextUnits(invoice_unit_id="star-tax-table", source="ocr", units=units), self.schema)
        self.assertEqual(items[0]["unit"], "项")
        self.assertEqual((items[0]["tax_rate"], items[0]["line_tax_amount"]), ("*", "0.00"))


if __name__ == "__main__":
    unittest.main()
