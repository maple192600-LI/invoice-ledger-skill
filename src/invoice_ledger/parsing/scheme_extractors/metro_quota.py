"""地铁定额发票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _first_regex, _joined, _number_after_label
from .._line_items import _add_json_item
from .._totals import _add_non_tax_totals, _amount_from_chinese_yuan


def extract(
    _text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    _schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    invoice_type = next((line.strip() for line in lines if "定额发票" in line), "地铁定额发票")
    seller_name = invoice_type.replace("定额发票", "")
    _add(fields, "invoice_type", invoice_type, "metro quota invoice title", 0.96)
    _add(fields, "seller_name", seller_name, "metro quota invoice seller", 0.92)

    invoice_code = _number_after_label(lines, "发票代码", r"\d{10,12}")
    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    seller_tax_id = _first_regex(r"统一社会信用代码\s*[:：]?\s*([0-9A-Z]{15,20})", text)
    if invoice_code:
        _add(fields, "invoice_code", invoice_code, "metro quota invoice code", 0.94)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "metro quota invoice number", 0.94)
    if seller_tax_id:
        _add(fields, "seller_tax_id", seller_tax_id, "metro seller tax id", 0.9)

    total = next((_amount_from_chinese_yuan(line) for line in lines if _amount_from_chinese_yuan(line)), None)
    _add_non_tax_totals(fields, total, "metro quota invoice amount", 0.9)
    if total:
        _add_json_item(
            fields,
            {
                "item_name": "地铁定额票",
                "spec_model": None,
                "unit": "张",
                "quantity": "1",
                "unit_price": total,
                "line_amount": total,
                "tax_rate": None,
                "line_tax_amount": "0.00",
                "line_total_with_tax": total,
            },
            1,
            "metro quota invoice item",
            0.9,
        )

