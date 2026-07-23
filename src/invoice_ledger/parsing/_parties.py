"""买卖方与税号字段抽取（含 OCR 列识别与几何推断）。"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import (
    FieldCandidate,
    SchemaDecision,
    TextUnit,
    TextUnits,
)
from ..schema.schema_loader import field_aliases

from ._helpers import DATE_RE, NON_PARTY_FIELD_NAMES, TAX_ID_EXCLUDED_CONTEXT_TERMS, TAX_ID_RE, _add, _center_x, _center_y, _clean_label_value, _has_chinese, _is_tax_id_value, _nested_section, _page_width, _role_near_line, _tax_ids, _unit_in_y_band, _units_in_layout, _units_with_geometry, _x0


def _extract_name_from_label(line: str, label: str) -> str | None:
    splitters = [f"{label}：", f"{label}:", label]
    if label == "名称":
        splitters.extend(["称：", "称:"])
    for splitter in splitters:
        if splitter in line:
            value = line.split(splitter, 1)[-1].strip()
            return value or None
    return None


def _is_non_party_text(line: str, schema: dict[str, Any]) -> bool:
    for field_name in NON_PARTY_FIELD_NAMES:
        for alias in field_aliases(schema, field_name):
            if alias and len(alias) > 1 and alias in line:
                return True
    return False


def _nearest_name_before(lines: list[str], index: int, schema: dict[str, Any]) -> str | None:
    label_terms = schema.get("text_labels", {}).get("ignore_near_name_terms", [])
    generic_name_label = schema.get("text_labels", {}).get("generic_name_label", "")
    for candidate in reversed(lines[max(0, index - 8):index]):
        stripped = candidate.strip()
        if not stripped:
            continue
        if _is_non_party_text(stripped, schema):
            continue
        if generic_name_label:
            parsed = _extract_name_from_label(stripped, generic_name_label)
            if parsed:
                return parsed
        if len(stripped) <= 1:
            continue
        if not re.search(r"[\u4e00-\u9fff]", stripped):
            continue
        if any(noise in stripped for noise in ["<", ">", "/", "SIGN", "发票专用章"]):
            continue
        if any(term == stripped if len(term) == 1 else term in stripped for term in label_terms):
            parsed = _extract_name_from_label(stripped, generic_name_label)
            if parsed:
                return parsed
            continue
        if TAX_ID_RE.search(stripped) or DATE_RE.search(stripped):
            continue
        return stripped
    return None


def _role_tax_ids_from_ocr_columns(text_units: TextUnits | None) -> dict[str, tuple[str, int, str]]:
    if text_units is None or text_units.source != "ocr":
        return {}
    units = _units_with_geometry(text_units)
    buyer_headers = [unit for unit in units if _is_party_column_header(unit.text, "buyer")]
    seller_headers = [unit for unit in units if _is_party_column_header(unit.text, "seller")]
    if not buyer_headers or not seller_headers:
        return {}

    buyer_x = sum(_center_x(unit) for unit in buyer_headers) / len(buyer_headers)
    seller_x = sum(_center_x(unit) for unit in seller_headers) / len(seller_headers)
    if buyer_x == seller_x:
        return {}

    line_index_by_order = {
        unit.order: index
        for index, unit in enumerate(unit for unit in text_units.units if unit.text.strip())
    }
    role_tax_ids: dict[str, tuple[str, int, str]] = {}
    for unit in units:
        text = unit.text.strip()
        if any(term in text for term in TAX_ID_EXCLUDED_CONTEXT_TERMS):
            continue
        for match in TAX_ID_RE.finditer(text):
            value = match.group(0)
            if not _is_tax_id_value(value):
                continue
            role = "buyer" if abs(_center_x(unit) - buyer_x) <= abs(_center_x(unit) - seller_x) else "seller"
            if role not in role_tax_ids:
                role_tax_ids[role] = (value, line_index_by_order.get(unit.order, 0), text)
            break
    return role_tax_ids


def _is_party_column_header(text: str, role: str) -> bool:
    compact = re.sub(r"\s+", "", text.strip())
    if role == "buyer":
        return compact in {"购买方", "购买方信息"}
    if role == "seller":
        return compact in {"销售方", "销售方信息"}
    return False


def _extract_names_and_tax_ids(
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
    text_units: TextUnits | None = None,
) -> None:
    buyer_name = None
    buyer_name_evidence = ""
    seller_name = None
    seller_name_evidence = ""
    buyer_labels = schema.get("text_labels", {}).get("buyer_name_labels", field_aliases(schema, "buyer_name"))
    seller_labels = schema.get("text_labels", {}).get("seller_name_labels", field_aliases(schema, "seller_name"))
    generic_name_label = schema.get("text_labels", {}).get("generic_name_label", "")

    for line in lines:
        for label in buyer_labels:
            if label in line:
                buyer_name = _extract_name_from_label(line, label)
                buyer_name_evidence = line
        for label in seller_labels:
            if label in line:
                seller_name = _extract_name_from_label(line, label)
                seller_name_evidence = line
        if (
            generic_name_label
            and f"{generic_name_label}：" in line
            and not _is_non_party_text(line, schema)
            and not any(label in line for label in buyer_labels + seller_labels)
        ):
            value = _extract_name_from_label(line, generic_name_label)
            if value and not buyer_name:
                buyer_name = value
                buyer_name_evidence = line

    tax_ids = _tax_ids(lines)
    role_tax_ids: dict[str, tuple[str, int, str]] = _role_tax_ids_from_ocr_columns(text_units)
    unbound_tax_ids: list[tuple[str, int, str]] = []
    for tax_id in tax_ids:
        if any(tax_id[0] == role_tax_id[0] for role_tax_id in role_tax_ids.values()):
            continue
        role = _role_near_line(lines, tax_id[1])
        if role in {"buyer", "seller"} and role not in role_tax_ids:
            role_tax_ids[role] = tax_id
        else:
            unbound_tax_ids.append(tax_id)

    if "buyer" in role_tax_ids:
        _add(fields, "buyer_tax_id", role_tax_ids["buyer"][0], role_tax_ids["buyer"][2], 0.96)
    elif tax_ids:
        _add(fields, "buyer_tax_id", tax_ids[0][0], tax_ids[0][2], 0.75, ["tax_id_order_fallback"])
    if not buyer_name and tax_ids:
        buyer_name = _nearest_name_before(lines, tax_ids[0][1], schema)
        buyer_name_evidence = "buyer name near tax id"

    if "seller" in role_tax_ids:
        _add(fields, "seller_tax_id", role_tax_ids["seller"][0], role_tax_ids["seller"][2], 0.96)
    elif len(tax_ids) > 1:
        fallback_tax_id = unbound_tax_ids[1] if len(unbound_tax_ids) > 1 else tax_ids[1]
        _add(fields, "seller_tax_id", fallback_tax_id[0], fallback_tax_id[2], 0.75, ["tax_id_order_fallback"])
    if not seller_name and len(tax_ids) > 1:
        seller_name = _nearest_name_before(lines, tax_ids[1][1], schema)
        seller_name_evidence = "seller name near tax id"

    if buyer_name:
        _add(fields, "buyer_name", buyer_name, buyer_name_evidence or "buyer name near tax id", 0.98)
    if seller_name:
        _add(fields, "seller_name", seller_name, seller_name_evidence or "seller name near tax id", 0.98)


def _company_name_in_units(units: list[TextUnit]) -> str | None:
    blocked_terms = [
        "名称",
        "纳税人识别号",
        "地址",
        "电话",
        "开户行",
        "账号",
        "价税合计",
        "合计",
        "元整",
        "圆整",
        "机器编号",
        "发票代码",
        "购买方信息",
        "销售方信息",
        "购买方",
        "销售方",
        "购",
        "买",
        "方",
        "销",
        "售",
        "信",
        "息",
    ]
    chinese_units = [
        unit.text.strip()
        for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item), item.order))
        if _has_chinese(unit.text)
        and len(unit.text.strip()) > 1
        and not TAX_ID_RE.search(unit.text)
        and not any(
            unit.text.strip() == term if len(term) == 1 else term in unit.text
            for term in blocked_terms
        )
    ]
    preferred_suffixes = ("公司", "有限公司", "经营部", "分公司")
    for text in chinese_units:
        if any(suffix in text for suffix in preferred_suffixes):
            return text
    return chinese_units[0] if chinese_units else None


def _tax_id_in_units(units: list[TextUnit]) -> str | None:
    for unit in sorted(units, key=lambda item: (_center_y(item), _x0(item), item.order)):
        for match in TAX_ID_RE.finditer(unit.text.strip()):
            value = match.group(0)
            if _is_tax_id_value(value):
                return value
    return None


def _party_geometry_rule(schema: dict[str, Any], decision: SchemaDecision) -> dict[str, Any]:
    rules = schema.get("party_geometry", {}).get("variant_rules", {})
    if not isinstance(rules, dict) or not decision.variant_id:
        return {}
    rule = rules.get(decision.variant_id, {})
    if not isinstance(rule, dict) or rule.get("enabled") is not True:
        return {}
    if not isinstance(rule.get("name_band"), dict) or not isinstance(rule.get("tax_band"), dict):
        return {}
    if "split_x_ratio" not in rule:
        return {}
    return rule


def _party_values_from_geometry(text_units: TextUnits, rule: dict[str, Any]) -> dict[str, str]:
    units = _units_with_geometry(text_units)
    width = _page_width(units)
    if not units or not width:
        return {}

    split_x = width * float(rule["split_x_ratio"])
    name_band = rule["name_band"]
    tax_band = rule["tax_band"]
    excluded_terms = [str(term) for term in name_band.get("exclude_terms", [])]
    upper_party_units = [
        unit
        for unit in units
        if _unit_in_y_band(unit, name_band) and not any(term and term in unit.text for term in excluded_terms)
    ]
    left_units = [unit for unit in upper_party_units if _x0(unit) < split_x]
    right_units = [unit for unit in upper_party_units if _x0(unit) >= split_x]
    left_tax_units = [
        unit for unit in units if _unit_in_y_band(unit, tax_band) and _x0(unit) < split_x
    ]
    right_tax_units = [
        unit for unit in units if _unit_in_y_band(unit, tax_band) and _x0(unit) >= split_x
    ]

    result: dict[str, str] = {}
    buyer_name = _company_name_in_units(left_units)
    seller_name = _company_name_in_units(right_units)
    buyer_tax_id = _tax_id_in_units(left_tax_units)
    seller_tax_id = _tax_id_in_units(right_tax_units)
    if buyer_name:
        result["buyer_name"] = buyer_name
    if seller_name:
        result["seller_name"] = seller_name
    if buyer_tax_id:
        result["buyer_tax_id"] = buyer_tax_id
    if seller_tax_id:
        result["seller_tax_id"] = seller_tax_id
    return result


def _company_name_candidates(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if "公司" in line or "有限公司" in line]


def _company_after_labels(lines: list[str], labels: list[str]) -> str | None:
    for index, line in enumerate(lines):
        for label in labels:
            if label not in line:
                continue
            value = _clean_label_value(line.split(label, 1)[-1])
            if value:
                return value
            for candidate in lines[index + 1 : index + 4]:
                company = _company_name_from_text(candidate)
                if company:
                    return company
    return None


def _company_name_from_text(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    match = re.search(r"([\u4e00-\u9fff]{2,}(?:有限责任公司|股份有限公司|有限公司|公司))", compact)
    return match.group(1) if match else None


def _road_bus_stamp_seller_name(
    text_units: TextUnits,
    schema: dict[str, Any],
    origin: str | None,
) -> str | None:
    layout = _nested_section(schema, "layout_rules", "seller_stamp")
    stamp_units = _units_in_layout(text_units, layout)
    if not stamp_units:
        return None

    texts = [unit.text.strip() for unit in stamp_units if unit.text.strip()]
    for text in texts:
        company = _company_name_from_text(text)
        if company:
            return company

    stamp_terms = [str(term) for term in layout.get("stamp_terms", ["发票专用章"])]
    has_stamp = any(any(term in text for term in stamp_terms) for text in texts)
    suffix = str(layout.get("origin_company_suffix", "")).strip()
    if not origin or not suffix or not has_stamp:
        return None

    partials = [
        text
        for text in texts
        if _has_chinese(text)
        and not re.search(r"[0-9A-Z]{8,}", text)
        and not any(term in text for term in stamp_terms)
        and "税务" not in text
    ]
    if not any(origin.startswith(partial) or partial in origin for partial in partials):
        return None

    if origin.endswith("市") and suffix.startswith("市"):
        return f"{origin}{suffix[1:]}"
    return f"{origin}{suffix}"


def _railway_station_names(lines: list[str]) -> list[str]:
    return [
        line.strip()
        for line in lines
        if re.fullmatch(r"[\u4e00-\u9fff]{1,12}站", line.strip())
    ]


def _taxi_plate_city_prefix(lines: list[str], schema: dict[str, Any]) -> str | None:
    completion = schema.get("seller_name_completion", {})
    prefixes = completion.get("plate_city_prefixes", {}) if isinstance(completion, dict) else {}
    if not isinstance(prefixes, dict):
        return None
    normalized_lines = [line.replace(" ", "") for line in lines]
    for plate_prefix, city_prefix in prefixes.items():
        if any(str(plate_prefix) in line for line in normalized_lines):
            return str(city_prefix)
    return None


def _complete_taxi_seller_name(
    seller_name: str | None,
    lines: list[str],
    schema: dict[str, Any],
) -> str | None:
    if not seller_name:
        return seller_name
    city_prefix = _taxi_plate_city_prefix(lines, schema)
    if not city_prefix or seller_name.startswith(city_prefix):
        return seller_name
    if seller_name.startswith("市"):
        return f"{city_prefix.rstrip('市')}{seller_name}"
    if "出租汽车有限公司" in seller_name and not seller_name.endswith(city_prefix):
        return f"{city_prefix}{seller_name}"
    return seller_name

