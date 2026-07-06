"""航空运输电子客票行程单字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _center_y, _cfg_terms, _date_candidates, _first_regex, _has_chinese, _joined, _money_hits, _nested_section, _required_float, _required_int, _x0
from .._line_items import _add_json_item
from .._totals import _add_non_tax_totals


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    _add(fields, "invoice_type", "航空运输电子客票行程单", "air ticket title", 0.98)

    ticket_no = _first_regex(r"(?<!\d)(\d{13})(?!\d)", text)
    if ticket_no:
        _add(fields, "invoice_no", ticket_no, "air ticket number", 0.96)

    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[-1][0], dates[-1][1], 0.94)

    passenger_layout = _nested_section(schema, "layout_rules", "passenger_name")
    exclude_terms = _cfg_terms(passenger_layout, "exclude_terms", ["旅客"])
    for unit in sorted(text_units.units, key=lambda item: (_center_y(item), _x0(item))):
        cleaned = unit.text.strip()
        if (
            _has_chinese(cleaned)
            and _required_int(passenger_layout, "min_len")
            < len(cleaned)
            <= _required_int(passenger_layout, "max_len")
            and _x0(unit) < _required_float(passenger_layout, "max_x")
            and _center_y(unit) < _required_float(passenger_layout, "max_y")
            and not any(term in cleaned for term in exclude_terms)
        ):
            _add(fields, "buyer_name", cleaned, "air passenger name", 0.9)
            break

    seller_name = next(
        (
            line.strip()
            for line in lines
            if line.strip().endswith("航空") and "航空运输" not in line and len(line.strip()) <= 8
        ),
        None,
    )
    if seller_name:
        _add(fields, "seller_name", seller_name, "air carrier", 0.9)

    hits = _money_hits(text_units)
    if not hits:
        return
    total_decimal, total, _ = max(hits, key=lambda hit: hit[0])
    _add_non_tax_totals(fields, total, "air ticket total", 0.92)

    fare = next((value for amount, value, unit in hits if "CNY" in unit.text and amount != total_decimal), None)
    fuel = next((value for _amount, value, unit in hits if "YQ" in unit.text), None)
    airport_tax = next(
        (
            value
            for amount, value, unit in hits
            if amount != total_decimal and value not in {fare, fuel} and "CNY" not in unit.text
        ),
        None,
    )
    components = [
        ("票价", fare),
        ("燃油附加费", fuel),
        ("机场建设费", airport_tax),
    ]
    for index, (name, amount) in enumerate([(name, amount) for name, amount in components if amount], start=1):
        _add_json_item(
            fields,
            {
                "item_name": name,
                "spec_model": None,
                "unit": None,
                "quantity": "1",
                "unit_price": amount,
                "line_amount": amount,
                "tax_rate": None,
                "line_tax_amount": "0.00",
                "line_total_with_tax": amount,
            },
            index,
            f"air ticket component {index}",
        )

