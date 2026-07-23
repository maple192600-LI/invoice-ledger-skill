"""航空运输电子客票行程单字段抽取（数电版直读票面 + legacy 旧版式兜底）。

数电版（2024-12 起全国推广）票面底部七列：票价/燃油附加费（均不含税）、增值税税率、
增值税税额、民航发展基金（不计税）、其他税费、合计。税额税率票面直接列明，只读不拆；
民航发展基金单列计入金额、不计税，使台账勾稽（金额+税额=价税合计）成立。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ...contracts import FieldCandidate, TextUnits
from .._helpers import (
    _add,
    _center_y,
    _cfg_terms,
    _date_candidates,
    _first_regex,
    _has_chinese,
    _joined,
    _money_hits,
    _nested_section,
    _required_float,
    _required_int,
    _x0,
)
from .._line_items import _add_json_item
from .._totals import _add_non_tax_totals

_DIGITAL_SIGNALS = ("民航发展基金", "增值税税额", "电子发票（航空运输电子客票行程单）")
_CNY_RE = re.compile(r"CNY\s*([\d,]+\.\d{2})")
_RATE_RE = re.compile(r"(?<!\d)(\d{1,2})%(?!\d)")
_NO20_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")
_COMPANY_RE = re.compile(r"[一-龥·]{4,25}(?:有限公司|集团有限责任公司|集团有限公司)")


def _to_dec(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", ""))
    except Exception:
        return None


def _fmt(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _sum(*values: Decimal | None) -> Decimal | None:
    if any(v is None for v in values):
        return None
    return sum(values, Decimal("0.00"))  # type: ignore[arg-type]


def _extract_digital(text: str, lines: list[str], fields: dict[str, list[FieldCandidate]]) -> None:
    _add(fields, "invoice_type", "电子发票（航空运输电子客票行程单）", "digital air title", 0.98)

    # 发票号码：20 位 数电号（不取 13 位电子客票号）
    match_no = _NO20_RE.search(text)
    if match_no:
        _add(fields, "invoice_no", match_no.group(1), "digital air invoice number", 0.96)

    # P6：开票日期优先"填开日期"标签；数电版式下首个日期即填开日期（航班日期在后）
    dates = _date_candidates(lines)
    if dates:
        _add(fields, "invoice_date", dates[0][0], dates[0][1], 0.9)

    companies = _COMPANY_RE.findall(text)
    seller = next((c for c in companies if "航空" in c), None)
    buyer = next((c for c in companies if "航空" not in c), None)
    if seller:
        _add(fields, "seller_name", seller, "digital air issuer", 0.9)
    if buyer:
        _add(fields, "buyer_name", buyer, "digital air purchaser", 0.88)

    cny_raw = _CNY_RE.findall(text)
    seen: set[str] = set()
    cny: list[str] = []
    for value in cny_raw:
        if value not in seen:
            seen.add(value)
            cny.append(value)
    rate_match = _RATE_RE.search(text)
    rate_str = f"{rate_match.group(1)}%" if rate_match else None

    # 票面七列 CNY 金额按序：票价、燃油附加费、增值税税额、民航发展基金、(其他税费)、合计
    if len(cny) < 5:
        return
    fare = _to_dec(cny[0])
    fuel = _to_dec(cny[1])
    tax_amount = _to_dec(cny[2])
    fund = _to_dec(cny[3])
    total = _to_dec(cny[-1])
    other_tax = _to_dec(cny[-2]) if len(cny) > 5 else Decimal("0.00")
    if None in (fare, fuel, tax_amount, fund, total):
        return

    fare_fuel = _sum(fare, fuel)
    line1_total = _sum(fare_fuel, tax_amount)
    # 金额合计含不计税的民航基金/其他税费，保证 金额+税额=价税合计 勾稽成立
    amount_total = _sum(fare_fuel, fund, other_tax)

    _add(fields, "tax_rate", rate_str, "digital air vat rate", 0.9)
    _add(fields, "amount_total", _fmt(amount_total), "digital air amount total", 0.92)
    _add(fields, "tax_total", _fmt(tax_amount), "digital air tax total", 0.92)
    _add(fields, "total_with_tax", _fmt(total), "digital air total", 0.94)

    items = [
        {
            "item_name": "航空运输服务（票价+燃油附加费）",
            "spec_model": None,
            "unit": None,
            "quantity": "1",
            "unit_price": _fmt(fare_fuel),
            "line_amount": _fmt(fare_fuel),
            "tax_rate": rate_str,
            "line_tax_amount": _fmt(tax_amount),
            "line_total_with_tax": _fmt(line1_total),
        },
        {
            "item_name": "民航发展基金",
            "spec_model": None,
            "unit": None,
            "quantity": "1",
            "unit_price": _fmt(fund),
            "line_amount": _fmt(fund),
            "tax_rate": None,
            "line_tax_amount": "0.00",
            "line_total_with_tax": _fmt(fund),
        },
    ]
    if other_tax is not None and other_tax != Decimal("0.00"):
        items.append(
            {
                "item_name": "其他税费",
                "spec_model": None,
                "unit": None,
                "quantity": "1",
                "unit_price": _fmt(other_tax),
                "line_amount": _fmt(other_tax),
                "tax_rate": None,
                "line_tax_amount": "0.00",
                "line_total_with_tax": _fmt(other_tax),
            }
        )
    for index, item in enumerate(items, start=1):
        _add_json_item(fields, item, index, f"digital air component {index}")


def _extract_legacy(
    text_units: TextUnits,
    text: str,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
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
    components = [("票价", fare), ("燃油附加费", fuel), ("机场建设费", airport_tax)]
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


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    if any(signal in text for signal in _DIGITAL_SIGNALS):
        _extract_digital(text, lines, fields)
        return
    _extract_legacy(text_units, text, lines, fields, schema)
