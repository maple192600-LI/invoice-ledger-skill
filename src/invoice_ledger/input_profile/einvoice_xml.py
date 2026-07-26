"""A2 通道：独立 XML 文件（总局 EInvoice 格式，如高德随票交付）解析为 InvoiceRecord。

标准库 xml.etree，零新依赖。金额含税口径按官方：TotalAmWithoutTax/TotalTaxAm 不含税，
TotalTax-includedAmount 为价税合计。明细行 IssuItemInformation 逐行映射（含负数折扣行）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET

from ..contracts import (
    InvoiceFields,
    InvoiceItem,
    InvoiceQuality,
    InvoiceRecord,
    InvoiceSource,
    RecognitionStatus,
)

NIL_NS = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _text(root, path: str) -> str | None:
    el = root.find(path)
    if el is None or el.get(NIL_NS) == "true":
        return None
    text = (el.text or "").strip()
    return text or None


def _money(value: str | None):
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _rate_to_percent(rate: str | None) -> str | None:
    if rate is None:
        return None
    try:
        d = Decimal(str(rate))
    except InvalidOperation:
        return None
    pct = (d * Decimal("100")).normalize()
    integral = pct.to_integral_value()
    return f"{integral}%" if integral == pct else f"{pct}%"


def parse_einvoice_xml(text: str, source: InvoiceSource, unit_id: str) -> InvoiceRecord | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    invoice_no = _text(root, ".//EIid") or _text(root, ".//InvoiceNumber")
    invoice_date = (_text(root, ".//IssueTime") or _text(root, ".//RequestTime") or "")[:10] or None

    gos = _text(root, ".//GeneralOrSpecialVAT/LabelName")
    invoice_type = "电子发票（增值税专用发票）" if gos == "增值税专用发票" else "电子发票（普通发票）"

    items: list[InvoiceItem] = []
    for index, el in enumerate(root.findall(".//IssuItemInformation"), start=1):
        if el.get(NIL_NS) == "true":
            continue
        items.append(
            InvoiceItem(
                line_no=index,
                item_name=_text(el, "ItemName"),
                quantity=_text(el, "Quantity"),
                unit_price=_text(el, "UnPrice"),
                line_amount=_money(_text(el, "Amount")),
                tax_rate=_rate_to_percent(_text(el, "TaxRate")),
                line_tax_amount=_money(_text(el, "ComTaxAm")),
                line_total_with_tax=_money(_text(el, "TotaltaxIncludedAmount")),
            )
        )

    return InvoiceRecord(
        invoice_unit_id=unit_id,
        schema_id="standard-invoice",
        variant_id="digital-invoice-form",
        source=source,
        invoice=InvoiceFields(
            invoice_no=invoice_no,
            invoice_date=invoice_date,
            buyer_name=_text(root, ".//BuyerName"),
            buyer_tax_id=_text(root, ".//BuyerIdNum"),
            seller_name=_text(root, ".//SellerName"),
            seller_tax_id=_text(root, ".//SellerIdNum"),
            invoice_type=invoice_type,
            amount_total=_money(_text(root, ".//TotalAmWithoutTax")),
            tax_total=_money(_text(root, ".//TotalTaxAm")),
            total_with_tax=_money(_text(root, ".//TotalTax-includedAmount")),
        ),
        items=items,
        quality=InvoiceQuality(
            status=RecognitionStatus.READY,
            confidence=0.95,
            remark="数据取自总局电子发票 XML 结构化数据。",
            data_source="structured",
        ),
    )
