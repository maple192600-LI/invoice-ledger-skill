"""Helper predicates for sequential line item extraction."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any

from ._helpers import (
    DEFAULT_TEXTUAL_SPEC_TOKENS,
    DEFAULT_UNIT_TOKENS,
    TAX_RATE_RE,
    _configured_set,
    _has_chinese,
    _has_unclosed_parenthesis,
    _is_money_text,
    _is_number_text,
    _is_total_marker,
    _safe_decimal,
)


def _split_unit_price_quantity(suffix: str, line_amount: Decimal) -> tuple[str | None, str | None]:
    compact = suffix.strip()
    for quantity_len in range(1, min(6, len(compact)) + 1):
        quantity_text = compact[-quantity_len:]
        price_text = compact[:-quantity_len]
        if not quantity_text.isdigit() or not price_text:
            continue
        price = _safe_decimal(price_text)
        quantity = _safe_decimal(quantity_text)
        if price is None or quantity is None:
            continue
        if abs(price * quantity - line_amount) <= Decimal("0.05"):
            return str(price), str(quantity)
    return None, None


def _unit_tokens(schema: dict[str, Any]) -> set[str]:
    return _configured_set(schema, "item_parse_rules", "unit_tokens", DEFAULT_UNIT_TOKENS)


def _textual_spec_tokens(schema: dict[str, Any]) -> set[str]:
    return _configured_set(
        schema,
        "item_parse_rules",
        "textual_spec_tokens",
        DEFAULT_TEXTUAL_SPEC_TOKENS,
    )


def _is_common_unit_token(text: str, unit_tokens: set[str]) -> bool:
    return text.strip().replace("(", "（").replace(")", "）") in unit_tokens


def _is_unit_before_numeric_values(lines: list[str], index: int, unit_tokens: set[str]) -> bool:
    if not _is_common_unit_token(lines[index], unit_tokens):
        return False
    return index + 1 < len(lines) and (_is_number_text(lines[index + 1]) or _is_money_text(lines[index + 1]))


def _is_textual_spec_before_unit(
    lines: list[str],
    index: int,
    unit_tokens: set[str],
    textual_spec_tokens: set[str],
) -> bool:
    return (
        lines[index].strip() in textual_spec_tokens
        and index + 2 < len(lines)
        and _is_common_unit_token(lines[index + 1], unit_tokens)
        and (_is_number_text(lines[index + 2]) or _is_money_text(lines[index + 2]))
    )


def _is_textual_spec_before_rate_and_unit(
    lines: list[str],
    index: int,
    unit_tokens: set[str],
    textual_spec_tokens: set[str],
) -> bool:
    return (
        lines[index].strip() in textual_spec_tokens
        and index + 2 < len(lines)
        and TAX_RATE_RE.fullmatch(lines[index + 1])
        and _is_common_unit_token(lines[index + 2], unit_tokens)
    )


def _is_trailing_item_name_fragment(current_name: str, text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith("*"):
        return False
    if not _has_unclosed_parenthesis(current_name) or not any(char in stripped for char in [")", "）"]):
        return False
    compact = re.sub(r"\s+", "", stripped)
    if TAX_RATE_RE.fullmatch(stripped) or _is_number_text(stripped) or _is_money_text(stripped):
        return False
    blocked_terms = [
        "项目名称",
        "规格型号",
        "单位",
        "数量",
        "单价",
        "金额",
        "税额",
        "合计",
        "价税合计",
        "收款人",
        "复核人",
        "开票人",
        "备注",
    ]
    if any(term in stripped or term in compact for term in blocked_terms):
        return False
    return len(stripped) <= 12 and bool(re.search(r"[\u4e00-\u9fffA-Za-z#（）()]", stripped))


def _finalize_sequence_item_name(lines: list[str], index: int, name_parts: list[str]) -> tuple[str, int]:
    while index < len(lines) and _is_trailing_item_name_fragment("".join(name_parts), lines[index]):
        name_parts.append(lines[index].strip())
        index += 1
    return "".join(name_parts), index


def _is_blocked_building_project_fragment(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    blocked_terms = [
        "合计",
        "价税合计",
        "开票人",
        "收款人",
        "复核",
        "备注",
        "购买方",
        "销售方",
        "纳税人识别号",
        "统一社会信用代码",
        "开户",
        "地址",
        "电话",
        "发票",
        "下载次数",
    ]
    return any(term in text or term in compact for term in blocked_terms)


def _building_sequence_parts(
    lines: list[str],
    index: int,
    name_parts: list[str],
) -> tuple[dict[str, str | None], int]:
    if not name_parts[0].startswith("*建筑服务*"):
        item_name, next_index = _finalize_sequence_item_name(lines, index, name_parts)
        return {"item_name": item_name, "service_location": None, "project_name": None}, next_index

    project_parts: list[str] = []
    next_index = index
    while next_index < len(lines) and len(project_parts) < 4:
        text = lines[next_index].strip()
        if (
            not text
            or text.startswith("*")
            or TAX_RATE_RE.fullmatch(text)
            or _is_number_text(text)
            or _is_money_text(text)
            or _is_total_marker(text)
            or _is_blocked_building_project_fragment(text)
        ):
            break
        if _has_chinese(text):
            project_parts.append(text)
            next_index += 1
            continue
        break
    return {
        "item_name": name_parts[0],
        "service_location": "".join(name_parts[1:]) or None,
        "project_name": "".join(project_parts) or None,
    }, next_index
