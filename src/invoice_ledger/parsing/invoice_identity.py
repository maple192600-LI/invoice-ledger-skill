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
    # T2.6: 按号码形态判断（20 位纯数字即数电号），与 schema 解耦——
    # 航空/铁路等任意票种的 20 位号码都入"数电发票号码"列。
    # 保留 schema_id/variant_id 入参仅为调用点签名兼容。
    return _is_standard_digital_invoice_number(invoice_no)


def _is_standard_digital_invoice_number(invoice_no: object) -> bool:
    number = str(invoice_no or "").strip()
    return number.isdigit() and len(number) == 20
