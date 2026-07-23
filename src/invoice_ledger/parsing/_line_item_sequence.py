"""Sequential text line item extraction."""

from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Any

from ..contracts import FieldCandidate, TextUnits
from ._helpers import (
    TAX_RATE_RE,
    _add,
    _clean_money,
    _is_money_text,
    _is_number_text,
    _lines,
    _looks_like_spec,
    _normalize_ocr_item,
)
from ._line_item_ocr_table import _extract_ocr_table_items
from ._line_item_sequence_helpers import (
    _building_sequence_parts,
    _is_textual_spec_before_rate_and_unit,
    _is_textual_spec_before_unit,
    _is_unit_before_numeric_values,
    _split_unit_price_quantity,
    _textual_spec_tokens,
    _unit_tokens,
)


def _numeric_line_item(line: str, previous_names: list[str]) -> dict[str, Any] | None:
    rate_match = TAX_RATE_RE.search(line)
    if not rate_match:
        return None
    rate = rate_match.group(1)

    if rate == "不征税":
        pattern = re.compile(
            r"^(?P<item>.+?)\s+(?P<spec>\S+)?\s*(?P<unit>\S+)\s+"
            r"(?P<quantity>\d+(?:\.\d+)?)\s+(?P<unit_price>\d+(?:\.\d+)?)\s+"
            r"(?P<amount>\d+(?:\.\d+)?)\s+不征税"
        )
        match = pattern.search(line)
        if not match:
            return None
        return {
            "item_name": match.group("item").strip(),
            "spec_model": (match.group("spec") or "").strip() or None,
            "unit": match.group("unit"),
            "quantity": match.group("quantity"),
            "unit_price": match.group("unit_price"),
            "line_amount": _clean_money(match.group("amount")),
            "tax_rate": rate,
            "line_tax_amount": "0.00",
            "line_total_with_tax": _clean_money(match.group("amount")),
        }

    rate_end = rate_match.end()
    prefix = line[: rate_match.start()].strip()
    after_rate = line[rate_end:].strip()
    unit_match = re.match(r"(?P<unit>[\u4e00-\u9fff（）()A-Za-z]+)\s+(?P<rest>.+)", after_rate)
    if unit_match:
        unit = unit_match.group("unit")
        rest = unit_match.group("rest")
    else:
        unit = None
        rest = after_rate

    amounts = re.findall(r"\d+\.\d{2}", rest)
    if len(amounts) < 2:
        pattern = re.compile(
            r"^(?P<item>.+?)\s+(?P<unit>\S+)\s+(?P<quantity>\d+(?:\.\d+)?)\s+"
            r"(?P<unit_price>\d+(?:\.\d+)?)\s+(?P<amount>\d+(?:\.\d+)?)\s+"
            r"(?P<rate>\d+(?:\.\d+)?%)\s+(?P<tax>\d+(?:\.\d+)?)"
        )
        match = pattern.search(line)
        if not match:
            return None
        amount = _clean_money(match.group("amount"))
        tax_amount = _clean_money(match.group("tax"))
        return {
            "item_name": match.group("item").strip(),
            "spec_model": None,
            "unit": match.group("unit"),
            "quantity": match.group("quantity"),
            "unit_price": match.group("unit_price"),
            "line_amount": amount,
            "tax_rate": match.group("rate"),
            "line_tax_amount": tax_amount,
            "line_total_with_tax": _clean_money(str(Decimal(amount) + Decimal(tax_amount))) if amount and tax_amount else None,
        }

    amount = _clean_money(amounts[0])
    line_amount = Decimal(amount) if amount else Decimal("0")
    tax_decimal = (line_amount * Decimal(rate.rstrip("%")) / Decimal("100")).quantize(Decimal("0.01"))
    tax_amount = _clean_money(str(tax_decimal))
    tax_text = f"{tax_decimal:.2f}"
    rest_after_amount = rest.split(amounts[0], 1)[-1].strip()
    rest_after_tax = rest_after_amount
    if rest_after_tax.startswith(tax_text):
        rest_after_tax = rest_after_tax[len(tax_text):]
    elif amounts[1] in rest_after_tax:
        rest_after_tax = rest_after_tax.split(amounts[1], 1)[-1]

    unit_price, quantity = _split_unit_price_quantity(rest_after_tax, line_amount)
    item_name = "".join(previous_names[-2:]).strip() or prefix or None
    return {
        "item_name": item_name,
        "spec_model": prefix or None,
        "unit": unit,
        "quantity": quantity,
        "unit_price": unit_price,
        "line_amount": amount,
        "tax_rate": rate,
        "line_tax_amount": tax_amount,
        "line_total_with_tax": _clean_money(str(line_amount + tax_decimal)),
    }


def _extract_sequence_item(
    lines: list[str],
    start: int,
    unit_tokens: set[str],
    textual_spec_tokens: set[str],
) -> tuple[dict[str, Any] | None, int]:
    if not lines[start].startswith("*"):
        return None, start + 1

    name_parts = [lines[start]]
    index = start + 1
    while index < len(lines) and not _looks_like_spec(lines[index]) and not TAX_RATE_RE.fullmatch(lines[index]) and not _is_number_text(lines[index]) and not lines[index].startswith("*"):
        if (
            _is_unit_before_numeric_values(lines, index, unit_tokens)
            or _is_textual_spec_before_unit(lines, index, unit_tokens, textual_spec_tokens)
            or _is_textual_spec_before_rate_and_unit(lines, index, unit_tokens, textual_spec_tokens)
        ):
            break
        if len(lines[index]) <= 20:
            name_parts.append(lines[index])
            index += 1
            continue
        break

    spec = None
    if index < len(lines) and (
        _looks_like_spec(lines[index])
        or _is_textual_spec_before_unit(lines, index, unit_tokens, textual_spec_tokens)
        or _is_textual_spec_before_rate_and_unit(lines, index, unit_tokens, textual_spec_tokens)
    ):
        spec = lines[index]
        index += 1

    rate = None
    if index < len(lines) and TAX_RATE_RE.fullmatch(lines[index]):
        rate = lines[index]
        index += 1

    unit = None
    if index < len(lines) and not _is_number_text(lines[index]) and not _is_money_text(lines[index]) and not TAX_RATE_RE.fullmatch(lines[index]):
        unit = lines[index]
        index += 1
        if index < len(lines) and lines[index].startswith(("（", "(")) and not _is_number_text(lines[index]):
            unit = f"{unit}{lines[index]}"
            index += 1

    if rate:
        if index + 3 < len(lines):
            amount = _clean_money(lines[index]) if _is_money_text(lines[index]) else None
            tax_amount = _clean_money(lines[index + 1]) if _is_money_text(lines[index + 1]) else None
            unit_price = lines[index + 2].strip()
            quantity = lines[index + 3].strip()
            if amount is not None and tax_amount is not None and _is_number_text(unit_price) and _is_number_text(quantity):
                building_parts, next_index = _building_sequence_parts(lines, index + 4, name_parts)
                total = _clean_money(str(Decimal(amount) + Decimal(tax_amount)))
                return (
                    {
                        **building_parts,
                        "spec_model": spec,
                        "unit": unit,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "line_amount": amount,
                        "tax_rate": rate,
                        "line_tax_amount": tax_amount,
                        "line_total_with_tax": total,
                    },
                    next_index,
                )
        if index + 1 < len(lines) and _is_money_text(lines[index]) and _is_money_text(lines[index + 1]):
            amount = _clean_money(lines[index])
            tax_amount = _clean_money(lines[index + 1])
            building_parts, next_index = _building_sequence_parts(lines, index + 2, name_parts)
            total = _clean_money(str(Decimal(amount) + Decimal(tax_amount))) if amount and tax_amount else None
            return (
                {
                    **building_parts,
                    "spec_model": spec,
                    "unit": unit,
                    "quantity": None,
                    "unit_price": None,
                    "line_amount": amount,
                    "tax_rate": rate,
                    "line_tax_amount": tax_amount,
                    "line_total_with_tax": total,
                },
                next_index,
            )
        return None, start + 1

    if (
        index + 4 < len(lines)
        and _is_number_text(lines[index])
        and _is_number_text(lines[index + 1])
        and _is_money_text(lines[index + 2])
        and TAX_RATE_RE.fullmatch(lines[index + 3])
        and _is_money_text(lines[index + 4])
    ):
        quantity = lines[index]
        unit_price = lines[index + 1]
        amount = _clean_money(lines[index + 2])
        tax_rate = lines[index + 3]
        tax_amount = _clean_money(lines[index + 4])
        building_parts, next_index = _building_sequence_parts(lines, index + 5, name_parts)
        total = _clean_money(str(Decimal(amount) + Decimal(tax_amount))) if amount and tax_amount else None
        return (
            {
                **building_parts,
                "spec_model": spec,
                "unit": unit,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_amount": amount,
                "tax_rate": tax_rate,
                "line_tax_amount": tax_amount,
                "line_total_with_tax": total,
            },
            next_index,
        )

    if index + 2 < len(lines) and _is_number_text(lines[index]) and _is_number_text(lines[index + 1]):
        quantity = lines[index]
        unit_price = lines[index + 1]
        amount_line = lines[index + 2]
        rate_match = TAX_RATE_RE.search(amount_line)
        if rate_match:
            amount_match = re.search(r"\d+(?:\.\d+)?", amount_line)
            amount = _clean_money(amount_match.group(0)) if amount_match else None
            tax_rate = rate_match.group(1)
            tax_amount = "0.00" if tax_rate == "不征税" else None
            building_parts, next_index = _building_sequence_parts(lines, index + 3, name_parts)
            total = amount if tax_amount == "0.00" else None
            return (
                {
                    **building_parts,
                    "spec_model": spec,
                    "unit": unit,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_amount": amount,
                    "tax_rate": tax_rate,
                    "line_tax_amount": tax_amount,
                    "line_total_with_tax": total,
                },
                next_index,
            )

    if (
        index + 2 < len(lines)
        and _is_money_text(lines[index])
        and TAX_RATE_RE.fullmatch(lines[index + 1])
        and _is_money_text(lines[index + 2])
    ):
        amount = _clean_money(lines[index])
        tax_rate = lines[index + 1]
        tax_amount = _clean_money(lines[index + 2])
        building_parts, next_index = _building_sequence_parts(lines, index + 3, name_parts)
        total = _clean_money(str(Decimal(amount) + Decimal(tax_amount))) if amount and tax_amount else None
        return (
            {
                **building_parts,
                "spec_model": spec,
                "unit": unit,
                "quantity": None,
                "unit_price": None,
                "line_amount": amount,
                "tax_rate": tax_rate,
                "line_tax_amount": tax_amount,
                "line_total_with_tax": total,
            },
            next_index,
        )
    return None, start + 1


def _extract_items(
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    pending_names: list[str] = []
    items: list[dict[str, Any]] = []
    unit_tokens = _unit_tokens(schema)
    textual_spec_tokens = _textual_spec_tokens(schema)
    index = 0
    while index < len(lines):
        item, next_index = _extract_sequence_item(lines, index, unit_tokens, textual_spec_tokens)
        if item:
            items.append(item)
            index = next_index
            pending_names = []
            continue
        line = lines[index]
        if line.startswith("*") and not TAX_RATE_RE.search(line):
            pending_names.append(line)
            index += 1
            continue
        item = _numeric_line_item(line, pending_names)
        if item:
            items.append(item)
            pending_names = []
        index += 1

    for index, item in enumerate(items, start=1):
        item["line_no"] = index
        value = json.dumps(item, ensure_ascii=False, sort_keys=True)
        _add(fields, "items", value, f"line item {index}", 0.8)


def _extract_items_from_text_units(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    if text_units.source == "ocr":
        items = _extract_ocr_table_items(text_units, schema)
        if items:
            for index, item in enumerate(items, start=1):
                item = _normalize_ocr_item(item, schema)
                item["line_no"] = index
                value = json.dumps(item, ensure_ascii=False, sort_keys=True)
                _add(fields, "items", value, f"ocr table line item {index}", 0.86)
            return
    _extract_items(_lines(text_units), fields, schema)


def _item_tax_rates(fields: dict[str, list[FieldCandidate]]) -> list[str]:
    rates: list[str] = []
    for candidate in fields.get("items", []):
        try:
            payload = json.loads(candidate.value)
        except json.JSONDecodeError:
            continue
        rate = payload.get("tax_rate")
        if rate:
            rates.append(str(rate))
    return rates
