"""医疗收费收据字段抽取。"""

from __future__ import annotations

import re
from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _date_candidates, _money_values, _nested_section, _required_float, _required_terms, _value_after_label, _y0
from .._line_items import _add_json_item, _extract_simple_receipt_items
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    invoice_type = next((line for line in lines[:8] if "收费票据" in line), "医疗收费票据")
    invoice_type = invoice_type.replace("医疗诊收费票据", "医疗门诊收费票据")
    _add(fields, "invoice_type", invoice_type, invoice_type, 0.97)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.96)

    seller_name = _value_after_label(lines, ["收款单位（章）", "收款单位"])
    if seller_name:
        seller_name = re.split(r"复核人|收款人", seller_name)[0].strip()
        _add(fields, "seller_name", seller_name, "medical receipt seller", 0.9)

    total = None
    for line in lines:
        if "小写" in line:
            values = _money_values(line)
            if values:
                total = values[-1]
    _add_non_tax_totals(fields, total, "medical receipt total", 0.93)

    item_layout = _nested_section(schema, "layout_rules", "simple_items")
    total_y = min(
        (_y0(unit) for unit in text_units.units if "小写" in unit.text),
        default=_required_float(item_layout, "total_fallback_y"),
    )
    items = _extract_simple_receipt_items(
        text_units,
        min_y=_required_float(item_layout, "min_y"),
        max_y=total_y - _required_float(item_layout, "max_y_before_total"),
        max_name_x=_required_float(item_layout, "max_name_x"),
        exclude_terms=_required_terms(item_layout, "exclude_terms"),
    )
    for index, item in enumerate(items, start=1):
        _add_json_item(fields, item, index, f"medical receipt item {index}")

