"""通用机打发票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _date_candidates, _first_regex, _joined, _money_values, _nested_section, _number_after_label, _required_float, _value_after_label, _y0
from .._line_items import _add_json_item, _extract_machine_invoice_item
from .._parties import _company_name_candidates
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    invoice_type = next((line.strip() for line in lines[:8] if "通用机打发票" in line), "通用机打发票")
    _add(fields, "invoice_type", invoice_type, "machine invoice title", 0.95)

    invoice_code = _number_after_label(lines, "发票代码", r"\d{10,12}")
    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    if invoice_code:
        _add(fields, "invoice_code", invoice_code, "machine invoice code", 0.95)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "machine invoice number", 0.95)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.95)

    buyer_name = _value_after_label(lines, ["顾客名称"])
    if not buyer_name:
        buyer_name = next((name for name in _company_name_candidates(lines) if "开票单位" not in name), None)
    if buyer_name:
        _add(fields, "buyer_name", buyer_name, "machine invoice buyer", 0.9)

    seller_name = _value_after_label(lines, ["开票单位（盖章）", "开票单位"])
    if not seller_name:
        companies = _company_name_candidates(lines)
        seller_name = companies[-1] if companies else None
    if seller_name:
        _add(fields, "seller_name", seller_name, "machine invoice seller", 0.9)

    seller_tax_id = _first_regex(r"销货方识别号\s*[:：]?\s*([0-9A-Z]{15,20})", text)
    buyer_tax_id = _first_regex(r"备注\s*[:：]?\s*([0-9A-Z]{15,20})", text)
    if seller_tax_id:
        _add(fields, "seller_tax_id", seller_tax_id, "machine invoice seller tax id", 0.92)
    if buyer_tax_id:
        _add(fields, "buyer_tax_id", buyer_tax_id, "machine invoice buyer tax id", 0.9)

    total = None
    for line in lines:
        if "小写" in line or "合计" in line:
            values = _money_values(line)
            if values:
                total = values[-1]
    _add_non_tax_totals(fields, total, "machine invoice total", 0.94)

    item_layout = _nested_section(schema, "layout_rules", "item_row")
    total_y = min(
        (_y0(unit) for unit in text_units.units if "合计" in unit.text or "小写" in unit.text),
        default=_required_float(item_layout, "total_fallback_y"),
    )
    item = _extract_machine_invoice_item(
        text_units,
        total_y,
        item_layout,
    )
    if item:
        _add_json_item(fields, item, 1, "machine invoice item", 0.9)

