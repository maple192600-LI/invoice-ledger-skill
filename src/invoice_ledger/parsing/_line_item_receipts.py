"""Receipt and machine-invoice line item extraction."""

from __future__ import annotations

import json
from typing import Any

from ..contracts import FieldCandidate, TextUnit, TextUnits
from ._helpers import (
    _add,
    _center_y,
    _compact_number,
    _has_chinese,
    _is_number_text,
    _money_values,
    _nearest_money_after_name,
    _required_float,
    _required_int,
    _required_terms,
    _same_row,
    _x0,
)


def _add_json_item(
    fields: dict[str, list[FieldCandidate]],
    item: dict[str, Any],
    line_no: int,
    evidence: str,
    confidence: float = 0.88,
) -> None:
    payload = {**item, "line_no": line_no}
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _add(fields, "items", value, evidence, confidence)


def _quantity_between(row_units: list[TextUnit], name_unit: TextUnit, amount_unit: TextUnit) -> str | None:
    for unit in sorted(row_units, key=_x0):
        if _x0(name_unit) < _x0(unit) < _x0(amount_unit) and _is_number_text(unit.text):
            return str(_compact_number(unit.text))
    return None


def _extract_simple_receipt_items(
    text_units: TextUnits,
    *,
    min_y: float,
    max_y: float,
    max_name_x: float,
    exclude_terms: list[str],
) -> list[dict[str, Any]]:
    units = [unit for unit in text_units.units if unit.text.strip()]
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item))):
        text = unit.text.strip()
        if not (min_y <= _center_y(unit) <= max_y):
            continue
        if _x0(unit) > max_name_x or not _has_chinese(text) or _money_values(text):
            continue
        if any(term in text for term in exclude_terms):
            continue
        if len(text) > 16:
            continue
        row_units = _same_row(units, unit)
        money_pair = _nearest_money_after_name(row_units, unit)
        if not money_pair:
            continue
        amount, amount_unit = money_pair
        key = (text, amount)
        if key in seen:
            continue
        seen.add(key)
        quantity = _quantity_between(row_units, unit, amount_unit)
        items.append(
            {
                "item_name": text,
                "spec_model": None,
                "unit": None,
                "quantity": quantity,
                "unit_price": None,
                "line_amount": amount,
                "tax_rate": None,
                "line_tax_amount": "0.00",
                "line_total_with_tax": amount,
            }
        )
    return items


def _extract_machine_invoice_item(
    text_units: TextUnits,
    total_y: float,
    layout: dict[str, Any],
) -> dict[str, Any] | None:
    units = [unit for unit in text_units.units if unit.text.strip()]
    exclude = ["发票", "顾客", "销货", "校验", "合计", "开票", "品名", "规格", "单位", "数量", "单价", "金额"]
    common_units = set(_required_terms(layout, "unit_tokens"))
    min_y = _required_float(layout, "min_y")
    max_y_before_total = _required_float(layout, "max_y_before_total")
    max_item_x = _required_float(layout, "max_item_x")
    max_item_len = _required_int(layout, "max_item_len")
    min_amount_delta_x = _required_float(layout, "min_amount_delta_x")
    unit_min_x = _required_float(layout, "unit_min_x")
    unit_max_x = _required_float(layout, "unit_max_x")
    max_unit_len = _required_int(layout, "max_unit_len")
    for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item))):
        item_name = unit.text.strip()
        if not (min_y <= _center_y(unit) <= total_y - max_y_before_total):
            continue
        if _x0(unit) > max_item_x or not _has_chinese(item_name) or len(item_name) > max_item_len:
            continue
        if any(term in item_name for term in exclude):
            continue
        row_units = _same_row(units, unit)
        money_units = [
            (value, money_unit)
            for money_unit in row_units
            for value in _money_values(money_unit.text)
            if _x0(money_unit) > _x0(unit) + min_amount_delta_x
        ]
        if not money_units:
            continue
        line_amount, amount_unit = sorted(money_units, key=lambda pair: _x0(pair[1]))[-1]
        quantity_unit = next(
            (
                candidate
                for candidate in sorted(row_units, key=_x0)
                if _x0(unit) < _x0(candidate) < _x0(amount_unit) and _is_number_text(candidate.text)
            ),
            None,
        )
        quantity = str(_compact_number(quantity_unit.text)) if quantity_unit else None
        unit_candidates = [
            candidate
            for candidate in sorted(row_units, key=_x0)
            if (
                unit_min_x <= _x0(candidate) <= unit_max_x
                and (quantity_unit is None or _x0(candidate) < _x0(quantity_unit))
                and _has_chinese(candidate.text.strip())
                and len(candidate.text.strip()) <= max_unit_len
            )
        ]
        unit_text = next((candidate.text.strip() for candidate in unit_candidates if candidate.text.strip() in common_units), None)
        if unit_text is None and unit_candidates:
            anchor_x = _x0(quantity_unit) if quantity_unit else 780.0
            unit_text = sorted(unit_candidates, key=lambda candidate: abs(_x0(candidate) - anchor_x))[0].text.strip()
        unit_price = next(
            (
                value
                for value, money_unit in sorted(money_units, key=lambda pair: _x0(pair[1]))
                if _x0(money_unit) < _x0(amount_unit)
            ),
            None,
        )
        return {
            "item_name": item_name,
            "spec_model": None,
            "unit": unit_text,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
            "tax_rate": None,
            "line_tax_amount": "0.00",
            "line_total_with_tax": line_amount,
        }
    return None
