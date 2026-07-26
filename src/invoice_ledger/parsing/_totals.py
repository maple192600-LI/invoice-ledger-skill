"""金额、税额、价税合计字段抽取（含中文大写金额解析）。"""

from __future__ import annotations

from decimal import Decimal
import re
from statistics import median
from typing import Any

from ..contracts import (
    FieldCandidate,
    TextUnits,
)
from ..input_profile.text_units import logical_text_lines

from ._helpers import _add, _clean_money, _compact_text, _joined, _line_confidence, _money_matches


def _extract_money_totals_from_logical_lines(
    text_units: TextUnits,
    fields: dict[str, list[FieldCandidate]],
) -> None:
    # 单页数电票的文本块同样会按列拆散；用视觉逻辑行取合计，不能只留给多页票。
    final_page = max(text_units.page_range) if text_units.source != "ocr" else None
    logical_lines = logical_text_lines(text_units)
    line_heights = [line.bbox[3] - line.bbox[1] for line in logical_lines if line.bbox and line.bbox[3] > line.bbox[1]]
    adjacent_line_gap = (median(line_heights) * 1.2) if line_heights else 12.0
    for index, line in enumerate(logical_lines):
        if final_page is not None and line.page != final_page:
            continue
        text = line.text.strip()
        compact = _compact_text(text)
        values = _money_matches(text)
        is_total_with_tax = "价税合计" in compact and "小写" in compact
        if not values and not is_total_with_tax:
            continue
        confidence = _line_confidence(line)
        if "合计" in compact and "价税合计" not in compact and len(values) >= 2:
            _add(fields, "amount_total", values[0], text, confidence)
            _add(fields, "tax_total", values[1], text, confidence)
        if is_total_with_tax:
            total_values = values
            if not total_values and line.bbox:
                for following_line in logical_lines[index + 1 :]:
                    if following_line.page != line.page:
                        break
                    if not following_line.bbox or following_line.bbox[1] - line.bbox[3] > adjacent_line_gap:
                        break
                    total_values = _money_matches(following_line.text)
                    if total_values:
                        break
            if total_values:
                _add(fields, "total_with_tax", total_values[-1], text, confidence)
                if not fields.get("amount_total") or not fields.get("tax_total"):
                    nearby_values: list[str] = []
                    for preceding_line in logical_lines[max(0, index - 4) : index]:
                        preceding_compact = _compact_text(preceding_line.text)
                        if (
                            preceding_line.page == line.page
                            and (preceding_compact.startswith("计") or "合计" in preceding_compact)
                        ):
                            nearby_values.extend(_money_matches(preceding_line.text))
                    expected = Decimal(total_values[-1])
                    if len(nearby_values) >= 2:
                        left, right = nearby_values[-2:]
                        if abs(Decimal(left) + Decimal(right) - expected) <= Decimal("0.02"):
                            evidence = f"final total arithmetic: {left} + {right} = {total_values[-1]}"
                            if not fields.get("amount_total"):
                                _add(fields, "amount_total", left, evidence, confidence)
                            if not fields.get("tax_total"):
                                _add(fields, "tax_total", right, evidence, confidence)
                            return


def _extract_money_totals(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any], text_units: TextUnits | None = None) -> None:
    if text_units is not None:
        _extract_money_totals_from_logical_lines(text_units, fields)

    for line in lines:
        values_in_yuan_line = _money_matches(line)
        values_in_yuan_line = [value for value in values_in_yuan_line if value is not None]
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

