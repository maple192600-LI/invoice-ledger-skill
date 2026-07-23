"""Field decision logic for the invoice draft ledger pipeline."""

from __future__ import annotations

import json

from ..contracts import (
    FieldCandidate,
    FieldCandidates,
    InvoiceFields,
    InvoiceItem,
    InvoiceQuality,
    InvoiceRecord,
    InvoiceSource,
    InvoiceUnit,
    RecognitionStatus,
    SchemaDecision,
    SchemaDecisionStatus,
)


CONFLICT_CONFIDENCE_FLOOR = 0.85
CONFLICT_CONFIDENCE_DELTA = 0.05


def _decision(field_name: str, candidates: list[FieldCandidate] | None) -> tuple[str | None, dict[str, object] | None]:
    if not candidates:
        return None, None
    ranked = sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)
    selected = ranked[0]
    conflicting_values = [
        candidate.value
        for candidate in ranked[1:]
        if candidate.value != selected.value
        and selected.confidence >= CONFLICT_CONFIDENCE_FLOOR
        and candidate.confidence >= CONFLICT_CONFIDENCE_FLOOR
        and abs(selected.confidence - candidate.confidence) <= CONFLICT_CONFIDENCE_DELTA
    ]
    risks = list(selected.risk)
    if conflicting_values and "conflict" not in risks:
        risks.append("conflict")
    if not selected.evidence.strip() and "missing_evidence" not in risks:
        risks.append("missing_evidence")
    if any(risk.startswith("low_confidence") for risk in selected.risk) and "low_confidence" not in risks:
        risks.append("low_confidence")
    return selected.value, {
        "field": field_name,
        "selected_value": selected.value,
        "candidate_count": len(ranked),
        "top_confidence": selected.confidence,
        "evidence": selected.evidence,
        "source": selected.source,
        "conflict": bool(conflicting_values),
        "conflicting_values": conflicting_values,
        "risks": risks,
    }


def _best(field_name: str, candidates: list[FieldCandidate] | None, decisions: dict[str, dict[str, object]]) -> str | None:
    value, decision = _decision(field_name, candidates)
    if decision is not None:
        decisions[field_name] = decision
    return value


def _items_decision(candidates: list[FieldCandidate] | None) -> dict[str, object] | None:
    if not candidates:
        return None
    risks: list[str] = []
    for candidate in candidates:
        for risk in candidate.risk:
            if risk not in risks:
                risks.append(risk)
    if any(risk.startswith("low_confidence") for risk in risks) and "low_confidence" not in risks:
        risks.append("low_confidence")
    evidences = [candidate.evidence for candidate in candidates if candidate.evidence.strip()]
    return {
        "field": "items",
        "selected_value": f"{len(candidates)} item candidate(s)",
        "candidate_count": len(candidates),
        "top_confidence": min(candidate.confidence for candidate in candidates),
        "evidence": "; ".join(evidences),
        "source": "rule",
        "conflict": False,
        "conflicting_values": [],
        "risks": risks,
    }


def _resolve_items(candidates: FieldCandidates, decisions: dict[str, dict[str, object]]) -> list[InvoiceItem]:
    items: list[InvoiceItem] = []
    item_candidates = candidates.fields.get("items", [])
    item_decision = _items_decision(item_candidates)
    if item_decision is not None:
        decisions["items"] = item_decision
    for candidate in item_candidates:
        try:
            payload = json.loads(candidate.value)
        except json.JSONDecodeError:
            continue
        items.append(
            InvoiceItem(
                line_no=int(payload.get("line_no") or len(items) + 1),
                item_name=payload.get("item_name"),
                service_location=payload.get("service_location"),
                project_name=payload.get("project_name"),
                spec_model=payload.get("spec_model"),
                unit=payload.get("unit"),
                quantity=payload.get("quantity"),
                unit_price=payload.get("unit_price"),
                line_amount=payload.get("line_amount"),
                tax_rate=payload.get("tax_rate"),
                line_tax_amount=payload.get("line_tax_amount"),
                line_total_with_tax=payload.get("line_total_with_tax"),
            )
        )
    return items


def resolve_invoice_record(
    unit: InvoiceUnit,
    decision: SchemaDecision,
    candidates: FieldCandidates,
) -> InvoiceRecord:
    if decision.decision != SchemaDecisionStatus.MATCHED:
        return InvoiceRecord(
            invoice_unit_id=unit.invoice_unit_id,
            schema_id=decision.schema_id,
            variant_id=decision.variant_id,
            source=InvoiceSource(source_file=unit.source_file, page_range=unit.page_range),
            invoice=InvoiceFields(),
            items=[],
            quality=InvoiceQuality(
                status=RecognitionStatus.UNMODELED,
                confidence=decision.confidence,
                remark="未匹配当前已建模票种。",
            ),
        )

    field_decisions: dict[str, dict[str, object]] = {}
    invoice = InvoiceFields(
        invoice_code=_best("invoice_code", candidates.fields.get("invoice_code"), field_decisions),
        invoice_no=_best("invoice_no", candidates.fields.get("invoice_no"), field_decisions),
        invoice_date=_best("invoice_date", candidates.fields.get("invoice_date"), field_decisions),
        buyer_name=_best("buyer_name", candidates.fields.get("buyer_name"), field_decisions),
        buyer_tax_id=_best("buyer_tax_id", candidates.fields.get("buyer_tax_id"), field_decisions),
        seller_name=_best("seller_name", candidates.fields.get("seller_name"), field_decisions),
        seller_tax_id=_best("seller_tax_id", candidates.fields.get("seller_tax_id"), field_decisions),
        invoice_type=_best("invoice_type", candidates.fields.get("invoice_type"), field_decisions),
        amount_total=_best("amount_total", candidates.fields.get("amount_total"), field_decisions),
        tax_total=_best("tax_total", candidates.fields.get("tax_total"), field_decisions),
        total_with_tax=_best("total_with_tax", candidates.fields.get("total_with_tax"), field_decisions),
    )
    items = _resolve_items(candidates, field_decisions)
    confidence_values = [
        candidate.confidence
        for field_candidates in candidates.fields.values()
        for candidate in field_candidates
    ]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return InvoiceRecord(
        invoice_unit_id=unit.invoice_unit_id,
        schema_id=decision.schema_id,
        variant_id=decision.variant_id,
        source=InvoiceSource(source_file=unit.source_file, page_range=unit.page_range),
        invoice=invoice,
        items=items,
        quality=InvoiceQuality(
            status=RecognitionStatus.REVIEW_REQUIRED,
            confidence=round(confidence, 4),
            remark="字段候选已决策，尚未通过字段校验。",
            field_decisions=field_decisions,
        ),
    )
