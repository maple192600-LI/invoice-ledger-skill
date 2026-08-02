"""Build user-facing recognition notices for the template issue sheet."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha1
from pathlib import Path
from typing import Any

from ..contracts import LedgerRow, RecognitionNotice, RecognitionStatus, normalize_amount
from ..validation.review_notes import user_review_issue, user_review_remark


def page_text(page_range: list[int]) -> str:
    pages = sorted({int(page) for page in page_range if page})
    if not pages:
        return "未识别页码"
    if len(pages) == 1:
        return f"第 {pages[0]} 页"
    return f"第 {pages[0]}-{pages[-1]} 页"


def _notice_id(invoice_unit_id: str, issue_type: str, notice_key: str | None) -> str:
    digest = sha1(f"{invoice_unit_id}|{issue_type}|{notice_key or ''}".encode("utf-8")).hexdigest()[:20]
    return f"notice_{digest}"


def _display_file_name(source_file: str) -> str:
    return Path(source_file).name


def _review_action(row: LedgerRow) -> str:
    text = str(row.review_remark or row.remark or "").strip()
    return user_review_remark(text) if text else "请根据原始发票核对该行识别结果。"


def _invoice_no(row: LedgerRow) -> str | None:
    invoice_no = str(row.invoice_no or "").strip()
    return invoice_no or None


def _amount_total(row: LedgerRow) -> Decimal | None:
    return normalize_amount(row.invoice_total_with_tax or row.line_total_with_tax)


def _existing_location(existing_row: dict[str, Any] | None, row: LedgerRow) -> str:
    if existing_row and existing_row.get("excel_row"):
        return f"采集表第 {existing_row['excel_row']} 行"
    if _invoice_no(row):
        return "采集表按发票号码搜索"
    return "采集表按文件名或金额搜索"


def _duplicate_notice_id(row: LedgerRow) -> str:
    key = row.invoice_key or row.invoice_unit_id or row.invoice_no or row.source_file
    return _notice_id(str(key), "疑似重复", row.invoice_no)


def duplicate_notice_from_ledger_row(
    row: LedgerRow,
    existing_row: dict[str, Any] | None = None,
) -> RecognitionNotice:
    issue_type = "疑似重复"
    pages = page_text(row.page_range)
    file_name = _display_file_name(row.source_file)
    return RecognitionNotice(
        notice_id=_duplicate_notice_id(row),
        source_file=file_name,
        page_range=row.page_range,
        page_text=pages,
        severity="未写入",
        issue_type=issue_type,
        invoice_no=_invoice_no(row),
        amount_total=_amount_total(row),
        check_location=_existing_location(existing_row, row),
        action="确认重复可忽略；不是重复请手工补录或重新导入",
        invoice_unit_id=row.invoice_unit_id,
    )


def _notice_from_ledger_row(row: LedgerRow) -> RecognitionNotice:
    issue_type = user_review_issue(row.review_remark or row.remark)
    pages = page_text(row.page_range)
    suggestion = _review_action(row)
    return RecognitionNotice(
        notice_id=_notice_id(row.invoice_unit_id, issue_type, None),
        source_file=_display_file_name(row.source_file),
        page_range=row.page_range,
        page_text=pages,
        severity="需复核",
        issue_type=issue_type,
        invoice_no=_invoice_no(row),
        amount_total=_amount_total(row),
        check_location="采集表已写入行",
        action=suggestion,
        invoice_unit_id=row.invoice_unit_id,
    )


def _unit_issue(record_status: RecognitionStatus, unit_status: RecognitionStatus) -> tuple[str, str, str]:
    if unit_status != RecognitionStatus.READY:
        return (
            "未写入",
            "无法生成台账记录",
            "请确认文件清晰、完整并且属于支持的发票格式；仍无法识别时，请手工补录。",
        )
    if record_status == RecognitionStatus.UNMODELED:
        return "未写入", "暂不支持此票种", "请确认票据类型；需要入账时，请手工补录。"
    if record_status == RecognitionStatus.FAILED:
        return (
            "未写入",
            "识别失败",
            "请确认原文件内容完整、可以正常打开；仍无法识别时，请手工补录。",
        )
    return "需复核", "识别结果需确认", "请对照原票核对识别结果。"


def _notice_from_unit_result(unit_result: dict[str, Any]) -> RecognitionNotice | None:
    invoice_record = unit_result["invoice_record"]
    invoice_unit = unit_result["invoice_unit"]
    status = invoice_record.quality.status
    if status not in {RecognitionStatus.FAILED, RecognitionStatus.UNMODELED} and invoice_unit.status == RecognitionStatus.READY:
        return None

    severity, issue_type, suggestion = _unit_issue(status, invoice_unit.status)
    pages = page_text(invoice_unit.page_range or invoice_record.source.page_range)

    return RecognitionNotice(
        notice_id=_notice_id(invoice_unit.invoice_unit_id, issue_type, None),
        source_file=_display_file_name(invoice_unit.source_file),
        page_range=invoice_unit.page_range,
        page_text=pages,
        severity=severity,
        issue_type=issue_type,
        invoice_no=invoice_record.invoice.invoice_no,
        amount_total=invoice_record.invoice.total_with_tax,
        check_location=f"原文件{pages}",
        action=suggestion,
        invoice_unit_id=invoice_unit.invoice_unit_id,
    )


def build_recognition_notices(
    unit_results: list[dict[str, Any]],
    ledger_rows: list[LedgerRow],
) -> list[RecognitionNotice]:
    review_rows: dict[str, LedgerRow] = {}
    for row in ledger_rows:
        if row.recognition_status != RecognitionStatus.REVIEW_REQUIRED and not row.review_remark:
            continue
        existing = review_rows.get(row.invoice_unit_id)
        if existing is None:
            review_rows[row.invoice_unit_id] = row
            continue
        combined = "；".join(
            dict.fromkeys(
                part
                for part in (existing.review_remark, row.review_remark)
                if part
            )
        )
        review_rows[row.invoice_unit_id] = existing.model_copy(update={"review_remark": combined})

    notices = [_notice_from_ledger_row(row) for row in review_rows.values()]
    noticed_units = {notice.invoice_unit_id for notice in notices}
    for unit_result in unit_results:
        invoice_unit = unit_result["invoice_unit"]
        if invoice_unit.invoice_unit_id in noticed_units:
            continue
        notice = _notice_from_unit_result(unit_result)
        if notice is not None:
            notices.append(notice)
            noticed_units.add(notice.invoice_unit_id)
    return notices
