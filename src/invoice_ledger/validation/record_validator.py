"""Invoice record validation for the invoice draft ledger pipeline."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..contracts import InvoiceFields, InvoiceItem, InvoiceRecord, RecognitionStatus
from ..parsing.invoice_identity import has_standard_digital_invoice_number, is_standard_digital_like
from ..schema.schema_loader import load_schema


def _close(left: Decimal | None, right: Decimal | None, tolerance: Decimal = Decimal("0.02")) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def _required_schema_fields(schema: dict[str, Any], variant_id: str | None = None) -> list[str]:
    fields = schema.get("fields", {})
    if not isinstance(fields, dict):
        return []
    required = [
        field_name
        for field_name, spec in fields.items()
        if isinstance(spec, dict) and spec.get("required") is True
    ]
    variant_required = schema.get("variant_required_fields", {})
    if isinstance(variant_required, dict) and variant_id in variant_required:
        required.extend(str(field_name) for field_name in variant_required[variant_id])
    elif (
        isinstance(variant_required, dict)
        and variant_id is None
        and schema.get("schema_id") == "standard-invoice"
        and "digital-invoice-form" in variant_required
    ):
        required.extend(str(field_name) for field_name in variant_required["digital-invoice-form"])
    return list(dict.fromkeys(required))


def _field_value(invoice: InvoiceFields, items: list[InvoiceItem], field_name: str) -> object:
    if field_name == "items":
        return items
    if hasattr(invoice, field_name):
        return getattr(invoice, field_name)
    item_values = [getattr(item, field_name) for item in items if hasattr(item, field_name)]
    return item_values if item_values else None


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return True


def _check_invoice_total(record: InvoiceRecord) -> list[str]:
    invoice = record.invoice
    if not _close(
        (invoice.amount_total + invoice.tax_total)
        if invoice.amount_total is not None and invoice.tax_total is not None
        else None,
        invoice.total_with_tax,
    ):
        return ["amount_total + tax_total != total_with_tax"]
    return []


def _check_amount_completeness(record: InvoiceRecord) -> list[str]:
    invoice = record.invoice
    amount_fields = [
        ("amount_total", invoice.amount_total),
        ("tax_total", invoice.tax_total),
        ("total_with_tax", invoice.total_with_tax),
    ]
    present_count = sum(value is not None for _, value in amount_fields)
    if present_count in {0, len(amount_fields)}:
        return []
    missing = [name for name, value in amount_fields if value is None]
    return [f"incomplete amount breakdown: missing {', '.join(missing)}"]


def _check_line_totals(record: InvoiceRecord) -> list[str]:
    issues: list[str] = []
    for item in record.items:
        line_total = (
            item.line_amount + item.line_tax_amount
            if item.line_amount is not None and item.line_tax_amount is not None
            else None
        )
        if not _close(line_total, item.line_total_with_tax):
            issues.append(f"line {item.line_no}: line_amount + line_tax_amount != line_total_with_tax")
    return issues


def _check_sum_line_amount(record: InvoiceRecord) -> list[str]:
    item_amount_sum = sum((item.line_amount or Decimal("0.00")) for item in record.items)
    if not _close(item_amount_sum, record.invoice.amount_total):
        return ["sum line_amount != amount_total"]
    return []


def _check_sum_line_tax_amount(record: InvoiceRecord) -> list[str]:
    item_tax_sum = sum((item.line_tax_amount or Decimal("0.00")) for item in record.items)
    if not _close(item_tax_sum, record.invoice.tax_total):
        return ["sum line_tax_amount != tax_total"]
    return []


AMOUNT_CHECKS = {
    "amount_completeness_check": _check_amount_completeness,
    "amount_total_plus_tax_total_equals_total_with_tax": _check_invoice_total,
    "line_amount_plus_line_tax_amount_equals_line_total_with_tax": _check_line_totals,
    "sum_line_amount_equals_amount_total": _check_sum_line_amount,
    "sum_line_tax_amount_equals_tax_total": _check_sum_line_tax_amount,
}


LOW_CONFIDENCE_FLOOR = Decimal("0.60")


def _requires_field_evidence(schema: dict[str, Any]) -> bool:
    validation = schema.get("validation", {})
    return isinstance(validation, dict) and validation.get("require_field_evidence") is True


def _decision_confidence(decision: dict[str, Any]) -> Decimal | None:
    value = decision.get("top_confidence")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _field_decision_issues(record: InvoiceRecord, required_fields: list[str], require_missing_evidence: bool) -> list[str]:
    issues: list[str] = []
    decisions = record.quality.field_decisions
    if not decisions:
        if require_missing_evidence:
            for field_name in required_fields:
                if _has_value(_field_value(record.invoice, record.items, field_name)):
                    issues.append(f"missing evidence {field_name}")
        return issues
    fields_to_check = list(required_fields)
    fields_to_check.extend(
        field_name
        for field_name in decisions
        if field_name not in required_fields and _has_value(_field_value(record.invoice, record.items, field_name))
    )
    for field_name in fields_to_check:
        value = _field_value(record.invoice, record.items, field_name)
        if not _has_value(value):
            continue
        decision = decisions.get(field_name)
        if not decision:
            if require_missing_evidence:
                issues.append(f"missing evidence {field_name}")
            continue
        risks = {str(risk) for risk in decision.get("risks", [])}
        evidence = str(decision.get("evidence") or "").strip()
        if not evidence or "missing_evidence" in risks:
            issues.append(f"missing evidence {field_name}")
        if decision.get("conflict") is True or "conflict" in risks:
            issues.append(f"conflict {field_name}")
        confidence = _decision_confidence(decision)
        if confidence is not None and confidence < LOW_CONFIDENCE_FLOOR:
            issues.append(f"low confidence {field_name}")
        elif "low_confidence" in risks or "low_confidence_ocr" in risks:
            issues.append(f"low confidence {field_name}")
    return issues


def _variant_identity_issues(record: InvoiceRecord) -> list[str]:
    if (
        record.schema_id == "standard-invoice"
        and record.variant_id == "digital-invoice-form"
        and not has_standard_digital_invoice_number(
            record.schema_id,
            record.variant_id,
            record.invoice.invoice_no,
        )
    ):
        return ["digital invoice number invalid"]
    if (
        is_standard_digital_like(
            record.schema_id,
            record.variant_id,
            record.invoice.invoice_no,
        )
        and _has_value(record.invoice.invoice_code)
    ):
        return ["digital invoice has invoice_code"]
    return []


def validate_invoice_record(record: InvoiceRecord, schema: dict[str, Any] | None = None) -> InvoiceRecord:
    if record.quality.status == RecognitionStatus.UNMODELED:
        return record

    active_schema = schema or load_schema(record.schema_id or "standard-invoice")
    issues: list[str] = []
    if record.quality.status == RecognitionStatus.REVIEW_REQUIRED and record.quality.remark.startswith("待复核："):
        issues.append(record.quality.remark)

    required_fields = _required_schema_fields(active_schema, record.variant_id)
    for field_name in required_fields:
        if not _has_value(_field_value(record.invoice, record.items, field_name)):
            issues.append(f"missing {field_name}")
    issues.extend(_field_decision_issues(record, required_fields, _requires_field_evidence(active_schema)))
    issues.extend(_variant_identity_issues(record))

    line_table = active_schema.get("line_table", {})
    if isinstance(line_table, dict) and line_table.get("required") is True and not record.items:
        issues.append("missing items")

    amount_checks = active_schema.get("amount_checks", [])
    if isinstance(amount_checks, list):
        for check_name in amount_checks:
            checker = AMOUNT_CHECKS.get(str(check_name))
            if checker:
                issues.extend(checker(record))

    if issues:
        record.quality.status = RecognitionStatus.REVIEW_REQUIRED
        record.quality.confidence = min(record.quality.confidence, 0.7)
        record.quality.remark = "; ".join(issues)
    else:
        record.quality.status = RecognitionStatus.READY
        record.quality.confidence = max(record.quality.confidence, 0.9)
        record.quality.remark = "通过当前字段校验。"

    return record
