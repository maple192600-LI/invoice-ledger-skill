"""Layout, OCR table, and row-neighborhood parsing helpers."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any

from ..contracts import TextUnit, TextUnits, normalize_amount
from ._helper_fields import _required_float
from ._helper_primitives import (
    TAX_RATE_RE,
    _bbox,
    _center_y,
    _clean_money,
    _decimal_scale,
    _is_money_text,
    _is_number_text,
    _is_total_marker,
    _join_item_parts,
    _last_money,
    _last_number,
    _last_rate,
    _looks_like_spec,
    _money_values,
    _safe_decimal,
    _split_embedded_spec,
    _split_two_numbers,
    _unit_from_parts,
    _x0,
    _y0,
)


def _layout_section(schema: dict[str, Any], *keys: str) -> dict[str, Any]:
    config: Any = schema.get("traditional_vat_layout", {})
    for key in keys:
        if not isinstance(config, dict):
            return {}
        config = config.get(key, {})
    return config if isinstance(config, dict) else {}


def _unit_in_box(unit: TextUnit, box: dict[str, Any]) -> bool:
    x = _x0(unit)
    y = _center_y(unit)
    min_x = float(box.get("min_x", float("-inf")))
    max_x = float(box.get("max_x", float("inf")))
    min_y = float(box.get("min_y", float("-inf")))
    max_y = float(box.get("max_y", float("inf")))
    return min_x <= x <= max_x and min_y <= y <= max_y


def _units_in_box(text_units: TextUnits, box: dict[str, Any]) -> list[TextUnit]:
    return [
        unit
        for unit in text_units.units
        if unit.text.strip() and _unit_in_box(unit, box)
    ]


def _first_regex_in_units(units: list[TextUnit], pattern: str) -> tuple[str, str] | None:
    compiled = re.compile(pattern)
    for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item), item.order)):
        match = compiled.search(unit.text.strip())
        if match:
            return match.group(0), unit.text.strip()
    return None


def _combined_text(units: list[TextUnit]) -> str:
    return " ".join(
        unit.text.strip()
        for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item), item.order))
        if unit.text.strip()
    )


def _normalize_spaced_date(text: str) -> str | None:
    match = re.search(r"(\d{4})(?:\s*[年/-]\s*|\s+)(\d{1,2})(?:\s*[月/-]\s*|\s+)(\d{1,2})\s*(?:日)?", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _has_bbox(unit: TextUnit) -> bool:
    return len(unit.bbox) == 4 and any(float(value) != 0.0 for value in unit.bbox)


def _units_with_geometry(text_units: TextUnits) -> list[TextUnit]:
    return [unit for unit in text_units.units if unit.text.strip() and _has_bbox(unit)]


def _page_width(units: list[TextUnit]) -> float | None:
    max_x = max((_bbox(unit)[2] for unit in units), default=0.0)
    return max_x or None


def _unit_in_y_band(unit: TextUnit, band: dict[str, Any]) -> bool:
    y = _center_y(unit)
    return float(band["min_y"]) <= y <= float(band["max_y"])


def _money_values_in_units(units: list[TextUnit]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item), item.order)):
        for value in _money_values(unit.text):
            values.append((value, unit.text.strip()))
    return values


def _ocr_column_for_x(x_position: float, table_config: dict[str, Any]) -> str:
    right_edges = table_config.get("column_right_edges", {})
    if not isinstance(right_edges, dict):
        right_edges = {}
    for column_name, right_edge in right_edges.items():
        if x_position < float(right_edge):
            return str(column_name)
    return str(table_config.get("last_column", "line_tax_amount"))


def _ocr_table_item(
    units: list[TextUnit],
    table_config: dict[str, Any],
    textual_spec_tokens: set[str],
) -> dict[str, Any] | None:
    if not units:
        return None

    name_parts: list[str] = []
    extra_name_parts: list[str] = []
    spec_parts: list[str] = []
    unit_parts: list[str] = []
    quantity_parts: list[str] = []
    unit_price_parts: list[str] = []
    amount_parts: list[str] = []
    rate_parts: list[str] = []
    tax_parts: list[str] = []

    for unit in sorted(units, key=lambda item: (_y0(item), _x0(item))):
        text = unit.text.strip()
        if not text or _is_total_marker(text):
            continue
        column_name = _ocr_column_for_x(_x0(unit), table_config)
        if column_name == "item_name":
            if text.startswith("*") or not (_is_number_text(text) or _is_money_text(text)):
                name_parts.append(text)
        elif column_name == "spec_model":
            if _looks_like_spec(text) or text in textual_spec_tokens:
                spec_parts.append(text)
            elif not _is_number_text(text) and not _is_money_text(text):
                extra_name_parts.append(text)
        elif column_name == "unit":
            if not _is_number_text(text) and not _is_money_text(text) and not TAX_RATE_RE.fullmatch(text):
                unit_parts.append(text)
        elif column_name == "quantity":
            quantity_parts.append(text)
        elif column_name == "unit_price":
            unit_price_parts.append(text)
        elif column_name == "line_amount":
            amount_parts.append(text)
        elif column_name == "tax_rate":
            rate_parts.append(text)
        else:
            tax_parts.append(text)

    item_name = _join_item_parts(name_parts + extra_name_parts)
    spec_model = _join_item_parts(spec_parts)
    item_name, spec_model = _split_embedded_spec(item_name, spec_model)
    amount = _last_money(amount_parts)
    tax_amount = _last_money(tax_parts)
    tax_rate = _last_rate(rate_parts)
    if not item_name or not amount or not tax_amount or not tax_rate:
        return None
    quantity = _last_number(quantity_parts)
    unit_price = _last_number(unit_price_parts)
    if quantity is None or unit_price is None:
        for part in quantity_parts + unit_price_parts:
            left, right = _split_two_numbers(part)
            if left and right:
                quantity = quantity or left
                unit_price = unit_price or right
                break
    if unit_price is None and quantity is not None and amount is not None:
        possible_unit_price = _safe_decimal(quantity)
        amount_value = normalize_amount(amount)
        if (
            possible_unit_price is not None
            and amount_value is not None
            and normalize_amount(possible_unit_price) == amount_value
            and _decimal_scale(quantity) > 2
        ):
            unit_price = quantity
            quantity = "1"
    total = _clean_money(str(Decimal(amount) + Decimal(tax_amount)))
    return {
        "item_name": item_name,
        "spec_model": spec_model,
        "unit": _unit_from_parts(unit_parts),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_amount": amount,
        "tax_rate": tax_rate,
        "line_tax_amount": tax_amount,
        "line_total_with_tax": total,
    }


def _normalize_ocr_item(item: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if item.get("unit"):
        item["unit"] = str(item["unit"]).replace("(", "（").replace(")", "）")
    return item


def _same_row(units: list[TextUnit], reference: TextUnit, tolerance: float = 20.0) -> list[TextUnit]:
    reference_y = _center_y(reference)
    return [unit for unit in units if abs(_center_y(unit) - reference_y) <= tolerance]


def _nearest_money_after_name(row_units: list[TextUnit], name_unit: TextUnit, min_delta: float = 45.0) -> tuple[str, TextUnit] | None:
    candidates: list[tuple[float, str, TextUnit]] = []
    for unit in row_units:
        if _x0(unit) <= _x0(name_unit) + min_delta:
            continue
        values = _money_values(unit.text)
        if values:
            candidates.append((_x0(unit), values[-1], unit))
    if not candidates:
        return None
    _, value, unit = sorted(candidates, key=lambda item: item[0])[0]
    return value, unit


def _money_hits(text_units: TextUnits) -> list[tuple[Decimal, str, TextUnit]]:
    hits: list[tuple[Decimal, str, TextUnit]] = []
    for unit in text_units.units:
        for value in _money_values(unit.text):
            decimal_value = _safe_decimal(value)
            if decimal_value is not None:
                hits.append((decimal_value, value, unit))
    return hits


def _text_in_layout(text_units: TextUnits, layout: dict[str, Any], pattern: str) -> str | None:
    compiled = re.compile(pattern)
    for unit in sorted(text_units.units, key=lambda item: (_center_y(item), _x0(item))):
        if not (
            _required_float(layout, "min_y")
            <= _center_y(unit)
            <= _required_float(layout, "max_y")
            and _required_float(layout, "min_x")
            <= _x0(unit)
            <= _required_float(layout, "max_x")
        ):
            continue
        match = compiled.search(unit.text.strip())
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None


def _units_in_layout(text_units: TextUnits, layout: dict[str, Any]) -> list[TextUnit]:
    if not layout:
        return []
    return [
        unit
        for unit in sorted(text_units.units, key=lambda item: (_center_y(item), _x0(item)))
        if _required_float(layout, "min_y")
        <= _center_y(unit)
        <= _required_float(layout, "max_y")
        and _required_float(layout, "min_x")
        <= _x0(unit)
        <= _required_float(layout, "max_x")
    ]
