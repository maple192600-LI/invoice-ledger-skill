"""Build user-facing recognition notices for the template issue sheet."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path
from typing import Any

from ..contracts import LedgerRow, RecognitionNotice, RecognitionStatus
from ..validation.review_notes import user_review_remark


def page_text(page_range: list[int]) -> str:
    pages = sorted({int(page) for page in page_range if page})
    if not pages:
        return "未识别页码"
    if len(pages) == 1:
        return f"第 {pages[0]} 页"
    return f"第 {pages[0]}-{pages[-1]} 页"


def _notice_id(invoice_unit_id: str, issue_type: str, issue_field: str | None) -> str:
    digest = sha1(f"{invoice_unit_id}|{issue_type}|{issue_field or ''}".encode("utf-8")).hexdigest()[:20]
    return f"notice_{digest}"


def _display_file_name(source_file: str) -> str:
    return Path(source_file).name


def _review_suggestion(row: LedgerRow) -> str:
    parts = [row.review_remark, row.remark]
    text = "；".join(str(part).strip() for part in parts if str(part or "").strip())
    return user_review_remark(text) if text else "请根据原始发票核对该行识别结果。"


def _invoice_no_text(row: LedgerRow) -> str:
    invoice_no = str(row.invoice_no or "").strip()
    return f"发票号码：{invoice_no}" if invoice_no else "发票号码：未识别"


def _row_content_text(row: LedgerRow) -> str:
    parts = []
    if row.item_name:
        parts.append(f"项目：{row.item_name}")
    if row.line_amount is not None:
        parts.append(f"金额：{row.line_amount}")
    if row.line_total_with_tax is not None:
        parts.append(f"价税合计：{row.line_total_with_tax}")
    return "；".join(parts) if parts else "项目和金额未识别"


def _existing_row_text(existing_row: dict[str, Any] | None) -> str:
    if not existing_row:
        return "已写入记录位置：未定位到具体行。"
    parts = [f"采集表第 {existing_row.get('excel_row')} 行"]
    if existing_row.get("source_file"):
        parts.append(f"旧文件：{existing_row['source_file']}")
    if existing_row.get("ticket_id"):
        parts.append(f"票据ID：{existing_row['ticket_id']}")
    if existing_row.get("invoice_no"):
        parts.append(f"旧发票号码：{existing_row['invoice_no']}")
    if existing_row.get("item_name"):
        parts.append(f"旧项目：{existing_row['item_name']}")
    if existing_row.get("line_amount"):
        parts.append(f"旧金额：{existing_row['line_amount']}")
    if existing_row.get("line_total_with_tax"):
        parts.append(f"旧价税合计：{existing_row['line_total_with_tax']}")
    return "；".join(parts)


def duplicate_notice_from_ledger_row(
    row: LedgerRow,
    existing_row: dict[str, Any] | None = None,
) -> RecognitionNotice:
    issue_type = "疑似重复"
    pages = page_text(row.page_range)
    invoice_no = _invoice_no_text(row)
    file_name = _display_file_name(row.source_file)
    current_content = _row_content_text(row)
    existing_text = _existing_row_text(existing_row)
    return RecognitionNotice(
        notice_id=_notice_id(row.invoice_line_key, issue_type, row.invoice_no),
        source_file=file_name,
        page_range=row.page_range,
        page_text=pages,
        severity="未写入",
        issue_type=issue_type,
        issue_field="invoice_no",
        recognized_value=invoice_no,
        system_judgement=(
            f"状态：疑似重复；来源页码：{pages}；原因：文件 {file_name} 的{invoice_no}"
            f"与已写入记录疑似重复；本次内容：{current_content}；重复位置：{existing_text}；本次未写入。"
        ),
        review_suggestion=f"请核对{existing_text}和本次原文件；确认重复则无需处理，确认不是重复则调整后重新导入。",
        invoice_unit_id=row.invoice_unit_id,
    )


def _notice_from_ledger_row(row: LedgerRow) -> RecognitionNotice:
    issue_type = "需复核"
    pages = page_text(row.page_range)
    suggestion = _review_suggestion(row)
    return RecognitionNotice(
        notice_id=_notice_id(row.invoice_unit_id, issue_type, row.invoice_line_key),
        source_file=_display_file_name(row.source_file),
        page_range=row.page_range,
        page_text=pages,
        severity="需复核",
        issue_type=issue_type,
        issue_field=None,
        recognized_value=None,
        system_judgement=f"状态：需复核；来源页码：{pages}；原因：识别结果已写入采集表，但需要人工确认。",
        review_suggestion=suggestion,
        invoice_unit_id=row.invoice_unit_id,
    )


def _unit_issue(record_status: RecognitionStatus, unit_status: RecognitionStatus) -> tuple[str, str, str]:
    if unit_status != RecognitionStatus.READY:
        return "未写入", "不成行", "无法形成采集行。"
    if record_status == RecognitionStatus.UNMODELED:
        return "未写入", "当前未建模", "票种未匹配当前已建模规则。"
    if record_status == RecognitionStatus.FAILED:
        return "未写入", "失败", "识别或解析失败，未写入采集表。"
    return "需复核", "需复核", "识别结果已写入采集表，但需要人工确认。"


def _notice_from_unit_result(unit_result: dict[str, Any]) -> RecognitionNotice | None:
    invoice_record = unit_result["invoice_record"]
    invoice_unit = unit_result["invoice_unit"]
    status = invoice_record.quality.status
    if status not in {RecognitionStatus.FAILED, RecognitionStatus.UNMODELED} and invoice_unit.status == RecognitionStatus.READY:
        return None

    severity, issue_type, default_reason = _unit_issue(status, invoice_unit.status)
    pages = page_text(invoice_unit.page_range or invoice_record.source.page_range)
    reason = invoice_record.quality.remark or "；".join(invoice_unit.messages) or default_reason
    if issue_type == "当前未建模":
        suggestion = f"请打开原文件{pages}核对；如果这是需要采集的票据，请补充该票种解析规则。"
    elif issue_type == "不成行":
        suggestion = f"请打开原文件{pages}核对文件类型和内容，必要时手工补录或转换为支持的发票文件。"
    else:
        suggestion = f"请打开原文件{pages}核对后手工补录，或修复识别失败原因后重新运行。"

    return RecognitionNotice(
        notice_id=_notice_id(invoice_unit.invoice_unit_id, issue_type, None),
        source_file=_display_file_name(invoice_unit.source_file),
        page_range=invoice_unit.page_range,
        page_text=pages,
        severity=severity,
        issue_type=issue_type,
        issue_field=None,
        recognized_value=None,
        system_judgement=f"状态：{issue_type}；来源页码：{pages}；原因：{reason}",
        review_suggestion=suggestion,
        invoice_unit_id=invoice_unit.invoice_unit_id,
    )


def build_recognition_notices(
    unit_results: list[dict[str, Any]],
    ledger_rows: list[LedgerRow],
) -> list[RecognitionNotice]:
    notices = [
        _notice_from_ledger_row(row)
        for row in ledger_rows
        if row.recognition_status == RecognitionStatus.REVIEW_REQUIRED or bool(row.review_remark)
    ]
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
