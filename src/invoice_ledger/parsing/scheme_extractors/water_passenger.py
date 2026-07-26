"""水路客运客票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _first_regex, _joined, _money_after_labels, _number_after_label
from .._invoice_fields import _date_after_labels
from .._line_items import _add_json_item
from .._parties import _company_after_labels
from .._totals import _add_non_tax_totals


def extract(
    _text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    _schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    invoice_type = next(
        (
            line.strip()
            for line in lines
            if any(term in line for term in ["水路旅客运输客票", "水路客运", "船票"])
        ),
        "水路旅客运输客票",
    )
    _add(fields, "invoice_type", invoice_type, "water passenger ticket title", 0.96)

    invoice_code = _number_after_label(lines, "发票代码", r"\d{10,12}")
    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    if invoice_code:
        _add(fields, "invoice_code", invoice_code, "water passenger invoice code", 0.9)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "water passenger invoice number", 0.95)

    invoice_date = _date_after_labels(lines, ["开票日期", "乘船日期", "乘船时间"])
    if invoice_date:
        _add(fields, "invoice_date", invoice_date, "water passenger ticket date", 0.95)

    seller_name = _company_after_labels(lines, ["承运人", "收款单位", "开票单位"])
    if seller_name:
        _add(fields, "seller_name", seller_name, "water passenger seller", 0.9)
    seller_tax_id = _first_regex(r"(?:统一社会信用代码|纳税人识别号)\s*[:：]?\s*([0-9A-Z]{15,20})", text)
    if seller_tax_id:
        _add(fields, "seller_tax_id", seller_tax_id, "water passenger seller tax id", 0.9)

    total = _money_after_labels(lines, ["票价", "金额"])
    _add_non_tax_totals(fields, total, "water passenger ticket fare", 0.92)
    if total:
        _add_json_item(
            fields,
            {
                "item_name": "水路客票",
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
            "water passenger ticket fare item",
            0.9,
        )

