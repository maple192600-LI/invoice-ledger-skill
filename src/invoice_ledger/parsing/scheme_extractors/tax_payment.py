"""完税证明字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _center_y, _date_candidates, _first_regex, _has_chinese, _joined, _money_hits, _nested_section, _required_float, _required_int, _required_terms, _value_after_label, _x0
from .._line_items import _add_json_item, _extract_simple_receipt_items
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    _add(fields, "invoice_type", "税收完税证明", "tax payment title", 0.98)

    invoice_no = _first_regex(r"([（(]\d{4,5}[）)]\s*[\u4e00-\u9fff]国证\s*\d{6,12})", text)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no.replace(" ", ""), "tax payment certificate number", 0.94)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.95)

    seller_name = _value_after_label(lines, ["税务机关"])
    if seller_name:
        _add(fields, "seller_name", seller_name, "tax authority", 0.9)

    buyer_tax_id = _first_regex(r"(?<!\d)(\d{15,20})(?!\d)", text)
    if buyer_tax_id:
        _add(fields, "buyer_tax_id", buyer_tax_id, "taxpayer id", 0.9)

    buyer_name = _value_after_label(lines, ["纳税人名称"])
    if not buyer_name:
        name_layout = _nested_section(schema, "layout_rules", "taxpayer_name")
        for unit in sorted(text_units.units, key=lambda item: (_center_y(item), _x0(item))):
            candidate = unit.text.strip()
            if (
                _required_float(name_layout, "min_y")
                <= _center_y(unit)
                <= _required_float(name_layout, "max_y")
                and _required_float(name_layout, "min_x")
                <= _x0(unit)
                <= _required_float(name_layout, "max_x")
                and _required_int(name_layout, "min_len")
                < len(candidate)
                <= _required_int(name_layout, "max_len")
                and _has_chinese(candidate)
            ):
                buyer_name = candidate
                break
    if buyer_name:
        _add(fields, "buyer_name", buyer_name, "taxpayer name", 0.88)

    hits = _money_hits(text_units)
    total = max(hits, key=lambda hit: hit[0])[1] if hits else None
    _add_non_tax_totals(fields, total, "tax payment total", 0.93)

    item_layout = _nested_section(schema, "layout_rules", "simple_items")
    items = _extract_simple_receipt_items(
        text_units,
        min_y=_required_float(item_layout, "min_y"),
        max_y=_required_float(item_layout, "max_y"),
        max_name_x=_required_float(item_layout, "max_name_x"),
        exclude_terms=_required_terms(item_layout, "exclude_terms"),
    )
    for index, item in enumerate(items, start=1):
        _add_json_item(fields, item, index, f"tax payment item {index}", 0.88)

