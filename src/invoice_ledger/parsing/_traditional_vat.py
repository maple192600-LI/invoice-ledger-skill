"""传统增值税专用发票的专用规则（表头、合计、明细组）。"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    FieldCandidate,
    TextUnits,
)

from ._helpers import _add, _combined_text, _first_regex_in_units, _layout_section, _money_values_in_units, _normalize_spaced_date, _units_in_box
from ._line_items import _add_json_item, _traditional_vat_items
from ._parties import _company_name_in_units, _tax_id_in_units


def _add_traditional_header_and_parties(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    header = _layout_section(schema, "header")
    for field_name in ["invoice_code", "invoice_no"]:
        box = header.get(field_name, {})
        if not isinstance(box, dict):
            continue
        hit = _first_regex_in_units(_units_in_box(text_units, box), str(box.get("pattern", r"\d+")))
        if hit:
            _add(fields, field_name, hit[0], hit[1], 0.97)

    date_units = _units_in_box(text_units, header.get("invoice_date", {}))
    for unit in date_units:
        value = _normalize_spaced_date(unit.text)
        if value:
            _add(fields, "invoice_date", value, unit.text.strip(), 0.97)
            break
    if "invoice_date" not in fields:
        value = _normalize_spaced_date(_combined_text(date_units))
        if value:
            _add(fields, "invoice_date", value, _combined_text(date_units), 0.97)

    parties = _layout_section(schema, "parties")
    for field_name in ["buyer_name", "seller_name"]:
        box = parties.get(field_name, {})
        if isinstance(box, dict):
            value = _company_name_in_units(_units_in_box(text_units, box))
            _add(fields, field_name, value, f"traditional layout {field_name}", 0.96)
    for field_name in ["buyer_tax_id", "seller_tax_id"]:
        box = parties.get(field_name, {})
        if isinstance(box, dict):
            value = _tax_id_in_units(_units_in_box(text_units, box))
            _add(fields, field_name, value, f"traditional layout {field_name}", 0.96)


def _add_traditional_totals(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    totals = _layout_section(schema, "totals")
    amount_tax_values = _money_values_in_units(_units_in_box(text_units, totals.get("amount_tax", {})))
    if len(amount_tax_values) >= 1:
        _add(fields, "amount_total", amount_tax_values[0][0], amount_tax_values[0][1], 0.96)
    if len(amount_tax_values) >= 2:
        _add(fields, "tax_total", amount_tax_values[1][0], amount_tax_values[1][1], 0.96)

    total_values = _money_values_in_units(_units_in_box(text_units, totals.get("total_with_tax", {})))
    if total_values:
        _add(fields, "total_with_tax", total_values[-1][0], total_values[-1][1], 0.96)


def _extract_traditional_vat_candidates(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> bool:
    if not _layout_section(schema):
        return False
    _add_traditional_header_and_parties(text_units, fields, schema)
    _add_traditional_totals(text_units, fields, schema)
    items = _traditional_vat_items(text_units, schema)
    for index, item in enumerate(items, start=1):
        _add_json_item(fields, item, index, f"traditional layout line item {index}", 0.92)
    return bool(items)

