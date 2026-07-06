"""金额、税额、价税合计字段抽取（含中文大写金额解析）。"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import (
    FieldCandidate,
    TextUnits,
)
from ..input_profile.text_units import logical_text_lines

from ._helpers import MONEY_RE, _add, _clean_money, _compact_text, _joined, _line_confidence, _money_matches


def _extract_money_totals_from_logical_lines(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
) -> None:
    if text_units.source != "ocr":
        return
    for line in logical_text_lines(text_units):
        text = line.text.strip()
        compact = _compact_text(text)
        values = _money_matches(text)
        if not values:
            continue
        confidence = _line_confidence(line)
        if "合计" in compact and "价税合计" not in compact and len(values) >= 2:
            _add(fields, "amount_total", values[0], text, confidence)
            _add(fields, "tax_total", values[1], text, confidence)
        if "价税合计" in compact and "小写" in compact:
            _add(fields, "total_with_tax", values[-1], text, confidence)


def _extract_money_totals(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any], text_units: TextUnits | None = None) -> None:
    money_values: list[tuple[str, str]] = []
    for line in lines:
        if "¥" not in line and "￥" not in line:
            continue
        for match in MONEY_RE.finditer(line):
            value = _clean_money(match.group(1))
            if value is not None:
                money_values.append((value, line))

    if "不征税" in _joined(lines):
        _add(fields, "tax_total", "0.00", "不征税", 0.75)

    if text_units is not None:
        _extract_money_totals_from_logical_lines(text_units, fields)

    for line in lines:
        values_in_yuan_line = _money_matches(line)
        values_in_yuan_line = [value for value in values_in_yuan_line if value is not None]
        if len(values_in_yuan_line) >= 2 and ("¥" in line or "￥" in line):
            _add(fields, "amount_total", values_in_yuan_line[0], line, 0.75)
            _add(fields, "tax_total", values_in_yuan_line[1], line, 0.75)
        total_rule = schema.get("text_labels", {}).get("total_line", {})
        include_all = total_rule.get("include_all", [])
        exclude_any = total_rule.get("exclude_any", [])
        is_total_line = all(term in line for term in include_all) and not any(term in line for term in exclude_any)
        if is_total_line and ("¥" in line or "￥" in line):
            values = _money_matches(line)
            values = [value for value in values if value is not None]
            if len(values) >= 1:
                _add(fields, "amount_total", values[0], line, 0.9)
            if len(values) >= 2:
                _add(fields, "tax_total", values[1], line, 0.9)
        if "小写" in line and ("¥" in line or "￥" in line):
            values = _money_matches(line)
            values = [value for value in values if value is not None]
            if values:
                _add(fields, "total_with_tax", values[-1], line, 0.9)

    if "amount_total" not in fields and money_values:
        _add(fields, "amount_total", money_values[0][0], money_values[0][1], 0.6, ["fallback_money_order"])
    if "tax_total" not in fields and len(money_values) >= 3:
        _add(fields, "tax_total", money_values[1][0], money_values[1][1], 0.6, ["fallback_money_order"])
    if "total_with_tax" not in fields and money_values:
        _add(fields, "total_with_tax", money_values[-1][0], money_values[-1][1], 0.6, ["fallback_money_order"])


def _add_non_tax_totals(
    fields: dict[str, list[FieldCandidate]],
    total: str | None,
    evidence: str,
    confidence: float = 0.9,
) -> None:
    if total is None:
        return
    _add(fields, "amount_total", total, evidence, confidence)
    _add(fields, "tax_total", "0.00", evidence, confidence)
    _add(fields, "total_with_tax", total, evidence, confidence)


def _amount_from_chinese_yuan(text: str) -> str | None:
    digit_map = {
        "零": 0,
        "壹": 1,
        "贰": 2,
        "叁": 3,
        "肆": 4,
        "伍": 5,
        "陆": 6,
        "柒": 7,
        "捌": 8,
        "玖": 9,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    match = re.search(r"([零壹贰叁肆伍陆柒捌玖一二三四五六七八九])元", text)
    if not match:
        return None
    return f"{digit_map[match.group(1)]:.2f}"

