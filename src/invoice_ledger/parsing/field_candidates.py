"""字段候选生成入口：调度通用字段抽取与票种特定策略。"""

from __future__ import annotations

import re
import json
from typing import Any

from ..contracts import (
    FieldCandidate,
    FieldCandidates,
    SchemaDecision,
    SchemaDecisionStatus,
    TextUnits,
)
from .invoice_identity import is_standard_digital_like
from ..schema.schema_loader import load_schema

from ._helpers import _add, _add_ocr_confidence_risks, _compact_text, _joined, _lines, _schema_section
from ._invoice_fields import _extract_dates, _extract_invoice_code, _extract_invoice_number, _extract_invoice_type
from ._line_items import _extract_items_from_text_units, _item_tax_rates
from ._parties import _extract_names_and_tax_ids, _party_geometry_rule, _party_values_from_geometry
from ._totals import _extract_money_totals
from ._traditional_vat import _extract_traditional_vat_candidates
from . import scheme_extractors  # noqa: F401


def _shared_fallback_fields(schema: dict[str, Any]) -> set[str]:
    value = _schema_section(schema, "shared_fallback").get("fields", [])
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


_RED_INVOICE_SIGNALS = ("红字发票", "（负数）", "(负数)", "被红冲", "红字信息确认单", "红冲")
_RE_ORIGINAL_NO = re.compile(r"被红冲蓝字发票号码[：:]\s*([0-9A-Z]+)")
_RE_ORIGINAL_CODE = re.compile(r"被红冲蓝字发票代码[：:]\s*([0-9A-Z]+)")
_RE_CONFIRM_NO = re.compile(r"红字发票信息确认单编号[：:]\s*([0-9A-Z]+)")
_SPECIAL_INVOICE_TYPES = {"建筑服务", "成品油"}


def _extract_special_invoice_type(
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    configured_value = schema.get("special_invoice_type_value")
    if configured_value:
        _add(fields, "special_invoice_type", str(configured_value), "schema route", 0.99)
        return
    for line in lines:
        value = line.strip()
        if value in _SPECIAL_INVOICE_TYPES:
            _add(fields, "special_invoice_type", value, line, 0.98)
            return


def _enrich_special_items(
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    candidates = fields.get("items", [])
    if not candidates:
        return
    payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            payloads.append(json.loads(candidate.value))
        except json.JSONDecodeError:
            return

    joined = "\n".join(lines)
    patterns = schema.get("item_context_patterns", {})
    if isinstance(patterns, dict):
        for field_name, spec in patterns.items():
            if not isinstance(spec, dict):
                continue
            if payloads[0].get(str(field_name)) not in (None, ""):
                continue
            for pattern_spec in spec.get("patterns", []):
                pattern = pattern_spec.get("pattern") if isinstance(pattern_spec, dict) else pattern_spec
                match = re.search(str(pattern), joined)
                if not match:
                    continue
                value = pattern_spec.get("value", spec.get("value")) if isinstance(pattern_spec, dict) else spec.get("value")
                if value is None:
                    group = int(spec.get("group", 1))
                    value = match.group(group)
                payloads[0][str(field_name)] = str(value).strip()
                break

    previous_regular_line: int | None = None
    for index, payload in enumerate(payloads, start=1):
        line_no = int(payload.get("line_no") or index)
        amount = str(payload.get("line_amount") or "")
        tax = str(payload.get("line_tax_amount") or "")
        if amount.startswith("-") or tax.startswith("-"):
            if previous_regular_line is not None:
                payload["discount_for_line_no"] = previous_regular_line
        else:
            previous_regular_line = line_no

    for candidate, payload in zip(candidates, payloads):
        candidate.value = json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _extract_red_invoice_signals(lines: list[str], fields: dict[str, list[FieldCandidate]]) -> None:
    """识别红冲(负数)发票信号 + 抽取备注区红冲关联字段。

    信号：票面"红字发票"标识 / 价税合计"（负数）"前缀 / 备注"被红冲蓝字"/"红字信息确认单"。
    红冲关联：被红冲蓝字发票号码/代码、红字发票信息确认单编号。
    """
    joined = "\n".join(lines)
    if any(signal in joined for signal in _RED_INVOICE_SIGNALS):
        _add(fields, "is_red_invoice", "是", "red invoice signal", 0.9)
    for line in lines:
        if not any(key in line for key in ("被红冲蓝字", "红字信息确认单")):
            continue
        match = _RE_ORIGINAL_NO.search(line)
        if match:
            _add(fields, "red_original_no", match.group(1), "remark red ref", 0.85)
        match = _RE_ORIGINAL_CODE.search(line)
        if match:
            _add(fields, "red_original_code", match.group(1), "remark red ref", 0.85)
        match = _RE_CONFIRM_NO.search(line)
        if match:
            _add(fields, "red_confirm_no", match.group(1), "remark red ref", 0.85)


def _extract_schema_specific_candidates(
    text_units: TextUnits,
    decision: SchemaDecision,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> bool:
    extractors = {
        "medical-fiscal-receipt": scheme_extractors.medical_receipt.extract,
        "air-ticket-itinerary": scheme_extractors.air_ticket.extract,
        "general-machine-invoice": scheme_extractors.machine_invoice.extract,
        "tax-payment-certificate": scheme_extractors.tax_payment.extract,
        "road-bus-ticket": scheme_extractors.road_bus.extract,
        "railway-ticket": scheme_extractors.railway.extract,
        "motor-vehicle-unified": scheme_extractors.motor_vehicle.extract,
        "water-passenger-ticket": scheme_extractors.water_passenger.extract,
        "taxi-machine-invoice": scheme_extractors.taxi.extract,
        "metro-quota-invoice": scheme_extractors.metro_quota.extract,
    }
    extractor = extractors.get(decision.schema_id or "")
    if not extractor:
        return False
    extractor(text_units, lines, fields, schema)
    return True


def _normalize_invoice_type_from_context(
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    decision: SchemaDecision,
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    rules = schema.get("invoice_type_context", {})
    if not isinstance(rules, dict):
        rules = {}
    for rule in rules.get("title_rules", []):
        if not isinstance(rule, dict):
            continue
        include_all = [str(term) for term in rule.get("include_all", [])]
        include_any = [str(term) for term in rule.get("include_any", [])]
        exclude_any = [str(term) for term in rule.get("exclude_any", [])]
        if include_all and not all(term in text for term in include_all):
            continue
        if include_any and not any(term in text for term in include_any):
            continue
        if exclude_any and any(term in text for term in exclude_any):
            continue
        if rule.get("value"):
            _add(
                fields,
                "invoice_type",
                rule["value"],
                "invoice type context",
                float(rule.get("confidence", 0.96)),
            )
            return
    rates = _item_tax_rates(fields)
    for rate_rule in rules.get("tax_rate_rules", []):
        if not isinstance(rate_rule, dict):
            continue
        rate = rate_rule.get("rate")
        if rate and str(rate) in rates and rate_rule.get("value"):
            _add(
                fields,
                "invoice_type",
                rate_rule["value"],
                "invoice type tax-rate context",
                float(rate_rule.get("confidence", 0.95)),
            )
            return
    variant_rules = rules.get("variant_rules", {})
    if isinstance(variant_rules, dict) and decision.variant_id in variant_rules:
        variant_rule = variant_rules.get(decision.variant_id, {})
        if not isinstance(variant_rule, dict):
            return
        for rate_rule in variant_rule.get("tax_rate_rules", []):
            if not isinstance(rate_rule, dict):
                continue
            rate = rate_rule.get("rate")
            if rate and str(rate) in rates and rate_rule.get("value"):
                _add(
                    fields,
                    "invoice_type",
                    rate_rule["value"],
                    "invoice type tax-rate context",
                    float(rate_rule.get("confidence", 0.96)),
                )
                return
        if variant_rule.get("default"):
            _add(
                fields,
                "invoice_type",
                variant_rule["default"],
                "invoice type variant default",
                float(variant_rule.get("confidence", 0.96)),
            )


# 通用坐标兜底：当某 required 明细字段文本流完全没抓到时，按 span 级坐标
# （标签右方 / 表头同列下方）补进 items payload。已有值不碰，真实发票不受影响。
_ITEM_COORD_FALLBACK = {
    "project_name": ("建筑项目名称", r"[一-鿿][一-鿿（）()A-Za-z0-9\-]{1,}", "below"),
    "service_location": ("建筑服务发生地", r"[一-鿿][一-鿿市新区县镇]{1,}", "below"),
}


def _find_value_by_coordinate(spans, label, mode, pattern):
    label_spans = [s for s in spans if label in _compact_text(s["text"])]
    if not label_spans:
        return None
    lb = label_spans[0]
    lb_x1 = lb["x1"]
    lb_cy = (lb["y0"] + lb["y1"]) / 2
    lb_cx = (lb["x0"] + lb["x1"]) / 2
    if mode == "right":
        cands = [
            s for s in spans
            if s["x0"] >= lb_x1 - 3
            and abs((s["y0"] + s["y1"]) / 2 - lb_cy) < 16
            and _compact_text(s["text"]) != _compact_text(lb["text"])
        ]
        cands.sort(key=lambda s: (s["x0"], abs((s["y0"] + s["y1"]) / 2 - lb_cy)))
    else:
        cands = [
            s for s in spans
            if (s["y0"] + s["y1"]) / 2 > lb_cy + 2
            and abs((s["x0"] + s["x1"]) / 2 - lb_cx) < 55
            and _compact_text(s["text"]) != _compact_text(lb["text"])
        ]
        cands.sort(key=lambda s: (s["y0"] + s["y1"]) / 2)
    for s in cands:
        text = s["text"].strip()
        if not text or text.endswith(("：", ":")):
            continue
        if pattern and not re.fullmatch(pattern, text):
            continue
        return text
    return None


def _coordinate_fallback(text_units, fields, schema):
    """文本流抓空的明细行级专属字段，按 span 级坐标补进 items payload。只增不减。"""
    schema_fields = schema.get("fields", {})
    if not isinstance(schema_fields, dict):
        return
    required = {
        name for name, spec in schema_fields.items()
        if isinstance(spec, dict) and spec.get("required") is True
    }
    # 早返回：该 schema 没有任何需要坐标兜底的字段时，不读 PDF（避免对非建筑发票白读）
    if not (required & _ITEM_COORD_FALLBACK.keys()):
        return
    if not text_units.source_file:
        return
    from ._line_item_ocr_table import _read_pdf_spans
    try:
        spans = _read_pdf_spans(text_units.source_file, list(text_units.page_range))
    except Exception:
        return
    spans = [s for s in spans if s.get("text", "").strip()]
    if not spans:
        return
    item_candidates = fields.get("items") or []
    if not item_candidates:
        return
    for field_name, (label, pattern, mode) in _ITEM_COORD_FALLBACK.items():
        if field_name not in required:
            continue
        value = _find_value_by_coordinate(spans, label, mode, pattern)
        if not value:
            continue
        for candidate in item_candidates:
            try:
                payload = json.loads(candidate.value)
            except json.JSONDecodeError:
                continue
            existing = payload.get(field_name)
            if not existing:
                payload[field_name] = value
                candidate.value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            elif field_name == "service_location":
                # 序列法在演示版可能把项目名拼进发生地；现值包含 project_name 时用坐标干净值替换。
                project_name = payload.get("project_name")
                if project_name and project_name in existing and value != existing:
                    payload[field_name] = value
                    candidate.value = json.dumps(payload, ensure_ascii=False, sort_keys=True)



def generate_field_candidates(text_units: TextUnits, decision: SchemaDecision) -> FieldCandidates:
    fields: dict[str, list[FieldCandidate]] = {}
    if decision.decision != SchemaDecisionStatus.MATCHED or not decision.schema_id:
        return FieldCandidates(
            invoice_unit_id=text_units.invoice_unit_id,
            schema_id=decision.schema_id or "unmodeled",
            fields=fields,
        )

    lines = _lines(text_units)
    schema = load_schema(decision.schema_id)
    used_schema_specific = _extract_schema_specific_candidates(text_units, decision, lines, fields, schema)
    used_traditional_vat = False
    if decision.variant_id == "traditional-vat-form":
        used_traditional_vat = _extract_traditional_vat_candidates(text_units, fields, schema)
    party_geometry_rule = _party_geometry_rule(schema, decision)
    if party_geometry_rule:
        party_values = _party_values_from_geometry(text_units, party_geometry_rule)
        for field_name, value in party_values.items():
            _add(fields, field_name, value, f"party geometry {field_name}", 0.84, ["weak_geometry"])
    shared_fallback_fields = _shared_fallback_fields(schema)
    _extract_invoice_type(lines, fields, schema)
    _extract_special_invoice_type(lines, fields, schema)
    if not fields.get("invoice_no"):
        _extract_invoice_number(lines, fields, schema)
    invoice_no_candidate = fields.get("invoice_no", [None])[0]
    if not is_standard_digital_like(
        decision.schema_id,
        decision.variant_id,
        invoice_no_candidate.value if invoice_no_candidate else None,
    ):
        _extract_invoice_code(lines, fields, schema)
    _extract_dates(lines, fields, schema)
    if not used_traditional_vat and (not used_schema_specific or "names_and_tax_ids" in shared_fallback_fields):
        _extract_names_and_tax_ids(lines, fields, schema, text_units)
    _extract_money_totals(lines, fields, schema, text_units)
    _extract_red_invoice_signals(lines, fields)
    if not used_traditional_vat:
        _extract_items_from_text_units(text_units, fields, schema)
        _enrich_special_items(lines, fields, schema)
    _normalize_invoice_type_from_context(lines, fields, decision, schema)
    _add_ocr_confidence_risks(text_units, fields)
    _coordinate_fallback(text_units, fields, schema)

    return FieldCandidates(
        invoice_unit_id=text_units.invoice_unit_id,
        schema_id=decision.schema_id,
        fields=fields,
    )

