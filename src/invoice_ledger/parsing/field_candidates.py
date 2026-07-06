"""字段候选生成入口：调度通用字段抽取与票种特定策略。"""

from __future__ import annotations

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

from ._helpers import _add, _add_ocr_confidence_risks, _joined, _lines, _schema_section
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
    if not used_traditional_vat:
        _extract_items_from_text_units(text_units, fields, schema)
    _normalize_invoice_type_from_context(lines, fields, decision, schema)
    _add_ocr_confidence_risks(text_units, fields)

    return FieldCandidates(
        invoice_unit_id=text_units.invoice_unit_id,
        schema_id=decision.schema_id,
        fields=fields,
    )

