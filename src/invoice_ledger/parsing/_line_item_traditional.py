"""Traditional VAT line item extraction."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any

from ..contracts import TextUnit, TextUnits
from ._helpers import TAX_RATE_RE, _clean_money, _has_chinese, _is_money_text, _is_number_text, _is_total_marker, _layout_section, _looks_like_spec, _units_in_box
from ._line_item_sequence_helpers import _is_common_unit_token, _unit_tokens


def _remove_spec_from_name(item_name: str, spec_model: str | None) -> str:
    if not spec_model:
        return item_name
    pattern = re.compile(re.escape(spec_model), re.IGNORECASE)
    return pattern.sub("", item_name).strip()


def _traditional_item_from_group(
    group: list[TextUnit],
    unit_tokens: set[str],
) -> dict[str, Any] | None:
    texts = [unit.text.strip() for unit in sorted(group, key=lambda unit: unit.order) if unit.text.strip()]
    if not texts or not texts[0].startswith("*"):
        return None
    rate_index = next((index for index, text in enumerate(texts) if TAX_RATE_RE.fullmatch(text)), None)
    if rate_index is None:
        return None

    name_parts = [texts[0]]
    spec_model = None
    unit = None
    quantity = None
    unit_price = None
    amount = None
    tax_amount = None

    before_rate = texts[1:rate_index]
    after_rate = texts[rate_index + 1 :]
    if before_rate:
        money_before = [text for text in before_rate if _is_money_text(text)]
        if money_before:
            amount = _clean_money(money_before[-1])
            leading = before_rate[: before_rate.index(money_before[-1])]
        else:
            leading = before_rate
        if leading:
            if _looks_like_spec(leading[0]):
                spec_model = leading[0]
                leading = leading[1:]
            if leading and _is_common_unit_token(leading[0], unit_tokens):
                unit = leading[0]
                leading = leading[1:]
            numeric_leading = [text for text in leading if _is_number_text(text)]
            if len(numeric_leading) >= 1:
                quantity = numeric_leading[0]
            if len(numeric_leading) >= 2:
                unit_price = numeric_leading[1]
    if after_rate:
        money_after = [text for text in after_rate if _is_money_text(text)]
        if amount is None and money_after:
            amount = _clean_money(money_after[0])
            money_after = money_after[1:]
        if money_after:
            tax_amount = _clean_money(money_after[0])
        for text in after_rate:
            if text in money_after or _is_money_text(text) or TAX_RATE_RE.fullmatch(text):
                continue
            if _has_chinese(text) and not _is_common_unit_token(text, unit_tokens):
                name_parts.append(text)

    if amount is None or tax_amount is None:
        return None
    item_name = _remove_spec_from_name("".join(name_parts), spec_model)
    return {
        "item_name": item_name,
        "spec_model": spec_model,
        "unit": unit,
        "quantity": quantity,
        "unit_price": unit_price,
        "line_amount": amount,
        "tax_rate": texts[rate_index],
        "line_tax_amount": tax_amount,
        "line_total_with_tax": _clean_money(str(Decimal(amount) + Decimal(tax_amount))),
    }


def _traditional_vat_items(text_units: TextUnits, schema: dict[str, Any]) -> list[dict[str, Any]]:
    item_area = _layout_section(schema, "item_area")
    header_terms = ["货物或应税劳务", "服务名称", "规格型号", "单价", "金额", "税额", "数量"]
    units = [
        unit
        for unit in _units_in_box(text_units, item_area)
        if not _is_total_marker(unit.text.strip())
        and not any(term in re.sub(r"\s+", "", unit.text) for term in header_terms)
    ]
    starts = [index for index, unit in enumerate(units) if unit.text.strip().startswith("*")]
    parsed: list[dict[str, Any]] = []
    unit_tokens = _unit_tokens(schema)
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(units)
        item = _traditional_item_from_group(units[start:end], unit_tokens)
        if item:
            parsed.append(item)
    return parsed
