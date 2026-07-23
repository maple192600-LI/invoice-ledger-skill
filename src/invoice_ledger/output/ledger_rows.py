"""Draft ledger row expansion for the invoice draft ledger pipeline."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha1
from pathlib import Path

from ..contracts import InvoiceItem, InvoiceRecord, LedgerRow, RecognitionStatus, normalize_amount
from ..validation.review_notes import user_review_remark


def _stable_hash(parts: list[object]) -> str:
    text = "|".join("" if part is None else str(part) for part in parts)
    return sha1(text.encode("utf-8")).hexdigest()[:20]


def _invoice_key(record: InvoiceRecord) -> str:
    invoice = record.invoice
    return "inv_" + _stable_hash(
        [
            invoice.seller_tax_id,
            invoice.invoice_no,
            invoice.invoice_date,
            invoice.total_with_tax,
        ]
    )


def _line_key(record: InvoiceRecord, item: InvoiceItem) -> str:
    return "line_" + _stable_hash(
        [
            _invoice_key(record),
            item.line_no,
            item.item_name,
            item.line_amount,
            item.line_tax_amount,
        ]
    )


def _display_file_name(source_file: str) -> str:
    return Path(source_file).name


def _processed_date(processed_at: str) -> str:
    return processed_at


def _line_total_with_tax(item: InvoiceItem) -> Decimal | None:
    if item.line_total_with_tax is not None:
        return item.line_total_with_tax
    if item.line_amount is None or item.line_tax_amount is None:
        return None
    return normalize_amount(item.line_amount + item.line_tax_amount)


def _sum_money(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return normalize_amount(sum(values, Decimal("0.00")))


def _money_diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return normalize_amount(left - right)


def _reconciliation_issue(
    summary_label: str,
    detail_label: str,
    summary_value: Decimal | None,
    detail_value: Decimal | None,
) -> str | None:
    diff = _money_diff(detail_value, summary_value)
    if diff is None:
        return f"待复核：{summary_label}或{detail_label}缺失"
    if diff == Decimal("0.00"):
        return None
    return (
        f"待复核：{summary_label}与{detail_label}不一致，"
        f"{summary_label} {summary_value:.2f}，"
        f"明细合计 {detail_value:.2f}，"
        f"差额 {diff:.2f}"
    )


def _reconciliation(record: InvoiceRecord) -> tuple[str, str]:
    invoice = record.invoice
    detail_amount = _sum_money([item.line_amount for item in record.items])
    detail_tax = _sum_money([item.line_tax_amount for item in record.items])
    detail_total = _sum_money([_line_total_with_tax(item) for item in record.items])
    checks = [
        ("汇总金额", "明细金额合计", invoice.amount_total, detail_amount),
        ("汇总税额", "明细税额合计", invoice.tax_total, detail_tax),
        ("汇总价税合计", "明细价税合计", invoice.total_with_tax, detail_total),
    ]

    issues: list[str] = []
    for summary_label, detail_label, summary_value, detail_value in checks:
        issue = _reconciliation_issue(summary_label, detail_label, summary_value, detail_value)
        if issue:
            issues.append(issue)

    if issues:
        return "需复核", "；".join(issues)
    return "通过", ""


def _summary_item_name(record: InvoiceRecord) -> str | None:
    if len(record.items) == 1:
        return record.items[0].item_name
    for item in record.items:
        item_name = item.item_name.strip() if item.item_name else ""
        if item_name:
            return f"{item_name}等{len(record.items)}项"
    return f"发票明细等{len(record.items)}项"


def _single_item(record: InvoiceRecord) -> InvoiceItem | None:
    return record.items[0] if len(record.items) == 1 else None


def _summary_tax_rate(record: InvoiceRecord, single_item: InvoiceItem | None) -> str | None:
    if single_item:
        return single_item.tax_rate
    contributing_items = [
        item
        for item in record.items
        if (_line_total_with_tax(item) or item.line_amount or Decimal("0.00")) != Decimal("0.00")
    ]
    if not contributing_items:
        return None
    if any(not item.tax_rate for item in contributing_items):
        return None
    all_rates = {item.tax_rate for item in contributing_items}
    if len(all_rates) == 1:
        return next(iter(all_rates))
    return None


def _item_context_remark(item: InvoiceItem) -> str:
    parts: list[str] = []
    if item.project_name:
        parts.append(f"项目名称：{item.project_name}")
    return "；".join(parts)


def _base_values(
    record: InvoiceRecord,
    run_id: str,
    processed_at: str,
    status: RecognitionStatus,
    remark: str,
) -> dict[str, object]:
    invoice = record.invoice
    invoice_key = _invoice_key(record)
    return {
        "run_id": run_id,
        "source_file": _display_file_name(record.source.source_file),
        "page_range": record.source.page_range,
        "invoice_unit_id": record.invoice_unit_id,
        "schema_id": record.schema_id,
        "variant_id": record.variant_id,
        "invoice_key": invoice_key,
        "processed_at": _processed_date(processed_at),
        "invoice_code": invoice.invoice_code,
        "invoice_no": invoice.invoice_no,
        "invoice_date": invoice.invoice_date,
        "buyer_name": invoice.buyer_name,
        "buyer_tax_id": invoice.buyer_tax_id,
        "seller_name": invoice.seller_name,
        "seller_tax_id": invoice.seller_tax_id,
        "invoice_type": invoice.invoice_type,
        "is_positive_invoice": "否" if (invoice.total_with_tax is not None and invoice.total_with_tax < 0) else "是",
        "invoice_amount_total": invoice.amount_total,
        "invoice_tax_total": invoice.tax_total,
        "invoice_total_with_tax": invoice.total_with_tax,
        "recognition_status": status,
        "confidence": record.quality.confidence,
        "remark": remark,
    }


def build_ledger_rows(
    record: InvoiceRecord,
    run_id: str,
    processed_at: str,
) -> list[LedgerRow]:
    if record.quality.status in {RecognitionStatus.UNMODELED, RecognitionStatus.FAILED}:
        return []

    rows: list[LedgerRow] = []
    status = record.quality.status
    review_remark = "" if status == RecognitionStatus.READY else user_review_remark(record.quality.remark)
    if not record.invoice.invoice_no:
        status = RecognitionStatus.REVIEW_REQUIRED
        if "invoice_no" not in record.quality.remark and "发票号码" not in review_remark:
            review_remark = "；".join(part for part in [review_remark, user_review_remark("missing invoice_no")] if part)

    if record.items:
        row_items = record.items
        reconciliation_status, reconciliation_remark = _reconciliation(record)
    else:
        row_items = [
            InvoiceItem(
                line_no=1,
                item_name=None,
                line_amount=record.invoice.amount_total,
                line_tax_amount=record.invoice.tax_total,
                line_total_with_tax=record.invoice.total_with_tax,
            )
        ]
        reconciliation_status = "需复核"
        reconciliation_remark = "待复核：明细缺失"

    base_review_remark = "；".join(part for part in [review_remark, reconciliation_remark] if part)
    row_status = RecognitionStatus.REVIEW_REQUIRED if reconciliation_status == "需复核" else status
    for item in row_items:
        context_remark = _item_context_remark(item)
        row_remark = "；".join(part for part in [base_review_remark, context_remark] if part)
        detail_base = _base_values(record, run_id, processed_at, row_status, row_remark)
        invoice_line_key = _line_key(record, item)
        draft_row_id = "draft_" + _stable_hash([run_id, record.invoice_unit_id, item.line_no])
        rows.append(
            LedgerRow(
                row_type="明细",
                draft_row_id=draft_row_id,
                invoice_line_key=invoice_line_key,
                line_no=item.line_no,
                item_name=item.item_name,
                spec_model=item.spec_model,
                unit=item.unit,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_amount=item.line_amount,
                tax_rate=item.tax_rate,
                line_tax_amount=item.line_tax_amount,
                line_total_with_tax=_line_total_with_tax(item),
                reconciliation_status=reconciliation_status,
                review_remark=base_review_remark,
                context_remark=context_remark,
                **detail_base,
            )
        )
    return rows
