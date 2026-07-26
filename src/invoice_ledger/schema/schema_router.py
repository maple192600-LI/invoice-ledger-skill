"""Schema routing for invoice document types."""

from __future__ import annotations

from typing import Any

from ..contracts import SchemaDecision, SchemaDecisionStatus, TextUnits
from .schema_loader import load_schema, load_schema_catalog


def _joined_text(text_units: TextUnits) -> str:
    return "\n".join(unit.text for unit in text_units.units)


def _schema_match_score(text: str, schema: dict[str, Any]) -> int:
    rules = schema.get("match_rules", {})
    if not isinstance(rules, dict):
        return 0

    exclude_any = [str(term) for term in rules.get("exclude_any", [])]
    if any(term and term in text for term in exclude_any):
        return 0

    required_all = [str(term) for term in rules.get("required_all", [])]
    if required_all and not all(term in text for term in required_all):
        return 0

    required_any = [str(term) for term in rules.get("required_any", [])]
    any_score = sum(1 for term in required_any if term and term in text)
    min_required_any = int(rules.get("min_required_any", 1 if required_any else 0))
    if any_score < min_required_any:
        return 0

    return len(required_all) + any_score


def _variant_id(schema: dict[str, Any], text: str) -> str | None:
    variants = schema.get("variants", [])
    for candidate_variant, spec in schema.get("variant_rules", {}).items():
        if any(keyword in text for keyword in spec.get("keywords", [])):
            return str(candidate_variant)
    configured_default = schema.get("default_variant")
    if configured_default:
        return str(configured_default)
    if isinstance(variants, list) and len(variants) == 1:
        return str(variants[0])
    return None


def _decide_catalog_schema(text_units: TextUnits, text: str) -> SchemaDecision | None:
    best_match: tuple[int, str, dict[str, Any]] | None = None
    for entry in load_schema_catalog():
        schema_id = entry.get("schema_id")
        if not schema_id or schema_id == "standard-invoice":
            continue
        schema = load_schema(str(schema_id))
        score = _schema_match_score(text, schema)
        if score <= 0:
            continue
        if best_match is None or score > best_match[0]:
            best_match = (score, str(schema_id), schema)
    if best_match is None:
        return None

    score, schema_id, schema = best_match
    variant_id = _variant_id(schema, text)
    return SchemaDecision(
        invoice_unit_id=text_units.invoice_unit_id,
        schema_id=schema_id,
        variant_id=variant_id,
        confidence=min(0.99, 0.55 + score * 0.08),
        decision=SchemaDecisionStatus.MATCHED,
        reason=[f"匹配 {schema.get('name', schema_id)} 票面结构关键词。"],
    )


def decide_schema(text_units: TextUnits) -> SchemaDecision:
    text = _joined_text(text_units)
    catalog_decision = _decide_catalog_schema(text_units, text)
    if catalog_decision:
        return catalog_decision

    schema = load_schema("standard-invoice")
    reasons: list[str] = []

    unsupported_rules = schema.get("match_rules", {}).get("unsupported_current", [])
    for unsupported in unsupported_rules:
        keyword = unsupported.get("keyword")
        if keyword and keyword in text:
            return SchemaDecision(
                invoice_unit_id=text_units.invoice_unit_id,
                schema_id=None,
                variant_id=None,
                confidence=0.8,
                decision=SchemaDecisionStatus.UNMODELED,
                reason=[unsupported.get("reason") or "当前票种未建模。"],
            )

    standard_signals = schema.get("match_rules", {}).get("required_any", [])
    score = sum(1 for signal in standard_signals if signal in text)
    if score < 2:
        return SchemaDecision(
            invoice_unit_id=text_units.invoice_unit_id,
            schema_id=None,
            variant_id=None,
            confidence=0.0,
            decision=SchemaDecisionStatus.UNMODELED,
            reason=["未匹配标准发票类关键结构。"],
        )

    variant_id = _variant_id(schema, text)
    if variant_id:
        reasons.append(f"匹配 {variant_id} 票面结构关键词。")
    else:
        reasons.append("匹配标准发票结构关键词，未匹配具体票面形态。")

    return SchemaDecision(
        invoice_unit_id=text_units.invoice_unit_id,
        schema_id="standard-invoice",
        variant_id=variant_id,
        confidence=min(0.99, 0.45 + score * 0.12),
        decision=SchemaDecisionStatus.MATCHED,
        reason=reasons,
    )
