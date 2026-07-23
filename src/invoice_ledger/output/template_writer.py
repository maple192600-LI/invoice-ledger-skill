"""Profile-driven Excel writer for invoice collection templates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
import yaml

from ..contracts import LedgerRow, RecognitionNotice, RecognitionStatus, WriteAction, WriteResult, normalize_date
from ..parsing.invoice_identity import has_standard_digital_invoice_number
from .._paths import PROJECT_ROOT
from ..validation.review_notes import user_review_remark
from . import duplicate_rows
from .recognition_notices import duplicate_notice_from_ledger_row
from .template_profile import load_template_profile, validate_template_workbook


REVIEW_REMARK_PREFIXES = (
    "missing evidence ",
    "missing ",
    "conflict ",
    "low confidence ",
    "amount_total + tax_total",
    "incomplete amount breakdown",
    "sum line_amount",
    "sum line_tax_amount",
    "line ",
    "digital invoice ",
    "failed",
    "error",
    "待复核：",
    "需复核：",
)

DEFAULT_STATUS_LABELS = {
    RecognitionStatus.READY.value: RecognitionStatus.READY.value,
    RecognitionStatus.REVIEW_REQUIRED.value: RecognitionStatus.REVIEW_REQUIRED.value,
    RecognitionStatus.UNMODELED.value: RecognitionStatus.UNMODELED.value,
    RecognitionStatus.FAILED.value: RecognitionStatus.FAILED.value,
}

ROW_FINGERPRINT_FIELDS = (
    "invoice_code",
    "invoice_no",
    "digital_invoice_no",
    "invoice_date",
    "seller_tax_id",
    "buyer_tax_id",
    "item_name",
    "line_amount",
    "line_tax_amount",
    "line_total_with_tax",
    "invoice_total_with_tax",
)


def _status_labels() -> dict[str, str]:
    status_path = PROJECT_ROOT / "config" / "status_messages.yaml"
    if not status_path.exists():
        return DEFAULT_STATUS_LABELS
    with status_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    statuses = loaded.get("statuses", {}) if isinstance(loaded, dict) else {}
    labels = {
        str(status): str(spec.get("zh"))
        for status, spec in statuses.items()
        if isinstance(spec, dict) and spec.get("zh")
    }
    return DEFAULT_STATUS_LABELS | labels


def _headers(ws) -> dict[str, int]:
    return {
        str(ws.cell(1, column).value).strip(): column
        for column in range(1, ws.max_column + 1)
        if ws.cell(1, column).value is not None
    }


def _field_columns(fields: dict[str, Any], headers: dict[str, int]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for field_name, spec in fields.items():
        if not isinstance(spec, dict):
            continue
        for header in spec.get("headers", []):
            if header in headers:
                columns[str(field_name)] = headers[header]
                break
    return columns


def _date_value(value: Any) -> date | Any:
    if value in {None, ""}:
        return value
    try:
        normalized = normalize_date(value)
    except ValueError:
        return value
    return date.fromisoformat(normalized) if normalized else value


_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M")


def _datetime_value(value: Any) -> datetime | Any:
    if value in {None, ""}:
        return value
    text = str(value).strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return _date_value(value)


def _cell_value(field_name: str, value: Any) -> Any:
    if field_name == "processed_at":
        return _datetime_value(value)
    if field_name in {"invoice_date", "correction_time"}:
        return _date_value(value)
    if isinstance(value, Enum):
        value = value.value
    if field_name in {"recognition_status", "issue_type"}:
        return _status_labels().get(str(value), str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return value


def _remark_parts(remark: str | None) -> list[str]:
    text = str(remark or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", "；").split("；") if part.strip()]


def _is_review_remark_part(part: str) -> bool:
    return part.startswith(REVIEW_REMARK_PREFIXES) or "不一致" in part or "缺失" in part


def _split_remark(remark: str | None) -> tuple[list[str], list[str]]:
    review_parts: list[str] = []
    business_parts: list[str] = []
    for part in _remark_parts(remark):
        if _is_review_remark_part(part):
            review_parts.append(part)
        else:
            business_parts.append(part)
    return review_parts, business_parts


def _display_remark(row: LedgerRow) -> str:
    if row.review_remark or row.context_remark:
        display_parts = []
        review_text = user_review_remark(row.review_remark) if row.review_remark else ""
        if review_text:
            display_parts.append(review_text)
        display_parts.extend(_remark_parts(row.context_remark))
        return "；".join(display_parts)

    if row.recognition_status == RecognitionStatus.READY:
        return "；".join(_remark_parts(row.remark))

    review_parts, business_parts = _split_remark(row.remark)
    display_parts = []
    review_text = user_review_remark("；".join(review_parts)) if review_parts else ""
    if review_text:
        display_parts.append(review_text)
    display_parts.extend(business_parts)
    return "；".join(display_parts)


def _is_digital_vat_invoice(row: LedgerRow) -> bool:
    return has_standard_digital_invoice_number(
        row.schema_id,
        row.variant_id,
        row.invoice_no,
    )


def _row_values(row: LedgerRow) -> dict[str, Any]:
    values = row.model_dump(mode="python")
    display_remark = _display_remark(row)
    if _is_digital_vat_invoice(row):
        values["digital_invoice_no"] = values.get("invoice_no")
        values["invoice_no"] = None
    else:
        values["digital_invoice_no"] = None
    values["issue_type"] = row.recognition_status
    values["remark"] = display_remark
    return values


def _value_for_field(
    field_name: str,
    spec: dict[str, Any],
    values: dict[str, Any],
    target_row: int,
) -> Any:
    if spec.get("value") == "row_number":
        return target_row - 1
    if "static" in spec:
        return spec["static"]
    source = str(spec.get("source") or field_name)
    return _cell_value(field_name, values.get(source))


def _append_rows(ws, fields: dict[str, Any], rows: list[LedgerRow]) -> tuple[int, int, list[tuple[LedgerRow, dict[str, Any]]]]:
    columns = _field_columns(fields, _headers(ws))
    written = 0
    skipped = 0
    skipped_rows: list[tuple[LedgerRow, dict[str, Any]]] = []
    existing_draft_row_ids = duplicate_rows.existing_draft_row_ids(ws, columns)
    existing_invoice_line_keys = duplicate_rows.existing_invoice_line_keys(ws, fields, columns)
    existing_row_fingerprints = duplicate_rows.existing_row_fingerprints(ws, columns, ROW_FINGERPRINT_FIELDS)
    existing_draft_row_id_rows = duplicate_rows.existing_draft_row_id_rows(ws, columns)
    existing_invoice_line_key_rows = duplicate_rows.existing_invoice_line_key_rows(ws, fields, columns)
    existing_row_fingerprint_rows = duplicate_rows.existing_row_fingerprint_rows(
        ws,
        columns,
        ROW_FINGERPRINT_FIELDS,
    )
    weak_duplicate_rows: list[tuple[LedgerRow, dict[str, Any]]] = []
    for row in rows:
        values = _row_values(row)
        row_fingerprint = duplicate_rows.row_fingerprint_from_values(
            fields,
            columns,
            values,
            ROW_FINGERPRINT_FIELDS,
            _value_for_field,
        )
        weak_identity = not row.invoice_no and not _is_digital_vat_invoice(row)
        is_duplicate = (
            row.draft_row_id in existing_draft_row_ids
            or row.invoice_line_key in existing_invoice_line_keys
            or (row_fingerprint is not None and row_fingerprint in existing_row_fingerprints)
        )
        if is_duplicate:
            existing_row_number = (
                existing_draft_row_id_rows.get(row.draft_row_id)
                or existing_invoice_line_key_rows.get(row.invoice_line_key)
                or (existing_row_fingerprint_rows.get(row_fingerprint) if row_fingerprint is not None else None)
                or duplicate_rows.first_row_by_invoice_number(ws, columns, row.invoice_no)
            )
            existing_context = duplicate_rows.existing_row_context(ws, columns, existing_row_number)
            if weak_identity:
                row.context_remark = "；".join(
                    part for part in [row.context_remark, "疑似重复（弱身份票），请人工确认"] if part
                )
                weak_duplicate_rows.append((row, existing_context))
            else:
                skipped += 1
                skipped_rows.append((row, existing_context))
                continue
        target_row = _last_data_row(ws) + 1
        for field_name, column in columns.items():
            spec = fields.get(field_name, {})
            cell = ws.cell(target_row, column)
            cell.value = _value_for_field(
                field_name,
                spec if isinstance(spec, dict) else {},
                values,
                target_row,
            )
            if isinstance(cell.value, datetime):
                cell.number_format = "YYYY-MM-DD HH:MM"
        existing_draft_row_ids.add(row.draft_row_id)
        existing_invoice_line_keys.add(row.invoice_line_key)
        written += 1
    return written, skipped, skipped_rows, weak_duplicate_rows


def _notice_values(notice: RecognitionNotice) -> dict[str, Any]:
    values = notice.model_dump(mode="python")
    values["draft_row_id"] = notice.notice_id
    values["invoice_line_key"] = notice.notice_id
    return values


def _value_for_notice_field(
    field_name: str,
    spec: dict[str, Any],
    values: dict[str, Any],
    target_row: int,
) -> Any:
    if spec.get("value") == "row_number":
        return target_row - 1
    source = str(spec.get("source") or field_name)
    if source in values:
        return _cell_value(field_name, values.get(source))
    if field_name in values:
        return _cell_value(field_name, values.get(field_name))
    if "static" in spec:
        return spec["static"]
    return None


def _append_notice_rows(
    ws,
    fields: dict[str, Any],
    notices: list[RecognitionNotice],
) -> tuple[int, int]:
    columns = _field_columns(fields, _headers(ws))
    written = 0
    skipped = 0
    existing_notice_ids = duplicate_rows.existing_draft_row_ids(ws, columns)
    for notice in notices:
        values = _notice_values(notice)
        if notice.notice_id in existing_notice_ids:
            skipped += 1
            continue
        target_row = _last_data_row(ws) + 1
        for field_name, column in columns.items():
            spec = fields.get(field_name, {})
            cell = ws.cell(target_row, column)
            cell.value = _value_for_notice_field(
                field_name,
                spec if isinstance(spec, dict) else {},
                values,
                target_row,
            )
            if isinstance(cell.value, datetime):
                cell.number_format = "YYYY-MM-DD HH:MM"
        existing_notice_ids.add(notice.notice_id)
        written += 1
    return written, skipped


def _last_data_row(ws) -> int:
    for row_number in range(ws.max_row, 1, -1):
        if any(
            ws.cell(row_number, column).value not in {None, ""}
            for column in range(1, ws.max_column + 1)
        ):
            return row_number
    return 1


def _summary_rows(rows: list[LedgerRow]) -> list[LedgerRow]:
    summaries: dict[str, LedgerRow] = {}
    for row in rows:
        if row.invoice_key not in summaries:
            summaries[row.invoice_key] = row
        if row.row_type == "汇总":
            summaries[row.invoice_key] = row
    return list(summaries.values())


def _issue_rows(rows: list[LedgerRow]) -> list[LedgerRow]:
    return [
        row
        for row in rows
        if row.recognition_status != RecognitionStatus.READY
        or bool(row.review_remark)
    ]


def write_with_template_profile(
    workbook_path: str | Path,
    template_profile_path: str | Path,
    ledger_rows: list[LedgerRow],
    run_id: str,
    clear_existing: bool = False,
    recognition_notices: list[RecognitionNotice] | None = None,
) -> WriteResult:
    profile = load_template_profile(template_profile_path)
    drift_report = validate_template_workbook(workbook_path, profile)
    if drift_report.get("blocked_write") is True or drift_report["status"] != "passed":
        raise ValueError(
            {
                "message": "Template workbook does not match profile",
                "template_drift_report": drift_report,
            }
        )

    workbook = load_workbook(workbook_path)
    result = WriteResult(run_id=run_id, target_sheet=str(profile.get("template_id") or "template"))
    try:
        for sheet_key, sheet_spec in profile["sheets"].items():
            ws = workbook[str(sheet_spec["name"])]
            if clear_existing and ws.max_row > 1:
                cleared_rows = ws.max_row - 1
                ws.delete_rows(2, cleared_rows)
                result.actions.append(
                    {
                        "action": WriteAction.UPDATED.value,
                        "sheet": sheet_spec["name"],
                        "mode": "clear_existing",
                        "rows": cleared_rows,
                    }
                )
            fields = sheet_spec.get("fields", {})
            mode = sheet_spec.get("mode")
            written = 0
            skipped = 0
            if mode == "ledger_rows":
                written, skipped, skipped_rows, weak_duplicates = _append_rows(ws, fields, ledger_rows)
                if recognition_notices is not None:
                    existing_notice_ids = {notice.notice_id for notice in recognition_notices}
                    for skipped_row, existing_row in skipped_rows:
                        notice = duplicate_notice_from_ledger_row(skipped_row, existing_row)
                        if notice.notice_id in existing_notice_ids:
                            continue
                        recognition_notices.append(notice)
                        existing_notice_ids.add(notice.notice_id)
                        duplicate_position = (
                            f"重复位置：采集表第 {existing_row['excel_row']} 行；"
                            if existing_row and existing_row.get("excel_row")
                            else ""
                        )
                        invoice_no = notice.invoice_no or "发票号码未识别"
                        result.messages.append(
                            f"疑似重复：文件 {notice.source_file}，发票号码 {invoice_no}，"
                            f"{duplicate_position}本次未写入；请查看 Excel 的“识别提示”页。"
                        )
                    for weak_row, existing_row in weak_duplicates:
                        notice = duplicate_notice_from_ledger_row(weak_row, existing_row).model_copy(
                            update={
                                "severity": "已写入",
                                "issue_type": "疑似重复（弱身份票）",
                                "action": "已写入采集表；请人工确认是否真重复，重复则删除该行。",
                            }
                        )
                        if notice.notice_id in existing_notice_ids:
                            continue
                        recognition_notices.append(notice)
                        existing_notice_ids.add(notice.notice_id)
                        duplicate_position = (
                            f"重复位置：采集表第 {existing_row['excel_row']} 行；"
                            if existing_row and existing_row.get("excel_row")
                            else ""
                        )
                        invoice_no = notice.invoice_no or "发票号码未识别"
                        result.messages.append(
                            f"疑似重复（弱身份票）：文件 {notice.source_file}，发票号码 {invoice_no}，"
                            f"{duplicate_position}本次已写入但需人工确认；请查看 Excel 的“识别提示”页。"
                        )
                result.added_rows = written
                result.skipped_duplicate_rows += skipped
            elif mode == "invoice_summary":
                written, skipped, _, _ = _append_rows(ws, fields, _summary_rows(ledger_rows))
                result.skipped_duplicate_rows += skipped
            elif mode == "review_issues":
                if recognition_notices is None:
                    issues = _issue_rows(ledger_rows)
                    written, skipped, _, _ = _append_rows(ws, fields, issues)
                    result.review_required_rows = len(issues)
                else:
                    written, skipped = _append_notice_rows(ws, fields, recognition_notices)
                    result.review_required_rows = len(recognition_notices)
                result.skipped_duplicate_rows += skipped
            result.actions.append(
                {
                    "action": WriteAction.ADDED.value,
                    "sheet": sheet_spec["name"],
                    "mode": mode,
                    "rows": written,
                    "skipped_duplicate_rows": skipped,
                }
            )
        workbook.save(workbook_path)
    finally:
        workbook.close()
    return result
