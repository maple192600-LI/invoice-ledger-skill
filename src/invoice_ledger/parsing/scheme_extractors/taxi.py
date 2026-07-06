"""出租车发票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _date_candidates, _first_regex, _joined, _money_hits, _number_after_label, _schema_output_default
from .._line_items import _add_json_item
from .._parties import _complete_taxi_seller_name
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    invoice_type = _schema_output_default(schema, "invoice_type", "出租车机打发票")
    _add(fields, "invoice_type", invoice_type, "taxi invoice title", 0.94)

    invoice_code = _number_after_label(lines, "发票代码", r"\d{10,12}")
    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    if invoice_code:
        _add(fields, "invoice_code", invoice_code, "taxi invoice code", 0.94)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "taxi invoice number", 0.94)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.95)

    seller_tax_id = _first_regex(r"(?<!\d)(\d{15})(?!\d)", text)
    if seller_tax_id:
        _add(fields, "seller_tax_id", seller_tax_id, "taxi seller tax id", 0.9)

    company_parts = [line.strip() for line in lines if "出租汽车" in line or line.strip() == "公司"]
    seller_name = "".join(company_parts).replace("有限公公司", "有限公司") if company_parts else None
    seller_name = _complete_taxi_seller_name(seller_name, lines, schema)
    if seller_name:
        _add(fields, "seller_name", seller_name, "taxi company", 0.88)

    hits = _money_hits(text_units)
    total = max(hits, key=lambda hit: hit[0])[1] if hits else None
    _add_non_tax_totals(fields, total, "taxi invoice total", 0.92)
    if total:
        _add_json_item(
            fields,
            {
                "item_name": "出租车费",
                "spec_model": None,
                "unit": "次",
                "quantity": "1",
                "unit_price": total,
                "line_amount": total,
                "tax_rate": None,
                "line_tax_amount": "0.00",
                "line_total_with_tax": total,
            },
            1,
            "taxi fare item",
            0.9,
        )

