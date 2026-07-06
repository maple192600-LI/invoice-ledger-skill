"""Shared invoice identity helpers used across extraction, validation, and writing."""

from __future__ import annotations


def is_standard_digital_like(
    schema_id: str | None,
    variant_id: str | None,
    invoice_no: object,
    invoice_code: object = None,
) -> bool:
    if not has_standard_digital_invoice_number(schema_id, variant_id, invoice_no):
        return False
    if variant_id == "digital-invoice-form":
        return True
    code = str(invoice_code or "").strip()
    return code == ""


def has_standard_digital_invoice_number(
    schema_id: str | None,
    variant_id: str | None,
    invoice_no: object,
) -> bool:
    if schema_id != "standard-invoice":
        return False
    if variant_id is not None:
        return variant_id == "digital-invoice-form" and _is_standard_digital_invoice_number(invoice_no)
    return _is_standard_digital_invoice_number(invoice_no)


def _is_standard_digital_invoice_number(invoice_no: object) -> bool:
    number = str(invoice_no or "").strip()
    return number.isdigit() and len(number) == 20
