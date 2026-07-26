"""公路客运客票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _center_y, _date_candidates, _has_chinese, _money_values, _nested_section, _number_after_label, _required_float, _text_in_layout, _x0
from .._line_items import _add_json_item
from .._parties import _road_bus_stamp_seller_name
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    invoice_type = next((line.strip() for line in lines[:8] if "公路汽车客票" in line), "公路汽车客票")
    _add(fields, "invoice_type", invoice_type, "road bus ticket title", 0.96)

    invoice_code = _number_after_label(lines, "发票代码", r"\d{10,12}")
    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    if invoice_code:
        _add(fields, "invoice_code", invoice_code, "road bus invoice code", 0.94)
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "road bus invoice number", 0.94)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.95)

    seller_tax_layout = _nested_section(schema, "layout_rules", "seller_tax_id")
    tax_id = _text_in_layout(
        text_units,
        seller_tax_layout,
        r"(?<![0-9A-Z])([0-9A-Z]{18})(?![0-9A-Z])",
    )
    if tax_id:
        _add(fields, "seller_tax_id", tax_id, "road bus seller tax id", 0.9)

    units = [unit for unit in text_units.units if unit.text.strip()]
    origin_layout = _nested_section(schema, "layout_rules", "origin")
    destination_layout = _nested_section(schema, "layout_rules", "destination")
    origin = next(
        (
            unit.text.strip()
            for unit in units
            if _required_float(origin_layout, "min_y")
            <= _center_y(unit)
            <= _required_float(origin_layout, "max_y")
            and _required_float(origin_layout, "min_x")
            <= _x0(unit)
            <= _required_float(origin_layout, "max_x")
            and _has_chinese(unit.text)
        ),
        None,
    )
    seller_name = _road_bus_stamp_seller_name(text_units, schema, origin)
    if seller_name:
        _add(fields, "seller_name", seller_name, "road bus stamp seller", 0.93)

    amount_unit = next((unit for unit in units if _money_values(unit.text)), None)
    total = _money_values(amount_unit.text)[-1] if amount_unit else None
    _add_non_tax_totals(fields, total, "road bus ticket fare", 0.92)

    destination = next(
        (
            unit.text.strip()
            for unit in units
            if _required_float(destination_layout, "min_y")
            <= _center_y(unit)
            <= _required_float(destination_layout, "max_y")
            and _x0(unit) >= _required_float(destination_layout, "min_x")
            and _has_chinese(unit.text)
        ),
        None,
    )
    item_name = f"{origin}至{destination}客票" if origin and destination else "公路汽车客票"
    if total:
        _add_json_item(
            fields,
            {
                "item_name": item_name,
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
            "road bus ticket fare item",
            0.9,
        )

